# Dictionnaire — `referentiel` (fichier CSV, 1 table)

Le référentiel des stations du réseau, **depuis l'origine** : 150 lignes, dont
30 pour des stations démontées. C'est la source d'autorité sur les codes, les
libellés et les régions — et c'est la **table de correspondance** qui permet de
rattacher la main courante de la maintenance au reste du catalogue.

## Colonnes

| Colonne | Sens |
|---|---|
| `code_station` | Code fonctionnel, `ST-nnn`. Unique. **C'est la clé de jointure avec `exploitation.stations` et `facturation.lignes_facture`.** |
| `libelle_station` | Libellé d'affichage, `« Ville — Quartier »`. **Unique** — c'est la clé de jointure avec `interventions.station_libelle`, au caractère près : accents, espaces et tiret cadratin compris. |
| `region` | Région administrative, en clair. `exploitation` la porte, elle, par une clé étrangère vers sa table `regions`. |
| `statut` | `ACT` (en service) ou `RET` (retirée du service, matériel démonté). Voir le piège nº 1. |
| `date_mise_en_service` | Date d'ouverture au public. **Seule colonne de date de la source** : elle n'a donc rien à désigner, et le catalogue ne lui met pas de `date_reference`. |
| `nb_points` | Nombre de points de charge prévus à la conception. Même réserve que dans `exploitation` : ce n'est pas un compte de bornes installées. |

---

## Les pièges de cette source

### 1. 150 stations ici, 120 dans `exploitation` — et les deux sont justes

`statut = 'RET'` marque les 30 stations démontées. Le référentiel les garde,
parce qu'un référentiel garde l'historique ; le SI d'exploitation ne les a
jamais eues, parce qu'il décrit le parc qui tourne.

« Combien de stations le réseau compte-t-il ? » a donc **deux réponses
légitimes**, et laquelle est la bonne dépend de la source à laquelle on pose la
question :

| Question | `referentiel` | `exploitation` |
|---|---|---|
| Toutes stations | 150 | 120 |
| En service | 120 (`WHERE statut = 'ACT'`) | 120 |

Un chiffre pris dans la mauvaise source est ici une réponse *fausse*, pas une
réponse imprécise.

### 2. Les stations `RET` n'ont pas de date de retrait

Le fichier dit qu'une station est retirée, pas **quand** elle l'a été. Toute
question du type « combien de stations en service en 2023 ? » est donc sans
réponse dans cette source : `date_mise_en_service` donne l'entrée du parc, rien
ne donne la sortie.

### 3. `region` est un libellé, pas un code

Il se compare à `regions.nom` d'`exploitation`, pas à `stations.region_id`. Une
jointure entre les deux sources sur la région passe par le nom, et la casse
compte.
