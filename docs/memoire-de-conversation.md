# La mémoire de conversation : ce qu'elle porte, et ce qui s'en sert

Un tableau produit dans un fil est enregistré comme **source éphémère** : un
nom (`resultat_1`), ses colonnes, son nombre de lignes, la question qui l'a
produit, la source d'origine, et un drapeau qui dit s'il est tronqué. Le
magasin existe, il est décrit dans le README et dans
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

    RELEVÉ : b14bd4fd6ecb17f45f73239f985a347eb8a3d88932fa1ff35b8ef7a4776e8e71

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
code : les quatre points d'entrée — `ConversationWorkspace.describe`,
`rappel.catalogue_pour_le_prompt`, `Orchestrator._mount_workspace`,
`Orchestrator._effective_catalog` — sont enveloppés, et le nom de la fonction
**appelante** est lu dans la pile. C'est ce qui permet de dire dans quels nœuds
l'inventaire entre, et dans lesquels il n'entre pas, sans le déduire.

---

## 2. Ce qui marche

### Le drapeau `tronque` traverse toute la chaîne (fil D, 3/3)

`liste-moi toutes les interventions de maintenance` rend 200 lignes sur 900 —
le plafond `DAA_RETRIEVAL_MAX_ROWS`. Le tour suivant, « combien de lignes dans
ce tableau ? », passe par le nœud `rappel`, appelle
`rejouer_un_code('resultat_1', …)`, et répond :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage
> DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne,
> comptage) décrit cet échantillon, pas le résultat entier de la requête qui
> les a produits.

Écrit dans le manifeste, lu dans le montage `/data/`, dit dans la réponse :
6 tableaux tronqués sur 42 produits, 6 tours dont un prompt porte la mention
`TRONQUÉ`, 12 tours dont la réponse porte le mot — dont **6 sans requête
neuve**, c'est-à-dire là où la phrase de la synthèse est loin derrière et où le
drapeau du magasin est la seule chose qui reste. C'est précisément le cas pour
lequel il a été écrit.

### Le témoin E ne se fait pas prendre (3/3)

`dans facturation, combien de factures ont été émises en 2025 ?`, posé juste
après un tableau sur `interventions`, bascule de source, l'annonce
(« Je passe sur la source `facturation` — on travaillait sur `interventions` »)
et interroge la base. **0/6** tour de ce fil ne repart d'un objet, **6/6**
écrivent du SQL. Le tableau d'à côté n'est jamais repris.

### Le fil long retrouve encore le premier objet (fil F)

Huit tours, huit tableaux, fenêtre `DAA_CONTEXT_ARTIFACT_WINDOW=8` : rien n'est
évincé, et au neuvième tour l'agent appelle `lire_un_artefact('resultat_1')`
3/3. **L'outil retrouve l'objet.** Ce qu'il en fait ensuite est une autre
affaire — cf. §3.

Le coût en contexte, mesuré tour par tour sur le tirage 1 :

| objets au magasin | `describe` (plan) | catalogue du rappel | prompt du planificateur |
|---|---|---|---|
| 1 | 0 car. | 0 car. | 1 783 tokens |
| 2 | 665 car. | 361 car. | 2 123 tokens |
| 4 | 932 car. | 654 car. | 2 222 tokens |
| 6 | 1 197 car. | 945 car. | 2 304 tokens |
| 8 | 1 548 car. | 1 322 car. | 2 421 tokens |

**+638 tokens pour huit objets**, là où leur contenu en pèserait des dizaines
de milliers. Le motif « on injecte l'index, on ouvre à la demande » tient.

### Le tableau d'à côté sert quand la question le désigne (fil A, 3/3)

`extrais-moi les passagères qui ont survécu` puis `fais-moi un graphique de
leur âge` : le code généré lit `/data/resultat_1.csv`, 3/3, et la réponse porte
la mention de troncature. La figure est faite sur les 200 femmes du tour
d'avant, pas sur les 891 passagers. C'est le cas que floSa décrit, et il
marche — **par le montage dans le bac à sable, pas par le plan** (§3.1).

---

## 3. Ce qui ne marche pas

### 3.1. Le plan ne désigne jamais un objet de la conversation — une règle le lui interdit

Sur les 81 tours, le planificateur a désigné un tableau du fil comme `source`
**5 fois**. Les 5 fois, une règle l'a remplacé par la source de la base :

| tour | ce que le modèle a demandé | ce que le plan a retenu |
|---|---|---|
| A.2 (×3) | `source='resultat_1'` | `titanic` |
| F.3 (×2) | `source='resultat_1'` | `interventions` |

