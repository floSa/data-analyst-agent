# Architecture — data-analyst-agent

Document de référence technique. Le *pourquoi* (contraintes, décisions, roadmap) est
dans [CADRAGE.md](CADRAGE.md) ; ici on décrit le *comment* : les schémas d'ensemble,
puis chaque service du package.

## 1. Principes directeurs

- **Orchestration explicite** : un graphe LangGraph typé, inspectable, tracé. La règle
  de routage est du code, pas du prompt.
- **Un seul LLM mutualisé** (Qwen3-Coder via Ollama) pour tous les rôles langage :
  planification, SQL, code d'analyse, synthèse. Les modèles ML métier (predict) sont
  des artefacts scikit-learn séparés — aucun LLM dans le calcul.
- **Tout code généré s'exécute en sandbox durcie** : conteneur éphémère, réseau coupé,
  rootfs en lecture seule.
- **Contrats Pydantic aux frontières** : les erreurs éclatent à la frontière du nœud,
  avec un message clair, sans faire tomber le graphe.
- **Licences permissives uniquement** (MIT / Apache-2.0 / BSD) — produit on-premise et
  commercialisable.

## 2. Schéma architectural (composants)

```mermaid
flowchart TB
    subgraph client["Client"]
        UI["Page de chat<br/>(HTML inline, zéro asset externe)"]
    end

    subgraph app["data-analyst-agent"]
        API["API FastAPI<br/>POST /chat · /conversations · GET /health"]
        ORCH["Orchestrateur LangGraph<br/>plan → route → capacité → synthèse"]
        LLM["Client LLM mutualisé<br/>PydanticAI → Ollama"]

        subgraph caps["Capacités"]
            RET["① Récupération<br/>catalogue + text-to-SQL à tools"]
            ANA["② Analyse<br/>génération de code stats/viz"]
            INF["③ Inférence gardée<br/>validation → predict déterministe"]
        end
    end

    subgraph infra["Infrastructure locale"]
        OLLAMA["Ollama<br/>qwen3-coder:30b"]
        SBX["Sandbox Docker<br/>kernel Jupyter · réseau coupé"]
        PG[("Postgres<br/>multi-tables")]
        FILES[("Fichiers<br/>CSV / Excel via DuckDB")]
        REG[("Registry modèles ML<br/>YAML + joblib")]
    end

    UI -->|JSON| API --> ORCH
    ORCH --> RET & ANA & INF
    ORCH -.->|prompts| LLM -.-> OLLAMA
    RET --> PG & FILES
    ANA -->|code Python| SBX
    INF --> REG
```

Réponse renvoyée au client : `{answer, artifacts[{mime,data}], plan, error, trace}` —
le texte en langage naturel plus les objets affichables (figure PNG en base64, table
JSON) et la trace d'exécution.

## 3. Schéma fonctionnel (le graphe)

```mermaid
flowchart LR
    Q(["question"]) --> PLAN["plan<br/>LLM → objet Plan"]
    PLAN -->|"query"| RETR["retrieval<br/>SQL lecture seule"]
    PLAN -->|"analyze"| ANAL["analysis<br/>code en sandbox"]
    PLAN -->|"predict"| INFE["inference<br/>valide → prédit"]
    PLAN -->|"fetch_then_predict"| FP["fetch_predict<br/>ligne SQL → features → prédit"]
    PLAN -->|"erreur"| SYN
    RETR --> SYN["synthesize"]
    ANAL --> SYN
    INFE --> SYN
    FP --> SYN
    SYN --> R(["réponse + artefacts + trace"])
```

Le planificateur classe la demande dans une capacité et en extrait les paramètres
(source, dataset, features). Le routage est ensuite mécanique. Chaque nœud est
« gardé » : une exception renseigne `error` dans le state et la synthèse produit une
réponse d'échec honnête au lieu d'un crash.

La synthèse choisit le mode le moins coûteux et le plus sûr :

