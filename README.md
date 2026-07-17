# data-analyst-agent

Agent conversationnel sur données, **on-premise**. À partir d'une source déclarée (fichier Excel/CSV, base DuckDB ou base Postgres multi-tables), l'utilisateur pose une question en langage naturel et le système sait :

1. **Récupérer** — générer la requête SQL (jointures comprises) sur Postgres, ou interroger le fichier/la base via DuckDB ;
2. **Analyser** — calculer KPI, statistiques (χ², ANOVA…) et visualisations en exécutant du code dans un bac à sable durci (réseau coupé) ;
3. **Prédire** — appeler un modèle de ML sur des features validées (Pydantic), en redemandant ce qui manque avant tout predict.

Réponse en langage naturel + objets affichables (tableau, figure). Un seul LLM mutualisé (Qwen3-Coder via Ollama), orchestration explicite et traçable, licences 100 % permissives (MIT/Apache/BSD).

La base de démonstration livrée est un **jeu retail animalerie** (1,66 M de lignes, données synthétiques) conçu pour ce genre d'agent : jointures non triviales, pièges de modélisation documentés, et 13 questions dont on connaît les vraies réponses — de quoi mesurer si l'agent a raison, et pas seulement s'il en a l'air.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=uv&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-sandbox-2496ED?logo=docker&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
![FastAPI](https://img.shields.io/badge/FastAPI-0.139-009688?logo=fastapi&logoColor=white)

## Architecture en un coup d'œil

```mermaid
flowchart LR
    U(["Utilisateur"]) --> API["API FastAPI<br/>+ page de chat"]
    API --> O["Orchestrateur LangGraph<br/>plan → route → capacité → synthèse"]
    O -.-> L["LLM mutualisé<br/>Qwen3-Coder / Ollama"]
    O --> R["① Récupération<br/>text-to-SQL à tools"]
    O --> A["② Analyse<br/>code stats/viz"]
    O --> I["③ Inférence gardée<br/>validation → predict"]
    R --> D[("Postgres ·<br/>DuckDB · CSV/Excel")]
    A --> S["Sandbox Docker<br/>réseau coupé"]
    I --> M[("Modèles ML<br/>registry joblib")]
```

Le fonctionnement détaillé (schéma fonctionnel du graphe, séquences, durcissement de la sandbox, explication service par service) est dans **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Documentation

| Document | Contenu |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | schémas architectural et fonctionnel, description de chaque service, sécurité, configuration, stratégie de tests |
| [docs/CADRAGE.md](docs/CADRAGE.md) | cahier des charges : contraintes, décisions, stack, roadmap, exigences de tests |
| [docs/spike-vanna.md](docs/spike-vanna.md) | spike text-to-SQL Vanna vs socle maison (verdict : socle maison conservé) |

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) (Python 3.12 géré automatiquement), **Docker** (sandbox d'exécution + tests d'intégration), et [Ollama](https://ollama.com) avec `qwen3-coder:30b` pour l'usage réel.

```bash
uv sync                                              # environnement + dépendances
uv run pytest                                        # suite de tests (couverture >= 85 %)
uv run playwright install chromium                   # une fois, pour les tests de la page
uv run pytest -m ui --no-cov                         # page de chat dans un vrai navigateur
uv run uvicorn data_analyst_agent.api.app:app        # API + chat sur http://localhost:8000
```

Sous Windows, les tests nécessitant Docker (intégration, e2e) se lancent depuis WSL ; sans Docker ils sont automatiquement sautés. Le test live du LLM (`-m live`) est exclu par défaut.

L'image de la sandbox se construit une fois : `docker build -t data-analyst-agent-sandbox:0.1 src/data_analyst_agent/sandbox/image/` (sinon elle est construite au premier usage).

## Configuration

Tout se règle par variables d'environnement `DAA_*` (ou fichier `.env`) : modèle (`DAA_LLM_MODEL`), URL Ollama (`DAA_OLLAMA_BASE_URL`), quotas sandbox, chemins du catalogue et du registre — tableau complet dans [docs/ARCHITECTURE.md §7](docs/ARCHITECTURE.md). Les sources de données se déclarent dans `sources/catalogue.yaml` (livré avec une source : `maxizoo`).

### La base de démonstration

| Source | Type | Contenu |
|---|---|---|
| `maxizoo` | Base DuckDB | Retail animalerie (données **synthétiques**) — schéma en étoile, 1,66 M de lignes, 10 tables : `sales_daily` (ventes au grain magasin × SKU × jour) entourée de `stores`, `products`, `promo_calendar`/`promo_scope`, plus `traffic_daily`, `sales_hourly`, `weather`, `inflation`, `store_hours`. Historique du 2021-07-01 au 2026-06-30. |

**La base n'est pas versionnée** (180 Mo) : elle vit dans un dépôt d'export dédié et se construit en une commande.

```bash
# 1. récupérer l'export (branche orpheline : la base et sa doc, rien d'autre)
git clone --branch export-db --single-branch \
    https://github.com/floSa/sales-ops-planning-poc.git ../base_demo

# 2. construire sources/maxizoo.duckdb + recopier le dictionnaire
uv run python scripts/load_maxizoo_duckdb.py --export ../base_demo
```

Le script charge le DDL et les 1,66 M de lignes contraintes actives (PK, FK, CHECK), et **refuse d'écrire** si un volume ne tombe pas juste : une base à moitié chargée ferait répondre des chiffres faux avec aplomb.

#### Le dictionnaire, et pourquoi il compte

`sources/maxizoo_dictionnaire.md` (recopié par le script) est **chargé dans le contexte de l'agent SQL** à chaque question. Il décrit chaque colonne, mais surtout **6 pièges de modélisation** qui ne s'infèrent d'aucun DDL et qui font écrire du SQL plausible et faux :

1. **Le e-commerce est un magasin** — `ONLINE` est une ligne de `stores` et pèse ~20 % du CA ; toute requête « par magasin » l'inclut.
2. **`quantity = 0` est une vraie ligne** — 46 % des lignes ; un jour sans vente, pas une donnée manquante.
3. **Absence de ligne ≠ zéro** — 4 SKU sont lancés en cours d'historique (cold start).
4. **`revenue` est le CA réalisé, pas la demande** — en rupture (`is_rupture = 1`), la vente est censurée.
5. **Une campagne sans `promo_scope` porte sur tout le catalogue** — un `JOIN` la fait disparaître en silence.
6. **La base contient du futur** — météo et promos vont jusqu'au 2026-12-31, les ventes s'arrêtent au 2026-06-30.

Le dépôt d'export livre aussi `questions_reference.md` : 13 questions en langage naturel dont **les réponses ont été vérifiées** en exécutant le SQL hors de tout agent. C'est le jeu d'évaluation — voir « Ce que l'agent sait faire, et où il échoue » ci-dessous.

#### Servir la même base par Postgres

Le catalogue accepte aussi bien un DSN Postgres (`type: postgres`) qu'un fichier DuckDB. Le DDL de l'export est du PostgreSQL standard : `psql -d poc_retail -f ../base_demo/schema.sql`, puis `../base_demo/chargement/postgres.sql`.

## Ce que l'agent sait faire, et où il échoue

Les 13 questions de référence ont été passées à l'agent réel (`gemma4:e4b`, dictionnaire en contexte, base complète), et comparées aux réponses vérifiées de l'export. **7 sur 13** au contrôle automatique strict, 8 à 10 selon la sévérité — et ce score, obtenu sur des questions **isolées**, est le plus flatteur des deux : en conversation en cascade, c'est 10 invariants violés sur 15 tours (voir plus bas). Le détail vaut mieux que le score :

| Ce qui passe | Ce qui casse |
|---|---|
| Agrégats et jointures (Q1, Q3, Q6) — au centime près | **Q5, le panier article** : l'agent joint `traffic_daily` à `sales_daily` sans agréger d'abord au grain magasin × jour. `nb_tickets` est dupliqué une fois par SKU, et le panier ressort à **0,047 au lieu de 2,76** — exactement 60 fois trop petit. |
| **Q2, le piège du e-commerce** : `Canal Online` sort bien en tête du CA par magasin | **Q10, l'uplift promo** : la requête à trois CTE (pendant vs 4 semaines avant) dépasse le modèle. |
| **Q9, le cold start** : les 4 SKU lancés en cours d'historique, avec les bonnes dates | **Q13, la cohérence des grains** : l'agent somme les deux grains au lieu de comparer leur écart maximum. |
| **Q11, l'effet météo** : raisonne sur `temp_anomaly` et non sur la température absolue | Q8 : trouve les 3 041 lignes en rupture, mais pas le pourcentage ni le CA associés. |

**Ce que ça dit.** Le dictionnaire en contexte fait gagner les pièges qu'il **énonce** (e-commerce, cold start, anomalie de température) : ce sont précisément les questions où un agent sans dictionnaire répond du plausible et du faux. Il ne sauve pas les pièges qu'il ne fait que **sous-entendre** : le piège de grain de Q5 n'est pas dans la liste des 6, et le dictionnaire se contente d'y donner l'ordre de grandeur attendu (~2,1 à 2,8). Le modèle rend 0,047 sans sourciller.

Ajouter au prompt une consigne de relecture (« si tu es loin de l'ordre de grandeur documenté, ta requête est fausse ») a été **essayé et n'a rien changé** : même requête naïve, même résultat, aucune auto-correction en deux essais. La consigne a donc été retirée plutôt que gardée pour la forme. Le levier est ailleurs — un modèle plus fort, ou le piège de grain écrit noir sur blanc dans le dictionnaire.

### En conversation réelle, c'est plus dur

Les 13 questions ci-dessus sont posées **isolément**. Passées en conversation en cascade sur le système entier (API + sandbox + modèle), les mêmes capacités décrochent davantage : **15 tours joués, 10 invariants de données violés**. Trois familles d'échecs, toutes reproductibles :

**1. Les questions sur le système, pas sur les données.** « Peux-tu me décrire la base : quelles tables ? » et « de quels attributs as-tu besoin pour une prévision ? » échouent toutes deux sur « Je n'ai pas bien compris ta demande ». Le planificateur n'a pas de route pour une question *méta* : il attend une question sur les données, et le schéma comme la liste des features lui sont pourtant déjà fournis dans son prompt. C'est le premier tour de deux conversations sur trois — la pire place pour un échec.

**2. L'anaphore vers une figure est instable.** « Fais-moi un diagramme en barres de ces CA » et « montre-moi ça en barres groupées » passent ; « fais-en un diagramme en barres » est routé en `query` et rend un tableau. Même intention, trois formulations, deux réussites — le routage tient à la tournure.

**3. Les questions à deux dimensions perdent la seconde.** « Comment se répartit le CA par univers ? » rend les montants mais pas les parts. « Quel jour vend le mieux, en magasin **et en ligne** ? » agrège les deux canaux au lieu de les séparer — et écrase justement le signal recherché (samedi en magasin, dimanche en ligne).

**Ce qui marche bien, en revanche :** le slot-filling. « Prédis les ventes de croquettes chien en grand magasin » → relance sur les features manquantes → complément → prédiction → « et si le produit était en promo à −30 % ? » → ajustement correct. Quatre tours sur cinq, sans accroc.

Pour rejouer l'exercice :

```bash
uv run python scripts/live_scenarios.py
```

Les valeurs attendues sont la vérité terrain de l'export, pas des estimations : une réponse bien tournée sur des chiffres faux y échoue. Le runner **sort en échec aujourd'hui** — c'est voulu : il mesure l'écart réel, il ne certifie pas que tout va bien.

## API / Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/chat` | Question en langage naturel → réponse + artefacts + trace (contrat `ChatAnswer`) |
| `GET` | `/health` | Sonde de vie |
| `GET` | `/` | Page de chat inline (rendu des PNG base64 et des tables JSON, zéro asset externe) |
| `GET` | `/conversations` | Liste des conversations, de la plus récente à la plus ancienne |
| `GET` | `/conversations/{id}` | Le fil complet (messages + artefacts) pour le reprendre |
| `POST` | `/conversations/{id}/duplicate` | Duplique une conversation |
| `DELETE` | `/conversations/{id}` | Supprime une conversation et sa mémoire |

## Le modèle de prévision

`models/maxizoo_sales.joblib` prédit la **quantité vendue** d'un SKU, dans un magasin, un jour donné (entraînement : [notebooks/train_maxizoo_sales.md](notebooks/train_maxizoo_sales.md), 1,35 M de lignes, MAE ~1,56 unité contre ~2,44 pour la moyenne, R² ~0,49).

Deux choix méritent d'être connus avant de s'en servir :

- **Il ne prend ni `sku_id` ni `store_id`**, seulement des attributs (univers, type de marque, prix catalogue, format de magasin, calendrier, remise, anomalie de température). Les identifiants n'apportaient presque rien — R² 0,487 → 0,493, mesuré — et coûtaient la capacité à prédire un SKU jamais vu. Or le dictionnaire documente 4 lancements en cours d'historique, et l'enseigne en fera d'autres : **un produit référencé demain se prédit sans réentraînement**.
- **Il est entraîné sans les lignes en rupture** (`is_rupture = 1`, piège n°4). Quand le stock est épuisé, la quantité observée n'est pas la demande : entraîner dessus apprendrait au modèle à reproduire nos ruptures.

**Ce qu'il a appris, et ce qu'il n'a pas appris.** Il capte la présence d'une campagne (uplift ~×1,6, dans la fourchette de ce qu'on mesure dans les données brutes) mais **pas la profondeur de la remise** : sa prévision est quasi plate de −15 % à −30 %. Ce n'est pas un défaut de modélisation — l'uplift empirique par palier de remise n'est lui-même pas monotone (×1,43 à 15 %, ×1,34 à 25 %, ×1,95 à 30 %), avec 25 à 100 campagnes par palier et une saisonnalité confondue avec la remise (les −30 % sont les Black Friday, donc novembre). En clair : il répond bien à « combien vendra-t-on pendant une campagne ? », et mal à « faut-il remiser à 20 ou à 30 % ? » — cette seconde question demanderait un plan d'expérience, pas ce jeu d'observations.

## Mémoire de conversation

Chaque conversation (`conversation_id`) dispose d'un espace de travail qui **persiste les tableaux intermédiaires en CSV** (`DAA_WORKSPACE_DIR`). Aux tours suivants, ces objets sont réexposés : interrogeables comme des sources (« et pour les femmes ? »), réutilisables pour une prédiction (« prédis **ces** lignes ») et **montés dans la sandbox** pour que le code d'analyse généré les relise (`pd.read_csv('/data/resultat_1.csv')`).

Le **fil lui-même est persisté** au même endroit (`transcript.json`) : la barre latérale de la page de chat liste les conversations précédentes, on en rouvre une pour reprendre où on en était (figures et tableaux compris), on la duplique ou on la supprime. Comme une conversation est un simple dossier, la duplication emporte la mémoire ci-dessus — la copie sait encore « prédire ces lignes » — et la suppression ne laisse aucun CSV orphelin.

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
docs/                     # ARCHITECTURE, CADRAGE, spike-vanna
models/                   # maxizoo_sales.joblib + registry.yaml
sources/                  # catalogue ; la base et son dictionnaire (non versionnés) atterrissent ici
notebooks/                # entraînement du modèle (jupytext .md + .ipynb)
scripts/                  # chargement de la base, extraction de l'échantillon, scénarios live
tests/                    # unit / integration / e2e golden / helpers
tests/fixtures/maxizoo_mini/  # échantillon versionné (425 Ko) : la base réelle en miniature
```

---

## Licences & composants

| Composant | Rôle | Licence |
|---|---|---|
| DuckDB | Moteur SQL analytique | MIT |
| FastAPI | API | MIT |
| LangGraph | Orchestration de l'agent | MIT |
| Pydantic / pydantic-ai | Typage & agent LLM | MIT |
| pandas | Manipulation de données | BSD-3-Clause |
| pg8000 | Driver PostgreSQL | BSD-3-Clause |
| joblib | Sérialisation des modèles | BSD-3-Clause |
| Ollama (Qwen3-Coder) | LLM mutualisé local | MIT (Ollama) / Apache-2.0 (Qwen) `<à confirmer selon le modèle>` |
| **Ce projet** | Code applicatif | MIT — Copyright (c) 2026 floSa `<à confirmer : aucun fichier LICENSE présent>` |
