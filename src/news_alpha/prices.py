"""Prix journaliers Alpaca (source principale, SPEC §4) et choix des benchmarks."""

import json
import os
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from news_alpha import net

ET = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/{symbol}/bars?{query}"
CORPORATE_ACTIONS = "https://data.alpaca.markets/v1/corporate-actions?{query}"
MARKET_ETF = "IWM"
MARKET_CLOSE = time(16, 0)  # clôtures anticipées non connues des barres journalières (D35)


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def last_complete_session_day() -> date:
    """Veille (heure de New York) : dernière date dont les barres SIP sont accessibles."""
    return datetime.now(ET).date() - timedelta(days=1)


def daily_bars(
    symbol: str, start: date, end: date, adjustment: str, asof: date | None = None
) -> tuple[list[Bar], str]:
    """Séances de [start, end] en ordre chronologique, et la source utilisée.

    adjustment : "raw" pour les filtres (prix réels à la date), "all" (splits et
    dividendes) pour les rendements. asof : date à laquelle le ticker désigne la
    société voulue (tickers renommés ou réattribués depuis).

    Flux SIP uniquement. Alpaca refuse le SIP pour une plage qui finit dans le
    futur et l'ancien repli sur IEX (2 à 5 % des volumes) faussait les filtres en
    silence : la fin est donc bornée à la veille, et un refus est une erreur.
    """
    end = min(end, last_complete_session_day())
    if start > end:
        return [], "alpaca:sip"
    headers = {
        "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
    }
    query = urllib.parse.urlencode(
        {
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "adjustment": adjustment,
            "feed": "sip",
            "limit": 10000,
            **({"asof": asof.isoformat()} if asof else {}),
        }
    )
    alpaca_symbol = symbol.replace("-", ".").replace(" ", ".")
    url = BARS.format(symbol=urllib.parse.quote(alpaca_symbol), query=query)
    try:
        body = net.get(url, headers, bucket="alpaca", min_interval=0.3,
                       cache_ok=lambda b: b'"t":' in b)
    except net.HttpError as err:
        if err.status in (400, 422):
            return [], "alpaca:sip"  # symbole refusé par Alpaca : prix absents
        raise
    payload = json.loads(body) if body else {}
    bars = [
        Bar(
            date=datetime.fromisoformat(b["t"]).astimezone(ET).date(),
            open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b["v"],
        )
        for b in payload.get("bars") or []
    ]
    return bars, "alpaca:sip"


def split_ratio(corporate_actions: dict, since: date, until: date) -> float:
    """Produit des splits (nouveau / ancien) dont la date ex est dans ]since, until]."""
    ratio = 1.0
    for kind in ("forward_splits", "reverse_splits"):
        for split in corporate_actions.get(kind, []):
            if since < date.fromisoformat(split["ex_date"]) <= until:
                ratio *= split["new_rate"] / split["old_rate"]
    return ratio


def split_factor(symbol: str, since: date, until: date) -> float:
    """Facteur à appliquer à un nombre d'actions daté `since` pour l'amener à `until`."""
    headers = {
        "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
    }
    query = urllib.parse.urlencode({
        "symbols": symbol.replace("-", ".").replace(" ", "."),
        "types": "forward_split,reverse_split",
        "start": since.isoformat(),
        "end": until.isoformat(),
    })
    body = net.get(CORPORATE_ACTIONS.format(query=query), headers, bucket="alpaca",
                   min_interval=0.3, cache_ok=lambda b: b'"corporate_actions"' in b)
    return split_ratio(json.loads(body)["corporate_actions"], since, until)


def pre_entry_move(bars: list[Bar], filed_on: date, entry: date) -> float | None:
    """Clôture de la veille du dépôt -> dernier prix connu avant le point d'entrée.

    Avec des barres journalières, le dernier prix connu est la clôture de la
    dernière séance antérieure au jour d'entrée : aucun prix postérieur.
    """
    before_filing = [b for b in bars if b.date < filed_on]
    before_entry = [b for b in bars if b.date < entry]
    if not before_filing or not before_entry:
        return None
    return before_entry[-1].close / before_filing[-1].close - 1


def move_timing(bars: list[Bar], acceptance: datetime, entry: date) -> str:
    """Le titre a-t-il coté depuis le dépôt ? 'no_session', 'during' ou 'after_close' (D35).

    Seule la séance du jour du dépôt peut tomber entre le dépôt et l'entrée : avant
    l'ouverture, l'entrée a lieu le jour même ; après, à la séance suivante.
    """
    filed_on = acceptance.date()
    if not any(filed_on <= bar.date < entry for bar in bars):
        return "no_session"  # avant l'ouverture, jour sans séance ou titre suspendu
    return "after_close" if acceptance.time() >= MARKET_CLOSE else "during"


# Code SIC -> ETF sectoriel SPDR (correspondance approximative, voir DECISIONS.md).
# Plages testées dans l'ordre : la première qui contient le code l'emporte.
SIC_TO_ETF = [
    ((1300, 1399), "XLE"), ((2900, 2999), "XLE"), ((1200, 1299), "XLE"),
    ((1000, 1499), "XLB"), ((2400, 2499), "XLB"), ((2600, 2699), "XLB"),
    ((2830, 2836), "XLV"), ((2840, 2844), "XLP"), ((2800, 2899), "XLB"),
    ((3000, 3099), "XLB"), ((3200, 3399), "XLB"),
    ((2000, 2199), "XLP"), ((100, 999), "XLP"),
    ((2200, 2399), "XLY"), ((2500, 2599), "XLY"), ((3100, 3199), "XLY"),
    ((2700, 2799), "XLC"),
    ((3570, 3579), "XLK"), ((3630, 3639), "XLY"), ((3600, 3699), "XLK"),
    ((3711, 3716), "XLY"), ((3750, 3751), "XLY"), ((3790, 3799), "XLY"),
    ((3840, 3851), "XLV"), ((3812, 3812), "XLI"), ((3800, 3899), "XLK"),
    ((3900, 3999), "XLY"), ((1500, 1799), "XLI"), ((3400, 3799), "XLI"),
    ((4950, 4959), "XLI"), ((4900, 4999), "XLU"), ((4800, 4899), "XLC"),
    ((4000, 4799), "XLI"),
    ((5000, 5099), "XLI"), ((5100, 5199), "XLP"), ((5400, 5499), "XLP"),
    ((5912, 5912), "XLP"), ((5200, 5999), "XLY"),
    ((6798, 6798), "XLRE"), ((6500, 6599), "XLRE"), ((6000, 6799), "XLF"),
    ((7370, 7379), "XLK"), ((7300, 7399), "XLI"), ((7800, 7899), "XLC"), ((7000, 7999), "XLY"),
    ((8000, 8099), "XLV"), ((8730, 8734), "XLV"), ((7300, 8999), "XLI"),
]


def sector_etf(sic: int | None) -> str | None:
    if sic is None:
        return None
    return next((etf for (low, high), etf in SIC_TO_ETF if low <= sic <= high), None)
