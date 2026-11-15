"""Mesures du SPEC §8 et décision du §9, figées avant la campagne (décision M7 du 25/10/2026).

uv run python -m news_alpha.analysis             # période d'analyse -> reports/phase1_analysis.md
uv run python -m news_alpha.analysis --reserved  # échantillon réservé -> reports/phase1_reserved.md

Règles (SPEC §8-§9, DECISIONS.md D37) :
- population : dépôts tirés et notés avec une sortie valide (prompt et modèle en vigueur),
  dont les rendements anormaux contre IWM à 1, 5 et 20 séances sont connus ; elle est
  formée avant les quintiles et vaut pour les trois horizons ;
- événement = société (CIK) × séance d'entrée, noté par le composite le plus élevé en
  valeur absolue, signe conservé ; à égalité, le dépôt accepté le premier (M1) ;
- mois = mois d'acceptation (heure de New York) du dépôt retenu ;
- chaque mois, rendements anormaux ramenés à leurs 1er et 99e centiles (M3) ;
- quintiles mensuels de composite ; égalités départagées par une clé aléatoire tirée,
  graine 20250401, dans l'ordre des numéros d'accession triés ; Q1 et Q5 reçoivent
  n // 5 événements, le reste va à Q2-Q4 (M5) ;
- critère : quintile haut à 20 séances, moyenne des valeurs mensuelles nettes de 1,2 %
  > 0, t de Newey-West à 1 retard > 2 (M4), au moins 7 mois positifs sur 10, au moins
  300 événements (C04, C05) ;
- réservé : moyenne des 3 valeurs mensuelles nettes > 0 et au moins la moitié de celle
  de la période d'analyse, sans t-stat (M6).
"""

import argparse
import hashlib
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from news_alpha import ROOT, db
from news_alpha.ingest import ET
from news_alpha.score import ANALYSIS_SHA256, MODEL, PROMPT_SHA256, PROMPT_VERSION

# Fenêtre de la phase 1 : dépôts acceptés à partir du 01/09/2025, coupure des données
# d'entraînement du modèle (juillet 2025) + 1 mois (décision C01 du 25/10/2026).
ANALYSIS_START, ANALYSIS_END = date(2025, 9, 1), date(2026, 6, 30)
RESERVED_START, RESERVED_END = date(2026, 7, 1), date(2026, 9, 30)
# Lendemain de la 20e séance qui suit le dernier dépôt de chaque période.
ANALYSIS_NOT_BEFORE, RESERVED_NOT_BEFORE = date(2026, 7, 30), date(2026, 10, 29)
ANALYSIS_REPORT = ROOT / "reports" / "phase1_analysis.md"  # n'existe qu'une fois l'analyse close
RESERVED_REPORT = ROOT / "reports" / "phase1_reserved.md"

SEED = 20250401
HORIZONS = (1, 5, 20)
DECISIVE = 20
COST = 0.012  # aller-retour (SPEC §8)
INFORMATIVE_COSTS = (0.02, 0.03)  # décision L4
WINSOR_LOW, WINSOR_HIGH = 0.01, 0.99
MIN_EVENTS = 300
MIN_T = 2.0
MIN_POSITIVE_MONTHS = 7  # sur les 10 mois de la période d'analyse


@dataclass(frozen=True)
class Event:
    accession: str  # dépôt retenu pour l'événement
    cik: int
    month: str  # 'AAAA-MM'
    composite: float
    ar: dict  # horizon -> rendement anormal contre IWM
    ar_sector: dict  # horizon -> contre l'ETF sectoriel, None sans ETF
    early_exit: bool  # sortie de cote avant la 20e séance (M2)


@dataclass
class Month:
    month: str
    events: list[Event]
    quintile: dict[str, int]  # accession -> 1 (bas) à 5 (haut)
    capped: dict[int, dict[str, float]]  # horizon -> accession -> rendement plafonné
    capped_sector: dict[int, dict[str, float]] = field(default_factory=dict)

    def mean(self, quintile: int, horizon: int, sector: bool = False,
             raw: bool = False) -> float | None:
        """Moyenne d'un quintile : plafonnée (décision) ou brute (pour information)."""
        if raw:
            values = {e.accession: e.ar[horizon] for e in self.events}
        else:
            values = self.capped_sector[horizon] if sector else self.capped[horizon]
        chosen = [values[e.accession] for e in self.events
                  if self.quintile[e.accession] == quintile and e.accession in values]
        return statistics.mean(chosen) if chosen else None


