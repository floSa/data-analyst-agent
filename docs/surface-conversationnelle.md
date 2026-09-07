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

Trois partis pris, qui sont ce qui rend la mesure rejouable :

- **Chaque question est posée dans une conversation neuve.** Une question méta ne
  doit pas devoir son succès au contexte laissé par la précédente.
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
un test unitaire le verrouille.

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
