# Surface conversationnelle — ce que l'agent sait répondre sur lui-même

Dix conversations de durcissement, et **aucune mesure** de ce que l'agent sait
répondre. On savait, par le README et [axes-amelioration.md](axes-amelioration.md),
que « De quels attributs as-tu besoin ? » tombait dans le repli ; on ne savait pas
*combien* de questions de la même famille tombaient avec elle. Ce document est cette
mesure, et la référence à rejouer.

Il ne porte pas sur les questions sur les **données** — celles-là ont leurs
scénarios golden et leur batterie live. Il porte sur les questions **sur le
système** : quelles sources, quelles tables, quelles colonnes, le sens d'une
colonne, quels modèles, quelles features, que sais-tu faire, quelle période,
combien de lignes.

## Comment lire ce document

Il est **chronologique**, et c'est ce qui lui donne sa valeur : on y voit une
correction être mesurée, tenir, puis se faire prendre en défaut par une
formulation à laquelle personne n'avait pensé.

| § | Ce qu'on y trouve | Toujours vrai du code ? |
|---|---|---|
| 1–4 | le défaut d'origine, la méthode de mesure, la première batterie (21 questions) | la **méthode** oui, les comptes non |
| 5–8 | la première correction : un **lexique** de tournures et un chemin déterministe | **non** — le lexique a été retiré (§9) |
| **6** | pourquoi la capacité système n'entre pas dans `Capability`, mesuré deux fois | **oui**, et verrouillé par un test |
| **8** | la frontière : l'agent répond sur ce qu'il **EST**, pas sur ce que les données **CONTIENNENT** | **oui**, mais tenue par un prompt et non par du code |
| 9–11 | le plafond du lexique mesuré, son remplacement par un **outil que le modèle appelle**, et la mesure d'après (40 questions) | **oui** |
| 12 | la **source de travail** d'une conversation : proposée, validée, portée par le fil | **oui** |
| 13 | ce qui reste ouvert | — |

## 1. Le défaut constaté, et sa cause

Question posée par le propriétaire, en usage réel :

> Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ?

Réponse obtenue, deux fois de suite, reformulation comprise :

> Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire —
> interroger une source (titanic, iris…), une analyse ou une visualisation, ou une
> prédiction — et sur quelles données ?

**La cause n'est pas le modèle.** `Capability`
([`orchestrator/plan.py`](../src/data_analyst_agent/orchestrator/plan.py)) ne vaut
que `query`, `analyze`, `predict`, `fetch_then_predict` : quatre actions **sur** les
données. Aucune valeur ne couvre une question **sur** le système. La demande est donc
*inclassable*, et non mal classée — améliorer le prompt ou le modèle n'y changerait
rien, puisqu'il n'existe littéralement aucune case où la ranger.

**Et le repli est absurde.** Le message qui déclare ne pas comprendre **nomme la
source** qu'on lui demandait (`titanic, iris…`) : l'information est disponible à
l'instant même où le système dit ne pas l'avoir. Elle y était même en dur, recopiée
dans la chaîne de caractères plutôt que lue dans le catalogue.

## 2. Comment la mesure est faite

Le runner est [`scripts/mesure_surface_conversationnelle.py`](../scripts/mesure_surface_conversationnelle.py),
sur le modèle de `live_scenarios.py` : **le vrai système**, pas la suite pytest.
Serveur LLM en place (`gemma4:e4b` servi par Ollama) et sources réelles du catalogue
— `titanic` (Postgres, deux tables jointes) et `iris` (CSV via DuckDB).

```bash
uv run python scripts/mesure_surface_conversationnelle.py --markdown /tmp/mesure.md --json /tmp/mesure.json
```

Prérequis : le serveur LLM répond, et Postgres est seedé
(`uv run python scripts/seed_titanic_postgres.py`). Une source injoignable fait
échouer la lecture de la vérité terrain — délibérément : elle changerait les
verdicts sans le dire.

> La batterie décrite ici comptait **21 questions méta et 4 témoins**. Elle en
> compte **36 et 4** depuis le §9, qui lui a ajouté quinze reformulations. Les
> vingt-et-une d'origine y sont restées à l'identique — sans quoi les deux mesures
> ne se compareraient plus.

Trois partis pris, qui sont ce qui rend la mesure rejouable :

- **Chaque question est posée dans une conversation neuve.** Une question méta ne
  doit pas devoir son succès au contexte laissé par la précédente. Ce parti pris a
  un prix, payé au §12 : la batterie ne peut rien dire d'un mécanisme multi-tours,
  et il a fallu un second runner pour la source de travail.
- **L'oracle est tiré des sources de vérité**, jamais recopié à la main : les noms du
  catalogue, les datasets du registre, les champs des schémas de features, les
  colonnes, leurs types et les comptes lus dans la source. Un oracle écrit en dur
  mesurerait la mémoire de son auteur, pas la couverture du système.
- **Le coût est compté, pas estimé** : le modèle est enveloppé dans un compteur
  d'allers-retours (`ModeleCompteur`). Un chrono ne distinguerait pas un appel LLM
  rapide d'une réponse construite sans appel du tout, et c'est exactement la
  distinction qui nous intéresse.

### Les quatre verdicts

| Verdict | Ce qu'il veut dire |
|---|---|
| **répondu correctement** | l'oracle est satisfait : la réponse contient ce que les sources de vérité disent |
| **répondu à côté** | une réponse est venue, mais pas celle-là — y compris une **clarification renvoyée** au lieu d'une réponse |
| **repli** | le message « je n'ai pas bien compris » : le planificateur n'a pas su classer |
| **erreur** | un nœud est tombé, `error` est renseigné |

L'ordre d'évaluation compte, et chaque marche a sa raison.

Un **repli** est d'abord un repli, même s'il cite par hasard une chaîne attendue —
c'est précisément l'absurdité du §1.

Une **clarification** ne compte pas comme une réponse, sauf quand la question laisse
réellement le choix ouvert (« de quels attributs as-tu besoin ? », trois modèles au
registre : renvoyer la liste des modèles *est* une réponse acceptable). Sans cette
règle, l'oracle littéral comptait « Sur quelle source veux-tu travailler : titanic,
iris ? » comme une bonne réponse à « quelles sources possèdes-tu ? » — la mesure
aurait déclaré couverte la classe même qu'on cherchait à mesurer.

### Les témoins

Quatre questions de la batterie ne sont **pas** des questions méta : ce sont de
vraies questions sur les données, avec la capacité par laquelle elles doivent
passer. Un routeur de questions méta se juge autant sur ce qu'il laisse passer que
sur ce qu'il attrape — et `temoin-colonnes-a-trous` (« quelles colonnes de la table
passengers contiennent des valeurs manquantes ? ») a la tournure d'une question de
schéma sans en être une. Leurs comptes ne sont jamais additionnés à ceux des
questions méta : ils sont là pour ne pas bouger.

## 3. Mesure du 2026-09-07, avant correction

Commit `3a91723`, `gemma4:e4b`, température 0.

### Questions méta — 21 question(s)

