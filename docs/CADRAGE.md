# data-analyst-agent — Cahier des charges

Nom du projet / dossier / repo : **`data-analyst-agent`**. Package Python : `data_analyst_agent`.

## 1. Pitch

`data-analyst-agent` est un **agent conversationnel sur données**, auto-hébergé. À partir d'une **source déclarée** (fichier Excel/CSV, ou base Postgres à plusieurs tables jointes), l'utilisateur pose sa question en langage naturel et le système sait :

1. **Récupérer** la bonne donnée dans la bonne source (SQL avec jointures, ou requête sur fichier) ;
2. **Analyser** — calculer des KPI et de vraies statistiques (moyennes, %, tests du χ², ANOVA, ACP…) **et produire des visualisations** (ex. bar chart) en générant puis exécutant du code Python dans un bac à sable ;
3. **Prédire** — appeler le bon modèle de ML sur des features validées, en relançant l'utilisateur si les données sont incomplètes.

Réponse rendue en **langage naturel + objets affichables** (tableau, figure). Orchestration explicite, traçable, débuggable, avec **un seul LLM mutualisé** pour tous les rôles langage.

### Exemples de questions cibles (scénarios « golden »)
- « Quels **magasins réalisent le plus de CA** en 2025 ? » → ① requête SQL agrégée (jointure `sales_daily` × `stores`).
- « **Fais-moi un bar chart** du CA par magasin. » → ② code de viz exécuté en sandbox → figure.
- « **Combien d'unités vendra-t-on** : grand magasin, univers chien, marque nationale à 49,90 €, un samedi de novembre, en promo −30 % ? » → ③ validation + modèle.

Ces trois scénarios servent de **tests end-to-end de référence** (cf. §12).

## 2. Objectifs & périmètre

