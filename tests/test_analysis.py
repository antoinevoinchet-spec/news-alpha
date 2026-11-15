"""Module d'analyse §8-§9, testé sur données fictives uniquement (décision M7)."""

import hashlib
import random

import pytest

from news_alpha import analysis, db, score
from news_alpha.analysis import (
    HORIZONS,
    Series,
    assign_quintiles,
    build_events,
    by_month,
    calendar_months,
    checks,
    information_coefficient,
    newey_west_t,
    quintile_sizes,
    reserved_confirms,
    simple_t,
    top_net,
    winsorize,
)

CALENDAR = calendar_months(analysis.ANALYSIS_START, analysis.ANALYSIS_END)


def filing(accession, cik, acceptance, composite, ar20, entry=None, ar5=None, early=False):
    return {"accession": accession, "cik": cik, "acceptance": acceptance,
            "entry_date": entry or acceptance[:10], "composite": composite,
            "ar": {1: ar20, 5: ar20 if ar5 is None else ar5, 20: ar20},
            "ar_sector": {h: None for h in HORIZONS}, "early_exit": early}


def campaign(months=CALENDAR, per_month=50, top_ar=lambda i: 0.03 + 0.005 * (i % 3 - 1)):
    """Composites distincts 0..n-1 par mois ; seuls les 20 % les mieux notés ont un rendement."""
    rows = []
    for i, month in enumerate(months):
        for k in range(per_month):
            ar = top_ar(i) if k >= per_month * 4 // 5 else 0.0
            rows.append(filing(f"{month}-{k:04d}", 1000 * i + k, f"{month}-10T08:00:00-04:00",
                               float(k), ar))
    return build_events(rows)


def test_analysis_period_has_ten_months_and_reserved_three():
    assert CALENDAR[0] == "2025-09" and CALENDAR[-1] == "2026-06" and len(CALENDAR) == 10
    assert calendar_months(analysis.RESERVED_START, analysis.RESERVED_END) == [
        "2026-07", "2026-08", "2026-09"]


def test_same_company_and_entry_session_is_one_event_with_the_strongest_composite():
    rows = [filing("a", 1, "2025-09-02T16:07:00-04:00", 0.5, 0.01, entry="2025-09-03"),
            filing("b", 1, "2025-09-02T16:14:00-04:00", -2.0, 0.01, entry="2025-09-03"),
            filing("c", 1, "2025-09-02T16:22:00-04:00", 1.0, 0.01, entry="2025-09-03"),
            filing("d", 2, "2025-09-02T16:30:00-04:00", 0.1, 0.02, entry="2025-09-03")]
    events = build_events(rows)
    assert [(e.accession, e.composite) for e in events] == [("b", -2.0), ("d", 0.1)]


def test_equal_strength_goes_to_the_first_filing():
    rows = [filing("z", 1, "2025-09-02T07:00:00-04:00", -1.5, 0.0),
            filing("a", 1, "2025-09-02T08:00:00-04:00", 1.5, 0.0)]
    assert build_events(rows)[0].accession == "z"


@pytest.mark.parametrize("n, sizes", [
    (10, [2, 2, 2, 2, 2]), (11, [2, 2, 3, 2, 2]), (12, [2, 3, 2, 3, 2]),
    (13, [2, 3, 3, 3, 2]), (14, [2, 3, 4, 3, 2]), (4, [0, 1, 2, 1, 0]),
])
def test_remainder_goes_to_the_middle_quintiles(n, sizes):
    assert quintile_sizes(n) == sizes


def test_quintile_sizes_always_add_up():
    assert all(sum(quintile_sizes(n)) == n for n in range(200))


def test_top_quintile_holds_the_highest_composites():
    events = campaign(months=["2025-09"], per_month=10)
    quintiles = assign_quintiles(events)
    assert sorted(e.composite for e in events if quintiles[e.accession] == 5) == [8.0, 9.0]


