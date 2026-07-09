# data-analyst-agent

Agent conversationnel sur données, **on-premise**. À partir d'une source déclarée (fichier Excel/CSV ou base Postgres multi-tables), l'utilisateur pose une question en langage naturel et le système sait :

1. **Récupérer** — générer la requête SQL (jointures comprises) sur Postgres, ou interroger le fichier via DuckDB ;
2. **Analyser** — calculer KPI, statistiques (χ², ANOVA…) et visualisations en exécutant du code dans un bac à sable durci (réseau coupé) ;
3. **Prédire** — appeler un modèle de ML sur des features validées (Pydantic), en redemandant ce qui manque avant tout predict.

Réponse en langage naturel + objets affichables (tableau, figure). Un seul LLM mutualisé (Qwen3-Coder via Ollama), orchestration explicite et traçable.

Cahier des charges complet : [docs/CADRAGE.md](docs/CADRAGE.md). Consignes de contribution (humain ou agent) : [CLAUDE.md](CLAUDE.md).

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) — Python 3.12 est installé et géré automatiquement.

```bash
uv sync            # crée l'environnement et installe les dépendances
uv run pytest      # lance la suite de tests (couverture exigée >= 85 %)
```

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
