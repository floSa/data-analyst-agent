# Dictionnaire — `ventes` (Postgres, 4 tables)

Le carnet de commandes des Cycles du Ponant : le catalogue produit, les clients,
les commandes et leur détail ligne à ligne. Il ne connaît ni la fabrication
(c'est `production`) ni les mouvements d'entrepôt (c'est `stocks`).

## Le modèle en une phrase

Un **client** passe des **commandes** ; chaque commande porte des **lignes**, et
chaque ligne porte un **produit**.

```
clients ──< commandes ──< lignes_commande >── produits
```

## `clients` — 18 lignes

| Colonne | Sens |
|---|---|
| `client_id` | Clé primaire, interne. |
| `code_client` | Code fonctionnel, `CLI-nn`. |
| `raison_sociale` | Nom du revendeur. Ce sont tous des professionnels. |
| `ville` | Ville du revendeur. |
| `canal` | `magasin` (détaillant physique), `grossiste`, `en ligne`. |
| `date_creation` | Date d'ouverture du compte. Ce n'est **pas** la colonne de référence de la source : la période couverte se lit sur `commandes.date_commande`. |

## `produits` — 12 lignes

| Colonne | Sens |
|---|---|
| `produit_id` | Clé primaire, interne. |
| `code_produit` | Code fonctionnel. **C'est lui qu'on retrouve dans `production` et dans `stocks`**, jamais `produit_id`. `VEL-nn` pour un vélo, `ACC-nn` pour un accessoire. |
| `libelle` | Nom commercial. |
| `famille` | `ville`, `tout-terrain`, `route`, `utilitaire`, `accessoire`. |
| `prix_unitaire_eur` | Prix catalogue, en **euros**, hors taxes. |

**8 vélos** (`VEL-01` à `VEL-08`) et **4 accessoires** (`ACC-01` à `ACC-04`).
Les vélos sont fabriqués à l'atelier ; les accessoires sont **achetés à un
fournisseur** et revendus tels quels. C'est pour cela que `production` n'en
connaît que 8 là où cette source en connaît 12 : l'écart est un fait du métier,
pas une donnée manquante.

## `commandes` — 180 lignes

| Colonne | Sens |
|---|---|
| `commande_id` | Clé primaire. |
| `code_commande` | Code fonctionnel, `CMD-nnnn`. |
| `client_id` | Clé étrangère vers `clients`. |
| `date_commande` | Date de prise de commande. **C'est la colonne de référence de la source.** |
| `statut` | Code de **trois lettres** : `LIV` livrée, `EXP` expédiée, `ANN` annulée. Voir le piège nº 1. |
| `montant_total_eur` | Total de la commande, en euros hors taxes. Il vaut exactement la somme des `montant_ligne_eur` de ses lignes. **C'est un total de NIVEAU COMMANDE** : il ne se somme jamais après une jointure vers `lignes_commande`. Voir le piège nº 2. |

## `lignes_commande` — 463 lignes

| Colonne | Sens |
|---|---|
| `ligne_id` | Clé primaire. |
| `commande_id`, `produit_id` | Clés étrangères. |
| `quantite` | Nombre d'unités commandées. Toujours **positive** : pas de retours ici. |
| `prix_unitaire_eur` | Prix appliqué, hors taxes. Toujours égal au prix catalogue du produit : aucune remise n'est pratiquée. |
| `montant_ligne_eur` | `quantite * prix_unitaire_eur`, en euros hors taxes. |

Une commande porte de 1 à 4 lignes, et jamais deux fois le même produit.

---

## Les pièges de cette source

### 1. `statut` se filtre pour une mesure et pas pour une autre

Les trois codes désignent des commandes **également enregistrées** : les 180
lignes sont 180 commandes reçues, quel que soit le statut. Ce qui les distingue
n'est pas leur nature, c'est leur **aboutissement commercial** — ce qui est
effectivement parti du quai et parti en facturation.

| Code | Sens | Nombre |
|---|---|---|
| `LIV` | Livrée au client | 116 |
| `EXP` | Expédiée, non encore livrée | 48 |
| `ANN` | Annulée avant expédition — rien n'est parti, rien n'est facturé | 16 |

**Règle par défaut : AUCUN filtre sur `statut`.** Tout **comptage** de commandes,
de lignes ou de clients porte sur les 180 commandes, les trois codes confondus.
Cela vaut quel que soit le mot employé pour les désigner — commandes, bons,
affaires, dossiers.

**L'unique exception, dite comme une propriété :** la question porte sur ce qui
a été **effectivement facturé ou effectivement expédié** — elle distingue ce qui
est parti de ce qui a seulement été enregistré. C'est le cas de toute somme en
**euros** (chiffre d'affaires, recette, montant facturé, total encaissé) et de
toute somme d'**unités vendues ou expédiées**. On pose alors
`WHERE statut <> 'ANN'`, et dans ce cas seulement.

Demander « combien de commandes avons-nous reçues » ne fait aucune distinction
de ce genre : aucun filtre. Filtrer quand rien ne le demande coûte exactement
aussi cher que l'oublier.

| Ce qu'on demande | Filtre | Le bon résultat, et le faux qu'on obtient sinon |
|---|---|---|
| « combien de commandes en 2025 ? » | aucun | **180** — et non 164 |
| « combien de lignes de commande ? » | aucun | **463** |
| « quel chiffre d'affaires sur 2025 ? » | `statut <> 'ANN'` | **1 496 743,00 €** — et non 1 636 093,00 € : les 16 annulées pèsent 139 350,00 € qui n'ont jamais été facturés |
| « quel est notre meilleur client ? » | `statut <> 'ANN'` | **Vélocité Bordeaux**, 170 149,00 € : c'est un classement en euros, donc facturé |
| « quel produit s'est le plus vendu ? » | `statut <> 'ANN'` | **ACC-03**, 264 unités : c'est un volume expédié |

### 2. `montant_total_eur` est au niveau de la COMMANDE, pas de la ligne

`commandes.montant_total_eur` est porté une fois par commande.
`lignes_commande.montant_ligne_eur` est porté une fois par ligne. Les deux sont
justes, et la somme de l'un égale la somme de l'autre — **tant qu'on ne les
mélange pas dans la même requête**.

Joindre `commandes` à `lignes_commande` **duplique l'en-tête** : une commande de
quatre lignes apporte quatre fois son `montant_total_eur`. La somme gonfle alors
du nombre moyen de lignes par commande, ici 2,57 — sans erreur, sans
avertissement, avec un nombre qui a l'air d'un chiffre d'affaires.

**Règle par défaut : pour toute somme d'argent, ne joindre QUE ce dont on a
besoin.** Un chiffre d'affaires par client ou par canal se calcule sur
`commandes` seule, jointe à `clients` — `lignes_commande` n'y a rien à faire :

```sql
SELECT cl.canal, sum(o.montant_total_eur)
FROM commandes o JOIN clients cl ON cl.client_id = o.client_id
WHERE o.statut <> 'ANN' GROUP BY cl.canal
```

**L'exception :** la question descend au **produit** — chiffre d'affaires par
produit, par famille, par référence. Il faut alors `lignes_commande`, et on
somme `montant_ligne_eur`, **jamais** `montant_total_eur`.

| Ce qu'on demande | Ce qu'on joint | Ce qu'on somme | Le bon résultat |
|---|---|---|---|
| CA par canal | `commandes` + `clients` | `montant_total_eur` | magasin **862 229 €**, en ligne **331 499 €**, grossiste **303 015 €** |
| CA par canal, via les lignes | + `lignes_commande` | `montant_ligne_eur` | les **mêmes** trois chiffres |
| CA par canal, en sommant l'en-tête après cette jointure | + `lignes_commande` | `montant_total_eur` | **FAUX** : 2 630 871 / 1 017 293 / 949 647 |
| CA par produit | `lignes_commande` + `produits` + `commandes` | `montant_ligne_eur` | le seul chemin possible |

### 3. `produits` ne dit pas ce qui est fabriqué

Les 12 produits de cette table sont ce que l'entreprise **vend**. Pour savoir ce
qu'elle **fabrique**, il faut `production`, qui n'en connaît que 8. Répondre
« 12 » à une question sur la fabrication est faux, et répondre « 8 » à une
question sur le catalogue de vente l'est aussi.

### 4. Aucune jointure par clé technique avec les autres sources

`produit_id` et `client_id` sont internes à cette base. Le seul identifiant
partagé avec `production` et `stocks` est **`code_produit`** : une jointure sur
`produit_id` vers une autre source rapprocherait des lignes au hasard.
