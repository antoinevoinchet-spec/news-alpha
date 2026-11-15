"""Extraction du texte des dépôts et troncature propre."""

from news_alpha import extract
from news_alpha.extract import TRUNCATION_MARK, documents, html_to_text, truncate

SUBMISSION = """<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>8-K
<SEQUENCE>1
<TEXT>
<html><head><title>8-K</title></head><body>
<div style="display:none"><ix:header><ix:hidden>dei:Hidden</ix:hidden></ix:header></div>
<p>Item&#160;2.02 Results</p><table><tr><td>Revenue</td><td>$10</td></tr></table>
<script>var x = 1;</script>
</body></html>
</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-99.1
<SEQUENCE>2
<TEXT>
Press release text
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""


def test_documents_are_split_by_type_in_order():
    assert [doc_type for doc_type, _ in documents(SUBMISSION)] == ["8-K", "EX-99.1"]


def test_html_to_text_keeps_visible_text_only():
    text = html_to_text(documents(SUBMISSION)[0][1])
    assert "Item 2.02 Results" in text
    assert "Revenue $10" in text
    assert "Hidden" not in text and "var x" not in text and "8-K" not in text


def test_short_text_is_not_truncated():
    assert truncate("abc", 100) == ("abc", False)


def test_truncation_cuts_on_paragraph_boundary_within_limit():
    text = "A" * 900 + "\n\n" + "B" * 500
    cut, truncated = truncate(text, 1000)
    assert truncated and len(cut) <= 1000
    assert cut == "A" * 900 + TRUNCATION_MARK


# Cas réel UPB (0002022626-25-000005) : EX-99.2 est un PDF encodé en texte (uuencode), dont
# les caractères contiennent par hasard '<p' et '<![', qui faisaient planter le lecteur HTML.
WITH_ENCODED_PDF = SUBMISSION.replace("</SEC-DOCUMENT>", """<DOCUMENT>
<TYPE>EX-99.2
<SEQUENCE>3
<TEXT>
<PDF>
begin 644 upb-ex99_2.pdf
M)5!$1BTQ<p RBWO70!I&W/T<![ED8OBIHK-:+EU]
end
</PDF>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>""")


def test_encoded_binary_exhibit_is_skipped_not_parsed(monkeypatch):
    monkeypatch.setattr(extract, "sec_get", lambda url, cache_ok=None: WITH_ENCODED_PDF.encode())
    text, truncated = extract.filing_text(1, "0000000000-25-000001")
    assert "Press release text" in text and "Revenue" in text
    assert "EX-99.2" not in text and "begin 644" not in text and not truncated
