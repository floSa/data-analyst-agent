# Catalogue des deux dates — une source, deux périodes également vraies

Ce dossier n'est pas un catalogue de production. Il sert à **mesurer** quelle
colonne de date porte la période affichée quand la source en a plusieurs.

Le relevé d'une source lit sa période d'un `min`/`max` sur une colonne de date
(`agents/retrieval/faits.py`). Quand il n'y en a qu'une, la question ne se pose
pas. Quand il y en a deux — une date de commande, une date de livraison — la
période était celle de la **première du schéma**, choisie par l'ordre du DDL.
Rien n'était faux : la colonne est nommée dans la réponse. Mais rien ne disait
non plus que c'était celle qui compte, et l'ordre du DDL n'est pas une décision.

La source le sait, et elle seule. Elle le déclare donc, à côté de `features` et
pour la même raison — `dictionary` s'adresse au modèle de langage, `features` et
`date_reference` s'adressent au code :

```yaml
date_reference: date_livraison   # ou `commandes.date_livraison`
```

## Les données

`commandes.csv` — 120 lignes, une table plate, **deux** colonnes de date. Le
fichier est **versionné** plutôt qu'engendré : c'est un oracle, et le régénérer
ferait bouger les bornes, donc perdrait la comparaison d'un avant et d'un après.
Il pèse 5 ko. Le tirage qui l'a produit est figé, et le voici en entier — une
livraison tombe entre 2 et 39 jours après sa commande, ce qui suffit à décaler
les deux intervalles des deux côtés :

```python
tirage = random.Random(20260915)
for i in range(120):
    commande = dt.date(2024, 2, 5) + dt.timedelta(days=tirage.randrange(0, 300))
    livraison = commande + dt.timedelta(days=tirage.randrange(2, 40))
    ...  # commande_id, date_commande, date_livraison, client, montant
```

| Colonne | Début | Fin |
|---|---|---|
| `date_commande` (première du schéma) | 2024-02-11 | 2024-11-30 |
| `date_livraison` | 2024-02-22 | 2025-01-07 |

Les deux bornes diffèrent des deux côtés, et la fin de `date_livraison` tombe
dans l'année suivante : la période affichée dit sans ambiguïté quelle colonne a
été lue.

## Les trois catalogues

Les mêmes octets de données, la même source, à une ligne de YAML près.

| Catalogue | `date_reference` | Période rendue | Avertissement |
|---|---|---|---|
| `sans-designation.yaml` | — | du 2024-02-11 au 2024-11-30 (`date_commande`) | — |
| `livraison-designee.yaml` | `date_livraison` | du 2024-02-22 au 2025-01-07 (`date_livraison`) | — |
| `designation-fautive.yaml` | `date_livraision` *(faute de frappe)* | du 2024-02-11 au 2024-11-30 (`date_commande`) | colonne introuvable, colonnes réelles nommées |

La troisième ligne est celle qui empêche la correction d'être un cache-misère.
Une désignation que le schéma ne porte pas ne fait pas tomber le relevé — les
tables, les lignes et une période restent bonnes, et un inventaire qui disparaît
pour une faute de frappe serait une punition démesurée. Mais elle **se dit** :
sans ce message, le repli sur `date_commande` serait indiscernable de la
première ligne, et la déclaration fausse survivrait indéfiniment.

## Ce qu'on y mesure

```bash
uv run python scripts/mesure_releve_des_sources.py --seulement dates
```

Le banc lit les trois catalogues à la suite et imprime la période de chacun.
Les mêmes trois cas sont verrouillés en test — `tests/unit/retrieval/test_faits.py`,
section « la colonne de date de référence ».

Relevé du 2026-09-15 :

```
sans désignation       1 table(s), 120 ligne(s) — du 2024-02-11 au 2024-11-30 (date_commande)
date_livraison         1 table(s), 120 ligne(s) — du 2024-02-22 au 2025-01-07 (date_livraison)
désignation fautive    1 table(s), 120 ligne(s) — du 2024-02-11 au 2024-11-30 (date_commande)
                       [colonne de date de référence déclarée introuvable : date_livraision
                        — colonnes de date de la source : commandes.date_commande, commandes.date_livraison]
```
