"""Scoring : une réponse payée est enregistrée tout de suite et jamais redemandée."""

import sqlite3
from datetime import date, datetime

import pytest

from news_alpha import db, run, score
from news_alpha.ingest import ET, Filing
from news_alpha.prices import Bar

VALID = score.Score8K(event_type="earnings", direction=1, magnitude=2, confidence=0.5,
                      priced_in=0.5, horizon_days=5, key_facts=["x"], rationale="y")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite")
    calls = []

    def fake_score_filing(client, prompt):
        calls.append(prompt)
        return score.ScoreResult("{}", VALID, None, 100, 20, 0.0002, 1.5)

    monkeypatch.setattr(score, "score_filing", fake_score_filing)
    filing = Filing("0000000000-25-000001", [1], "ACME", date(2025, 9, 2), cik=1,
                    acceptance=datetime(2025, 9, 2, 8, 0, tzinfo=ET))
    return db.connect(), filing, calls


def test_score_is_committed_before_anything_else_can_fail(setup):
    conn, filing, _ = setup
    run.score_once(conn, None, filing, "text", 0.0, "no_session", calls_made=0)
    other = sqlite3.connect(db.DB_PATH)  # autre connexion = ce que voit une relance après arrêt
    assert other.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1


def test_already_scored_filing_is_never_sent_again(setup):
    conn, filing, calls = setup
    assert run.score_once(conn, None, filing, "text", 0.0, "no_session", calls_made=0) == (1.5, True)
    assert run.score_once(conn, None, filing, "text", 0.0, "no_session", calls_made=1) == (1.5, False)
    assert len(calls) == 1


def test_call_cap_stops_before_paying(setup):
    conn, filing, calls = setup
    with pytest.raises(SystemExit):
        run.score_once(conn, None, filing, "text", 0.0, "no_session", calls_made=run.MAX_LLM_CALLS)
    assert calls == []


def bar(day: date, close: float) -> Bar:
    return Bar(day, close, close, close, close, 1_000_000)


def test_market_screen_never_sees_filing_day_prices_and_dates_splits_to_price_day(setup, monkeypatch):
    conn, filing, _ = setup  # dépôt du mardi 02/09/2025
    filing.ticker = "ACME"
    sessions = [date(2025, 7, d) for d in range(21, 32) if date(2025, 7, d).weekday() < 5]
    sessions += [date(2025, 8, d) for d in range(1, 30) if date(2025, 8, d).weekday() < 5]
    raw = [bar(day, 20.0) for day in sessions] + [bar(date(2025, 9, 2), 1000.0)]  # prix aberrant le jour J
    split_until = []
    monkeypatch.setattr(run.filters, "shares_outstanding", lambda cik, filed: (50e6, date(2025, 8, 1)))
    monkeypatch.setattr(run.prices, "daily_bars", lambda *args, **kwargs: (raw, "alpaca:sip"))
    monkeypatch.setattr(run.prices, "split_factor",
                        lambda symbol, since, until: split_until.append(until) or 1.0)
    reason, metrics, _ = run.market_screen(conn, filing)
    assert reason is None and metrics["prev_close"] == 20.0 and metrics["market_cap"] == 1e9
    assert split_until == [date(2025, 8, 29)]  # vendredi, veille du dépôt (lundi 01/09 férié)


def test_index_is_read_past_the_end_for_evening_filings(setup, monkeypatch):
    conn, _, _ = setup
    days = []
    monkeypatch.setattr(run.ingest, "daily_8k", lambda day: days.append(day) or [])
    run.screen_period(conn, date(2026, 6, 29), date(2026, 6, 30))
    assert days[-1] == date(2026, 7, 4)


def test_universe_uses_the_sic_of_the_filing_date_not_today(monkeypatch):
    cover = ('<ix:nonNumeric name="dei:Security12bTitle" contextRef="c">Class A ordinary shares'
             '</ix:nonNumeric><ix:nonNumeric name="dei:TradingSymbol" contextRef="c">GATE'
             '</ix:nonNumeric><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c">'
             'Nasdaq</ix:nonNumeric>')
    header = "<FILER>\n<COMPANY-DATA>\n<CIK>0001838513\n<ASSIGNED-SIC>6770\n</COMPANY-DATA>\n</FILER>"

    def fake_details(filing):
        filing.acceptance = datetime(2025, 4, 1, 8, 0, tzinfo=ET)
        filing.items, filing.primary_doc, filing.sic = ["8.01"], "d.htm", 4911  # SIC actuel
        return True

    monkeypatch.setattr(run.ingest, "add_details", fake_details)
    monkeypatch.setattr(run.ingest, "document", lambda cik, accession, name: cover)
    monkeypatch.setattr(run.ingest, "filing_header", lambda cik, accession: header)
    filing = Filing("0001193125-25-071789", [1838513], "Marblegate", date(2025, 4, 1))
    assert run.cheap_filter(filing, date(2025, 4, 1), date(2025, 4, 15)) == "spac_sic_6770"
    assert filing.sic == 6770