| Clé | Famille | Question | Réponse obtenue | Capacité | Synthèse | Appels LLM | Verdict |
|---|---|---|---|---|---|---|---|
| `sources-directe` | sources | Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ? | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu à côté** |
| `sources-reformulee` | sources | Sur quoi peux-tu travailler ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `sources-indirecte` | sources | À quelles bases de données as-tu accès ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `sources-imperative` | sources | Liste-moi tes sources de données. | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu à côté** |
| `tables-directe` | tables | Quelles tables contient la source titanic ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `tables-indirecte` | tables | Comment est structurée la base titanic ? | La base de données est structurée autour de deux tables principales : `classes` et `passengers`. 1. **Table `classes`**: Cette table définit les classes sociales à bord du Titanic. Elle contient un identifiant (`class_id`), le niveau de la classe (`level`) et une étiquette descriptive (`label`, par exemple '1re classe… | query | résumé de la récupération | 3 | **répondu correctement** |
| `colonnes-table` | colonnes | Quelles colonnes y a-t-il dans la table passengers ? | Les colonnes de la table `passengers` sont : * **passenger\_id**: L'identifiant unique du passager (clé primaire). * **name**: Le nom du passager. * **sex**: Le sexe ('female' ou 'male'). * **age**: L'âge du passager. * **sibsp**: Le nombre de frères et sœurs à bord. * **parch**: Le nombre d'enfants à bord. * **fare**… | query | résumé de la récupération | 3 | **répondu correctement** |
| `colonnes-fichier` | colonnes | Quels champs trouve-t-on dans la source iris ? | Les champs présents dans la source iris sont : `sepal_length`, `sepal_width`, `petal_length`, `petal_width`, et `species`. | query | résumé de la récupération | 3 | **répondu correctement** |
| `sens-colonne` | sens d'une colonne | Que signifie la colonne class_id de la table passengers ? | 5 lignes retournées — voir le tableau ci-dessous. | query | résumé déterministe (multi-lignes) | 4 | **répondu à côté** |
| `modeles-directe` | modèles | Quels modèles de prédiction sais-tu utiliser ? | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu à côté** |
| `modeles-indirecte` | modèles | Est-ce que tu sais faire des prédictions, et sur quoi ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `features-dataset` | features | De quels attributs as-tu besoin pour prédire la survie d'un passager du Titanic ? | Je ne peux pas encore lancer la prédiction titanic : - sex (Sexe du passager) : valeur manquante - pclass (Classe du billet (1re, 2e, 3e)) : valeur manquante - age (Âge en années) : valeur manquante - sibsp (Frères/sœurs + conjoint à bord) : valeur manquante - parch (Parents + enfants à bord) : valeur manquante - fare… | predict | relance | 1 | **répondu correctement** |
| `features-iris` | features | Quelles mesures faut-il te donner pour que tu prédises l'espèce d'un iris ? | Je ne peux pas encore lancer la prédiction iris : - sepal_length (Longueur du sépale (cm)) : Input should be a valid number (reçu : None) - sepal_width (Largeur du sépale (cm)) : Input should be a valid number (reçu : None) - petal_length (Longueur du pétale (cm)) : Input should be a valid number (reçu : None) - petal… | predict | relance | 1 | **répondu correctement** |
| `features-nue` | features | De quels attributs as-tu besoin ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `features-indirecte` | features | De quoi as-tu besoin pour prédire ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `capacites-directe` | capacités | Que sais-tu faire ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `capacites-indirecte` | capacités | À quoi sers-tu, exactement ? | Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux faire — interroger une source (titanic, iris…), une analyse ou une visualisation, ou une prédiction — et sur quelles données ? | query | clarification | 2 | **repli « je n'ai pas bien compris »** |
| `periode-directe` | période | Sur quelle période portent les données de la source titanic ? | Il n'y a aucune colonne de date ou d'heure dans le schéma des données disponibles pour déterminer la période couverte par les informations du Titanic. | query | résumé de la récupération | 3 | **répondu correctement** |
| `periode-indirecte` | période | De quand datent les données que tu as ? | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu correctement** |
| `volumetrie-table` | volumétrie | Combien de lignes contient la table passengers ? | La table `passengers` contient un total de 891 lignes. | query | résumé de la récupération | 4 | **répondu correctement** |
| `volumetrie-globale` | volumétrie | Quelle est la taille de tes données ? | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu correctement** |

| Verdict | Nombre | Part |
|---|---|---|
| répondu correctement | 9 | 43% |
| répondu à côté | 4 | 19% |
| repli « je n'ai pas bien compris » | 8 | 38% |
| erreur | 0 | 0% |
| **Total** | **21** | |

Coût mesuré : **43 appels LLM** pour 21 question(s) (moyenne 2.05 par question).

### Témoins — questions sur les données — 4 question(s)

| Clé | Famille | Question | Réponse obtenue | Capacité | Synthèse | Appels LLM | Verdict |
|---|---|---|---|---|---|---|---|
| `temoin-comptage` | témoin (données) | Combien de passagers ont survécu ? | Selon les données, 342 passagers ont survécu. | query | résumé de la récupération | 4 | **répondu correctement** |
| `temoin-maximum` | témoin (données) | Quel est l'âge du passager le plus âgé ? | L'âge du passager le plus âgé est de 80 ans. | query | résumé de la récupération | 4 | **répondu correctement** |
| `temoin-colonnes-a-trous` | témoin (données) | Quelles colonnes de la table passengers contiennent des valeurs manquantes ? | Les colonnes contenant des valeurs manquantes dans la table `passengers` sont l'âge (`age`) et le port d'embarquement (`embarked`). Il y a 177 enregistrements pour l'âge et 2 enregistrements pour le port d'embarquement qui ne contiennent pas de données. | query | résumé de la récupération | 4 | **répondu correctement** |
| `temoin-prediction` | témoin (données) | Prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80 livres, embarquée à Southampton, sans frère, sœur, parent ni enfant à bord. | Prédiction (titanic) : a survécu (probabilité 93.4%) — détail : n'a pas survécu : 6.6%, a survécu : 93.4% | predict | modèle (déterministe) | 1 | **répondu correctement** |

| Verdict | Nombre | Part |
|---|---|---|
| répondu correctement | 4 | 100% |
| répondu à côté | 0 | 0% |
| repli « je n'ai pas bien compris » | 0 | 0% |
| erreur | 0 | 0% |
| **Total** | **4** | |

Coût mesuré : **13 appels LLM** pour 4 question(s) (moyenne 3.25 par question).

## 4. Ce que ça dit de la couverture réelle

**Sur 21 questions méta : 9 correctes, 4 à côté, 8 en repli, 0 erreur.**
Une question sur trois reçoit « je n'ai pas bien compris ». Aucune n'échoue par
erreur technique : le système ne tombe pas, il refuse de comprendre.

**Le défaut est plus large que l'exemple.** La famille dont on connaissait un cas
(« de quels attributs as-tu besoin ? ») compte en réalité au moins quatre familles
en repli complet :

| Famille | Résultat |
|---|---|
| **capacités** (« que sais-tu faire ? », « à quoi sers-tu ? ») | **2/2 en repli** — l'agent ne sait rien dire de lui-même |
| **features non qualifiées** (« de quels attributs as-tu besoin ? ») | **2/2 en repli** |
| **sources** | 0/4 correctes : 2 replis, et 2 clarifications qui énumèrent la réponse sans la donner |
| **modèles** | 0/2 correctes : 1 repli, 1 clarification |

**Ce qui marche, marche par accident.** Les 9 réponses correctes ne viennent d'aucune
route dédiée. Trois mécanismes les produisent, tous détournés de leur usage :

1. **L'agent de récupération et son tool `get_schema`** (5 cas : `tables-indirecte`,
   `colonnes-table`, `colonnes-fichier`, `periode-directe`, `volumetrie-table`). Quand le
   planificateur parvient à classer la demande en `query`, l'agent SQL appelle
   `get_schema`, lit l'ontologie et la raconte. Ça fonctionne — `colonnes-table` rend
   les dix colonnes de `passengers`, `periode-directe` répond honnêtement qu'il n'y a
   aucune colonne de date — mais **au prix de 3 à 4 appels LLM** et d'une réponse
   *narrée* par le modèle : rien ne garantit qu'elle soit complète, et rien ne la
   vérifie. Le même mécanisme échoue sur `tables-directe` (repli) alors qu'il réussit
   sur `tables-indirecte` : la différence n'est pas dans la donnée, elle est dans la
   tournure.
2. **La relance de slot-filling** (2 cas : `features-dataset`, `features-iris`).
   « De quels attributs as-tu besoin pour
   prédire la survie… ? » est routé en `predict` sans features, la validation échoue,
   et la relance énumère les sept features avec leur description. La réponse est
   **juste sur le fond, fausse sur la forme** : l'utilisateur qui demande une liste
   reçoit « Je ne peux pas encore lancer la prédiction titanic : … valeur
   manquante ». D'où un malentendu tenace : on croit le modèle absent alors qu'il
   attend des valeurs — le registre le déclare et son artefact est bien là.
3. **Une clarification tolérée par l'oracle** (2 cas : `periode-indirecte`,
   `volumetrie-globale`) — cf. « ce que la mesure ne dit pas », plus bas.

**Le coût est celui d'une question sur les données.** 43 appels LLM pour 21
questions, **2,05 en moyenne**, jusqu'à 4 pour « combien de lignes contient la table
passengers ? ». Or la réponse à « quelles sources possèdes-tu ? » est **entièrement
déterminée par `sources/catalogue.yaml`** : elle est connue avant même que la
question soit lue. Deux appels LLM pour ne pas la donner sont deux appels de trop.

**Les 8 replis coûtent 2 appels chacun**, et non 1 : le planificateur rate sa sortie
structurée, `UnexpectedModelBehavior` est levée après réessai, et il n'y a aucun tour
précédent sur lequel se replier. Seize appels LLM dépensés pour dire « je n'ai pas
bien compris ».

**Les témoins passent à 4/4.** C'est la ligne de base à ne pas dégrader : toute route
ajoutée pour les questions méta doit laisser ces quatre-là exactement où elles sont.

### Ce que la mesure ne dit pas

- **Le modèle n'est pas déterministe, même à température 0.** Entre deux exécutions à
  code identique, `sources-directe` est passé de « repli » à « clarification » : le
  verdict d'**une** question peut bouger d'un cran. Les comptes par famille, eux, ont
  tenu. Un écart d'une ou deux questions entre deux rejeux n'est donc pas un signal.
- **Deux verdicts « correct » sont des tolérances assumées**, et sont marqués
  `clarification` dans la colonne Synthèse : `periode-indirecte` et
  `volumetrie-globale` reçoivent « Sur quelle source veux-tu travailler : titanic,
  iris ? », admis parce que ces deux questions ne désignent aucune source. La
  tolérance joue **contre** la démonstration d'une amélioration, ce qui est le bon
  sens du biais.
- **La batterie porte sur deux sources et trois modèles.** Une source à quarante
  tables poserait un problème de volume de réponse que ce catalogue ne révèle pas.
  Sur dix, la mesure a été faite depuis (§7) : le volume tient, mais trois défauts
  de désignation en sont sortis.

---

## 5. Ce qui a été ajouté

> **Les §5 à §8 décrivent un état qui n'est plus celui du code.** Le lexique de
> reconnaissance qu'ils présentent a été **mesuré à son plafond puis remplacé** :
> [§9](#9-le-lexique-avait-un-plafond-et-il-a-été-mesuré) et la suite. On les garde
> parce qu'ils portent les mesures d'origine et les deux décisions coûteuses qui
> tiennent toujours — la cinquième valeur de `Capability` (§6) et la frontière entre
> ce que l'agent EST et ce que les données CONTIENNENT (§8).

Un module, un nœud, un court-circuit, et un repli réécrit.

### `orchestrator/introspection.py` — les réponses, lues et non racontées

Chaque réponse est construite depuis un artefact du dépôt : le catalogue
(`sources/catalogue.yaml`), le registre (`models/registry.yaml`), les schémas de
features (`SCHEMAS`, `describe_features`), l'ontologie que la source rend elle-même,
et — quand la source en déclare un — son **dictionnaire** (nouveau champ facultatif
`dictionary`, de la même forme que sur la branche `Maxizoo`, pour que la fusion reste
simple).

**Rien depuis la mémoire du modèle.** C'est le défaut corrigé par `acfd8f5` — « décris
le dataset iris » répondu de mémoire, zéro requête, et une jolie prose sur « un
ensemble classique de classification floristique » servie comme une lecture de la
source. Le module est **pur** : il n'ouvre ni fichier ni connexion (un test le
vérifie sur son propre code source), et l'entrée/sortie reste dans un nœud du graphe.

