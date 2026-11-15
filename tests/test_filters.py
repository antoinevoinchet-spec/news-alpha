"""Cotation lue sur le dépôt ; nombre d'actions connu à la date du 8-K, et assez récent."""

from datetime import date

import pytest

from news_alpha.filters import (
    clean_symbol,
    cover_listing,
    cover_shares,
    exchange_code,
    is_common_equity,
    latest_shares,
    sic_exclusion,
)
from news_alpha.prices import split_ratio


def tag(name: str, context: str, value: str) -> str:
    return f'<ix:nonNumeric name="dei:{name}" contextRef="{context}">{value}</ix:nonNumeric>'


def cover(*securities: tuple[str, str, str, str], cik: str = "0001526113") -> str:
    """Page de garde iXBRL minimale : (contexte, titre, ticker, bourse affichée) par titre."""
    rows = "".join(
        f"<tr><td>{tag('Security12bTitle', c, title)}</td><td>{tag('TradingSymbol', c, symbol)}"
        f"</td><td>{tag('SecurityExchangeName', c, exchange)}</td></tr>"
        for c, title, symbol, exchange in securities
    )
    return tag("EntityCentralIndexKey", "c-1", cik) + f"<table>{rows}</table>"


def test_cover_listing_reads_common_stock_symbol_exchange_and_registrant():
    document = cover(
        ("c-2", "Common Stock, $0.01 par value per share", "GNL", "New\n    York Stock Exchange"),
        ("c-3", "7.25% Series A Cumulative Redeemable Preferred Stock", "GNL\n    PR A", "NYSE"),
    )
    assert cover_listing(document) == (1526113, "GNL", "NYSE")


def test_spac_units_listed_first_are_skipped_for_the_ordinary_shares():
    document = cover(
        ("c-2", "Units, each consisting of one Class A Ordinary Share and one Right", "CLRCU", "Nasdaq"),
        ("c-3", "Class A Ordinary Shares, par value $0.0001 per share", "CLRC", "Nasdaq"),
    )
    assert cover_listing(document)[1:] == ("CLRC", "Nasdaq")


def test_issuer_with_only_listed_notes_is_not_listed():
    document = cover(("c-2", "Medium-Term Notes, Series B Stated Maturity Date 2028", "TM/28",
                      "New York Stock Exchange"))
    assert cover_listing(document) is None


def test_co_registrant_takes_the_cik_of_the_listed_security_context():
    # Cas OGE : la filiale (74145) apparaît en premier, la société cotée (1021635) ensuite.
    document = (tag("EntityCentralIndexKey", "c-1", "0000074145")
                + tag("EntityCentralIndexKey", "c-2", "0001021635")
                + cover(("c-2", "Common Stock", "OGE", "New York Stock Exchange"), cik="0000074145"))
    assert cover_listing(document) == (1021635, "OGE", "NYSE")


def test_filing_without_listed_common_stock_is_not_listed():
    assert cover_listing("<p>No securities registered under Section 12(b)</p>") is None
    assert cover_listing(cover(("c-2", "Common Stock", "ABCD", "NYSE Arca"))) is None


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Common Stock, without par value", True),
        ("Class A common stock, $0.0001 par value", True),
        ("Common Shares of Beneficial Interest", True),
        ("Common units representing limited partner interests", True),
        ("Ordinary Shares, no par value", True),
        ("Class A Limited Voting Shares", True),
        ("American Depositary Shares, each representing one Ordinary Share", False),
        ("Warrants to purchase Common Stock", False),
        ("Depositary Shares, each representing 1/1000th of a share of Preferred Stock", False),
        ("0.524% Notes due 2028", False),
        ("Rights, each entitling the holder to one-tenth of one ordinary share", False),
    ],
)
def test_security_titles_are_classified(title, expected):
    assert is_common_equity(title) is expected


@pytest.mark.parametrize(
    "display, code",
    [
        ("The Nasdaq Stock Market LLC", "Nasdaq"),
        ("Nasdaq Global Select Market", "Nasdaq"),
        ("NASDAQ", "Nasdaq"),
        ("New York Stock Exchange", "NYSE"),
        ("NYSE", "NYSE"),
        ("NYSE American LLC", "NYSE American"),
        ("NYSE MKT LLC", "NYSE American"),
        ("NYSE Arca, Inc.", None),
        ("NYSE Texas, Inc.", None),
        ("OTCQX", None),
    ],
)
def test_exchange_display_names_are_normalised(display, code):
    assert exchange_code(display) == code

# Cas réel Alcoa (CIK 1675149) : aucune valeur publiée entre mai 2024 et février 2026.
ALCOA = [
    {"end": "2024-04-29", "filed": "2024-05-02", "val": 179_559_688},
    {"end": "2026-02-20", "filed": "2026-02-26", "val": 263_839_742},
]


def test_value_older_than_180_days_is_ignored():
    assert latest_shares(ALCOA, date(2025, 4, 1)) is None


def test_value_published_after_the_filing_is_never_used():
    assert latest_shares(ALCOA, date(2024, 6, 1)) == (179_559_688, date(2024, 4, 29))


