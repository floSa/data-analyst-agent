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

**Et ce 16/16 mesure seize PHRASES, pas seize questions** — celles de la colonne
« Question » ci-dessous, que nous avons écrites nous-mêmes. Chaque tour porte
depuis deux paraphrases d'utilisateur, même intention et même oracle, et le
score réel des quarante-huit est plus bas : cf. « Seize questions,
quarante-huit phrases ».

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

**La cause de cet aller-retour est lue, pas devinée, et ce n'est pas le
dictionnaire.** `AnalysisResult` ne garde que le DERNIER code et la dernière
exécution : le premier essai — celui dont on veut savoir pourquoi il échoue —
n'y figure pas. Une sonde posée sur `SandboxSession.execute` l'enregistre. Trois
tirages, trois fois le même couple, au caractère près :

```text
essai 1 (error) : releves_filtre['heure'] = releves_filtre['horodatage'].dt.floor('H')
                  ValueError: Invalid frequency: H. ... Did you mean h?
essai 2 (ok)    : # CORRECTION: Le message d'erreur indique que 'H' est invalide et suggère 'h'.
                  releves_filtre['heure'] = releves_filtre['horodatage'].dt.floor('h')
```

**Une majuscule.** pandas a retiré les alias de fréquence capitalisés ; le modèle
les écrit encore. Le dictionnaire n'est pour rien dans cette faute — il ne fait
que conduire le code vers un arrondi temporel, là où « par heure » lu comme
l'heure du jour (`dt.hour`) n'en touche aucun. Retirer quoi que ce soit au
dictionnaire ne retirerait pas la majuscule.

**Elle n'est pas corrigée, et voici pourquoi.** La seule correction possible sans
toucher au dictionnaire est une consigne de prompt qui nomme l'alias. Elle a été
essayée et mesurée au chantier précédent : le modèle écrit bien la minuscule du
premier coup, les cinq tirages tiennent toujours en deux essais **sur une autre
erreur**, et le tour passe de 34 s à 51 s. Ce serait, une fois de plus, un pari
de prompt sur un détail de bibliothèque — exactement ce que la section « La colonne des comptes qui
disparaissait » vient de retirer du produit.

Le mécanisme qui répare réellement existe déjà, et il est structurel : la boucle
de self-debug renvoie au modèle l'erreur RÉELLE, qui contient ici la correction
en toutes lettres (« Did you mean h? »), et le second essai passe 3 fois sur 3.
Un aller-retour de plus, dans une boucle qui en autorise trois, n'est pas un
chiffre faux. Il est recensé, il n'est pas payé deux fois.

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

### La colonne des comptes qui disparaissait, et ce que « 0/5 → 5/5 » ne disait pas

`Q4` demande « les trois stations avec le plus de sessions ? **donne leur code
et leur nom** », et le modèle s'y tenait à la lettre : code et nom dans le
`SELECT`, le compte seulement dans le `ORDER BY`. L'ordre et les stations
étaient justes, le SQL n'était pas filtré — une réponse littérale, pas un
chiffre faux, mais un palmarès sans ses chiffres ne dit pas de combien le
premier devance le second, et ne se vérifie pas.

Le remède posé alors tenait en une ligne du prompt de l'agent SQL, et cette
ligne **énumérait des tournures** : « les trois plus… », « le plus gros… »,
« classe par… ». **0/5 avant, 5/5 après** — sur la phrase qui l'avait motivée.

Remesurée sur une SECONDE formulation de la même demande — « les trois stations
**les plus sollicitées** : donne leur code et leur nom » — elle rend **0/3**. La
tournure n'est pas dans la liste.

C'est le pari que ce produit a déjà payé une fois : le lexique de mots-clés du
planificateur plafonnait à 3 tournures sur 10 et a dû être remplacé par des
outils que le modèle appelle. Une règle de prompt qui énumère des tournures est
le même pari, déplacé d'un cran.

#### Dix façons de demander le même palmarès

Runner :
[`scripts/mesure_classement_sans_lexique.py`](../scripts/mesure_classement_sans_lexique.py).
Dix formulations d'utilisateur de la MÊME demande sur `exploitation`, trois
tirages chacune, et **deux verdicts par tirage** :

- **le SQL**, jugé par [`agents/retrieval/classement`](../src/data_analyst_agent/agents/retrieval/classement.py) :
  toute expression du `ORDER BY` figure-t-elle dans le `SELECT` ? C'est la
  propriété qu'on veut, énoncée sans regarder la question — donc mesurable
  quelle que soit la phrase ;
- **ce que l'utilisateur voit** : la réponse ET le tableau portent-ils les trois
  stations et leurs trois comptes ?

Les deux sont nécessaires. Un SQL en règle qui classe sur la mauvaise grandeur
reste un tour faux, et ce n'est pas le même défaut.

#### Quatre états, mesurés côte à côte

| | SQL en règle | oracle | appels LLM | tours relancés |
|---|---|---|---|---|
| **(0)** la règle de C31, qui énumère des tournures | 27/30 | 21/30 | 150 | — |
| **(a)** la règle reformulée : elle porte sur la REQUÊTE, pas sur les mots | **12/30** | 9/30 | 150 | — |
| **(b)** vérification structurelle, AUCUNE règle de prompt | **30/30** | 21/30 | 164 | 14/30 |
| **(b) + (0)** la vérification, et la règle gardée | **30/30** | **24/30** | 153 | **3/30** |

Les appels LLM sont comptés par le runner, un par requête au serveur ; « tours
relancés » est leur écart à 5 appels, qui est le coût d'un tour sans relance.
Aucune durée n'est citée : deux de ces campagnes ont partagé le GPU avec une
autre mesure, et un temps de tour ne se compare qu'à charge égale.

**La voie (a) est un échec net, et c'est le résultat le plus instructif.** Dire
la règle en propriété abstraite — « toute expression de ton ORDER BY figure
aussi dans ton SELECT » — au lieu d'énumérer des tournures fait passer de 27/30
à 12/30. Quatre formulations qui passaient cessent de passer. On ne remplace pas
une liste de mots par une phrase mieux tournée : le prompt ne tient pas cette
propriété, quelle que soit sa rédaction.

**La voie (b) la tient, et sans regarder la question.** La vérification lit le
SQL produit, repère les expressions du `ORDER BY` absentes du `SELECT`, et
renvoie la remarque au modèle **en même temps que son résultat** — le tableau
est déjà calculé et il est juste, le retenir pour forcer une correction
transformerait un palmarès sans ses chiffres en tour mort. Une relance au plus
par récupération, sur `retrieval_request_limit` : la redire à chaque requête
dépenserait le budget sur une remarque déjà lue.

**Une porte de sortie a été essayée, et retirée.** La première rédaction offrait
au modèle de garder son résultat « si le tri ne sert qu'à rendre la sortie
lisible ». Il a pris cette sortie **3 fois sur 3** sur la question même qui a
motivé tout ceci, et la relance ne réparait rien. Une consigne qui propose de ne
rien faire se fait suivre à la lettre.