def build_events(filings: list[dict]) -> list[Event]:
    """Un événement par (CIK, séance d'entrée) : le composite le plus fort en valeur absolue (M1)."""
    groups = defaultdict(list)
    for filing in filings:
        groups[(filing["cik"], filing["entry_date"])].append(filing)
    events = []
    for group in groups.values():
        chosen = min(group, key=lambda f: (-abs(f["composite"]),
                                           datetime.fromisoformat(f["acceptance"]), f["accession"]))
        events.append(Event(chosen["accession"], chosen["cik"], chosen["acceptance"][:7],
                            chosen["composite"], chosen["ar"], chosen["ar_sector"],
                            chosen["early_exit"]))
    return sorted(events, key=lambda event: event.accession)


def winsorize(values: list[float]) -> list[float]:
    """Valeurs ramenées aux 1er et 99e centiles (interpolation linéaire, méthode 'inclusive')."""
    if len(values) < 2:
        return list(values)
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    low, high = cuts[round(WINSOR_LOW * 100) - 1], cuts[round(WINSOR_HIGH * 100) - 1]
    return [min(max(value, low), high) for value in values]


def quintile_sizes(n: int) -> list[int]:
    """Tailles de Q1 à Q5 : n // 5 aux extrêmes ; le reste à Q3, puis à Q2 et Q4 (M5)."""
    edge = n // 5
    middle = n - 2 * edge
    q2 = q3 = q4 = middle // 3
    if middle % 3 == 1:
        q3 += 1
    elif middle % 3 == 2:
        q2, q4 = q2 + 1, q4 + 1
    return [edge, q2, q3, q4, edge]


def assign_quintiles(events: list[Event]) -> dict[str, int]:
    """Quintile de chaque événement d'un même mois, égalités départagées à graine fixe (M5)."""
    rng = random.Random(SEED)
    tie_key = {accession: rng.random() for accession in sorted(e.accession for e in events)}
    ranked = sorted(events, key=lambda e: (e.composite, tie_key[e.accession]))
    quintiles, position = {}, 0
    for quintile, size in enumerate(quintile_sizes(len(ranked)), start=1):
        for event in ranked[position:position + size]:
            quintiles[event.accession] = quintile
        position += size
    return quintiles


def _capped(events: list[Event], returns: str) -> dict[int, dict[str, float]]:
    capped = {}
    for horizon in HORIZONS:
        known = [e for e in events if getattr(e, returns)[horizon] is not None]
        values = winsorize([getattr(e, returns)[horizon] for e in known])
        capped[horizon] = {e.accession: value for e, value in zip(known, values)}
    return capped


def by_month(events: list[Event]) -> list[Month]:
    grouped = defaultdict(list)
    for event in events:
        grouped[event.month].append(event)
    return [Month(month, group, assign_quintiles(group), _capped(group, "ar"),
                  _capped(group, "ar_sector"))
            for month, group in sorted(grouped.items())]


def calendar_months(start: date, end: date) -> list[str]:
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def newey_west_t(series: list[float]) -> float | None:
    """t de la moyenne, variance de Newey-West à 1 retard (M4).

    S = γ0 + 2 × ½ × γ1 (noyau de Bartlett), γk = Σ e_t e_{t-k} / T, e = écarts à la
    moyenne, sans correction de petit échantillon ; t = moyenne / √(S / T).
    """
    n = len(series)
    if n < 2:
        return None
    mean = sum(series) / n
    e = [x - mean for x in series]
    s = sum(x * x for x in e) / n + sum(e[t] * e[t - 1] for t in range(1, n)) / n
    return mean / math.sqrt(s / n) if s > 0 else None


def simple_t(series: list[float]) -> float | None:
    """t naïve du SPEC v0.2 : moyenne / (écart-type / √mois), pour information."""
    if len(series) < 2 or statistics.stdev(series) == 0:
        return None
    return statistics.mean(series) / (statistics.stdev(series) / math.sqrt(len(series)))


