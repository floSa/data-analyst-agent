# data-analyst-agent — Cahier des charges

> **Ce document est le cahier des charges d'origine** : les contraintes et les décisions de départ, pas l'état du jour. Il reste à ce chemin parce que `src/` le cite. Index des documents de chantier : [historique/README.md](historique/README.md).

Nom du projet / dossier / repo : **`data-analyst-agent`**. Package Python : `data_analyst_agent`.

## 1. Pitch

`data-analyst-agent` est un **agent conversationnel sur données**, auto-hébergé. À partir d'une **source déclarée** (fichier Excel/CSV, ou base Postgres à plusieurs tables jointes), l'utilisateur pose sa question en langage naturel et le système sait :

1. **Récupérer** la bonne donnée dans la bonne source (SQL avec jointures, ou requête sur fichier) ;
2. **Analyser** — calculer des KPI et de vraies statistiques (moyennes, %, tests du χ², ANOVA, ACP…) **et produire des visualisations** (ex. bar chart) en générant puis exécutant du code Python dans un bac à sable ;
3. **Prédire** — appeler le bon modèle de ML sur des features validées, en relançant l'utilisateur si les données sont incomplètes.

Réponse rendue en **langage naturel + objets affichables** (tableau, figure). Orchestration explicite, traçable, débuggable, avec **un seul LLM mutualisé** pour tous les rôles langage.

### Exemples de questions cibles (scénarios « golden »)
- « À partir de la table `passengers`, donne-moi le **% de femmes de 1ʳᵉ classe qui ont survécu**. » → ① requête SQL agrégée.
- « **Fais-moi un bar chart** de la survie par classe. » → ② code de viz exécuté en sandbox → figure.
- « **Fais-moi la prédiction** pour un passager : sexe=female, classe=1, âge=28… » → ③ validation + modèle.

Ces trois scénarios servent de **tests end-to-end de référence** (cf. §12).

## 2. Objectifs & périmètre

