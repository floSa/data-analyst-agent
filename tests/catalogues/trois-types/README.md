# Catalogue des trois types — postgres, file et duckdb à la fois

Ce dossier n'est pas un catalogue de production. Il sert à **mesurer** qu'un
agent répond juste sur les trois types de source déclarables, dans le même
catalogue et dans la même conversation, **sans jamais les confondre**.

C'est ce qu'aucun autre catalogue de mesure ne fait. `../realiste/` a bien trois
*natures* de source — Postgres, CSV, classeur Excel — mais deux d'entre elles
sont le même *type* de catalogue (`file`) : il ne peut donc rien dire du
troisième, `duckdb`, ni du fait qu'un agent ne mélange pas des sources ouvertes
par trois chemins d'adaptateur différents.

| Source | Type | Adaptateur | Ce qu'elle apporte de particulier |
|---|---|---|---|
| `commandes` | `postgres` | `PostgresAdapter` | un serveur, deux tables jointes par `client_id` |
| `capteurs` | `file` | `DuckDBAdapter.from_file` | un CSV plat, sans aucune contrainte |
| `entrepot` | `duckdb` | `DuckDBAdapter.from_database` | une **étoile** de trois tables, clés primaires ET étrangères déclarées |

La dernière ligne est la raison d'être du type `duckdb`. Un CSV et un classeur
n'ont rien à déclarer : un schéma en étoile passé par la porte `file` arriverait
au modèle sans ses jointures, et le modèle les devinerait. Une base, elle, porte
ses clés — `ventes.magasin_id` désigne `magasins`, et le DDL servi au modèle le
dit.

## Les données

Elles sont **engendrées, pas versionnées** : une base Postgres ne se versionne
pas, et un fichier `.duckdb` de 3,7 Mo non plus. Le tirage est figé
(`random.Random(20260914)`), donc deux exécutions rendent les mêmes chiffres.

```bash
uv run python scripts/seed_catalogue_trois_types.py
```

Le CSV `capteurs.csv` fait exception : il est minuscule, il est un oracle, et il
est versionné.

## Les oracles, relevés le 2026-09-14

| Source | Type | Tables | Lignes | Période couverte |
|---|---|---|---|---|
| `commandes` | postgres | 2 | 837 (clients : 60, commandes : 777) | 2023-01-01 → 2024-12-30 |
| `capteurs` | file (CSV) | 1 | 111 | 2025-03-01 → 2025-06-19 |
| `entrepot` | duckdb | 3 | 40 052 (magasins : 12, articles : 40, ventes : 40 000) | 2024-01-01 → 2024-12-31 |

Les trois volumétries sont **franchement distinctes** (837 / 111 / 40 052), et
c'est tout l'intérêt : une réponse qui donne le chiffre d'une autre source est
une réponse *fausse*, pas une réponse imprécise. C'est sur ces nombres que
l'oracle du runner décide, et c'est pour ça qu'ils ne se ressemblent pas.

Les trois **se recoupent** par ailleurs, sans quoi aucune question ne serait
ambiguë : chacune a une colonne de date sous un nom différent (`date_commande`,
`horodatage`, `jour`), et deux ont un `montant`.

## Ce qu'on y mesure

```bash
DAA_CATALOG_PATH=tests/catalogues/trois-types/catalogue.yaml \
uv run python scripts/mesure_trois_types_de_source.py
```

Six tours dans une seule conversation : l'inventaire → la liaison de la source
Postgres → sa volumétrie sans la nommer → la bascule vers la base DuckDB et la
sienne → **une question qui n'a de réponse que par la jointure déclarée en clé
étrangère** → la bascule vers le CSV et la sienne.

Le runner sort en échec si un seul tour se trompe de source. Il lit la réponse
**et les tableaux servis avec** : à « combien de lignes en tout ? » sur une
source à deux tables, l'agent répond « 2 lignes retournées — voir le tableau
ci-dessous », et le chiffre est dans le tableau — un oracle qui ne lirait que la
prose compterait faux une réponse que l'utilisateur lit correctement.

Relevé du 2026-09-14, sur vLLM (`google/gemma-4-E4B-it-qat-w4a16-ct`) :
**6/6 tours justes, 24 appels LLM** (moyenne 4,00 par tour).
