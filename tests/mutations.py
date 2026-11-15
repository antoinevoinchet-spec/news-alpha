"""Tests par mutation (audit C09), adaptés au code de la phase 1 : uv run python -I tests/mutations.py

Chaque mutation viole une règle critique dans une COPIE du paquet (data/tmp/mut/<nom>),
puis la suite tests/ du projet tourne sur cette copie (PYTHONPATH en tête). Une mutation
qui « survit » est une règle qu'aucun test ne protège. Les fichiers du projet ne sont
jamais modifiés. Dans la copie, l'empreinte du module d'analyse est recalculée, pour que
ce soient les tests de comportement, et non le contrôle d'empreinte, qui jugent.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUT = ROOT / "data" / "tmp" / "mut"

MUTATIONS = {
    # Mutations de l'audit, adaptées au code actuel.
    "entree_a_09h30_pile": ("returns.py", "> ready_at", ">= ready_at",
                            "entrée à l'ouverture strictement postérieure"),
    "entree_cloture_meme_jour": ("returns.py", "MARKET_OPEN, tzinfo=ET) > ready_at",
                                 "time(16, 0), tzinfo=ET) > ready_at", "jamais la clôture du jour du dépôt"),
    "mouvement_inclut_jour_entree": ("prices.py", "before_entry = [b for b in bars if b.date < entry]",
                                     "before_entry = [b for b in bars if b.date <= entry]",
                                     "aucun prix postérieur à l'entrée transmis au modèle"),
    "filtre_marche_inclut_jour_depot": ("run.py", "[bar for bar in raw if bar.date < filed_on]",
                                        "[bar for bar in raw if bar.date <= filed_on]",
                                        "filtres sur la veille du dépôt (D12)"),
    "filtre_marche_tout_l_historique": ("run.py", "[bar for bar in raw if bar.date < filed_on]",
                                        "raw", "filtres sur les séances antérieures au dépôt"),
    "actions_meme_jour_que_8k": ("filters.py", 'fact["filed"] < filed_before.isoformat()',
                                 'fact["filed"] <= filed_before.isoformat()', "actions publiées avant le 8-K"),
    "actions_sans_limite_180j": ("filters.py", "if filed_before - as_of > MAX_SHARES_AGE:",
                                 "if False:", "valeur simple de moins de 180 jours"),
    "plage_acceptation_ignoree": ("run.py", "if not start <= filing.acceptance.date() <= end:",
                                  "if False:", "dépôt gardé si accepté dans la plage (D7)"),
    "renotation_systematique": ("run.py", "    if scored:\n        return scored[\"latency_s\"], False",
                                "    if False:\n        return scored[\"latency_s\"], False",
                                "jamais renoté (même prompt, même modèle)"),
    "prompt_recoit_le_ticker": ("score.py", "    return (TEMPLATE.replace(",
                                '    return "TICKER=XYZ FUTURE_RETURN=+12%\\n" + (TEMPLATE.replace(',
                                "le prompt ne reçoit que le texte et le mouvement"),
    "flux_iex": ("prices.py", '"feed": "sip",', '"feed": "iex",', "flux SIP uniquement"),
    "split_date_ex_incluse": ("prices.py", 'if since < date.fromisoformat(split["ex_date"]) <= until',
                              'if since <= date.fromisoformat(split["ex_date"]) <= until',
                              "splits postérieurs à la date du nombre d'actions"),
    "rendement_ouverture_vers_cloture_veille": ("returns.py", "return bars[exit_].close / bars[entry].open - 1",
                                                "return bars[exit_].close / bars[entry].close - 1",
                                                "achat à l'ouverture de la séance d'entrée"),
    "validation_sans_extra_forbid": ("score.py", 'extra="forbid", strict=True', 'extra="ignore", strict=True',
                                     "clé en trop rejetée"),
    "acceptation_decalage_fixe_moins_4h": ("ingest.py", "return datetime.fromisoformat(value).astimezone(ET)",
        "from datetime import timedelta, timezone\n    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=-4)))",
        "conversion UTC -> New York, heure d'hiver comprise"),
    "ouverture_utc_moins_4h_fixe": ("returns.py", 'ET = ZoneInfo("America/New_York")',
        "from datetime import timedelta, timezone\nET = timezone(timedelta(hours=-4))",
        "ouverture 09:30 New York toute l'année"),
    # Règles ajoutées depuis l'audit.
    "spac_garde": ("filters.py", "if sic == SPAC_SIC:", "if False:", "SPAC exclus (L1)"),
    "sic_actuel_garde": ("run.py", "filing.sic = ingest.assigned_sic(header, filing.cik)",
                         "filing.sic = filing.sic or ingest.assigned_sic(header, filing.cik)",
                         "SIC à la date du dépôt (C26)"),
    "index_sans_queue": ("run.py", "while day <= end + INDEX_TAIL:", "while day <= end:",
                         "dépôts du soir du dernier jour lus (C13)"),
    "balise_vide_non_retiree": ("filters.py", "IX_NON_FRACTION.findall(IX_SELF_CLOSING.sub(\"\", document))",
                                "IX_NON_FRACTION.findall(document)", "balises iXBRL vides (C11)"),
    "split_jusqu_au_jour_du_depot": ("run.py", "price_day = bars_before[-1].date if bars_before else filed_on",
                                     "price_day = filed_on", "splits jusqu'à la séance du prix (C14)"),
    "score_non_valide_sur_disque": ("run.py", "    conn.commit()  # une réponse payée",
                                    "    pass  # une réponse payée", "score enregistré dès réception (C08)"),
    "cle_dupliquee_acceptee": ("score.py", '        raise DuplicateKey(", ".join(duplicated))',
                               "        pass", "clé dupliquée rejetée (C17)"),
    "emballage_sans_json": ("score.py", 'CODE_FENCE = re.compile(r"```json\\n(.*)\\n```", re.S)',
                            'CODE_FENCE = re.compile(r"```(?:json)?\\n(.*)\\n```", re.S)',
                            "emballage ```json seul (C17)"),
    "rationale_blanc": ("score.py", 'rationale: str = Field(pattern=r"\\S")', "rationale: str",
                        "rationale non blanc (C17)"),
    "balise_filing_non_neutralisee": ("score.py", 'FILING_TAG.sub("&lt;", filing_text)', "filing_text",
                                      "le dépôt ne ferme pas sa balise (C19)"),
    "sans_temperature": ("score.py", '        extra_body={"temperature": TEMPERATURE},', "",
                         "température 0 (C20)"),
    "version_api_recente": ("score.py", 'API_VERSION = "2023-06-01"', 'API_VERSION = "2026-10-21"',
                            "version d'API qui accepte temperature (C20)"),
    "empreinte_prompt_ignoree": ("run.py", "    if score.TEMPLATE_SHA256 != score.PROMPT_SHA256:",
                                 "    if False:", "prompt gelé (C18)"),
    "moment_inverse": ("prices.py",
                       'return "after_close" if acceptance.time() >= MARKET_CLOSE else "during"',
                       'return "during" if acceptance.time() >= MARKET_CLOSE else "after_close"',
                       "moment du mouvement (D35)"),
    "reserve_sans_option": ("window.py", "        if end >= RESERVED_START:", "        if False:",
                            "réservé seulement avec --reserved (C22)"),
    "reserve_avant_29_octobre": ("window.py", "    if today < RESERVED_NOT_BEFORE:", "    if False:",
                                 "réservé pas avant le 29/10/2026 (C22)"),
    "reserve_melange": ("window.py", "    if start < RESERVED_START or end > RESERVED_END:", "    if False:",
                        "réservé traité seul (C22)"),
    "sortie_non_marquee": ("returns.py", '"early_exit": int(exit_ is not None and exit_ < planned),',
                           '"early_exit": 0,', "sortie de cote marquée (M2)"),
    "iwm_horizon_complet": ("returns.py", "market_ret = period_return(market, entry, exit_) if exit_ else None",
                            "market_ret = period_return(market, entry, planned) if exit_ else None",
                            "IWM sur la même période de détention (M2)"),
    "evenement_composite_signe": ("analysis.py", 'key=lambda f: (-abs(f["composite"]),',
                                  'key=lambda f: (-f["composite"],', "composite le plus fort en valeur absolue (M1)"),
    "sans_winsorisation": ("analysis.py", "    return [min(max(value, low), high) for value in values]",
                           "    return list(values)", "winsorisation 1/99 (M3)"),
    "sans_frais": ("analysis.py", "        return top - cost if top is not None else None",
                   "        return top", "net de 1,2 % (C04)"),
    "t_sans_newey_west": ("analysis.py", " + sum(e[t] * e[t - 1] for t in range(1, n)) / n", "",
                          "Newey-West à 1 retard (M4)"),
    "six_mois_suffisent": ("analysis.py", "MIN_POSITIVE_MONTHS = 7", "MIN_POSITIVE_MONTHS = 6",
                           "7 mois positifs sur 10"),
    "moins_de_300": ("analysis.py", "MIN_EVENTS = 300", "MIN_EVENTS = 200", "300 événements"),
    "reste_au_quintile_2": ("analysis.py", "    if middle % 3 == 1:\n        q3 += 1",
                            "    if middle % 3 == 1:\n        q2 += 1", "reste aux quintiles du milieu (M5)"),
    "egalites_sans_graine": ("analysis.py", "    rng = random.Random(SEED)", "    rng = random.Random()",
                             "égalités à graine fixe (M5)"),
    "reserve_sans_moitie": ("analysis.py", "and reserved.mean >= analysis_mean / 2", "",
                            "au moins la moitié de l'ampleur (M6)"),
    "note_v1_melangee": ("analysis.py",
                         '"WHERE accession = ? AND prompt_version = ? AND model = ?",\n'
                         '            (filing["accession"], PROMPT_VERSION, MODEL),',
                         '"WHERE accession = ? AND model = ? ORDER BY prompt_version",\n'
                         '            (filing["accession"], MODEL),', "notes du prompt en vigueur seulement"),
    "analyse_reecrite": ("analysis.py", "if target.exists()", "if False", "analyse faite une seule fois"),
    "analyse_incomplete": ("analysis.py", "    if pending:", "    if False:", "campagne complète avant l'analyse"),
    "binaire_encode_lu": ("extract.py", ' and not UUENCODED.match(content):', ":",
                          "binaire encodé jamais transmis au modèle (D20)"),
}


def run(name: str, filename: str, old: str, new: str) -> tuple[str, str, bool]:
    target = MUT / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(ROOT / "src", target / "src")
    shutil.copytree(ROOT / "prompts", target / "prompts")
    package = target / "src" / "news_alpha"
    source = package / filename
    text = source.read_text()
    if text.count(old) != 1:
        shutil.rmtree(target)
        return "TEXTE INTROUVABLE", f"{text.count(old)} occurrence(s)", False
    source.write_text(text.replace(old, new))
    digest = hashlib.sha256((package / "analysis.py").read_bytes()).hexdigest()
    score = package / "score.py"
    score.write_text(re.sub(r'ANALYSIS_SHA256 = "[0-9a-f]+"', f'ANALYSIS_SHA256 = "{digest}"',
                            score.read_text()))
    env = {**os.environ, "PYTHONPATH": str(target / "src")}
    proc = subprocess.run(
        ["uv", "run", "--frozen", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
    probe = subprocess.run(["uv", "run", "--frozen", "python", "-c",
                            "import news_alpha; print(news_alpha.__file__)"],
                           cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip()
    shutil.rmtree(target)
    return ("TUÉE" if proc.returncode != 0 else "SURVIT"), last, probe.startswith(str(target))


selected = sys.argv[1:] or list(MUTATIONS)
for name in selected:
    filename, old, new, rule = MUTATIONS[name]
    verdict, last, used = run(name, filename, old, new)
    print(f"{verdict:6} {name:36} copie={used!s:5} [{rule}] -> {last}", flush=True)