| Situation | Mode de synthèse |
|---|---|
| Erreur d'un nœud | template déterministe (« Je n'ai pas pu répondre : … ») |
| Features invalides/incomplètes | la relance structurée, telle quelle (pas de LLM) |
| Prédiction réussie | template déterministe (classe, probabilité, unité) |
| Requête SQL réussie | le résumé déjà produit par l'agent récupération |
| Analyse réussie | LLM (transforme le stdout du code en 1-4 phrases) |

### Séquence type — scénario golden n°1 (requête SQL)

```mermaid
sequenceDiagram
    actor U as Utilisateur
    participant A as API
    participant O as Orchestrateur
    participant L as LLM (Ollama)
    participant P as Postgres

    U->>A: « Quels magasins réalisent le plus de CA en 2025 ? »
    A->>O: ask(question)
    O->>L: plan(question, sources, datasets)
    L-->>O: Plan{capability: query, source: maxizoo}
    O->>L: agent récupération (tools + dictionnaire)
    L-->>O: get_schema()
    O->>P: introspection (tables, colonnes, FK)
    L-->>O: run_sql(SELECT … JOIN stores …)
    O->>P: exécution (garde-fou lecture seule)
    P-->>O: taux = 96.81
    L-->>O: « 96,81 % des femmes de 1re classe ont survécu. »
    O-->>A: answer + table JSON + trace
    A-->>U: réponse affichée
```

En cas d'erreur SQL, le tool `run_sql` renvoie le message d'erreur au modèle qui
corrige sa requête — borné par `retrieval_request_limit` pour couper toute boucle.

## 4. Les services, un par un

### 4.1 `api/` — serveur HTTP et chat

`app.py` expose `POST /chat` (contrat `ChatAnswer` complet, trace comprise),
`GET /health` et `GET /` (page de chat inline : rendu des PNG base64 et des tables
JSON, aucun CDN — compatible réseau coupé). L'orchestrateur est construit
paresseusement au premier appel : le serveur démarre sans Ollama ni Docker.

**Multi-tours** : chaque réponse porte un `conversation_id` (généré si absent de la
requête) ; le serveur y associe l'éventuelle *prédiction en attente de features*
(`pending`). Quand l'utilisateur répond à une relance (« elle a 28 ans, billet à
80 livres... »), le planificateur reçoit le contexte (dataset, features déjà
connues, features manquantes), extrait les nouvelles valeurs, et l'orchestrateur
fusionne — le nouveau message prime sur l'acquis, ce qui permet aussi de corriger
une valeur refusée. Une digression solde le contexte.

**Persistance des conversations** (barre latérale) : le fil est écrit sur disque, il
survit donc au rechargement de la page comme au redémarrage du serveur.

| Route | Rôle |
|---|---|
| `GET /conversations` | résumés (id, titre, horodatages, nb de messages), du plus récent au plus ancien |
| `GET /conversations/{id}` | le fil complet — messages, artefacts et `pending` : de quoi reprendre où on en était |
| `POST /conversations/{id}/duplicate` | copie sous un nouvel id |
| `DELETE /conversations/{id}` | supprime le fil **et** sa mémoire |

Le titre est tiré du premier message. Une conversation est un **dossier unique**
(`workspace_dir/<id>/`) : `transcript.json` y voisine le manifeste et les CSV des
tableaux intermédiaires (§4.2). D'où deux propriétés : dupliquer est une copie de
dossier, donc la copie hérite de la mémoire de l'originale (« prédis ces lignes »
fonctionne encore) et évolue ensuite indépendamment ; supprimer efface aussi les
CSV, sans laisser de données orphelines.

### 4.2 `orchestrator/` — plan et graphe

- `plan.py` — le modèle `Plan` (capability, source, dataset, features,
  data_question) et l'agent planificateur PydanticAI à **sortie structurée** : le
  prompt liste les sources du catalogue et les modèles ML avec leurs features
  attendues ; le LLM n'a le droit de choisir que dans ces listes.