#### Ce qui est gardé, et pourquoi les deux

**La vérification est la garantie ; la règle de prompt est devenue une
économie.** C'est la mesure qui range les deux dans cet ordre : la vérification
rend 30/30 avec ou sans la règle, et la règle fait écrire la projection juste du
premier coup plus souvent — **3 relances au lieu de 14** sur trente tirages, soit
153 appels LLM contre 164.

Une consigne de prompt qui ne garantit rien mais qui fait gagner 7 % d'appels
vaut d'être gardée. Ce qu'il fallait retirer, ce n'est pas la ligne : c'est
l'idée qu'elle suffisait. Le test qui la tenait dans
[`tests/unit/test_prompts.py`](../tests/unit/test_prompts.py) le dit maintenant
dans ces termes.

#### Deux autres familles, que ce correctif ne touche pas

Trois formulations sur dix restent fausses contre l'oracle de `Q4`, avec un SQL
**parfaitement en règle** — ce ne sont pas des palmarès sans chiffres, ce sont
des palmarès d'autre chose :

| formulation | ce que le modèle a classé | lecture |
|---|---|---|
| « **qui charge le plus** ? je veux les 3 premières stations… » | `SUM(energie_kwh)` | défendable : « charger » se lit en kilowattheures |
| « **où est-ce qu'on recharge le plus** ? … » | `SUM(energie_kwh)` | idem |
| « sur quelles stations y a-t-il eu le plus de **recharges** ? … » | `COUNT(*)` avec `WHERE statut = 'T'` | défendable : le dictionnaire dit que seules les sessions `'T'` sont des recharges abouties |

La troisième est la plus intéressante : le modèle applique la définition que la
source déclare, et l'oracle de `Q4` — qui compte TOUTES les sessions — est celui
qui a tort pour cette phrase-là. Ces trois écarts sont recensés en dette : ils ne
relèvent pas de la cause réparée ici, et les corriger demanderait de décider ce
qu'une question ambiguë veut dire, ce qui n'est pas une décision de code.

### Seize questions, quarante-huit phrases — ce que le 16/16 mesurait

Le parcours ci-dessus est **16/16**. Il est mesuré sur seize phrases que nous
avons écrites nous-mêmes, une par question. La section précédente montre ce que
ce chiffre garantit : la phrase, pas la question.

Chacun des seize tours porte donc maintenant **deux paraphrases** en plus de la
sienne — même intention, même oracle, autre formulation : l'une courte et
familière, l'autre longue et polie. Quarante-huit questions, trois tirages
chacune, une commande :

```bash
DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml \
  uv run python scripts/mesure_parcours_de_demonstration.py --tirages 3
```

**Le score réel : 44 questions sur 48** (132 tirages conformes sur 144).

| formulation | score | ce qui tombe |
|---|---|---|
| **canonique** — nos seize phrases | **48/48** | rien : le 16/16 se reproduit à trois tirages |
| **courte** — « statut dans sessions, ça veut dire quoi ? » | **45/48** | une question |
| **longue** — « Pourrais-tu m'ouvrir la source telemetrie… ? » | **39/48** | trois questions |

Les douze échecs sont **quatre questions × trois tirages**, identiques d'un
tirage à l'autre. Ce n'est pas du bruit de modèle : ce sont quatre défauts
déterministes que seize phrases ne pouvaient pas voir.

#### Les familles d'écart

**A — l'ouverture polie perd le court-circuit de source.** Trois questions, neuf
tirages. Un message réduit au nom d'une source (« telemetrie ») — ou presque
(« passe sur telemetrie », « facturation, vas-y ») — déclenche le court-circuit
déterministe de `_choix_de_source`, qui lie la source et l'annonce avec ses
tables : « Entendu : on travaille sur **telemetrie** … 3 table(s), 547 820
ligne(s) ». Une phrase polie ne le déclenche pas, et part à l'agent système, qui
répond par l'inventaire des tables et de leurs colonnes — utile, mais il ne
COMPTE pas les tables, et la source n'est pas liée pour le tour suivant.

Le pire des trois est `verrou-ouverture` : « J'aimerais reprendre le travail sur
la source exploitation, peux-tu la charger ? » est routée vers la récupération,
qui répond honnêtement « Je n'ai pas interrogé la source pour cette question,
je ne peux donc rien en affirmer. Reformule ». La réserve de forme du document
disait que la question doit nommer sa source ; il faut y ajouter qu'un message
d'ouverture doit s'y RÉDUIRE.

`ouverture-exploitation·longue` passe, et c'est un faux succès instructif :
l'agent système a rendu l'inventaire des CINQ sources, où « 6 table(s) »
figure pour `exploitation`. L'oracle est satisfait par un chemin qui n'est pas
celui qu'on mesure.

**B — une question courte sur le sens d'une colonne ne cite plus sa source.**
Une question, trois tirages. « statut dans sessions, ça veut dire quoi ? » part
à la récupération au lieu de l'agent système, et la récupération répond
JUSTE — « 'T' : Terminée, la recharge a abouti ; 'I' : Interrompue… », contenu
qui ne peut venir que du dictionnaire, qu'elle a dans son prompt. Elle ne dit
simplement pas « selon le dictionnaire de `exploitation` ». L'oracle exige cette
attribution, et il a raison de l'exiger : c'est elle qui distingue une lecture
de la source d'un savoir général sur les codes de statut. Le chiffre n'est pas
faux ; la provenance n'est pas dite.

#### Ce que la vérification de classement coûte sur ce parcours : rien

Les quarante-huit questions ont été rejouées DEUX fois, trois tirages chacune —
avant la vérification structurelle et après. **132/144 et 540 appels LLM des
deux côtés**, aux mêmes quatre questions près. La relance ne se déclenche sur
aucun des quarante-huit tours : la consigne de prompt suffit à faire projeter la
grandeur sur ces phrases-là, et la vérification n'a rien à redire.

C'est la réponse à « qu'est-ce que ça casse ailleurs ». Sur la seule campagne où
elle se déclenche — les dix formulations de classement — elle coûte 3 relances
sur 30 tirages. Partout ailleurs, elle est un filet qu'on ne sent pas.

#### Ce qui est corrigé ici, et ce qui part en dette

Rien de ces deux familles n'est corrigé dans ce chantier : **aucune ne relève de
la cause du sujet 1**, et les traiter demanderait de rouvrir le routage des
questions — le court-circuit de source pour A, la frontière entre agent système
et récupération pour B. Les deux sont recensées, avec leur trace, comme matériau
des chantiers suivants :

