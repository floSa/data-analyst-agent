# Dictionnaire — `facturation` (classeur Excel, 3 feuilles)

L'export mensuel de la comptabilité : ce qui a été facturé aux clients du
réseau sur 2025. Chaque feuille du classeur devient une table.

## Le modèle en une phrase

Une **facture** porte plusieurs **lignes de facture**, chacune rattachée à une
station et à un poste tarifaire.

```
factures ──< lignes_facture >── postes_tarifaires
```

Ces relations **ne sont pas déclarées** : un classeur ne porte ni clé primaire
ni clé étrangère. `lignes_facture.facture_id` désigne bien `factures.facture_id`,
mais c'est le dictionnaire qui le dit, pas le schéma. C'est exactement ce que la
base DuckDB `telemetrie`, elle, sait déclarer.

## Feuille `factures` — 1 200 lignes

| Colonne | Sens |
|---|---|
| `facture_id` | Numéro de facture, unique. |
| `code_client` | Code du client facturé, `CLI-nnnn`. **Correspond à `exploitation.clients.code_client`**, pas à `client_id`. |
| `periode` | Mois facturé, `AAAA-MM`. |
| `date_emission` | Date d'émission. **C'est la colonne de référence de la source** — une facture se rattache à l'exercice où elle est émise. |
| `date_echeance` | Date d'exigibilité, systématiquement `date_emission + 30 jours`. |
| `montant_ht_eur` | Montant hors taxes, en **euros**. |
| `montant_ttc_eur` | Montant toutes taxes comprises, en euros. **Dérivé de `montant_ht_eur` à 20 % près** : le sommer avec lui compte deux fois la même recette. Voir le piège nº 2. |
| `statut_paiement` | `payee`, `en_attente`, `impayee`. Libellés en clair, sans accents. |

## Feuille `lignes_facture` — 4 800 lignes

| Colonne | Sens |
|---|---|
| `ligne_id` | Clé technique de la ligne. |
| `facture_id` | Rattachement à `factures`, **non déclaré**. |
| `code_station` | Station où l'énergie a été consommée, `ST-nnn`. Toujours une station **en service**. |
| `code_tarif` | Poste tarifaire appliqué. Voir `postes_tarifaires`. |
| `energie_kwh` | Énergie **facturée** sur la ligne, en **kilowattheures**. À ne pas confondre avec `exploitation.sessions.energie_kwh`, qui est l'énergie **délivrée** : les deux totaux n'ont aucune raison de coïncider. Voir le piège nº 3. |
| `montant_ht_eur` | Montant hors taxes de la ligne, en euros. |

## Feuille `postes_tarifaires` — 4 lignes

La même table de correspondance que `exploitation.tarifs`, vue par la compta.

| `code_tarif` | `libelle` | `prix_kwh_eur` | `tva_pct` |
|---|---|---|---|
| `BASE` | Tarif de base, sans engagement | 0,45 | 20,0 |
| `HP` | Heures pleines, abonnés | 0,38 | 20,0 |
| `HC` | Heures creuses, abonnés | 0,22 | 20,0 |
| `ABO` | Forfait mensuel illimité | 0,00 | 20,0 |

---

## Les pièges de cette source

### 1. La somme des lignes ne fait pas le montant de la facture

`factures.montant_ht_eur` est le montant **arrêté** par la compta ; les
`lignes_facture` sont le détail d'usage remonté par l'exploitation. Les deux
sont produits par deux chaînes différentes et ne se recoupent pas à l'euro près.
Pour un chiffre d'affaires, c'est `factures` qui fait foi ; `lignes_facture`
sert à ventiler par station ou par tarif, pas à totaliser.

### 2. `montant_ttc_eur` n'est pas une colonne à sommer avec les autres

Elle est dérivée de `montant_ht_eur` à 20 % près. Sommer HT et TTC dans le même
agrégat compte deux fois la même recette.

### 3. `energie_kwh` n'est pas celle d'`exploitation`

Ici c'est de l'énergie **facturée**, agrégée par facture et par station. Dans
`exploitation.sessions`, c'est de l'énergie **délivrée**, session par session.
Les deux totaux n'ont aucune raison de coïncider — rythme de facturation, reports,
forfaits `ABO` — et l'écart n'est pas une anomalie de données.

### 4. `prix_kwh_eur = 0,00` sur `ABO` est une vraie valeur

Le forfait illimité se facture au mois, pas au kilowattheure. Reconstituer une
recette en multipliant `energie_kwh` par `prix_kwh_eur` rend donc zéro pour tout
un pan de la clientèle, et sous-estime le total d'autant.
