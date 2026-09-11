# Catalogue d'ambiguïté — deux sources, une colonne commune

Ce dossier n'est pas un catalogue de production. Il sert à **mesurer** ce que
fait l'agent quand une question peut porter sur deux sources et qu'aucune n'est
liée à la conversation. Il est versionné pour une seule raison : une mesure
d'« avant » et une mesure d'« après » ne sont comparables que sur les mêmes
octets. Régénérer `employes.csv` ferait bouger l'oracle et perdrait la
comparaison.

## Les deux sources

| Source | Fichier | Lignes | Colonne partagée |
|---|---|---|---|
| `titanic` | `../../../sources/titanic.csv` (celui de la production) | 891 | `Sex` |
| `employes` | `employes.csv` (figé ici) | 300 | `sex` |

## Les oracles

Question de mesure : **« Quel est le pourcentage de femmes ? »**

| Source | Femmes | Total | Oracle |
|---|---|---|---|
| `titanic` | 314 | 891 | **35,24 %** |
| `employes` | 153 | 300 | **51,00 %** |

Deux valeurs franchement distinctes : la réponse dit sans ambiguïté quelle
source a été interrogée.

## Les deux catalogues

`titanic-en-premier.yaml` et `employes-en-premier.yaml` déclarent **les mêmes
deux sources, dans l'ordre inverse**. Les données ne changent pas ; seul l'ordre
change. C'est la variable de l'expérience, et la seule.

## La mesure

Lancer l'application sur l'un puis l'autre, poser la question dans une
conversation **neuve** à chaque fois, cinq fois :

```
DAA_CATALOG_PATH=tests/catalogues/ambiguite/titanic-en-premier.yaml \
DAA_SESSION_COOKIE_SECURE=false \
uv run uvicorn data_analyst_agent.api.app:app --host 127.0.0.1 --port 8079
```

### Mesure du 2026-09-11, sur `22c9507`

| Ordre déclaré | Comportement, 5 essais |
|---|---|
| `titanic` en premier | **5/5** répond « 35,24 % » sans rien demander |
| `employes` en premier | **5/5** énumère les deux sources, ne répond jamais |

Même question, mêmes données, seul l'ordre du YAML change : le comportement
bascule entièrement. **Aucun des deux n'est un choix.** Le second tombe du bon
côté par accident — le planificateur a laissé `plan.source` vide, et
`_regle_choisir_la_source` a donc pu proposer. Il ne faut pas le « corriger ».

## Ce que la mesure doit devenir

Le même comportement dans les deux ordres : proposer l'inventaire et attendre.
