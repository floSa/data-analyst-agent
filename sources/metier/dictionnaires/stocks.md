# Dictionnaire — `stocks` (classeur Excel, 3 feuilles)

Les stocks des Cycles du Ponant, tels que l'entrepôt les exporte : trois
entrepôts, le journal des mouvements de l'année, et l'inventaire constaté en fin
d'exercice. Chaque **feuille** du classeur est une table.

## Le modèle en une phrase

Un **entrepôt** enregistre des **mouvements** d'entrée et de sortie, et fait
l'objet d'un **inventaire** annuel produit par produit.

```
entrepots ──< mouvements
          └──< inventaire
```

Rien n'est déclaré dans un classeur : les rapprochements se font sur
`code_entrepot` et sur `code_produit`, qui sont écrits en clair sur chaque
ligne.

## `entrepots` — 3 lignes

| `code_entrepot` | `ville` | `surface_m2` | `responsable` |
|---|---|---|---|
| `E-NAN` | Nantes | 4 200 | Camille Ferrand |
| `E-LYO` | Lyon | 2 800 | Hugo Delmas |
| `E-LIL` | Lille | 1 900 | Nora Vasseur |

Le code porte les trois premières lettres de la ville : il se devine, donc il se
cite sans ouvrir la table.

## `mouvements` — 480 lignes

| Colonne | Sens |
|---|---|
| `mouvement_id` | Identifiant de la ligne. |
| `date_mouvement` | Jour du mouvement. **C'est la colonne de référence de la source.** |
| `code_produit` | Code du produit, `VEL-nn` ou `ACC-nn`. **C'est la clé de recoupement avec `ventes` et `production`.** Les 12 produits du catalogue y figurent. |
| `code_entrepot` | Entrepôt concerné. |
| `sens` | `ENT` pour une entrée, `SOR` pour une sortie. **C'est par cette colonne qu'on sépare les deux, jamais par le signe seul.** |
| `quantite` | Nombre d'unités, **SIGNÉ** : positif sur une entrée, négatif sur une sortie. Voir le piège nº 1. |
| `motif` | `réception atelier`, `retour client`, `transfert entrant` pour les entrées ; `expédition client`, `transfert sortant`, `rebut` pour les sorties. |

## `inventaire` — 36 lignes

| Colonne | Sens |
|---|---|
| `code_entrepot`, `code_produit` | Le couple identifie la ligne : 3 entrepôts fois 12 produits. |
| `quantite_en_stock` | Quantité **constatée physiquement** au jour de l'inventaire. Toujours positive ou nulle. |
| `date_inventaire` | `2025-12-31` sur les 36 lignes : c'est un constat daté, pas un historique. |

L'inventaire est un **état constaté**, pas le résultat d'un calcul. Il n'a
aucune raison d'égaler la somme des mouvements de l'année, et les écarter l'un
contre l'autre n'est pas un contrôle de cohérence : c'est comparer un comptage
physique à un journal.

---

## Les pièges de cette source

### 1. `quantite` est signée, et sa somme brute répond à une autre question

Une sortie est écrite en **négatif**. Une somme brute de la colonne ne donne
donc ni ce qui est entré, ni ce qui est sorti : elle donne ce qui **reste**,
c'est-à-dire la différence entre les deux. Et elle le donne sans erreur, sans
avertissement, avec un nombre plausible.

**Règle par défaut : toute question sur un VOLUME de marchandise se filtre par
`sens` et se somme en valeur absolue.** C'est le cas de « combien d'unités sont
sorties », « combien avons-nous expédié », « combien est entré », « quel volume
a transité » — tout ce qui compte de la marchandise qui bouge. On écrit alors
`sum(abs(quantite)) ... WHERE sens = 'SOR'` (ou `'ENT'`), ou de façon
équivalente `-sum(quantite) ... WHERE quantite < 0`.

**L'unique exception, dite comme une propriété :** la question porte sur la
**variation** du stock — de combien il a augmenté ou diminué sur la période,
quel est le solde du journal. Elle ne demande pas un volume déplacé, elle
demande une différence. On somme alors `quantite` **brute**, sans aucun filtre,
et dans ce cas seulement.

| Ce qu'on demande | Comment | Le bon résultat, et le faux qu'on obtient sinon |
|---|---|---|
| « combien d'unités sont sorties des entrepôts ? » | `sens = 'SOR'`, en valeur absolue | **2 279** — et non 4 293, qui est la variation nette et non une sortie |
| « combien d'unités sont entrées ? » | `sens = 'ENT'` | **6 572** |
| « de combien le stock a-t-il varié sur 2025 ? » | `sum(quantite)` brute | **+4 293** |
| « combien de sorties enregistrées ? » | `sens = 'SOR'`, on compte les lignes | **294** mouvements — un comptage de lignes, pas une somme de quantités |
| « combien est sorti de `E-NAN` ? » | `sens = 'SOR'` et `code_entrepot = 'E-NAN'` | **685** — et non 1 356, qui est la variation nette de cet entrepôt |

Une **moyenne** de `quantite` brute n'a aucun sens non plus : elle moyenne des
entrées et des sorties de signes opposés. Une taille moyenne de mouvement se
calcule sur un seul `sens` à la fois.

### 2. Les 12 produits sont ici, les 8 fabriqués sont dans `production`

Cette source stocke tout ce qui se vend, accessoires compris : ses mouvements
portent les 12 codes produit. `production` n'en connaît que 8, parce que les
accessoires sont achetés. « Combien de produits ? » vaut **12** ici, et c'est
aussi juste que le 8 de `production`.

### 3. `quantite_en_stock` n'est pas signée, elle

La colonne de l'inventaire est un comptage physique : elle est toujours positive
ou nulle, et la règle du piège nº 1 ne s'y applique pas. Seule
`mouvements.quantite` est signée.