def test_ties_are_broken_by_the_fixed_seed_whatever_the_input_order():
    events = [e for e in campaign(months=["2025-09"], per_month=20)]
    tied = [type(e)(e.accession, e.cik, e.month, 0.0, e.ar, e.ar_sector, e.early_exit) for e in events]
    first = assign_quintiles(tied)
    random.Random(1).shuffle(tied)
    assert assign_quintiles(tied) == first
    assert sorted(first.values()) == [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4


def test_winsorize_caps_at_the_1st_and_99th_percentiles():
    capped = winsorize([float(v) for v in range(1, 101)])
    assert min(capped) == pytest.approx(1.99) and max(capped) == pytest.approx(99.01)
    assert capped[1:99] == [float(v) for v in range(2, 100)]


def test_one_extreme_return_cannot_carry_the_month():
    assert max(winsorize([0.0] * 99 + [100.0])) == pytest.approx(1.0)  # +10 000 % ramené à +100 %


def test_newey_west_and_simple_t_match_hand_computation():
    # moyenne 2,5 ; γ0 = 1,25 ; γ1 = 0,3125 ; S = 1,5625 ; t = 2,5 / √(1,5625 / 4) = 4
    assert newey_west_t([1.0, 2.0, 3.0, 4.0]) == pytest.approx(4.0)
    assert simple_t([1.0, 2.0, 3.0, 4.0]) == pytest.approx(2.5 / ((5 / 3) ** 0.5 / 2))
    assert newey_west_t([0.01] * 10) is None  # aucune variabilité : pas de t, donc pas de go


def test_go_when_the_top_quintile_beats_costs_every_month():
    events = campaign()
    criterion = top_net(by_month(events), CALENDAR)
    assert criterion.mean == pytest.approx(0.03 - 0.012, abs=1e-3)
    assert all(checks(events, criterion).values())


def test_no_go_with_only_six_positive_months():
    events = campaign(top_ar=lambda i: 0.05 if i < 6 else 0.0)
    passed = checks(events, top_net(by_month(events), CALENDAR))
    assert not passed["au moins 7 mois positifs sur 10"]


def test_no_go_below_300_events():
    events = campaign(per_month=29)
    assert not checks(events, top_net(by_month(events), CALENDAR))["au moins 300 événements"]


def test_costs_are_taken_once_from_the_top_quintile():
    months = by_month(campaign(top_ar=lambda i: 0.012))
    assert top_net(months, CALENDAR).mean == pytest.approx(0.0)
    assert top_net(months, CALENDAR, cost=0.03).mean == pytest.approx(-0.018)


@pytest.mark.parametrize("reserved_mean, confirmed", [(0.011, True), (0.010, True),
                                                      (0.009, False), (-0.01, False)])
def test_reserved_sample_needs_same_sign_and_half_the_size(reserved_mean, confirmed):
    reserved = Series({"2026-07": reserved_mean, "2026-08": reserved_mean, "2026-09": reserved_mean})
    assert reserved_confirms(0.02, reserved) is confirmed


def test_information_coefficient_is_a_rank_correlation():
    rows = [filing(f"a{k}", k, "2025-09-10T08:00:00-04:00", float(k), 0.0, ar5=k ** 3)
            for k in range(10)]
    assert information_coefficient(by_month(build_events(rows)), ["2025-09"]).mean == pytest.approx(1.0)


def test_analysis_code_matches_its_frozen_fingerprint():
    with open(analysis.__file__, "rb") as source:
        assert hashlib.sha256(source.read()).hexdigest() == score.ANALYSIS_SHA256


def store(conn, accession, cik, acceptance, composite=1.0, ar=0.01, selected=1, valid=1,
          scored=True, with_returns=True, reason=None):
    db.upsert(conn, "filings", {"accession": accession, "cik": cik, "acceptance_et": acceptance,
                                "selected": selected, "reject_reason": reason})
    if scored:
        db.upsert(conn, "scores", {"accession": accession, "cik": cik, "valid": valid,
                                   "composite": composite if valid else None, "cost_usd": 0.001})
    if with_returns:
        for horizon in HORIZONS:
            db.upsert(conn, "returns", {"accession": accession, "horizon": horizon, "cik": cik,
                                        "entry_date": acceptance[:10], "abnormal_iwm": ar,
                                        "early_exit": 0})


def test_population_and_follow_up_counts_come_from_the_database(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.sqlite")
    conn = db.connect()
    at = "2025-09-10T08:00:00-04:00"
    store(conn, "ok", 1, at)
    store(conn, "not-drawn", 2, at, selected=0, scored=False, with_returns=False)
    store(conn, "api-error", 3, at, scored=False)
    store(conn, "schema", 4, at, valid=0)
    store(conn, "no-price", 5, at, with_returns=False)
    store(conn, "excluded", 6, at, reason="prix_inferieur_2")
    store(conn, "before", 7, "2025-08-29T08:00:00-04:00")
    conn.execute("INSERT INTO scores (accession, cik, valid, composite, created_at, prompt_version,"
                 " model) VALUES ('ok', 1, 1, -3.0, 'x', 'v1', ?)", (score.MODEL,))
    rows, counts = analysis.load(conn, analysis.ANALYSIS_START, analysis.ANALYSIS_END)
    assert [r["accession"] for r in rows] == ["ok"] and rows[0]["composite"] == 1.0  # pas la note v1
    assert (counts["éligibles"], counts["éligibles non traités"], counts["traités sans note"],
            counts["rejets de schéma"], counts["rendements manquants"]) == (5, 1, 1, 1, 1)


def test_analysis_report_is_written_once_and_only_after_a_complete_campaign(monkeypatch, tmp_path,
                                                                           capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.sqlite")
    monkeypatch.setattr(analysis, "ANALYSIS_REPORT", tmp_path / "phase1_analysis.md")
    conn = db.connect()
    for event in campaign():
        store(conn, event.accession, event.cik, f"{event.month}-10T08:00:00-04:00",
              composite=event.composite, ar=event.ar[20])
    store(conn, "late", 99999, "2026-06-30T08:00:00-04:00", scored=False)  # pas encore noté
    conn.commit()
    assert analysis.main([]) == 1 and not analysis.ANALYSIS_REPORT.exists()
    assert "1 dépôts éligibles ne sont pas notés" in capsys.readouterr().err

    db.upsert(conn, "scores", {"accession": "late", "cik": 99999, "valid": 1, "composite": 0.0})
    conn.commit()
    assert analysis.main([]) == 0
    report = analysis.ANALYSIS_REPORT.read_text()
    assert "Critères remplis sur la période d'analyse" in report and "| 2026-06 | 51 |" in report
    assert analysis.main([]) == 1  # déjà close : jamais réécrite
    assert "existe déjà" in capsys.readouterr().err
