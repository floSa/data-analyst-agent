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
| 12 | la **source de travail** d'une conversation : proposée, validée, portée par le fil | **oui**, mais le §12 croyait le choix acquis — cf. §14 |
| 13 | ce qui restait ouvert **au moment du §12** | daté : le §14 en reprend deux points |
| **14** | l'**ordre du catalogue** décidait de la réponse, mesuré dans les deux sens ; ce qui a été corrigé, et l'inventaire qui se lit au lieu de se réciter | **oui** |
| **15** | la même batterie sur **deux moteurs** — le trou de la ceinture que vLLM a découvert, et la mesure à 36/36 des deux côtés | **oui** |
| **16** | le typage des arguments d'outil : le **seul** champ que le schéma ne déclare pas, et la prédiction que seul Ollama rendait | **oui** |

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
- **Une source à quarante tables** n'est pas couverte par cette batterie : le tour
  d'horizon (tables sans colonnes) est déjà là pour ça, mais le volume de la réponse
  à « quelles colonnes a la source X ? » n'a été éprouvé que sur deux et dix
  colonnes.

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


## 13. Ce qui restait ouvert au moment du §12

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

## 14. L'ordre du catalogue décidait de la réponse

Le §12 croyait le choix de source acquis : l'agent **propose**, l'utilisateur
**valide**, la conversation **porte** la source. Les trois tours étaient mesurés
et ils tenaient. Ce qui n'avait pas été mesuré, c'est **le cas où l'agent ne
propose pas** — et il ne proposait pas dans le cas le plus courant.

### Le défaut : deux sources, la même question, deux comportements

`_regle_choisir_la_source` ne se déclenchait que si `plan.source` était vide. Or
le planificateur le remplit presque toujours, au hasard des descriptions du
catalogue. **Sa supposition suffisait donc à choisir sur quelles données
répondre.**

Le montrer demandait un terrain où l'on puisse tout tenir constant sauf une
chose. C'est [`tests/catalogues/ambiguite/`](../tests/catalogues/ambiguite/) :
deux sources de fichiers qui partagent une colonne `sex` — `titanic.csv`
(35,24 % de femmes) et `employes.csv` (51,00 %) — déclarées dans les **deux
ordres**. Les données sont identiques d'un catalogue à l'autre, octet pour
octet ; seul l'ordre change.

```bash
uv run python scripts/mesure_ambiguite_de_source.py --essais 5
```

Le runner mène chaque essai dans une conversation **neuve**, par le magasin
comme l'API, et lit le verdict dans le **chiffre** de la réponse — jamais dans
le nom qu'elle cite. Un modèle qui écrit « d'après titanic » sans avoir rien
interrogé ne prouve rien ; un 35,24 ne peut venir que d'un comptage sur 891
lignes.

#### Avant, sur `e83e634` — 5 essais par ordre

| Ordre déclaré | A répondu sur `titanic` | A répondu sur `employes` | A proposé et attendu |
|---|---|---|---|
| `titanic` en premier | **5/5** (35,24 %) | 0/5 | 0/5 |
| `employes` en premier | 0/5 | 0/5 | **5/5** |

Même question, mêmes octets, deux comportements complets. Et **aucun des deux
n'est un choix** : le second tombe du bon côté par accident — le planificateur
avait laissé `plan.source` vide, la règle a donc pu proposer. Il ne fallait
surtout pas le « corriger ».

#### Après — 5 essais par ordre

| Ordre déclaré | A répondu sur `titanic` | A répondu sur `employes` | A proposé et attendu |
|---|---|---|---|
| `titanic` en premier | 0/5 | 0/5 | **5/5** |
| `employes` en premier | 0/5 | 0/5 | **5/5** |

**10/10, identiques dans les deux ordres.** Rien n'est lié au fil, aucune
requête n'est lancée, et le tour coûte 2 appels LLM (le nœud système, puis le
planificateur) — le prix du plan, qu'on aurait payé de toute façon.

### Ce qui a été corrigé : effacer, pas contourner

La règle qui propose **n'a pas changé d'une ligne**. Ce qui a changé est le plan
qu'elle lit.

`_regle_source_de_la_conversation` **efface** désormais `plan.source` quand
personne ne l'a validée — ni l'utilisateur en nommant une source dans son
message, ni le fil en en portant une. « Aucune source retenue » veut alors dire
ce que ça devait dire depuis le début.

L'effacer à la source plutôt que de la contourner en aval n'est pas un détail de
style : c'est ce qui garantit qu'**aucune** règle suivante, aucun nœud et aucune
liaison ne peut reprendre la supposition pour un choix — il n'y a plus rien à
reprendre. La première version de ce correctif filtrait dans
`_regle_choisir_la_source` seulement, et la mesure l'a prise en défaut : l'agent
proposait bien l'inventaire, mais `_lier_la_source` avait déjà lié au fil la
source devinée. L'utilisateur recevait une question, et la réponse à cette
question était déjà tranchée.

Quatre cas ne bougent pas, et chacun a son test :

- **une seule source déclarée** → repli automatique, annoncé (« Je travaille sur
  la source `iris`. ») et la question répondue dans le même tour. C'est le
  comportement d'avant, intact ;
- **une source nommée dans le message** → c'est une désignation : on répond, on
  lie, et une bascule est annoncée en première ligne (§12) ;
- **un tableau intermédiaire du fil** (`plan.source` hors catalogue déclaré) →
  ce n'est pas un choix entre sources ambiguës, c'est un résultat que la
  conversation vient de produire ;
- **hors conversation** (`ask()` sans `conversation_id` : la batterie du §11, un
  script) → il n'y a pas de fil pour porter la réponse à une proposition, la
  poser serait une impasse. Le comportement d'avant est gardé tel quel, et c'est
  pourquoi les 36 questions méta et les 4 témoins ne bougent pas.