**V1 (ce qu'on construit) :**
- Chat qui répond en langage naturel + objets affichables.
- ① Récupération : catalogue de sources, text-to-SQL avec jointure sur Postgres, requêtes sur Excel/CSV/base DuckDB. Une source peut déclarer un **dictionnaire** (Markdown) chargé dans le contexte de l'agent : le DDL donne les types, le dictionnaire donne le sens et les pièges de modélisation.
- ② Analyse : génération + exécution de code stat **et viz** dans un sandbox durci.
- ③ Inférence : validation Pydantic des features, slot-filling conversationnel, appel du bon modèle. Modèle livré : **prévision des ventes Maxizoo** (régression — quantité vendue par SKU × magasin × jour). Le registre reste multi-modèles et le chemin classification est conservé.
- Chaînage ①→③ : récupérer une ligne en base → la mapper sur le schéma de features → valider → prédire.

**Hors périmètre V1 (plus tard) :**
- ④ AutoML / entraînement automatique (les modèles V1 sont pré-entraînés à la main).
- Multi-tenant avancé, RBAC fin, gros volumes.

## 3. Contraintes (fermes)

- **Déploiement on-premise** : aucune dépendance à un service cloud obligatoire. Réseau sortant potentiellement coupé.
- **Commercialisable** : **toute dépendance doit être sous licence permissive (MIT / Apache-2.0 / BSD)**. GPL/AGPL et licences non-commerciales **interdites** (ex. `pingouin` en GPL-3 → exclu ; utiliser scipy + statsmodels).
- **Échelle cible** : petite équipe, **10 à 20 utilisateurs** max.
- **Matériel** :
  - Dev (machine floSa) : GPU RTX 4060 Ti (~16 Go VRAM), Ryzen 5 9600X, 64 Go RAM.
  - Prod visée : VM avec **NVIDIA L4 (24 Go VRAM)**.
- **Python géré par `uv`**, version **3.12**. Outillage : `ruff` (format + lint), `pytest`, Docker.

## 4. Architecture

```
                         ┌───────────────────────────┐
   utilisateur  ───────► │  ORCHESTRATEUR (LangGraph) │
                         │  planner → route → synthèse│
                         └───────────┬───────────────┘
                                     │  utilise
                          ┌──────────▼───────────┐
                          │  UN SEUL LLM          │
                          │  Qwen3-Coder          │  ← mutualisé partout
                          │  (14B dev / 32B prod) │
                          └──────────┬───────────┘
             ┌───────────────────────┼───────────────────────┐
             ▼                       ▼                       ▼
    ① RÉCUPÉRATION           ② ANALYSE                ③ INFÉRENCE
    routeur de source        LLM génère du code       valide features (Pydantic)
    → ontologie              → SANDBOX Jupyter        → appelle le .pkl du
    → SQL (jointures)        (stats + viz)            registry → predict
    [Postgres / DuckDB]                               [déterministe, sans LLM]
             └───────────────────────┼───────────────────────┘
                                     ▼
                        réponse NL + objets (DataFrame, figure)
```

**Principe directeur** : un graphe d'orchestration **explicite** (nœuds = agents typés). Le pipeline n'est pas une boîte noire : il s'inspecte, se trace, se rejoue. Contrat typé entre nœuds (Pydantic) → les erreurs pètent à la frontière avec un message clair. La règle de routage est du **code**, pas du prompt. Une même demande peut enchaîner plusieurs briques (ex. requête ① puis graphe ②).

## 5. Le LLM mutualisé

- **Un seul modèle** pour tout ce qui est langage : router, générer le SQL, générer le code de stats/viz, rédiger la réponse.
- **Qwen3-Coder** (Apache-2.0), servi par **Ollama**.
  - *Réalité registry (constatée 2026-07)* : la famille n'existe qu'en **30B-A3B** (MoE, 3B actifs, ~19 Go en Q4_K_M) et 480B. Pas de 14B/32B dense.
  - Dev (4060 Ti 16 Go) : `qwen3-coder:30b` en répartition GPU+RAM (64 Go) — MoE 3B actifs, débit acceptable.
  - Prod (L4 24 Go) : `qwen3-coder:30b` Q4 tient entièrement en VRAM.
  - Modèle configurable via `DAA_LLM_MODEL` (fallback possible : `qwen2.5-coder:14b`, dense, ~9 Go).
- À ne pas confondre avec les **modèles ML métier** (prévision des ventes Maxizoo) : artefacts scikit-learn séparés, appelés par ③, sans LLM dans le calcul.
- Qwen3-Coder-Next (80B MoE) écarté : ne tient ni sur la 4060 Ti ni sur la L4.

## 6. La sandbox (capacité ② et exécution de code)

| Aspect | Décision |
|---|---|
| Isolation | Docker durci, **réseau coupé** (default-deny), quotas CPU/RAM, éphémère |
| Contenu | un **kernel Jupyter** → sorties MIME (`text`, `image/png`, `application/json`) |
| Rôle | exécuter le code Python généré par l'agent Analyse (stats **et** figures) |
| Build image | via **`uv`** (lockfile) ; socle pré-installé : pandas, numpy, scipy, statsmodels, prince, scikit-learn, matplotlib, plotly, duckdb |
| Extension | `uv` vers un **miroir PyPI local** (jamais internet) |
| Fiabilité | boucle self-debug (ré-exécute sur erreur, N essais), timeout, code non persistant |
| Contrat de retour | `{ stdout, results: [{mime, data}], error }` |

## 7. Les capacités en détail

### ① Récupération
- **Routeur de source** : catalogue déclaratif (YAML) des sources connues (bases Postgres, fichiers). Un agent choisit la bonne source et expose son ontologie (tables, colonnes, types, relations).
- **Génération de requête** : tools SQL typés (`list_tables`, `get_schema`, `run_sql`) avec self-correction sur erreur SQL. Postgres pour les bases ; **DuckDB** pour requêter des fichiers Excel/CSV avec la même logique.
- Règle de routage : *base ou fichier + demande de requête/agrégat → SQL ; analyse stat multi-étapes ou viz → capacité ②.*

### ② Analyse / auto-stats & viz
- L'agent **génère du code Python** (pandas, scipy.stats, statsmodels, prince, matplotlib/plotly) et l'exécute dans la sandbox.
- Moteur : **maison**, inspiré du pattern `smolagents` (Apache-2.0).
- Couvre : KPI, %, distributions, tests (χ², ANOVA via statsmodels), analyses factorielles (prince), et **figures** (bar chart, etc.) renvoyées en `image/png`.
- **`pingouin` exclu (GPL-3)** — scipy + statsmodels couvrent les mêmes besoins sous BSD.

### ③ Inférence gardée
- **Schéma Pydantic écrit à la main**, un par dataset = source de vérité (champs, types, bornes).
- **Slot-filling** : `validate_features()` renvoie les erreurs structurées (manquant / hors bornes / mauvais type) → le LLM relance l'utilisateur. Logique unique : *valide → demande ce qui manque* (couvre le dump partiel comme le formulaire complet). **Pas de predict tant que ça ne valide pas.**
- **Registry léger maison** : YAML `dataset → {model_path, schéma, méta}`, modèles chargés via `joblib`. Cible d'évolution : **MLflow Model Registry** (Apache-2.0), même interface.
- **Predict déterministe** : classif → classe + probabilités ; régression → valeur. Le LLM formule en NL, l'orchestrateur garde l'objet.
- **Extensibilité** : ajouter un dataset = 1 schéma Pydantic + 1 `.pkl` enregistré + 1 ligne de registre.

## 8. Stack technique (tout permissif)

| Brique | Choix | Licence |
|---|---|---|
| Orchestration | LangGraph | MIT |
| Agents (nœuds typés) | PydanticAI + Pydantic v2 | MIT |
| LLM serving | Ollama + Qwen3-Coder | MIT / Apache-2.0 |
| Text-to-SQL (socle) | tools maison + SQLAlchemy | MIT / BSD |
| Text-to-SQL (spike comparatif) | Vanna (MIT, upstream archivé) ; alt. WrenAI (Apache) | MIT / Apache-2.0 |
| Fichiers → SQL | DuckDB | MIT |
| Analyse & viz | pandas, numpy, scipy, statsmodels, prince, scikit-learn, matplotlib, plotly | BSD / MIT |
| Moteur code-agent | smolagents | Apache-2.0 |
| Sandbox | Docker + kernel Jupyter (ipykernel) | Apache-2.0 / BSD |
| Inférence registry | joblib → MLflow | BSD / Apache-2.0 |
| API / chat | FastAPI + uvicorn | MIT / BSD |
| Tests | pytest, pytest-cov, testcontainers (Postgres) | MIT / Apache-2.0 |
| Observabilité | traces LangGraph + OpenTelemetry | MIT / Apache-2.0 |

## 9. Décisions — figées vs à trancher

**Figé :** cible on-prem + commercialisable (MIT/Apache/BSD only) · LLM mutualisé Qwen3-Coder · sandbox Docker+Jupyter buildée avec uv, réseau coupé · analyse maison (scipy/statsmodels/prince, pas pingouin) · inférence Pydantic-à-la-main + registry maison → MLflow · 3 capacités sous orchestrateur, contrat de retour MIME · **couverture de tests maximale exigée (cf. §12)**.

**À trancher (recos par défaut) — à confirmer avant de les coder :**
1. **Text-to-SQL** : tools-maison comme socle **+ spike Vanna** pour comparer. *(alt. WrenAI, Apache, maintenu.)*
2. **Excel ad-hoc** : DuckDB pour requête/jointure, pandas pour la stat.
3. **Orchestration** : LangGraph + nœuds PydanticAI.

## 10. Arborescence de repo

```
data-analyst-agent/
├── pyproject.toml            # uv + deps
├── uv.lock
├── README.md
├── CLAUDE.md                 # consignes IA (fichier dédié)
├── docs/CADRAGE.md           # ce document
├── src/data_analyst_agent/
│   ├── config.py             # settings (pydantic-settings)
│   ├── llm.py                # client Ollama / Qwen3-Coder mutualisé
│   ├── orchestrator/         # graphe LangGraph, planner, routage, state
│   ├── agents/
│   │   ├── retrieval/        # catalog.py, sql.py, duckdb_excel.py
│   │   ├── analysis/         # agent.py, sandbox_client.py
│   │   └── inference/        # schemas/, registry.py, predict.py
│   ├── sandbox/              # Dockerfile, client.py
│   └── api/                  # FastAPI + chat
├── models/                   # .pkl (ou MLflow)
├── sources/                  # catalogue.yaml, excels de test
├── notebooks/                # entraînement des modèles jouets
└── tests/
    ├── unit/                 # par brique, isolé (mocks LLM/DB)
    ├── integration/          # sandbox réel, Postgres via testcontainers
    └── e2e/                  # les 3 scénarios golden (§12)
```

## 11. Ordre de construction (roadmap) — chaque étape livrée AVEC ses tests

0. **Scaffold** — `uv init`, ruff, pre-commit, CI GitHub Actions (lint + tests + couverture), structure, squelette `tests/`.
1. **Sandbox** — Dockerfile + client kernel Jupyter ; valider exécution de code + retour MIME. *(fondation)* — tests d'intégration sur exécution réelle.
2. **LLM** — client Ollama Qwen3-Coder, ping de bout en bout (tests avec réponse mockée + un test live optionnel).
3. **② Analyse** — agent → sandbox sur un CSV (stat + un bar chart).
4. **① Récupération** — catalogue + tools SQL Postgres (testcontainers) + DuckDB sur Excel.
5. **③ Inférence** — entraîner les 3 modèles jouets (notebooks/), puis schémas Pydantic + registry + predict.
6. **Orchestrateur** — LangGraph (planner + routage + chaînage ①→③).
7. **API + chat** — FastAPI, interface minimale.
8. **Observabilité** — traces, rejouabilité.
9. **Spike Vanna** — en parallèle, comparé au socle maison.

Commiter à la fin de chaque étape vérifiée **et testée** (cf. CLAUDE.md).

## 12. Stratégie de tests (exigence forte : couverture maximale avant présentation)

**Principe : rien n'est « fait » sans tests verts. La solution n'est pas présentable tant que la suite complète (dont les 3 scénarios golden) ne passe pas.**

- **Tests unitaires** (`tests/unit/`) : chaque fonction/agent isolé. LLM et DB **mockés**. Couvrent la validation Pydantic (cas manquant / hors bornes / mauvais type), le routage, le parsing des sorties sandbox, le registry.
- **Tests d'intégration** (`tests/integration/`) : sandbox **réelle** (exécution de code + retour MIME), Postgres via **testcontainers**, DuckDB sur un Excel de test, chargement réel des `.joblib`. Les données sont un **échantillon versionné** de la base (`tests/fixtures/maxizoo_mini/`), extrait pour porter les 6 pièges du dictionnaire : la base complète (180 Mo) n'est pas versionnée, et un échantillon qui perdrait les pièges laisserait passer les régressions qu'on veut attraper.
- **Tests end-to-end** (`tests/e2e/`) — les 3 scénarios golden, du message utilisateur à la réponse :
  1. « quels magasins réalisent le plus de CA » → valeurs numériques correctes (vérifiées contre un calcul pandas de référence), et `Canal Online` en tête : le e-commerce est une ligne de `stores` (piège n°1 du dictionnaire).
  2. « bar chart du CA par magasin » → un objet `image/png` non vide est produit.
  3. « combien d'unités vendra-t-on … » → une valeur cohérente avec son unité ; et le cas **features incomplètes** → le système redemande (pas de predict).
- **Robustesse** : tests des chemins d'erreur (SQL invalide → self-correction ; code sandbox qui plante → boucle self-debug ; timeout).
- **Qualité** : `ruff check` + `ruff format --check` en CI ; **couverture visée ≥ 85 %** (`pytest-cov`), la CI échoue en dessous.
- **Déterminisme** : les tests ne dépendent pas d'un appel LLM réseau (mock/enregistrement) ; un éventuel test « live LLM » est marqué `@pytest.mark.live` et exclu de la CI par défaut.

## 13. Références (état de l'art, mi-2026)

- Orchestration : pattern **Plan-and-Execute** ; LangGraph + PydanticAI se composent (agent typé = nœud).
- Text-to-SQL : benchmark **BIRD** ; Vanna (RAG, le LLM ne voit que le schéma, jamais les lignes).
- Sandbox : E2B (open-source, Firecracker) comme référence ; ici auto-hébergé via kernel Jupyter conteneurisé.
- Analyse : survey *LLM/Agent-as-Data-Analyst* (arXiv 2509.23988) ; MetaGPT Data Interpreter, DeepAnalyze.
- Modèles locaux : famille **Qwen3-Coder** (Apache-2.0), référence coder/SQL local mi-2026.