@dataclass
class Series:
    """Une valeur par mois du calendrier (None si le mois n'en a pas), et ses statistiques."""
    monthly: dict[str, float | None]

    @property
    def values(self) -> list[float]:
        return [v for v in self.monthly.values() if v is not None]

    @property
    def mean(self) -> float | None:
        return statistics.mean(self.values) if self.values else None

    @property
    def t_nw(self) -> float | None:
        return newey_west_t(self.values)

    @property
    def t_simple(self) -> float | None:
        return simple_t(self.values)

    @property
    def positive(self) -> int:
        return sum(v > 0 for v in self.values)


def monthly_series(months: list[Month], calendar: list[str], value) -> Series:
    found = {m.month: value(m) for m in months}
    return Series({month: found.get(month) for month in calendar})


def top_net(months: list[Month], calendar: list[str], horizon: int = DECISIVE,
            cost: float = COST, sector: bool = False) -> Series:
    """Quintile haut, net des frais, mois par mois."""
    def value(m: Month) -> float | None:
        top = m.mean(5, horizon, sector)
        return top - cost if top is not None else None
    return monthly_series(months, calendar, value)


def spread(months: list[Month], calendar: list[str], horizon: int) -> Series:
    """Écart haut − bas brut, mois par mois (§8, pour information)."""
    def value(m: Month) -> float | None:
        top, bottom = m.mean(5, horizon), m.mean(1, horizon)
        return top - bottom if None not in (top, bottom) else None
    return monthly_series(months, calendar, value)


def information_coefficient(months: list[Month], calendar: list[str]) -> Series:
    """Corrélation de rang (Spearman) entre composite et rendement anormal à 5 séances."""
    def value(m: Month) -> float | None:
        if len(m.events) < 3:
            return None
        try:
            return statistics.correlation([e.composite for e in m.events],
                                          [e.ar[5] for e in m.events], method="ranked")
        except statistics.StatisticsError:  # composites tous égaux
            return None
    return monthly_series(months, calendar, value)


def checks(events: list[Event], criterion: Series) -> dict[str, bool]:
    """Critères du §9 sur la période d'analyse : tous requis."""
    return {
        f"au moins {MIN_EVENTS} événements": len(events) >= MIN_EVENTS,
        "moyenne mensuelle nette > 0": criterion.mean is not None and criterion.mean > 0,
        f"t de Newey-West > {MIN_T:g}": criterion.t_nw is not None and criterion.t_nw > MIN_T,
        f"au moins {MIN_POSITIVE_MONTHS} mois positifs sur 10": criterion.positive >= MIN_POSITIVE_MONTHS,
    }


def reserved_confirms(analysis_mean: float | None, reserved: Series) -> bool:
    """Même signe et au moins la moitié de l'ampleur, sur la moyenne des 3 mois (M6)."""
    return (None not in (analysis_mean, reserved.mean) and reserved.mean > 0
            and reserved.mean >= analysis_mean / 2)


def load(conn: sqlite3.Connection, start: date, end: date) -> tuple[list[dict], Counter]:
    """Dépôts de la population et comptes de suivi, acceptations de [start, end] (heure de New York)."""
    counts = Counter(dict.fromkeys(  # ordre d'affichage fixe
        ("éligibles", "éligibles non traités", "traités sans note", "rejets de schéma",
         "rendements manquants", "dépôts de la population", "coût LLM ($)"), 0))
    filings = conn.execute(
        "SELECT accession, cik, acceptance_et, selected FROM filings "
        "WHERE reject_reason IS NULL AND substr(acceptance_et, 1, 10) BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    population = []
    for filing in filings:
        counts["éligibles"] += 1
        if not filing["selected"]:
            counts["éligibles non traités"] += 1
            continue
        scored = conn.execute(
            "SELECT valid, composite, cost_usd FROM scores "
            "WHERE accession = ? AND prompt_version = ? AND model = ?",
            (filing["accession"], PROMPT_VERSION, MODEL),
        ).fetchone()
        if scored is None:
            counts["traités sans note"] += 1
            continue
        counts["coût LLM ($)"] += scored["cost_usd"] or 0
        if not scored["valid"]:
            counts["rejets de schéma"] += 1
            continue
        rows = {r["horizon"]: r for r in conn.execute(
            "SELECT horizon, entry_date, abnormal_iwm, abnormal_sector, early_exit "
            "FROM returns WHERE accession = ?", (filing["accession"],))}
        if any(rows.get(h) is None or rows[h]["abnormal_iwm"] is None for h in HORIZONS):
            counts["rendements manquants"] += 1
            continue
        population.append({
            "accession": filing["accession"], "cik": filing["cik"],
            "acceptance": filing["acceptance_et"], "entry_date": rows[DECISIVE]["entry_date"],
            "composite": scored["composite"],
            "ar": {h: rows[h]["abnormal_iwm"] for h in HORIZONS},
            "ar_sector": {h: rows[h]["abnormal_sector"] for h in HORIZONS},
            "early_exit": bool(rows[DECISIVE]["early_exit"]),
        })
    counts["dépôts de la population"] = len(population)
    return population, counts


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2%}"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _series_line(label: str, series: Series) -> str:
    return (f"| {label} | {_pct(series.mean)} | {_num(series.t_nw)} | {_num(series.t_simple)} "
            f"| {series.positive}/{len(series.monthly)} |")


