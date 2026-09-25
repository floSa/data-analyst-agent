# data-analyst-agent

Agent conversationnel sur données, **on-premise**. On déclare une source — un CSV,
un classeur Excel, une base Postgres, une base DuckDB — on pose sa question en
français, et le système sait :

1. **Récupérer** — écrire le SQL, jointures comprises, et l'exécuter en lecture seule ;
2. **Analyser** — écrire du Python (KPI, statistiques, figures) et l'exécuter dans un
   bac à sable Docker sans réseau ;
3. **Prédire** — appeler un modèle de ML sur des features validées, en réclamant ce
   qui manque avant tout `predict`.

Il sait aussi répondre **sur lui-même** (« quelles données as-tu ? ») et **reprendre
ce qu'il a produit** (« reprends le graphe et mets les barres en bleu »).

Un seul modèle de langage, joint par un **endpoint OpenAI-compatible**, servi
localement : rien ne sort de la machine. Le moteur n'est nommé nulle part dans le
code — il se change en changeant une URL. En service : **vLLM**, servant
`google/gemma-4-E4B-it-qat-w4a16-ct` ([docs/MOTEUR.md](docs/MOTEUR.md)).

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=uv&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-sandbox-2496ED?logo=docker&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)

## Je veux… → je lis…

| Si vous voulez… | Lisez |
|---|---|
| **savoir ce que le produit fait et ne fait pas** — c'est l'entrée | [docs/LIVRAISON.md](docs/LIVRAISON.md) |
| **vous en servir** : poser des questions dans la page de chat | [docs/GUIDE-UTILISATEUR.md](docs/GUIDE-UTILISATEUR.md) |
| **brancher vos données** : déclarer une source, la vérifier | [docs/AJOUTER-UNE-SOURCE.md](docs/AJOUTER-UNE-SOURCE.md) |
| écrire le **dictionnaire** d'une source pour qu'il tienne devant l'agent | [docs/rediger-un-dictionnaire-de-source.md](docs/rediger-un-dictionnaire-de-source.md) |
| **installer** le service sur une machine nue | [docs/INSTALLATION.md](docs/INSTALLATION.md) |
| **exploiter** : commander, exposer en HTTPS, sauvegarder, restaurer | [docs/EXPLOITATION.md](docs/EXPLOITATION.md) |
| **comprendre comment c'est construit** — schémas, service par service, réglages, sécurité | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| comprendre **comment il répond**, tour par tour | [docs/parcours-de-l-agent.md](docs/parcours-de-l-agent.md) |
| connaître le **moteur** : ce dont le système dépend, et ce qui casse sans | [docs/MOTEUR.md](docs/MOTEUR.md) |
| les **chiffres du jour de livraison**, campagne par campagne | [docs/releve-de-livraison.md](docs/releve-de-livraison.md) |
| savoir **ce qui reste à faire**, ancré `fichier:ligne` | [docs/axes-amelioration.md](docs/axes-amelioration.md) |
| lancer une **campagne de mesure** ou un script | [scripts/README.md](scripts/README.md) |
| savoir **pourquoi c'est comme ça** — les journaux de chantier | [docs/historique/README.md](docs/historique/README.md) |

## Démarrage

**Pour installer le service**, ce n'est pas ici : c'est
**[docs/INSTALLATION.md](docs/INSTALLATION.md)**, qui part d'une machine nue et
n'exige que Docker. Ce qui suit est le démarrage d'un poste de **développement**.