| dette | portée | trace |
|---|---|---|
| **A** — le court-circuit de source ne reconnaît qu'un message réduit au nom d'une source | 3 questions sur 48, déterministe | `ouverture-telemetrie·longue`, `ouverture-facturation·longue`, `verrou-ouverture·longue` |
| **B** — une question de sens formulée court est routée vers la récupération, qui répond juste sans citer le dictionnaire | 1 question sur 48, déterministe | `sens-statut·courte` |
| **C** — trois formulations de classement choisissent une autre grandeur (énergie) ou appliquent le filtre `statut = 'T'` du dictionnaire | 3 formulations sur 10 du runner de classement | `F03`, `F07`, `F10` de `mesure_classement_sans_lexique` |

**Les trois ont été reprises depuis** : voir [Les trois dettes de routage,
reprises une à une](#les-trois-dettes-de-routage-reprises-une-à-une) plus bas.

### Les trois dettes de routage, reprises une à une

Le chantier précédent a laissé trois dettes nommées, avec leur trace et leur
runner. Elles sont reprises ici, et **la règle qui a commandé tout le travail
est la même que celle qui a fait retirer le lexique de l'introspection** : ce
produit a payé deux fois le pari d'une liste de tournures — un lexique de
mots-clés dans le planificateur (3 sur 10), une règle de prompt qui énumère des
formulations de classement (tenue sur la phrase mesurée, 0/3 sur une
paraphrase). Aucune des trois dettes n'est donc réparée par une énumération de
mots de la question, et chaque sujet exige des formulations **écrites après le
correctif** — la seule façon de savoir si on a réparé l'intention ou la phrase.

Deux runners sont nés de ce chantier, et le troisième a été retourné :

| sujet | runner | ce qu'il mesure |
|---|---|---|
| A | [`mesure_ouverture_de_source.py`](../scripts/mesure_ouverture_de_source.py) | dix ouvertures, quatre contre-épreuves, trois tours de verrou |
| B | [`mesure_provenance_du_sens.py`](../scripts/mesure_provenance_du_sens.py) | six questions de sens, quatre témoins sans dictionnaire |
| C | [`mesure_classement_sans_lexique.py`](../scripts/mesure_classement_sans_lexique.py) | les dix formulations, avec l'oracle arbitré et son `--oracle-davant` |

Et le bilan des trois, mesuré sur vLLM, trois tirages partout :

| dette | avant | après | ce qui l'a réparée |
|---|---|---|---|
| **A** — l'ouverture polie perd le court-circuit de source | 18/51 | **51/51** | un sixième outil de l'agent système + la frontière du prompt dite comme une propriété ; l'outil appelé sans nom rend l'inventaire, et les ouvertures passent de 27/30 à 30/30 |
| **B** — une réponse juste dont la provenance n'est pas dite | 21/30 | **27/30** (30/30 sur l'oracle arbitré) | la récupération attribue quand elle répond depuis le dictionnaire |
| **C** — trois formulations qui choisissent autre chose | 24/30 | **30/30** | la phrase nomme la grandeur classée, et l'oracle est arbitré sur deux lignes |

### Dette A — l'ouverture polie, et les deux voies mesurées avant de trancher

Le défaut, reproduit d'abord : le court-circuit déterministe de
`_choix_de_source` ne reconnaît qu'un message **réduit** au nom d'une source.
Une phrase qui demande la même chose part ailleurs, et les trois traces nommées
au chantier précédent se rejouent à l'identique —
`ouverture-telemetrie·longue` et `ouverture-facturation·longue` reçoivent
l'inventaire des tables et de leurs colonnes, que l'agent système ne COMPTE pas ;
`verrou-ouverture·longue` part à la récupération et s'y fait répondre « Je n'ai
pas interrogé la source pour cette question, je ne peux donc rien en affirmer.
Reformule ».

Un runner a été écrit pour ce sujet, et ses **dix formulations d'ouverture ont
été écrites après le correctif** :
[`scripts/mesure_ouverture_de_source.py`](../scripts/mesure_ouverture_de_source.py).
Trois volets, cinquante-un tours à trois tirages :

- **ouverture** — dix phrases, cinq sources, familières, polies, longues, avec
  ou sans le mot « source ». L'oracle ne se lit pas dans le texte : la source
  doit être **liée au fil** après le tour, et l'accusé de réception doit porter
  le volume lu dans la source. C'est ce qui distingue une ouverture réussie
  d'une réponse bien tournée qui n'a rien retenu — le faux succès de
  `ouverture-exploitation·longue`, où l'agent système avait énuméré les CINQ
  sources et où « 6 table(s) » figurait par accident ;
- **contre-épreuve** — quatre phrases d'ouverture SUIVIES d'une vraie question.
  Un second chemin de liaison qui les avale répare dix tours en cassant quatre,
  et il faut le savoir avant de le garder. Le nœud `retrieval` doit être
  atteint : sans cette exigence, deux des quatre passaient à tort, parce que
  l'accueil d'une source porte son volume table par table et que « 1 200
  factures » s'y trouve déjà ;
- **verrou** — la source liée par une PHRASE, puis les deux tours de C15 : la
  question qui ne nomme personne, et celle qui nomme l'autre source.

#### Les deux voies, et ce que chacune coûte

**Voie 1 — le planificateur porte l'intention.** Une cinquième valeur de
`Capability` (`bind_source`), décrite dans le prompt du planificateur comme une
propriété et non comme une liste de tournures : *retire le nom de la source du
message ; s'il ne reste aucune question à laquelle une requête, un calcul ou une
prédiction répondrait, c'est une liaison.* La source liée est celle que
l'utilisateur a NOMMÉE, jamais celle que le planificateur a choisie.

**Voie 2 — un outil que le modèle appelle.** Un sixième outil de l'agent
système, `travailler_sur_une_source`, sur le modèle exact des cinq autres : le
modèle décide d'appeler, l'outil rend la fiche de la source, le nœud lie. C'est
le mécanisme qui a déjà remplacé le lexique de tournures de l'introspection.

Les deux ont été mesurées **seules**, sur le même runner, trois tirages :

| état | ouverture | contre-épreuve | verrou | total | appels LLM |
|---|---|---|---|---|---|
| **avant** | 3/30 | 12/12 | 3/9 | **18/51** | 202 |
| **voie 1 seule** | 30/30 | 3/12 | 3/9 | **36/51** | 114 |
| **voie 2 seule** | 15/30 | 12/12 | 9/9 | **36/51** | 195 |
| **voie 2 + la frontière dite dans le prompt système** | 27/30 | 12/12 | 9/9 | **48/51** | 165 |
| **et l'outil appelé sans nom rend l'inventaire** — l'état retenu | **30/30** | 12/12 | 9/9 | **51/51** | 156 |

Les deux voies rendent le même total, et c'est un hasard : elles échouent aux
deux bouts opposés.

**La voie 1 répare toutes les ouvertures et AVALE les questions.** Neuf tours de
contre-épreuve sur douze partent en accusé de réception sans qu'aucune requête
soit lancée : le planificateur lit « mets-moi sur telemetrie ; il y a combien de
lignes dans releves_puissance ? » comme une liaison et jette la question. La
propriété écrite dans le prompt — *s'il reste une question, classe selon ELLE* —
n'est pas suivie. C'est une régression sur un comportement qui marchait,
et c'est ce qui la disqualifie : on ne casse pas quatre questions pour en
réparer dix.

