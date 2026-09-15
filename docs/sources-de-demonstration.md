# Le catalogue de démonstration

Le produit se démontrait sur `titanic` et `iris` : un naufrage de 1912 et des
fleurs mesurées en 1936. Les deux sont d'excellents jeux d'essai et ne
ressemblent à aucun client — ni par leur volumétrie (891 et 150 lignes), ni par
leur modélisation (une table à plat, des colonnes en anglais dont le sens est de
notoriété publique), ni par les questions qu'on leur pose. Un prospect qui
regarde une démonstration y cherche **son** système d'information : plusieurs
bases, plusieurs formats, des codes maison, un export de tableur, et des
chiffres qui ne tombent pas juste du premier coup.

Ce document décrit le catalogue qui remplace les deux, ce qu'il contient, et ce
qu'il a effectivement rendu quand on l'a éprouvé.

## Le domaine, et pourquoi celui-là

**L'exploitation d'un réseau de bornes de recharge pour véhicules électriques.**
Un seul domaine, pour que les questions croisées aient un sens.

Il a été choisi pour trois raisons, et non pour son pittoresque :

1. **Il se décline naturellement sur les trois types de source.** Un SI
   d'exploitation transactionnel (Postgres), une télémétrie volumineuse d'objets
   connectés (une base analytique), une main courante saisie à la main (un CSV),
   un export comptable (un classeur). Ce découpage n'est pas plaqué : c'est
   celui qu'on trouve chez un exploitant réel, et chaque type y est là pour une
   raison qu'on peut énoncer.
2. **Il produit du volume sans artifice.** 380 bornes qui remontent une mesure
   par heure font 547 200 lignes en soixante jours, sans qu'on ait eu à gonfler
   quoi que ce soit.
3. **Il porte ses pièges tout seuls.** Un code d'état de session, une valeur
   sentinelle de compteur, un libellé saisi là où il y aurait dû y avoir un
   code : ce sont les défauts ordinaires de ce métier, pas des chausse-trapes
   fabriquées pour l'occasion.

Les données sont **entièrement synthétiques**. Aucune donnée réelle, aucune
donnée personnelle, aucune entreprise existante — les régions et les villes sont
des noms géographiques, les clients sont des codes `CLI-nnnn`.

## Les cinq sources

Déclaration : [`sources/demonstration/catalogue.yaml`](../sources/demonstration/catalogue.yaml).

| Source | Type | Tables | Lignes | Période couverte | Colonne de date désignée |
|---|---|---|---|---|---|
| `exploitation` | `postgres` | 6 | **49 410** (regions 6, stations 120, bornes 380, clients 900, tarifs 4, sessions 48 000) | 2025-01-01 00:02 → 2025-12-31 23:53 | `sessions.debut_session` |
| `telemetrie` | `duckdb` | 3 | **547 820** (bornes_suivies 380, incidents_reseau 240, releves_puissance 547 200) | 2025-07-01 → 2025-08-29 23:00 | `releves_puissance.horodatage` |
| `interventions` | `file` (CSV) | 1 | **900** | 2025-01-01 → 2025-12-31 | `date_signalement` |
| `referentiel` | `file` (CSV) | 1 | **150** | 2019-01-01 → 2024-06-14 | *(aucune — une seule colonne de date)* |
| `facturation` | `file` (XLSX) | 3 | **6 004** (factures 1 200, lignes_facture 4 800, postes_tarifaires 4) | 2025-01-31 → 2025-12-27 | `factures.date_emission` |

Les volumétries sont franchement distinctes (49 410 / 547 820 / 900 / 150 /
6 004). C'est délibéré : quand une réponse porte le chiffre d'une autre source,
c'est une réponse **fausse** et pas une réponse imprécise, et ça se lit d'un
coup d'œil.

`referentiel` est le seul à ne pas désigner de colonne de date, parce qu'il n'en
a qu'une. La désignation doit se lire là où elle sert ; l'écrire partout par
précaution en ferait une formalité qu'on cesserait de relire.

### Ce qui les relie

```
                 code_station
  exploitation ──────┬──────── telemetrie
       │             ├──────── facturation
       │             └──────── referentiel ───── libelle_station ───── interventions
       │
       ├── code_client ──────── facturation
       ├── code_tarif ───────── facturation
       ├── borne_id ─────────── telemetrie
       └── energie_kwh ──────── facturation   (le même nom, deux grandeurs)
```

