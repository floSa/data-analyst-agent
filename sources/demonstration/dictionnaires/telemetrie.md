# Dictionnaire — `telemetrie` (base DuckDB, 3 tables)

La télémétrie des bornes du réseau : ce que chaque borne a remonté, heure par
heure, sur soixante jours. C'est la source **volumineuse** du catalogue —
547 200 relevés — et celle qui porte la valeur sentinelle du catalogue.

## Le modèle en une phrase

Une **borne suivie** remonte des **relevés de puissance** horaires, et connaît
des **incidents réseau**.

```
bornes_suivies ──< releves_puissance
               └──< incidents_reseau
```

Les clés étrangères sont **déclarées dans la base** : `releves_puissance.borne_id`
et `incidents_reseau.borne_id` désignent `bornes_suivies`. C'est la raison d'être
du type `duckdb` plutôt que d'un fichier de plus — un CSV n'a rien à déclarer,
et les jointures seraient à deviner.

## `bornes_suivies` — 380 lignes

| Colonne | Sens |
|---|---|
| `borne_id` | Clé primaire. **Le même identifiant que `exploitation.bornes.borne_id`.** |
| `code_borne` | Code fonctionnel, `BRN-nnnn`. Idem `exploitation`. |
| `code_station` | Code de la station d'accueil, `ST-nnn`. **Dénormalisé ici** : la télémétrie n'a pas de table des stations, elle porte le code en clair sur la borne. |
| `puissance_nominale_kw` | Puissance maximale du matériel, en kilowatts. Recopiée d'`exploitation`. |

## `releves_puissance` — 547 200 lignes

| Colonne | Sens |
|---|---|
| `releve_id` | Clé primaire. |
| `borne_id` | Clé étrangère vers `bornes_suivies`. |
| `horodatage` | Instant du relevé, au pas **horaire**. **C'est la colonne de référence de la source.** Un relevé par borne et par heure, du 2025-07-01 00:00 au 2025-08-29 23:00 — soit 1 440 relevés par borne, sans trou. |
| `puissance_kw` | Puissance instantanée mesurée, en **kilowatts**. **`-1` est une valeur sentinelle** — le compteur n'a rien remonté — et n'est pas une puissance : à écarter de toute moyenne. `0` en est une, elle : la borne répond et ne charge personne. Voir le piège nº 1. |
| `temperature_c` | Température de l'armoire, en **degrés Celsius**. Peut être négative, et c'est normal. |

## `incidents_reseau` — 240 lignes

| Colonne | Sens |
|---|---|
| `incident_id` | Clé primaire. |
| `borne_id` | Clé étrangère vers `bornes_suivies`. |
| `debut_incident`, `fin_incident` | Horodatages. Tout incident de cette table est **clos** : il n'y a pas de `fin_incident` nulle. |
| `cause` | Libellé libre : `coupure secteur`, `défaut modem`, `surchauffe`, `mise à jour logicielle`. |

---

## Les pièges de cette source

### 1. `puissance_kw = -1` est une valeur sentinelle, pas une puissance

Quand le compteur d'une borne n'a rien remonté à l'heure dite, le relevé est
quand même écrit, avec `puissance_kw = -1.0`. **Ce n'est pas une puissance
nulle, et encore moins une puissance négative** : c'est l'absence de mesure.

16 447 relevés sur 547 200 sont dans ce cas, soit environ 3,0 %.

Conséquence directe : `avg(puissance_kw)` est **faux**, et faux vers le bas. La
moyenne juste est `avg(puissance_kw) ... WHERE puissance_kw >= 0`. La même
précaution vaut pour `min`, pour les percentiles, et pour toute somme d'énergie
reconstituée à partir de cette colonne.

Une borne à l'arrêt, elle, remonte bien `0.0` — et c'est une information
différente : le matériel répond, il ne charge personne. 44 197 relevés, soit
8,1 %, sont dans ce cas. Les deux valeurs se ressemblent dans un tableau ; elles
ne se traitent pas de la même façon, et c'est tout le piège.

### 2. La période de la source est celle des relevés, pas celle des incidents

`incidents_reseau` porte deux colonnes de date de plus. La source **désigne**
`releves_puissance.horodatage` comme colonne de référence : c'est la table qui
fait sa volumétrie et son intérêt. Sans cette désignation, la période affichée
serait celle de la première colonne de date rencontrée dans le schéma, choisie
par l'ordre du DDL et non par le métier.

### 3. Le parc y est figé, celui d'`exploitation` ne l'est pas

`bornes_suivies` est une photographie du parc au moment où la collecte a
démarré. Une borne posée depuis n'y est pas. Croiser les deux sources par
`borne_id` est légitime, mais un `count(*)` de part et d'autre n'a aucune raison
de coïncider indéfiniment.