def test_existing_database_gains_the_v2_columns_without_losing_scores(monkeypatch, tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE scores (id INTEGER PRIMARY KEY, accession TEXT NOT NULL, "
                "cik INTEGER NOT NULL, valid INTEGER NOT NULL, created_at TEXT NOT NULL, "
                "prompt_version TEXT NOT NULL, model TEXT NOT NULL)")
    old.execute("INSERT INTO scores VALUES (1, 'a', 1, 1, 'x', 'v1', 'm')")
    old.commit()
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = db.connect()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(scores)")}
    assert {"prompt_sha256", "price_move_timing"} <= columns
    assert conn.execute("SELECT prompt_version, prompt_sha256 FROM scores").fetchone()[:] == ("v1", None)


def test_filing_accepted_outside_the_range_is_left_out(monkeypatch):
    def fake_details(filing):
        filing.acceptance = datetime(2025, 3, 31, 18, 0, tzinfo=ET)  # indexé au 01/04
        filing.items = ["8.01"]
        return True

    monkeypatch.setattr(run.ingest, "add_details", fake_details)
    monkeypatch.setattr(run.ingest, "document", lambda *args: pytest.fail("aucune lecture attendue"))
    filing = Filing("0000000000-25-000009", [1], "ACME", date(2025, 4, 1))
    assert run.cheap_filter(filing, date(2025, 4, 1), date(2025, 4, 15)) == "acceptation_hors_plage"


def test_changed_prompt_stops_the_run_before_any_request(monkeypatch, capsys):
    monkeypatch.setattr(run.score, "TEMPLATE_SHA256", "0" * 64)
    monkeypatch.setattr(run, "load_env", lambda path: pytest.fail("aucune requête attendue"))
    assert run.main(["--start", "2025-09-02", "--end", "2025-09-03"]) == 1
    assert "a changé depuis son gel" in capsys.readouterr().err


def test_examined_count_equals_eligible_plus_exclusions(setup, monkeypatch):
    conn, _, _ = setup
    days = {date(2026, 6, 30): ["a", "b"], date(2026, 7, 1): ["c", "d"]}  # c : soir du 30/06
    monkeypatch.setattr(run.ingest, "daily_8k", lambda day: [
        Filing(name, [1], "ACME", day) for name in days.get(day, [])])
    reasons = {"a": "items_non_retenus", "b": None, "c": None, "d": "acceptation_hors_plage"}
    monkeypatch.setattr(run, "cheap_filter", lambda filing, start, end: reasons[filing.accession])
    monkeypatch.setattr(run, "market_screen", lambda conn, filing: (None, {}, []))
    monkeypatch.setattr(run, "save_filing", lambda *args, **kwargs: None)
    eligible, exclusions, examined = run.screen_period(conn, date(2026, 6, 30), date(2026, 6, 30))
    assert examined == len(eligible) + sum(exclusions.values()) == 3


def test_previous_month_evening_filings_are_never_overwritten(setup, monkeypatch):
    # Cas réel : l'index du 01/10 contient des dépôts du 30/09 au soir ; les enregistrer comme
    # « hors plage » écrasait leur ligne de septembre, déjà notée.
    conn, _, _ = setup
    monkeypatch.setattr(run.ingest, "daily_8k", lambda day: [
        Filing("sept-30-soir", [1], "ACME", day), Filing("oct-1", [2], "BETA", day)
    ] if day == date(2025, 10, 1) else [])
    reasons = {"sept-30-soir": "acceptation_hors_plage", "oct-1": "items_non_retenus"}
    monkeypatch.setattr(run, "cheap_filter", lambda filing, start, end: reasons[filing.accession])
    saved = []
    monkeypatch.setattr(run, "save_filing", lambda conn, filing, reason, **kw: saved.append(filing.accession))
    _, exclusions, examined = run.screen_period(conn, date(2025, 10, 1), date(2025, 10, 31))
    assert saved == ["oct-1"] and examined == 1 and "acceptation_hors_plage" not in exclusions
