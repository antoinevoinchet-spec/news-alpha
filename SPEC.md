# News Alpha — spec

Version 0.3 — 25/10/2026. Règles de la phase 1 figées (pré-enregistrement, §6). Ce document est la référence du projet : toute déviation doit être notée dans `DECISIONS.md` avec sa raison et sa date. Les détails d'application y sont numérotés (D1, D2…) et font partie des règles figées. Historique des versions au §13.

## 1. Objectif

Tester si un LLM qui lit des informations d'entreprise primaires produit un signal qui prédit un rendement anormal positif, **net de frais**. On ne cherche pas à « trouver une stratégie gagnante » mais à valider ou rejeter une hypothèse précise, vite et honnêtement.

## 2. Hypothèse testée (H1)

Sur les small et mid caps américaines (capitalisation 300 M$ – 10 Md$), les dépôts 8-K contiennent de l'information que le marché sous-interprète dans les jours qui suivent. Un LLM qui note chaque 8-K sur « importance × direction × degré déjà intégré dans le prix » doit isoler un quintile, celui des dépôts les mieux notés, dont le rendement anormal moyen à 20 séances est positif après 1,2 % de frais aller-retour. C'est ce que la phase 2 achèterait.

L'écart entre les quintiles haut et bas, et l'horizon de 5 séances, mesurent le pouvoir prédictif des notes ; ils sont rapportés mais n'entrent pas dans la décision (§9).

**H0** : le rendement anormal du quintile haut, net de frais, est nul ou négatif. C'est le résultat par défaut tant que les données ne disent pas le contraire.

## 3. Phasage

| Phase | Contenu | Durée cible | Trading |
|---|---|---|---|
| 0 | Pipeline de bout en bout sur 20 dépôts | 1 week-end | Non |
| 1 | Event study sur la fenêtre propre (voir §6) | 2 à 4 semaines | Non |
| 2 | Paper trading Alpaca, seulement si go en phase 1 | 3 mois min. | Simulé |

Aucune phase ne démarre sans que les critères de la précédente soient remplis (§9). Phase 0 terminée le 25/10/2026 (`reports/phase0_sample.md`, prompt v1).

## 4. Sources

- **Événements** : EDGAR (SEC), dépôts 8-K (amendements 8-K/A exclus). Items retenus : 1.01 (accord important), 2.02 (résultats), 5.02 (changement de dirigeant), 7.01 et 8.01 (autres annonces). Accès gratuit ; la SEC exige un User-Agent identifiant (nom + e-mail) et limite le débit : 5 requêtes par seconde au plus.
- **Cotation** : ticker et bourse lus sur la page de garde du 8-K (titres de la section 12(b), balisés en XBRL), donc à la date du dépôt et jamais sur une liste actuelle (D2, D8, D9).
- **Code SIC** : lu dans l'en-tête EDGAR du dépôt, donc à la date du dépôt, et non dans les données actuelles de la SEC (D34).
- **Prix journaliers** : API de données Alpaca (clé gratuite), flux SIP uniquement. Toute plage est bornée à la veille, et un refus du SIP arrête l'exécution : il n'y a plus de repli sur IEX (D13). Les prix sont demandés « à la date du dépôt » (`asof`), pour retrouver les sociétés renommées ou délistées depuis (D13, D14). Pas de source de secours (D1). Stocker la source de chaque prix.
- **Capitalisation** : actions en circulation × prix de clôture brut de la veille du dépôt (D11, D12). Les actions viennent des données XBRL EDGAR : valeur simple de moins de 180 jours, sinon somme des classes en page de garde du dernier 10-K/10-Q de moins de 180 jours. Le nombre est ajusté des splits survenus jusqu'à la séance du prix retenu.
- **Benchmarks** : IWM (Russell 2000), référence des rendements anormaux. L'ETF sectoriel SPDR correspondant au code SIC sert de contrôle secondaire (D18, D19).
- **Hors périmètre phase 0-1** : réseaux sociaux, presse, transcripts d'earnings calls. Ils pourront servir de filtre plus tard, pas de déclencheur.

## 5. Pipeline

Deux passes (D10). La passe 1 applique les étapes 1-2 à tous les 8-K de la période, sans appel au modèle. La passe 2 tire au hasard (graine fixe 20250401) les dépôts éligibles à traiter, puis leur applique les étapes 3 à 7. En phase 1, tous les éligibles sont traités (`--limit 0`).

