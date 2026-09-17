# Dictionnaire — `production` (base DuckDB, 4 tables)

L'atelier des Cycles du Ponant : ce qui est **fabriqué**, sur quelle machine, et
les arrêts que ces machines ont connus. Il ne connaît ni les commandes (c'est
`ventes`) ni les entrepôts (c'est `stocks`).

## Le modèle en une phrase

Un **atelier** abrite des **machines** ; chaque **ordre de fabrication** tourne
sur une machine, et chaque **arrêt** immobilise une machine.

```
ateliers ──< machines ──< ordres_fabrication
                      └──< arrets_machine
```

Les clés étrangères sont **déclarées dans la base** : `machines.atelier_id`,
`ordres_fabrication.machine_id` et `arrets_machine.machine_id`. C'est la raison
d'être du type `duckdb` plutôt que d'un fichier de plus — un CSV n'a rien à
déclarer, et les jointures seraient à deviner.

## `ateliers` — 3 lignes

| `code_atelier` | `libelle` | `site` |
|---|---|---|
| `AT-CAD` | Cadrage et soudure | Nantes |
| `AT-PEI` | Peinture et finition | Nantes |
| `AT-ASS` | Assemblage final | Saint-Herblain |

## `machines` — 9 lignes

| Colonne | Sens |
|---|---|
| `machine_id` | Clé primaire, interne. |
| `code_machine` | Code fonctionnel, `M-00n`. **C'est lui qu'on cite**, jamais `machine_id`. |
| `atelier_id` | Clé étrangère vers `ateliers`. |
| `libelle` | Désignation du matériel. |
| `annee_installation` | Année de mise en service. |

Trois machines par atelier : `M-001` à `M-003` au cadrage, `M-004` à `M-006` à
la peinture, `M-007` à `M-009` à l'assemblage.

## `ordres_fabrication` — 140 lignes

| Colonne | Sens |
|---|---|
| `of_id` | Clé primaire. |
| `code_of` | Code fonctionnel, `OF-nnnn`. |
| `code_produit` | Code du produit fabriqué. **C'est la clé de recoupement avec `ventes` et `stocks`.** Seuls les 8 codes `VEL-**` y apparaissent. |
| `machine_id` | Clé étrangère vers `machines`. |
| `date_lancement` | Date de lancement de l'ordre. **C'est la colonne de référence de la source.** |
| `quantite_produite` | Nombre de vélos sortis bons de l'ordre. Toujours positive. |
| `quantite_rebut` | Nombre de vélos écartés au contrôle. `0` est fréquent, et c'est un **vrai zéro** : l'ordre n'a rien rebuté. Aucune valeur sentinelle sur cette colonne. |

Seuls les **8 vélos** sont fabriqués. Les 4 accessoires `ACC-**` du catalogue de
`ventes` sont **achetés à un fournisseur** : ils ne sont jamais lancés en
fabrication, et ils n'ont aucune raison d'apparaître ici. « Combien de produits
fabriquons-nous ? » se répond **8** depuis cette source ; « combien de produits
vendons-nous ? » se répond 12 depuis `ventes`. Les deux sont justes.

## `arrets_machine` — 70 lignes

| Colonne | Sens |
|---|---|
| `arret_id` | Clé primaire. |
| `machine_id` | Clé étrangère vers `machines`. |
| `date_arret` | Jour de l'arrêt. Ce n'est **pas** la colonne de référence de la source. |
| `duree_minutes` | Durée d'immobilisation, en **minutes**. **`-1` est une valeur sentinelle.** Voir le piège nº 1. |
| `motif` | Libellé libre : `panne mécanique`, `changement d'outil`, `maintenance préventive`, `rupture d'approvisionnement`, `défaut qualité`. |

---

## Les pièges de cette source

### 1. `duree_minutes = -1` est une valeur sentinelle, pas une durée

Quand un arrêt est **encore ouvert** au moment de l'extraction, sa durée n'est
pas close : la ligne est quand même écrite, avec `duree_minutes = -1`. **Ce
n'est pas une durée nulle, et encore moins une durée négative** : c'est
l'absence de mesure.

9 arrêts sur 70 sont dans ce cas.

Conséquence directe : `avg(duree_minutes)` est **faux**, et faux vers le bas. La
moyenne juste est `avg(duree_minutes) ... WHERE duree_minutes >= 0`. La même
précaution vaut pour `min`, pour les percentiles, et pour toute somme de temps
d'immobilisation.

**`0` lui ressemble et EST une durée.** Une fausse alerte suivie d'une remise en
route immédiate est enregistrée à `0` minute : la machine s'est bien arrêtée, et
elle n'a rien coûté. 8 arrêts sur 70 sont dans ce cas, et ils **entrent** dans
la moyenne. Écarter « les valeurs négatives ou nulles » est donc aussi faux
qu'oublier la sentinelle, dans l'autre sens.

| Ce qu'on demande | Filtre | Le bon résultat, et le faux qu'on obtient sinon |
|---|---|---|
| « durée moyenne d'un arrêt ? » | `duree_minutes >= 0` | **202,10 min** — et non 175,99, qui moyenne 9 absences de mesure comme si elles valaient −1 minute |
| « combien d'arrêts en 2025 ? » | aucun | **70** : un arrêt encore ouvert est un arrêt |
| « combien d'arrêts encore ouverts ? » | `duree_minutes = -1` | **9** |

Le **comptage** des arrêts, lui, ne se filtre jamais : les 70 lignes sont 70
arrêts. C'est la **moyenne** et les autres agrégats de durée qui excluent la
sentinelle, et eux seuls.

### 2. `quantite_produite` ne se rapproche pas de `quantite` dans `ventes`

Ce qui est fabriqué et ce qui est commandé ne sont pas la même grandeur et ne
couvrent pas les mêmes produits : les accessoires sont vendus sans être
fabriqués. Comparer les deux totaux sans le dire donne un écart qu'on prendrait
pour une anomalie.

### 3. Le rebut n'est pas déduit de la production

`quantite_produite` compte les vélos **sortis bons**. `quantite_rebut` compte
ceux écartés **en plus**, pas dedans. Le nombre de pièces engagées sur un ordre
est donc la somme des deux, et la production utile est `quantite_produite`
seule.
