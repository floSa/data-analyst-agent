# data-analyst-agent

Agent conversationnel sur données, **on-premise**. À partir d'une source déclarée (fichier Excel/CSV ou base Postgres multi-tables), l'utilisateur pose une question en langage naturel et le système sait :

1. **Récupérer** — générer la requête SQL (jointures comprises) sur Postgres, ou interroger le fichier via DuckDB ;
2. **Analyser** — calculer KPI, statistiques (χ², ANOVA…) et visualisations en exécutant du code dans un bac à sable durci (réseau coupé) ;
3. **Prédire** — appeler un modèle de ML sur des features validées (Pydantic), en redemandant ce qui manque avant tout predict.

Réponse en langage naturel + objets affichables (tableau, figure). Un seul LLM mutualisé, joint par un **endpoint OpenAI-compatible** — Ollama aujourd'hui, vLLM sans changer une ligne de code ; le service en place sert `gemma4:e4b`. Orchestration explicite et traçable, licences 100 % permissives (MIT/Apache/BSD).

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=uv&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-sandbox-2496ED?logo=docker&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)

## Sommaire

- [Architecture en un coup d'œil](#architecture-en-un-coup-dœil)
- [Deux branches durables, aux exigences opposées](#deux-branches-durables-aux-exigences-opposées)
- [Documentation](#documentation)
- [Démarrage](#démarrage)
- [Configuration](#configuration)
- [API / Endpoints](#api--endpoints)
- [Le début d'une conversation : choisir la source](#le-début-dune-conversation--choisir-la-source)
- [Ce que l'agent sait dire de lui-même](#ce-que-lagent-sait-dire-de-lui-même)
- [Mémoire de conversation](#mémoire-de-conversation)
- [Observabilité](#observabilité)
- [Qualité](#qualité)
- [Structure](#structure)
- [Licences & composants](#licences--composants)

## Architecture en un coup d'œil

```mermaid
flowchart LR
    U(["Utilisateur"]) --> API["API FastAPI<br/>+ page de chat"]
    API --> O["Orchestrateur LangGraph<br/>plan → route → capacité → synthèse"]
    O -.-> L["LLM mutualisé<br/>endpoint OpenAI-compatible"]
    O --> R["① Récupération<br/>text-to-SQL à tools"]
    O --> A["② Analyse<br/>code stats/viz"]
    O --> I["③ Inférence gardée<br/>validation → predict"]
    O --> Y["④ Répondre sur soi-même<br/>outils de faits, jamais de mémoire"]
    Y --> C[("Catalogue · registre ·<br/>schémas · ontologies")]
    R --> D[("Postgres ·<br/>CSV/Excel via DuckDB")]
    A --> S["Sandbox Docker<br/>réseau coupé"]
    I --> M[("Modèles ML<br/>registry joblib")]
```

Le fonctionnement détaillé (schéma fonctionnel du graphe, séquences, durcissement de la sandbox, explication service par service) est dans **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Deux branches durables, aux exigences opposées

**À lire avant de « corriger » quoi que ce soit sur l'une ou l'autre.** Ce dépôt
porte deux branches qui ne convergeront pas, et qui n'ont pas le même cahier des
charges :

| Branche | Ce qu'elle est | Authentification |
|---|---|---|
| **`main`** | le **socle produit** : la base sur laquelle plusieurs cas d'usage clients seront bâtis | **exigée** — hormis `/health`, aucune route n'est atteignable sans session, et chaque compte est cloisonné dans son dossier |
| **`Maxizoo`** | une **démonstration client**, qu'on ouvre à quelqu'un en lui envoyant un lien | **absente, et c'est un choix** — pas un retard, pas un oubli |

Une démonstration derrière un écran de connexion n'est plus une démonstration : il
faudrait créer un compte pour chaque personne à qui on la montre, et le premier
geste demandé à un prospect serait de taper un mot de passe. L'absence
d'authentification sur `Maxizoo` est donc **une décision de périmètre**, tenable
parce que la branche ne sert que des données de démonstration et ne vit que le temps
d'une présentation.

Concrètement :

- **ne pas porter l'authentification de `main` vers `Maxizoo`.** Si un durcissement
  de `main` touche `auth/`, il ne remonte pas — c'est le seul écart attendu entre
  les deux branches ;
- **ne pas déployer `Maxizoo` sur une adresse publique durable** ni y brancher de
  données réelles : c'est là, et seulement là, que l'absence de compte devient un
  vrai problème ;
- tout le reste — correctifs de sécurité SQL, plafonds de contexte, libération des
  ressources, découpages — vaut pour les deux et doit être reporté.

Le récapitulatif des garde-fous de
[ARCHITECTURE §5](docs/ARCHITECTURE.md#5-sécurité--récapitulatif-des-garde-fous)
décrit `main`. Sur `Maxizoo`, en retirer le point 1.

## Documentation

| Document | Contenu |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | schémas architectural et fonctionnel, description de chaque service, sécurité, configuration, stratégie de tests |
| [docs/CADRAGE.md](docs/CADRAGE.md) | cahier des charges : contraintes, décisions, stack, roadmap, arborescence, exigences de tests |
| [docs/AUDIT-2026-09.md](docs/AUDIT-2026-09.md) | état des lieux mesuré et backlog priorisé (multi-utilisateurs, mémoire, moteur LLM, sécurité, qualité) |
| [docs/spike-vanna.md](docs/spike-vanna.md) | spike text-to-SQL Vanna vs socle maison (verdict : socle maison conservé) |
| [docs/VLLM.md](docs/VLLM.md) | banc d'essai vLLM : le tool calling mesuré, ce qui casse sans les bonnes options, ce qui reste à vérifier |
| [docs/surface-conversationnelle.md](docs/surface-conversationnelle.md) | ce que l'agent sait répondre **sur lui-même** : la batterie de mesure, les comptes avant/après, le coût en appels LLM, et les décisions déjà mesurées et retirées |
| [docs/axes-amelioration.md](docs/axes-amelioration.md) | dette technique et chantiers ouverts, ancrés `fichier:ligne`, avec un récapitulatif priorisé |

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) (Python 3.12 géré automatiquement), **Docker** (sandbox d'exécution + tests d'intégration), et un serveur LLM à endpoint OpenAI-compatible pour l'usage réel — [Ollama](https://ollama.com) aujourd'hui, [vLLM](https://docs.vllm.ai) sans changer une ligne de code ([docs/VLLM.md](docs/VLLM.md)).

```bash
uv sync                                              # environnement + dépendances
uv run pytest                                        # suite de tests (couverture >= 85 %)
uv run playwright install chromium                   # une fois, pour les tests de la page
uv run pytest -m ui --no-cov                         # page de chat dans un vrai navigateur
uv run python scripts/manage_users.py create alice   # un compte (aucun n'existe au départ)
uv run uvicorn data_analyst_agent.api.app:app        # API + chat sur http://localhost:8000
```

**L'application est authentifiée** : hormis `/health`, aucune route n'est
atteignable sans session. Il n'y a ni inscription ouverte ni compte par défaut —
le premier compte se crée avec `scripts/manage_users.py` (le mot de passe est
demandé au terminal). En développement local, l'accès se fait en http : le
cookie de session étant `Secure` par défaut, il faut poser
`DAA_SESSION_COOKIE_SECURE=false` dans le `.env`, et rien d'autre.

Sous Windows, les tests nécessitant Docker (intégration, e2e) se lancent depuis WSL ; sans Docker ils sont automatiquement sautés. Le test live du LLM (`-m live`) est exclu par défaut.

**L'image de la sandbox se construit à la main, une fois**, et elle n'est *pas* construite au premier usage :

```bash
docker build -t data-analyst-agent-sandbox:0.1 src/data_analyst_agent/sandbox/image/
```

`ensure_image()` sait la construire, mais rien dans le chemin applicatif ne l'appelle — seuls les tests d'intégration et e2e s'en servent. Sans image, une analyse échoue au `docker run` ; elle ne déclenche pas de build.

## Configuration

Tout se règle par variables d'environnement `DAA_*` (ou fichier `.env`). Une quarantaine de réglages, groupés par domaine dans **[docs/ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-configuration-daa_)** : LLM, sources et capacités, mémoire de conversation et contexte, authentification et sessions, surface HTTP et débit, sandbox. Les plus souvent touchés :

| Réglage | Défaut | Quand y toucher |
|---|---|---|
| `DAA_LLM_BASE_URL` | `http://localhost:11434/v1` | changer de serveur LLM (Ollama → vLLM) |
| `DAA_LLM_MODEL` | `gemma4:e4b` | changer de modèle servi |
| `DAA_SESSION_COOKIE_SECURE` | `true` | `false` pour un développement local en http |
| `DAA_WORKSPACE_DIR` | `var/workspaces` | pointer un volume dédié en production |
| `DAA_API_DOCS_ENABLED` | `false` | `true` pour développer contre l'OpenAPI |
| `DAA_SANDBOX_MAX_SESSIONS` | `4` | régler sur la RAM réellement disponible |

Les sources de données se déclarent dans `sources/catalogue.yaml` (livré avec deux sources : `titanic` et `iris`).

### Sources livrées

| Source | Type | Contenu |
|---|---|---|
| `titanic` | Postgres | Base multi-tables `passengers` + `classes` (jointes par la clé étrangère `class_id`) — pour les jointures SQL. |
| `iris` | Fichier CSV | Dataset Iris (`sources/iris.csv`) : `sepal_length, sepal_width, petal_length, petal_width, species` — requêtable en SQL/stats/viz. |

**La source `titanic` requiert un Postgres lancé et seedé.** En local :

```bash
# 1. un Postgres jetable
docker run -d --name daa-postgres -p 5432:5432 \
  -e POSTGRES_PASSWORD=change-me postgres:16-alpine

# 2. les variables de connexion (défauts alignés sur .env.example)
export DAA_PG_HOST=localhost DAA_PG_PORT=5432 \
       DAA_PG_USER=postgres DAA_PG_PASSWORD=change-me

# 3. création de la base 'titanic' + schéma 2 tables + seed depuis le CSV
uv run python scripts/seed_titanic_postgres.py
```

La source `iris` ne demande aucun service (fichier local lu via DuckDB).

## API / Endpoints

Toutes les routes exigent une session valide, **sauf `/health`** — une sonde de
disponibilité n'en a pas, et lui refuser l'accès ferait passer le service pour
tombé. Sans session : `401` sur l'API, page de connexion en navigation. Les
routes qui modifient l'état exigent en plus l'en-tête `X-CSRF-Token`, repris du
cookie posé à la connexion.

**Chacun ne voit que ses conversations.** Les routes `/conversations…` et le
`conversation_id` accepté par `POST /chat` sont résolus sous le dossier de
l'utilisateur de la session, et il n'existe pas de vue plus large : le fil d'un
autre compte répond **`404`, jamais `403`** — un `403` confirmerait son
existence. Reprendre l'identifiant du fil de quelqu'un d'autre dans `POST /chat`
n'y écrit rien : cela ouvre un fil neuf et vide chez soi.

| Méthode | Route | Session | Rôle |
|---|---|---|---|
| `GET` | `/health` | non | Sonde de vie |
| `GET` | `/login` | non | Page de connexion (formulaire HTML, sans JavaScript) |
| `POST` | `/login` | non | Ouvre une session ; pose le cookie de session et le jeton anti-CSRF |
| `POST` | `/logout` | oui | Révoque la session **côté serveur** et efface les cookies |
| `GET` | `/me` | oui | Le compte de la session en cours (`{"login": …}`) |
| `POST` | `/chat` | oui | Question en langage naturel → réponse + artefacts + trace (contrat `ChatAnswer`) |
| `GET` | `/` | oui | Page de chat (rendu des PNG base64 et des tables JSON, zéro asset externe) |
| `GET` | `/conversations` | oui | **Ses** conversations, de la plus récente à la plus ancienne |
| `GET` | `/conversations/{id}` | oui | Le fil complet (messages + artefacts) pour le reprendre ; `404` s'il est à quelqu'un d'autre |
| `POST` | `/conversations/{id}/duplicate` | oui | Duplique une de ses conversations |
| `DELETE` | `/conversations/{id}` | oui | Supprime une de ses conversations et sa mémoire |

### Comptes et sessions

Les comptes vivent dans un fichier YAML non versionné (`var/users.yaml` par
défaut, 0600), mots de passe hachés en **argon2id**. Le fichier ne se peuple que
par le CLI — forme du fichier dans `users.example.yaml` :

```bash
uv run python scripts/manage_users.py create alice          # ouvre un compte
uv run python scripts/manage_users.py list                  # état des comptes
uv run python scripts/manage_users.py disable alice         # ferme le compte et ses sessions
uv run python scripts/manage_users.py reset-password alice  # nouveau mot de passe
```

Les sessions sont **côté serveur** : le navigateur ne reçoit qu'un identifiant
opaque, tout l'état est sur disque. C'est ce qui rend la déconnexion effective —
et ce qui permet à `disable` de couper immédiatement les onglets déjà ouverts.

**Le login est normalisé** à la création comme à la connexion : normalisation
unicode NFKC, espaces de bord retirés, casse repliée (`casefold`). `floSa`,
`FLOSA` et ` flosa ` désignent donc un seul et même compte — ce qui compte
d'autant plus que le login est aussi le nom du dossier où vivent ses
conversations. L'espace interne, le caractère de contrôle et le login de plus de
64 caractères sont refusés ; l'unicode, lui, est accepté.

## Le début d'une conversation : choisir la source

Le catalogue peut déclarer plusieurs sources. Plutôt que de laisser le planificateur en deviner une à chaque tour, l'agent **propose**, l'utilisateur **valide**, et c'est celle sur laquelle on travaille ensuite.

```
> bonjour, je voudrais regarder des données
  J'ai accès à 2 source(s) de données :
  - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes…)
  - **iris** (file) — Dataset Iris (fichier CSV local)…
  Donne-moi le nom de celle qui t'intéresse : je la garde pour la suite de la conversation.
  Sur laquelle veux-tu travailler ?

> titanic
  Entendu : on travaille sur **titanic** (postgres) — Base Titanic multi-tables…

> combien de lignes en tout ?          ← la source n'est plus nommée, et n'est plus devinée
  Il y a un total de 891 lignes dans la table des passagers.

> et dans iris, combien de lignes ?    ← une autre source nommée : on bascule, et on le dit
  Je passe sur la source `iris` — on travaillait sur `titanic`.
  La table `iris` contient un total de 150 lignes.
```

*(Transcription réelle, mesurée contre le serveur — cf. [docs/surface-conversationnelle.md](docs/surface-conversationnelle.md) §12.)*

Ce qu'il faut savoir de ce mécanisme :

- **S'il n'y a qu'une source, elle est annoncée** au lieu d'être demandée : « Je travaille sur la source `iris`. » Poser une question dont la réponse est déjà connue serait un tour perdu.
- **La proposition n'arrive que si une source est nécessaire.** « Prédis pour une passagère de 1re classe, 28 ans… » n'interroge aucune source : la question est répondue directement.
- **La validation ne coûte aucun appel LLM** : c'est le nom d'une source du catalogue, reconnu dans le message. Un message qui n'en nomme aucune n'est pas un choix et repart comme une question ordinaire — on ne reste pas coincé dans une question qu'on ne veut pas trancher.
- **Un message qui nomme une source ET pose une question est traité comme la question qu'il est** : « et dans iris, combien de lignes ? » lie la source *en chemin* et répond, au lieu de se contenter d'accuser réception.
- **La source est portée par la conversation** : elle se persiste dans `transcript.json` comme le propriétaire du fil, et la reprise d'un ancien fil la retrouve. Une conversation ouverte **avant** ce mécanisme fonctionne comme avant, sans migration.
- **Nommer une autre source la remplace, et l'agent le dit** : « Je passe sur la source `iris` — on travaillait sur `titanic`. » Le choix a été de basculer plutôt que de refuser ou de redemander : refuser obligerait à ouvrir un fil pour une question d'une ligne. Ce qui est dangereux n'est pas de changer de source, c'est de changer sans le dire. Une source choisie par le *planificateur*, elle, ne fait jamais basculer quoi que ce soit — seul le texte de l'utilisateur compte.
- Le champ `source` de `POST /chat` reste ce qu'il était : une source **imposée pour ce tour**, qui ne lie rien.

## Ce que l'agent sait dire de lui-même

« Quelles données as-tu ? », « c'est quoi ton périmètre ? », « quelles colonnes dans `passengers` ? », « de quels attributs as-tu besoin pour prédire ? », « que sais-tu faire ? » — ces questions ne portent pas *sur* les données mais **sur l'agent**, et elles sont le premier tour d'une conversation sur deux.

Le premier nœud du graphe leur est consacré, et c'est le **modèle** qui décide : il reçoit la question avec cinq outils qui rendent les faits du dépôt — catalogue, registre des modèles, schémas d'attributs, schéma réel des sources — puis il les formule. S'il n'appelle aucun outil, la question repart au planificateur comme n'importe quelle question sur les données.

**Rien ne vient de la mémoire du modèle**, et ce n'est pas une intention mais une vérification : une formulation qui cite un nom qu'aucun outil n'a rendu, ou qui oublie un nom qu'un outil a rendu, est **écartée** — ce sont alors les faits eux-mêmes qui partent à l'utilisateur. Un nom de table inventé est plus nocif qu'une réponse absente : il a l'air d'une lecture de la source.

La frontière : l'agent répond sur ce qu'il **EST**, jamais sur ce que les données **CONTIENNENT**. « Combien de lignes dans `passengers` ? » et « sur quelle période portent les données ? » sont des `COUNT` et des `MIN`/`MAX` : elles suivent le chemin SQL.

La mesure de cette surface — quarante formulations posées au vrai serveur, avant et après, avec le coût en appels LLM — est dans **[docs/surface-conversationnelle.md](docs/surface-conversationnelle.md)**.

## Mémoire de conversation

Chaque conversation (`conversation_id`) dispose d'un espace de travail qui **persiste les tableaux intermédiaires en CSV** (`DAA_WORKSPACE_DIR`). Aux tours suivants, ces objets sont réexposés : interrogeables comme des sources (« et pour les femmes ? »), réutilisables pour une prédiction (« prédis **ces** lignes ») et **montés dans la sandbox** pour que le code d'analyse généré les relise (`pd.read_csv('/data/resultat_1.csv')`).

Le **fil lui-même est persisté** au même endroit (`transcript.json`) : la barre latérale de la page de chat liste les conversations précédentes, on en rouvre une pour reprendre où on en était (figures et tableaux compris), on la duplique ou on la supprime. Comme une conversation est un simple dossier, la duplication emporte la mémoire ci-dessus — la copie sait encore « prédire ces lignes » — et la suppression ne laisse aucun CSV orphelin.

### Ce que l'agent se rappelle vraiment

Moins que ce que la persistance laisse croire, et il vaut mieux le savoir :

- **le transcript n'est jamais renvoyé au modèle** — il sert l'affichage ;
- **le contexte conversationnel ne retient qu'UN tour** (`context.json`) : la
  question précédente, l'action, la source, le code de figure, les features de
  la dernière prédiction réussie. Deux tours en arrière est déjà oublié ;
- ce qui remonte vraiment au modèle, c'est **la liste des tableaux
  intermédiaires** — et c'est elle, et elle seule, qui grossissait sans fin.

### Ce qui entre dans le contexte est plafonné

Réinjecter tous les tableaux à chaque tour n'avait aucune borne : mesuré à
100 tours, le `docker run` de l'analyse portait 100 arguments `--volume` et le
prompt du planificateur 13 348 caractères de catalogue d'objets. Au-delà de la
fenêtre du serveur, Ollama tronque **sans erreur ni message** — 48 350 tokens
envoyés pour 32 768 servis, et l'agent répond « je n'ai pas bien compris ».

Deux plafonds, appliqués **aux trois axes à la fois** (prompt du planificateur,
montages de la sandbox, catalogue des sources éphémères) — les désaccorder
donnerait un tableau décrit au modèle mais introuvable à l'exécution :

| Réglage | Défaut | Ce qu'il borne |
|---|---|---|
| `DAA_CONTEXT_ARTIFACT_WINDOW` | `8` | nombre de tableaux réinjectés, les plus récents (`0` = pas de fenêtre) |
| `DAA_CONTEXT_TOKEN_BUDGET` | `8000` | taille du prompt du planificateur, décomptée **avant** l'appel (`0` = pas de budget) |

**On plafonne ce qu'on injecte, pas ce qu'on conserve** : les tableaux évincés
restent sur le disque, dans le manifeste et dans le fil affiché. Au dépassement
du budget, ce sont les **plus anciens** qui sortent en premier — la dégradation
est ordonnée, pas subie.

### Quand du contexte est coupé, l'application le dit

C'est le corollaire : une mémoire plafonnée qui ne s'annonce pas est une
mémoire qui ment. La coupe apparaît dans la trace (`prompt_tokens`,
`server_prompt_tokens`, `truncated`, `truncation`) **et** dans la réponse rendue
— la trace n'est pas dépliée par défaut :

```
Il y a 150 lignes dans la table `iris`.

Contexte tronqué : 892 des 900 tableaux intermédiaires de cette conversation ne
sont plus transmis au modèle (fenêtre DAA_CONTEXT_ARTIFACT_WINDOW=8). Ils
restent enregistrés — le fil, lui, reste complet.
```

Quatre situations sont distinguées, et se cumulent :

1. **éviction délibérée** — la fenêtre ou le budget ont retiré des tableaux ;
2. **prompt plus long que la fenêtre du serveur** (`DAA_CONTEXT_MODEL_WINDOW`,
   `32768`) — constaté *avant* l'envoi, parce qu'au-delà le modèle ne rend plus
   de sortie exploitable et qu'il n'y aurait alors plus rien à mesurer ;
3. **troncature constatée côté serveur** — `prompt_eval_count` confronté à ce
   qu'on a envoyé. Le compteur de tokens local est approché (3 caractères par
   token) et **surestime** de 1 à 17 % (mesuré contre `gemma4:e4b`) : il coupe
   donc un peu trop tôt plutôt que trop tard ;
4. **table matérialisée coupée** — analyser une source SQL matérialise chaque table
   en CSV, plafonnée à `DAA_ANALYSIS_TABLE_MAX_ROWS`. Un CSV coupé reste
   parfaitement lisible : rien, dans le fichier, ne dit qu'il manque des lignes, et
   un agrégat calculé dessus est faux tout en se présentant comme juste. L'avis part
   donc à la fois dans le contexte du code généré et dans la réponse rendue.

Un serveur qui **refuse** au lieu de tronquer (vLLM répond `400 … maximum
context length`) est reconnu comme tel et dit en clair, au lieu de finir en
`ModelHTTPError` illisible.

Pour mesurer soi-même ce qu'une conversation injecte, tour après tour :

```bash
uv run python scripts/mesure_contexte.py --tours 1 10 30 100
```

### Arborescence

Tout est rangé **par utilisateur**, et c'est ce rangement qui cloisonne : une
route ne peut pas oublier un filtre qui n'existe pas, elle n'a jamais eu qu'une
racine sous les yeux. Les verrous suivent — ils sont posés à côté de la
ressource qu'ils sérialisent — ce qui cloisonne aussi la contention entre
comptes. Les dossiers sont créés en `0o700`.

```
$DAA_WORKSPACE_DIR/
└── <utilisateur>/            # login normalisé, encodé pour le système de fichiers
    ├── .locks/               # verrous de CET utilisateur
    └── <conversation_id>/
        ├── transcript.json   # le fil : messages, titre, propriétaire, prédiction en attente
        ├── manifest.json     # les tableaux intermédiaires mémorisés
        ├── context.json      # le tour précédent (pour résoudre un ajustement)
        └── resultat_*.csv    # les tableaux eux-mêmes
```

Les deux segments variables passent par le même encodage : tout octet hors
`[0-9A-Za-z_-]` devient `~XX`. Il est réversible, donc **sans collision** — deux
logins qui ne diffèrent que par la ponctuation ne peuvent pas se retrouver dans
le même dossier — et il ne peut produire ni `/` ni `.`, donc ni `..` ni chemin
absolu.

### Reprise d'un dossier antérieur au cloisonnement

Un `workspace_dir` où les conversations sont posées **à la racine** date d'avant
le rangement par utilisateur : l'application ne les y cherche plus. Un script
dédié les range, sans rien réécrire d'autre que leur propriétaire :

```bash
# à blanc (défaut) : montre ce qui serait fait, n'écrit rien
uv run python scripts/migrate_workspace_owner.py --workspace <dossier> --owner <login>
# pour de vrai
uv run python scripts/migrate_workspace_owner.py --workspace <dossier> --owner <login> --appliquer
```

Il est idempotent (relancé, il ne trouve plus rien), il refuse tout le lot
plutôt que d'écraser un fil en cas de collision de noms, et il estampille avant
de déplacer — une interruption laisse donc soit un fil encore à la racine, que
la relance reprendra, soit un fil rangé et lisible par son compte.

## Observabilité

Chaque réponse embarque une trace typée par nœud du graphe (plan, capacité exécutée, synthèse, durées) — visible dans le JSON de `/chat` — et le serveur journalise chaque nœud (logger `data_analyst_agent.orchestrator`). Le nœud `plan` y ajoute ce qu'a pesé le prompt (`prompt_tokens`), ce que le serveur dit en avoir lu (`server_prompt_tokens`) et ce qui a été coupé du contexte (`truncated`, `truncation`).

## Qualité

```bash
uv run ruff format           # formatage
uv run ruff check --fix      # lint
uv run pre-commit install    # hooks git (une seule fois)
```

Les tests marqués `live` (LLM local requis) sont exclus par défaut : `uv run pytest -m live` pour les lancer explicitement.

## Structure

```
src/data_analyst_agent/   # package
├── orchestrator/         # graphe, plan et ses règles, budget de contexte, mémoire des fils
├── agents/               # ① retrieval  ② analysis  ③ inference
│                         #   (④ « répondre sur soi-même » vit dans orchestrator/)
├── auth/                 # comptes argon2id, sessions côté serveur, anti-force brute
├── prompts/              # les 5 prompts système, hors du code (.txt)
├── sandbox/              # client durci + image/ (Dockerfile, bridge Jupyter)
└── api/                  # app.py (HTTP seul) + templates/ (chat, connexion)
docs/                     # ARCHITECTURE, CADRAGE, AUDIT, VLLM, spike-vanna, surface-conversationnelle
models/                   # artefacts ML jouets + registry.yaml (Titanic, Iris, California)
sources/                  # catalogue des sources + datasets vendorisés
scripts/                  # comptes, migration du workspace, seed Postgres, runners de mesure, bancs
notebooks/                # entraînement des modèles jouets (jupytext .md + .ipynb)
tests/                    # unit / integration / e2e golden / helpers / fakes / fixtures
var/                      # NON versionné : comptes, sessions, conversations (0o700)
```

L'arborescence détaillée, fichier par fichier, est dans [docs/CADRAGE.md §10](docs/CADRAGE.md).

---

## Licences & composants

| Composant | Rôle | Licence |
|---|---|---|
| DuckDB | Moteur SQL analytique | MIT |
| FastAPI | API | MIT |
| uvicorn | Serveur ASGI | BSD-3-Clause |
| LangGraph | Orchestration de l'agent | MIT |
| Pydantic / pydantic-ai | Typage & agent LLM | MIT |
| pydantic-settings | Lecture des réglages `DAA_*` et du `.env` | MIT |
| SQLAlchemy | Accès Postgres | MIT |
| pg8000 | Driver PostgreSQL | BSD-3-Clause |
| pandas | Manipulation de données | BSD-3-Clause |
| scikit-learn | Modèles de prédiction | BSD-3-Clause |
| joblib | Sérialisation des modèles | BSD-3-Clause |
| openpyxl | Lecture des classeurs Excel | MIT |
| PyYAML | Catalogue de sources, registre de modèles, comptes | MIT |
| argon2-cffi | Empreintes de mots de passe (argon2id) | MIT |
| python-multipart | Lecture du formulaire de connexion | Apache-2.0 |
| Ollama | Serveur du LLM mutualisé, local | MIT |
| `gemma4:e4b` | Modèle servi par l'instance en place | Apache-2.0 — licence **déclarée par le modèle lui-même** (`POST /api/show`), à revérifier si le modèle servi change |
| **Ce projet** | Code applicatif | MIT annoncé, **mais aucun fichier `LICENSE` n'est présent** et `pyproject.toml` ne déclare rien : l'annonce est donc sans portée juridique en l'état (cf. [axes-amelioration](docs/axes-amelioration.md)) |
