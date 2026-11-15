"""Étape 3 : texte du document principal et des exhibits 99.x, tronqué proprement."""

import re
from html.parser import HTMLParser

from news_alpha.net import sec_get

FULL_SUBMISSION = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}.txt"
MAX_CHARS = 40_000
TRUNCATION_MARK = "\n[... texte tronqué ...]"

SKIPPED_TAGS = {"script", "style", "head", "title", "ix:header"}  # ix:header = faits XBRL cachés
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
CELL_TAGS = {"td", "th"}
# Binaire encodé en texte (PDF, image, archive) : `<PDF>` puis `begin 644 nom`. Ses caractères
# n'ont aucun sens et peuvent imiter du HTML (cas réel : EX-99.2 d'UPB, 0002022626-25-000005).
UUENCODED = re.compile(r"\A\s*(<[A-Z]+>\s*)?begin [0-7]{3} ", re.I)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in SKIPPED_TAGS:
            self.skipping += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in CELL_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self.skipping = max(0, self.skipping - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Coupe sous max_chars à la dernière fin de paragraphe, de ligne ou de phrase."""
    if len(text) <= max_chars:
        return text, False
    head = text[: max_chars - len(TRUNCATION_MARK)]
    for separator in ("\n\n", "\n", ". "):
        cut = head.rfind(separator)
        if cut >= len(head) * 0.8:
            keep = cut + 1 if separator == ". " else cut
            return head[:keep].rstrip() + TRUNCATION_MARK, True
    return head + TRUNCATION_MARK, True


def documents(full_submission: str) -> list[tuple[str, str]]:
    """(type, contenu) de chaque <DOCUMENT> du fichier de dépôt complet, dans l'ordre."""
    found = []
    for block in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", full_submission, re.S):
        doc_type = re.search(r"<TYPE>([^\n<]+)", block)
        content = re.search(r"<TEXT>(.*?)</TEXT>", block, re.S)
        if doc_type and content:
            found.append((doc_type.group(1).strip(), content.group(1)))
    return found


def filing_text(cik: int, accession: str, max_chars: int = MAX_CHARS) -> tuple[str, bool]:
    """Texte du 8-K puis de ses EX-99.x ; renvoie (texte, tronqué ?)."""
    url = FULL_SUBMISSION.format(cik=cik, folder=accession.replace("-", ""), accession=accession)
    body = sec_get(url, cache_ok=lambda b: b"<DOCUMENT>" in b)
    if body is None:
        return "", False
    sections = []
    for doc_type, content in documents(body.decode("utf-8", errors="replace")):
        if (doc_type == "8-K" or doc_type.startswith("EX-99")) and not UUENCODED.match(content):
            is_html = re.search(r"<(html|body|div|p|table)\b", content, re.I)
            text = html_to_text(content) if is_html else content.strip()
            sections.append(f"=== {doc_type} ===\n{text}")
    return truncate("\n\n".join(sections), max_chars)
