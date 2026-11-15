"""Étape 1 : 8-K EDGAR d'une plage de dates, avec acceptanceDateTime."""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from news_alpha.net import HttpError, sec_get

ET = ZoneInfo("America/New_York")
DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{ymd}.idx"
SUBMISSIONS = "https://data.sec.gov/submissions/{name}"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
FILER_BLOCK = re.compile(r"<FILER>(.*?)</FILER>", re.S)


@dataclass
class Filing:
    accession: str
    ciks: list[int]  # un même 8-K peut être déposé par plusieurs sociétés liées
    company: str
    filing_date: date
    cik: int = 0
    form: str = "8-K"
    acceptance: datetime | None = None  # heure de New York
    items: list[str] = field(default_factory=list)
    primary_doc: str | None = None
    sic: int | None = None
    ticker: str | None = None
    exchange: str | None = None


def daily_8k(day: date) -> list[Filing]:
    """8-K de l'index quotidien EDGAR (8-K/A exclus) ; [] les jours sans index."""
    url = DAILY_INDEX.format(
        year=day.year, quarter=(day.month - 1) // 3 + 1, ymd=day.strftime("%Y%m%d")
    )
    try:
        body = sec_get(url, cache_ok=lambda b: b"|8-K|" in b)
    except HttpError as err:
        # Week-end ou jour férié : le stockage d'EDGAR répond « AccessDenied » au lieu
        # de 404. Tout autre refus (limite de débit...) reste une erreur.
        if err.status == 403 and "<Code>AccessDenied</Code>" in err.body:
            return []
        raise
    if body is None:
        return []
    by_accession: dict[str, Filing] = {}
    for line in body.decode("latin-1").splitlines():
        parts = line.split("|")
        if len(parts) != 5 or parts[2] != "8-K" or not parts[0].isdigit():
            continue
        accession = parts[4].rsplit("/", 1)[-1].removesuffix(".txt")
        if accession in by_accession:
            by_accession[accession].ciks.append(int(parts[0]))
        else:
            by_accession[accession] = Filing(accession, [int(parts[0])], parts[1], day)
    return list(by_accession.values())


def document(cik: int, accession: str, name: str) -> str:
    """Un document d'un dépôt EDGAR (HTML), ou '' s'il est introuvable."""
    url = ARCHIVE.format(cik=cik, folder=accession.replace("-", ""), name=name)
    body = sec_get(url, cache_ok=lambda b: len(b) > 0)
    return body.decode("utf-8", errors="replace") if body else ""


def filing_header(cik: int, accession: str) -> str:
    """En-tête EDGAR du dépôt (`.hdr.sgml`), ou '' s'il est introuvable."""
    return document(cik, accession, f"{accession}.hdr.sgml")


def assigned_sic(header: str, cik: int) -> int | None:
    """Code SIC du déclarant `cik` à la date du dépôt, d'après l'en-tête EDGAR.

    L'API submissions donne le code actuel, qui a pu changer depuis (audit C26).
    None si l'en-tête n'en porte pas : la SEC n'en attribue pas aux BDC (FSK, OBDC…).
    """
    for block in FILER_BLOCK.findall(header):
        found_cik = re.search(r"<CIK>\s*(\d+)", block)
        if found_cik and int(found_cik.group(1)) == cik:
            sic = re.search(r"<ASSIGNED-SIC>\s*(\d+)", block)
            return int(sic.group(1)) if sic else None
    return None


def parse_acceptance(value: str) -> datetime:
    """'2025-04-01T15:36:51.000Z' (UTC) -> datetime de New York.

    Vérifié contre l'en-tête <ACCEPTANCE-DATETIME> du dépôt, en heure de New York
    (20250401113651 pour cet exemple) : voir DECISIONS.md.
    """
    return datetime.fromisoformat(value).astimezone(ET)


def _submissions(name: str) -> dict | None:
    body = sec_get(SUBMISSIONS.format(name=name), cache_ok=lambda b: b'"accessionNumber"' in b)
    return json.loads(body) if body else None


def _pages(company: dict, since: date, until: date) -> list[dict]:
    """Pages de dépôts d'une société : récentes, plus les anciennes qui recoupent [since, until]."""
    pages = [company["filings"]["recent"]]
    for older in company["filings"].get("files", []):
        if older["filingFrom"] <= until.isoformat() and since.isoformat() <= older["filingTo"]:
            pages.append(_submissions(older["name"]) or {})
    return pages


def latest_periodic_report(cik: int, before: date, since: date) -> tuple[str, str, date] | None:
    """Dernier 10-K ou 10-Q déposé dans [since, before[ : (accession, document, date)."""
    company = _submissions(f"CIK{cik:010d}.json")
    if company is None:
        return None
    found = [
        (page["filingDate"][i], page["accessionNumber"][i], page["primaryDocument"][i])
        for page in _pages(company, since, before)
        for i, form in enumerate(page.get("form", []))
        if form in ("10-K", "10-Q") and since.isoformat() <= page["filingDate"][i] < before.isoformat()
    ]
    if not found:
        return None
    filed, accession, primary_doc = max(found)
    return accession, primary_doc, date.fromisoformat(filed)


def add_details(filing: Filing) -> bool:
    """Complète acceptance, items et document principal ; False si introuvable.

    Le SIC n'est pas pris ici : celui de l'API est l'actuel (voir `assigned_sic`).
    """
    company = _submissions(f"CIK{filing.cik:010d}.json")
    if company is None:
        return False
    for page in _pages(company, filing.filing_date, filing.filing_date):
        if filing.accession not in page.get("accessionNumber", []):
            continue
        i = page["accessionNumber"].index(filing.accession)
        filing.form = page["form"][i]
        filing.acceptance = parse_acceptance(page["acceptanceDateTime"][i])
        filing.items = [item for item in page["items"][i].split(",") if item]
        filing.primary_doc = page["primaryDocument"][i]
        return True
    return False
