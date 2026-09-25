# Développement

Ce document décrit la mise en place d'un poste de développement pour data-analyst-agent. L'installation en service est décrite dans [INSTALLATION.md](INSTALLATION.md).

| Section | Contenu |
|---|---|
| [Prérequis](#prérequis) | Outils nécessaires |
| [Démarrage](#démarrage) | De l'installation à la première page |
| [Points d'attention](#points-dattention) | Bac à sable, authentification, sources, tests |
| [Configuration](#configuration) | Variables `DAA_*` |
| [Qualité du code](#qualité-du-code) | Formatage, analyse, hooks |

## Prérequis

- [uv](https://docs.astral.sh/uv/) ; Python 3.12 est installé par uv.
- Docker : bac à sable et tests d'intégration.
- Un LLM servi derrière une API compatible OpenAI (`/v1/chat/completions`) avec appel d'outils, désigné par `DAA_LLM_BASE_URL`.

## Démarrage

```bash
uv sync                                              # environnement et dépendances
docker build -t data-analyst-agent-sandbox:0.1 src/data_analyst_agent/sandbox/image/
uv run pytest                                        # suite de tests, couverture ≥ 85 %
uv run python scripts/manage_users.py create alice   # premier compte
uv run uvicorn data_analyst_agent.api.app:app        # API et chat sur http://localhost:8000
```

## Points d'attention

- **Image du bac à sable** : elle se construit une fois, à la main. Sans elle, toute analyse échoue.
- **Authentification** : aucun compte par défaut. En HTTP local, poser `DAA_SESSION_COOKIE_SECURE=false` dans le `.env`.
- **Source `titanic`** : elle nécessite un Postgres lancé et alimenté (`uv run python scripts/seed_titanic_postgres.py`). La source `iris` ne nécessite rien.
- **Tests** : sans Docker, les tests qui en dépendent sont ignorés. Les tests `-m live` (LLM requis) et `-m ui` (navigateur, après `uv run playwright install chromium`) sont exclus par défaut. Sous Windows, les tests Docker se lancent depuis WSL.

## Configuration

Les réglages passent par des variables d'environnement `DAA_*` ou un fichier `.env`.
La liste complète, par domaine : [ARCHITECTURE.md](ARCHITECTURE.md#7-configuration-daa_).

## Qualité du code

```bash
uv run ruff format           # formatage
uv run ruff check --fix      # analyse statique
uv run pre-commit install    # hooks git, une seule fois
```
