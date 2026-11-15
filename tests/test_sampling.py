"""Tirage de l'échantillon : reproductible et réparti sur toute la période."""

from news_alpha.run import draw_order

# 300 dépôts fictifs sur 15 jours, numérotés dans l'ordre chronologique.
ACCESSIONS = [f"0000000000-25-{n:06d}" for n in range(300)]


def test_draw_is_reproducible_and_independent_of_input_order():
    assert draw_order(ACCESSIONS) == draw_order(list(reversed(ACCESSIONS)))


def test_sample_is_spread_over_the_period_not_the_first_filings():
    sample = draw_order(ACCESSIONS)[:20]
    assert sample != ACCESSIONS[:20]
    positions = sorted(ACCESSIONS.index(a) for a in sample)
    assert positions[0] < 100 and positions[-1] >= 200  # début et fin de période représentés


def test_every_filing_is_drawn_exactly_once():
    assert sorted(draw_order(ACCESSIONS)) == ACCESSIONS
