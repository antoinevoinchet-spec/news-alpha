"""Rapport Markdown d'une exécution (reports/run_<start>_<end>.md par défaut)."""

import sqlite3
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from news_alpha.score import MODEL, PROMPT_VERSION


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2%}"


def write(
    conn: sqlite3.Connection,
    path: Path,
    accessions: list[str],
    exclusions: Counter,
    index_count: int,
    eligible_count: int,
    start: date,
    end: date,
) -> tuple[int, float]:
    """Écrit le rapport ; renvoie (nombre de rejets de schéma, coût LLM total en $)."""
    lines = [
        f"# Échantillon de {len(accessions)} dépôts 8-K",
        "",
        f"Plage {start} → {end} · tirage aléatoire reproductible parmi {eligible_count} "
        f"dépôts éligibles · prompt {PROMPT_VERSION} · modèle `{MODEL}` · "
        f"généré le {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
        "| CIK | Ticker | Acceptation (ET) | Items | Score composite "
        "| RA 1 séance | RA 5 séances | RA 20 séances | Rejets de schéma | Coût LLM ($) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rejects, cost, missing_returns, reject_reasons = 0, 0.0, 0, []
    for accession in accessions:
        filing = conn.execute("SELECT * FROM filings WHERE accession = ?", (accession,)).fetchone()
        scored = conn.execute(
            "SELECT * FROM scores WHERE accession = ? AND prompt_version = ? AND model = ?",
            (accession, PROMPT_VERSION, MODEL),
        ).fetchone()
        abnormal = dict(
            conn.execute(
                "SELECT horizon, abnormal_iwm FROM returns WHERE accession = ?", (accession,)
            ).fetchall()
        )
        if scored is None:
            composite, reject = "non noté", "—"
        elif scored["valid"]:
            composite, reject = f"{scored['composite']:+.3f}", "0"
        else:
            composite, reject = "rejet", "1"
            rejects += 1
            reject_reasons.append(f"{filing['ticker']} : {scored['reject_reason']}")
        cost += scored["cost_usd"] if scored else 0.0
        if any(abnormal.get(h) is None for h in (1, 5, 20)):
            missing_returns += 1
        accepted = datetime.fromisoformat(filing["acceptance_et"])
        lines.append(
            f"| {filing['cik']} | {filing['ticker']} | {accepted:%Y-%m-%d %H:%M} "
            f"| {filing['items']} | {composite} | {_pct(abnormal.get(1))} "
            f"| {_pct(abnormal.get(5))} | {_pct(abnormal.get(20))} | {reject} "
            f"| {scored['cost_usd'] if scored else 0:.4f} |"
        )
    lines.append(f"| **Total** | | | | | | | | **{rejects}** | **{cost:.4f}** |")

    lines += [
        "",
        "RA = rendement anormal : rendement du titre (achat à l'ouverture de la séance "
        "d'entrée, vente à la clôture de la n-ième séance) moins celui d'IWM sur la même période. "
        "Le rendement contre l'ETF sectoriel est dans la table `returns`.",
        "",
        "## Synthèse",
        "",
        f"- 8-K examinés (index EDGAR de la période, plus les dépôts du soir indexés après la fin) : "
        f"{index_count} ; éligibles après filtres : "
        f"{eligible_count} ; tirés et traités : {len(accessions)}.",
        "- Exclusions : "
        + (", ".join(f"{reason} {n}" for reason, n in exclusions.most_common()) or "aucune")
        + ".",
        f"- Rejets de schéma : {rejects} / {len(accessions)}"
        + (f" ({' ; '.join(reject_reasons)})" if reject_reasons else "")
        + ".",
        f"- Dépôts retenus avec au moins un rendement manquant : {missing_returns}.",
        f"- Coût LLM total : {cost:.4f} $"
        + (f" ({cost / len(accessions):.4f} $ par dépôt)." if accessions else "."),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return rejects, cost