### L'inventaire cesse d'être le YAML recopié

Un inventaire qui ne rend que ce que le YAML déclare ne permet pas de choisir :
deux descriptions écrites à la main se ressemblent toujours. Chaque source porte
désormais trois faits **lus dans la source elle-même**
([`agents/retrieval/faits.py`](../src/data_analyst_agent/agents/retrieval/faits.py)) :
le nombre de tables, le nombre de lignes, et la période couverte s'il existe une
colonne de date.

```
- **titanic** (file) — Passagers du Titanic (fichier CSV) — survie, sexe, âge…
  1 table(s), 891 ligne(s) (titanic : 891)
- **employes** (file) — Effectif d'une entreprise (fichier CSV) — sexe, âge…
  1 table(s), 300 ligne(s) (employes : 300)
```

**Jamais devinés.** C'est le défaut d'`acfd8f5` — « décris le dataset iris »
répondu de mémoire, avec une jolie prose et zéro requête — et il reviendrait par
cette porte si la volumétrie était déduite d'un nom de fichier. Une source qui ne
répond pas rend donc **la raison de son silence et aucun chiffre** :
« volumétrie non relevée : la source n'a pas répondu ». Et elle reste dans la
liste : une source absente de l'inventaire ressemble à une source non déclarée,
ce qui est un tout autre problème à aller corriger.

Le relevé coûte une ouverture de connexion et quelques agrégats par source. Il
est donc fait **une fois par session** et gardé, **paresseusement** — ouvrir
toutes les sources au démarrage ferait payer le prix à qui ne pose aucune
question d'inventaire, et ferait dépendre le démarrage du serveur de la
disponibilité de chaque base. Le cache est **dit** à l'utilisateur : « Tables,
lignes et périodes sont lues dans les sources elles-mêmes, au premier inventaire
de la session. » Un chiffre qui date n'est pas un chiffre faux, mais il ne doit
pas passer pour frais.

Une mise en forme qui n'est pas cosmétique : les faits viennent **sous** la
puce, sans puce et sans accent grave. La ceinture du §10 lit le premier nom
décoré de chaque puce comme le sujet de la ligne (`_enumeres`) ; une sous-puce
nommant `passengers` exigerait de toute réponse du modèle qu'elle recopie chaque
nom de table pour être servie. Les faits sont du contexte sur la source, pas de
nouveaux sujets.

### La source de travail, enfin visible — et changeable sans la taper

Elle existait, elle se portait de tour en tour, et **elle ne se voyait nulle
part** : pour savoir sur quelles données on travaillait, il fallait relire la
dernière annonce dans le fil. Un bandeau permanent, au-dessus de la
conversation, dit laquelle est retenue et ce qu'on a lu dedans ; un menu permet
d'en changer.

**La source vient toujours du fil.** Le menu écrit dans la conversation
(`PUT /conversations/<id>/source`), `POST /chat` continue de la relire de là, et
aucun corps de question ne la transporte — `ChatRequest` n'a toujours pas de
champ pour ça, et un test le vérifie. Trois propriétés encadrent ce menu :

- **seule une source DÉCLARÉE se lie** : un tableau intermédiaire est
  interrogeable, ce n'est pas une source de données ;
- **le changement est inscrit dans la transcription** comme un message de
  l'agent. Relire un fil dont les réponses changent de données sans que rien ne
  le dise est exactement ce que la bascule annoncée évite ;
- **on peut délier** (« aucune ») : l'agent reproposera son inventaire à la
  prochaine question qui en demande une.

Choisir avant d'avoir écrit quoi que ce soit demande qu'un fil existe — d'où
l'ouverture d'un fil vide (`POST /conversations`), titré par son premier message
comme les autres.

### Le parcours complet, sur un catalogue réaliste

Deux CSV suffisent à montrer un défaut d'ordre ; ils ne ressemblent pas à un
déploiement. [`tests/catalogues/realiste/`](../tests/catalogues/realiste/)
déclare **quatre sources de trois natures** — deux bases Postgres multi-tables,
un CSV, un classeur Excel à deux feuilles — avec des **colonnes qui se
recoupent** : `sexe`/`sex`, `departement` et une colonne de date vivent chacun
dans trois sources sur quatre, jamais sous le même nom. C'est ce recoupement qui
rend une question ordinaire réellement ambiguë, sans avoir à la fabriquer.

| Source | Type | Tables | Lignes | Période couverte |
|---|---|---|---|---|
| `ventes` | postgres | 2 | 620 (clients : 120, commandes : 500) | 2023-01-01 → 2024-12-30 |
| `rh` | postgres | 2 | 184 (salaries : 180, services : 4) | 2015-01-11 → 2024-12-17 |
| `employes` | file (CSV) | 1 | 300 | *(aucune colonne de date)* |
| `absences` | file (Excel) | 2 | 244 (absences : 240, postes : 4) | 2024-01-01 → 2024-12-28 |

```bash
DAA_CATALOG_PATH=tests/catalogues/realiste/catalogue.yaml \
uv run python scripts/mesure_choix_de_source.py
```

Le parcours du §12 a gagné un tour — le **tour du verrou** —, et c'est le tour
qui manquait : le tour 3 prouve que la source liée est reprise, le tour 4 qu'elle
ne se fait pas déloger par une question qui ressemble à une autre source.

