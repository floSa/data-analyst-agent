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
| 2 | `telemetrie` | **Une valeur sentinelle.** `puissance_kw = -1` veut dire « le compteur n'a rien remonté ». | 16 447 relevés sur 547 200 (3,0 %). `avg(puissance_kw)` rend **66,67** ; la moyenne juste, `WHERE puissance_kw >= 0`, rend **68,76**. Et `0` est une vraie puissance — la borne répond, elle ne charge personne : 44 197 relevés (8,1 %). |
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

Ce parcours est **rejoué intégralement** après chaque réparation du socle, sur
vLLM, et par un runner : **16/16, 56 appels LLM**
(cf. [Les garde-fous, et ce qui n'a pas bougé](#les-garde-fous-et-ce-qui-na-pas-bougé)).
Les oracles de ce tableau ont été recalculés hors de l'agent à cette occasion :
ils sont inchangés.

**Ce que comptent les seize tours**, puisque le décompte n'était pas détaillé :
trois tours d'**ouverture** qui nomment une source et lient la conversation
(`exploitation`, `telemetrie`, `facturation` — la table du verrou en montre un,
marqué « — ») ; les **sept** questions Q1 à Q7 ; les **trois** tours du verrou de
source ; les **deux** questions sur le sens d'une colonne piégeuse ; et la
**réserve de forme**, la même question posée sans nommer sa source.

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

## Ce que ce catalogue a fait apparaître dans le socle, et ce qu'on en a fait

Un catalogue réaliste éprouve le socle, et il a trouvé plusieurs choses que
`titanic` et `iris` ne déclenchaient pas. Le chantier qui a fourni le catalogue
en a **constaté deux sans les réparer** ; le suivant les a réparées, et la
réparation en a découvert une troisième — le même défaut à l'agent suivant. Les
sections qui suivent gardent chaque constat d'origine et lui ajoutent la mesure
d'après.

Elles se lisent dans cet ordre : le dictionnaire jusqu'à l'agent SQL (1),
`get_schema` et son argument refusé (2), le dictionnaire jusqu'à celui qui écrit
le Python (3), puis les deux questions que ces réparations ont posées — un
dictionnaire écrit pour un humain tient-il, et la colonne des comptes qui
disparaissait d'un classement.

**Le moteur en service est vLLM, seul** — `google/gemma-4-E4B-it-qat-w4a16-ct`
sur `http://localhost:8100/v1`. Tout ce qui est chiffré ici a été joué contre
lui, depuis un message utilisateur passé à l'orchestrateur complet,
planificateur compris. L'avant et l'après d'un même défaut sont toujours du même
côté : une comparaison qui enjamberait deux moteurs ne vaudrait rien.

> **Chiffres Ollama — trace historique, plus rejouée.** Une partie de ce
> chantier a d'abord été mesurée contre Ollama (`gemma4:e4b`, port 11434). Ces
> relevés sont datés du **15 septembre 2026** et conservés plus bas à titre de
> trace : ils ne sont **plus rejoués**, ne servent d'oracle à rien, et aucun
> chiffre courant ne s'y compare. Ils sont signalés là où ils apparaissent.

### 1. Le dictionnaire ne descendait pas jusqu'à l'agent de récupération

`source.dictionary_text()` était passé aux ontologies de l'agent **système**
(`orchestrator/systeme.py`) — celui qui répond « que signifie cette colonne ? ».
L'agent de **récupération**, celui qui écrit le SQL, ne le recevait pas.

Conséquence mesurée, dans une seule et même conversation, à un tour d'écart :

| Tour | Question | Réponse |
|---|---|---|
| 4 | « que signifie la colonne statut de la table sessions ? » | cite le dictionnaire : `'T'` = terminée, seules les `T` ont abouti |
| 5 | « combien de recharges ont **réellement abouti** en 2025 ? » | **1 405** — le SQL produit était `WHERE statut = 'E'` |

L'oracle est 42 281. Le modèle venait de lire la bonne réponse et a écrit le
contraire, parce que ce qu'il avait lu ne lui était pas repassé au moment
d'écrire la requête. C'est le défaut le plus coûteux des deux : il ne produit
pas d'erreur, il produit **un chiffre faux et plausible**. Et c'est précisément
ce contre quoi un dictionnaire est censé protéger.

**Reproduit 5 fois sur 5** sur vLLM, la même conversation à deux tours, le même
SQL à chaque fois :

| Tirages | SQL produit au tour 5 | Réponse | Oracle |
|---|---|---|---|
| 5/5 | `WHERE statut = 'E' AND EXTRACT(YEAR FROM debut_session) = 2025` | **1 405** ✘ | 42 281 |

*(Trace historique du 15 septembre 2026, plus rejouée : Ollama `gemma4:e4b`
reproduisait le même défaut, 5/5, avec le même `statut = 'E'`.)*

#### Le correctif : le dictionnaire entre dans le prompt de l'agent SQL

`run_retrieval` prend un argument `dictionary`, l'orchestrateur y passe
`source.dictionary_text()`, et le texte est collé **après** la démarche dans le
prompt système — ce qu'on lit en dernier est ce qu'on a sous les yeux au moment
d'écrire, et l'erreur corrigée est une erreur d'écriture de requête. Une source
qui ne déclare pas de dictionnaire — `titanic`, `iris` — reçoit le prompt
d'avant, **au caractère près**, et c'est tenu par un test.

Un **outil** (`lire_le_dictionnaire`) avait été envisagé, et écarté sur la
mesure : le défaut n'est pas que le modèle cherche le sens et ne le trouve pas,
c'est qu'il ne le cherche **pas** — il écrivait `statut = 'E'` avec assurance,
10 fois sur 10. Un outil qu'on n'appelle jamais ne dit rien, et il aurait coûté
en plus un aller-retour sur `retrieval_request_limit`.

Après, sur vLLM :

| Tirages | SQL produit au tour 5 | Réponse | Oracle |
|---|---|---|---|
| 5/5 | `WHERE statut = 'T' AND debut_session >= '2025-01-01' AND debut_session < '2026-01-01'` | **42 281** ✔ | 42 281 |

#### Ce que ça coûte, en tokens rendus par le serveur

Le dictionnaire est du Markdown libre, et le prompt de l'agent SQL repart en
entier à chaque aller-retour de sa boucle de correction : c'est le prompt du
système qui se paie le plus de fois par tour. Le poids a donc été mesuré avant
et après, par `/tokenize` de vLLM — le tokeniseur du serveur, pas une
estimation.

| Source | Prompt nu | Prompt + dictionnaire | Rapport | Dictionnaire |
|---|---|---|---|---|
| `exploitation` | 824 tokens | **3 324** | ×4,03 | entier |
| `telemetrie` | 824 | **2 580** | ×3,13 | entier |
| `facturation` | 824 | **2 519** | ×3,06 | entier |
| `interventions` | 824 | **2 186** | ×2,65 | entier |
| `referentiel` | 824 | **1 998** | ×2,42 | entier |

Le prompt quadruple au pire, et il reste petit : 3 324 tokens pour une fenêtre
servie de 32 768, soit **10 %**. Une source sans dictionnaire reste à 824.

**Le plafond, et la règle de coupe.** Le catalogue de démonstration tient ;
celui d'un client peut peser dix fois plus. `DAA_DICTIONARY_MAX_CHARS`
(8 000 caractères, ≈ 2 550 tokens) borne ce qui entre dans le prompt. *(Il
s'appelait `DAA_RETRIEVAL_DICTIONARY_MAX_CHARS` tant qu'un seul agent lisait le
dictionnaire ; l'ancien nom reste accepté et se signale comme déprécié — voir la
section 3.)* Au-delà,
la coupe garde des **sections Markdown entières**, parcourues dans l'ordre du
document, et passe son chemin quand l'une ne tient pas dans ce qui reste. Jamais
au milieu d'une phrase : « la valeur -1 est une sentinelle » amputé de « à
écarter de toute moyenne » se lit comme une remarque et non comme une consigne.

Elle passe son chemin au lieu de s'arrêter, et c'est un choix mesuré : dans les
cinq dictionnaires de ce catalogue, les **pièges sont la dernière section**. Une
règle de préfixe jetterait donc systématiquement la seule partie dont la requête
a besoin, et garderait les fiches de colonnes que le schéma donne déjà.

Ce que la règle **ne** garantit pas, et il faut le dire : qu'aucun sens utile
n'est perdu. Une section écartée peut être celle qui comptait, et le Markdown
ne porte aucun signal de ce qui fait autorité sur une requête. C'est pourquoi
l'amputation n'est jamais muette — elle est écrite dans le prompt (le modèle
sait qu'il ne voit pas tout) **et** remontée à l'utilisateur par la trace, sur
le modèle de `Orchestrator._avis_de_troncature`. Remplacer un chiffre faux
silencieux par un dictionnaire amputé silencieux aurait déplacé le défaut, pas
corrigé.

Ce qu'on peut donc affirmer, et qui est vérifié par un test : **au plafond par
défaut, les cinq dictionnaires passent entiers** — la règle ne se déclenche
jamais sur ce catalogue, et n'y perd donc rien. Le plafond est choisi au-dessus
du plus gros d'entre eux (`exploitation`, 6 797 caractères) ; si l'un grossit au
point de le franchir, le test le dira avant l'utilisateur.

#### Ce que l'injection a cassé, et qu'il a fallu réparer

Le dictionnaire ne se contente pas de réparer : il **déplace** ce que le modèle
écrit, et pas toujours du bon côté. Trois régressions sont apparues au rejeu des
questions métier, toutes sur la même colonne, et toutes du même côté — **un
filtre de trop**.

| Question | Oracle | Avec le dictionnaire, premières versions |
|---|---|---|
| Q3 « quelle énergie totale, en kWh, a été délivrée sur l'année ? » | 1 757 519,23 | **1 730 823,72** — le modèle ajoutait `WHERE statut = 'T'` |
| Q1 « combien de sessions de recharge y a-t-il en tout ? » | 48 000 | **42 281** — même filtre |
| Q4 « les trois stations avec le plus de sessions » | 1 015 / 911 / 872 | **907 / 818 / 759** — même filtre, appliqué à un classement |

Le dictionnaire disait pourtant l'inverse pour l'énergie (« pour l'énergie
totale délivrée, les sessions `I` comptent »). **Trois formulations d'en-tête
successives n'y ont rien changé** — 0/3, puis 0/3 avec une consigne renforcée,
puis 0/3 encore avec un en-tête délibérément **neutre**, pour tester l'hypothèse
inverse. Ce n'était pas la consigne : c'était le dictionnaire lui-même. Sa
section « pièges » énonçait la règle de comptage en gras et rangeait le
contre-cas dans un paragraphe de fin. Impeccable pour un lecteur humain, et
trompeur pour un modèle de quatre milliards de paramètres, qui retient ce qui
est mis en avant.

**Le dictionnaire a désormais un second lecteur.** Il a été écrit pour une
personne ; il est maintenant lu par celui qui écrit le SQL. Le piège nº 1
énonce donc une **règle par défaut et une exception unique**, suivies de leurs
cas — sans rien changer à son sens :

> **Règle par défaut : AUCUN filtre sur `statut`.** Tout comptage, tout
> classement et toute somme portent sur les 48 000 sessions.
>
> **L'unique exception :** la question demande explicitement les recharges
> **abouties**. On pose alors `WHERE statut = 'T'`, et dans ce cas seulement.

La forme compte autant que le fond, et c'est mesuré : une **table de
correspondance à quatre entrées** disant la même chose passait Q1, Q2, Q3 et la
question du défaut nº 1, et rendait Q4 fausse sur vLLM ; la reformuler pour
couvrir les classements réparait Q4 et cassait Q3. Le modèle y cherchait la
ligne qui ressemble le plus à sa question. « Une règle, une exception » ne se
prête pas à cette lecture, et c'est elle qui tient.

*(La première de ces trois régressions, Q1, avait été vue sur Ollama le
15 septembre 2026 ; les deux autres et toutes les vérifications d'après sont sur
vLLM. Rien d'Ollama n'est rejoué ni comparé ici.)*

Après quoi les quatre questions qui se disputent cette colonne sont justes,
trois tirages chacune sur vLLM :

| Question | Attendu | Tirages |
|---|---|---|
| « combien de sessions en tout ? » | 48 000 | 3/3 |
| « combien au statut T en 2025 ? » | 42 281 | 3/3 |
| « quelle énergie totale délivrée ? » | 1 757 519,23 | 3/3 |
| « combien ont réellement abouti ? » | 42 281 | 3/3 |

Et Q4, rejouée dans sa conversation — celle où elle échouait — rend à nouveau
les trois stations sans filtre. Une nuance demeure, et elle n'est pas un
chiffre faux : **le modèle ne projette pas toujours la colonne des comptes**. La
question dit « donne leur code et leur nom », et il s'y tient parfois à la
lettre ; les trois stations et leur ordre sont justes dans tous les cas. La
campagne d'origine, elle, obtenait les comptes à chaque fois.

Un test tient cette précision du dictionnaire
(`test_le_code_de_statut_dit_quel_filtre_pour_quelle_question`) : il exige que
le piège nº 1 nomme le cas où l'on filtre **et** le cas où l'on ne filtre pas,
et cite les quatre chiffres que l'ambiguïté fait diverger. Il ne pèse aucune
tournure — il vérifie que l'ambiguïté reste levée.

C'est la leçon la plus utile de ce chantier : **porter le dictionnaire jusqu'au
SQL rend le dictionnaire responsable de ce que le SQL filtre.** Une ambiguïté
qu'un humain levait tout seul devient un chiffre faux, et le sens seul ne suffit
pas — la hiérarchie du texte compte. Un dictionnaire qui alimente un agent doit
dire quel filtre se pose pour quelle question, une règle par défaut d'abord et
ses exceptions ensuite.

### 2. `get_schema` n'acceptait aucun argument, et le budget de reprise vaut 1

L'outil `get_schema` de l'agent de récupération était sans paramètre. Le modèle
l'appelait avec `{"table_name": "..."}`, la validation refusait, il **réessayait
à l'identique**, et le tour mourait sur `UnexpectedModelBehavior: Tool
'get_schema' exceeded max retries count of 1`. Côté utilisateur : « Je n'ai pas
pu répondre : la source de données n'a pas pu être interrogée ».

Trace complète :

```
APPEL  list_tables  args={}
RETOUR list_tables  ['referentiel']
APPEL  get_schema   args={"table_name": "referentiel"}
RETRY  get_schema   [{'type': 'extra_forbidden', 'loc': ('table_name',), ...}]
APPEL  get_schema   args={"table_name": "referentiel"}      ← à l'identique
```

**Reproduit 9 fois sur 9 sur vLLM**, trois tirages sur chacune des trois sources
(`referentiel`, `interventions`, `telemetrie`) avec « combien de lignes en
tout ? ». Il reste intermittent côté utilisateur : une autre formulation passe
(« combien de lignes dans la table releves_puissance ? » → 547 200), ce qui est
plus pénible qu'une panne franche.

*(Trace historique du 15 septembre 2026, plus rejouée : le même modèle servi par
Ollama ne reproduisait PAS ce défaut — 0 fois sur 19 tirages, l'appel partant
toujours avec `{}`. Le déclencheur tenait donc au gabarit d'appel d'outil du
moteur et non au modèle. Ce constat n'est plus vérifié et ne sert plus de
référence ; il reste noté parce qu'il a pesé dans l'arbitrage ci-dessous.)*

#### Le déclencheur disparaît de lui-même — et c'est la raison de ne pas s'y fier

Mesure faite après le correctif du défaut nº 1, l'outil laissé **exactement
comme avant** (aucun argument, budget de reprise à 1), sur vLLM, trois tirages
par source :

| Ce qui est en place | Tours aboutis | Reprises d'outil |
|---|---|---|
| L'outil d'avant, **sans** dictionnaire dans le prompt (témoin) | **0/9** | 9 |
| L'outil d'avant, **avec** le dictionnaire | **9/9** | 0 |

Injecter mille à deux mille tokens de plus a suffi à faire cesser l'invention de
`table_name`. Le défaut nº 2 n'est donc pas une propriété stable du modèle :
c'est une réaction à un prompt donné. Un prompt qui rebouge — un autre
dictionnaire, une autre source, une autre version du moteur — peut le ramener,
et personne ne le verra venir.

#### Les trois correctifs possibles, mesurés côte à côte

Aucun n'a été tranché par principe. Tous mesurés sur vLLM, trois tirages sur
chacune des trois sources, dictionnaire injecté sauf mention contraire.

| Variante | Tours aboutis | Appels LLM | Réponse sur `telemetrie` |
|---|---|---|---|
| **A** — l'outil **accepte** un `table_name` facultatif | **9/9** | 51 | les trois comptes, justes (380 / 240 / 547 200) |
| **B** — l'outil n'en prend aucun, le prompt l'**interdit** explicitement | 9/9 | 51 | « **934 820** lignes » ✘ (l'oracle est 547 820) |
| **C** — l'outil n'en prend aucun, budget de reprise porté à 2 | 9/9 | 51 | « **934 820** lignes » ✘ |
| *témoin* — l'outil d'avant, budget à 3, **sans** dictionnaire | 6/9 | 63 | échec 3/3, après quatre `get_schema` brûlés |

**A est retenue.** Trois raisons, dans cet ordre :

1. C'est la seule qui **supprime la possibilité** de l'échec au lieu de parier
   sur le comportement du modèle devant un prompt donné. Le témoin ci-dessus
   montre que ce comportement bouge ; B et C reposent entièrement dessus.
2. Elle ne coûte rien. Mesuré : le modèle passe `{}` dans les 15 tirages
   d'après-correctif — l'argument est un filet, jamais un détour.
3. B inscrirait une contrainte permanente dans le prompt de l'agent le plus
   chargé, pour un défaut qui est une propriété de la **signature d'un outil**.

Les variantes B, C et le témoin partagent en outre un défaut que A n'a pas :
faute de pouvoir demander le schéma, le modèle enchaîne `list_tables` puis trois
comptages séparés, et annonce en prose un total de **934 820** là où la somme
est 547 820. Un chiffre faux et plausible, encore. La causalité est indirecte —
c'est la liste d'outils qui change, donc le plan du modèle — et elle est
rapportée telle qu'observée, 3/3 sur chacune des trois variantes.

**Le budget de reprise reste à 1**, et la mesure le justifie. Le porter à 3 ne
répare pas : il fait passer 6 tours sur 9 au prix de **deux allers-retours de
plus par tour** (5 → 7), pris sur `retrieval_request_limit`, et `telemetrie`
échoue quand même après avoir brûlé quatre appels à `get_schema`. Réessayer à
l'identique est bien un tour dépensé pour rien ; la réparation est de ne pas
créer l'erreur de validation, pas d'en absorber davantage.

**Un nom de table inconnu ne lève pas non plus.** Il rend le schéma complet en
disant que le nom n'existe pas et en listant ceux qui existent — même choix que
`run_sql`, qui rend son erreur SQL en texte : une boucle de correction se
nourrit de ce qu'on lui rend.

#### Après

« Combien de lignes en tout ? », sur vLLM — trois tirages avant, cinq après :

| Source | Avant | Après | Oracle |
|---|---|---|---|
| `referentiel` | 0/3 — tour mort | **5/5** — 150 | 150 |
| `interventions` | 0/3 — tour mort | **5/5** — 900 | 900 |
| `telemetrie` | 0/3 — tour mort | **5/5** — 380 / 547 200 / 240 | 380 / 547 200 / 240 |

Aucune reprise d'outil sur les quinze tours d'après. Sur `telemetrie`, la réponse
est le tableau des trois comptes et non leur somme : les valeurs sont justes,
l'agrégation est laissée à l'utilisateur — « en tout » sur une source à trois
tables n'a pas de lecture unique.

### 3. Le dictionnaire ne descendait pas non plus jusqu'à l'agent d'analyse

Réparé pour celui qui écrit le SQL, le défaut restait entier pour celui qui
écrit le **Python**. L'agent d'analyse recevait les CSV matérialisés, le schéma,
l'avis de troncature — et rien de ce que les valeurs veulent dire.

**Et c'est pire ici, pour une raison qui tient au support du résultat.** Une
requête fausse laisse son texte dans la conversation : on la relit, on voit le
`WHERE` manquant. Un `df['puissance_kw'].mean()` faux ne laisse qu'un nombre —
ou une courbe, et personne ne relit une courbe.

#### Les oracles, établis en SQL direct avant toute mesure d'agent

Le piège nº 2 : `telemetrie.releves_puissance.puissance_kw` vaut `-1.0` quand le
compteur n'a rien remonté, et `0.0` quand la borne est à l'arrêt — la première
est une absence de mesure, la seconde une mesure vraie.

**Deux assiettes, et il faut les deux** : l'agent SQL interroge la table
entière, le code d'analyse ne reçoit que ce que `analysis_table_max_rows`
(10 000) a matérialisé en CSV. Confondre leurs oracles ferait passer pour faux
un chiffre juste.

| Assiette | Lignes | `= -1` | `= 0` | Moyenne naïve | Moyenne juste (`-1` écartés, `0` gardés) | Sur-corrigée (`> 0`) |
|---|---|---|---|---|---|---|
| table entière | 547 200 | 16 447 | 44 197 | 66,6663 | **68,7631** | 75,0093 |
| tranche montée | 10 000 | 290 | 806 | 66,3215 | **68,3321** | 74,5176 |

*(44 197 et non 44 187 : le tirage force 44 187 relevés à zéro, et dix autres
tombent sur `0.00` d'eux-mêmes. Le compte du semis se lit maintenant dans la
donnée écrite, pas dans le masque qui l'a produite — c'est la base que l'agent
interroge.)*

#### Avant et après, cinq tirages par question

Runner : [`scripts/mesure_dictionnaire_analyse.py`](../scripts/mesure_dictionnaire_analyse.py).
Chaque tirage est une conversation neuve, sur `telemetrie`.

| Question | Nœud | Avant | Après |
|---|---|---|---|
| « quelle est la puissance moyenne relevée ? » | `retrieval` | **5/5 juste** — 68,76 | **5/5 juste** — 68,76 |
| « trace-moi la puissance moyenne par heure » | `analysis` | **0/5** — sentinelle gardée, réponse à 66,32 | **5/5** — sentinelle écartée, zéros gardés, réponse à 68,332 |

La première question **ne passe jamais par l'agent d'analyse** : le
planificateur la route vers le SQL, qui a son dictionnaire depuis le chantier
précédent. Elle est mesurée quand même, et c'est utile — elle montre que le
défaut n'est pas dans la question, mais dans l'agent qui la traite.

Le code d'avant faisait `releves.dropna(...)` puis `.mean()` : `dropna` ne voit
pas un `-1`, ce n'est pas un NaN, c'est un nombre. Le code d'après écrit le
filtre en clair et cite la règle en commentaire — c'est ce que l'en-tête lui
demande :

<!-- Balisé `text` et non `python` : c'est une CITATION du code rendu par le
     modèle, et `ruff format` reformate les blocs Python des fichiers Markdown —
     il réécrirait les apostrophes, et la citation cesserait d'être exacte. -->

```text
# Règle : "-1 est une valeur sentinelle ... à écarter de toute moyenne."
# Règle : "0 en est une, elle : la borne répond et ne charge personne." (à conserver)
releves_filtre = releves[releves['puissance_kw'] != -1.0].copy()
```

**Les figures ont été regardées, pas seulement leur code.** Cinq sur cinq sont
tracées et non vides, avant comme après ; ce qui change est le niveau de la
courbe, deux kilowatts plus haut. Un `statut ok` du bac à sable ne dit rien de
ce qui est dessiné.

**Ce que ça coûte, en allers-retours.** Le dictionnaire déplace aussi
l'interprétation : sans lui, quatre tirages sur cinq lisaient « par heure »
comme l'heure du jour (`groupby(dt.hour)`, 24 points) ; avec lui — il désigne
`horodatage` comme colonne de référence, au pas horaire — les cinq lisent une
série temporelle (`resample('h')`, 1 440 points). Cette seconde lecture bute une
fois sur l'alias `'H'` déprécié de pandas et se corrige d'elle-même : **deux
essais au lieu d'un**, dans une boucle qui en autorise trois. La réponse est
juste dans les deux lectures ; la question est ambiguë et le coût est réel.

**Une règle de prompt a été essayée pour récupérer cet aller-retour, et elle est
retirée.** Dire au modèle que pandas 2 a retiré les alias de fréquence en
majuscule le lui fait bien écrire en minuscule du premier coup — et ne récupère
rien : les cinq tirages tiennent toujours en deux essais, sur une autre erreur,
et le tour passe de 34 s à 51 s parce que le code produit devient une agrégation
par borne **puis** par heure. Une consigne qui déplace la faute sans la retirer,
et qui coûte la moitié du temps du tour, ne vaut pas d'être gardée. C'est, à une
autre échelle, la leçon de la section suivante.

#### Le module de coupe est partagé, pas dupliqué

`agents/retrieval/dictionnaire` est devenu
[`agents/dictionnaire`](../src/data_analyst_agent/agents/dictionnaire.py) : il a
deux lecteurs. `preparer` taille, `bloc_de_prompt` colle, et **l'en-tête est
passé par l'appelant** — `EN_TETE_SQL` parle de requête et de `WHERE`,
`EN_TETE_CODE` de `mean()`, de `dropna` et de courbe.

La machinerie est commune ; le vocabulaire ne l'est pas, et ce n'est pas une
politesse. Un en-tête qui dit « avant d'écrire ta requête » devant un agent qui
n'écrit jamais de requête lui demande de transposer, et la transposition est
l'opération qu'il rate : il lit « à écarter de toute moyenne », pense SQL, et
écrit `df['x'].mean()` sans filtre.

**Le plafond est partagé lui aussi, et renommé pour le dire.**
`DAA_DICTIONARY_MAX_CHARS` règle ce qu'une **source** transmet de son sens, pas
le coût d'un agent : deux valeurs distinctes diraient qu'un même dictionnaire
est amputé dans un prompt et entier dans l'autre — donc que « combien de
recharges ? » et « trace-moi les recharges » ne s'appuient pas sur le même
texte. L'ancien nom, `DAA_RETRIEVAL_DICTIONARY_MAX_CHARS`, reste lu et se
signale comme déprécié : un plafond qui redeviendrait son défaut en silence,
c'est un contexte qui gonfle sans que personne l'ait décidé.

#### Ce que ça coûte, en tokens rendus par le serveur

Mesuré au `/tokenize` de vLLM, comme pour l'agent SQL.

| Prompt système | Nu | + `telemetrie` | + `exploitation` (le plus gros) |
|---|---|---|---|
| agent d'analyse | 300 tokens | **2 199** | **2 943** |
| agent SQL | 824 | 2 580 | 3 324 |

Le prompt de l'analyse est le plus court des deux à vide et le reste chargé. Au
plafond de 8 000 caractères (≈ 2 550 tokens sur ce Markdown), il est borné à
~3 400 tokens et celui du SQL à ~3 800 — pour une fenêtre servie de 32 768.

#### L'amputation remonte des deux côtés, et les deux se cumulent

Le nœud d'analyse peut désormais livrer **deux** dégradations : des LIGNES
coupées à la matérialisation (`analysis_table_max_rows`) et des SECTIONS coupées
du dictionnaire. L'une fait compter sur un échantillon, l'autre fait compter
sans la règle ; elles s'additionnent dans la trace et dans la réponse, et un
test le tient.

### Un dictionnaire écrit pour un humain tient-il ? — la contrainte de produit

C'est la question qui décide si le produit tient chez un client : **un client
n'écrira jamais son dictionnaire pour notre agent.** Il l'écrira comme celui
d'`exploitation` avant sa réécriture.

Le texte d'avant a donc été remis en place — en mémoire, sans rien écrire dans
le dépôt — et mesuré tel quel, avec la consigne en service. Quatre questions,
trois tirages chacune, verdict lu dans le chiffre ET dans le SQL :

| Question | Oracle | Obtenu | Tirages justes |
|---|---|---|---|
| « combien de sessions en tout ? » | 48 000 | 48 000 | **3/3** |
| « quelle énergie totale, en kWh ? » | 1 757 519,23 | **1 730 823,72**, avec `WHERE statut = 'T'` | **0/3** |
| « les trois stations avec le plus de sessions » | 1 015 / 911 / 872 | les trois, dans l'ordre, sans leurs comptes | 3/3 au SQL |
| « combien de recharges ont abouti ? » | 42 281 | 42 281 | **3/3** |

Puis quatre formulations de consigne ont été essayées sur la question fausse,
après les trois du chantier précédent — **sept en tout, 0/3 chacune**. La
sixième est pire que le défaut : sommée de dérouler une procédure en
commentaires, le modèle l'écrit et **n'appelle plus l'outil**. La septième,
placée APRÈS le dictionnaire là où la récence joue, fait disparaître le filtre
sur `statut` et le remplace par un filtre sur l'année en cours — la réponse
devient « énergie nulle ». **Le prompt décide de quelle faute est commise ; il
ne décide pas qu'aucune ne l'est.**

Le témoin qui tranche : même consigne, même question, même moteur, seul le
dictionnaire change — **0/3 sur le texte d'avant, 3/3 sur le texte réécrit**.

C'est donc **(b)** : ce n'est plus un défaut de code, c'est une contrainte de
produit, et elle est écrite dans un document à part —
[**Rédiger un dictionnaire de source pour cet agent**](rediger-un-dictionnaire-de-source.md).
Six règles, le relevé complet des sept formulations, ce que l'ambiguïté coûte en
chiffres faux, et une liste de contrôle. Le dictionnaire d'`exploitation` est
resté dans sa version réécrite.

### La colonne des comptes qui disparaissait

`Q4` demande « les trois stations avec le plus de sessions ? **donne leur code
et leur nom** », et le modèle s'y tenait à la lettre : code et nom dans le
`SELECT`, le compte seulement dans le `ORDER BY`. Mesuré 5 fois sur 5, sur le
dictionnaire en service. L'ordre et les stations étaient justes, le SQL n'était
pas filtré — une réponse littérale, pas un chiffre faux.

Le remède tient en une ligne du prompt de l'agent SQL : la grandeur qui **classe**
va dans le `SELECT`, et les colonnes que la question énumère sont un minimum,
pas un maximum. **0/5 avant, 5/5 après**, avec 1 015 / 911 / 872 dans le
tableau. Le parcours métier et la surface conversationnelle ne bougent pas.

Il valait la peine d'être corrigé parce qu'un palmarès sans ses chiffres ne dit
pas de combien le premier devance le second, et ne se vérifie pas. Un test de
CONTENU tient cette ligne — le seul du fichier des prompts, parce qu'elle a un
avant et un après chiffrés et que la retirer ferait revenir le défaut sans que
rien n'échoue.

### Les garde-fous, et ce qui n'a pas bougé

`assert_read_only`, `retrieval_request_limit`, `retrieval_max_rows` et le bac à
sable au réseau coupé sont inchangés. La suite complète passe : **1 089 tests,
99,56 % de couverture** (référence d'avant : 1 074 et 99,56 % ; avant elle,
1 046 et 99,55 %).

La surface conversationnelle, rejouée sur vLLM contre `sources/catalogue.yaml`
(`titanic` + `iris`, qui ne déclarent aucun dictionnaire et dont le prompt est
donc inchangé au caractère près) : **36/36 questions méta et 4/4 témoins**.
L'oracle durci ne se desserre pas.

Le parcours métier complet, rejoué depuis un message utilisateur : **16/16 sur
vLLM, 56 appels LLM** — contre 69 à la campagne d'origine. L'économie vient des
trois tours d'ouverture, désormais servis sans aucun appel au modèle par le
court-circuit de choix de source.

Il se rejoue maintenant d'une commande :
[`scripts/mesure_parcours_de_demonstration.py`](../scripts/mesure_parcours_de_demonstration.py).
Il se refaisait à la main d'un chantier à l'autre, et une campagne qu'on refait
à la main ne se compare qu'à ce dont on se souvient. Chaque tour y est jugé sur
ce que l'utilisateur voit vraiment — **la phrase de réponse ET le tableau** : le
prompt de l'agent SQL lui interdit de recopier les lignes dans sa phrase, et ne
lire que la phrase ferait passer pour muet un tour qui a rendu ses chiffres.

**Un piège de protocole, trouvé en rejouant.** Les trois tours d'ouverture
échouaient d'abord, et c'était le banc de mesure qui avait tort : il passait
`source_de_travail=None` au premier tour d'un fil, là où l'API passe toujours
une chaîne (`Conversation.source_de_travail: str = ""`). Avec `None`, le
court-circuit déterministe de `_choix_de_source` ne se déclenche jamais et un
message qui ne porte qu'un nom de source ne lie rien — un chemin que le produit
ne prend pas. Un banc qui n'imite pas fidèlement l'appelant réel fabrique des
défauts qui n'existent que pour lui, et fait perdre le temps qu'on croyait
gagner.

**Ce qui restait à traiter est traité.** La colonne des comptes absente de Q4
était la réserve laissée par le chantier précédent ; elle est mesurée et
corrigée plus haut, 0/5 avant et 5/5 après.

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
18 tests sur la déclaration, les deux CSV, la correspondance libellé → code, le
gel du classeur, et le fait que le piège nº 1 dise quel filtre pour quelle
question.
