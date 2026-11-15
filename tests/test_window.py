"""Échantillon réservé : ni filtré ni noté avant la clôture de l'analyse et le 29/10/2026."""

from datetime import date

import pytest

from news_alpha import run
from news_alpha.window import date_guard

BEFORE, AFTER = date(2026, 10, 25), date(2026, 10, 29)


def test_analysis_period_runs_without_option():
    assert date_guard(date(2025, 9, 1), date(2026, 6, 30), False, BEFORE, False) is None


@pytest.mark.parametrize("start, end", [
    (date(2026, 6, 29), date(2026, 7, 1)),  # chevauche le début du réservé
    (date(2026, 7, 1), date(2026, 7, 15)),
    (date(2026, 10, 1), date(2026, 10, 2)),  # au-delà de la fenêtre
])
def test_reserved_dates_need_the_reserved_option(start, end):
    assert "--reserved" in date_guard(start, end, False, AFTER, True)


def test_reserved_sample_waits_for_october_29_and_the_closed_analysis():
    start, end = date(2026, 7, 1), date(2026, 9, 30)
    assert "2026-10-29" in date_guard(start, end, True, BEFORE, True)
    assert "pas close" in date_guard(start, end, True, AFTER, False)
    assert date_guard(start, end, True, AFTER, True) is None


def test_reserved_option_cannot_mix_in_analysis_dates():
    assert date_guard(date(2026, 6, 1), date(2026, 9, 30), True, AFTER, True)


def test_refused_range_stops_before_any_request(monkeypatch, capsys):
    monkeypatch.setattr(run, "load_env", lambda path: pytest.fail("aucune requête attendue"))
    assert run.main(["--start", "2026-07-01", "--end", "2026-07-02"]) == 1
    assert "réservé" in capsys.readouterr().err
