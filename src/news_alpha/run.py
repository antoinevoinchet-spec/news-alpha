"""Pipeline, étapes 1 à 7 du SPEC §5.

uv run python -m news_alpha.run --start 2025-04-01 --end 2025-04-15 --limit 20

Passe 1 : filtres sur tous les 8-K de la période, sans appel au modèle.
Passe 2 : tirage aléatoire reproductible parmi les dépôts éligibles, puis
extraction, scoring et rendements des dépôts tirés.
"""

import argparse
import json
import os
import random
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

from news_alpha import ROOT, db, extract, filters, ingest, prices, report, returns, score, window
from news_alpha.ingest import Filing
from news_alpha.prices import Bar

REQUIRED_ENV = ("ANTHROPIC_API_KEY", "SEC_USER_AGENT", "ALPACA_API_KEY", "ALPACA_SECRET_KEY")
MAX_LLM_CALLS = 50  # par défaut ; au-delà, --max-calls avec accord explicite (SPEC §11)
MAX_CONSECUTIVE_API_ERRORS = 5  # ex. crédit épuisé : inutile de faire défiler la campagne
PRICE_WINDOW = timedelta(days=45)  # couvre 20 séances avant le dépôt et après l'entrée
SAMPLE_SEED = 20250401  # graine fixe : même tirage à chaque exécution
INDEX_TAIL = timedelta(days=4)  # couvre un week-end prolongé après le dernier jour
REPORTS = ROOT / "reports"  # phase0_sample.md, preuve de la phase 0, n'est plus réécrit (audit C22)


def load_env(path: Path) -> None:
    """Charge .env sans écraser l'environnement existant ; n'affiche aucune valeur."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def store_bars(conn: sqlite3.Connection, symbol: str, cik: int | None,
               bars: list[Bar], adjustment: str, source: str) -> None:
    for bar in bars:
        db.upsert(conn, "prices", {
            "symbol": symbol, "date": bar.date.isoformat(), "adjustment": adjustment,
            "cik": cik, "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume, "source": source,
        })


def save_filing(conn: sqlite3.Connection, filing: Filing, reason: str | None,
                selected: bool = False, **extra) -> None:
    """reason = motif d'exclusion (None si éligible) ; selected = tiré et traité."""
    db.upsert(conn, "filings", {
        "accession": filing.accession, "cik": filing.cik, "company": filing.company,
        "form": filing.form, "filing_date": filing.filing_date.isoformat(),
        "acceptance_et": filing.acceptance.isoformat() if filing.acceptance else None,
        "items": ",".join(filing.items), "ticker": filing.ticker, "exchange": filing.exchange,
        "sic": filing.sic, "selected": int(selected), "reject_reason": reason, **extra,
    })


def cheap_filter(filing: Filing, start: date, end: date) -> str | None:
    """Filtres sans prix ; renvoie le motif d'exclusion ou None."""
    filing.cik = filing.ciks[0]
    if not ingest.add_details(filing):
        return "absent_des_submissions"
    if not start <= filing.acceptance.date() <= end:
        return "acceptation_hors_plage"
    if not filters.has_retained_item(filing.items):
        return "items_non_retenus"
    cover = ingest.document(filing.cik, filing.accession, filing.primary_doc)
    listing = filters.cover_listing(cover)
    if listing is None:
        # Distinguer « rien de coté n'est balisé » de « coté ailleurs ou pas en actions ».
        return "hors_nyse_nasdaq" if "dei:TradingSymbol" in cover else "aucun_titre_cote_balise"
    registrant, filing.ticker, filing.exchange = listing
    if registrant in filing.ciks and registrant != filing.cik:
        filing.cik = registrant  # dépôt commun : on suit le déclarant principal
        ingest.add_details(filing)
    header = ingest.filing_header(filing.cik, filing.accession)
    if not header:
        return "en_tete_introuvable"
    filing.sic = ingest.assigned_sic(header, filing.cik)  # à la date du dépôt (audit C26)
    return filters.sic_exclusion(filing.sic)


def market_screen(conn: sqlite3.Connection, filing: Filing) -> tuple[str | None, dict, list[Bar]]:
    """Filtres de marché ; renvoie (motif ou None, mesures, séances brutes du titre)."""
    filed_on = filing.acceptance.date()
    known = filters.shares_outstanding(filing.cik, filed_on)
    if known is None:
        return "actions_absentes_ou_anciennes", {}, []
    raw, source = prices.daily_bars(
        filing.ticker, filed_on - PRICE_WINDOW, filed_on + PRICE_WINDOW, "raw", asof=filed_on
    )
    store_bars(conn, filing.ticker, filing.cik, raw, "raw", source)
    bars_before = [bar for bar in raw if bar.date < filed_on]
    # Splits appliqués jusqu'à la séance du prix retenu (la veille), pas jusqu'au jour du
    # dépôt : sinon un split daté du jour du 8-K multiplie des actions « après » par un
    # prix « avant » (audit C14).
    price_day = bars_before[-1].date if bars_before else filed_on
    shares = known[0] * prices.split_factor(filing.ticker, known[1], price_day)
    reason, metrics = filters.market_filter(shares, bars_before)
    return reason, {**metrics, "shares_out": shares}, raw