**La voie 2 ne coûte rien à la contre-épreuve et répare le verrou.** Douze sur
douze des deux côtés, et 9/9 au verrou contre 3/9 avant — parce que
`V1-ouvre` (« Pourrais-tu te mettre sur la source exploitation, s'il te
plaît ? ») était intercepté par l'agent système, qui répondait sans rien lier ;
les deux tours suivants héritaient d'un fil sans source. Elle ne repérait en
revanche que la moitié des ouvertures : celles qui portent un verbe d'action
explicite (« charger », « ouvrir », « sélectionner »). Les autres — « on bosse
sur exploitation », « on va regarder du côté de telemetrie si ça te va » — ne
sont pas des questions, et le prompt système ne parlait que de questions.

**Ce qui a été ajouté, et ce n'est pas une liste de tournures.** Le prompt de
l'agent système énonce maintenant sa frontière comme une **propriété** — *est
pour toi ce à quoi on répond en LISANT ce que l'installation déclare ; n'est pas
pour toi ce à quoi on répond en CALCULANT sur les lignes ; ni la longueur, ni la
politesse, ni le verbe ne décident* — et il dit qu'un message peut ne poser
AUCUNE question et désigner quand même une source. Les ouvertures passent de
15/30 à 27/30, la contre-épreuve reste à 12/12, et la campagne coûte **165
appels LLM contre 202 avant** : une ouverture reconnue par l'outil s'arrête au
nœud système et ne paie ni planificateur ni récupération. La branche du nom
absent, ajoutée après coup pour réparer « tu bosses sur quoi ? », a fini de
fermer le volet : **30/30 ouvertures, 156 appels** — l'outil est appelé plus
souvent, et chaque fois qu'il l'est le tour s'arrête plus tôt.

**Une mise en garde, parce qu'elle a coûté cher ici.** Cette phrase de prompt a
d'abord été déplacée et réécrite pour corriger une « régression » de la surface
conversationnelle (36/36 annoncé, 34/36 obtenu). La réécriture a fait tomber la
surface à 33/36 ET la dette B de 27/30 à 24/30 ; le prompt d'AVANT le chantier,
rejoué le même jour dans une campagne UNIQUE, a rendu 33/36 lui aussi, et on en
a conclu que la référence avait dérivé toute seule.

**C'était faux, et c'est la mesure qui l'a dit.** Rejouées le 2026-09-16, deux
campagnes de chaque côté, le même jour, sur le même serveur :

| octets mesurés | campagne 1 | campagne 2 | appels LLM |
|---|---|---|---|
| `972870f`, sans ce chantier | 36/36 | 36/36 | 78 |
| ce chantier, avant réparation | 34/36 | 34/36 | 81 |

La référence n'avait pas bougé d'un point. C'est ce chantier qui faisait tomber
deux questions, toujours les mêmes, aux deux campagnes — donc de façon
déterministe. **Une campagne unique ne tranche rien** : elle donne un nombre,
pas un verdict, et c'est sur ce nombre-là qu'on s'était attribué une innocence.
La règle qui en sort est plus dure que celle qu'on avait écrite : avant de
conclure à une dérive, on rejoue DEUX fois chaque côté, et deux échecs
identiques sont une cause, pas un aléa. Le détail des deux défaillances et de
leur réparation est plus bas.

**Tranché : la voie 2 est gardée, la voie 1 est retirée.** Un second chemin qui
casse ce que le premier tenait n'est pas un second chemin, c'est un échange. Et
le court-circuit déterministe reste devant, intact : un message réduit au nom
d'une source ne paie toujours **aucun** aller-retour et ne passe même pas par le
nœud système (`O05`, 0 appel sur trois tirages).

#### Les deux verrous de la liaison par outil, et le défaut qu'ils ont révélé

Lier une source par une phrase ne doit pas permettre d'en changer à l'insu de
l'utilisateur. Deux vérifications, dans `Orchestrator._liaison_demandee` :

1. **la source liée est celle que l'UTILISATEUR a nommée**, pas celle que le
   modèle a passée à l'outil (`introspection.source_nommee` sur le message). Le
   modèle propose un argument à chaque appel, parfois au hasard des
   descriptions ; c'est la précaution que prend déjà
   `_regle_source_de_la_conversation`, et pour la même raison ;
2. **hors conversation, rien ne se lie** : sans fil, il n'y a rien à retenir.

Ces deux verrous ont fait apparaître un défaut que la campagne live ne pouvait
pas voir, et c'est un test qui l'a trouvé. L'outil rendait directement l'accueil
complet de la source, « je garde cette source pour la suite de la
conversation » comprise — et ce texte était servi tel quel quand le nœud
REFUSAIT de lier. L'utilisateur lisait une promesse que personne n'avait tenue,
et son tour suivant retombait sur « sur quelle source veux-tu travailler ? ».
L'outil ne rend donc plus que la **fiche** de la source
(`introspection.fiche_de_source`) ; la promesse appartient au nœud qui la tient.

#### Ce qui reste

`O01` — « on bosse sur exploitation aujourd'hui, tu peux me la sortir ? » —
échoue encore, 0/3, de façon déterministe : le modèle lit « tu peux me la
sortir ? » comme une demande de contenu et part à la récupération, qui décrit la
source sans compter ses tables. La source EST liée ; c'est la réponse qui ne
porte pas le volume. Une ouverture sur dix, et elle reste en dette plutôt que
d'être réparée par un mot de plus dans le prompt.

### Dette B — une réponse juste dont la provenance n'était pas dite

« statut dans sessions, ça veut dire quoi ? » partait à la récupération, qui
répondait JUSTE — « 'T' : Terminée, la recharge a abouti ; 'I' : Interrompue… »,
contenu qui ne peut venir que du dictionnaire — sans dire d'où elle le tenait.
C'est cette attribution qui distingue une **lecture de la source** d'un savoir
général sur des codes de statut répandus, et l'utilisateur n'a aucun autre
moyen de faire la différence.

Un runner a été écrit pour ce sujet, et ses **dix questions ont été écrites après
le correctif** :
[`scripts/mesure_provenance_du_sens.py`](../scripts/mesure_provenance_du_sens.py).
Il porte deux catalogues, et le second n'est pas un décor :

- **volet sens** — six questions de sens, court et long, sur deux sources qui
  DÉCLARENT un dictionnaire (`exploitation`, `telemetrie`). Le contenu attendu
  ne peut venir que de lui : rien dans le schéma ne dit que `-1` est une
  sentinelle, que `nb_points` est un nombre *prévu*, ni que `prix_kwh_eur = 0`
  sur `ABO` n'est pas une valeur manquante. La réponse doit porter ce contenu
  **et** nommer sa provenance ;