def test_value_published_the_same_day_as_the_8k_is_not_yet_known():
    facts = [{"end": "2025-03-01", "filed": "2025-03-05", "val": 1_000_000},
             {"end": "2025-04-01", "filed": "2025-04-02", "val": 2_000_000}]
    assert latest_shares(facts, date(2025, 4, 2)) == (1_000_000, date(2025, 3, 1))


def test_most_recent_value_known_before_filing_is_used():
    facts = [
        {"end": "2024-11-04", "filed": "2024-11-06", "val": 230_633_265},
        {"end": "2025-02-24", "filed": "2025-02-27", "val": 230_783_453},
        {"end": "2025-05-02", "filed": "2025-05-08", "val": 223_208_729},
    ]
    assert latest_shares(facts, date(2025, 4, 1)) == (230_783_453, date(2025, 2, 24))


def shares_tag(context: str, value: str, scale: str = "0") -> str:
    return (f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" '
            f'contextRef="{context}" unitRef="shares" scale="{scale}">{value}</ix:nonFraction>')


def test_cover_shares_sums_classes_once_each():
    # Société à deux classes ; la classe A est répétée ailleurs dans le document.
    document = (shares_tag("c-5", "41,250,113") + shares_tag("c-6", "<span>22,881,295</span>")
                + shares_tag("c-5", "41,250,113"))
    assert cover_shares(document) == 41_250_113 + 22_881_295


def test_cover_shares_applies_scale_and_ignores_other_facts():
    document = shares_tag("c-1", "258.9", scale="6") + (
        '<ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-2">5,000</ix:nonFraction>')
    assert cover_shares(document) == 258_900_000
    assert cover_shares("<p>no XBRL</p>") is None


def test_split_ratio_applies_only_splits_after_the_share_count_date():
    # Cas réel ANGI : regroupement 1 pour 10 le 24/03/2025, nombre d'actions daté de février.
    actions = {"reverse_splits": [{"ex_date": "2025-03-24", "new_rate": 1, "old_rate": 10}],
               "forward_splits": [{"ex_date": "2024-06-10", "new_rate": 10, "old_rate": 1}]}
    assert split_ratio(actions, date(2025, 2, 25), date(2025, 4, 1)) == pytest.approx(0.1)
    assert split_ratio(actions, date(2025, 3, 24), date(2025, 4, 1)) == 1.0
    assert split_ratio({}, date(2025, 2, 25), date(2025, 4, 1)) == 1.0


@pytest.mark.parametrize(
    "raw, symbol",
    [
        ("WINV‌", "WINV"),  # cas réel : caractère invisible U+200C
        ("QTI 1", "QTI"),        # cas réel : renvoi de note de bas de page
        ("ABC(1)", "ABC"),
        ("ABC*", "ABC"),
        ("BRK B", "BRK B"),      # classe d'actions : conservée, convertie en BRK.B pour Alpaca
        ("XOM28", "XOM28"),      # chiffres collés : jamais retirés
    ],
)
def test_cover_symbols_are_cleaned(raw, symbol):
    assert clean_symbol(raw) == symbol


def test_self_closing_nil_tag_does_not_swallow_the_next_class():
    # Cas réel Movado : une balise vide avant la classe A faisait lire 6,5 M au lieu de 22 M.
    document = ('<ix:nonFraction name="us-gaap:CommitmentsAndContingencies" contextRef="c-9" '
                'xsi:nil="true"/>' + shares_tag("c-5", "15,684,480") + shares_tag("c-6", "6,458,376"))
    assert cover_shares(document) == 15_684_480 + 6_458_376


def test_implausible_share_total_is_rejected():
    assert cover_shares(shares_tag("c-1", "1")) is None  # cas réel FNVT


def test_single_listed_security_tagged_in_separate_contexts_is_paired():
    # Cas réel Northpointe : titre et ticker dans un contexte, bourse dans un autre.
    document = (tag("Security12bTitle", "c-2", "Common Stock, no par value")
                + tag("TradingSymbol", "c-2", "NPB")
                + tag("SecurityExchangeName", "c-3", "New York Stock Exchange"))
    assert cover_listing(document)[1:] == ("NPB", "NYSE")


def test_single_untitled_equity_symbol_is_accepted_but_not_a_note():
    blackberry = tag("TradingSymbol", "c-2", "BB") + tag("SecurityExchangeName", "c-2", "NYSE")
    assert cover_listing(blackberry)[1:] == ("BB", "NYSE")
    note = tag("TradingSymbol", "c-2", "TM/28") + tag("SecurityExchangeName", "c-2", "NYSE")
    assert cover_listing(note) is None


def test_two_securities_without_complete_rows_stay_ambiguous():
    document = (tag("TradingSymbol", "c-2", "ABC") + tag("TradingSymbol", "c-3", "ABCW")
                + tag("SecurityExchangeName", "c-4", "Nasdaq"))
    assert cover_listing(document) is None


@pytest.mark.parametrize("sic, reason", [
    (None, "sans_code_sic"),  # BDC
    (6770, "spac_sic_6770"),
    (6798, None),  # REIT : gardé
    (4911, None),
])
def test_spacs_and_filers_without_sic_are_outside_the_universe(sic, reason):
    assert sic_exclusion(sic) == reason
