# La mémoire de conversation : ce qu'elle porte, et ce qui s'en sert

Un tableau produit dans un fil est enregistré comme **source éphémère** : un
nom (`resultat_1`), ses colonnes avec leur type et leurs valeurs possibles, son
nombre de lignes, la question qui l'a produit, la source d'origine, et un
drapeau qui dit s'il est tronqué. Le magasin existe, il est décrit dans le
README et dans
[ARCHITECTURE §4.12](ARCHITECTURE.md#412-les-artefacts-nommés-dune-conversation--relire-rejouer),
et il est écrit à chaque tour.

Ce document ne redit pas ce qu'il est. Il mesure **ce qui s'en sert** : est-ce
qu'un tour repart du tableau d'à côté ou relance la base, est-ce que l'agent
sait énumérer ses objets, est-ce qu'il distingue une source du catalogue d'une
source qu'il a lui-même fabriquée, et est-ce que le drapeau `tronque` arrive
jusqu'à l'utilisateur.

Rien n'y est affirmé de mémoire. Tout vient d'une campagne, et elle se rejoue :

```bash
uv run python scripts/mesure_memoire_de_conversation.py --markdown docs/releve-de-la-memoire-de-conversation.md
```

Le relevé obtenu est dans
[`docs/releve-de-la-memoire-de-conversation.md`](releve-de-la-memoire-de-conversation.md) :
81 tours, six fils, trois tirages, avec pour chacun les nœuds traversés, les
appels d'outil et leurs arguments, le SQL émis, le code généré, ce que la
mémoire a injecté dans quel prompt, et la réponse servie. Les chiffres cités
ici en sortent.

**Ce document est tiré du relevé d'empreinte SHA-256 :**

    RELEVÉ : 4f5c2b44d92dd791a032b64eecbd75fd90105097012ed662e9106cda3aa4f074

Relevé le 2026-09-22, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogues `sources/catalogue.yaml`
(fil A) et `sources/demonstration/catalogue.yaml` (fils B à F). Aucun test ne
recalcule cette empreinte — contrairement à
[`parcours-de-l-agent.md`](parcours-de-l-agent.md), qui en a un. C'est une
différence assumée et non un oubli : verrouiller une prose sur une mesure du
jour même, c'est verrouiller ce qu'on n'a pas encore décidé de garder.

---

## 1. Comment la campagne est faite

Six fils, joués comme de **vrais fils** : ce que l'API reporte d'un tour au
suivant — `source_de_travail`, `echange_precedent`, `pending`,
`workspace_root` — est reporté à l'identique, comme dans
[`releve_des_parcours.py`](../scripts/releve_des_parcours.py). Sans ce report,
« fais-moi un graphique de leur âge » ne veut rien dire.

| fil | ce qu'il cherche | tours mesurés |
|---|---|---|
| **A** | le tour 2 repart-il du tableau du tour 1, ou de la base ? | 2 × 3 |
| **B** | sait-il énumérer ses objets et dire ce que chacun contient ? | 4 × 3 |
| **C** | distingue-t-il les sources du catalogue de celles qu'il a fabriquées ? | 2 × 3 |
| **D** | dit-il qu'un tableau est une tranche, ou donne-t-il la tranche pour le tout ? | 2 × 3 |
| **E** | témoin : ce qui ne doit **pas** repartir de la mémoire | 2 × 3 |
| **F** | témoin : le fil long — huit objets, puis « reprends le premier » | 9 × 3 |

**Chaque fil commence par une amorce** (« travaillons sur *X* »), relevée mais
hors comptage. Ce n'est pas une facilité : sur un catalogue à plusieurs
sources, la première question de données d'un fil neuf n'interroge rien —
`_regle_choisir_la_source` propose l'inventaire et attend qu'on choisisse
([surface-conversationnelle.md §14](surface-conversationnelle.md)). Sans un
tour qui lie la source, les six fils auraient tous mesuré la même chose.

**Ce que la mémoire injecte est relevé par enveloppe**, pas par lecture du
code : les points d'entrée — `ConversationWorkspace.describe`,
`rappel.catalogue_pour_le_prompt`, `Orchestrator._mount_workspace`,
`Orchestrator._effective_catalog`, et depuis C51
`ConversationWorkspace.objets_de_la_conversation` et `sources_transformees` —
sont enveloppés, et le nom de la fonction **appelante** est lu dans la pile.
C'est ce qui permet de dire dans quels nœuds l'inventaire entre, et dans
lesquels il n'entre pas, sans le déduire.

---

## 2. Ce qui marche

### Un tour d'analyse ne peut plus se tromper de fichier (fil B, 3/3)

C'était le défaut le plus grave du relevé précédent, et il ne se voyait pas.

- tour 1 : « les 10 interventions les plus longues » → `resultat_1`, 10 lignes ;
- tour 2 : « fais-moi un graphique du nombre d'interventions **par nature** ».

La question se suffit à elle-même et porte sur la source. Les deux fichiers
étaient montés côte à côte — `/data/interventions.csv` (900 lignes) **et**
`/data/resultat_1.csv` (10 lignes) — et le code généré ouvrait le second :

> Les données montrent que la nature « écran illisible » représente le plus
> grand nombre d'interventions avec **3 cas**.

Deux ordres de grandeur d'écart, et rien ne le signalait : `resultat_1` n'est
pas tronqué au sens du drapeau — c'était un `LIMIT 10` demandé, pas une coupe
subie — donc aucun avis ne partait. Un chiffre plausible, tiré au sort entre
deux fichiers, servi comme un fait.

**Le montage suit désormais le plan** (`Orchestrator._objets_vises`) : un tour
dont le plan dit `source=interventions` ne voit plus, sous `/data/`, que
`interventions.csv`. Ce que le tour sert, 3/3 :

> Les données montrent que « communication réseau perdue » représente le plus
> grand nombre d'interventions avec **138** cas, suivie par « défaut de
> paiement carte » (135), « écran illisible » (131), « câble endommagé » (127)
> et « borne hors service » (127).

138 est exactement ce que porte la source une fois le dictionnaire appliqué —
les 900 lignes, moins celles dont `duree_indispo_min` vaut `-1`, et sans
`maintenance préventive`, qui n'est pas une panne. Le code généré cite les deux
règles en commentaire.

### L'arbitrage entre les deux montages, et il a été mesuré

Deux directions étaient possibles : **(a)** ne monter que ce que le tour vise,
ou **(b)** monter les deux et vérifier après coup que le code a ouvert le bon
fichier. Elles ont été départagées sur le même commit, le même moteur, le même
fil, cinq tirages de chaque côté — le seul écart étant `_objets_vises`, rendue
équivalente à l'ancien comportement pour le régime (b) :

| régime | montés sous `/data/` | fichier ouvert par le code | chiffre servi |
|---|---|---|---|
| **(a)** ne monter que le visé | `interventions.csv` | `interventions.csv` **5/5** | 138 |
| **(b)** monter les deux | `interventions.csv`, `resultat_1.csv` | `resultat_1.csv` **5/5** | 3 |

Le tirage au sort n'en était pas un : sous (b), le modèle a ouvert le tableau
du fil dix fois sur dix (deux campagnes de cinq). (b) demanderait donc une
vérification qui se déclenche à chaque tour de ce genre, et une réexécution
derrière ; (a) ferme le chemin. C'est (a) qui est en place.

Et la vérification de (b) aurait dû passer par une phrase de prompt — « dis sur
quel fichier tu travailles ». Cinq formulations ont été écrites puis retirées
sur ce projet, chacune au prix d'une question de la surface conversationnelle.
Les sept empreintes SHA-256 des prompts ne bougent pas d'un caractère.

### Le plan désigne enfin un objet du fil (fil A, 3/3)

`extrais-moi les passagères qui ont survécu` puis `fais-moi un graphique de
leur âge` : le planificateur demande `source='resultat_1'`, et il a raison. Une
règle l'écrasait — 0 désignation retenue sur 81 tours — parce que
`_regle_source_de_la_conversation` reposait la source liée sans condition, donc
à partir du deuxième tour de n'importe quelle conversation. L'exception que sa
docstring décrivait vivait dans une branche plus basse, inatteignable.

Le plan retient maintenant `resultat_1` **3/3**, le tour monte ce seul
fichier, et la figure est faite sur les 200 femmes du tour d'avant — avec la
mention de troncature.

**La règle n'a pas été supprimée, elle a été bornée.** Elle corrige de vraies
erreurs du modèle, et le relevé précédent en montrait une : au tour 3 du fil F,
`source='resultat_1'` répondait à « les 5 techniciens qui sont intervenus le
plus souvent » sur un tableau qui ne porte aucune colonne de technicien. Ce qui
départage les deux cas n'est ni la tournure ni la capacité, c'est de savoir si
l'objet peut RÉPONDRE : une colonne que la question nomme, que la source porte
et que l'objet n'a pas disqualifie la désignation
(`introspection.colonne_hors_de_l_objet`). Le témoin tient — F.3 repart en SQL
sur `interventions` **3/3**.

### « Qu'est-ce que tu as en mémoire ? » a un propriétaire (fil B.3, 3/3)

La question n'appartenait à personne : l'agent système répond sur ce que
l'agent EST et ne voyait jamais le magasin, l'agent de rappel s'interdit
explicitement les questions sur ce dont il dispose. Elle tombait au
planificateur, qui la classait `query`, partait interroger `interventions`,
n'aboutissait pas, et l'utilisateur lisait « je n'ai pas interrogé la source
pour cette question ». Le catalogue des objets était pourtant dans le prompt du
nœud de rappel, sous les yeux du modèle.

Un huitième outil à l'agent système — `memoire_de_la_conversation` — le lui
donne. Le tour se joue en `system → synthesize`, et sert :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> * **Sources TRANSFORMÉES** :
>   * `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle`
>     (texte : 'Colmar — Parc des Expos', …), `nature` (texte : 'borne hors
>     service', …), `duree_indispo_min` (entier) — produit par : « les 10
>     interventions les plus longues, avec leur station et leur nature »
> * **Code produit dans CETTE conversation** :
>   * `graphique_1` — code Python d'une figure (1 image(s)) — produit par :
>     « fais-moi un graphique du nombre d'interventions par nature »

### L'inventaire cesse d'omettre ce que la conversation a fabriqué (fil C, 3/3)

« Quelles données as-tu à ta disposition **maintenant** ? » recevait le
catalogue du YAML, identique au premier tour comme au dixième :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les
> informations sur les stations.

C'était une décision écrite, et elle tient toujours : l'outil qui énumère lit
le catalogue **déclaré**, parce qu'annoncer un tableau intermédiaire comme une
source du catalogue induirait en erreur. Ce qu'elle coûtait, la mesure l'a
montré — le mot « maintenant » ne changeait rien à la réponse.

Les deux tiennent ensemble parce que les tableaux du fil partent sous leur
**propre en-tête** : l'inventaire ne ment sur rien, il cesse d'omettre. Ce sont
les mots de floSa — « j'ai les sources primaires, j'ai les sources
transformées ». 3/3 :

> J'ai à ma disposition la source `referentiel` … Cette source contient
> 1 table, soit 150 lignes …
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau
> de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station`
> (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte)
> et `nb_points` (entier).

Le plancher ne parle **que là où l'on énumère** — le catalogue entier, ou la
source liée quand le message ne nomme personne. Une fiche demandée par son nom
répond à une question sur une source ; une recherche par sujet est une matière
à choisir ; ni l'une ni l'autre n'est un état des données.

### Un tableau dérivé est décrit comme une source (partout)

Une source primaire déclare le type de chaque colonne et, pour les
catégorielles à faible cardinalité, ses valeurs possibles. Un tableau du fil
n'avait que ses noms de colonnes et son nombre de lignes : le modèle en savait
moins sur ce qu'il venait de produire que sur ce dont il était parti.

Le type et les valeurs sont maintenant relevés à l'écriture, sur le DataFrame
qu'on a en main — un CSV relu a perdu ses types, tout y est du texte — avec la
même règle de faible cardinalité qu'ailleurs, et par le même code
(`low_cardinality_values`, quinze valeurs au plus). Une ligne de catalogue
reste **une** ligne : au-delà de 600 caractères, ce sont les VALEURS qui
tombent et les types qui restent, et la coupe est dite dans la ligne.

Ce que cela coûte, mesuré sur le fil F, tirage 1 :

| objets au magasin | `describe` (plan) | catalogue du rappel | prompt du planificateur |
|---|---|---|---|
| 1 | 794 car. | 490 car. | 2 166 tokens |
| 2 | 1 080 car. | 789 car. | 2 267 tokens |
| 4 | 1 582 car. | 1 317 car. | 2 434 tokens |
| 6 | 2 238 car. | 1 999 car. | 2 653 tokens |
| 8 | 2 652 car. | 2 426 car. | 2 789 tokens |

**+1 006 tokens pour huit objets**, contre +638 avant que les types et les
valeurs y soient. Le motif « on injecte l'index, on ouvre à la demande » tient
encore — leur contenu pèserait des dizaines de milliers de tokens — mais
l'index a grossi de moitié, et c'est le prix de la description fine.

### Ce qui marchait déjà et n'a pas bougé

**Le drapeau `tronque` traverse toute la chaîne (fil D, 3/3).**
`liste-moi toutes les interventions de maintenance` rend 200 lignes sur 900 —
le plafond `DAA_RETRIEVAL_MAX_ROWS`. Le tour suivant, « combien de lignes dans
ce tableau ? », passe par le nœud `rappel`, appelle `rejouer_un_code`, et
répond « Le tableau contient 200 lignes. Données tronquées : resultat_1
coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — … ». Écrit dans le
manifeste, lu dans le montage `/data/`, dit dans la réponse : 6 tableaux
tronqués sur 42 produits, 6 tours dont un prompt porte la mention `TRONQUÉ`,
12 tours dont la réponse porte le mot — dont **6 sans requête neuve**.

**Le témoin E ne se fait pas prendre (3/3).** `dans facturation, combien de
factures ont été émises en 2025 ?`, posé juste après un tableau sur
`interventions`, bascule de source, l'annonce et interroge la base. **0/6**
tour de ce fil ne repart d'un objet, **6/6** écrivent du SQL.

**Le fil long retrouve encore le premier objet.** Huit tours, huit tableaux,
fenêtre `DAA_CONTEXT_ARTIFACT_WINDOW=8` : rien n'est évincé, et au neuvième
tour l'agent appelle `lire_un_artefact('resultat_1')` 3/3. Ce qu'il en fait
ensuite est une autre affaire — cf. §3.

---

## 3. Ce qui ne marche pas

### 3.1. L'outil retrouve l'objet, la phrase du modèle le jette (fil F.9, 3/3)

C'est le seul défaut du relevé précédent qui n'a pas été traité, et il n'a pas
bougé. « Reprends le tableau du tout premier tour » :
`lire_un_artefact('resultat_1')` rend le contenu, et l'utilisateur reçoit :

> Je ne peux pas « reprendre » le tableau du tout premier tour sans une
> instruction spécifique sur ce que vous souhaitez faire avec ce tableau (par
> exemple, le trier, calculer une moyenne, le filtrer, etc.).
>
> Le tableau `resultat_1` est le résultat de la requête : « les 10
> interventions les plus longues ».

La garde est
[`defaut_de_formulation`](../src/data_analyst_agent/orchestrator/rappel.py#L393).
Son quatrième critère — « une formulation qui ne porte RIEN de ce que l'outil a
rendu » — s'écrit :

```python
if attendus and not (attendus & _jetons(reponse)):   # rappel.py:432
```

`attendus` contient le nom de l'artefact **et tous les jetons de son contenu**,
et un seul jeton commun suffit. Un renoncement **qui cite ce qu'il renonce à
servir** en partage plusieurs : il passe. La docstring assume cette générosité
— « on cherche à distinguer *formulé autrement* de *n'a rien formulé du
tout* » — et c'est bien cette frontière-là qui est mal placée.

Le voisin immédiat, lui, est passé du mauvais au bon côté : B.4, « et le
premier tableau, il contenait quoi ? », sert les dix lignes du tableau 3/3 là
où deux tirages sur trois rendaient un renoncement.

### 3.2. Un point de la surface conversationnelle a été payé

L'agent système porte les 36 questions de la surface conversationnelle, et lui
donner une matière de plus, c'est risquer qu'il la récite quand on ne lui
demande rien. La borne a été mesurée deux fois de chaque côté, même moteur,
même commit — seul l'outil `memoire_de_la_conversation` change :

| agent système | méta | témoins | ce qui tombe |
|---|---|---|---|
| sept outils | 35/36 puis **36/36** | 4/4, 4/4 | `volumetrie-globale` au premier tirage |
| huit outils, fiche longue | 35/36, 35/36 | 4/4, 4/4 | `periode-directe`, les deux fois |
| huit outils, fiche courte | 35/36, 35/36 | 4/4, 4/4 | `periode-directe`, les deux fois |

**La surface recule d'un point** : 39/40 au lieu de 40/40. Ce qui tombe est
`periode-directe` — « sur quelle période portent les données de la source
titanic ? » — qui reçoit la fiche de la source sans sa période. C'est la
question que le code nomme déjà comme le canari d'une fiche d'outil plus
attirante.

Et le raccourcissement de la fiche n'y change rien : deux tirages avec la fiche
longue, deux avec une fiche réduite à deux phrases, le même point tombe. Ce
n'est donc pas la rédaction de la fiche, c'est l'**existence d'un huitième
outil**. `volumetrie-globale`, elle, bascule d'une campagne à l'autre sans que
le code bouge ; ce n'est pas un verdict.

### 3.3. Le nœud système voit le magasin sans forcément le lire

L'instrument a été étendu pour ne pas le cacher. Le texte des objets du fil est
**mis à disposition** du nœud système à 45 tours sur 63 — médiane 970
caractères — mais il n'entre dans un prompt que si le modèle appelle l'outil
qui le rend. Compter ce texte comme « injecté » ferait croire à un coût de
contexte que la plupart de ces tours ne paient pas ; ne pas le compter du tout
laisserait croire que ce nœud ne voit toujours rien du magasin, ce qui était
vrai avant et ne l'est plus. Le relevé le porte sur une ligne à part.

---

## 4. Les chiffres

### Les chemins

Sur les 63 tours mesurés (hors amorces) :

| chemin | tours | part | avant C51 |
|---|---|---|---|
| repart d'un objet déjà produit | 12 | 19 % | 15 (24 %) |
| relance un SQL sur une source | 42 | 67 % | 42 (67 %) |
| les deux dans le même tour | 0 | 0 % | 0 |
| ni l'un ni l'autre | 9 | 14 % | 6 (10 %) |
| rematérialise la base en CSV | 6 | 10 % | 9 (14 %) |

Un tour « repart d'un objet » quand l'une des trois signatures suivantes est
présente, et il suffit d'une : le plan désigne un objet du magasin comme source
(**3 tours**, contre jamais auparavant) ; un outil de rappel a été appelé
(9 tours) ; le code généré ouvre un CSV de la mémoire monté sous `/data/`.

Les trois tours perdus sont ceux du fil B où le code ouvrait le tableau d'à
côté : ils comptaient comme « repart d'un objet » et ils répondaient faux. Les
trois rematérialisations économisées sont celles du fil A, où le plan désigne
maintenant le tableau au lieu de la base.

| fil | repart d'un objet | relance un SQL |
|---|---|---|
| A | 3/6 | 3/6 |
| B | 3/12 | 3/12 |
| C | 0/6 | 3/6 |
| D | 3/6 | 3/6 |
| E | 0/6 | 6/6 |
| F | 3/27 | 24/27 |

### Ce que l'inventaire coûte, et dans quels nœuds il entre

Le graphe a huit nœuds. **Trois** reçoivent un texte de la mémoire de
conversation dans leur prompt :

| nœud | ce qui est injecté | tours | médiane | max |
|---|---|---|---|---|
| `plan` | `ConversationWorkspace.describe()` | 30 | 1 188 car. | 2 652 car. |
| `rappel` | `catalogue_pour_le_prompt()` | 39 | 808 car. | 2 588 car. |
| `analysis` | la description des tableaux montés sous `/data/` | 6 | 370 car. | 428 car. |

Un quatrième le reçoit **à disposition**, sans qu'il entre nécessairement dans
un prompt (§3.3) :

| nœud | ce qui est offert | tours | médiane | max |
|---|---|---|---|---|
| `system` | `objets_de_la_conversation()` | 45 | 970 car. | 2 652 car. |
| `system` | `sources_transformees()` | 45 | 724 car. | 2 652 car. |

Rapporté au prompt qui le porte, le catalogue pèse **18 % du prompt du
planificateur** en médiane, et jusqu'à **32 %** (fil F, tour 8).

Aucun texte de la mémoire n'entre dans `retrieval` ni `synthesize` — ni dans
`inference` et `fetch_predict`, que ces six fils ne traversent jamais. Ces
nœuds ne l'ignorent pas complètement pour autant : ils reçoivent les tableaux
comme sources *interrogeables* (`_effective_catalog`), sans qu'aucune ligne de
catalogue n'entre dans leur prompt :

| nœud | tours où au moins une source éphémère lui est exposée |
|---|---|
| `system` | 45 |
| `plan` | 30 |
| `retrieval` | 24 |
| `analysis` | 6 |
| `rappel` | 3 |

Enfin, `fit_to_budget` **redemande** le même catalogue pour le peser avant de
décider ce qu'il en garde : 60 appels de plus sur la campagne, médiane
1 188 caractères. Rien n'est envoyé au modèle ; c'est du calcul local, et il
est compté à part pour ne pas gonfler le coût réel.

### Le drapeau `tronque`

| étape | compte |
|---|---|
| **écrit** — tableaux du magasin portant `tronque: true` | 6 / 42 tableaux produits |
| **lu** — tours dont un prompt porte la mention `TRONQUÉ` | 6 |
| **dit** — tours dont la réponse servie porte le mot | 12 |
| dont **dit sans requête neuve** (la tranche vient d'un tour passé) | 6 |

La chaîne ne se coupe nulle part.

---

## 5. Ce que cette campagne n'établit pas

- **Un seul moteur, une seule température.** `gemma-4-E4B-it-qat-w4a16-ct` à
  température 0. Les trois tirages sont identiques à quelques phrases près :
  ils mesurent la stabilité du chemin, pas la variance d'un modèle.
- **Aucune éviction n'a été observée.** Huit objets, fenêtre de huit : le fil F
  touche la borne sans la franchir. Ce que fait le rappel quand un objet est
  vraiment sorti du contexte n'est pas mesuré ici.
- **Aucun fil ne mélange les natures.** Les huit objets du fil F sont des
  tableaux ; les fenêtres séparées tableaux/code ne sont donc pas éprouvées.
- **`fetch_then_predict` et `inference` ne sont jamais traversés.** « Prédis
  ces lignes » sur un tableau de la conversation — un usage que le magasin
  annonce, et que la désignation d'objet rend enfin atteignable — n'est pas
  dans ces six fils.
- **La borne de la désignation n'est éprouvée que sur un cas.** Une colonne
  réclamée et absente disqualifie l'objet ; le fil F en donne un exemple, et un
  seul. Une question qui réclame une colonne sans la nommer passerait.
- **Les chiffres dépendent du catalogue.** Une source unique au catalogue ne
  déclenche pas la même règle de liaison.

---

## 6. Réparations écrites et non faites

1. **§3.1 — la générosité de `defaut_de_formulation`.** Un seul jeton commun
   suffit à laisser passer un renoncement qui cite l'artefact. Une garde
   supplémentaire — un renoncement explicite alors qu'une lecture a abouti —
   servirait les faits au lieu de la phrase, comme elle le fait déjà pour la
   sentinelle. C'est le dernier défaut de cette famille.
2. **§3.2 — le point de surface.** `periode-directe` tombe avec le huitième
   outil et ne remonte pas quand on raccourcit sa fiche. Ce qui reste à
   essayer est ailleurs : la fiche de `sources_de_donnees` promet déjà la
   période, et c'est elle que le modèle appelle. Rien ne dit que c'est la
   présence de l'outil de mémoire qu'il faut défaire plutôt que cette fiche-là
   qu'il faut rendre plus nette.
3. **La borne de la désignation, élargie.** `colonne_hors_de_l_objet` compare
   des noms de colonnes ; elle ne voit pas qu'« les techniciens » et la colonne
   `agent_intervenant` sont la même chose. Le dictionnaire de la source le
   dirait, et il est déjà lu ailleurs.