- **volet témoin** — les quatre mêmes formes de question sur `titanic` et
  `iris`, qui n'en déclarent AUCUN. Le bloc de dictionnaire est alors absent du
  prompt : toute attribution y est une invention, et une attribution inventée
  est pire que pas d'attribution — elle donne l'autorité de la base à un savoir
  général. C'est le piège que la voie 2 pouvait ouvrir, et la seule façon de
  savoir s'il s'est ouvert est de le mesurer.

#### Les deux voies

**Voie 1 — router ces questions vers l'agent système**, dont c'est le métier :
son outil `schema_d_une_source` porte déjà l'extrait de dictionnaire de la
colonne visée, et il attribue spontanément (« Selon le dictionnaire de
`exploitation`… »). Ce qui lui manquait n'était pas l'outil mais la
**frontière** : son prompt ne parlait que de « questions SUR cet agent », et la
liste « CE QUI N'EST PAS POUR TOI » range du côté du calcul tout ce qui touche
au contenu. Une question sur le SENS d'une colonne tombait entre les deux. La
frontière est maintenant dite comme une propriété — *est pour toi ce à quoi on
répond en LISANT ce que l'installation déclare ; n'est pas pour toi ce à quoi on
répond en CALCULANT sur les lignes* — et le dictionnaire y est nommé comme une
des choses qui se lisent.

**Voie 2 — faire attribuer la récupération** quand elle répond depuis le
dictionnaire : une consigne ajoutée à `EN_TETE_SQL`, le texte qui coiffe le
dictionnaire dans le prompt de l'agent SQL.

#### Les trois états, mesurés

Trois tirages, trente tours à chaque fois. « provenance dite » compte les tours
du volet sens où la réponse nomme le dictionnaire ; « fausse attribution »
compte les tours du volet témoin où elle le nomme alors qu'il n'y en a pas.

| état | sens | témoin | total | provenance dite | fausse attribution | appels LLM |
|---|---|---|---|---|---|---|
| **avant** | 9/18 | 12/12 | **21/30** | 9/18 | **0/12** | 84 |
| **voie 1 seule** (la frontière dite comme une propriété) | 12/18 | 12/12 | **24/30** | 12/18 | **0/12** | 84 |
| **voie 1 + voie 2** (la récupération attribue) | 15/18 | 12/12 | **27/30** | **18/18** | **0/12** | 87 |

**Le piège de la voie 2 ne s'est pas ouvert, et c'est le résultat qui compte.**
Zéro fausse attribution sur douze tours de témoin, dans les trois états. La
consigne dit à quelle condition écrire la mention — *et que tu le tiens de ce
dictionnaire* — et elle interdit explicitement de l'écrire pour autre chose ;
sur `titanic` et `iris` le bloc de dictionnaire est absent du prompt, et le
modèle n'a rien inventé. L'attribution passe de 9/18 à **18/18**.

**Ce que la voie 1 a fait, et ce qu'elle n'a pas fait.** Il faut le dire
franchement : **elle n'a déplacé aucun routage.** Les mêmes questions passent
par les mêmes nœuds avant et après — `S1`, `S3`, `S4`, `S6` vont toujours à la
récupération, `S2` et `S5` toujours à l'agent système. La frontière énoncée
comme une propriété n'a donc pas convaincu l'agent système de prendre les
questions de sens formulées court. Les trois tours gagnés (`S1`) tiennent à
l'attribution sur le chemin de la récupération, et cette variation-là n'est pas
séparable de la réécriture du dictionnaire d'`exploitation` faite au même
chantier ni d'un effet de tirage : **elle ne lui est pas attribuée**.

Ce qui l'a méritée, c'est la dette A : la même phrase de prompt fait passer les
ouvertures de 15/30 à 27/30 (plus haut). Elle est gardée pour ça, et la partie
« le dictionnaire se lit » est vraie mais reste sans effet mesuré.

**Tranché : c'est la voie 2 qui répare la dette B.** L'attribution est produite
par celui qui lit le dictionnaire, là où il le lit, et le témoin dit qu'elle ne
déborde pas. Le coût est de **3 appels LLM sur 87** — une reprise sur les trente
tours.

#### Ce qui reste, et une limite de l'oracle qu'il faut dire

`S4` — « puissance_kw, ça signifie quoi ? » — reste comptée en échec, et
l'oracle y est en cause autant que la réponse. La réponse dit exactement ce
qu'il faut, provenance comprise :

> La colonne `puissance_kw` … représente la puissance instantanée mesurée en
> kilowatts. **Selon le dictionnaire de la source**, la valeur `-1` indique que
> le compteur n'a rien remonté…

L'oracle, lui, exigeait le mot « sentinelle ». C'est **notre** vocabulaire, pas
celui de l'utilisateur, et l'exiger mesure la reprise d'un jargon et non la
provenance d'un fait. Rejugé sur le FAIT — la réponse dit-elle que `-1` n'est
pas une puissance ? — le tableau devient :

| état | oracle déclaré | oracle arbitré sur `S4` |
|---|---|---|
| avant | 21/30 | 21/30 |
| voie 1 seule | 24/30 | 24/30 |
| voie 1 + voie 2 | **27/30** | **30/30** |

L'arbitrage ne change **rien** aux deux premiers états : il ne concerne que le
tour où la réponse est devenue juste, et il ne fabrique donc pas l'écart qu'il
mesure. Les deux colonnes sont données parce qu'un oracle qu'on desserre sans le
montrer est un chiffre qu'on s'offre — et parce que celui-ci a été écrit avant
la mesure, pas après.

### Dette C — deux formulations qui classent autre chose, et l'oracle qui avait tort

Trois formulations du runner de classement étaient portées en dette : `F03` et
`F07`, qui classent sur l'énergie plutôt que sur le nombre de sessions, et `F10`,
qui appliquait le `WHERE statut = 'T'` que le dictionnaire prescrit pour une
AUTRE grandeur.

**`F10` ne se reproduit pas.** Douze tirages, à code et à dictionnaire
identiques à ceux du chantier précédent : 12/12 conformes, jamais de filtre sur
`statut`. Le défaut était donc **intermittent**, et non déterministe comme la
dette le disait. Ce qui ne le rend pas irréel, et la section suivante dit ce
qu'on en a fait.

**`F03` et `F07`, elles, sont parfaitement stables** — 0/3 chacune, le même SQL
au caractère près sur les trois tirages :

```sql
SELECT T1.code_station, T1.nom, SUM(T3.energie_kwh) AS total_energie_kwh
FROM stations AS T1 JOIN bornes AS T2 … JOIN sessions AS T3 …
GROUP BY … ORDER BY total_energie_kwh DESC LIMIT 3
```

#### L'arbitrage, question par question

Le fait qui tranche a été établi en SQL direct, avant tout jugement :