| Tour | Attendu | Message | Réponse | Source liée | Appels LLM |
|---|---|---|---|---|---|
| 1 | la proposition | bonjour, je voudrais regarder des données | « … Je dispose actuellement de quatre sources de données : `ventes`, `rh`, `employes` et `absences`… » | *(aucune)* | 2 |
| 2 | la validation | **ventes** | « Entendu : on travaille sur **ventes** (postgres) — … **2 table(s), 620 ligne(s) (clients : 120, commandes : 500) — période…** » | `ventes` | **0** |
| 3 | la source est portée | combien de lignes en tout ? | « Il y a un total de **120 clients** enregistrés et **500 commandes** passées dans la base de données. » | `ventes` | 6 |
| 4 | **le verrou tient** | et combien de femmes ? | « Il y a un total de **60 femmes** dans la base de données des clients. » | `ventes` | 5 |
| 5 | la bascule, annoncée | et dans **rh**, combien de lignes ? | « **Je passe sur la source `rh` — on travaillait sur `ventes`.** Il y a 1 ligne dans le département RH. » | `rh` | 5 |
| 6 | la nouvelle source tient | et combien de colonnes ? | « … deux tables : `salaries` qui contient 7 colonnes et `services` qui en contient 2. » | `rh` | 4 |

Coût mesuré : **22 appels LLM pour 6 tours** (3,67 par tour).

Ce que le tableau montre :

- **le tour 2 coûte toujours zéro appel**, et il porte maintenant ce qu'on a lu
  dans la source : 620 lignes et une période, pas seulement une description ;
- **le tour 4 est le nouveau**, et c'est le plus important. « Combien de
  femmes ? » ne nomme personne et **trois des quatre sources pourraient y
  répondre**. La réponse vient de `ventes` — 60 femmes sur 120 clients, l'oracle
  — et la source liée ne bouge pas. Le verrou tient là où deviner serait le plus
  tentant ;
- **le tour 5 bascule parce que l'utilisateur a écrit `rh`**, et le dit en
  première ligne.

Une réserve, et elle est du modèle et non du mécanisme : au tour 5, « il y a 1
ligne dans le département RH » répond à côté — `rh` est à la fois le nom de la
source et celui d'un département de `services`, et le modèle a compté les lignes
du second. La bascule, elle, est juste, et le tour 6 le confirme en lisant le
bon schéma. Ce qui est mesuré ici est le choix de source ; la qualité du SQL sur
une formulation ambiguë est le sujet d'une autre batterie.

### Les 36 questions méta et les 4 témoins ne bougent pas

C'est la vérification qui compte le plus pour un changement de cette nature : le
choix de source touche au **plan**, et les questions sur le système passent en
amont de lui. Rien ne devait bouger — encore fallait-il le mesurer.

```bash
uv run python scripts/mesure_surface_conversationnelle.py
```

| Passage | Méta correctes | Appels (36 méta) | Témoins corrects |
|---|---|---|---|
| Référence §11, rejeu 1 | 36 / 36 | 79 | 3 / 4 |
| Référence §11, rejeu 2 | 34 / 36 | 81 | 4 / 4 |
| Référence §11, rejeu 3 | 36 / 36 | 79 | 4 / 4 |
| **Après, passage 1** | **35 / 36** | 81 | 2 / 4 |
| **Après, passage 2** | **35 / 36** | 81 | 3 / 4 |

Les deux passages tombent dans l'enveloppe des trois rejeux de référence (34 à
36 méta, 3 à 4 témoins), et le coût par question ne bouge pas. Trois écarts
méritent d'être nommés plutôt que noyés dans une moyenne :

- **`temoin-colonnes-a-trous`, en erreur au passage 1 et correct au passage 2.**
  L'agent SQL a bouclé 275 secondes puis échoué, sur une machine qui faisait
  tourner le parcours réaliste en même temps. Non reproduit seul ;