- `graph.py` — le `StateGraph` LangGraph : state typé (`TypedDict` avec accumulation
  des artefacts et de la trace), nœuds gardés, routage code, chaînage
  `fetch_then_predict` (lignes SQL → intersection avec les champs du schéma de
  features, insensible à la casse → validation → predict ; ce que l'utilisateur a
  fourni explicitement prime sur la ligne lue). **Une ligne récupérée → prédiction
  unitaire ; plusieurs lignes (« toutes les femmes ») → prédiction en lot** :
  chaque ligne validée, les valides prédites en un seul appel modèle vectorisé,
  les invalides écartées et comptées, réponse agrégée (répartition des classes ou
  moyenne) + table de détail ligne à ligne en artefact. Chaque nœud produit un
  `TraceStep{node, detail, duration_ms}` et journalise (logger
  `data_analyst_agent.orchestrator`).

### 4.3 `llm.py` + `config.py` — LLM mutualisé et réglages

`build_model()` fabrique l'unique modèle PydanticAI, pointé sur l'endpoint
OpenAI-compatible d'Ollama, température 0 par défaut. `Settings`
(pydantic-settings) centralise tous les réglages, surchargeables par variables
d'environnement `DAA_*` ou `.env` (tableau complet en §7).

### 4.4 `agents/retrieval/` — capacité ① Récupération

- `catalog.py` — catalogue **déclaratif** des sources (`sources/catalogue.yaml`) :
  `postgres` (DSN SQLAlchemy, `${VARIABLES}` d'environnement autorisées) ou `file`
  (CSV/Excel, chemin relatif au YAML). `open_source()` renvoie l'adaptateur adapté.
- `sql.py` — l'ontologie (tables, colonnes, types, clés primaires/étrangères) rendue
  en DDL compact pour le prompt ; le **garde-fou lecture seule** (une seule
  instruction, `SELECT`/`WITH` uniquement, mots-clés d'écriture bloqués) ;
  l'adaptateur Postgres via **pg8000** (BSD — psycopg est LGPL, écarté par la règle
  licences) ; les résultats normalisés (`Decimal`→float, dates→ISO) et tronqués à
  `retrieval_max_rows`.
- `duckdb_source.py` — ce que DuckDB sait ouvrir. Les **fichiers** : CSV nativement
  (`read_csv_auto`), Excel lu par pandas/openpyxl puis chaque feuille enregistrée
  comme table DuckDB (une feuille = une table, jointures inter-feuilles possibles).
  Les **bases** (`.duckdb`) : ouvertes en lecture seule, avec leurs clés primaires
  ET étrangères introspectées via `duckdb_constraints()` — un schéma en étoile dont
  on tairait les FK obligerait le modèle à deviner les jointures. `read_only`
  n'est pas qu'une ceinture de plus : il évite le verrou exclusif, sans quoi l'API
  et un notebook ne pourraient pas ouvrir la même base. Aucune extension DuckDB à
  télécharger — compatible on-prem.
- Le catalogue peut attacher un **dictionnaire** (Markdown) à une source ; il est
  chargé dans le prompt système de l'agent SQL. Le DDL dit les types, le
  dictionnaire dit ce que les données veulent dire — et surtout ses pièges de
  modélisation, qui ne s'infèrent d'aucun schéma. Il passe *avant* la question, et
  non en réponse à un tool : un modèle qui apprend au 3e tour que le e-commerce est
  une ligne de `stores` a déjà rendu son classement des magasins.
- `agent.py` — l'agent text-to-SQL à **tools typés** (`list_tables`, `get_schema`,
  `run_sql`). Une erreur SQL revient au modèle en texte pour self-correction ;
  `UsageLimits` borne les allers-retours. Renvoie le SQL exécuté, le résultat, le
  résumé en français et la trace des tentatives.

### 4.5 `agents/analysis/` — capacité ② Analyse

