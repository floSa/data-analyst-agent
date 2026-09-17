# Dictionnaire — `iris` (fichier CSV, 150 lignes)

Le jeu de données Iris de Fisher (1936) : quatre mesures morphologiques sur 150
fleurs, et leur espèce. **Ce fichier n'a aucun rapport avec les Cycles du
Ponant.** Il est dans ce catalogue pour une raison précise et pour une seule :
c'est un jeu de **référence**, dont les chiffres sont connus hors de cet agent,
et il sert à montrer les statistiques et la prédiction.

Il est **recopié tel quel** depuis `sources/iris.csv`, sans une ligne de
modification. Le « mettre au thème » du fabricant de vélos — renommer les
colonnes, traduire les espèces — ferait mentir toute comparaison avec la
littérature et avec le modèle entraîné du registre.

## Les colonnes

Une table plate, sans clé, sans date. Une ligne = une fleur.

| Colonne | Sens |
|---|---|
| `sepal_length` | Longueur du sépale, en **centimètres**. |
| `sepal_width` | Largeur du sépale, en centimètres. |
| `petal_length` | Longueur du pétale, en centimètres. |
| `petal_width` | Largeur du pétale, en centimètres. |
| `species` | Espèce : `setosa`, `versicolor`, `virginica`. C'est la **cible** du modèle `iris` du registre. |

**Aucune valeur manquante, aucune valeur sentinelle.** Les 150 lignes sont
complètes sur les cinq colonnes, et toute mesure est une vraie mesure. C'est dit
ici pour que la précaution qui s'impose sur `production` et `stocks` ne soit pas
appliquée par réflexe à un fichier qui n'en a pas besoin.

## Ce qu'il faut savoir avant de l'interroger

Les trois espèces sont **exactement équilibrées** : 50 lignes chacune. Un
comptage par espèce qui ne rend pas 50 / 50 / 50 est faux, et c'est le contrôle
le plus rapide sur cette source.

| Espèce | Lignes | `petal_length` moyenne |
|---|---|---|
| `setosa` | 50 | 1,462 cm |
| `versicolor` | 50 | 4,260 cm |
| `virginica` | 50 | 5,552 cm |

`setosa` est **linéairement séparable** des deux autres sur la seule longueur de
pétale ; `versicolor` et `virginica` se chevauchent. C'est ce qui rend ce jeu
utile en démonstration : une classification y atteint de très bons scores sans
être triviale.

## La prédiction

La source déclare ses `features` pour le dataset `iris` du registre
(`models/registry.yaml`), et la correspondance est l'identité : les en-têtes du
fichier portent déjà les noms attendus par le modèle. Une prédiction se demande
donc en donnant les quatre mesures, et le modèle rend `setosa`, `versicolor` ou
`virginica`.
