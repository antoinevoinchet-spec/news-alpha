"""Point d'entrée : ouverture de la première séance après acceptation et scoring."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from news_alpha.ingest import parse_acceptance
from news_alpha.prices import Bar, move_timing, pre_entry_move
from news_alpha.returns import entry_session, horizon_returns

ET = ZoneInfo("America/New_York")
# Mardi 1er -> lundi 7 avril 2025 (week-end des 5-6 avril).
SESSIONS = [date(2025, 4, d) for d in (1, 2, 3, 4, 7)]
WEDNESDAY, THURSDAY = date(2025, 4, 2), date(2025, 4, 3)


def at(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=ET)


def test_edgar_acceptance_is_utc_converted_to_new_york():
    # Cas réels : API submissions (UTC) contre en-tête du dépôt (heure de New York).
    assert parse_acceptance("2025-04-01T15:36:51.000Z") == at(date(2025, 4, 1), 11, 36, 51)
    assert parse_acceptance("2025-04-01T20:01:19.000Z") == at(date(2025, 4, 1), 16, 1, 19)


def test_filing_before_open_enters_same_day_open():
    assert entry_session(at(WEDNESDAY, 9, 0), SESSIONS) == WEDNESDAY


def test_filing_accepted_at_0930_sharp_misses_that_open():
    assert entry_session(at(WEDNESDAY, 9, 30, 0), SESSIONS) == THURSDAY


# Hiver (heure normale, UTC-5) : tous les cas ci-dessus datent d'avril (UTC-4).
WINTER = [date(2026, 1, 30), date(2026, 2, 2)]  # vendredi, lundi


def test_winter_acceptance_is_converted_with_standard_time():
    # Comparer l'heure affichée : deux datetimes à fuseau désignant le même instant sont
    # égaux, or le pipeline classe les dépôts sur l'heure de New York (.time(), .date()).
    accepted = parse_acceptance("2026-01-30T13:00:43.000Z")
    assert (accepted.date(), accepted.time()) == (WINTER[0], time(8, 0, 43))


def test_winter_open_is_1430_utc():
    assert entry_session(parse_acceptance("2026-01-30T14:00:00.000Z"), WINTER) == WINTER[0]
    assert entry_session(parse_acceptance("2026-01-30T14:30:00.000Z"), WINTER) == WINTER[1]


def test_filing_at_1559_enters_next_open_not_same_day_close():
    assert entry_session(at(WEDNESDAY, 15, 59), SESSIONS) == THURSDAY


def test_filing_at_1601_enters_next_open():
    assert entry_session(at(WEDNESDAY, 16, 1), SESSIONS) == THURSDAY


def test_friday_after_close_enters_monday():
    assert entry_session(at(date(2025, 4, 4), 16, 1), SESSIONS) == date(2025, 4, 7)


def test_scoring_finishing_after_open_pushes_entry_to_next_session():
    ready_at = at(WEDNESDAY, 9, 29, 50) + timedelta(seconds=20)
    assert entry_session(ready_at, SESSIONS) == THURSDAY


def bar(day: date, open_: float, close: float) -> Bar:
    return Bar(day, open_, max(open_, close), min(open_, close), close, 1e6)


BARS = [bar(date(2025, 4, 1), 10, 10), bar(WEDNESDAY, 10, 11), bar(THURSDAY, 12, 13)]


def test_price_move_uses_only_prices_before_entry():
    # Dépôt mercredi 16:01 : clôture mardi (10) -> clôture mercredi (11), jamais jeudi.
    assert pre_entry_move(BARS, WEDNESDAY, THURSDAY) == 11 / 10 - 1
    # Dépôt mercredi 09:00 : entrée mercredi, dernier prix connu = clôture de mardi.
    assert pre_entry_move(BARS, WEDNESDAY, WEDNESDAY) == 0


def test_returns_buy_entry_open_and_sell_close_of_nth_session():
    stock = {b.date: b for b in BARS}
    market = {b.date: bar(b.date, 100, 100) for b in BARS}
    one_day = horizon_returns(WEDNESDAY, SESSIONS, stock, market, None)[0]
    assert one_day["horizon"] == 1 and one_day["exit_date"] == "2025-04-02"
    assert one_day["ret"] == 11 / 10 - 1 and one_day["abnormal_iwm"] == 11 / 10 - 1


@pytest.mark.parametrize("hour, minute, entry, timing", [
    (9, 0, WEDNESDAY, "no_session"),  # avant l'ouverture : entrée le jour même
    (11, 0, THURSDAY, "during"),
    (15, 59, THURSDAY, "during"),
    (16, 1, THURSDAY, "after_close"),  # la séance du jour a eu lieu avant le dépôt
])
def test_move_timing_tells_whether_the_stock_traded_since_the_filing(hour, minute, entry, timing):
    assert move_timing(BARS, at(WEDNESDAY, hour, minute), entry) == timing


def test_weekend_filing_has_no_session_before_monday_entry():
    saturday = date(2025, 4, 5)
    assert move_timing(BARS, at(saturday, 10, 0), date(2025, 4, 7)) == "no_session"


def test_delisted_stock_is_sold_at_its_last_close_and_flagged():
    # Le titre ne cote plus après mercredi : à 3 séances (mer -> ven), vente à la clôture de mercredi.
    stock = {b.date: b for b in BARS[:2]}
    market = {day: bar(day, 100, 100 + i) for i, day in enumerate(SESSIONS)}
    rows = {r["horizon"]: r for r in horizon_returns(WEDNESDAY, SESSIONS, stock, market, None)}
    assert rows[1]["early_exit"] == 0 and rows[1]["exit_date"] == "2025-04-02"
    five = rows[5]  # mer 2 -> mar 8 avril : hors calendrier connu ici
    assert five["ret"] is None
    sessions = SESSIONS + [date(2025, 4, 8)]
    market[date(2025, 4, 8)] = bar(date(2025, 4, 8), 100, 110)
    five = {r["horizon"]: r for r in horizon_returns(WEDNESDAY, sessions, stock, market, None)}[5]
    assert five["early_exit"] == 1 and five["exit_date"] == "2025-04-02"
    assert five["ret"] == 11 / 10 - 1
    assert five["iwm_ret"] == market[WEDNESDAY].close / 100 - 1  # même période de détention


def test_no_bar_on_entry_day_means_no_return():
    stock = {THURSDAY: bar(THURSDAY, 12, 13)}
    market = {day: bar(day, 100, 100) for day in SESSIONS}
    one_day = horizon_returns(WEDNESDAY, SESSIONS, stock, market, None)[0]
    assert one_day["ret"] is None and one_day["early_exit"] == 0