`code_station` vit dans quatre sources sur cinq. La cinquième — `interventions` —
ne l'a pas : elle porte le libellé, et c'est le piège nº 1.

Sans ce recoupement, le verrou de source n'aurait rien à protéger : une question
qui ne nomme pas sa source n'aurait qu'une réponse possible. Ici,
« quelle énergie totale en kWh ? » en a deux, toutes deux légitimes, et
l'utilisateur seul sait laquelle il veut.

## Les quatre pièges de modélisation, assumés et documentés

Chacun est décrit dans le dictionnaire de sa source
([`sources/demonstration/dictionnaires/`](../sources/demonstration/dictionnaires/)).

| # | Source | Piège | Ce qu'on rate sans le dictionnaire |
|---|---|---|---|
| 1 | `interventions` | **Un libellé là où on attendrait un code.** `station_libelle` porte `« Ville — Quartier »`, pas `ST-nnn`. | Aucune jointure directe avec le reste du catalogue. Le passage obligé est `referentiel`, qui est la **table de correspondance**. Et 177 des 900 interventions portent sur des stations démontées, qu'`exploitation` ne connaît pas : joindre par le code depuis là en perd une part **silencieusement**. |
| 2 | `telemetrie` | **Une valeur sentinelle.** `puissance_kw = -1` veut dire « le compteur n'a rien remonté ». | 16 447 relevés sur 547 200 (3,0 %). `avg(puissance_kw)` rend **66,67** ; la moyenne juste, `WHERE puissance_kw >= 0`, rend **68,76**. Et `0` est une vraie puissance — la borne répond, elle ne charge personne : 44 187 relevés (8,1 %). |
| 3 | `exploitation` | **Un code d'état.** `sessions.statut` vaut `T`, `I` ou `E`. | `count(*)` rend 48 000 : c'est le nombre de **tentatives**. Le nombre de recharges abouties est 42 281. Plus de 10 % d'écart. |
| 4 | `interventions` | **Une seconde sentinelle, d'un autre format.** `duree_indispo_min = -1` veut dire « non renseigné ». | 108 lignes sur 900 (12,0 %). Durée moyenne naïve : **1 284,63 min** ; durée moyenne juste : **1 459,95 min**. |

Le nº 4 existe parce que le nº 2 existe : les deux se ressemblent assez pour
qu'on croie avoir traité le second en traitant le premier, et ils sont sur deux
sources différentes.