def screen_period(conn: sqlite3.Connection, start: date, end: date
                  ) -> tuple[list[tuple[Filing, list[Bar], dict]], Counter, int]:
    """Passe 1 : (dépôts éligibles avec leurs prix bruts et mesures, exclusions, 8-K examinés).

    Un 8-K accepté après 17:30 ET est indexé au jour ouvré suivant : les index sont
    donc lus jusqu'à `end` + INDEX_TAIL, en ne gardant que les acceptations de la plage.
    """
    eligible, exclusions, index_count = [], Counter(), 0
    day = start
    while day <= end + INDEX_TAIL:
        daily = ingest.daily_8k(day)
        for filing in daily:
            reason, metrics, raw = cheap_filter(filing, start, end), {}, []
            if reason == "acceptation_hors_plage":
                # Dépôt d'une autre période (soir de la veille du début, ou lendemain de la
                # fin) : l'enregistrer écraserait sa ligne, déjà traitée avec sa propre période.
                continue
            index_count += 1  # éligibles + exclusions = 8-K examinés
            if reason is None:
                reason, metrics, raw = market_screen(conn, filing)
            if reason:
                exclusions[reason] += 1
            else:
                eligible.append((filing, raw, metrics))
            save_filing(conn, filing, reason, **metrics)
        conn.commit()
        print(f"{day} : {len(daily)} 8-K, {len(eligible)} éligibles cumulés")
        day += timedelta(days=1)
    return eligible, exclusions, index_count


def draw_order(accessions: list[str], seed: int = SAMPLE_SEED) -> list[str]:
    """Ordre de tirage aléatoire reproductible : les n premiers forment l'échantillon."""
    order = sorted(accessions)
    random.Random(seed).shuffle(order)
    return order


