# Dictionnaire — `interventions` (fichier CSV, 1 table)

La main courante de la maintenance : un signalement par ligne, 900 lignes sur
l'année 2025. Saisie par les techniciens, avec ce que ça implique — elle désigne
les stations comme on les lit sur le terrain, et laisse des cases vides.

## Colonnes

| Colonne | Sens |
|---|---|
| `intervention_id` | Numéro de ticket, unique. |
| `station_libelle` | **Le libellé de la station, pas son code** — `« Ville — Quartier »`. Aucune jointure directe avec les autres sources : le passage se fait par `referentiel.libelle_station`. Voir le piège nº 1. |
| `date_signalement` | Jour où le problème a été signalé. **C'est la colonne de référence de la source.** |
| `date_resolution` | Jour où le ticket a été clos. Toujours renseignée dans cet extrait, et toujours ≥ `date_signalement`. |
| `nature` | Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`. |
| `duree_indispo_min` | Durée d'indisponibilité de la station, en **minutes**. **`-1` veut dire « non renseigné »**, pas « zéro minute » : à écarter de toute moyenne. Voir le piège nº 2. |
| `technicien` | Matricule anonymisé, `TECH-nn`. |

---

## Les pièges de cette source

### 1. `station_libelle` est un libellé là où on attendrait un code

C'est **le** piège de ce catalogue. Toutes les autres sources désignent une
station par `code_station` (`ST-nnn`). Celle-ci porte le libellé d'affichage,
`« Ville — Quartier »`, parce qu'il est saisi par des humains qui lisent le nom
écrit sur la borne, pas la clé du référentiel.

Il n'existe donc **aucune jointure directe** entre cette source et
`exploitation` ou `facturation`. Le passage obligé est `referentiel`, dont la
colonne `libelle_station` porte exactement les mêmes chaînes — accents, espaces
et tiret cadratin compris.

Deuxième moitié du piège : les interventions portent aussi sur les **stations
démontées**, qui ont été maintenues avant de l'être. `exploitation` ne les
connaît pas. Une jointure qui partirait d'`exploitation` en perdrait une part
**silencieusement** — le résultat serait un nombre plausible, simplement trop
petit. `referentiel`, lui, a les 150 : **177 des 900 interventions** portent sur une
station démontée, et c'est exactement ce qu'une jointure par le code
perdrait.

### 2. `duree_indispo_min = -1` veut dire « non renseigné »

108 lignes sur 900 portent `-1`, soit environ 12,0 %. Le technicien n'a pas
relevé la durée ; le fichier ne sait pas représenter une absence autrement.

Une durée moyenne d'indisponibilité calculée sans écarter ces lignes est fausse,
et faussée vers le bas. Le filtre juste est `WHERE duree_indispo_min >= 0`.

C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`,
dans un autre format et sur une autre source : les deux se ressemblent assez
pour qu'on croie avoir traité le second en traitant le premier.

### 3. Une intervention n'est pas une panne de borne

Le grain est la **station**, pas la borne — le fichier ne dit pas quelle borne
était concernée, et `maintenance préventive` ne correspond à aucune panne. Comme
la nature est un libellé libre et non un code, la seule façon d'isoler les
pannes est de filtrer sur ces libellés, en les citant.