S'y ajoutent trois **tables de correspondance** (`exploitation.tarifs`,
`facturation.postes_tarifaires`, et `referentiel` lui-même), et deux colonnes
qui portent le même nom sans mesurer la même chose (`energie_kwh`, délivrée d'un
côté et facturée de l'autre).

## Les données sont engendrées, pas versionnées

```bash
uv run python scripts/seed_catalogue_demonstration.py
```

Une graine fixe (`GRAINE = 20260915`), une douzaine de secondes, et tout est
reconstruit à l'identique : la base Postgres `daa_demonstration`, la base
DuckDB, les deux CSV et le classeur.

**Ce qui est versionné** : le script, le catalogue, les cinq dictionnaires, et
les deux CSV — ils sont petits (8 ko et 68 ko) et ce sont des oracles dont les
tests dépendent.

**Ce qui ne l'est pas**, et pourquoi, dit dans
[`sources/demonstration/.gitignore`](../sources/demonstration/.gitignore) :

- `telemetrie.duckdb` — 18,6 Mo, et ses **octets** ne sont pas reproductibles
  (identifiants de blocs, ordre d'écriture) même si son **contenu** l'est ;
- `facturation.xlsx` — un binaire de 230 ko qu'aucune revue ne peut lire, et que
  le semis rend en deux secondes.

### Preuve : le semis rejoué deux fois

Semis joué, puis rejoué immédiatement, sans rien toucher entre les deux.

| Artefact | Octets identiques | Contenu identique |
|---|---|---|
| `referentiel.csv` | **oui** (`cmp` muet) | oui |
| `interventions.csv` | **oui** (`cmp` muet) | oui |
| `facturation.xlsx` | **oui** (`cmp` muet) | oui |
| `telemetrie.duckdb` | non — sha256 différent | **oui** — empreinte de contenu `4904fc3c2ea97498` des deux côtés |
| base `daa_demonstration` | *(sans objet)* | **oui** — empreinte de contenu `58554cf6678343b2` des deux côtés |

Les comptes et les périodes imprimés par le semis sont identiques ligne pour
ligne entre les deux exécutions (seul le sha256 du `.duckdb` diffère).

L'empreinte de contenu est un sha256 de toutes les lignes de toutes les tables,
lues dans l'ordre de la clé primaire ; c'est ce qui permet de dire « le contenu
est reproductible » là où les octets ne le sont pas.

Le classeur, lui, **est** figé octet pour octet, et ça a demandé du travail :
openpyxl horodate à la fois `docProps/core.xml` et chaque entrée du zip, et
réécrit `dcterms:modified` à la sauvegarde en écrasant la valeur qu'on lui pose.
`figer_le_classeur` neutralise les deux. C'est la seule façon de faire du
classeur une fonction de la graine et de rien d'autre — et c'est tenu par un
test (`test_le_classeur_fige_ne_depend_plus_de_l_heure`).

## L'inventaire rendu par l'agent

Relevé fait par le socle lui-même (`agents/retrieval/faits.relever`), source par
source, comparé à ce que le semis a écrit.

| Source | Ce que le semis a écrit | Ce que l'agent a lu | Accord |
|---|---|---|---|
| `exploitation` | 6 tables, 49 410 lignes, 2025-01-01 00:02 → 2025-12-31 23:53 | 6 tables, 49 410 lignes, même période, **colonne `debut_session` de `sessions`** | ✔ |
| `telemetrie` | 3 tables, 547 820 lignes, 2025-07-01 → 2025-08-29 23:00 | idem, **colonne `horodatage` de `releves_puissance`** | ✔ |
| `interventions` | 1 table, 900 lignes, 2025-01-01 → 2025-12-31 | idem, **colonne `date_signalement`** | ✔ |
| `referentiel` | 1 table, 150 lignes, 2019-01-01 → 2024-06-14 | idem, **colonne `date_mise_en_service`** | ✔ |
| `facturation` | 3 tables, 6 004 lignes, 2025-01-31 → 2025-12-27 | idem, **colonne `date_emission` de `factures`** | ✔ |

Les cinq désignations de `date_reference` sont respectées : l'agent lit la
période sur la colonne que la source désigne, pas sur la première venue.

### Le temps du premier inventaire

C'est lui qui décide si un client attend au démarrage. Mesuré à froid, un
processus neuf par relevé, trois passes :

| Source | Type | 1ʳᵉ passe | 2ᵉ | 3ᵉ |
|---|---|---|---|---|
| `exploitation` | postgres, 49 410 lignes | 0,292 s | 0,284 s | 0,228 s |
| **`telemetrie`** | **duckdb, 547 820 lignes** | **0,052 s** | **0,060 s** | **0,037 s** |
| `interventions` | CSV, 900 lignes | 0,067 s | 0,083 s | 0,054 s |
| `referentiel` | CSV, 150 lignes | 0,059 s | 0,028 s | 0,032 s |
| `facturation` | XLSX, 6 004 lignes | 0,314 s | 0,351 s | 0,297 s |
| **Total** | | **0,784 s** | **0,806 s** | **0,648 s** |

**La plus grosse source est la plus rapide.** 547 820 lignes relevées en 37 à
60 millisecondes : DuckDB répond `count(*)` depuis ses métadonnées, sans lire
une ligne. Le coût n'est pas dans la volumétrie, il est dans le **format** — le
classeur Excel, 91 fois plus petit, coûte 6 fois plus cher parce qu'openpyxl
doit le désarchiver et le parcourir en entier.

Un client n'attend donc pas au démarrage : **moins d'une seconde pour les cinq
sources**, et ce chiffre ne bougera pas si la télémétrie double.

## Les questions métier, leurs oracles et les réponses obtenues

Parcours joué en quatre conversations, contre vLLM
(`google/gemma-4-E4B-it-qat-w4a16-ct`, `http://localhost:8100/v1`). Les oracles
sont calculés **hors de l'agent**, en SQL direct sur Postgres et DuckDB et en
pandas sur le classeur.

**16 tours sur 16 justes, 69 appels LLM.**

| # | Source | Question | Oracle (calculé à part) | Réponse de l'agent |
|---|---|---|---|---|
| **Q1** | `exploitation` | « combien de sessions de recharge y a-t-il en tout ? » | 48 000 | **48 000** ✔ |
| **Q2** | `exploitation` | « combien de sessions ont le statut T en 2025 ? » | 42 281 | **42 281** ✔ |
| **Q3** | `exploitation` | « quelle énergie totale, en kWh, a été délivrée sur l'année ? » | 1 757 519,23 kWh | **1 757 519,23 kWh** ✔ |
| **Q4** | `exploitation` | « quelles sont les trois stations avec le plus de sessions ? donne leur code et leur nom » *(jointure 3 tables)* | ST-097 Cherbourg — Centre 1 015 ; ST-029 Limoges — Centre 911 ; ST-016 Le Mans — Marché 872 | **les trois, dans l'ordre, avec les comptes** ✔ |
| **Q5** | `exploitation` | « combien de sessions par région ? classe-les de la plus active à la moins active » *(jointure 4 tables)* | Grand Est 9 673 ; Pays de la Loire 9 500 ; Bretagne 8 866 ; Nouvelle-Aquitaine 7 664 ; Normandie 6 372 ; Occitanie 5 925 | **les six, dans l'ordre** ✔ |
| **Q6** | `telemetrie` | « combien de lignes dans la table releves_puissance ? » | 547 200 | **547 200** ✔ |
| **Q7** | `facturation` | « combien de factures as-tu, et quel est le montant total hors taxes facturé ? » *(classeur à 3 feuilles)* | 1 200 factures, 574 408,10 € HT | **1 200 et 574 408,10 €** ✔ |

Q4 n'a de réponse que par `sessions → bornes → stations`, Q5 y ajoute `regions` :
ce sont les clés étrangères déclarées qui portent la jointure, et c'est la raison
d'être du type `postgres` — comme celles du `.duckdb` sont la raison d'être du
type `duckdb`.

### Le verrou de source, éprouvé sur une colonne partagée

`energie_kwh` existe dans `exploitation.sessions` (énergie **délivrée**) et dans
`facturation.lignes_facture` (énergie **facturée**). Même nom, deux grandeurs,
deux ordres de grandeur.

| Tour | Message | Source liée | Réponse | Verdict |
|---|---|---|---|---|
| 1 | « exploitation » | `exploitation` | annonce la source, ses 6 tables et sa période | — |
| 2 | « quelle est l'énergie totale en kWh **dans cette source** ? » | `exploitation` | **1 757 519,23 kWh** — et *pas* 531 098 | ✔ la bonne valeur |
| 3 | « et **dans facturation**, quelle est l'énergie totale en kWh ? » | `facturation` | « Je passe sur la source `facturation` — on travaillait sur `exploitation`. » puis **531 098,10 kWh** — et *pas* 1 757 519 | ✔ la bascule est annoncée, et la valeur est celle du voisin de droite |

Le tour 2 est le vrai test : la question ne nomme aucune source, la colonne
existe des deux côtés, et la réponse est celle de la source liée. Le tour 3
montre qu'on peut en sortir, à condition de le demander.

### Le sens d'une colonne piégeuse, et ce que le dictionnaire fait dire

Deux questions posées à l'agent, sur deux pièges différents. Les réponses sont
citées telles quelles.

> **« que signifie la colonne statut de la table sessions ? »**
>
> « La colonne `statut` de la table `sessions` est de type `CHAR(1)` et est
> obligatoire (`NOT NULL`). Les valeurs présentes sont `'E'`, `'I'`, et `'T'`.
> **Selon le dictionnaire de `exploitation`**, le code `'T'` signifie
> "terminée", `'I'` signifie "interrompue", et `'E'` signifie "erreur". Il est
> précisé que seules les sessions avec `statut = 'T'` sont considérées comme des
> recharges abouties. »

> **« que veut dire la colonne puissance_kw dans la source telemetrie ? »**
>
> « La colonne `puissance_kw` dans la source `telemetrie` est de type `DOUBLE`
> et est obligatoire (`NOT NULL`). **Selon le dictionnaire de `telemetrie`**,
> elle représente la "Puissance instantanée mesurée, en **kilowatts**". Il est
> précisé que la valeur `-1` est une "valeur sentinelle" indiquant que "le
> compteur n'a rien remonté" et qu'elle "n'est pas une puissance : à écarter de
> toute moyenne". De plus, la valeur `0` est une puissance valide, signifiant
> "la borne répond et ne charge personne". »

Le DDL ne donnait que `CHAR(1) NOT NULL` et `DOUBLE NOT NULL`. Tout le reste
vient du dictionnaire, et l'agent le cite en le nommant. C'est exactement ce que
`titanic` et `iris` ne pouvaient pas montrer : leurs colonnes se décrivent
entièrement par leur ontologie, et aucune des deux sources ne déclare de
dictionnaire.

Une réserve de forme : **la question doit nommer sa source**. « que veut dire la
colonne puissance_kw ? », sans « dans la source telemetrie », a été routée comme
une demande d'inventaire et a rendu la liste des tables.

## Ce que ce catalogue a fait apparaître dans le socle

Un catalogue réaliste éprouve le socle, et il a trouvé deux choses que
`titanic` et `iris` ne déclenchaient pas. **Les deux sont dans `src/` et n'ont
pas été corrigées ici** : ce chantier fournit le catalogue, pas la réparation du
moteur.

### 1. `get_schema` n'accepte aucun argument, et le budget de reprise vaut 1

L'outil `get_schema` de l'agent de récupération est sans paramètre. Le modèle
l'appelle avec `{"table_name": "..."}`, la validation refuse, il **réessaie à
l'identique**, et le tour meurt sur `UnexpectedModelBehavior: Tool 'get_schema'
exceeded max retries count of 1`. Côté utilisateur : « Je n'ai pas pu répondre :
la source de données n'a pas pu être interrogée ».

Trace complète :

```
APPEL  list_tables  args={}
RETOUR list_tables  ['referentiel']
APPEL  get_schema   args={"table_name": "referentiel"}
RETRY  get_schema   [{'type': 'extra_forbidden', 'loc': ('table_name',), ...}]
APPEL  get_schema   args={"table_name": "referentiel"}      ← à l'identique
```

Reproductible 3 fois sur 3 sur `referentiel`, `interventions` et `telemetrie`
avec « combien de lignes en tout ? ». Jamais déclenché par `iris` avec la même
question, 3 fois sur 3. Une autre formulation passe
(« combien de lignes dans la table releves_puissance ? » → 547 200), ce qui
rend le défaut intermittent du point de vue de l'utilisateur, donc plus pénible
qu'une panne franche.

Deux correctifs possibles, tous deux dans `src/` : accepter et ignorer un
`table_name` facultatif, ou relever le budget de reprise de l'outil.

### 2. Le dictionnaire ne descend pas jusqu'à l'agent de récupération

`source.dictionary_text()` est passé aux ontologies de l'agent **système**
(`orchestrator/systeme.py`) — celui qui répond « que signifie cette colonne ? ».
L'agent de **récupération**, celui qui écrit le SQL, ne le reçoit pas.

Conséquence mesurée, dans une seule et même conversation, à un tour d'écart :

| Tour | Question | Réponse |
|---|---|---|
| 4 | « que signifie la colonne statut de la table sessions ? » | cite le dictionnaire : `'T'` = terminée, seules les `T` ont abouti |
| 5 | « combien de recharges ont **réellement abouti** en 2025 ? » | **1 405** — le SQL produit était `WHERE statut = 'E'` |

L'oracle est 42 281. Le modèle venait de lire la bonne réponse et a écrit le
contraire, parce que ce qu'il avait lu ne lui était pas repassé au moment
d'écrire la requête. La même question en citant le code — « combien de sessions
ont le statut T en 2025 ? » — rend 42 281.

C'est le défaut le plus coûteux des deux : il ne produit pas d'erreur, il produit
**un chiffre faux et plausible**. Et c'est précisément ce contre quoi un
dictionnaire est censé protéger.

## Jouer la démonstration

```bash
uv run python scripts/seed_catalogue_demonstration.py
```

```bash
DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml uv run uvicorn data_analyst_agent.api.app:app
```

Prérequis : un Postgres joignable par les variables `DAA_PG_*` du `.env`, et le
serveur LLM. Le semis recrée la base `daa_demonstration` à chaque exécution —
il ne touche à aucune autre.

Ce que le dépôt tient sans rien semer : `uv run pytest tests/catalogues/` —
17 tests sur la déclaration, les deux CSV, la correspondance libellé → code, et
le gel du classeur.
