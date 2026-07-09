# CLAUDE.md — data-analyst-agent

Consignes pour toute session IA travaillant sur ce repo. **Lis `docs/CADRAGE.md` en premier** : c'est le cahier des charges complet (pitch, architecture, décisions figées, roadmap, stratégie de tests).

## Contexte en une phrase

`data-analyst-agent` = agent conversationnel sur données, **on-premise et commercialisable**, qui à partir d'une source (Excel ou base Postgres multi-tables jointes) répond à des questions du type : prédiction ML, requête/stat (« % de femmes de 1ʳᵉ classe ayant survécu »), ou visualisation (« bar chart »). Récupère (SQL/Excel), analyse (auto-stats + viz dans un sandbox), prédit (ML gardé) ; orchestré par un pipeline explicite avec un seul LLM mutualisé (Qwen3-Coder via Ollama).

## Règles NON négociables

1. **Licences** : produit destiné à être commercialisé. **Toute dépendance ajoutée doit être MIT / Apache-2.0 / BSD** (ou équivalent permissif). **Interdit : GPL, AGPL, non-commercial.** Vérifie la licence AVANT d'ajouter une lib ; en cas de doute, demande. (`pingouin` est GPL → interdit ; utiliser scipy + statsmodels.)
2. **On-prem** : aucune dépendance à un service cloud obligatoire. La sandbox tourne **réseau coupé** ; pas d'appel internet au runtime.
3. **Un seul LLM mutualisé** : Qwen3-Coder (Ollama). Ne pas introduire un second modèle langage sans validation explicite.
4. **Suivre l'ordre de la roadmap** (CADRAGE §11) ; valider ET tester chaque étape avant la suivante. Ne pas coder à moitié une étape puis sauter à une autre.
5. **Tests** : rien n'est « fait » sans tests verts (voir section Tests). La solution **n'est pas présentable** tant que la suite complète ne passe pas.

## Tests (exigence forte du propriétaire)

L'utilisateur veut **un maximum de tests avant toute présentation**. Applique strictement la §12 du CADRAGE :

- **Chaque brique livrée avec ses tests** : unitaires (LLM/DB mockés), intégration (sandbox réelle, Postgres via `testcontainers`, DuckDB sur Excel), end-to-end.
- **3 scénarios golden e2e obligatoires et verts** avant présentation :
  1. « % de femmes de 1ʳᵉ classe survivantes » → valeur correcte vs calcul pandas de référence ;
  2. « bar chart de la survie par classe » → objet `image/png` non vide ;
  3. « prédiction pour ce passager … » → classe + probabilité cohérentes ; **+ cas features incomplètes → le système redemande, pas de predict**.
- **Chemins d'erreur testés** : SQL invalide → self-correction ; code sandbox qui plante → self-debug ; timeout.
- **CI GitHub Actions** : `ruff format --check` + `ruff check` + `pytest` + **couverture ≥ 85 %** (`pytest-cov`), la CI échoue en dessous.
- Tests **déterministes** : pas d'appel LLM réseau en CI (mock/enregistrement) ; test « live LLM » marqué `@pytest.mark.live`, exclu par défaut.
- Écris le test **en même temps** que le code (TDD quand c'est naturel), pas après coup.

## Python & outillage

- **`uv` pour tout** : `uv venv`, `uv sync`, `uv run <cmd>`. Jamais `pip`/`.venv/bin/python` en direct. Source de vérité des deps = `pyproject.toml` + `uv.lock` (committer le lock).
- **Python 3.12** (`uv python install 3.12`, `uv venv --python 3.12`).
- Package importable : **`data_analyst_agent`** (le repo/dossier a un tiret, le package un underscore).
- **`ruff`** format ET lint (pas de black/isort/flake8) : `ruff format` + `ruff check --fix`.
- **Typage strict** partout. **Pydantic v2** pour modèles de données et settings (`pydantic-settings`).
- **`pytest`** (+ `pytest-cov`, `testcontainers`).
- **Docker** : dans les images, `pip` toléré, mais préférer `uv pip install`/`uv sync` avec lockfile (vitesse, déterminisme).
- Style : **clarté avant cleverness**. Noms explicites, commentaires utiles seulement.

## Conventions Git

- **Commits granulaires** : un par livrable cohérent (un module, une étape). Précéder si besoin d'un `chore` (deps, config) ; clôturer par un `docs` si pertinent. **Pas de gros commit fourre-tout.**
- **Proposer les commits au fil de l'eau**, à la fin de chaque étape vérifiée **et testée (tests verts)** — sans attendre qu'on le demande. Jamais committer du code cassé ou non testé.
- **Messages en français**, clairs.
- **AUCUNE mention d'IA nommée** dans commits, PR, issues, branches, tags : jamais les mots `Claude` ni `Anthropic` (ni variantes), **jamais** de `Co-Authored-By: Claude ...`. Une allusion vague à un outil automatisé est tolérée ; nommer un modèle ne l'est pas.
- **Identité des commits** (forcer via flags, ne pas se fier au `git config` local) :
  ```
  git -c user.name="floSa" -c user.email="florian.horellou@gmail.com" commit -m "..."
  ```
  Mode « pro » seulement si demandé explicitement : `Florian H <florian.horellou@aosis.net>`.
- **Branches** : `feat/...`, `fix/...`, `chore/...`, `docs/...`. Une branche `claude/...` d'un worktree doit être renommée avant tout push (ne pousser que le nom propre).
- **Jamais de commit/push hors demande.** Jamais de `--force`, `rebase` ou réécriture d'historique sans accord explicite.
- Avant un push : vérifier l'identité (`git log --format="%an <%ae>"`) — un seul contributeur attendu (floSa/gmail).

## Vérification

- Avant de déclarer une étape « faite » : l'exécuter réellement (lancer le code / les tests), pas seulement typer/compiler. Rapporter fidèlement : un test rouge se dit, avec la sortie.
- Sandbox et inférence : tester le chemin de bout en bout (donnée → validation → résultat), pas juste les fonctions isolées.

## En cas de doute

Demander. Ne pas inventer une licence, un choix d'archi non tranché, ou une convention. Les 3 points ouverts (text-to-SQL, Excel, orchestration — CADRAGE §9) doivent être confirmés avant d'être implémentés.