`agent.py` : le LLM reçoit la question, la liste des fichiers montés sous `/data/`
et le contexte (schéma), et répond par un bloc de code Python (pandas, scipy,
statsmodels, prince, matplotlib…). Le code est exécuté dans la sandbox ; en cas
d'erreur, le traceback est renvoyé au modèle qui corrige — jusqu'à
`analysis_max_attempts`. Pour une source SQL, l'orchestrateur matérialise d'abord
chaque table en CSV (borné par `analysis_table_max_rows`) et les monte en lecture
seule. Les figures reviennent en `image/png` (base64) via le protocole MIME du
kernel.

### 4.6 `agents/inference/` — capacité ③ Inférence gardée

- `schemas/` — **la source de vérité** : un schéma Pydantic par dataset (bornes,
  valeurs autorisées, descriptions, `extra="forbid"`). Ajouter un dataset = 1 schéma
  + 1 artefact + 1 entrée de registre.
- `validation.py` — `validate_features()` accepte n'importe quel payload (dump
  partiel comme formulaire complet) et renvoie des anomalies **structurées** :
  `manquant`, `hors_bornes`, `valeur_non_autorisee`, `type_invalide`,
  `champ_inconnu` — plus la question de relance en français. **Pas de predict tant
  que ça ne valide pas.**
- `registry.py` — registre YAML (`models/registry.yaml`) : dataset → artefact
  joblib, tâche, libellés de classes, unité. Cache de chargement. Cible d'évolution :
  MLflow Model Registry, même interface.
- `predict.py` — predict **100 % déterministe, sans LLM** : classification → classe
  + libellé + probabilités ; régression → valeur + unité.

### 4.7 `sandbox/` — exécution durcie de code

- `image/` — le Dockerfile (python 3.12-slim + socle scientifique verrouillé par
  `uv pip compile`) et `bridge.py` : un pont stdio↔kernel Jupyter qui parle un
  protocole JSON ligne à ligne (`execute`/`ping` → `{status, stdout, results[{mime,
  data}], error}`). Le kernel donne les sorties riches (PNG matplotlib) via les
  messages MIME Jupyter standard.