def render(title: str, period: tuple[date, date], events: list[Event], counts: Counter,
           months: list[Month], calendar: list[str], verdict: list[str]) -> str:
    criterion = top_net(months, calendar)
    lines = [
        f"# {title}", "",
        f"Dépôts acceptés du {period[0]} au {period[1]} · prompt {PROMPT_VERSION} "
        f"(`{PROMPT_SHA256[:12]}…`) · modèle `{MODEL}` · code d'analyse `{ANALYSIS_SHA256[:12]}…` · "
        f"généré le {datetime.now(ET):%Y-%m-%d %H:%M} ET", "",
        *verdict, "",
        "## Population", "",
        "| Étape | Nombre |", "|---|---|",
        *[f"| {label} | {value:.2f} |" if isinstance(value, float) else f"| {label} | {value} |"
          for label, value in counts.items()],
        f"| événements (société × séance d'entrée) | {len(events)} |",
        f"| dont sorties de cote avant la 20e séance | {sum(e.early_exit for e in events)} |",
        "",
        "## Quintile haut, mois par mois (20 séances)", "",
        "| Mois | Événements | Quintile haut | Rendement anormal plafonné | Net de 1,2 % | Écart haut − bas |",
        "|---|---|---|---|---|---|",
    ]
    by_name = {m.month: m for m in months}
    for month in calendar:
        m = by_name.get(month)
        if m is None:
            lines.append(f"| {month} | 0 | 0 | — | — | — |")
            continue
        top = sum(q == 5 for q in m.quintile.values())
        gap = spread([m], [month], DECISIVE).monthly[month]
        lines.append(f"| {month} | {len(m.events)} | {top} | {_pct(m.mean(5, DECISIVE))} "
                     f"| {_pct(criterion.monthly[month])} | {_pct(gap)} |")
    lines += ["", "## Par quintile (moyenne des mois)", "",
              "| Quintile | Horizon | Plafonné | Brut | Médiane | Événements | Sorties anticipées |",
              "|---|---|---|---|---|---|---|"]
    for quintile in range(1, 6):
        members = [e for m in months for e in m.events if m.quintile[e.accession] == quintile]
        for horizon in HORIZONS:
            capped = monthly_series(months, calendar, lambda m: m.mean(quintile, horizon)).mean
            raw = monthly_series(months, calendar, lambda m: m.mean(quintile, horizon, raw=True)).mean
            median = statistics.median([e.ar[horizon] for e in members]) if members else None
            lines.append(f"| Q{quintile} | {horizon} | {_pct(capped)} | {_pct(raw)} | {_pct(median)} "
                         f"| {len(members)} | {sum(e.early_exit for e in members)} |")
    top_members = [e for m in months for e in m.events if m.quintile[e.accession] == 5]
    hits = sum(e.ar[DECISIVE] > COST for e in top_members)
    without_exits = [e for e in events if not e.early_exit]
    ic = information_coefficient(months, calendar)
    lines += [
        "", "## Mesures informatives (hors décision)", "",
        "| Mesure | Moyenne mensuelle | t Newey-West | t simple | Mois positifs |",
        "|---|---|---|---|---|",
        _series_line("Critère : quintile haut 20 séances, net de 1,2 %", criterion),
        *[_series_line(f"Quintile haut 20 séances, net de {cost:.0%}", top_net(months, calendar, cost=cost))
          for cost in INFORMATIVE_COSTS],
        _series_line("Quintile haut 5 séances, net de 1,2 %", top_net(months, calendar, horizon=5)),
        *[_series_line(f"Écart haut − bas {h} séances, brut", spread(months, calendar, h))
          for h in (5, DECISIVE)],
        _series_line("Écart haut − bas 20 séances, net de 1,2 %", Series(
            {k: (v - COST if v is not None else None)
             for k, v in spread(months, calendar, DECISIVE).monthly.items()})),
        _series_line("Contrôle sectoriel : quintile haut 20 séances, net", top_net(
            months, calendar, sector=True)),
        _series_line("Sans les sorties de cote anticipées (critère)", top_net(
            by_month(without_exits), calendar)),
        _series_line("Information coefficient (Spearman, 5 séances)", ic),
        "",
        f"- Taux de réussite : {hits}/{len(top_members)} événements du quintile haut "
        f"dépassent +1,2 % à 20 séances (rendements bruts).",
        f"- Coût LLM : {counts['coût LLM ($)']:.2f} $ au total, "
        f"{counts['coût LLM ($)'] / len(events) if events else 0:.4f} $ par événement.",
        "",
    ]
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shown(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse de la phase 1 (SPEC §8-§9)")
    parser.add_argument("--reserved", action="store_true", help="échantillon réservé")
    args = parser.parse_args(argv)
    if _sha256(Path(__file__)) != ANALYSIS_SHA256:
        print("Le code d'analyse a changé depuis son gel (empreinte inscrite au SPEC) : arrêt.",
              file=sys.stderr)
        return 1
    today = datetime.now(ET).date()
    target = RESERVED_REPORT if args.reserved else ANALYSIS_REPORT
    not_before = RESERVED_NOT_BEFORE if args.reserved else ANALYSIS_NOT_BEFORE
    refusal = (
        f"{_shown(target)} existe déjà : l'analyse ne se fait qu'une fois" if target.exists()
        else f"pas avant le {not_before}" if today < not_before
        else "l'analyse de la période principale n'est pas close" if args.reserved and not ANALYSIS_REPORT.exists()
        else None
    )
    if refusal:
        print(f"Analyse refusée : {refusal}.", file=sys.stderr)
        return 1

    conn = db.connect()
    period = (RESERVED_START, RESERVED_END) if args.reserved else (ANALYSIS_START, ANALYSIS_END)
    rows, counts = load(conn, *period)
    pending = counts["éligibles non traités"] + counts["traités sans note"]
    if pending:
        print(f"Analyse refusée : {pending} dépôts éligibles ne sont pas notés ; "
              "terminer la campagne d'abord.", file=sys.stderr)
        return 1

    analysis_calendar = calendar_months(ANALYSIS_START, ANALYSIS_END)
    analysis_events = build_events(load(conn, ANALYSIS_START, ANALYSIS_END)[0])
    criterion = top_net(by_month(analysis_events), analysis_calendar)
    passed = checks(analysis_events, criterion)
    verdict = ["## Décision", "", "| Critère (période d'analyse) | Rempli |", "|---|---|",
               *[f"| {label} | {'oui' if ok else 'non'} |" for label, ok in passed.items()], ""]
    if not all(passed.values()):
        verdict.append("**Décision : NO-GO, arrêt.**")
    elif not args.reserved:
        verdict.append("**Critères remplis sur la période d'analyse.** "
                       "La décision finale attend l'échantillon réservé.")

    events = build_events(rows)
    months = by_month(events)
    calendar = calendar_months(*period)
    if args.reserved:
        reserved = top_net(months, calendar)
        confirmed = reserved_confirms(criterion.mean, reserved)
        verdict += [f"Réservé : moyenne nette {_pct(reserved.mean)}, contre {_pct(criterion.mean)} "
                    f"sur la période d'analyse (au moins la moitié requise) : "
                    f"{'confirmé' if confirmed else 'non confirmé'}.", "",
                    f"**Décision finale : {'GO vers la phase 2' if all(passed.values()) and confirmed else 'NO-GO, arrêt'}.**"]
    title = "Phase 1 : échantillon réservé" if args.reserved else "Phase 1 : période d'analyse"
    target.write_text(render(title, period, events, counts, months, calendar, verdict))
    print(f"Rapport écrit : {_shown(target)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