Prérequis : [uv](https://docs.astral.sh/uv/) (Python 3.12 géré automatiquement),
**Docker** (bac à sable et tests d'intégration), et un serveur LLM à endpoint
OpenAI-compatible pour l'usage réel — `DAA_LLM_BASE_URL` suffit à en désigner un,
pourvu qu'il serve `/v1/chat/completions` avec le *tool calling*.

```bash
uv sync                                              # environnement + dépendances
docker build -t data-analyst-agent-sandbox:0.1 src/data_analyst_agent/sandbox/image/
uv run pytest                                        # suite de tests (couverture ≥ 85 %)
uv run python scripts/manage_users.py create alice   # un compte (aucun n'existe au départ)
uv run uvicorn data_analyst_agent.api.app:app        # API + chat sur http://localhost:8000
```

Quatre choses à savoir avant la première question :

- **l'image du bac à sable se construit à la main, une fois** (2ᵉ commande). Rien
  dans le chemin applicatif ne la construit : sans elle, une analyse échoue au
  `docker run` ;
- **l'application est authentifiée.** Ni inscription ouverte, ni compte par défaut.
  En local, l'accès se fait en http : le cookie de session étant `Secure` par
  défaut, il faut poser `DAA_SESSION_COOKIE_SECURE=false` dans le `.env` ;
- **la source `titanic` du catalogue livré demande un Postgres** lancé et semé —
  `uv run python scripts/seed_titanic_postgres.py`, défauts alignés sur
  `.env.example`. La source `iris` ne demande rien ;
- sous Windows, les tests qui exigent Docker se lancent depuis WSL ; sans Docker
  ils sont sautés. Les tests `-m live` (LLM requis) et `-m ui` (navigateur, après
  `uv run playwright install chromium`) sont exclus par défaut.

Tout se règle par variables d'environnement `DAA_*` (ou un `.env`). Les 51 réglages
sont groupés par domaine dans
**[ARCHITECTURE §7](docs/ARCHITECTURE.md#7-configuration-daa_)**.

## Qualité

```bash
uv run ruff format           # formatage
uv run ruff check --fix      # lint
uv run pre-commit install    # hooks git (une seule fois)
```

## Deux branches, et elles ne convergeront pas

**`main`** est le socle produit, **authentifié**. **`Maxizoo`** est une démonstration
client, **sans authentification — et c'est un choix de périmètre**, pas un oubli.

Ne pas porter `auth/` de l'une vers l'autre ; ne pas déployer `Maxizoo` sur une
adresse publique durable ni y brancher de données réelles. Tout le reste vaut pour
les deux et doit être reporté. Le détail, et ce qui l'a décidé :
[ARCHITECTURE §5](docs/ARCHITECTURE.md#5-sécurité--récapitulatif-des-garde-fous).

## Structure

```
src/data_analyst_agent/   # le package
├── orchestrator/         # graphe, plan et ses règles, budget de contexte, mémoire des fils
├── agents/               # ① retrieval  ② analysis  ③ inference
├── auth/                 # comptes argon2id, sessions côté serveur, anti-force brute
├── prompts/              # les 7 prompts système, hors du code (.txt)
├── sandbox/              # client durci + image/ (Dockerfile, bridge Jupyter)
└── api/                  # app.py (HTTP seul) + templates/ (chat, connexion)
deploy/                   # image de l'app, compose, unité systemd, daactl, sauvegarde, TLS
docs/                     # la documentation — voir la table ci-dessus
├── historique/           #   les journaux de chantier
models/                   # artefacts ML jouets + registry.yaml
sources/                  # catalogue des sources + datasets vendorisés
scripts/                  # comptes, migration, semis, campagnes de mesure, bancs
notebooks/                # entraînement des modèles jouets (jupytext .md + .ipynb)
tests/                    # unit / integration / e2e golden / helpers / fakes / catalogues
var/                      # NON versionné : comptes, sessions, conversations (0o700)
```

L'arborescence détaillée, fichier par fichier, est dans
[docs/CADRAGE.md §10](docs/CADRAGE.md).

## Licences

**Composants logiciels sous licences 100 % permissives** (MIT / Apache-2.0 / BSD) —
le tableau composant par composant est dans
[ARCHITECTURE §9](docs/ARCHITECTURE.md#9-licences--composants). Les **poids du
modèle** relèvent, eux, de la licence de son éditeur, qui n'est pas lisible depuis
l'application et doit être relue à chaque changement de modèle servi.

Le code applicatif est annoncé MIT, **mais aucun fichier `LICENSE` n'est présent**
et `pyproject.toml` ne déclare rien : l'annonce est sans portée juridique en l'état
(cf. [axes-amelioration](docs/axes-amelioration.md)).
