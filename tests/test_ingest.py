"""Index quotidien EDGAR : jours sans index, sans masquer un vrai refus."""

from datetime import date

import pytest

from news_alpha import ingest
from news_alpha.net import HttpError

S3_MISSING = '<?xml version="1.0"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>'
RATE_LIMITED = "<html><h1>Your Request Originates from an Undeclared Automated Tool</h1></html>"


def refuse_with(body: str):
    def fake_sec_get(url, cache_ok=None):
        raise HttpError(url, 403, body)
    return fake_sec_get


def test_weekend_or_holiday_without_index_yields_no_filing(monkeypatch):
    monkeypatch.setattr(ingest, "sec_get", refuse_with(S3_MISSING))
    assert ingest.daily_8k(date(2025, 4, 5)) == []


def test_other_refusal_is_never_silenced(monkeypatch):
    monkeypatch.setattr(ingest, "sec_get", refuse_with(RATE_LIMITED))
    with pytest.raises(HttpError):
        ingest.daily_8k(date(2025, 4, 7))


def test_co_registrants_are_grouped_under_one_filing(monkeypatch):
    index = (b"CIK|Company Name|Form Type|Date Filed|File Name\n----\n"
             b"1021635|OGE ENERGY CORP.|8-K|20250401|edgar/data/1021635/0000950170-25-048180.txt\n"
             b"74145|OKLAHOMA GAS & ELECTRIC CO|8-K|20250401|edgar/data/74145/0000950170-25-048180.txt\n"
             b"1000045|NICHOLAS FINANCIAL INC|8-K/A|20250401|edgar/data/1000045/0000950170-25-049123.txt\n")
    monkeypatch.setattr(ingest, "sec_get", lambda url, cache_ok=None: index)
    [filing] = ingest.daily_8k(date(2025, 4, 1))
    assert filing.accession == "0000950170-25-048180" and filing.ciks == [1021635, 74145]


# En-tête EDGAR réel (OGE, 0000950170-25-048180), SIC de la filiale modifié pour le test.
TWO_FILERS = """<SEC-HEADER>0000950170-25-048180.hdr.sgml : 20250401
<TYPE>8-K
<FILER>
<COMPANY-DATA>
<CONFORMED-NAME>OGE ENERGY CORP.
<CIK>0001021635
<ASSIGNED-SIC>4911
</COMPANY-DATA>
</FILER>
<FILER>
<COMPANY-DATA>
<CONFORMED-NAME>OKLAHOMA GAS & ELECTRIC CO
<CIK>0000074145
<ASSIGNED-SIC>4931
</COMPANY-DATA>
</FILER>
"""
# En-tête réel de FS KKR (BDC, 0001104659-25-030541), abrégé : pas de <ASSIGNED-SIC>.
BDC = """<SEC-HEADER>0001104659-25-030541.hdr.sgml : 20250401
<TYPE>8-K
<FILER>
<COMPANY-DATA>
<CONFORMED-NAME>FS KKR Capital Corp
<CIK>0001422183
<IRS-NUMBER>260627017
</COMPANY-DATA>
</FILER>
"""


def test_sic_is_read_from_the_header_for_the_followed_filer():
    assert ingest.assigned_sic(TWO_FILERS, 1021635) == 4911
    assert ingest.assigned_sic(TWO_FILERS, 74145) == 4931


def test_bdc_header_carries_no_sic():
    assert ingest.assigned_sic(BDC, 1422183) is None