- **`sources-perimetre` (« c'est quoi ton périmètre ? ») compté à côté aux deux
  passages.** Le modèle appelle l'outil des capacités et sa formulation omet les
  noms des sources, que l'oracle exige. La ceinture ne l'attrape pas : elle
  n'exige que ce que les **puces** des faits nomment, et « Mes sources :
  `titanic`, `iris` » n'en est pas une ;
- **`temoin-prediction` compté à côté aux deux passages**, la prédiction
  n'aboutissant pas.

Les deux derniers **se reproduisent à l'identique sur le code d'avant** : la
vérification a été faite en remettant la docstring d'origine de l'outil
`sources_de_donnees` — la seule chose que ce chantier change dans le prompt de
l'agent système — et en rejouant ces deux questions seules. Verdicts inchangés
des deux côtés. Ce sont donc des variations du modèle sur le chemin
système/planificateur, pas une régression de ce chantier. Le mode d'échec de
`temoin-prediction`, lui, diffère selon la version (l'agent système s'en empare
d'un côté, la prédiction n'aboutit pas de l'autre) : c'est un écart de routage
qui ne change pas le verdict, et qui mériterait sa propre mesure.

### Ce qui reste ouvert

Les points du §13 restent vrais, moins celui du choix de source qui vient d'être
tranché. S'y ajoutent :

- **le relevé date du premier inventaire de la session.** Une base qui grossit
  pendant que le serveur tourne affichera son volume d'hier. C'est le compromis
  du cache, il est dit à l'utilisateur, et la sortie serait de le rafraîchir sur
  demande explicite plutôt qu'à chaque tour ;
- **la période est celle de la PREMIÈRE colonne de date rencontrée.** Une source
  qui en porte plusieurs (`created_at`, `closed_at`) n'en montre qu'une — nommée
  dans la réponse, donc sans ambiguïté sur ce qui a été lu, mais sans choix ;
- **le relevé n'est pas borné en temps.** Un `count(*)` sur une table de
  plusieurs centaines de millions de lignes ferait attendre le premier
  inventaire. Rien ne l'a montré ici : la plus grosse source mesurée fait 620
  lignes ;
- **`sources-perimetre` et `temoin-prediction`** sont deux écarts constatés sur
  le chemin système, antérieurs à ce chantier et non expliqués. Ils appellent
  leur propre mesure.

## 15. Deux moteurs, la même batterie — et le trou que vLLM a découvert

La bascule du moteur vers vLLM ([VLLM.md §7](VLLM.md#7-le-modèle-du-vllm-partagé--gemma-4-e4b-it-qat-w4a16-ct))
a fait tomber quatre questions qui tenaient sous Ollama. Même code, même
modèle, deux serveurs. Le relevé du propriétaire, conversation neuve à chaque
fois :

| Question | vLLM | Ollama |
|---|---|---|
| « Sur quoi peux-tu travailler ? » | **raté**, 1 s | correct, 15 s |
| « sur quoi je peux travailler ? » | **raté**, 1 s | correct, 16 s |
| « montre-moi ce que tu as » | **raté**, 1 s | correct, 11 s |
| « je peux te demander quoi ? » | **raté** | **raté** |

Ce §15 dit ce que le fil brut en a montré, et pourquoi la cause n'était ni
celle qu'on supposait ni, à proprement parler, une affaire de moteur.

### 15.1 Le fil brut : l'outil est appelé, et sa réponse est jetée

L'hypothèse de départ était que l'agent système n'appelait pas ses outils —
une réponse en une seconde qui paraphrase le prompt système ressemble beaucoup
à cela, et le [§7.4 de VLLM.md](VLLM.md#74-le-mauvais-analyseur--pas-un-400-un-faux-négatif-silencieux)
avait justement montré qu'un analyseur inadapté laisse l'appel d'outil *fuir
dans le texte*, sans le moindre code d'erreur. Deux causes opposées, deux
remèdes opposés : il fallait regarder le fil, pas le déduire.

Le fil a été capturé en branchant un `event_hook` httpx sur le client OpenAI du
nœud système, sous vLLM, pour « Sur quoi peux-tu travailler ? ». Il tient en
deux allers-retours.

**Requête 1** — cinq outils offerts, `tool_choice` laissé au modèle :

```
tool_choice   : 'auto'
tools envoyés : ['capacites_de_l_agent', 'sources_de_donnees', 'schema_d_une_source',
                 'modeles_de_prediction', 'attributs_d_un_modele']
```

**Réponse 1** — le serveur rend un appel d'outil, structuré et extrait :

```json
{"message": {"content": null,
             "tool_calls": [{"function": {"name": "capacites_de_l_agent", "arguments": "{}"}}]},
 "finish_reason": "tool_calls", "usage": {"completion_tokens": 14}}
```

**Réponse 2**, après que l'outil a rendu ses faits :

```
Je peux interroger des sources en SQL, analyser et visualiser des données avec du
code Python, prédire des cas ou des individus en utilisant des modèles, et répondre
sur moi-même concernant mes sources, leurs structures, mes modèles et leurs
exigences.
```

**Le modèle appelle bel et bien son outil, et le serveur rend l'appel.** Ni
analyseur en défaut, ni tool calling muet : `finish_reason` vaut `tool_calls`,
`outils_appeles` vaut `('capacites_de_l_agent',)`, et le nœud système déclare
`concerne_le_systeme = True`. Ce qui manque est ailleurs — dans ce que l'outil
a rendu et que la réponse a laissé tomber :

```
Je suis un agent d'analyse de données. Je sais :

- **interroger une source en SQL** — … ;
- …

Mes sources : `titanic`, `iris`. Mes modèles de prédiction : `california_housing`, `iris`, `titanic`.
```

La dernière ligne porte exactement ce que la question demandait. Le modèle a
résumé les puces et l'a jetée.

### 15.2 Pourquoi la ceinture ne l'a pas retenu

C'est le cœur de l'affaire, et le §14 l'avait **écrit un mois plus tôt sans le
corriger** :

> `sources-perimetre` … Le modèle appelle l'outil des capacités et sa
> formulation omet les noms des sources, que l'oracle exige. La ceinture ne
> l'attrape pas : elle n'exige que ce que les **puces** des faits nomment, et
> « Mes sources : `titanic`, `iris` » n'en est pas une.

`defaut_de_fondation` sert les faits tels quels dès qu'une réponse omet un nom
rendu par un outil. Mais `_enumeres`, qui dit *quels* noms, ne lisait que les
lignes commençant par une puce. L'inventaire de `decrire_les_capacites` tient
sur une ligne ordinaire : il n'exigeait rien, et la ceinture laissait passer.

**Ce que vLLM a changé n'est donc pas le mécanisme, c'est la verbosité.** Sous
Ollama, le même modèle recopie les faits presque au long — d'où les 11 à 16
secondes — et les noms survivent **par accident**. Sous vLLM il condense en une
phrase, en une seconde, et l'accident ne se produit plus. Le défaut était déjà
là ; un moteur plus concis l'a fait passer d'une question sur trente-six à
quatre.

C'est la leçon du §15 : **une ceinture qui ne se déclenche jamais ne prouve
rien.** Celle-ci tenait parce que le modèle était bavard, pas parce qu'elle
couvrait le cas.

### 15.3 Ce qui a été corrigé

**Une énumération tenue sur une ligne est exigible comme une puce**
(`_enumeres`). Un deux-points suivi de noms décorés, jusqu'au point qui clôt la
phrase, rend **tous** ses membres : une énumération n'a pas de sujet, elle n'a
que des membres. Deux bornes la tiennent loin des rejets déjà mesurés à tort au
§11 — un deux-points en **fin de ligne** introduit ce qui suit et n'énumère
rien (« La table `passengers` de la source `titanic` : »), et les lignes de
**citation** sont laissées de côté : elles recopient le dictionnaire de la
source, le seul texte de faits que le module n'écrit pas.

**Le prompt exige les noms sans qu'on ait demandé une liste.** « Quand on te
demande une liste, rends-la ENTIÈRE » ne s'appliquait qu'à une demande
explicite, et « Sur quoi peux-tu travailler ? » n'en est pas une. La ceinture
rattrape désormais l'omission, mais la rattraper sert le gabarit : le modèle
est plus utile quand il n'a pas à être repris.

**Et une prédiction retourne au planificateur.** Ajouté à la liste de ce qui
n'est pas pour l'agent système : « Prédis la survie d'une passagère de 1re
classe de 28 ans, tarif 80 livres… » donne des **valeurs d'attributs**, et
l'agent système y répondait par son registre de modèles.

#### Une consigne écrite, mesurée, et retirée

Une quatrième idée visait « je peux te demander quoi ? » : « nomme les ACTIONS
que tu sais faire avant les ressources sur lesquelles elles portent ». Elle
faisait effectivement citer les actions — et elle poussait le modèle à
**ratisser** : « de quoi disposes-tu ? » partait lire le schéma de chaque
source, quatre outils au lieu d'un, et dépassait `systeme_request_limit = 4`.
Le nœud système tombait alors en *fail-open* vers le planificateur, qui
renvoyait une clarification.

La bisection, sur `sources-disposes`, deux essais par variante sous vLLM :

| Variante du prompt | Verdict | Appels LLM | Trace du nœud système |
|---|---|---|---|
| A — prompt d'origine | correct | 2 | `capacites_de_l_agent` — faits servis tels quels |
| B — A + « cite-les tous » | correct | 2 | `capacites_de_l_agent` — formulé par le modèle |
| **C — B + « nomme les actions »** | **à côté** | **5** | **agent système écarté — passe au planificateur** |
| D — C + l'exemple de prédiction | à côté | 5 | agent système écarté — passe au planificateur |

Le défaut apparaît avec ce paragraphe et avec lui seul. Il a été retiré : la
ceinture suffit à traiter « je peux te demander quoi ? », puisque les faits
omis sont servis et qu'ils portent les actions. **Une consigne de prompt qui
corrige une question peut en casser une autre par un chemin qui n'a rien à voir
— ici le plafond d'allers-retours.** La ligne A du tableau dit aussi ceci : la
correction de la ceinture, à elle seule, rendait déjà la bonne réponse.

### 15.4 Les quatre questions, rejouées sur les deux moteurs

Conversation neuve à chaque fois, `--only` sur les quatre clés :

| Question | vLLM avant | vLLM après | Ollama avant | Ollama après |
|---|---|---|---|---|
| `sources-reformulee` — « Sur quoi peux-tu travailler ? » | **à côté**, 1,4 s | correct, 1,6 s | correct, 18,8 s | correct, 5,8–18,5 s |
| `sources-premiere-personne` — « sur quoi je peux travailler ? » | **à côté**, 1,3 s | correct, 1,6 s | correct, 13,3 s | correct, 15,3–21,3 s |
| `sources-montre-moi` — « montre-moi ce que tu as » | **à côté**, 1,2 s | correct, 3,3 s | correct, 11,5 s | correct, 12,5–18,4 s |
| `capacites-demander-quoi` — « je peux te demander quoi ? » | **à côté**, 0,9 s | correct, 2,1–2,4 s | correct, 7,0 s | correct, 7,2–11,0 s |

Deux remarques d'honnêteté sur ce tableau.

**Les trois premières manquaient `titanic` et `iris`** — l'oracle le dit mot
pour mot (`manque : titanic, iris`). La quatrième manquait `analys` : le modèle
avait gardé l'inventaire et jeté les actions. **C'est un défaut distinct**, il
est antérieur à la bascule, et c'est celui que le propriétaire signalait comme
raté sur les deux moteurs.

**Il n'est pas déterministe sous Ollama.** Le passage de référence rejoué ici
l'a compté correct ; le relevé du propriétaire l'avait compté raté. Sous vLLM,
en revanche, il ratait **à chaque fois** — c'est là qu'il a pu être observé, et
c'est le second service que la bascule aura rendu. Les deux défauts sont
traités par la même mécanique (les faits omis sont servis), mais par deux
chemins : pour les trois premières, l'omission portait sur des **noms** ; pour
la quatrième, sur des **actions**, que la ceinture ne garde pas — elle ne
connaît que des noms. Ce sont les noms omis de la même réponse qui la font
partir.

### 15.5 La batterie complète, sur les deux moteurs

```bash
# Ollama (le moteur du .env, inchangé)
uv run python scripts/mesure_surface_conversationnelle.py

# vLLM, par variables d'environnement — le .env n'est pas touché
DAA_LLM_BASE_URL=http://localhost:8100/v1 \
DAA_LLM_MODEL=google/gemma-4-E4B-it-qat-w4a16-ct \
uv run python scripts/mesure_surface_conversationnelle.py
```

| Moteur | Passage | Méta | Témoins | Appels LLM (36 méta) | Durée des 36 méta | Moyenne |
|---|---|---|---|---|---|---|
| vLLM | avant | 32 / 36 | 2 / 4 | 82 | 70 s | 1,9 s |
| vLLM | **après, passage 1** | **36 / 36** | 2 / 4 | 78 | 118 s | 3,3 s |
| vLLM | **après, passage 2** | **36 / 36** | 2 / 4 | 78 | 122 s | 3,4 s |
| Ollama | avant | 34 / 36 | 2 / 4 | 83 | 305 s | 8,5 s |
| Ollama | **après, passage 1** | **36 / 36** | **3 / 4** | 78 | 568 s | 15,8 s |
| Ollama | **après, passage 2** | **36 / 36** | **3 / 4** | 78 | 334 s | 9,3 s |

**36 / 36 sur les quatre passages, et sur les deux moteurs.** Aucun passage de
référence antérieur n'avait fait mieux que 36 (§11, §14), et les deux passages
du §14 plafonnaient à 35.

Le **coût en appels LLM baisse** — 78 contre 82 et 83 — parce qu'une réponse
servie par la ceinture ne coûte pas d'aller-retour supplémentaire. Le temps de
paroi, lui, monte : les réponses sont **plus longues d'environ 20 %** (354 →
428 caractères en moyenne sous vLLM, 634 → 715 sous Ollama), puisqu'elles
portent désormais les noms qu'elles laissaient tomber. Le reste de l'écart —
334 s contre 568 s pour deux passages Ollama identiques — est la variance de la
machine, qui servait les deux moteurs sur la même carte pendant toute la
mesure : ce tableau ne compare pas des vitesses de serveurs.

Les questions méta sous Ollama restent servies à **35 sur 36 par l'agent
système** (34 avant), et sous vLLM à 34 sur 36 : la correction n'a déplacé
aucune question vers le planificateur.

### 15.6 Les témoins, et les deux questions de données qui restent

Les témoins ne mesurent pas l'agent système : ils vérifient qu'**il ne prend
pas** ce qui ne lui appartient pas.

| Témoin | vLLM avant | vLLM après | Ollama avant | Ollama après |
|---|---|---|---|---|
| `temoin-comptage` | correct | correct | correct | correct |
| `temoin-maximum` | correct | correct | correct | correct |
| `temoin-colonnes-a-trous` | à côté | à côté | à côté | **à côté / erreur** |
| `temoin-prediction` | à côté | à côté | **à côté** | **correct** |

Sur les quatre passages d'après, **le nœud système laisse passer les quatre
témoins** : « aucun outil appelé — passe au planificateur ». C'était déjà vrai
sous vLLM ; sous Ollama, `temoin-prediction` était **pris** par l'agent système,
qui répondait par son registre de modèles au lieu de prédire. L'exemple ajouté
au prompt le rend au planificateur — 3 essais sur 3 avant la batterie, puis les
deux passages complets.

Restent deux échecs, tous deux **en aval du nœud système**, et ce que leur
trace en dit :

- **`temoin-colonnes-a-trous`, sur les deux moteurs.** « Quelles colonnes de la
  table `passengers` contiennent des valeurs manquantes ? » Sous Ollama, l'agent
  SQL boucle et épuise son `retrieval_request_limit = 10` (`incident e65aaf64`,
  112 s) ou finit par renoncer (97 s) ; rejoué **seul**, il écrit pourtant la
  bonne requête — un `CASE WHEN COUNT(col) < COUNT(*)` par colonne — et rend
  `grounded = True`. Sous vLLM il écrit `SELECT * FROM passengers WHERE age IS
  NULL OR …` et rend 179 lignes : la réponse est le tableau, pas la liste des
  colonnes que l'oracle exige. C'est un défaut de l'agent SQL, il est antérieur
  à ce chantier — le [§14](#14-lordre-du-catalogue-décidait-de-la-réponse) l'a
  déjà constaté et non reproduit — et il n'a pas été traité ici ;
- **`temoin-prediction`, sous vLLM seulement.** Le nœud système le laisse
  passer, le planificateur le classe `predict` — et la validation refuse :
  `pclass (Classe du billet) : Input should be 1, 2 or 3 (reçu : '1')`. Le
  modèle servi par vLLM rend ses arguments d'outil en **chaînes**, là où Ollama
  rend des entiers, et `Literal[1, 2, 3]` refuse `'1'`. C'est un écart de
  typage sur le chemin d'inférence, pas sur le chemin système ; le corriger
  touche `SCHEMAS` et la validation des features, et appelle sa propre mesure.

**Les témoins sous Ollama sont donc à 3 / 4 et non 4 / 4.** Le quatrième est
`temoin-colonnes-a-trous`, qui ratait déjà avant ce chantier : la correction
n'a rien déplacé vers le planificateur, mais elle n'a pas non plus réparé
l'agent SQL, et ce n'était pas son objet.

### 15.7 Ce qui reste ouvert après le §15

- **`temoin-colonnes-a-trous`** — l'agent SQL, sur les deux moteurs, par deux
  chemins différents. Le seul écart de la batterie qui ne soit pas expliqué
  par ce §15 ;
- ~~**les arguments d'outil en chaînes sous vLLM**~~ — **traité depuis**,
  [§16](#16-literal1-2-3-refusait-1--la-prédiction-que-seul-ollama-rendait).
  L'étendue mesurée n'était pas celle qu'on supposait : ce n'est pas vLLM qui
  rend des chaînes, c'est `Plan.features` qui ne déclare aucun type — le seul
  argument d'outil du système dans ce cas ;
- **la ceinture ne garde que des noms.** « je peux te demander quoi ? » est
  rattrapé parce que la même réponse omettait aussi des noms. Une réponse qui
  citerait tous les noms et aucune action passerait — c'est un comportement de
  modèle, tenu par le prompt et par la mesure, pas par du code ;
- **`systeme_request_limit = 4` est serré.** Il a suffi d'une consigne de
  prompt pour le faire dépasser. Le *fail-open* a fait son office — le tour est
  reparti au planificateur au lieu d'échouer — mais la réponse rendue n'était
  pas la bonne, et rien dans l'interface ne le disait.

## 16. `Literal[1, 2, 3]` refusait `'1'` : la prédiction que seul Ollama rendait

Le [§15.7](#157-ce-qui-reste-ouvert-après-le-15) laissait deux écarts ouverts.
Celui-ci en ferme un : `temoin-prediction`, correct sous Ollama et **à côté sous
vLLM**, pour la même question et le même modèle.

> Le `.env` n'a pas été touché. Ollama reste le moteur en service ; les mesures
> sous vLLM se font par `DAA_LLM_BASE_URL` et `DAA_LLM_MODEL` à l'exécution.

### 16.1 Le défaut, reproduit sur les deux moteurs avant de toucher à rien

Question de la batterie, conversation neuve, `HEAD d8c0142` :

> Prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80 livres,
> embarquée à Southampton, sans frère, sœur, parent ni enfant à bord.

Le plan rendu par le planificateur, valeur par valeur, avec son type Python :

| Feature | Ollama | vLLM |
|---|---|---|
| `sex` | `'female'` *(str)* | `'female'` *(str)* |
| `pclass` | `1` *(int)* | `'1'` *(str)* |
| `age` | `28` *(int)* | `'28'` *(str)* |
| `sibsp` | `0` *(int)* | `'0'` *(str)* |
| `parch` | `0` *(int)* | `'0'` *(str)* |
| `fare` | `80` *(int)* | `'80'` *(str)* |
| `embarked` | `'S'` *(str)* | `'S'` *(str)* |

**L'extraction est juste des deux côtés.** Le modèle a compris la question,
traduit « 1re classe » en `pclass=1` et « Southampton » en `embarked='S'`. Seul
le type diffère. Et les réponses divergent :

```
Ollama : Prédiction (titanic) : a survécu (probabilité 93.4%)
         — détail : n'a pas survécu : 6.6%, a survécu : 93.4%

vLLM   : Je ne peux pas encore lancer la prédiction titanic :
         - pclass (Classe du billet (1re, 2e, 3e)) : Input should be 1, 2 or 3 (reçu : '1')
         Peux-tu me donner ces informations ?
```

### 16.2 Ce n'est pas « vLLM rend des chaînes »

C'était l'explication de travail, et elle est fausse. Un tool aux arguments
**typés** a été posé aux deux serveurs — `integer`, `number`, `boolean`, et un
`integer` sous `enum` — avec `tool_choice="required"`
([`scripts/mesure_typage_des_arguments_d_outil.py`](../scripts/mesure_typage_des_arguments_d_outil.py)) :

```
Ollama : {"acompte":25.5,"convives":4,"etage":2,"nom":"Dupont","terrasse":true}
vLLM   : {"acompte": 25.5, "convives": 4, "etage": 2, "nom": "Dupont", "terrasse": true}
```

**Types identiques, aux espaces près.** Quand le JSON Schema déclare le type,
les deux serveurs le respectent. Ce qui les sépare, c'est ce qu'ils font quand
il ne déclare **rien** — et le schéma du `Plan` a exactement un endroit comme
ça :

```json
"features": { "additionalProperties": true, "type": "object" }
```

`Plan.features` est un `dict[str, Any]`, et c'est **délibéré** : le
planificateur extrait des valeurs sans savoir encore de quel dataset elles
relèvent. C'est le seul argument d'outil de tout le système qui arrive sans type
annoncé. Tous les autres champs du plan — `capability`, `source`, `dataset`,
`data_question`, `reason` — sont déclarés `string`, et sont indemnes : mesurés
sous vLLM, ils rendent bien des `str` et des `None`. Les cinq tools de l'agent
système et les trois de l'agent SQL n'ont que des arguments texte : indemnes par
construction. Cela ferme aussi le premier point resté ouvert au
[§8.3 de VLLM.md](VLLM.md#83-ce-qui-reste-non-mesuré-côté-agent-système).

### 16.3 L'étendue : tout arrivait en chaînes, une seule chose cassait

Les trois schémas de features, mesurés sous vLLM, question complète pour chacun :

| Schéma | Champs rendus en chaînes | Validation | Prédiction |
|---|---|---|---|
| `titanic` | **7 / 7** | **échoue** sur `pclass` | refusée |
| `iris` | **4 / 4** | passe | `setosa` (98.5 %) |
| `california_housing` | **8 / 8** | passe | 4.139 |

**Tout arrivait en chaînes ; une seule feature faisait échouer la validation.**
Pydantic, en mode souple, lit déjà `'28'` comme un `float` et `'322'` comme un
`int` — les dix-neuf champs `int`/`float` des trois schémas passaient donc tout
seuls. Il ne rattrape pas `Literal[1, 2, 3]`, qui **compare des valeurs** : pour
lui `'1'` n'est pas `1`. `pclass` est le seul `Literal` d'entiers des trois
schémas ; `sex` et `embarked` sont des `Literal` de chaînes, et une chaîne est
ce qu'ils attendent.

Autrement dit **iris et california passaient par accident**, et le prochain
`Literal` d'entiers ajouté à un schéma aurait rouvert le défaut. Il n'y a aucun
champ booléen dans les trois schémas aujourd'hui — la question posée en amont
n'était donc pas mesurable sur eux, et la conversion en traite un par avance.

### 16.4 La correction : une conversion, à la frontière, pilotée par le schéma

`validation.coerce_values`, entre le réalignement des clés et le schéma. Elle ne
peut pas vivre plus tôt : **le type attendu d'une feature n'existe nulle part
avant ce module**, puisque le planificateur ne connaît pas encore le dataset. Et
elle ne vit pas non plus dans les schémas : elle lit leurs annotations, donc
aucun des trois n'a à s'en soucier, ni le prochain.

Ce qu'elle fait, et ce qu'elle refuse de faire :

- elle ne touche **qu'aux chaînes** — une valeur déjà typée passe telle quelle ;
- elle ne convertit que vers un type **sans ambiguïté** : `Literal[1, 2, 3]`
  attend un `int`, `Literal['S', 'C', 'Q']` une `str` (rien à faire), un
  `Literal[1, 'un']` ou un `int | str` ne désignent rien et sont laissés ;
- une chaîne **illisible reste la chaîne d'origine**, pour que le schéma refuse
  en citant ce qui a été écrit plutôt qu'une exception avalée ailleurs ;
- les booléens passent par une table explicite, jamais par `bool()` — qui rend
  `True` pour `'false'`.

### 16.5 La garde n'a pas bougé

C'était le risque : remplacer un défaut visible par un défaut muet. Les cas
hostiles, tous rejoués après la correction :

| Entrée | Verdict | Message rendu |
|---|---|---|
| `pclass='1'` | **acceptée** | — *(prédiction : a survécu, 93.4 %)* |
| `pclass='4'` | **refusée** | `pclass (Classe du billet…) : Input should be 1, 2 or 3 (reçu : 4)` |
| `pclass='abc'` | **refusée** | `… Input should be 1, 2 or 3 (reçu : 'abc')` |
| `pclass='3e classe'` | **refusée** | `… Input should be 1, 2 or 3 (reçu : '3e classe')` |
| `pclass=''` | **refusée** | `… Input should be 1, 2 or 3 (reçu : '')` |
| `age='150'` | **refusée** | `age (Âge en années) : Input should be less than or equal to 100` |
| `age='douze'` | **refusée** | `age : … unable to parse string as a number (reçu : 'douze')` |
| `embarked='X'` | **refusée** | `embarked (Port d'embarquement…) : Input should be 'S', 'C' or 'Q'` |

Deux chemins distincts, et c'est voulu. `'4'` **est** converti, en `4`, et c'est
le `Literal` qui le refuse — le message cite `4`. `'abc'` n'est **pas**
convertible, reste `'abc'`, et le `Literal` le refuse en citant `'abc'`. Dans
les deux cas une erreur de validation lisible, jamais un silence.

`'3e classe'` n'est pas un cas de laboratoire : c'est ce que rend la source
Postgres sur le chemin `fetch_then_predict`, où la table `classes` porte le
libellé humain. Il doit rester refusé, et il l'est.

### 16.6 La batterie complète, sur les deux moteurs

```bash
# Ollama (le moteur du .env, inchangé)
uv run python scripts/mesure_surface_conversationnelle.py

# vLLM, par variables d'environnement
DAA_LLM_BASE_URL=http://localhost:8100/v1 \
DAA_LLM_MODEL=google/gemma-4-E4B-it-qat-w4a16-ct \
uv run python scripts/mesure_surface_conversationnelle.py
```

| Moteur | Passage | Méta | Témoins | Appels LLM (36 méta) | Durée des 36 méta |
|---|---|---|---|---|---|
| vLLM | §15 (avant ce §16) | 36 / 36 | 2 / 4 | 78 | 118 s, 122 s |
| vLLM | **après** | **36 / 36** | **3 / 4** | 78 | 79 s |
| Ollama | §15 (avant ce §16) | 36 / 36 | 3 / 4 | 78 | 568 s, 334 s |
| Ollama | **après** | **36 / 36** | **4 / 4** | 78 | 333 s |

**Aucune régression sur les 36 questions méta**, et le coût en appels LLM est
inchangé — 78 des deux côtés, comme au §15. Attendu : la correction est en aval
du nœud système, sur le chemin d'inférence, et ne lui ôte ni ne lui ajoute un
seul aller-retour. Les questions méta restent servies par l'agent système à 35
sur 36 sous Ollama et 34 sur 36 sous vLLM, exactement comme au §15.

Les durées ne comparent pas les deux serveurs : la même carte les servait tous
les deux.

### 16.7 Les témoins

| Témoin | vLLM §15 | vLLM après | Ollama §15 | Ollama après |
|---|---|---|---|---|
| `temoin-comptage` | correct | correct | correct | correct |
| `temoin-maximum` | correct | correct | correct | correct |
| `temoin-colonnes-a-trous` | à côté | à côté | à côté / erreur | **correct** |
| `temoin-prediction` | **à côté** | **correct** | correct | correct |

**`temoin-prediction` passe désormais sous vLLM**, avec la réponse mot pour mot
de l'oracle Ollama :

> Prédiction (titanic) : a survécu (probabilité 93.4%) — détail : n'a pas
> survécu : 6.6%, a survécu : 93.4%

Deux appels LLM, `capability = predict`, synthèse « modèle (déterministe) » :
même chemin, même coût, même résultat que sous Ollama.

**`temoin-colonnes-a-trous` reste hors sujet, et il faut le dire proprement.**
Il est passé sous Ollama à ce passage-ci — le §15.6 avait déjà relevé qu'il
n'est **pas déterministe** de ce côté : il a consommé 10 appels LLM, soit
exactement `retrieval_request_limit`, contre 5 pour les autres témoins. Il rate
toujours sous vLLM, et par le même chemin qu'au §15 : `SELECT * FROM passengers
WHERE … IS NULL`, 179 lignes rendues au lieu de la liste des colonnes. C'est un
défaut de l'agent SQL, antérieur à ce chantier, **non traité ici** : il appelle
sa propre mesure.

### 16.8 Ce qui reste ouvert après le §16

- **`temoin-colonnes-a-trous`** — le dernier écart de la batterie, sur les deux
  moteurs. Inchangé depuis le §14 ;
- **la conversion ne lit que des scalaires.** Hors booléen et nombre, elle
  s'abstient et laisse le schéma trancher. Aucun schéma de features n'a
  aujourd'hui de date, de décimal ni de liste ; le jour où l'un en aura, il
  faudra remesurer plutôt que supposer ;
- **`Plan.features` reste non typé**, et c'est le bon choix — mais c'est donc
  toujours le seul argument d'outil du système dont le serveur devine le type.
  La conversion rattrape la devinette ; elle ne la supprime pas.