**V1 (ce qu'on construit) :**
- Chat qui répond en langage naturel + objets affichables.
- ① Récupération : catalogue de sources, text-to-SQL avec jointure sur Postgres, requêtes sur Excel/CSV via DuckDB.
- ② Analyse : génération + exécution de code stat **et viz** dans un sandbox durci.
- ③ Inférence : validation Pydantic des features, slot-filling conversationnel, appel du bon modèle. Datasets jouets : **Titanic** (classif.), **Iris** (classif.), **California Housing** (régression — PAS Boston, retiré de scikit-learn).
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
- **Qwen3-Coder** (Apache-2.0), servi par le moteur d'inférence local.
  - *Réalité registry (constatée 2026-07)* : la famille n'existe qu'en **30B-A3B** (MoE, 3B actifs, ~19 Go en Q4_K_M) et 480B. Pas de 14B/32B dense.
  - Dev (4060 Ti 16 Go) : `qwen3-coder:30b` en répartition GPU+RAM (64 Go) — MoE 3B actifs, débit acceptable.
  - Prod (L4 24 Go) : `qwen3-coder:30b` Q4 tient entièrement en VRAM.
  - Modèle configurable via `DAA_LLM_MODEL` (fallback possible : `qwen2.5-coder:14b`, dense, ~9 Go).
- À ne pas confondre avec les **modèles ML métier** (Titanic/Iris/California) : artefacts scikit-learn séparés, appelés par ③, sans LLM dans le calcul.
- Qwen3-Coder-Next (80B MoE) écarté : ne tient ni sur la 4060 Ti ni sur la L4.

> **Note de relecture (2026-09-15) — la seule décision de cadrage que les faits ont
> démentie.** Ce document est historique et n'est pas réécrit ; cette section fait
> exception parce qu'elle décrit un choix qui n'a jamais eu lieu. **`qwen3-coder:30b`
> n'a jamais été chargé sur le service central**, et le repli du code qui le désignait
> échouait en `404 model not found` — le masquage des erreurs ne laissant qu'un « je
> n'ai pas réussi à interpréter la demande » dans la réponse. Le modèle réellement
> servi, et le défaut du code depuis, est **`google/gemma-4-E4B-it-qat-w4a16-ct`**
> ([`config.py`](../src/data_analyst_agent/config.py)) ; le `.env` reste maître.
> Le raisonnement de cette section — *un seul modèle pour tout ce qui est langage* —
> tient, et c'est lui qu'il faut lire ici ; le nom du modèle, non. Deux conséquences
> ont suivi et sont documentées ailleurs : le **moteur** n'est plus nommé dans le code
> (l'application ne parle que `/v1/chat/completions`, `DAA_LLM_BASE_URL` suffit à
> désigner le serveur — [ARCHITECTURE §4.3](ARCHITECTURE.md#43-llmpy--configpy--llm-mutualisé-et-réglages),
> [MOTEUR.md](MOTEUR.md)), et la licence du modèle servi est **déclarée par le modèle
> lui-même**, donc à revérifier à chaque changement (voir le tableau des licences du
> [README](../README.md#licences--composants)).

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
| LLM serving | moteur d'inférence local + Qwen3-Coder | MIT / Apache-2.0 |
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

Telle qu'elle est, et non telle qu'elle était visée à l'ouverture du projet : cette
section décrit le dépôt tel qu'il est construit, authentification, prompts
externalisés, mémoire de conversation et scripts d'exploitation compris.

```
data-analyst-agent/
├── pyproject.toml                # uv + deps + config ruff/pytest/coverage
├── uv.lock
├── README.md                     # démarrage, routes, mémoire, DEUX BRANCHES
├── .env.example                  # modèle de configuration (dont les DAA_PG_*)
├── users.example.yaml            # forme du magasin de comptes (le vrai n'est pas versionné)
├── docs/
│   ├── CADRAGE.md                # ce document (le pourquoi)
│   ├── ARCHITECTURE.md           # le comment, service par service + réglages (§7)
│   ├── AUDIT-2026-09.md          # état des lieux et backlog priorisé
│   ├── MOTEUR.md                 # banc d'essai du tool calling sur vLLM
│   └── historique/               # les journaux de chantier (cf. son README)
│       └── spike-vanna.md        # comparaison text-to-SQL (verdict : socle maison)
├── src/data_analyst_agent/
│   ├── config.py                 # Settings (pydantic-settings), préfixe DAA_
│   ├── llm.py                    # LLM mutualisé, endpoint OpenAI-compatible
│   ├── prompts/                  # les 4 prompts système, hors du code (.txt)
│   ├── orchestrator/
│   │   ├── plan.py               # modèle Plan + agent à sortie structurée
│   │   ├── graph.py              # graphe LangGraph, nœuds gardés, règles du plan
│   │   ├── context_budget.py     # fenêtre, budget de tokens, débordement constaté
│   │   ├── workspace.py          # mémoire d'un fil (tableaux intermédiaires, contexte)
│   │   └── conversations.py      # persistance des fils, par utilisateur
│   ├── agents/
│   │   ├── retrieval/            # catalog.py, sql.py, duckdb_excel.py, agent.py
│   │   ├── analysis/             # agent.py (génération de code + self-debug)
│   │   └── inference/            # schemas/, validation.py, registry.py, predict.py
│   ├── auth/                     # accounts.py, sessions.py, throttle.py, rate_limit.py
│   ├── sandbox/                  # client.py + image/ (Dockerfile, bridge.py, lockfile)
│   └── api/                      # app.py, pages.py, templates/ (chat + connexion)
├── models/                       # artefacts joblib + registry.yaml
├── sources/                      # catalogue.yaml + datasets vendorisés
├── notebooks/                     # entraînement des modèles jouets (jupytext .md + .ipynb)
├── scripts/                      # comptes, migration du workspace, seed, mesures, bancs
├── var/                          # NON versionné : comptes, sessions, workspaces
└── tests/
    ├── unit/                     # par brique, isolé (LLM scripté, sandbox doublée)
    ├── integration/              # sandbox réelle, Postgres via testcontainers
    ├── e2e/                      # les scénarios golden (§12)
    ├── fakes/                    # faux bridge de sandbox
    ├── helpers/                  # ScriptedLLM, doublures, seed + oracle Titanic
    └── fixtures/                 # échantillons de données
```

`var/` n'existe pas dans le dépôt : il est créé au premier usage, en `0o700`, et
porte tout ce qui est propre à un déploiement — comptes, sessions, conversations.

## 11. Ordre de construction (roadmap) — chaque étape livrée AVEC ses tests

0. **Scaffold** — `uv init`, ruff, pre-commit, CI GitHub Actions (lint + tests + couverture), structure, squelette `tests/`.
1. **Sandbox** — Dockerfile + client kernel Jupyter ; valider exécution de code + retour MIME. *(fondation)* — tests d'intégration sur exécution réelle.
2. **LLM** — client Qwen3-Coder sur endpoint OpenAI-compatible, ping de bout en bout (tests avec réponse mockée + un test live optionnel).
3. **② Analyse** — agent → sandbox sur un CSV (stat + un bar chart).
4. **① Récupération** — catalogue + tools SQL Postgres (testcontainers) + DuckDB sur Excel.
5. **③ Inférence** — entraîner les 3 modèles jouets (notebooks/), puis schémas Pydantic + registry + predict.
6. **Orchestrateur** — LangGraph (planner + routage + chaînage ①→③).
7. **API + chat** — FastAPI, interface minimale.
8. **Observabilité** — traces, rejouabilité.
9. **Spike Vanna** — en parallèle, comparé au socle maison.

Commiter à la fin de chaque étape vérifiée **et testée**. Les consignes de
contribution vivent dans un fichier local **non versionné** (voir `.gitignore`) :
elles ne sont donc pas dans un dépôt fraîchement cloné, et les conventions qui
comptent pour un contributeur extérieur sont dans le [README](../README.md#qualité).

## 12. Stratégie de tests (exigence forte : couverture maximale avant présentation)

**Principe : rien n'est « fait » sans tests verts. La solution n'est pas présentable tant que la suite complète (dont les 3 scénarios golden) ne passe pas.**

- **Tests unitaires** (`tests/unit/`) : chaque fonction/agent isolé. LLM et DB **mockés**. Couvrent la validation Pydantic (cas manquant / hors bornes / mauvais type), le routage, le parsing des sorties sandbox, le registry.
- **Tests d'intégration** (`tests/integration/`) : sandbox **réelle** (exécution de code + retour MIME), Postgres via **testcontainers**, DuckDB sur un Excel de test, chargement réel des `.pkl`.
- **Tests end-to-end** (`tests/e2e/`) — les 3 scénarios golden, du message utilisateur à la réponse :
  1. « % de femmes de 1ʳᵉ classe qui ont survécu » → valeur numérique correcte (vérifiée contre un calcul pandas de référence).
  2. « bar chart de la survie par classe » → un objet `image/png` non vide est produit.
  3. « prédiction pour ce passager … » → une classe + une probabilité cohérentes ; et le cas **features incomplètes** → le système redemande (pas de predict).
- **Robustesse** : tests des chemins d'erreur (SQL invalide → self-correction ; code sandbox qui plante → boucle self-debug ; timeout).
- **Qualité** : `ruff check` + `ruff format --check` en CI ; **couverture visée ≥ 85 %** (`pytest-cov`), la CI échoue en dessous.
- **Déterminisme** : les tests ne dépendent pas d'un appel LLM réseau (mock/enregistrement) ; un éventuel test « live LLM » est marqué `@pytest.mark.live` et exclu de la CI par défaut.

## 13. Références (état de l'art, mi-2026)

- Orchestration : pattern **Plan-and-Execute** ; LangGraph + PydanticAI se composent (agent typé = nœud).
- Text-to-SQL : benchmark **BIRD** ; Vanna (RAG, le LLM ne voit que le schéma, jamais les lignes).
- Sandbox : E2B (open-source, Firecracker) comme référence ; ici auto-hébergé via kernel Jupyter conteneurisé.
- Analyse : survey *LLM/Agent-as-Data-Analyst* (arXiv 2509.23988) ; MetaGPT Data Interpreter, DeepAnalyze.
- Modèles locaux : famille **Qwen3-Coder** (Apache-2.0), référence coder/SQL local mi-2026.