**`plan.source` ne désigne un objet de la conversation dans aucun des 81
tours.** La règle est
[`_regle_source_de_la_conversation`](../src/data_analyst_agent/orchestrator/graph.py#L924) :

```python
if nommee:
    plan.source = nommee
elif ctx.source_de_travail:  # graph.py:972
    plan.source = ctx.source_de_travail
```

Sa docstring dit pourtant, quinze lignes plus haut :

> Un `plan.source` qui désigne un **objet du fil** (un tableau intermédiaire)
> n'est pas effacé : ce n'est pas un choix entre sources ambiguës, c'est un
> résultat que la conversation vient de produire.

C'est vrai de la branche d'effacement (`elif plan.source in declarees`), qui
est en dernier. Mais la branche `elif ctx.source_de_travail` s'exécute avant et
écrase sans condition. **L'exception documentée est inatteignable dès qu'une
source est liée au fil** — c'est-à-dire à partir du deuxième tour de n'importe
quelle conversation.

Conséquence mesurée sur le fil A : `plan.source = titanic`, donc le nœud
d'analyse matérialise **toute la base** en CSV avant de produire un code qui ne
l'ouvre pas.

| monté sous `/data/` au tour A.2 | origine | lu par le code ? |
|---|---|---|
| `passengers.csv` (891 lignes) | table de la base | non |
| `classes.csv` (3 lignes) | table de la base | non |
| `resultat_1.csv` (200 lignes) | tableau de la conversation | **oui** |

9 tours sur 63 rematérialisent ainsi une base dont le code généré ne lira rien.

À décharge : en F.3, le modèle demandait `resultat_1` (les 10 interventions les
plus longues) pour répondre à « les 5 techniciens qui sont intervenus le plus
souvent » — la règle a corrigé une erreur. Elle n'est donc pas purement nuisible,
et c'est ce qui rend l'arbitrage réel.

### 3.2. Un tableau monté à côté de la base peut répondre à sa place — en silence

**C'est le défaut le plus grave de la campagne.** Fil B, tour 2, 3/3 :

- tour 1 : « les 10 interventions les plus longues » → `resultat_1`, 10 lignes ;
- tour 2 : « fais-moi un graphique du nombre d'interventions **par nature** ».

La question se suffit à elle-même et porte sur la source. Le plan dit
`source=interventions`. Les deux fichiers sont montés —
`/data/interventions.csv` (900 lignes) **et** `/data/resultat_1.csv`
(10 lignes). Le code généré ouvre le second :

```text
df_resultat_1 = pd.read_csv('/data/resultat_1.csv')
```

Et la réponse servie :

> Les données montrent que la nature « écran illisible » représente le plus
> grand nombre d'interventions avec **3 cas**. Les autres natures, « défaut de
> paiement carte » et « câble endommagé », comptent chacune 2 interventions.

La vérité de la source : `maintenance préventive` 157, `défaut de paiement
carte` 155, `communication réseau perdue` 155, `écran illisible` 152, `borne
hors service` 141, `câble endommagé` 140. **Deux ordres de grandeur d'écart, et
rien ne le signale** — `resultat_1` n'est pas tronqué au sens du drapeau (c'est
un `LIMIT 10` demandé, pas une coupe subie), donc aucun avis ne part.

Le texte injecté dans le prompt d'analyse est celui-ci, 149 caractères :

    Objets intermédiaires de la conversation (réutilisables) :
    - /data/resultat_1.csv (10 lignes ; colonnes : station_libelle, nature, duree_indispo_min)