1. **Ingestion** : récupérer les 8-K d'une plage de dates, avec `acceptanceDateTime`, converti d'UTC en heure de New York (D5-D7). Un dépôt est gardé si sa date d'acceptation est dans la plage ; les index sont lus jusqu'à 4 jours après la fin, pour les dépôts du soir indexés le jour ouvré suivant (D7).
2. **Filtre** : items retenus ; action ordinaire cotée au NYSE, au Nasdaq ou au NYSE American ; ni SPAC (SIC 6770) ni déclarant sans code SIC, c'est-à-dire les BDC (D34) ; capitalisation dans la fourchette, prix > 2 $, volume moyen 20 j > 500 k$ (D8-D12).
3. **Extraction** : texte du document principal et des exhibits 99.x (communiqués), tronqué proprement à 40 000 caractères (option `--max-chars`), sur une fin de paragraphe, de ligne ou de phrase (D20, D21).
4. **Scoring LLM** : sortie JSON validée contre le schéma du §7. Le modèle est `claude-haiku-4-5-20251001`, avec le prompt `prompts/score_8k_v2.md`, figé par son empreinte (§6). Un appel par dépôt, sans outil ni recherche web, à température 0, avec la version d'API 2023-06-01, la dernière qui accepte ce paramètre (D24). Le prompt indique si le titre a coté depuis le dépôt (D35). Un dépôt n'est jamais renoté avec le même prompt et le même modèle (D22-D26).
5. **Point d'entrée** : ouverture (09:30 ET) de la première séance qui suit à la fois `acceptanceDateTime` et la fin du scoring. En backtest, la fin du scoring est simulée par acceptanceDateTime + durée mesurée de l'appel (D3). Dépôt après 16:00 ET → ouverture du lendemain. Jamais le prix de clôture du jour du dépôt.
6. **Rendements** : rendement brut et rendement anormal (titre − IWM ; titre − ETF sectoriel en contrôle) à 1, 5 et 20 séances depuis le point d'entrée. On achète à l'ouverture de la séance d'entrée et on vend à la clôture de la h-ième séance, entrée comprise, sur des prix ajustés des splits et dividendes (D17). Si le titre cesse de coter avant, la vente a lieu à sa dernière clôture, la ligne est marquée, et les benchmarks sont mesurés sur la même période (D37).
7. **Stockage** : SQLite, une table par étape (`filings`, `scores`, `prices`, `returns`), chaque ligne horodatée avec la version du prompt et du modèle ; `scores` porte aussi l'empreinte du prompt. Les réponses brutes d'EDGAR et d'Alpaca sont mises en cache dans `data/cache/` (D31).

## 6. Garde-fous contre les biais

- **Look-ahead** : le modèle de scoring ne doit pas avoir vu la période testée. Fenêtre propre = dépôts postérieurs à la fin de ses données d'entraînement + 1 mois de marge. Modèle de scoring : Claude Haiku 4.5 (`claude-haiku-4-5-20251001`). Sa coupure de connaissances fiable est février 2025, mais ses données d'entraînement vont jusqu'à juillet 2025 : c'est cette seconde date qui compte, d'où une fenêtre ouverte le 01/09/2025. Tout changement de modèle redéfinit la fenêtre.
- **Fenêtre de la phase 1** : dépôts acceptés du **01/09/2025 au 30/09/2026** inclus (13 mois). Période d'analyse : 01/09/2025 – 30/06/2026 (10 mois). Échantillon réservé : 01/07/2026 – 30/09/2026. Ces dates sont dans le code (`analysis.py`).
- **Fuite d'information** : le scoring ne reçoit que le texte du dépôt et le mouvement de cours entre la clôture veille et le dernier prix disponible avant le point d'entrée. Avec des prix journaliers, ce dernier prix est la dernière clôture antérieure à la séance d'entrée (D16). Jamais de prix postérieur. Le prompt précise si ce mouvement a eu lieu avant ou après le dépôt (D35).
- **Survivorship** : garder les sociétés délistées depuis ; identifier par CIK, pas par ticker actuel ; lire la cotation et le code SIC sur le dépôt lui-même (D2, D34) ; compter le rendement d'une société qui cesse de coter pendant l'horizon (D37).
- **Pré-enregistrement** : hypothèse, règles et critères de go/no-go figés par cette version 0.3 avant la phase 1. Maximum 2 itérations documentées du prompt ou des filtres ; au-delà, on conclut. Itérations utilisées : 0/2. Une itération ne peut être décidée que sur des critères qui ignorent les rendements de la période d'analyse : taux de rejets du schéma, bug avéré, donnée manquante. Un résultat décevant n'est jamais une raison d'itérer. Les changements v0.1 → v0.3, faits avant tout résultat de phase 1, ne sont pas des itérations.
- **Empreintes** : le code ne tourne que si ces fichiers sont identiques à leur version gelée (D35, D37). Toute modification se voit.
  - `prompts/score_8k_v2.md` : `7611eddae367c6d65aeeadc2d873e5290a370bb9ecb2b0caade9a5908a1d2691`
  - `src/news_alpha/analysis.py` : `01ffb5d33915023f159e8666d93a3bd91817041d3003f20110c36f946c878136`