| | par `COUNT(sessions)` | par `SUM(energie_kwh)` |
|---|---|---|
| 1er | `ST-097` — 1 015 | `ST-097` — 37 957,56 |
| 2e | `ST-029` — 911 | `ST-029` — 33 821,99 |
| 3e | `ST-016` — 872 | `ST-016` — 32 178,53 |

**Les trois stations sont les mêmes, dans le même ordre.** Ce n'est donc pas un
palmarès faux : c'est le même palmarès, rendu avec une autre mesure. Ce que
l'utilisateur perd n'est pas l'exactitude, c'est de savoir **laquelle**.

- **`F03` — « qui charge le plus ? je veux les 3 premières stations… »** :
  **l'oracle avait tort.** La question ne nomme aucune unité, et le verbe
  *charger* porte une quantité d'énergie autant qu'un événement — « qui charge
  le plus » se lit d'abord comme « qui délivre le plus de charge ». Classer sur
  `SUM(energie_kwh)` est une lecture légitime, et c'est même la plus directe.
- **`F07` — « où est-ce qu'on recharge le plus ? »** : **l'oracle avait tort**,
  d'un cran plus faiblement. « On recharge le plus » se lit en fréquence
  (« le plus souvent ») comme en volume (« le plus de kilowattheures »). Une
  question à deux lectures légitimes ne peut pas avoir un oracle à valeur
  unique.
- **Les huit autres restent strictes, et rien n'y est desserré.** Elles nomment
  toutes soit l'unité — « le plus de sessions » (`F01`), « le plus de
  recharges » (`F10`) —, soit une notion de FRÉQUENCE : sollicitation (`F02`),
  activité (`F06`), fréquentation (`F08`), « tourner » (`F05`), « top »
  (`F04`), « palmarès » (`F09`). Le nombre de sessions est la seule lecture de
  ces mots-là, et elles passent déjà 3/3. Les desserrer aurait acheté un
  chiffre pour rien.

Cette distinction n'est pas une intuition posée après coup : c'est exactement
celle qui sépare les deux échecs des huit succès. Le modèle lisait ces deux
questions correctement.

#### Le correctif : la phrase dit sur quoi elle classe

Ce que le produit doit à l'utilisateur n'est donc pas une grandeur en
particulier, c'est de **dire laquelle il a classée**. Et il ne le disait pas : la
réponse multi-lignes est déterministe, et elle valait « 3 lignes retournées —
voir le tableau ci-dessous ». Deux réponses justes sur deux grandeurs
différentes s'y lisaient à l'identique.

`agents/retrieval/classement.grandeur_du_classement` lit donc la grandeur qui
ordonne **sur le SQL exécuté** — la même lecture que `grandeurs_non_projetees`,
servie par l'autre bout — et la phrase la nomme :

> 3 lignes retournées, classées par `total_energie_kwh` (ordre décroissant) —
> voir le tableau ci-dessous.

Aucune énumération de tournures, et c'est le point : la propriété est vraie ou
fausse quelle que soit la langue, la phrase, ou le modèle qui a écrit la
requête. Une consigne de prompt qui aurait demandé au modèle de nommer sa
grandeur aurait tenu sur les phrases qu'on lui aurait montrées — ce produit a
déjà payé ce pari deux fois. Elle ne s'ajoute qu'au résumé déterministe
multi-lignes : le résumé d'un agrégat d'une ligne vient du modèle, qui nomme
déjà ce qu'il a calculé, et il n'y a pas de classement à une ligne.

#### L'oracle, avant et après — ce qui a été desserré et ce qui a été resserré

Un oracle qu'on desserre sans le montrer est un chiffre qu'on s'offre. Voici
donc les deux définitions côte à côte.

| | oracle d'AVANT | oracle d'APRÈS |
|---|---|---|
| les huit formulations qui nomment une fréquence ou l'unité | les 3 stations + les 3 comptes de sessions | idem, **+ la phrase nomme la grandeur classée** |
| `F03` et `F07` | les 3 stations + les 3 comptes de sessions | les 3 stations + les comptes **OU** les énergies, **+ la phrase nomme la grandeur classée** |

Desserré sur deux lignes, resserré sur les dix. Le `pourquoi` de chaque
desserrage est écrit dans le runner, sur la ligne où il s'applique
(`Formulation.pourquoi`), et non dans un document à côté.

**Et voici la décomposition, pour qu'on voie d'où vient chaque point gagné.**
Le runner sait rejouer l'oracle d'avant (`--oracle-davant`), ce qui permet de
comparer deux MESURES et non deux définitions :

| | oracle d'AVANT | oracle arbitré |
|---|---|---|
| code d'avant | **24/30** | — |
| code d'après (la phrase nomme la grandeur) | **24/30** | **30/30** |

Le correctif de produit ne gagne donc **aucun point** sous l'ancien oracle, et
c'est exactement ce qu'on attend de lui : il ne change pas la grandeur que le
modèle choisit, il dit laquelle il a choisie. Les six tirages gagnés viennent
**entièrement de l'arbitrage de l'oracle** — et le correctif est ce qui donne le
droit d'arbitrer, parce que sans lui les deux lectures resteraient
indistinguables pour l'utilisateur. La grandeur est nommée **30/30** des deux
côtés du tableau, et le SQL reste en règle 30/30.

#### Le F10 du dictionnaire : une contradiction de VOCABULAIRE, pas de prompt

`F10` ne se reproduit plus, mais sa cause est lisible dans le texte, et elle
relève bien de la **rédaction du dictionnaire**. `exploitation.md` disait, dans
le corps de sa section : « Le nombre de recharges réelles est
`WHERE statut = 'T'` ». Et deux paragraphes plus bas : « **Règle par défaut :
AUCUN filtre sur `statut`.** Tout comptage, tout classement et toute somme… ».
Les deux phrases sont vraies et elles se contredisent sur un mot : **recharge**,
qui désigne à la fois une ligne de `sessions` — n'importe quel statut — et, dans
l'autre phrase, la seule qui a abouti. « Sur quelles stations y a-t-il eu le plus
de **recharges** ? » tombe exactement là.

Un lecteur humain lève l'ambiguïté seul. Le modèle n'a aucune raison de la lever
toujours dans le même sens, et c'est pourquoi le défaut est intermittent plutôt
qu'absent. Deux choses ont donc été faites, et aucune n'est une liste de mots :

- le dictionnaire d'`exploitation` ne porte plus le mot à deux sens. Il dit en
  tête de section que **session et recharge désignent la même chose**, que ce
  qui distingue les codes est l'**aboutissement**, et son exception est énoncée
  comme une propriété — *la question distingue ce qui a réussi de ce qui a été
  tenté* — au lieu d'une liste de tournures suivie d'un exemple qui emploie le
  mot ordinaire ;
- `docs/rediger-un-dictionnaire-de-source.md` gagne une **septième règle** :
  *un mot qui déclenche une exception ne peut pas être le mot ordinaire de la
  chose.* Elle borne explicitement la règle 1, qui demande de nommer les mots
  déclencheurs — et qui, mal appliquée, fabrique cette contradiction-là.