def score_once(conn: sqlite3.Connection, client: anthropic.Anthropic, filing: Filing,
               text: str, move: float | None, timing: str | None,
               calls_made: int, max_calls: int = MAX_LLM_CALLS) -> tuple[float | None, bool]:
    """Note le dépôt s'il ne l'est pas déjà ; renvoie (latence du scoring, appel effectué)."""
    scored = conn.execute(
        "SELECT latency_s FROM scores WHERE accession = ? AND prompt_version = ? AND model = ?",
        (filing.accession, score.PROMPT_VERSION, score.MODEL),
    ).fetchone()
    if scored:
        return scored["latency_s"], False
    if calls_made >= max_calls:
        raise SystemExit(f"Plafond de {max_calls} appels au modèle atteint.")
    try:
        result = score.score_filing(client, score.build_prompt(text, move, timing))
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.NotFoundError):
        raise
    except anthropic.APIError as err:
        print(f"{filing.accession} : erreur API {type(err).__name__} ({err.message[:160]}), non noté",
              file=sys.stderr)
        return None, True
    parsed = result.score
    db.upsert(conn, "scores", {
        "accession": filing.accession, "cik": filing.cik,
        "valid": int(parsed is not None), "reject_reason": result.reject_reason,
        "raw_output": result.raw_output,
        **(parsed.model_dump(mode="json") if parsed else {}),
        "key_facts": json.dumps(parsed.key_facts) if parsed else None,
        "composite": parsed.composite if parsed else None,
        "price_move": move, "price_move_timing": timing, "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens, "cost_usd": result.cost_usd,
        "latency_s": result.latency_s, "prompt_sha256": score.TEMPLATE_SHA256,
    })
    conn.commit()  # une réponse payée ne doit jamais être perdue par un arrêt ultérieur
    return result.latency_s, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News Alpha")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--limit", type=int, default=20,
                        help="taille de l'échantillon tiré au hasard (0 = tous les éligibles)")
    parser.add_argument("--max-chars", type=int, default=extract.MAX_CHARS)
    parser.add_argument("--max-calls", type=int, default=MAX_LLM_CALLS,
                        help="plafond d'appels au modèle pour cette exécution")
    parser.add_argument("--report", type=Path, default=None,
                        help="rapport à écrire (défaut : reports/run_<start>_<end>.md)")
    parser.add_argument("--reserved", action="store_true",
                        help="traiter l'échantillon réservé (SPEC §6)")
    args = parser.parse_args(argv)
    refused = window.date_guard(args.start, args.end, args.reserved,
                                datetime.now(ingest.ET).date(), window.ANALYSIS_REPORT.exists())
    if refused:
        print(f"Plage refusée : {refused}.", file=sys.stderr)
        return 1
    report_path = args.report or REPORTS / f"run_{args.start}_{args.end}.md"
    if score.TEMPLATE_SHA256 != score.PROMPT_SHA256:
        print(f"{score.PROMPT_PATH.relative_to(ROOT)} a changé depuis son gel "
              f"(empreinte {score.TEMPLATE_SHA256[:12]}…) : arrêt.", file=sys.stderr)
        return 1

    load_env(ROOT / ".env")
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"Variables absentes de .env : {', '.join(missing)}", file=sys.stderr)
        return 1

    conn = db.connect()
    client = score.make_client()

    # Calendrier des séances et benchmark marché : IWM, ajusté splits et dividendes.
    iwm, iwm_source = prices.daily_bars(
        prices.MARKET_ETF, args.start - PRICE_WINDOW, args.end + 2 * PRICE_WINDOW, "all"
    )
    store_bars(conn, prices.MARKET_ETF, None, iwm, "all", iwm_source)
    sessions = [bar.date for bar in iwm]
    market = {bar.date: bar for bar in iwm}
    sectors: dict[str, dict[date, Bar]] = {}

    eligible, exclusions, index_count = screen_period(conn, args.start, args.end)
    by_accession = {filing.accession: (filing, raw, metrics) for filing, raw, metrics in eligible}
    limit = args.limit or len(eligible)
    selected: list[Filing] = []
    llm_calls = api_errors = 0

    for accession in draw_order(list(by_accession)):
        if len(selected) >= limit:
            break
        filing, _, metrics = by_accession[accession]
        filed_on = filing.acceptance.date()
        text, truncated = extract.filing_text(filing.cik, filing.accession, args.max_chars)
        if not text:
            exclusions["texte_introuvable"] += 1
            save_filing(conn, filing, "texte_introuvable", **metrics)
            conn.commit()
            continue

        # Étape 4 : scoring (une seule fois par dépôt, version de prompt et modèle). Le
        # mouvement est mesuré sur prix ajustés : un split ne doit pas se lire « −50 % ».
        adjusted, source = prices.daily_bars(
            filing.ticker, filed_on - timedelta(days=10), filed_on + PRICE_WINDOW, "all",
            asof=filed_on,
        )
        store_bars(conn, filing.ticker, filing.cik, adjusted, "all", source)
        provisional_entry = returns.entry_session(filing.acceptance, sessions)
        move, timing = None, None
        if provisional_entry:
            move = prices.pre_entry_move(adjusted, filed_on, provisional_entry)
            timing = prices.move_timing(adjusted, filing.acceptance, provisional_entry)
        latency, called = score_once(conn, client, filing, text, move, timing, llm_calls,
                                     args.max_calls)
        llm_calls += called
        api_errors = api_errors + 1 if called and latency is None else 0
        if api_errors >= MAX_CONSECUTIVE_API_ERRORS:
            raise SystemExit(f"{api_errors} erreurs d'API consécutives : arrêt (crédit, quota ?).")

        # Étapes 5-6 : point d'entrée après acceptation ET fin du scoring, puis rendements.
        entry = returns.entry_session(filing.acceptance + timedelta(seconds=latency or 0), sessions)
        etf = prices.sector_etf(filing.sic)
        if etf and etf not in sectors:
            etf_bars, etf_source = prices.daily_bars(
                etf, args.start - PRICE_WINDOW, args.end + 2 * PRICE_WINDOW, "all"
            )
            store_bars(conn, etf, None, etf_bars, "all", etf_source)
            sectors[etf] = {bar.date: bar for bar in etf_bars}
        if entry:
            stock = {bar.date: bar for bar in adjusted}
            for row in returns.horizon_returns(
                entry, sessions, stock, market, sectors.get(etf) if etf else None
            ):
                db.upsert(conn, "returns", {
                    "accession": filing.accession, "cik": filing.cik,
                    "entry_date": entry.isoformat(), "sector_etf": etf, **row,
                })
        save_filing(conn, filing, None, selected=True, **metrics,
                    text_chars=len(text), text_truncated=int(truncated))
        conn.commit()
        selected.append(filing)
        print(f"[{len(selected)}/{limit}] {filing.ticker} {filing.accession} "
              f"accepté {filing.acceptance:%Y-%m-%d %H:%M} ET, entrée {entry}")

    accessions = [f.accession for f in sorted(selected, key=lambda f: f.acceptance)]
    rejects, cost = report.write(
        conn, report_path, accessions, exclusions, index_count, len(eligible),
        args.start, args.end,
    )
    print(f"Terminé : {len(selected)} dépôts tirés parmi {len(eligible)} éligibles, "
          f"{rejects} rejets de schéma, {llm_calls} appels au modèle, coût LLM {cost:.4f} $, "
          f"rapport {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
