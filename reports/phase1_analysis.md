# Phase 1 : période d'analyse

Dépôts acceptés du 2025-09-01 au 2026-06-30 · prompt v2 (`7611eddae367…`) · modèle `claude-haiku-4-5-20251001` · code d'analyse `01ffb5d33915…` · généré le 2026-10-28 02:25 ET

## Décision

| Critère (période d'analyse) | Rempli |
|---|---|
| au moins 300 événements | oui |
| moyenne mensuelle nette > 0 | non |
| t de Newey-West > 2 | non |
| au moins 7 mois positifs sur 10 | non |

**Décision : NO-GO, arrêt.**

## Population

| Étape | Nombre |
|---|---|
| éligibles | 16683 |
| éligibles non traités | 0 |
| traités sans note | 0 |
| rejets de schéma | 160 |
| rendements manquants | 91 |
| dépôts de la population | 16432 |
| coût LLM ($) | 115.96 |
| événements (société × séance d'entrée) | 15907 |
| dont sorties de cote avant la 20e séance | 137 |

## Quintile haut, mois par mois (20 séances)

| Mois | Événements | Quintile haut | Rendement anormal plafonné | Net de 1,2 % | Écart haut − bas |
|---|---|---|---|---|---|
| 2025-09 | 1131 | 226 | +2.95% | +1.75% | +3.04% |
| 2025-10 | 1678 | 335 | -0.72% | -1.92% | +3.00% |
| 2025-11 | 2008 | 401 | +1.45% | +0.25% | +1.55% |
| 2025-12 | 1171 | 234 | -0.32% | -1.52% | -0.19% |
| 2026-01 | 1424 | 284 | -1.75% | -2.95% | -1.44% |
| 2026-02 | 2032 | 406 | +2.25% | +1.05% | +1.78% |
| 2026-03 | 1438 | 287 | -2.23% | -3.43% | -1.27% |
| 2026-04 | 1538 | 307 | -1.61% | -2.81% | +0.39% |
| 2026-05 | 2234 | 446 | -1.19% | -2.39% | -0.59% |
| 2026-06 | 1253 | 250 | +0.55% | -0.65% | -1.54% |

## Par quintile (moyenne des mois)

| Quintile | Horizon | Plafonné | Brut | Médiane | Événements | Sorties anticipées |
|---|---|---|---|---|---|---|
| Q1 | 1 | -0.27% | -0.31% | -0.26% | 3176 | 34 |
| Q1 | 5 | -0.28% | +0.24% | -0.58% | 3176 | 34 |
| Q1 | 20 | -0.53% | +0.25% | -1.30% | 3176 | 34 |
| Q2 | 1 | -0.07% | -0.05% | -0.14% | 3184 | 49 |
| Q2 | 5 | +0.03% | +0.21% | -0.29% | 3184 | 49 |
| Q2 | 20 | +0.14% | +0.44% | -0.51% | 3184 | 49 |
| Q3 | 1 | -0.17% | -0.15% | -0.15% | 3187 | 32 |
| Q3 | 5 | -0.46% | -0.40% | -0.56% | 3187 | 32 |
| Q3 | 20 | -0.99% | -0.94% | -1.39% | 3187 | 32 |
| Q4 | 1 | -0.06% | -0.06% | -0.12% | 3184 | 16 |
| Q4 | 5 | -0.13% | -0.11% | -0.34% | 3184 | 16 |
| Q4 | 20 | -0.70% | -0.52% | -1.15% | 3184 | 16 |
| Q5 | 1 | -0.13% | -0.11% | -0.15% | 3176 | 6 |
| Q5 | 5 | -0.26% | -0.09% | -0.64% | 3176 | 6 |
| Q5 | 20 | -0.06% | +0.25% | -1.52% | 3176 | 6 |

## Mesures informatives (hors décision)

| Mesure | Moyenne mensuelle | t Newey-West | t simple | Mois positifs |
|---|---|---|---|---|
| Critère : quintile haut 20 séances, net de 1,2 % | -1.26% | -2.73 | -2.23 | 3/10 |
| Quintile haut 20 séances, net de 2% | -2.06% | -4.47 | -3.65 | 2/10 |
| Quintile haut 20 séances, net de 3% | -3.06% | -6.63 | -5.42 | 0/10 |
| Quintile haut 5 séances, net de 1,2 % | -1.46% | -5.88 | -4.34 | 1/10 |
| Écart haut − bas 5 séances, brut | +0.02% | 0.11 | 0.07 | 5/10 |
| Écart haut − bas 20 séances, brut | +0.47% | 0.79 | 0.84 | 5/10 |
| Écart haut − bas 20 séances, net de 1,2 % | -0.73% | -1.22 | -1.30 | 4/10 |
| Contrôle sectoriel : quintile haut 20 séances, net | -0.66% | -1.37 | -1.00 | 4/10 |
| Sans les sorties de cote anticipées (critère) | -1.21% | -2.64 | -2.13 | 2/10 |
| Information coefficient (Spearman, 5 séances) | -0.82% | -0.89 | -0.63 | 5/10 |

- Taux de réussite : 1324/3176 événements du quintile haut dépassent +1,2 % à 20 séances (rendements bruts).
- Coût LLM : 115.96 $ au total, 0.0073 $ par événement.