Les deux fichiers sont bien annoncés au modèle : `_initial_prompt` liste
`- /data/interventions.csv` et `- /data/resultat_1.csv` sous « Fichiers de
données disponibles »
([`analysis/agent.py:101`](../src/data_analyst_agent/agents/analysis/agent.py#L101)).
Ce qui manque n'est donc pas l'information, c'est l'**arbitrage** : le seul des
deux qualifié dans le prompt est le tableau du fil, et il l'est par le mot
*réutilisable*. Rien ne dit que la source du plan est le sujet de la question,
ni que l'objet intermédiaire n'est là que pour le cas où on le désigne. Le
montage est un décor ; rien ne départage ses pièces.

### 3.3. « Qu'est-ce que tu as en mémoire ? » n'appartient à personne (fil B.3, 3/3)

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en
> affirmer. Reformule en précisant ce que tu veux en savoir (par exemple :
> « combien de lignes ? », « quelles colonnes ? »).

Le fil portait `resultat_1` et `graphique_1`. Le parcours relevé :
`system → rappel → plan → retrieval → synthesize`, 4 appels LLM, 0 requête
aboutie. Chacun des deux agents qui pourraient répondre l'exclut par son
prompt :

- l'agent **système** répond « sur ce que l'agent EST, jamais sur ce que les
  données CONTIENNENT », et aucun de ses cinq outils ne lit le magasin de la
  conversation ;
- l'agent de **rappel** exclut explicitement « une question sur ce que tu sais
  faire ou sur les sources dont tu disposes ».

La question tombe donc au planificateur, qui la classe `query`, part
interroger `interventions`, n'aboutit pas, et la synthèse écarte la réponse
comme non fondée. **Le catalogue des objets est dans le prompt du nœud
`rappel`** (465 caractères à ce tour) — le modèle l'a sous les yeux et n'a pas
le droit de s'en servir pour cette question-là.

### 3.4. L'inventaire des sources ignore ce que la conversation a fabriqué (fil C, 3/3)

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les
> informations sur les stations. Cette source contient 1 table, soit 150
> lignes, et couvre la période du 2019-01-01 au 2024-06-14.

`resultat_1` existe, il est interrogeable, il est même passé au nœud `system`
dans le catalogue *effectif*. Mais l'outil qui énumère lit le catalogue
**déclaré**, et c'est écrit :

> Le **déclaré** pour énumérer les sources : un tableau intermédiaire de la
> conversation n'est pas une source de données, et l'annoncer comme telle
> induirait en erreur.
> ([`systeme.py:106`](../src/data_analyst_agent/orchestrator/systeme.py#L106))

C'est une décision, pas un accident. La campagne ne dit pas qu'elle est
mauvaise ; elle dit que le mot « **maintenant** » de la question —
« quelles données as-tu à ta disposition *maintenant* ? » — ne change rien à la
réponse, et qu'un utilisateur qui vient de fabriquer un tableau ne le voit nulle
part dans l'inventaire.

### 3.5. L'outil retrouve l'objet, la phrase du modèle le jette (6 tours sur 9)

Sur les 9 tours où un outil de rappel a été appelé, **6 ont perdu ce que
l'outil avait rendu**.

Fil F, tour 9, « reprends le tableau du tout premier tour », 3/3 :
`lire_un_artefact('resultat_1')` rend le contenu, et l'utilisateur reçoit :

> Je ne peux pas « valider » un feedback. Je suis un modèle de langage et je
> n'ai pas de mécanisme de validation pour les retours utilisateurs. Si vous
> souhaitez que je fasse quelque chose avec le tableau `resultat_1` (le tableau
> des 10 interventions les plus longues), veuillez me donner une instruction
> claire.

Fil B, tour 4, « et le premier tableau, il contenait quoi ? » — et là les trois
tirages se séparent, ce qui montre exactement où passe la limite :

| tirage | ce que le modèle a rendu | ce que la ceinture en a fait | ce que l'utilisateur a lu |
|---|---|---|---|
| 1 | la sentinelle `AUTRE` | **écartée** — « faits servis » | les 10 lignes du tableau |
| 2 | « je ne comprends pas votre demande… `resultat_1` » | laissée passer | le renoncement |
| 3 | idem | laissée passer | le renoncement |

La garde est
[`defaut_de_formulation`](../src/data_analyst_agent/orchestrator/rappel.py#L393).
Elle attrape la sentinelle, la réponse vide et le nom inventé. Son quatrième
critère — « une formulation qui ne porte RIEN de ce que l'outil a rendu » —
s'écrit :

```python
if attendus and not (attendus & _jetons(reponse)):   # rappel.py:432
```

`attendus` contient le nom de l'artefact **et tous les jetons de son contenu**,
et un seul jeton commun suffit. Une phrase qui dit « le tableau `resultat_1`
des 10 interventions les plus longues » en partage plusieurs : elle passe. La
docstring assume cette générosité — « on cherche à distinguer *formulé
autrement* de *n'a rien formulé du tout* » — mais un renoncement **qui cite ce
qu'il renonce à servir** tombe du mauvais côté de cette frontière, 6 fois sur 9.

---

## 4. Les chiffres

### Les chemins

Sur les 63 tours mesurés (hors amorces) :

| chemin | tours | part |
|---|---|---|
| repart d'un objet déjà produit | 15 | 24 % |
| relance un SQL sur une source | 42 | 67 % |
| les deux dans le même tour | 0 | 0 % |
| ni l'un ni l'autre | 6 | 10 % |
| rematérialise la base en CSV | 9 | 14 % |

Un tour « repart d'un objet » quand l'une des trois signatures suivantes est
présente, et il suffit d'une : le plan désigne un objet du magasin comme
source (**jamais observé**, §3.1) ; un outil de rappel a été appelé ; le code
généré ouvre un CSV de la mémoire monté sous `/data/`.

Par fil :

| fil | repart d'un objet | relance un SQL |
|---|---|---|
| A | 3/6 | 3/6 |
| B | 6/12 | 3/12 |
| C | 0/6 | 3/6 |
| D | 3/6 | 3/6 |
| E | 0/6 | 6/6 |
| F | 3/27 | 24/27 |

### Ce que l'inventaire coûte, et dans quels nœuds il entre

Le graphe a huit nœuds. **Trois** reçoivent un texte de la mémoire de
conversation :

| nœud | ce qui est injecté | tours | médiane | max |
|---|---|---|---|---|
| `plan` | `ConversationWorkspace.describe()` — le catalogue des objets | 33 | 886 car. | 1 548 car. |
| `rappel` | `catalogue_pour_le_prompt()` — une ligne par artefact | 42 | 481 car. | 1 467 car. |
| `analysis` | la description des tableaux du fil montés sous `/data/` | 9 | 196 car. | 224 car. |

Rapporté au prompt qui le porte, le catalogue pèse **14 % du prompt du
planificateur** en médiane, et jusqu'à **22 %** (fil F, tour 8).

Aucun texte de la mémoire n'entre dans `retrieval`, `system` ni `synthesize` —
ni dans `inference` et `fetch_predict`, que ces six fils ne traversent jamais.
Pour `system`, c'est le fait qui explique §3.3 et §3.4 : **le nœud qui répond
aux questions sur l'agent ne voit jamais le catalogue de ce que la conversation
a produit.** Ces nœuds ne l'ignorent pas
complètement pour autant — ils reçoivent les tableaux comme sources
*interrogeables* (`_effective_catalog`), sans qu'aucune ligne de catalogue
n'entre dans leur prompt :

| nœud | tours où au moins une source éphémère lui est exposée |
|---|---|
| `system` | 45 |
| `plan` | 33 |
| `retrieval` | 27 |
| `analysis` | 6 |
| `rappel` | 3 |

Enfin, `fit_to_budget` **redemande** le même catalogue pour le peser avant de
décider ce qu'il en garde : 66 appels de plus sur la campagne, médiane
886 caractères. Rien n'est envoyé au modèle ; c'est du calcul local, et il est
compté à part pour ne pas gonfler le coût réel.

### Le drapeau `tronque`

| étape | compte |
|---|---|
| **écrit** — tableaux du magasin portant `tronque: true` | 6 / 42 tableaux produits |
| **lu** — tours dont un prompt porte la mention `TRONQUÉ` | 6 |
| **dit** — tours dont la réponse servie porte le mot | 12 |
| dont **dit sans requête neuve** (la tranche vient d'un tour passé) | 6 |

La chaîne ne se coupe nulle part. C'est le seul mécanisme de ce document dont
on puisse dire cela.

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
  annonce — n'est pas dans ces six fils.
- **Les chiffres des §3.1 et §3.2 dépendent du catalogue.** Une source unique
  au catalogue ne déclenche pas la même règle de liaison.

---

## 6. Réparations évidentes, écrites et non faites

Cette campagne mesure ; elle ne répare pas. Ce qui suit est noté pour être
décidé sur les chiffres, pas appliqué.

1. **§3.1 — l'ordre des branches de `_regle_source_de_la_conversation`.** Faire
   précéder `elif ctx.source_de_travail` d'un test « le plan désigne-t-il un
   objet du magasin ? » rendrait atteignable l'exception que la docstring
   décrit déjà. Conséquence attendue : plus de rematérialisation de base
   inutile, et un `plan.source` qui dit la vérité. Contre-indication mesurée :
   F.3, où la règle corrigeait le modèle.
2. **§3.2 — le montage ne dit pas lequel est le sujet.** Le contexte d'analyse
   énumère la base et les tableaux du fil au même rang. Y nommer la source du
   plan comme celle sur laquelle la question porte — ou n'y monter les objets
   intermédiaires que lorsque le plan les désigne — fermerait le chemin qui
   rend « 3 cas » pour 152.
3. **§3.3 — personne ne répond à « qu'est-ce que tu as en mémoire ? ».** Un
   sixième outil à l'agent système, qui lirait le magasin de la conversation,
   donnerait un propriétaire à cette question. C'est un changement de frontière
   entre les deux agents, pas un ajustement de prompt.
4. **§3.4 — le mot « maintenant ».** Décider si l'inventaire doit mentionner
   les sources fabriquées, et sous quel intitulé. La décision actuelle est
   écrite et argumentée dans `systeme.py` ; la mesure montre seulement ce
   qu'elle coûte à l'utilisateur.
5. **§3.5 — la générosité de `defaut_de_formulation`.** Un seul jeton commun
   suffit à laisser passer un renoncement qui cite l'artefact. Une garde
   supplémentaire — un renoncement explicite alors qu'une lecture a abouti —
   servirait les faits au lieu de la phrase, comme elle le fait déjà pour la
   sentinelle.

Aucune de ces cinq n'a été appliquée : `src/` n'a pas été touché par cette
tâche.