- `client.py` — `SandboxSession` : lance `docker run` **durci** et dialogue avec le
  bridge. Timeout à deux étages : le bridge interrompt d'abord le kernel
  (l'exécution suivante reste possible) ; si le conteneur ne répond plus, l'hôte le
  tue (`sandbox_kill_grace`).

| Durcissement appliqué au `docker run` | Effet |
|---|---|
| `--network=none` | aucun accès réseau, même DNS |
| `--read-only` + `--tmpfs /tmp` | rootfs immuable, /tmp éphémère |
| `--cap-drop=ALL`, `--security-opt=no-new-privileges` | aucun privilège |
| `--memory`, `--cpus`, `--pids-limit` | quotas ressources |
| montages `-v …:ro` sous `/data/` | données en lecture seule |
| utilisateur non-root (uid 1000) | pas de root dans le conteneur |
| `--rm`, conteneur par session | rien ne persiste |

## 5. Sécurité — récapitulatif des garde-fous

1. **SQL** : lecture seule vérifiée *avant* exécution (première instruction
   `SELECT`/`WITH`, une seule instruction, mots-clés d'écriture refusés).
2. **Code généré** : jamais exécuté sur l'hôte — uniquement dans la sandbox du §4.7.
3. **Prédiction** : features validées par schéma strict (`extra="forbid"`), aucune
   valeur inventée, relance sinon.
4. **LLM** : boucles bornées partout (`retrieval_request_limit`,
   `analysis_max_attempts`) ; le planificateur ne choisit que dans les listes
   fournies.

## 6. Stratégie de tests

```
tests/
├── unit/          # rapide, sans Docker ni réseau : LLM scripté, sandbox doublée
├── integration/   # Docker : sandbox réelle, Postgres testcontainers, artefacts ML réels
├── e2e/           # les scénarios golden, du message à la réponse (LLM scripté)
├── fixtures/      # échantillon Maxizoo versionné (425 Ko) : la base réelle en miniature,
│                 #   choisi pour porter les 6 pièges du dictionnaire
└── helpers/       # ScriptedLLM (réponses par agent), doublures, mini-base + oracle Maxizoo
```

- Le **LLM est scripté** dans toute la suite (déterminisme, zéro réseau en CI) : le
  helper `ScriptedLLM` route des réponses préparées vers chaque agent via un
  marqueur de son prompt système. Le test « live » (`-m live`) parle au vrai Ollama,
  exclu par défaut.
- Le scénario golden n°1 est vérifié contre un **oracle pandas** calculé
  indépendamment du pipeline.
- CI GitHub Actions : lint (ruff) + suite complète avec build de l'image sandbox
  (cache buildx) — couverture exigée ≥ 85 %.

## 7. Configuration (`DAA_*`)

| Variable | Défaut | Rôle |
|---|---|---|
| `DAA_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | endpoint OpenAI-compatible d'Ollama |
| `DAA_LLM_MODEL` | `qwen3-coder:30b` | le modèle mutualisé |
| `DAA_LLM_TEMPERATURE` | `0.0` | déterminisme des générations |
| `DAA_CATALOG_PATH` | `sources/catalogue.yaml` | catalogue des sources |
| `DAA_RETRIEVAL_MAX_ROWS` | `200` | lignes max renvoyées par requête |
| `DAA_RETRIEVAL_REQUEST_LIMIT` | `10` | allers-retours LLM max (anti-boucle) |
| `DAA_ANALYSIS_MAX_ATTEMPTS` | `3` | essais de self-debug du code |
| `DAA_ANALYSIS_TABLE_MAX_ROWS` | `10000` | lignes matérialisées par table pour l'analyse |
| `DAA_MODELS_REGISTRY_PATH` | `models/registry.yaml` | registre des modèles ML |
| `DAA_SANDBOX_DOCKER_CMD` | `["docker"]` | commande docker (ex. `["wsl","docker"]`) |
| `DAA_SANDBOX_IMAGE` | `data-analyst-agent-sandbox:0.1` | image de la sandbox |
| `DAA_SANDBOX_MEM_LIMIT` / `_CPUS` / `_PIDS_LIMIT` | `1g` / `1.0` / `256` | quotas conteneur |
| `DAA_SANDBOX_START_TIMEOUT` / `_EXEC_TIMEOUT` / `_KILL_GRACE` | `60` / `30` / `10` s | délais sandbox |

## 8. Limites connues et pistes V2

- **Mémoire conversationnelle limitée au slot-filling** : le multi-tours couvre
  la complétion de features d'une prédiction (fusion, correction, digression) ;
  il ne couvre pas encore les références anaphoriques générales (« et pour les
  hommes ? » après une requête SQL).
- **Conversations stockées sur le disque local** (un dossier par fil) : simple et
  sans dépendance, mais lié à une instance — à externaliser si multi-instances.
  Ni purge ni quota : les fils s'accumulent jusqu'à suppression explicite, et la
  liste relit chaque `transcript.json` à l'affichage (suffisant pour les 10-20
  utilisateurs de la V1, à indexer au-delà).
- **Lot borné par `retrieval_max_rows`** : une prédiction en lot porte sur au plus
  `DAA_RETRIEVAL_MAX_ROWS` lignes (200 par défaut) ; au-delà, le résultat est
  tronqué et la réponse le signale.
- **Registry maison** → migration MLflow prévue (même interface).
- **Mémoire RAG de paires question→SQL validées** (idée retenue du
  [spike Vanna](spike-vanna.md)) pour améliorer les questions récurrentes.
- **Extension de la sandbox en prod** : prévoir un miroir PyPI local (l'image est
  figée par lockfile, rien ne s'installe au runtime).
