# data-analyst-agent

Agent conversationnel sur données, **on-premise**. À partir d'une source déclarée (fichier Excel/CSV ou base Postgres multi-tables), l'utilisateur pose une question en langage naturel et le système sait :

1. **Récupérer** — générer la requête SQL (jointures comprises) sur Postgres, ou interroger le fichier via DuckDB ;
2. **Analyser** — calculer KPI, statistiques (χ², ANOVA…) et visualisations en exécutant du code dans un bac à sable durci (réseau coupé) ;
3. **Prédire** — appeler un modèle de ML sur des features validées (Pydantic), en redemandant ce qui manque avant tout predict.

Réponse en langage naturel + objets affichables (tableau, figure). Un seul LLM mutualisé (Qwen3-Coder via Ollama), orchestration explicite et traçable.

Cahier des charges complet : [docs/CADRAGE.md](docs/CADRAGE.md). Consignes de contribution (humain ou agent) : [CLAUDE.md](CLAUDE.md).

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) (Python 3.12 géré automatiquement), **Docker** (sandbox d'exécution + tests d'intégration), et [Ollama](https://ollama.com) avec `qwen3-coder:30b` pour l'usage réel.

```bash
uv sync                                              # environnement + dépendances
uv run pytest                                        # suite de tests (couverture >= 85 %)
uv run uvicorn data_analyst_agent.api.app:app        # API + chat sur http://localhost:8000
```

Sous Windows, les tests nécessitant Docker (intégration, e2e) se lancent depuis WSL ; sans Docker ils sont automatiquement sautés. Le test live du LLM (`-m live`) est exclu par défaut.

L'image de la sandbox se construit une fois : `docker build -t data-analyst-agent-sandbox:0.1 src/data_analyst_agent/sandbox/image/` (sinon elle est construite au premier usage).

## Configuration

Tout se règle par variables d'environnement `DAA_*` (ou fichier `.env`) : modèle (`DAA_LLM_MODEL`), URL Ollama (`DAA_OLLAMA_BASE_URL`), quotas sandbox, chemins du catalogue et du registre — voir `src/data_analyst_agent/config.py`. Les sources de données se déclarent dans `sources/catalogue.yaml`.

## Observabilité

Chaque réponse embarque une trace typée par nœud du graphe (plan, capacité exécutée, synthèse, durées) — visible dans le JSON de `/chat` — et le serveur journalise chaque nœud (logger `data_analyst_agent.orchestrator`).

## Qualité

```bash
uv run ruff format           # formatage
uv run ruff check --fix      # lint
uv run pre-commit install    # hooks git (une seule fois)
```

Les tests marqués `live` (LLM local requis) sont exclus par défaut : `uv run pytest -m live` pour les lancer explicitement.

## Structure

```
src/data_analyst_agent/   # package (orchestrator, agents, sandbox, api)
docs/CADRAGE.md           # cahier des charges (architecture, roadmap, tests)
models/                   # artefacts ML jouets (Titanic, Iris, California Housing)
sources/                  # catalogue des sources + fichiers de test
notebooks/                # entraînement des modèles jouets
tests/                    # unit / integration / e2e (3 scénarios golden)
```