C'est la seule réponse honnête à « dictionnaire ou prompt ? » : la variable qui
décide est le texte de la source, comme la contrainte de produit le dit déjà, et
sept formulations de consigne ont échoué à réparer un texte ambigu.

### Les garde-fous, et ce qui n'a pas bougé

`assert_read_only`, `retrieval_request_limit`, `retrieval_max_rows` et le bac à
sable au réseau coupé sont inchangés — au diff près : aucun de ces fichiers n'est
touché par le chantier des trois dettes. La vérification de classement
s'intercale APRÈS `adapter.run`, donc après `assert_read_only` : elle ne voit que
du SQL déjà accepté en lecture seule, et n'a aucun moyen d'en faire passer
d'autre. Sa relance dépense `retrieval_request_limit` comme n'importe quel
aller-retour, et elle est bornée à UNE par récupération. La lecture de la
grandeur qui classe (`grandeur_du_classement`) ne touche à rien : elle lit une
chaîne de SQL déjà exécutée et rend un nom à mettre dans une phrase.

**Le court-circuit déterministe du choix de source est intact**, et c'est la
propriété qu'on ajoutait un second chemin pour ne pas perdre : un message réduit
au nom d'une source ne paie toujours **aucun** aller-retour et ne passe même pas
par le nœud système. Mesuré des deux côtés — `O05` du runner d'ouverture (0 appel
sur trois tirages) et le tour 2 de `mesure_choix_de_source.py` (0 appel).

Le **verrou de source** (C15) et l'**oracle d'ambiguïté** (C22) sont verts :

- `mesure_choix_de_source.py` : la source se lie, tient sur une question qui ne
  la nomme pas, bascule en l'annonçant (« Je passe sur la source `telemetrie` —
  on travaillait sur `exploitation` »), et la nouvelle tient à son tour ;
- `mesure_ambiguite_de_source.py`, cinq essais par ordre de déclaration :
  **5/5 propositions des deux côtés**, aucune source choisie par l'ordre du
  YAML, aucune liée sans que l'utilisateur ait tranché. Le verrou ajouté à la
  liaison par outil est le même que celui-là — la source liée est celle que
  l'UTILISATEUR a nommée — et il est éprouvé par le volet verrou du runner
  d'ouverture, 9/9.

