# Développement

Pour travailler sur le code. Pour installer le service, voir [INSTALLATION.md](INSTALLATION.md).

## Démarrer un poste de développement

**Pour installer le service**, ce n'est pas ici : c'est
**[INSTALLATION.md](INSTALLATION.md)**, qui part d'une machine nue et
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
**[ARCHITECTURE §7](ARCHITECTURE.md#7-configuration-daa_)**.

## Qualité du code

```bash
uv run ruff format           # formatage
uv run ruff check --fix      # lint
uv run pre-commit install    # hooks git (une seule fois)
```