- **Hors échantillon** : l'échantillon réservé n'est ni filtré ni noté avant la clôture de l'analyse de la période 01/09/2025 – 30/06/2026, matérialisée par `reports/phase1_analysis.md`. Il est ensuite traité une seule fois, avec les mêmes règles, et pas avant le 29/10/2026, date à laquelle les rendements à 20 séances de ses derniers dépôts sont disponibles. Le code refuse toute autre séquence (D36).
- **Continuité du modèle** : Claude Haiku 4.5 est classé « legacy » ; un retrait est annoncé au moins 60 jours à l'avance.
  1. La campagne est lancée dès que le module d'analyse est figé.
  2. Si l'échantillon réservé ne peut plus être noté avant le retrait du modèle, la phase 1 ne peut pas donner de go.
  3. Une phase 2 n'utilise ce modèle que si sa date de retrait laisse les 3 mois nécessaires ; sinon, elle exige une nouvelle phase 1 sur le modèle successeur, avec sa propre fenêtre.

## 7. Schéma de sortie du LLM

```json
{
  "event_type": "earnings | guidance | contract | m&a | management | financing | legal | other",
  "direction": -2,
  "magnitude": 0,
  "confidence": 0.0,
  "priced_in": 0.0,
  "horizon_days": 5,
  "key_facts": ["max 3 faits tirés du texte"],
  "rationale": "2 phrases max"
}
```

`direction` ∈ {−2…+2}, `magnitude` ∈ {0…3}, `confidence` et `priced_in` ∈ [0, 1]. Score composite = `direction × magnitude × confidence × (1 − priced_in)`. Toute réponse non conforme est rejetée et journalisée, pas corrigée à la main.

Validation (D25, D26) :
- est rejetée toute clé absente, en trop ou répétée, tout mauvais type et toute valeur hors bornes ;
- `horizon_days` doit être ≥ 1 et `key_facts` contenir au plus 3 éléments ;
- `rationale` doit contenir au moins un caractère visible ; les « 2 phrases » ne sont pas vérifiées automatiquement ;
- seuls sont retirés les espaces de bord et un unique emballage ```` ```json … ``` ```` entourant toute la réponse ; le contenu n'est jamais modifié ;
- une réponse interrompue est un rejet ; une erreur d'API laisse le dépôt non noté.

## 8. Mesures

Calculées par `src/news_alpha/analysis.py`, figé par son empreinte et testé sur données fictives uniquement (D37).

