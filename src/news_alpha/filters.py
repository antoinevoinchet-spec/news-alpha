"""Étape 2 : filtres du SPEC §5 (items, bourse, capitalisation, prix, liquidité)."""

import html
import json
import re
import unicodedata
from datetime import date, timedelta
from statistics import mean

from news_alpha import ingest
from news_alpha.net import sec_get
from news_alpha.prices import Bar

RETAINED_ITEMS = {"1.01", "2.02", "5.02", "7.01", "8.01"}
EXCHANGES = {"NYSE", "Nasdaq", "NYSE American"}  # NYSE American inclus (décision du 25/10/2026)
IX_NON_NUMERIC = re.compile(r"<ix:nonNumeric\b([^>]*)>(.*?)</ix:nonNumeric>", re.S | re.I)
IX_NON_FRACTION = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
# Balise vide (`xsi:nil="true"/>`) : sans ce retrait, l'expression ci-dessus la prend pour
# une ouvrante et avale le fait suivant (cas réel : Movado lu à 6,5 M d'actions au lieu de 22 M).
IX_SELF_CLOSING = re.compile(r"<ix:non(?:Fraction|Numeric)\b[^>]*/>", re.I)
EQUITY_SYMBOL = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")
MIN_PLAUSIBLE_SHARES = 1_000
# Titre du titre coté (dei:Security12bTitle) : actions ordinaires seulement.
COMMON_EQUITY = re.compile(r"\bcommon\b|\bordinary\b|beneficial interest|voting shares", re.I)
NOT_COMMON_EQUITY = re.compile(
    r"warrant|\brights?\b|preferred|depositary|\bnotes?\b|debenture|each consisting", re.I
)
FOOTNOTE_MARK = re.compile(r"(\s+\d+|\s*\(\d+\)|\s*\*+)$")  # 'QTI 1', 'ABC(1)', 'ABC*'
COVER_TAGS = {
    "dei:Security12bTitle": "title",
    "dei:TradingSymbol": "symbol",
    "dei:SecurityExchangeName": "exchange",
    "dei:EntityCentralIndexKey": "cik",
}
SPAC_SIC = 6770  # « blank checks »
CAP_MIN, CAP_MAX = 300e6, 10e9
MIN_PRICE = 2.0
MIN_ADV_USD = 500e3
ADV_SESSIONS = 20
MAX_SHARES_AGE = timedelta(days=180)  # republié chaque trimestre en temps normal
SHARES = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}"
    "/dei/EntityCommonStockSharesOutstanding.json"
)


def _attribute(attributes: str, name: str) -> str | None:
    match = re.search(rf'\b{name}="([^"]*)"', attributes, re.I)
    return match.group(1) if match else None


def _visible_text(inner: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())


def exchange_code(display_name: str) -> str | None:
    """Nom de bourse affiché en page de garde -> 'Nasdaq', 'NYSE', 'NYSE American' ou None."""
    name = display_name.lower()
    if "nasdaq" in name:
        return "Nasdaq"
    if any(word in name for word in ("american", "amex", "mkt")):
        return "NYSE American"
    if any(word in name for word in ("arca", "texas", "chicago", "national")):
        return None
    if "new york stock exchange" in name or name.startswith("nyse"):
        return "NYSE"
    return None


def clean_symbol(raw: str) -> str:
    """Retire caractères invisibles (ex. U+200C) et renvoi de note final : 'QTI 1' -> 'QTI'."""
    visible = "".join(ch for ch in raw if unicodedata.category(ch) != "Cf")
    return FOOTNOTE_MARK.sub("", visible).strip()


def is_common_equity(title: str) -> bool:
    return bool(COMMON_EQUITY.search(title)) and not NOT_COMMON_EQUITY.search(title)


def cover_listing(document: str) -> tuple[int | None, str, str] | None:
    """(CIK du déclarant, ticker, bourse) de l'action ordinaire cotée, d'après la page de garde.

    Les titres cotés (section 12(b)) y sont balisés en XBRL à la date du dépôt, ce
    qui évite le biais du survivant d'une liste de tickers actuelle. Chaque titre a
    son propre contexte XBRL, qui porte aussi le CIK de son émetteur en cas de dépôt
    commun à plusieurs sociétés ; à défaut, on prend le premier CIK de la page.

    Si aucune ligne n'est complète, une page qui ne cote qu'un seul titre et une
    seule bourse retenue est acceptée même quand titre, ticker et bourse sont balisés
    séparément, ou le titre absent (cas réels : Northpointe, BlackBerry).
    """
    contexts: dict[str | None, dict[str, str]] = {}
    for attributes, inner in IX_NON_NUMERIC.findall(IX_SELF_CLOSING.sub("", document)):
        field = COVER_TAGS.get(_attribute(attributes, "name") or "")
        if field:
            context = contexts.setdefault(_attribute(attributes, "contextRef"), {})
            context.setdefault(field, _visible_text(inner))

    def listed(context: dict[str, str], exchange: str) -> tuple[int | None, str, str]:
        cik = context.get("cik", first_cik)
        return (int(cik) if cik and cik.isdigit() else None), clean_symbol(context["symbol"]), exchange

    first_cik = next((c["cik"] for c in contexts.values() if "cik" in c), None)
    for context in contexts.values():
        exchange = exchange_code(context.get("exchange", ""))
        if "symbol" in context and exchange in EXCHANGES and is_common_equity(context.get("title", "")):
            return listed(context, exchange)

    with_symbol = [c for c in contexts.values() if "symbol" in c]
    retained = {exchange_code(c["exchange"]) for c in contexts.values() if "exchange" in c} & EXCHANGES
    if len(with_symbol) == 1 and len(retained) == 1:
        title = with_symbol[0].get("title", "")
        untitled_equity = not title and EQUITY_SYMBOL.fullmatch(clean_symbol(with_symbol[0]["symbol"]))
        if is_common_equity(title) or untitled_equity:
            return listed(with_symbol[0], retained.pop())
    return None


