"""Étapes 5-6 : point d'entrée et rendements bruts / anormaux à 1, 5 et 20 séances."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from news_alpha.prices import Bar

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
HORIZONS = (1, 5, 20)


def entry_session(ready_at: datetime, sessions: list[date]) -> date | None:
    """Première séance dont l'ouverture (09:30 ET) est postérieure à `ready_at`.

    `ready_at` = le plus tardif de acceptanceDateTime et de la fin du scoring.
    Un dépôt à 15:59 ou 16:01 ET entre donc à l'ouverture de la séance suivante,
    jamais à la clôture du jour du dépôt.
    """
    for day in sessions:
        if datetime.combine(day, MARKET_OPEN, tzinfo=ET) > ready_at:
            return day
    return None


def period_return(bars: dict[date, Bar], entry: date, exit_: date) -> float | None:
    """Achat à l'ouverture de `entry`, vente à la clôture de `exit_`."""
    if entry not in bars or exit_ not in bars:
        return None
    return bars[exit_].close / bars[entry].open - 1


def horizon_returns(
    entry: date,
    sessions: list[date],
    stock: dict[date, Bar],
    market: dict[date, Bar],
    sector: dict[date, Bar] | None,
) -> list[dict]:
    """Une ligne par horizon h : sortie à la clôture de la h-ième séance (entrée comprise).

    Si le titre cesse de coter avant (rachat, faillite), la vente a lieu à sa
    dernière clôture et la ligne est marquée `early_exit` ; IWM et l'ETF sectoriel
    sont alors mesurés sur la même période de détention (décision M2 du 25/10/2026).
    """
    start = sessions.index(entry)
    rows = []
    for horizon in HORIZONS:
        planned = sessions[start + horizon - 1] if start + horizon - 1 < len(sessions) else None
        held = [day for day in stock if planned and entry <= day <= planned]
        exit_ = max(held) if entry in stock and held else None
        ret = period_return(stock, entry, exit_) if exit_ else None
        market_ret = period_return(market, entry, exit_) if exit_ else None
        sector_ret = period_return(sector, entry, exit_) if exit_ and sector else None
        rows.append(
            {
                "horizon": horizon,
                "exit_date": exit_.isoformat() if exit_ else None,
                "early_exit": int(exit_ is not None and exit_ < planned),
                "ret": ret,
                "iwm_ret": market_ret,
                "abnormal_iwm": ret - market_ret if None not in (ret, market_ret) else None,
                "sector_ret": sector_ret,
                "abnormal_sector": ret - sector_ret if None not in (ret, sector_ret) else None,
            }
        )
    return rows
