"""Fenêtre de la phase 1 (SPEC §6) et garde-fou de l'échantillon réservé (audit C22)."""

from datetime import date

from news_alpha import ROOT
# Dates définies avec les règles de décision, pour que l'empreinte du module d'analyse les couvre.
from news_alpha.analysis import ANALYSIS_REPORT, RESERVED_END, RESERVED_NOT_BEFORE, RESERVED_START


def date_guard(start: date, end: date, reserved: bool, today: date,
               analysis_closed: bool) -> str | None:
    """Motif de refus de la plage demandée, ou None si elle peut être traitée.

    L'échantillon réservé n'est ni filtré ni noté avant la clôture de l'analyse et
    avant le 29/10/2026 (SPEC §6), puis il est traité seul, avec --reserved.
    """
    if start > end:
        return "--start est postérieur à --end"
    if not reserved:
        if end >= RESERVED_START:
            return (f"la plage touche l'échantillon réservé (dépôts à partir du "
                    f"{RESERVED_START}) : il ne se traite qu'avec --reserved")
        return None
    if start < RESERVED_START or end > RESERVED_END:
        return f"--reserved traite uniquement la plage {RESERVED_START} → {RESERVED_END}"
    if today < RESERVED_NOT_BEFORE:
        return f"échantillon réservé : pas avant le {RESERVED_NOT_BEFORE}"
    if not analysis_closed:
        return (f"échantillon réservé : l'analyse de la période principale n'est pas close "
                f"({ANALYSIS_REPORT.relative_to(ROOT)} absent)")
    return None