La suite complète passe : **1 159 tests, 99,59 % de couverture** (référence
d'avant : 1 128 et 99,58 %). Les trente et un tests ajoutés sont tous du code pur,
sans serveur ni modèle, et les quatre modules touchés — `graph`, `systeme`,
`introspection`, `classement` — restent à 100 %. **Deux d'entre eux ont trouvé un
défaut que la campagne live ne pouvait pas voir** : l'outil de liaison servait
la promesse « je garde cette source pour la suite » même quand le nœud refusait
de lier (cf. la dette A).

La surface conversationnelle, rejouée sur vLLM contre `sources/catalogue.yaml`
(`titanic` + `iris`, qui ne déclarent aucun dictionnaire) : **36/36 questions
méta et 4/4 témoins, à deux campagnes consécutives**, 81 et 17 appels LLM à
chacune.

#### Deux défaillances déterministes, prises pour une dérive

Ce 36/36 est un 34/36 réparé, et l'histoire de l'erreur vaut le résultat.

Le chantier avait d'abord annoncé 34/36 en expliquant que la référence avait
dérivé : une campagne de `972870f` rejouée le même jour rendait 33/36. Deux
campagnes de chaque côté ont dit le contraire — `972870f` rend 36/36 et 36/36
(78 appels), le chantier 34/36 et 34/36 (81 appels). Les mêmes deux questions
tombaient à chaque fois, aux mêmes mots près : ce n'était pas du bruit, c'était
une régression, et ce sont les deux premières phrases qu'un prospect tape devant
la démonstration.

**`sources-tu-bosses` — « tu bosses sur quoi ? ».** Le fil brut du tour est court
et sans appel : `system : aucun outil appelé — passe au planificateur`, puis
`plan : clarification demandée`. L'agent système répondait `AUTRE`, le tour
repartait au planificateur, qui le classait `query` et demandait de choisir une
source. Ce qui s'affichait était pourtant l'inventaire complet — la clarification
d'un `query` sans source EST l'inventaire —, et c'est ce qui a masqué le défaut :
la réponse avait l'air juste. Elle l'était par accident, et le tour était un
tour de clarification.

La cause est l'outil de liaison ajouté par ce chantier, et elle a été isolée par
variantes, chacune mesurée :

| variante | « tu bosses sur quoi ? » |
|---|---|
| cinq outils (`972870f`) | appelle `capacites_de_l_agent` ✅ |
| six outils, dont `travailler_sur_une_source` | aucun outil, `AUTRE` ❌ |
| six outils, le sixième NEUTRE et sans argument | appelle `capacites_de_l_agent` ✅ |
| six outils, le sixième NEUTRE et à argument requis | appelle `capacites_de_l_agent` ✅ |
| six outils, le sixième débarrassé du verbe (nom ET fiche) | appelle `capacites_de_l_agent` ✅ |

Ce n'est donc ni le nombre d'outils, ni l'argument requis : c'est le **verbe**.
`travailler sur une source` est la tournure même avec laquelle on demande à
l'agent sur quoi il travaille. Le modèle range la question sous l'outil, ne peut
pas remplir `source`, et rend le tour entier plutôt que d'appeler un autre outil.

**Et la réparation n'est pas de retirer le verbe.** Débarrasser le nom et la
fiche de « travailler sur » répare ce tour-là et fait tomber les ouvertures de la
dette A de 10 sur 10 à 4 sur 10 : le verbe est exactement ce qui fait attraper
« on se met sur X », « passe-moi la main sur X », « reprendre mon travail sur X ».
Le verbe est la portée de l'outil ; le supprimer, c'est supprimer l'outil.

Ce qui est fait à la place tient en une phrase : **un outil qui ne peut pas
remplir son argument doit avoir quelque chose à rendre, sinon c'est le tour que
le modèle rend.** Appelé avec une `source` vide, `travailler_sur_une_source` rend
désormais l'inventaire des sources — et l'inventaire n'est pas un pis-aller,
c'est la réponse à « sur quoi travailles-tu ? ». Rien n'est lié, l'utilisateur
n'ayant nommé personne ; mais l'outil compte comme appelé, donc l'agent système
répond au lieu d'abdiquer. Une ligne de fiche, une branche de code, et les
ouvertures passent de 8 sur 10 à **10 sur 10**.

**`capacites-demander-quoi` — « je peux te demander quoi ? ».** Ici l'outil était
bel et bien appelé, et les faits rendus énuméraient les cinq capacités. La
réponse du modèle les a toutes perdues : elle a énuméré des SUJETS — mes
capacités, mes sources, mes modèles — sans dire une seule fois qu'elle savait
ANALYSER. La ceinture (`defaut_de_fondation`) n'a rien vu.

Pourquoi elle n'a rien vu est le vrai défaut. Elle exige d'une puce les
IDENTIFIANTS qu'elle porte ; une puce de capacité — « - **analyser et
visualiser** — du code Python… » — n'en porte aucun, donc n'exigeait rien. La
famille « que sais-tu faire ? » tenait par ACCIDENT : `decrire_les_capacites`
finit par « Mes sources : `titanic`, `iris`… », que la ceinture réclame, et le
modèle qui résumait les capacités laissait aussi tomber l'inventaire, donc se
faisait prendre là-dessus. Le jour où sa formulation a cité les trois noms tout
en oubliant les quatre verbes, plus rien ne l'arrêtait.

La ceinture demande désormais aussi les **actions**, par leur radical et non par
leur conjugaison : `interrog`, `analys`, `predi`, `repond`. Une réponse a le
droit d'écrire « je prédis » ou « des prédictions » ; elle n'a pas le droit de
ne pas le dire. C'est la convention de l'oracle de
`mesure_surface_conversationnelle.py`, qui attend « analys » et « predi » —
mesurer une capacité et exiger qu'elle soit dite sont la même opération, faite à
deux endroits. Vérifié sur les autres textes de faits : l'inventaire et le
registre n'exigent aucune action, leurs puces portant toutes un identifiant.

**Ce qu'on retire de l'épisode.** Un chiffre de campagne live n'est pas un
acquis, c'est une mesure datée — cela restait vrai. Mais la conclusion qu'on en
avait tirée était la mauvaise : une campagne unique ne distingue pas une dérive
d'une régression, et c'est précisément quand elle innocente qu'il faut s'en
méfier. Deux campagnes de chaque côté, et deux échecs identiques sont une cause.

Le parcours métier complet, rejoué depuis un message utilisateur : **46/48** sur
les quarante-huit questions (16/16 canoniques, 16/16 courtes, 14/16 longues),
180 appels LLM — contre 44/48 avant. Les quatre tours des dettes A et B qui
échouaient sont réparés ; deux échouent encore, et aucun des deux n'est une
réponse fausse :

- `ouverture-facturation·longue` — « Je souhaiterais consulter la source
  facturation ; **peux-tu m'indiquer ce qu'on y trouve** ? ». La phrase désigne
  une source ET demande son contenu ; l'agent répond le contenu. C'est
  exactement le comportement que la contre-épreuve du runner d'ouverture
  protège (12/12), et l'oracle du parcours, lui, attend l'accusé de réception.
  Les deux ne peuvent pas avoir raison en même temps ;
- `sens-statut·longue` — la réponse est juste et attribuée :

  > La signification de la colonne `statut` … est un code d'une lettre : `T`
  > signifie terminée, `I` signifie interrompue, et `E` signifie erreur. **Ces
  > informations proviennent du dictionnaire de la source `exploitation`.**

  L'oracle cherche les fragments `'T'`, `'I'`, `'E'` **entre apostrophes
  droites** ; le modèle les a écrits entre accents graves. Il mesure une
  typographie, pas un contenu. Il n'a pas été corrigé ici — le corriger aurait
  ajouté un point au score du chantier sans rien apprendre — et il est nommé
  pour que le prochain le desserre délibérément.

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

**Ce qui reste à traiter.** Les trois dettes du chantier précédent sont closes,
et ce qui subsiste est plus petit et mieux cerné. La dette tenue à jour :

| dette | portée | trace | état |
|---|---|---|---|
| **A** — le court-circuit de source ne reconnaît qu'un message réduit au nom d'une source | 3 questions sur 48, déterministe | `ouverture-telemetrie·longue`, `ouverture-facturation·longue`, `verrou-ouverture·longue` | **close** — un outil de l'agent système lie la source ; 18/51 → **51/51** |
| **B** — une question de sens formulée court est routée vers la récupération, qui répond juste sans citer le dictionnaire | 1 question sur 48, déterministe | `sens-statut·courte` | **close** — la récupération attribue ; provenance dite 9/18 → 18/18, et 0 fausse attribution sur 12 témoins |
| **C** — trois formulations de classement choisissent une autre grandeur ou appliquent le filtre `statut = 'T'` | 3 formulations sur 10 | `F03`, `F07`, `F10` | **close** — oracle arbitré sur `F03`/`F07`, la phrase nomme la grandeur ; `F10` ne se reproduisait plus, et sa cause de rédaction est corrigée |
| **D** — une phrase qui désigne une source ET demande son contenu répond le contenu sans annoncer le volume | 1 ouverture sur 10 (`O01`), 1 tour sur 48 | `O01` du runner d'ouverture, `ouverture-facturation·longue` | **ouverte** — et c'est peut-être l'oracle qui a tort : la contre-épreuve protège exactement ce comportement |
| **E** — deux oracles mesurent une typographie ou un jargon plutôt qu'un fait | 1 tour sur 48, 1 sur 30 | `sens-statut·longue` du parcours, `S4` du runner de provenance | **ouverte** — nommée pour être desserrée délibérément, pas en passant |
| **F** — deux questions méta de la surface conversationnelle tombaient, et on l'avait mis sur le compte d'une dérive de la référence | 2 sur 36, déterministe | `sources-tu-bosses`, `capacites-demander-quoi` | **close** — deux campagnes de chaque côté ont montré une régression du chantier ; le verbe de l'outil de liaison et un trou de la ceinture ; 34/36 → **36/36 à deux campagnes** |

`D` et `E` portent sur des oracles autant que sur le produit : aucune ne se
répare par une ligne de prompt de plus, et c'est la seule chose dont on soit
sûr. `F` a été fermée, et elle laisse une leçon qui ne tient pas dans un
tableau — on l'avait imputée au modèle sans l'avoir mesurée deux fois.

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

### Rejouer les campagnes de ce document

Toutes contre le serveur LLM en place, catalogue semé. Chacune écrit son
tableau en Markdown et son journal en JSON.

```bash
export DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml
uv run python scripts/mesure_parcours_de_demonstration.py --tirages 1
uv run python scripts/mesure_ouverture_de_source.py --tirages 3
uv run python scripts/mesure_provenance_du_sens.py --tirages 3
uv run python scripts/mesure_classement_sans_lexique.py --tirages 3
uv run python scripts/mesure_classement_sans_lexique.py --tirages 3 --oracle-davant
uv run python scripts/mesure_choix_de_source.py
```

Les deux qui ne lisent PAS `DAA_CATALOG_PATH`, parce qu'une mesure de
provenance ou d'ambiguïté n'a de sens que sur des octets connus :

```bash
uv run python scripts/mesure_surface_conversationnelle.py
uv run python scripts/mesure_ambiguite_de_source.py --essais 3
```

`mesure_provenance_du_sens.py` porte lui aussi ses deux catalogues en dur — le
catalogue de démonstration pour les six questions de sens, celui de production
pour les quatre témoins sans dictionnaire.