Cinq sujets. Quatre sont entièrement déterminés par la configuration — `sources`,
`modeles`, `features`, `capacites` — et se répondent sans rien ouvrir. Le cinquième,
`schema`, exige l'ontologie de la source, donc une connexion, et se décline à quatre
niveaux de détail selon ce que la question nomme : une colonne (sa fiche, clé
étrangère et dictionnaire compris), une table (ses colonnes), une source (toutes ses
tables), rien de décidable (le tour d'horizon — les tables de chaque source, sans les
colonnes : déplier trois cents colonnes pour dire « je ne sais pas laquelle tu veux »
n'aide personne).

### Le nœud `system` et le court-circuit du plan

`Orchestrator._court_circuit_meta` reconnaît le sujet par un lexique et route vers le
nœud `system` **avant** `_peser_le_prompt`, donc avant tout appel LLM.

**Pourquoi pas une règle de `_REGLES_DU_PLAN`.** `axes-amelioration.md` désignait
`_regle_choisir_le_modele` comme point d'atterrissage pour « quelles features ? ».
Ces règles ajustent un `Plan` que le LLM a **déjà** rendu : l'aller-retour est
derrière elles. L'élargir aurait donc (a) gardé l'appel, (b) ne rattrapé que les
demandes que le LLM avait su classer en `predict` — pas le cas réellement constaté,
où il ne classe rien et où le repli s'enclenche — et (c) fait répondre à des questions
une règle dont le nom dit « choisir le modèle ». Il fallait une étape **avant** les
règles, pas une règle de plus.

**Le lexique est précis plutôt qu'exhaustif**, et il s'abstient dès qu'un marqueur de
calcul paraît (`combien`, `moyenne`, `manquant`, `histogramme`…). Se tromper de ce
côté coûte un appel LLM ; se tromper de l'autre donne une réponse fausse à une
question sur les données, sans le dire.

### Le repli, qui ne repart plus les mains vides

L'ancien message déclarait ne pas comprendre **en nommant les sources** — et il les
nommait en dur, `titanic, iris…` recopiés dans la chaîne, donc faux dès qu'un
déploiement change de catalogue. Il rend maintenant l'inventaire réel, lu dans le
catalogue et le registre, et il **finit** par la question au lieu de commencer par
elle : ce qu'on lit en dernier est ce à quoi on répond.

## 6. La décision qui coûtait cher : ne pas offrir la capacité au modèle

Le premier réflexe est d'ajouter une cinquième valeur à `Capability` et de la décrire
dans le prompt du planificateur, comme filet des tournures que le lexique ne connaît
pas. **Ça a été fait, mesuré, et retiré** — deux fois, pour deux raisons distinctes.

**Expérience 1 — la valeur annoncée dans le prompt.** Coût : **+134 tokens à chaque
requête**, questions sur les données comprises. Effet sur la batterie : **quatre
questions passées d'une bonne réponse à une mauvaise, aucune dans l'autre sens.**
« Sur quelle période portent les données ? », « quelle est la taille de tes
données ? » et « quelles colonnes contiennent des valeurs manquantes ? » étaient
routées en `describe_system` — alors que leur réponse est un `SELECT` — et
recevaient la liste des capacités. Le modèle en service généralise « décris-toi » à
toute question sur la *forme* des données.

**Expérience 2 — la valeur dans le seul `Literal`, prompt inchangé.** Le prompt
n'en parlait plus, et le modèle a continué de la choisir : **`Capability` *est* le
JSON Schema de la sortie structurée**, que le modèle lit indépendamment du prompt.
Pire, l'élargir dégrade l'extraction sur une capacité qui n'a rien à voir : « prédis
la survie d'une passagère de 1re classe de 28 ans… » ressortait avec `pcass` au lieu
de `pclass` — champ inconnu, prédiction remplacée par une relance. Le `Literal`
remis à quatre valeurs, la prédiction aboutit. Reproductible dans les deux sens.

**Décision : la capacité système n'entre pas dans `Capability`.** Elle est routée par
du code, et le graphe l'aiguille sur `system_topic` posé dans le state plutôt que sur
`plan.capability` — c'est la seule branche du graphe qui ne vient pas d'un `Plan`, et
c'est aussi ce qui la rend gratuite. Conséquence assumée : `ChatAnswer.plan` reste
**vide** pour une question méta. Ce n'est pas un oubli, c'est exact — il n'y a pas eu
de planification, parce qu'il n'y avait rien à planifier. La trace, elle, le dit en
clair : `système (sources) — sans appel LLM`.

Ce que ça préserve, et qui est le point : **le contrat que lit le modèle est
inchangé, au token près.** Le prompt du planificateur ne bouge pas d'un caractère
(`git diff` vide), le schéma de sortie non plus. Aucun chemin existant ne peut donc
être affecté par construction, et non par vérification.

## 7. Mesure du 2026-09-07, après correction

Même batterie, même serveur, même modèle. Rejouée par la même commande.

### Questions méta — 21 question(s)

| Clé | Famille | Question | Réponse obtenue | Capacité | Synthèse | Appels LLM | Verdict |
|---|---|---|---|---|---|---|---|
| `sources-directe` | sources | Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ? | J'ai accès à 2 source(s) de données : - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe. - **iris** (file) — Dataset Iris (fichier CSV local) — mesures des sépales et pétales (sepal_length, sepal_width, petal_length, petal_… | — | système (déterministe) | 0 | **répondu correctement** |
| `sources-reformulee` | sources | Sur quoi peux-tu travailler ? | J'ai accès à 2 source(s) de données : - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe. - **iris** (file) — Dataset Iris (fichier CSV local) — mesures des sépales et pétales (sepal_length, sepal_width, petal_length, petal_… | — | système (déterministe) | 0 | **répondu correctement** |
| `sources-indirecte` | sources | À quelles bases de données as-tu accès ? | J'ai accès à 2 source(s) de données : - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe. - **iris** (file) — Dataset Iris (fichier CSV local) — mesures des sépales et pétales (sepal_length, sepal_width, petal_length, petal_… | — | système (déterministe) | 0 | **répondu correctement** |
| `sources-imperative` | sources | Liste-moi tes sources de données. | J'ai accès à 2 source(s) de données : - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe. - **iris** (file) — Dataset Iris (fichier CSV local) — mesures des sépales et pétales (sepal_length, sepal_width, petal_length, petal_… | — | système (déterministe) | 0 | **répondu correctement** |
| `tables-directe` | tables | Quelles tables contient la source titanic ? | La source `titanic` contient 2 table(s) (postgresql) : - **classes** (3 colonnes) : - `class_id` (INTEGER) — clé primaire ; obligatoire - `level` (INTEGER) — obligatoire - `label` (TEXT) — obligatoire ; valeurs : '1re classe', '2e classe', '3e classe' - **passengers** (10 colonnes) : - `passenger_id` (INTEGER) — clé p… | — | système (déterministe) | 0 | **répondu correctement** |
| `tables-indirecte` | tables | Comment est structurée la base titanic ? | La source `titanic` contient 2 table(s) (postgresql) : - **classes** (3 colonnes) : - `class_id` (INTEGER) — clé primaire ; obligatoire - `level` (INTEGER) — obligatoire - `label` (TEXT) — obligatoire ; valeurs : '1re classe', '2e classe', '3e classe' - **passengers** (10 colonnes) : - `passenger_id` (INTEGER) — clé p… | — | système (déterministe) | 0 | **répondu correctement** |
| `colonnes-table` | colonnes | Quelles colonnes y a-t-il dans la table passengers ? | La table `passengers` de la source `titanic` : - **passengers** (10 colonnes) : - `passenger_id` (INTEGER) — clé primaire ; obligatoire - `name` (TEXT) - `sex` (TEXT) — obligatoire ; valeurs : 'female', 'male' - `age` (DOUBLE PRECISION) - `sibsp` (INTEGER) - `parch` (INTEGER) - `fare` (DOUBLE PRECISION) - `embarked` (… | — | système (déterministe) | 0 | **répondu correctement** |
| `colonnes-fichier` | colonnes | Quels champs trouve-t-on dans la source iris ? | La table `iris` de la source `iris` : - **iris** (5 colonnes) : - `sepal_length` (DOUBLE) - `sepal_width` (DOUBLE) - `petal_length` (DOUBLE) - `petal_width` (DOUBLE) - `species` (VARCHAR) — valeurs : 'setosa', 'versicolor', 'virginica' | — | système (déterministe) | 0 | **répondu correctement** |
| `sens-colonne` | sens d'une colonne | Que signifie la colonne class_id de la table passengers ? | Dans la table `passengers`, la colonne `class_id` est de type INTEGER. Elle est obligatoire (NOT NULL). C'est une **clé étrangère** : elle référence `classes(class_id)` — le sens de la valeur se lit dans la table `classes`. | — | système (déterministe) | 0 | **répondu correctement** |
| `modeles-directe` | modèles | Quels modèles de prédiction sais-tu utiliser ? | Je dispose de 3 modèle(s) de prédiction : - **california_housing** (régression, en centaines de milliers de dollars) — cible `MedHouseVal` — Prix médian des logements d'un îlot californien (recensement 1990) — HistGradientBoostingRegressor. - **iris** (classification) — cible `species` — Espèce d'iris à partir des mes… | — | système (déterministe) | 0 | **répondu correctement** |
| `modeles-indirecte` | modèles | Est-ce que tu sais faire des prédictions, et sur quoi ? | Je dispose de 3 modèle(s) de prédiction : - **california_housing** (régression, en centaines de milliers de dollars) — cible `MedHouseVal` — Prix médian des logements d'un îlot californien (recensement 1990) — HistGradientBoostingRegressor. - **iris** (classification) — cible `species` — Espèce d'iris à partir des mes… | — | système (déterministe) | 0 | **répondu correctement** |
| `features-dataset` | features | De quels attributs as-tu besoin pour prédire la survie d'un passager du Titanic ? | Pour prédire avec le modèle **titanic** (classification de `survived`), il me faut 7 attribut(s) : * sex — Sexe du passager ; valeurs autorisées : 'male', 'female' * pclass — Classe du billet (1re, 2e, 3e) ; valeurs autorisées : 1, 2, 3 * age — Âge en années * sibsp — Frères/sœurs + conjoint à bord * parch — Parents +… | — | système (déterministe) | 0 | **répondu correctement** |
| `features-iris` | features | Quelles mesures faut-il te donner pour que tu prédises l'espèce d'un iris ? | Pour prédire avec le modèle **iris** (classification de `species`), il me faut 4 attribut(s) : * sepal_length — Longueur du sépale (cm) * sepal_width — Largeur du sépale (cm) * petal_length — Longueur du pétale (cm) * petal_width — Largeur du pétale (cm) Donne-les-moi comme tu veux, en une phrase : j'en extrais les va… | — | système (déterministe) | 0 | **répondu correctement** |
| `features-nue` | features | De quels attributs as-tu besoin ? | Ça dépend du modèle. Voici ce que chacun attend : - **california_housing** (regression) : `med_inc`, `house_age`, `ave_rooms`, `ave_bedrms`, `population`, `ave_occup`, `latitude`, `longitude` - **iris** (classification) : `sepal_length`, `sepal_width`, `petal_length`, `petal_width` - **titanic** (classification) : `se… | — | système (déterministe) | 0 | **répondu correctement** |
| `features-indirecte` | features | De quoi as-tu besoin pour prédire ? | Ça dépend du modèle. Voici ce que chacun attend : - **california_housing** (regression) : `med_inc`, `house_age`, `ave_rooms`, `ave_bedrms`, `population`, `ave_occup`, `latitude`, `longitude` - **iris** (classification) : `sepal_length`, `sepal_width`, `petal_length`, `petal_width` - **titanic** (classification) : `se… | — | système (déterministe) | 0 | **répondu correctement** |
| `capacites-directe` | capacités | Que sais-tu faire ? | Je suis un agent d'analyse de données. Je sais : - **interroger une source en SQL** — comptages, agrégats, pourcentages, listes filtrées, jointures ; - **analyser et visualiser** — du code Python exécuté en bac à sable (tests statistiques, ACP, figures) ; - **prédire un cas** dont tu me donnes les attributs (« une pas… | — | système (déterministe) | 0 | **répondu correctement** |
| `capacites-indirecte` | capacités | À quoi sers-tu, exactement ? | Je suis un agent d'analyse de données. Je sais : - **interroger une source en SQL** — comptages, agrégats, pourcentages, listes filtrées, jointures ; - **analyser et visualiser** — du code Python exécuté en bac à sable (tests statistiques, ACP, figures) ; - **prédire un cas** dont tu me donnes les attributs (« une pas… | — | système (déterministe) | 0 | **répondu correctement** |
| `periode-directe` | période | Sur quelle période portent les données de la source titanic ? | Il n'y a aucune colonne de date ou d'heure dans le schéma des données disponibles pour déterminer la période couverte par les informations du Titanic. | query | résumé de la récupération | 3 | **répondu correctement** |
| `periode-indirecte` | période | De quand datent les données que tu as ? | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu correctement** |
| `volumetrie-table` | volumétrie | Combien de lignes contient la table passengers ? | La table `passengers` contient un total de 891 lignes. | query | résumé de la récupération | 4 | **répondu correctement** |
| `volumetrie-globale` | volumétrie | Quelle est la taille de tes données ? | Sur quelle source veux-tu travailler : titanic, iris ? | query | clarification | 1 | **répondu correctement** |

| Verdict | Nombre | Part |
|---|---|---|
| répondu correctement | 21 | 100% |
| répondu à côté | 0 | 0% |
| repli « je n'ai pas bien compris » | 0 | 0% |
| erreur | 0 | 0% |
| **Total** | **21** | |

Coût mesuré : **9 appels LLM** pour 21 question(s) (moyenne 0.43 par question).

### Témoins — questions sur les données — 4 question(s)

| Clé | Famille | Question | Réponse obtenue | Capacité | Synthèse | Appels LLM | Verdict |
|---|---|---|---|---|---|---|---|
| `temoin-comptage` | témoin (données) | Combien de passagers ont survécu ? | Selon les données, 342 passagers ont survécu. | query | résumé de la récupération | 4 | **répondu correctement** |
| `temoin-maximum` | témoin (données) | Quel est l'âge du passager le plus âgé ? | L'âge du passager le plus âgé est de 80 ans. | query | résumé de la récupération | 4 | **répondu correctement** |
| `temoin-colonnes-a-trous` | témoin (données) | Quelles colonnes de la table passengers contiennent des valeurs manquantes ? | Les colonnes de la table `passengers` qui contiennent des valeurs manquantes sont `age` et `embarked`. Plus précisément, la colonne `age` présente 177 valeurs manquantes, tandis que la colonne `embarked` en contient 2. | analyze | LLM | 3 | **répondu à côté** |
| `temoin-prediction` | témoin (données) | Prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80 livres, embarquée à Southampton, sans frère, sœur, parent ni enfant à bord. | Prédiction (titanic) : a survécu (probabilité 93.4%) — détail : n'a pas survécu : 6.6%, a survécu : 93.4% | predict | modèle (déterministe) | 1 | **répondu correctement** |

| Verdict | Nombre | Part |
|---|---|---|
| répondu correctement | 3 | 75% |
| répondu à côté | 1 | 25% |
| repli « je n'ai pas bien compris » | 0 | 0% |
| erreur | 0 | 0% |
| **Total** | **4** | |

Coût mesuré : **12 appels LLM** pour 4 question(s) (moyenne 3.00 par question).

## 8. Le bilan

| | Avant (`3a91723`) | Après |
|---|---|---|
| Répondu correctement | 9 / 21 (43 %) | **21 / 21 (100 %)** |
| Répondu à côté | 4 | 0 |
| Repli « je n'ai pas bien compris » | 8 | **0** |
| Erreur | 0 | 0 |
| Appels LLM (21 questions méta) | 43 — 2,05 par question | **9 — 0,43 par question** |
| Témoins (questions sur les données) | 4 / 4 | 3 / 4 (voir plus bas) |

**Douze questions changent de verdict, toutes dans le bon sens.** Les huit replis
disparaissent : plus aucune question méta ne reçoit « je n'ai pas bien compris ».

**Le coût s'effondre là où il n'avait pas lieu d'être.** Dix-sept des vingt-et-une
questions se répondent maintenant à **zéro appel LLM** — et la trace le prouve, le
nœud `plan` n'y compte plus aucun token. Les neuf appels restants sont ceux des
quatre questions que le lexique laisse délibérément passer, parce que leur réponse
n'est pas dans la configuration.

**Le coût d'une question méta, précisément :**

| Question | Avant | Après |
|---|---|---|
| « quelles sources possèdes-tu ? » | 2 appels, ~11 s | **0 appel**, ~0 ms |
| « quelles colonnes dans passengers ? » | 3 appels | **0 appel** (une connexion, pas d'appel LLM) |
| « que signifie class_id ? » | 4 appels | **0 appel** |
| « de quels attributs as-tu besoin ? » | 2 appels (repli) | **0 appel** |
| « combien de lignes dans passengers ? » | 4 appels | 4 appels — *inchangé, et c'est voulu* |

### La frontière, et pourquoi elle est là

Quatre questions de la batterie **restent** routées vers `query`, et c'est une
décision, pas un oubli : **période** et **volumétrie**. Leur réponse ne se lit pas
dans la configuration — elle exige un `MIN`/`MAX` ou un `COUNT` sur les données. Le
lexique s'en abstient donc, et le SQL les traite bien : « il n'y a aucune colonne de
date ou d'heure dans le schéma » est une réponse juste, fondée, et que
l'introspection n'aurait pas mieux formulée. La règle est simple à énoncer :
**`describe_system` répond sur ce que l'agent EST, jamais sur ce que les données
CONTIENNENT.**

### Le témoin qui bouge, et ce qu'il dit

`temoin-colonnes-a-trous` (« quelles colonnes de la table passengers contiennent des
valeurs manquantes ? ») était routé en `query`, il l'est maintenant en `analyze`.
**Ce n'est pas dû au changement**, et c'est vérifiable : le prompt du planificateur
est identique au caractère près (`git diff` vide sur `prompts/`) et le schéma de
sortie aussi, donc l'entrée du planificateur pour cette question n'a pas bougé. C'est
la non-détermination du modèle entre deux sessions, déjà relevée au §4.

Le contenu de la réponse, lui, est **juste** : « les colonnes `age` et `embarked`…
177 valeurs manquantes pour `age`, 2 pour `embarked` » — les chiffres exacts de la
base. Seule la route diffère, plus coûteuse. Le témoin épingle la capacité et non
seulement le contenu, et il fait exactement son travail en le signalant.

Le lexique, lui, laisse bien passer cette question : il s'abstient sur `manquant`, et
un test unitaire le verrouille. *(Depuis le §9, c'est le prompt de l'agent système
qui porte cette abstention, et les témoins qui la surveillent : un test unitaire ne
peut plus la verrouiller, c'est devenu un comportement de modèle.)*

### Ce qui reste ouvert

- **La non-détermination du planificateur** rend le verdict d'une question ou deux
  instable entre deux rejeux, dans les deux sens. Les dix-sept questions à zéro appel
  LLM, en revanche, sont **déterministes par construction** : aucun modèle
  n'intervient. C'est un effet de bord agréable de la correction — la moitié de la
  batterie ne peut plus varier.
- **Un incident non reproductible** a été observé une fois sur `temoin-prediction`
  (« la prédiction n'a pas pu être lancée », référence d'incident), pendant une
  exécution intermédiaire où `Capability` portait encore cinq valeurs. Il n'a pas
  reparu depuis le retour à quatre, ni sur six exécutions ciblées. Le runner
  enregistre désormais **la trace complète** dans son journal JSON, précisément pour
  qu'un échec vu une fois laisse de quoi être diagnostiqué au rejeu.
- **Une source à quarante tables** n'est pas couverte par cette batterie. Elle l'a
  été depuis sur **dix** — la base Maxizoo, schéma en étoile de dix tables et
  soixante-dix-neuf colonnes, au report de `main` dans `maxizoo-durci`. Trois
  défauts en sont sortis, que deux tables jouets ne pouvaient pas produire ; ils
  sont corrigés, et les mesures sont ci-dessous.

### Le tour d'horizon sur dix tables (mesuré sur la base Maxizoo réelle)

| Question | Réponse | Taille | Verdict |
|---|---|---|---|
| « Quelles sources as-tu ? » | la source, son type, sa description | 659 c. / 5 l. | lisible |
| « Quelles tables ? » | les dix noms, sur une ligne | 296 c. / 3 l. | lisible |
| « Quelles colonnes dans la table sales_daily ? » | les 8 colonnes + l'extrait du dictionnaire | 1 157 c. / 20 l. | lisible |
| « Quelles colonnes a la source maxizoo ? » | les 10 tables et leurs 79 colonnes | 4 979 c. / 72 l. / ~1 668 tokens | **long, et c'est ce qui est demandé** |

Le seul cas volumineux est celui où l'utilisateur demande **toutes** les colonnes
de **toute** la source : 72 lignes, l'équivalent d'un `\d+` sur dix tables. Le
réduire serait répondre à côté. À quarante tables il faudrait probablement un
palier de plus (les tables, puis les colonnes à la demande) ; à dix, la question
et sa réponse sont de la même taille.

### Les trois défauts que dix tables ont montrés

1. **Le mot qui introduit la source était pris pour une colonne.** « Quelles
   colonnes a la SOURCE maxizoo ? » répondait « dans la table `weather`, la
   colonne `source` est de type VARCHAR » — `weather.source` existe. Réponse
   fausse, sans le dire. Une base réelle a des colonnes qui portent les noms du
   métier ; `titanic` et `iris` n'en avaient aucune.
2. **Le sens d'une colonne exigeait le mot « colonne ».** « Que signifie
   store_id ? » partait au planificateur, qui la classait en `query` et faisait
   écrire un `SELECT` sur une colonne dont on demandait le SENS — qui n'est pas
   dans les données, il est dans le dictionnaire.
3. **Une clé répétée était décrite depuis n'importe quelle table.** `store_id`
   vit dans six des dix tables ; décrit depuis `promo_calendar`, il ressortait
   avec deux valeurs sur treize. Il est maintenant lu là où il est clé primaire.

Le premier et le troisième donnaient une réponse **fausse sans le dire** — le
défaut le plus cher de ce module, et celui que ce rapport était fait pour
traquer.

---

## 9. Le lexique avait un plafond, et il a été mesuré

Les §5 à §8 s'arrêtaient sur 21/21. Le propriétaire a rouvert l'application et
posé la même question autrement.

> Mesuré à l'instant, sur dix formulations naturelles de la MÊME question
> (« quelles données as-tu ? ») : **trois court-circuitées, sept parties au
> planificateur** — qui les classe en `query` et écrit du SQL, ou tombe dans le
> repli.

Les sept : « tu as acces à quelles données », « sur quoi je peux travailler ? »,
« c'est quoi ton périmètre ? », « de quoi disposes-tu ? », « montre-moi ce que tu
as », « tu bosses sur quoi ? », « liste tes bases ».

**Le 21/21 du §7 ne mesurait pas la couverture, il mesurait le lexique contre la
batterie qui avait servi à l'écrire.** C'est le défaut classique d'un oracle et
de sa liste écrits de la même main, le même jour : la mesure n'était pas fausse,
son échantillon l'était. Un lexique de tournures est une **liste**, et la famille
« parle-moi de toi » est **ouverte** — il n'existe pas de version exhaustive de
cette liste, seulement des versions plus longues.

Second grief, et il porte sur la réponse et non sur le routage :

> La réponse, quand elle arrive, est un **gabarit déterministe**. Le
> propriétaire le voit et n'en veut pas : « ce n'est même pas le LLM qui
> répond ».

### La batterie, élargie de quinze reformulations

Les vingt-et-une questions d'origine restent, à l'identique — sans quoi les deux
mesures ne se comparent plus. S'y ajoutent **quinze reformulations**, dont les
sept nommées ci-dessus (marquées ★), écrites **telles quelles** : fautes et
accents manquants compris (« tu as acces à quelles données »). Les corriger
mesurerait un utilisateur qui n'existe pas. Les huit autres portent sur les
familles voisines — capacités, colonnes, modèles, features — parce que rien ne
disait qu'elles tenaient mieux : leur lexique est écrit de la même main.

Quarante questions en tout : **36 méta et 4 témoins**.

### Mesure du 2026-09-07, avant

Commit `ae9aeba` — le lexique en place, celui du §7.

| | 21 questions d'origine | 15 reformulations | dont les 7 nommées ★ | Total (36) |
|---|---|---|---|---|
| Répondu correctement | **21 / 21** | **3 / 15** | **0 / 7** | 24 / 36 |
| Répondu à côté | 0 | 5 | 3 | 5 |
| Repli « je n'ai pas bien compris » | 0 | 7 | 4 | 7 |
| Appels LLM | 9 | 28 | 13 | 37 |

**Les vingt-et-une d'origine tiennent à 100 %, les quinze nouvelles à 20 %, et
les sept relevées en usage réel à zéro.** Le constat du propriétaire est
reproduit à l'identique : ce n'est pas une question de modèle ni de prompt, c'est
le plafond d'une liste.

Toutes les quinze partent au planificateur, et **toutes sont classées `query`** —
du SQL écrit pour répondre à une question de configuration — sauf
`features-familier`, classée `predict`, qui reçoit une relance de slot-filling.

| Clé | Question | Avant — verdict | Après — ce qui a répondu | Après — verdict |
|---|---|---|---|---|
| `sources-acces-familier` ★ | tu as acces à quelles données | **repli « je n'ai pas bien compris »** | **le modèle**, sur `sources_de_donnees` | **répondu correctement** |
| `sources-premiere-personne` ★ | sur quoi je peux travailler ? | **répondu à côté** | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction` | **répondu correctement** |
| `sources-perimetre` ★ | c'est quoi ton périmètre ? | **repli « je n'ai pas bien compris »** | **le modèle**, sur `capacites_de_l_agent` | **répondu correctement** |
| `sources-disposes` ★ | de quoi disposes-tu ? | **repli « je n'ai pas bien compris »** | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction` | **répondu correctement** |
| `sources-montre-moi` ★ | montre-moi ce que tu as | **répondu à côté** | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction, attributs_d_un_modele, schema_d_une_source` | **répondu correctement** |
| `sources-tu-bosses` ★ | tu bosses sur quoi ? | **repli « je n'ai pas bien compris »** | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction` | **répondu correctement** |
| `sources-liste-bases` ★ | liste tes bases | **répondu à côté** | **le modèle**, sur `sources_de_donnees` | **répondu correctement** |
| `sources-comme-donnees` | qu'est-ce que tu as comme données ? | **répondu à côté** | **le modèle**, sur `sources_de_donnees` | **répondu correctement** |
| `sources-consulter` | quelles infos tu peux consulter ? | **repli « je n'ai pas bien compris »** | **le modèle**, sur `sources_de_donnees` | **répondu correctement** |
| `capacites-demander-quoi` | je peux te demander quoi ? | **repli « je n'ai pas bien compris »** | **le modèle**, sur `capacites_de_l_agent` | **répondu correctement** |
| `capacites-capable` | dis-moi ce dont tu es capable | **repli « je n'ai pas bien compris »** | **le modèle**, sur `capacites_de_l_agent` | **répondu correctement** |
| `colonnes-familier` | il y a quoi comme colonnes dans passengers ? | **répondu correctement** | **le modèle**, sur `schema_d_une_source` | **répondu correctement** |
| `colonnes-champs-classes` | c'est quoi les champs de la table classes ? | **répondu correctement** | **le modèle**, sur `schema_d_une_source` | **répondu correctement** |
| `modeles-previsions` | quel genre de prévisions tu peux faire ? | **répondu à côté** | **le modèle**, sur `modeles_de_prediction` | **répondu correctement** |
| `features-familier` | il te faut quoi pour deviner l'espèce d'un iris ? | **répondu correctement** | **les faits** (ceinture) | **répondu correctement** |
★ = l'une des sept formulations relevées par le propriétaire en usage réel.


## 10. Ce qui a remplacé le lexique : un outil, et le modèle qui décide

Le lexique est retiré. La reconnaissance et la formulation sont rendues au
modèle ; ce qui reste du code, c'est **ce que le modèle a le droit de savoir**.

### L'agent système et ses cinq outils

[`orchestrator/systeme.py`](../src/data_analyst_agent/orchestrator/systeme.py) —
un agent `pydantic-ai` à cinq outils typés, sur le modèle des trois outils de
l'agent SQL, qui fonctionnent déjà avec `gemma4:e4b`. Chaque outil rend un texte
de `introspection.py`, donc un texte **construit depuis un artefact du dépôt**.

| Outil | Source de vérité | Connexion ? |
|---|---|---|
| `sources_de_donnees` | `sources/catalogue.yaml` | non |
| `modeles_de_prediction` | `models/registry.yaml` | non |
| `attributs_d_un_modele` | `SCHEMAS` + `describe_features` | non |
| `capacites_de_l_agent` | les valeurs de `Capability`, plus l'inventaire | non |
| `schema_d_une_source` | l'ontologie de la source (+ son dictionnaire) | **oui** |

Le nœud `system` passe **en tête du graphe** : la première question d'un tour
n'est plus « quelle capacité ? » mais « est-ce une question sur moi ? ».

### Le signal de routage est l'appel d'outil, jamais le texte

Le prompt demande au modèle de répondre `AUTRE` quand la question porte sur les
données. **Rien ne repose sur ce mot** : `outils_appeles` vide veut dire « ce
n'est pas pour moi », et le tour repart au planificateur. Un modèle qui
paraphrase la consigne, ou qui répond de mémoire sans rien regarder, ne peut donc
pas se faire servir — c'est la même règle que `RetrievalResult.grounded` pour
l'agent SQL, dont l'origine est le même incident (`acfd8f5`).

### La ceinture : le déterministe n'est pas jeté, il change de rôle

Les textes de `introspection.py` ont maintenant **deux emplois** : ils sont la
matière que le modèle formule, et le repli servi tel quel quand sa formulation ne
les porte pas. Deux défauts la disqualifient (`defaut_de_fondation`) :

- **un nom qu'aucun fait ne porte** — c'est `acfd8f5` par une autre porte. Un
  modèle à qui l'on demande les tables d'une base « familière » sait en citer de
  mémoire, et un nom inventé est plus nocif qu'une réponse absente : il a l'air
  d'une lecture de la source. Un test l'épingle : `flights` cité par le modèle,
  absent du catalogue, n'atteint jamais l'utilisateur.
- **un nom que l'outil a rendu et que la réponse omet** — une liste incomplète
  n'est pas une réponse à « quelles colonnes ? », et c'est le défaut mesuré au
  §4 : la version narrée avait laissé tomber deux colonnes sur dix.

Sont comptés pour des noms techniques, côté modèle, ce qu'il met **entre accents
graves** et tout jeton **à blanc souligné**. Jamais le gras : il en met sur des
mots français ordinaires, et les compter ferait écarter des réponses justes.

**La première version de cette ceinture était trop stricte, et la mesure l'a
dit.** Elle rejetait quinze formulations sur trente-six, dont trois à tort :

| Rejet à tort | Cause | Correction |
|---|---|---|
| « il me faut `sex` » | `describe_features` rend ses champs **nus** (`* sex — Sexe du passager`) : `sex` n'était pas dans les noms « connus » | côté FAITS, tout jeton de forme d'identifiant compte, décoré ou non |
| « elle pointe vers `classes` » | les faits écrivent `` `classes(class_id)` `` : la portée entière n'est pas un identifiant | les identifiants sont extraits **à l'intérieur** des portées |
| la liste des 10 colonnes, sans redire `titanic` | `titanic` figurait dans l'**en-tête** (« La table `passengers` de la source `titanic` : ») | seul ce qu'une **puce** nomme est exigible ; l'en-tête et la prose sont du contexte |

D'où la règle en une phrase : **ce qu'une puce nomme doit revenir, ce qu'elle en
dit est du contexte.** « Quels modèles ? » n'oblige donc plus à recopier la
colonne cible de chacun. Les trois cas ont un test.

### Ce qui n'a PAS bougé, et c'est le point

`Capability` reste à quatre valeurs, et **le prompt du planificateur ne bouge pas
d'un caractère**. Un outil est autre chose qu'une capacité : il ne touche pas le
JSON Schema de la sortie structurée, qui est le contrat mesuré deux fois au §6.
Le chemin des questions sur les données est donc inchangé par construction, et
non par vérification — les témoins le confirment.

### Le coût, assumé

Cette classe de questions repasse par le LLM, et **une question sur les données
paie un aller-retour de plus** : le nœud système est en tête de chaque tour.
C'est le prix du routage par le modèle, il est mesuré au §11, et il n'a pas
d'alternative — un lexique de mots-clés est ce qu'on vient de retirer.

Deux tours sont **exemptés**, parce que le message y répond à une question déjà
posée et n'est donc pas à interpréter : une prédiction en attente de features, et
un choix de source (§12).


## 11. Mesure du 2026-09-07, après le routage par le modèle

Même batterie, même serveur, même modèle, même commande. Le tableau ci-dessous
est le **troisième rejeu** ; les deux autres sont résumés juste après.

### Questions méta — 36 question(s)

| Clé | Question | Réponse obtenue | Ce qui a répondu | Appels LLM | Verdict |
|---|---|---|---|---|---|
| `sources-directe` | Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ? | J'ai accès à deux sources de données : `titanic` (une base de données PostgreSQL contenant des informations sur la survie, le sexe, l'âge, le tarif et la classe) et `iris` (un fichier CSV local avec les mesures des sépales et pétales ainsi… | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `sources-reformulee` | Sur quoi peux-tu travailler ? | Je suis un agent d'analyse de données. Je sais : * **interroger une source en SQL** — comptages, agrégats, pourcentages, listes filtrées, jointures ; * **analyser et visualiser** — du code Python exécuté en bac à sable (tests statistiques,… | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees` | 2 | **répondu correctement** |
| `sources-indirecte` | À quelles bases de données as-tu accès ? | J'ai accès à deux sources de données : `titanic` (une base PostgreSQL contenant des informations sur la survie, le sexe, l'âge, le tarif et la classe) et `iris` (un fichier CSV local avec les mesures des sépales et pétales ainsi que l'espè… | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `sources-imperative` | Liste-moi tes sources de données. | J'ai accès à deux sources de données : `titanic` (une base PostgreSQL contenant des informations sur la survie, le sexe, l'âge, le tarif et la classe) et `iris` (un fichier CSV local avec les mesures des sépales et pétales ainsi que l'espè… | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `tables-directe` | Quelles tables contient la source titanic ? | La source `titanic` contient 2 table(s) (postgresql) : - **classes** (3 colonnes) : - `class_id` (INTEGER) — clé primaire ; obligatoire - `level` (INTEGER) — obligatoire - `label` (TEXT) — obligatoire ; valeurs : '1re classe', '2e classe',… | **les faits** (ceinture) | 2 | **répondu correctement** |
| `tables-indirecte` | Comment est structurée la base titanic ? | La source `titanic` est structurée autour de deux tables. 1. **`classes`** : Cette table contient trois colonnes : * `class_id` (clé primaire, INTEGER) : Identifiant de la classe. * `level` (INTEGER) : Niveau de la classe. * `label` (TEXT)… | **le modèle**, sur `schema_d_une_source` | 2 | **répondu correctement** |
| `colonnes-table` | Quelles colonnes y a-t-il dans la table passengers ? | Les colonnes de la table `passengers` sont les suivantes : * `passenger_id` (clé primaire, obligatoire) * `name` * `sex` (obligatoire ; valeurs : 'female', 'male') * `age` * `sibsp` * `parch` * `fare` * `embarked` (valeurs : 'C', 'Q', 'S')… | **le modèle**, sur `schema_d_une_source` | 2 | **répondu correctement** |
| `colonnes-fichier` | Quels champs trouve-t-on dans la source iris ? | Les champs trouvés dans la source `iris` sont les suivants : * **sepal\_length** (DOUBLE) * **sepal\_width** (DOUBLE) * **petal\_length** (DOUBLE) * **petal\_width** (DOUBLE) * **species** (VARCHAR) — valeurs : 'setosa', 'versicolor', 'vir… | **le modèle**, sur `schema_d_une_source` | 2 | **répondu correctement** |
| `sens-colonne` | Que signifie la colonne class_id de la table passengers ? | La colonne `class_id` de la table `passengers` est de type `INTEGER`, est obligatoire et représente une clé étrangère qui fait référence à `classes(class_id)`. Le sens de cette valeur doit être lu dans la table `classes`. Souhaitez-vous co… | **le modèle**, sur `schema_d_une_source` | 2 | **répondu correctement** |
| `modeles-directe` | Quels modèles de prédiction sais-tu utiliser ? | Je dispose des modèles suivants : `california_housing`, `iris` et `titanic`. N'hésite pas à me demander plus de détails sur un modèle en particulier. | **le modèle**, sur `modeles_de_prediction` | 2 | **répondu correctement** |
| `modeles-indirecte` | Est-ce que tu sais faire des prédictions, et sur quoi ? | Je suis un agent d'analyse de données. Je sais interroger une source en SQL (comptages, agrégats, pourcentages, listes filtrées, jointures), analyser et visualiser des données via du code Python exécuté en bac à sable (tests statistiques, … | **le modèle**, sur `capacites_de_l_agent` | 2 | **répondu correctement** |
| `features-dataset` | De quels attributs as-tu besoin pour prédire la survie d'un passager du Titanic ? | Je dois fournir les sept attributs suivants pour prédire la survie avec le modèle `titanic` : * `sex` — Sexe du passager ; valeurs autorisées : 'male', 'female' * `pclass` — Classe du billet (1re, 2e, 3e) ; valeurs autorisées : 1, 2, 3 * `… | **le modèle**, sur `attributs_d_un_modele` | 2 | **répondu correctement** |
| `features-iris` | Quelles mesures faut-il te donner pour que tu prédises l'espèce d'un iris ? | Selon le modèle `iris`, pour prédire l'espèce (`species`), il me faut quatre attributs : `sepal_length` (Longueur du sépale en cm), `sepal_width` (Largeur du sépale en cm), `petal_length` (Longueur du pétale en cm) et `petal_width` (Largeu… | **le modèle**, sur `attributs_d_un_modele` | 2 | **répondu correctement** |
| `features-nue` | De quels attributs as-tu besoin ? | Selon le modèle que vous souhaitez utiliser, les attributs requis varient : * **`california_housing`** (régression) nécessite : `med_inc`, `house_age`, `ave_rooms`, `ave_bedrms`, `population`, `ave_occup`, `latitude`, et `longitude`. * **`… | **le modèle**, sur `attributs_d_un_modele` | 3 | **répondu correctement** |
| `features-indirecte` | De quoi as-tu besoin pour prédire ? | Selon le modèle que vous souhaitez utiliser, les attributs requis varient. Je peux vous fournir un aperçu des modèles disponibles : * **`california_housing`** (régression) nécessite les attributs suivants : `med_inc`, `house_age`, `ave_roo… | **le modèle**, sur `attributs_d_un_modele` | 2 | **répondu correctement** |
| `capacites-directe` | Que sais-tu faire ? | Je suis un agent d'analyse de données. Je sais interroger une source en SQL (comptages, agrégats, pourcentages, listes filtrées, jointures), analyser et visualiser des données via du code Python exécuté en bac à sable (tests statistiques, … | **le modèle**, sur `capacites_de_l_agent` | 2 | **répondu correctement** |
| `capacites-indirecte` | À quoi sers-tu, exactement ? | Je suis un agent d'analyse de données. Je sais : - **interroger une source en SQL** — comptages, agrégats, pourcentages, listes filtrées, jointures ; - **analyser et visualiser** — du code Python exécuté en bac à sable (tests statistiques,… | **le modèle**, sur `capacites_de_l_agent` | 2 | **répondu correctement** |
| `periode-directe` | Sur quelle période portent les données de la source titanic ? | Il n'y a aucune colonne de date ou d'heure dans le schéma des données disponibles pour déterminer la période couverte par les informations du Titanic. | **le planificateur** | 4 | **répondu correctement** |
| `periode-indirecte` | De quand datent les données que tu as ? | J'ai accès à 2 source(s) de données : - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe. - **iris** (file) — Dataset Iris (fichier CSV local) —… | **le planificateur** | 2 | **répondu correctement** |
| `volumetrie-table` | Combien de lignes contient la table passengers ? | La table `passengers` contient un total de 891 lignes. | **le planificateur** | 5 | **répondu correctement** |
| `volumetrie-globale` | Quelle est la taille de tes données ? | J'ai accès à 2 source(s) de données : - **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe. - **iris** (file) — Dataset Iris (fichier CSV local) —… | **le planificateur** | 2 | **répondu correctement** |
| `sources-acces-familier` | tu as acces à quelles données | J'ai accès à deux sources de données : `titanic` (une base de données PostgreSQL contenant des informations sur la survie, le sexe, l'âge, le tarif et la classe) et `iris` (un fichier CSV local avec les mesures des sépales et pétales ainsi… | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `sources-premiere-personne` | sur quoi je peux travailler ? | Vous pouvez travailler sur trois axes principaux : les données disponibles, les modèles de prédiction ou en me demandant d'analyser des données spécifiques. **Mes capacités :** Je suis un agent d'analyse de données et je peux : * Interroge… | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction` | 2 | **répondu correctement** |
| `sources-perimetre` | c'est quoi ton périmètre ? | Je suis un agent d'analyse de données. Je sais interroger une source en SQL (comptages, agrégats, pourcentages, listes filtrées, jointures), analyser et visualiser du code Python exécuté en bac à sable (tests statistiques, ACP, figures), p… | **le modèle**, sur `capacites_de_l_agent` | 2 | **répondu correctement** |
| `sources-disposes` | de quoi disposes-tu ? | Je dispose de plusieurs éléments : des capacités d'analyse, deux sources de données et trois modèles de prédiction. ### Mes capacités Je suis un agent d'analyse de données. Je sais : * **interroger une source en SQL** — comptages, agrégats… | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction` | 2 | **répondu correctement** |
| `sources-montre-moi` | montre-moi ce que tu as | Je suis un agent d'analyse de données et je peux vous présenter ce que j'ai en termes de capacités, sources de données et modèles de prédiction. **Mes capacités :** Je sais : * `interroger une source en SQL` — comptages, agrégats, pourcent… | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction, attributs_d_un_modele, schema_d_une_source` | 2 | **répondu correctement** |
| `sources-tu-bosses` | tu bosses sur quoi ? | Je suis un agent d'analyse de données qui peut interroger des sources en SQL (comptages, agrégats, jointures), analyser et visualiser des données via Python, faire des prédictions sur des cas ou des groupes entiers, et répondre sur ma prop… | **le modèle**, sur `capacites_de_l_agent, sources_de_donnees, modeles_de_prediction` | 2 | **répondu correctement** |
| `sources-liste-bases` | liste tes bases | J'ai accès à deux sources de données : `titanic` (une base PostgreSQL contenant des informations sur les passagers, leurs classes, leur survie, etc.) et `iris` (un fichier CSV local avec des mesures florales). Souhaites-tu que je te donne … | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `sources-comme-donnees` | qu'est-ce que tu as comme données ? | J'ai accès à deux sources de données : `titanic` (une base de données PostgreSQL contenant des informations sur la survie, le sexe, l'âge, le tarif et la classe) et `iris` (un fichier CSV local avec les mesures des sépales et pétales ainsi… | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `sources-consulter` | quelles infos tu peux consulter ? | J'ai accès à deux sources de données : `titanic` (une base de données PostgreSQL contenant des informations sur la survie, le sexe, l'âge, le tarif et la classe) et `iris` (un fichier CSV local avec les mesures des sépales et pétales ainsi… | **le modèle**, sur `sources_de_donnees` | 2 | **répondu correctement** |
| `capacites-demander-quoi` | je peux te demander quoi ? | Je suis un agent d'analyse de données. Je peux vous aider à : * **Interroger une source en SQL** : effectuer des comptages, des agrégats, calculer des pourcentages, obtenir des listes filtrées ou réaliser des jointures sur les sources disp… | **le modèle**, sur `capacites_de_l_agent` | 2 | **répondu correctement** |
| `capacites-capable` | dis-moi ce dont tu es capable | Je suis un agent d'analyse de données. Je sais : * **interroger une source en SQL** — comptages, agrégats, pourcentages, listes filtrées, jointures ; * **analyser et visualiser** — du code Python exécuté en bac à sable (tests statistiques,… | **le modèle**, sur `capacites_de_l_agent` | 2 | **répondu correctement** |
| `colonnes-familier` | il y a quoi comme colonnes dans passengers ? | Les colonnes de la table `passengers` sont les suivantes : * `passenger_id` (clé primaire, obligatoire) * `name` * `sex` (obligatoire ; valeurs : 'female', 'male') * `age` * `sibsp` * `parch` * `fare` * `embarked` (valeurs : 'C', 'Q', 'S')… | **le modèle**, sur `schema_d_une_source` | 2 | **répondu correctement** |
| `colonnes-champs-classes` | c'est quoi les champs de la table classes ? | Les champs de la table `classes` sont les suivants : * **`class_id`** (INTEGER) : clé primaire et obligatoire. * **`level`** (INTEGER) : obligatoire. * **`label`** (TEXT) : obligatoire, avec des valeurs possibles : '1re classe', '2e classe… | **le modèle**, sur `schema_d_une_source` | 2 | **répondu correctement** |
| `modeles-previsions` | quel genre de prévisions tu peux faire ? | Je peux faire trois types de prévisions différentes : * **`california_housing`** : Il s'agit d'une régression qui permet d'estimer le prix médian des logements d'un îlot californien (recensement 1990), avec une unité en centaines de millie… | **le modèle**, sur `modeles_de_prediction` | 2 | **répondu correctement** |
| `features-familier` | il te faut quoi pour deviner l'espèce d'un iris ? | Je dispose de 3 modèle(s) de prédiction : - **california_housing** (régression, en centaines de milliers de dollars) — cible `MedHouseVal` — Prix médian des logements d'un îlot californien (recensement 1990) — HistGradientBoostingRegressor… | **les faits** (ceinture) | 3 | **répondu correctement** |

| Verdict | Nombre | Part |
|---|---|---|
| répondu correctement | 36 | 100% |
| répondu à côté | 0 | 0% |
| repli « je n'ai pas bien compris » | 0 | 0% |
| erreur | 0 | 0% |
| **Total** | **36** | |

Coût mesuré : **79 appels LLM** pour 36 question(s) (moyenne 2.19 par question).
### Témoins — questions sur les données — 4 question(s)

| Clé | Question | Réponse obtenue | Ce qui a répondu | Appels LLM | Verdict |
|---|---|---|---|---|---|
| `temoin-comptage` | Combien de passagers ont survécu ? | Selon les données, 342 passagers ont survécu. | **le planificateur** | 5 | **répondu correctement** |
| `temoin-maximum` | Quel est l'âge du passager le plus âgé ? | L'âge du passager le plus âgé est de 80 ans. | **le planificateur** | 5 | **répondu correctement** |
| `temoin-colonnes-a-trous` | Quelles colonnes de la table passengers contiennent des valeurs manquantes ? | Les colonnes contenant des valeurs manquantes dans la table `passengers` sont l'âge (`age`) et le port d'embarquement (`embarked`). Il y a 177 enregistrements pour l'âge et 2 enregistrements pour le port d'embarquement qui ne contiennent p… | **le planificateur** | 5 | **répondu correctement** |
| `temoin-prediction` | Prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80 livres, embarquée à So… | Prédiction (titanic) : a survécu (probabilité 93.4%) — détail : n'a pas survécu : 6.6%, a survécu : 93.4% | **le planificateur** | 2 | **répondu correctement** |

| Verdict | Nombre | Part |
|---|---|---|
| répondu correctement | 4 | 100% |
| répondu à côté | 0 | 0% |
| repli « je n'ai pas bien compris » | 0 | 0% |
| erreur | 0 | 0% |
| **Total** | **4** | |

Coût mesuré : **17 appels LLM** pour 4 question(s) (moyenne 4.25 par question).
### Trois rejeux, et ce qu'ils disent de la stabilité

| Rejeu | Méta correctes | Appels (36 méta) | Témoins | Le modèle formule | Les faits sont servis | Laissé au planificateur |
|---|---|---|---|---|---|---|
| 1 | 36 / 36 | 79 | 3 / 4 | 30 | 2 | 4 |
| 2 | 34 / 36 | 81 | 4 / 4 | 30 | 2 | 4 |
| 3 | 36 / 36 | 79 | 4 / 4 | 30 | 2 | 4 |

**Les trois dernières colonnes ne bougent pas d'un rejeu à l'autre.** Les
trente-deux questions que l'agent système prend en charge se comportent
identiquement trois fois de suite : même outil appelé, même partage entre la
formulation du modèle et les faits servis. Tout l'écart — les deux replis du
rejeu 2, le témoin du rejeu 1 — tient aux **quatre questions laissées au
planificateur**, et c'est exactement la non-détermination déjà relevée au §4.
Le routage, lui, s'est stabilisé : c'est un effet qu'on n'attendait pas d'un
déplacement vers le modèle.

Les deux replis du rejeu 2 (`periode-indirecte`, `volumetrie-globale`) sont des
échecs de sortie structurée du planificateur, sur deux questions qui ne désignent
aucune source. Ni l'une ni l'autre n'a rien à voir avec l'agent système : il
s'est abstenu sur les deux, dans les trois rejeux.

### Le bilan

| | Avant (`ae9aeba`) | Après |
|---|---|---|
| Répondu correctement | 24 / 36 (67 %) | **36 / 36 (100 %)** |
| — les 21 questions d'origine | 21 / 21 | 21 / 21 |
| — les 15 reformulations | 3 / 15 | **15 / 15** |
| — les 7 relevées en usage réel | **0 / 7** | **7 / 7** |
| Répondu à côté | 5 | 0 |
| Repli « je n'ai pas bien compris » | 7 | 0 |
| Témoins (questions sur les données) | 3 / 4 | 4 / 4 |
| Appels LLM (36 méta) | 37 — 1,03 par question | 79 — 2,19 par question |
| Appels LLM (4 témoins) | 12 — 3,00 par question | 17 — 4,25 par question |

**Douze verdicts changent, tous dans le bon sens** ; aucun ne va dans l'autre.

### Ce qui répond, et c'est la deuxième moitié du constat

**Trente des trente-six réponses sont la formulation du modèle.** Le
propriétaire écrivait « ce n'est même pas le LLM qui répond » : ça ne se lit plus
dans le tableau, et les tournures le montrent — « J'ai accès à deux sources de
données : `titanic` (une base PostgreSQL contenant des informations sur la
survie, le sexe, l'âge…) et `iris` (un fichier CSV local…) » n'est écrit nulle
part dans le dépôt.

Deux réponses partent en faits, et les deux fois la ceinture a raison :

- `tables-directe` — le modèle a nommé les deux tables sans en donner les
  colonnes, alors que l'outil les avait rendues ;
- `features-familier` — le modèle a appelé deux outils, dont celui du tour
  d'horizon des trois modèles, et n'a parlé que d'iris.

Quatre questions restent au planificateur, et c'est **la frontière du §8, tenue
par le prompt au lieu du lexique** : période et volumétrie exigent un `MIN`/`MAX`
ou un `COUNT`. L'agent système s'en abstient dans les trois rejeux.

### Le coût, précisément

| Question | Avant | Après |
|---|---|---|
| « quelles sources possèdes-tu ? » (tournure connue du lexique) | 0 appel | **2 appels** |
| « tu bosses sur quoi ? » (tournure inconnue) | 2 appels, et un repli | **2 appels**, et une réponse |
| « quelles colonnes dans passengers ? » | 0 appel | **2 appels** |
| « combien de lignes dans passengers ? » | 4 appels | **5 appels** |
| « combien de passagers ont survécu ? » (témoin) | 4 appels | **5 appels** |
| Prédiction avec features complètes (témoin) | 1 appel | **2 appels** |

**Une question sur les données paie exactement un aller-retour de plus** : celui
du nœud système qui décline. Sur `gemma4:e4b`, ce refus prend 1 à 3 secondes,
contre 15 à 30 pour le tour complet.

**Une question sur le système coûte deux appels**, où le lexique en coûtait zéro
*quand il reconnaissait la tournure* et deux à quatre quand il la manquait — pour
ne rien répondre. Le coût moyen d'une question méta passe donc de 1,03 à 2,19,
mais la moyenne d'avant était basse parce qu'un tiers des questions abandonnaient
tôt. **Comparer 79 appels qui répondent à 37 appels dont 13 ne répondaient pas
est le bon cadrage.**


## 12. La source de travail d'une conversation, mesurée de bout en bout

Le catalogue de ce dépôt déclare **deux** sources : c'est le bon terrain pour
éprouver un vrai choix. Le parcours demandé est : l'agent **propose**,
l'utilisateur **valide**, et c'est la source sur laquelle on travaille **ensuite**.

### Un second runner, parce que la batterie ne peut pas le mesurer

[`scripts/mesure_choix_de_source.py`](../scripts/mesure_choix_de_source.py). La
batterie pose chaque question dans une conversation **neuve** — parti pris du §2,
et il tient : une question méta ne doit pas devoir son succès au contexte laissé
par la précédente. Elle ne peut donc rien dire d'un mécanisme qui est, par
nature, multi-tours. Le second runner mène **une** conversation de cinq tours, et
il la mène **par le magasin** (`ConversationStore`) et non de mémoire : c'est lui
qui persiste la source et la relit au tour suivant, et un runner qui court-
circuiterait le disque mesurerait un mécanisme qui n'est pas celui de
l'application.

```bash
uv run python scripts/mesure_choix_de_source.py --markdown /tmp/parcours.md
```

### Premier parcours : deux défauts, que seule cette mesure pouvait montrer

La suite unitaire était verte, et le mécanisme faux. Les deux défauts venaient du
même choix de conception : la reconnaissance d'un choix de source dépendait d'un
**drapeau posé au tour précédent** (« une proposition attend une réponse »).

| Tour | Message | Ce qui s'est passé |
|---|---|---|
| 1 | « bonjour, je voudrais regarder des données » | l'**agent système** a répondu, en énumérant les sources — une bonne réponse, mais aucun drapeau posé |
| 2 | « titanic » | le drapeau n'y étant pas, ce n'était pas un choix : l'agent système a repris la main et **rendu l'inventaire du catalogue**. L'utilisateur valide, on lui repose la question |
| 4 | « et dans iris, combien de lignes ? » | reconnu comme un choix — la source bascule, mais **la question est perdue** : l'utilisateur reçoit un accusé de réception |

**Un choix de source se lit dans le message, pas dans l'histoire.** Le drapeau a
été retiré, `SourceDeTravail` avec lui — la source de travail est redevenue ce
qu'elle aurait toujours dû être, un simple nom persisté avec le fil comme
`owner`. La reconnaissance est maintenant `introspection.choix_de_source` : le
nom d'une source du catalogue, **et le fait que le message ne dise presque rien
d'autre** (au plus trois mots en plus du nom). « titanic » et « va pour titanic,
merci » sont des choix ; « et dans iris, combien de lignes ? » et « combien de
passagers dans titanic ? » sont des questions, dont la source se lie en chemin.

Le seuil est un nombre, et c'est assumé : il est posé sur les formulations
réellement mesurées, pas sur une intuition. **Se tromper ne coûte pas une réponse
fausse** — une source nommée est liée de toute façon par
`_regle_source_de_la_conversation` — le seul écart est un accusé de réception là
où on aurait pu répondre du même coup.

### Second parcours, après correction

| Tour | Attendu | Message | Réponse | Source liée après | Appels LLM |
|---|---|---|---|---|---|
| 1 | la proposition | bonjour, je voudrais regarder des données | « Bonjour ! Je suis un agent d'analyse de données… Je dispose actuellement de deux sources de données : `titanic` (une base de…), `iris`… » | *(aucune)* | 2 |
| 2 | la validation | **titanic** | « Entendu : on travaille sur **titanic** (postgres) — Base Titanic multi-tables… Je garde cette source pour la suite de la conversation. » | `titanic` | **0** |
| 3 | la source est portée | combien de lignes en tout ? | « Il y a un total de 891 lignes dans la table des passagers. » | `titanic` | 5 |
| 4 | la bascule, annoncée | et dans **iris**, combien de lignes ? | « **Je passe sur la source `iris` — on travaillait sur `titanic`.** La table `iris` contient un total de 150 lignes. » | `iris` | 5 |
| 5 | la nouvelle source tient | et combien de colonnes ? | « La table disponible, nommée `iris`, contient 5 colonnes : `sepal_length`, `sepal_width`, `petal_length`, `petal_width` et `species`. » | `iris` | 4 |

Coût mesuré : **16 appels LLM pour 5 tours**.

Ce que ce tableau montre, ligne à ligne :

- **le tour 2 coûte zéro appel** — reconnaître le nom d'une source du catalogue
  dans un message est du code, et le faire trancher par un modèle serait payer un
  aller-retour pour comparer deux chaînes de caractères ;
- **le tour 3 ne nomme aucune source et reçoit la bonne réponse** (891 lignes,
  le compte de `titanic.passengers`) : la source est portée par la conversation,
  et le planificateur la reçoit dans son contexte au lieu de la deviner ;
- **le tour 4 bascule ET répond** — la bascule est en première ligne, le compte
  d'`iris` (150) suit ;
- **le tour 5 confirme que la bascule a tenu**, sans que la source soit nommée.

### Ce qui a été tranché : bascule, pas refus ni question

Le propriétaire ne demandait pas de pouvoir changer de source en cours de
conversation. **Le choix est de basculer, et de le dire.** Refuser aurait obligé
à ouvrir un fil pour une question d'une ligne ; demander confirmation aurait
dépensé un tour pour une intention déjà écrite noir sur blanc. Ce qui est
dangereux n'est pas de changer de source, c'est de **répondre sur d'autres
données sans le dire** — d'où l'avis en tête de la réponse, et non dans la trace,
qui n'est pas dépliée par défaut.

Trois garde-fous encadrent la bascule :

- **elle ne suit que le TEXTE de l'utilisateur** (`introspection.source_nommee`),
  jamais `plan.source`. Le planificateur choisit une source à chaque tour, souvent
  au hasard des descriptions du catalogue ; sa supposition ne doit pas faire
  basculer le travail de quelqu'un ;
- **une source imposée par l'appelant** (`ask(source=…)`, champ `source` de
  `POST /chat`) ne lie rien : c'est un paramètre d'API pour un tour ;
- **un tableau intermédiaire du fil ne lie rien** : il est interrogeable, ce n'est
  pas une source de données.

### La proposition, et pourquoi elle arrive après le plan

Elle est portée par `_regle_choisir_la_source`, donc **après** l'appel au
planificateur. C'est le seul moment où l'on sait qu'une source est réellement
nécessaire : « prédis pour une passagère de 1re classe, 28 ans… » n'en demande
aucune, et lui proposer un catalogue serait un tour perdu. Le coût est celui du
plan, qu'on aurait payé de toute façon.

S'il n'y a **qu'une** source, elle est **annoncée** au lieu d'être demandée
(« Je travaille sur la source `iris`. ») et la question est répondue dans le même
tour. Elle était déjà choisie en silence par `_resolve_source` ; ce qui change,
c'est que l'utilisateur l'apprend.

### La compatibilité, sans migration

Une transcription écrite **avant** ce champ le reçoit à sa valeur par défaut —
vide, donc aucune source liée — et le fil continue de fonctionner comme avant, le
planificateur choisissant à chaque tour. C'est la différence avec `owner`, dont
la migration avait dû être écrite : ici le défaut **est** le comportement d'avant.
Un test le vérifie sur un `transcript.json` écrit à la main, sans le champ.


## 13. Ce qui reste ouvert

- **Le coût d'un aller-retour par question sur les données** est le prix du
  routage par le modèle, et il n'est pas récupérable sans revenir à un filtre
  écrit à la main — c'est-à-dire à ce qu'on vient de retirer. La piste, s'il
  fallait la rouvrir : un modèle plus petit et plus rapide pour ce seul nœud,
  puisqu'il ne fait que choisir un outil et recopier ce qu'il rend.
- **La frontière EST / CONTIENT tient désormais par un prompt**, plus par du
  code. Elle a tenu sur les quatre témoins et sur les quatre questions de période
  et de volumétrie, dans trois rejeux ; c'est une mesure, pas une garantie. Les
  témoins sont là pour la surveiller, et c'est leur seul travail.
- **La ceinture est réglée sur ce catalogue.** « Ce qu'une puce nomme doit
  revenir » est vérifié sur des listes de deux à dix éléments. Une source à
  quarante tables demanderait de rejouer la mesure : soit le modèle rendra la
  liste entière, soit la ceinture servira les faits à chaque fois — et dans ce
  second cas ce serait le bon comportement, mais il faudrait le savoir.
- **Le seuil de trois mots** qui distingue un choix de source d'une question est
  posé sur les formulations mesurées. « On part sur titanic s'il te plaît » en
  compte six : il sera traité comme une question, la source sera liée quand même,
  et l'utilisateur recevra une réponse au lieu d'un accusé de réception. C'est le
  bon sens du biais, mais c'en est un.
- **Une source à quarante tables** reste hors de portée de cette batterie, comme
  au §4 : le tour d'horizon est là pour ça, le volume de la réponse à « quelles
  colonnes a la source X ? » n'a été éprouvé que sur deux à dix colonnes.