- **Population** : dépôts éligibles, notés avec une sortie valide, dont les rendements anormaux contre IWM à 1, 5 et 20 séances sont connus. Elle est formée avant les quintiles et vaut pour les trois horizons. Rejets et rendements manquants sont comptés et rapportés, jamais imputés.
- **Événement** : une société (CIK) × une séance d'entrée. Plusieurs 8-K d'une société qui entrent à la même séance forment un seul événement, noté par le composite le plus élevé en valeur absolue, signe conservé (à égalité, le premier déposé). Le mois de l'événement est le mois d'acceptation de ce dépôt.
- **Valeurs extrêmes** : chaque mois, les rendements anormaux sont ramenés à leurs 1er et 99e centiles (winsorisation). Moyenne brute et médiane sont rapportées à côté.
- **Quintiles** : chaque mois civil, les événements sont classés par composite en 5 groupes. Les égalités, comme les nombreux scores nuls, sont départagées par une clé aléatoire de graine 20250401, tirée dans l'ordre des numéros d'accession triés. Les quintiles haut et bas reçoivent chacun n // 5 événements ; le reste va aux quintiles du milieu.
- **Critère** : chaque mois, rendement anormal moyen du quintile haut à 20 séances, moins 1,2 % de frais aller-retour (hypothèse : 0,5 % de change à l'aller et au retour chez un broker européen + spread). On en prend la moyenne sur les mois, avec une t-stat de Newey-West à 1 retard, qui tient compte du chevauchement des rendements de fin de mois sur le mois suivant.
- **Rapporté pour information**, hors décision : le même critère à 2 % et 3 % de frais ; le quintile haut à 5 séances ; l'écart haut − bas à 5 et 20 séances, brut et net ; la t-stat simple ; le rendement anormal moyen et médian par quintile et par horizon ; le taux de réussite (part des événements du quintile haut au-dessus de +1,2 % à 20 séances) ; l'information coefficient (corrélation de rang de Spearman entre score et rendement à 5 séances, chaque mois) ; les mêmes mesures contre l'ETF sectoriel ; le critère sans les sociétés sorties de cote pendant l'horizon ; le coût LLM total et par événement.

## 9. Critères de go/no-go

**Phase 0 → 1** : le pipeline tourne de bout en bout sur 20 dépôts sans intervention, tests verts. *Rempli le 25/10/2026.*

**Phase 1 → 2**, tous requis :
- sur la période d'analyse (01/09/2025 – 30/06/2026) :
  - au moins 300 événements ;
  - critère du §8 > 0 en moyenne mensuelle, avec une t-stat de Newey-West > 2 ;
  - critère positif au moins 7 mois sur 10 ;
- sur l'échantillon réservé (juillet – septembre 2026) : moyenne des 3 valeurs mensuelles du critère > 0, et au moins égale à la moitié de celle de la période d'analyse.

Sinon : arrêt et note de conclusion. Un résultat négatif propre est un résultat.

## 10. Phase 2 (préliminaire, à figer avant de démarrer)

Paper trading Alpaca, long uniquement, pas de levier, 10 positions max à poids égal, sortie à 20 séances ou stop à −8 %. Comparaison à IWM et SPY. Frais simulés appliqués à chaque ordre.

## 11. Stack et organisation

- Python 3.12, gestion des dépendances avec `uv` (paquet construit avec `uv_build`), SQLite, SDK `anthropic`, `pydantic` pour le schéma, `pytest`. HTTP, lecture de `.env` et statistiques par la bibliothèque standard ; aucune autre dépendance sans accord.
- Secrets dans `.env` (jamais commités) : `ANTHROPIC_API_KEY`, `SEC_USER_AGENT`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`.
- Budget de la phase 1, estimé d'après septembre à novembre 2025 (de 1 174 à 2 111 éligibles par mois selon la saison des résultats, 0,0065 $ par appel) : environ 16 500 appels au modèle (~105 $) sur la période d'analyse, dans un plafond accordé de 17 000 appels (~110 $), puis environ 4 500 (~30 $) sur l'échantillon réservé, à accorder séparément (D28). L'estimation initiale, faite sur avril 2025, était de 9 400 appels. Le plafond de 50 appels par exécution n'est relevé, avec `--max-calls`, qu'au lancement et avec accord explicite.
- Arborescence :

```
news-alpha/
  SPEC.md  DECISIONS.md  README.md  .env.example
  prompts/score_8k_v1.md   # phase 0, archivé
  prompts/score_8k_v2.md   # phase 1
  src/news_alpha/{ingest,filters,extract,score,prices,returns,report,run,net,db,window,analysis}.py
  data/          # SQLite + caches + fichiers temporaires, ignoré par git
  reports/       # sorties d'analyse
  tests/         # dont mutations.py, tests par mutation des règles critiques (D38)
```

## 12. Hors périmètre

Interface graphique, exécution réelle, réseaux sociaux, ventes à découvert, options, intraday, optimisation de portefeuille.

## 13. Historique des versions

- **0.1 — 24/10/2026** : spec de départ.
- **0.2 — 25/10/2026** : règles figées pour la phase 1, d'après la phase 0. Changements :
  - cotation lue sur le dépôt (biais du survivant) ;
  - actions en circulation (secours par classes, ajustement des splits) ;
  - yfinance retiré ;
  - traitement en deux passes avec tirage aléatoire ;
  - validation de la sortie précisée (emballage JSON) ;
  - fenêtre, période d'analyse et échantillon réservé datés ;
  - méthode statistique (quintiles mensuels, t-stat sur les mois) ;
  - tous les éligibles notés.
- **0.3 — 25/10/2026** : corrections après l'audit indépendant de la branche `phase1-prep` (`reports/audit_phase1_prep.md`), avant tout résultat de phase 1. Changements :
  - fenêtre ouverte le 01/09/2025 : les données d'entraînement du modèle vont jusqu'à juillet 2025 (C01) ;
  - critère de décision : quintile haut net de frais à 20 séances, au lieu de l'écart haut − bas ; H1 réécrite (C04, C05) ;
  - au moins 7 mois positifs sur 10 ; t-stat de Newey-West (C15) ;
  - unité d'observation, sorties de cote, winsorisation, départage des égalités et confirmation sur le réservé définis (C02, C06, C07) ;
  - module d'analyse écrit et figé avant la campagne, empreinte inscrite ici (C02) ;
  - SPAC et BDC exclus ; code SIC lu à la date du dépôt (C25, C26) ;
  - prompt v2 : il précise si le titre a coté depuis le dépôt, et le texte du dépôt ne peut plus fermer sa balise (C16, C19) ; prompt figé par son empreinte (C18) ;
  - température 0 avec la version d'API 2023-06-01 (C20) ;
  - validation resserrée : emballage ```` ```json ```` seul, clé répétée et `rationale` blanc rejetés (C17) ;
  - repli IEX supprimé (C03) ;
  - garde-fous d'exécution : rapport par exécution, `--max-calls`, échantillon réservé verrouillé (C22) ;
  - règle de continuité en cas de retrait du modèle (C10) ;
  - frais de 2 % et 3 % rapportés pour information.