def has_retained_item(items: list[str]) -> bool:
    return bool(RETAINED_ITEMS.intersection(items))


def sic_exclusion(sic: int | None) -> str | None:
    """SPAC et déclarants sans code SIC (les BDC) hors univers (décision L1 du 25/10/2026).

    Univers fixé avant la campagne pour éviter un retrait après coup (audit C25) ;
    sans code SIC, une BDC n'a pas non plus d'ETF de contrôle sectoriel.
    """
    if sic is None:
        return "sans_code_sic"
    if sic == SPAC_SIC:
        return "spac_sic_6770"
    return None


def latest_shares(facts: list[dict], filed_before: date) -> tuple[float, date] | None:
    """(valeur, date) la plus récente publiée avant le 8-K, ignorée au-delà de 180 jours."""
    known = [fact for fact in facts if fact["filed"] < filed_before.isoformat()]
    if not known:
        return None
    latest = max(known, key=lambda fact: (fact["end"], fact["filed"]))
    as_of = date.fromisoformat(latest["end"])
    if filed_before - as_of > MAX_SHARES_AGE:
        return None
    return float(latest["val"]), as_of


def cover_shares(document: str) -> float | None:
    """Actions en circulation déclarées en page de garde d'un 10-K/10-Q, toutes classes sommées.

    Les sociétés à plusieurs classes ne publient le nombre que par classe (un
    contexte XBRL par classe), d'où l'absence de valeur simple dans l'API XBRL.
    """
    by_context: dict[str | None, float] = {}
    for attributes, inner in IX_NON_FRACTION.findall(IX_SELF_CLOSING.sub("", document)):
        if _attribute(attributes, "name") != "dei:EntityCommonStockSharesOutstanding":
            continue
        digits = _visible_text(inner).replace(",", "").replace(" ", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        scale = int(_attribute(attributes, "scale") or 0)
        by_context.setdefault(_attribute(attributes, "contextRef"), round(value * 10**scale))
    total = sum(by_context.values())
    return total if total >= MIN_PLAUSIBLE_SHARES else None  # cas réel : FNVT lu à 1 action


def shares_outstanding(cik: int, filed_before: date) -> tuple[float, date] | None:
    """(nombre d'actions, date de ce nombre) connu à la date du 8-K, ou None.

    1. Valeur XBRL simple (API companyconcept) de moins de 180 jours ;
    2. sinon, somme des classes en page de garde du dernier 10-K/10-Q de moins de
       180 jours, datée du dépôt de ce rapport.
    Les splits survenus depuis cette date sont appliqués par l'appelant.
    """
    body = sec_get(SHARES.format(cik=cik), cache_ok=lambda b: b'"units"' in b)
    if body:
        known = latest_shares(json.loads(body)["units"].get("shares", []), filed_before)
        if known:
            return known
    report = ingest.latest_periodic_report(cik, filed_before, filed_before - MAX_SHARES_AGE)
    if report is None:
        return None
    accession, primary_doc, report_date = report
    shares = cover_shares(ingest.document(cik, accession, primary_doc))
    return (shares, report_date) if shares else None


def market_filter(shares: float, bars_before: list[Bar]) -> tuple[str | None, dict]:
    """Prix > 2 $, volume moyen 20 j > 500 k$, capitalisation 300 M$ - 10 Md$.

    `bars_before` : séances non ajustées antérieures au jour du dépôt.
    Renvoie (motif de rejet ou None, mesures calculées).
    """
    if len(bars_before) < ADV_SESSIONS:
        return "historique_prix_insuffisant", {}
    prev_close = bars_before[-1].close
    metrics = {
        "prev_close": prev_close,
        "adv20_usd": mean(b.close * b.volume for b in bars_before[-ADV_SESSIONS:]),
        "market_cap": shares * prev_close,
    }
    if prev_close <= MIN_PRICE:
        return "prix_inferieur_2", metrics
    if metrics["adv20_usd"] <= MIN_ADV_USD:
        return "liquidite_inferieure_500k", metrics
    if not CAP_MIN <= metrics["market_cap"] <= CAP_MAX:
        return "capitalisation_hors_fourchette", metrics
    return None, metrics
