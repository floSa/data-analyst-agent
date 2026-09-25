# Surface conversationnelle — ce que l'agent sait répondre sur lui-même

> **Ce document est un journal de mesure** : ce que l'agent sait répondre sur lui-même, formulation par formulation, avant et après. Il reste à ce chemin parce que `src/`, les tests et les scripts le citent. Index : [historique/README.md](historique/README.md).

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
| **15** | le trou de la ceinture que la batterie a découvert, et la mesure à 36/36 | **oui** |
| **16** | le typage des arguments d'outil : le **seul** champ que le schéma ne déclare pas, et la prédiction qui n'allait pas au bout | **oui** |
| **17** | le planificateur **substituait** une valeur légale à l'entrée impossible : la garde mesurée sur une entrée que le système ne fabriquait pas | **oui** |
| **18** | un témoin qui **bouclait** : une question sur les colonnes, dans un prompt qui n'en connaissait pas | **oui** |
| **19** | la correspondance source → features **devinée** : trois requêtes pour une même question, et la déclaration qui les remplace | **oui** |
| **20** | un artefact **se nomme, se désigne et se rejoue** : le catalogue injecté sans son contenu, le rejeu mesuré au tour +2 et au tour +5, et la ligne de prompt qui en coûtait deux | **oui** |
| **21** | **dire qu'on ne l'a pas fait** : la désignation d'un artefact absent reconnue dans le message, l'absence affirmée par le catalogue, et les deux moitiés mesurées | **oui** |

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
Serveur LLM en place et sources réelles du catalogue
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

Commit `3a91723`, modèle en service, température 0.

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

### L'agent système et ses six outils

[`orchestrator/systeme.py`](../src/data_analyst_agent/orchestrator/systeme.py) —
un agent `pydantic-ai` à six outils typés, sur le modèle des trois outils de
l'agent SQL, qui fonctionnent déjà avec le modèle en service. Chaque outil rend un texte
de `introspection.py`, donc un texte **construit depuis un artefact du dépôt**.

| Outil | Source de vérité | Connexion ? |
|---|---|---|
| `sources_de_donnees` | `sources/catalogue.yaml` | non |
| `modeles_de_prediction` | `models/registry.yaml` | non |
| `attributs_d_un_modele` | `SCHEMAS` + `describe_features` | non |
| `capacites_de_l_agent` | les valeurs de `Capability`, plus l'inventaire | non |
| `schema_d_une_source` | l'ontologie de la source (+ son dictionnaire) | **oui** |
| `travailler_sur_une_source` | le catalogue déclaré + les relevés de volume | non |

Le sixième est d'une autre nature : il ne DIT pas un fait, il en **retient** un
— la source de travail de la conversation. C'est le second chemin de la liaison
d'une source, mesuré contre celui du planificateur et retenu parce que l'autre
avalait les questions (`docs/sources-de-demonstration.md`, dette A). Il ne
décide rien seul : l'appelant confronte la source demandée au texte de
l'utilisateur avant de lier, exactement comme
`_regle_source_de_la_conversation` le fait depuis §14.

**Et il répond quand on ne lui donne pas de nom.** Appelé avec une `source`
vide, il rend l'inventaire des sources et ne lie rien. Ce n'est pas une
commodité : son verbe — « travailler sur une source » — est la tournure même
avec laquelle on demande à l'agent sur quoi il travaille, et « tu bosses sur
quoi ? » venait se ranger sous lui. Le modèle, ne pouvant pas remplir `source`,
rendait le TOUR entier (`AUTRE`, aucun outil, retour au planificateur, une
clarification au lieu d'une réponse — mesuré deux campagnes le 2026-09-16). Le
verbe n'est pas retirable : c'est lui qui fait attraper « on se met sur X » ou
« passe-moi la main sur X », et l'en débarrasser fait retomber les ouvertures de
10 sur 10 à 4 sur 10. La règle qui en sort tient en une phrase — **un outil qui
ne peut pas remplir son argument doit avoir quelque chose à rendre, sinon c'est
le tour que le modèle rend** — et ce qu'il rend ici est la bonne réponse à la
question posée.

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
les porte pas. Trois défauts la disqualifient (`defaut_de_fondation`) :

- **un nom qu'aucun fait ne porte** — c'est `acfd8f5` par une autre porte. Un
  modèle à qui l'on demande les tables d'une base « familière » sait en citer de
  mémoire, et un nom inventé est plus nocif qu'une réponse absente : il a l'air
  d'une lecture de la source. Un test l'épingle : `flights` cité par le modèle,
  absent du catalogue, n'atteint jamais l'utilisateur.
- **un nom que l'outil a rendu et que la réponse omet** — une liste incomplète
  n'est pas une réponse à « quelles colonnes ? », et c'est le défaut mesuré au
  §4 : la version narrée avait laissé tomber deux colonnes sur dix.
- **une action que les faits annoncent et que la réponse ne dit pas.** Les deux
  premiers défauts portent sur des NOMS, et une puce de capacité n'en porte
  aucun : « - **analyser et visualiser** — du code Python… » ne nomme rien au
  sens technique, donc n'exigeait rien. La famille « que sais-tu faire ? »
  tenait par accident — le modèle qui résumait les capacités laissait tomber
  l'inventaire de la dernière ligne avec, et se faisait prendre sur
  l'inventaire. Le 2026-09-16, deux campagnes de suite, sa formulation a cité
  `titanic`, `iris` et `california_housing` en oubliant les quatre verbes :
  « je peux te demander quoi ? » rendait une liste de SUJETS et ne disait plus
  qu'il savait ANALYSER. Ce qui est exigé est le **radical** — `interrog`,
  `analys`, `predi`, `repond` —, jamais le mot entier : une réponse a le droit
  d'écrire « je prédis » ou « des prédictions », pas de se taire.

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

L'exemption liée à la prédiction est **bornée** au nœud `rappel` : un message qui
désigne un artefact déjà produit ne complète pas une prédiction, et ce nœud reste
armé pour lui. Sans cette borne, une prédiction restée en attente rendait
inatteignable un tableau que le fil avait pourtant produit
(`docs/parcours-de-l-agent.md`).


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
du nœud système qui décline. Sur le modèle en service, ce refus prend 1 à 3 secondes,
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

## 15. La batterie de questions méta, et le trou qu'elle a découvert

La bascule du moteur vers vLLM ([MOTEUR.md §7](MOTEUR.md#7-le-modèle-servi--gemma-4-e4b-it-qat-w4a16-ct))
a fait tomber quatre questions qui tenaient sous l'ancien moteur. Même code, même
modèle, deux serveurs. Le relevé du propriétaire, conversation neuve à chaque
fois :

| Question | moteur en service | ancien moteur |
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
à cela, et le [§7.4 de MOTEUR.md](MOTEUR.md#74-le-mauvais-analyseur--pas-un-400-un-faux-négatif-silencieux)
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
l'ancien moteur, le même modèle recopiait les faits presque au long — d'où les 11 à 16
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

| Question | vLLM avant | vLLM après | ancien moteur avant | ancien moteur après |
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

**Il n'est pas déterministe sous l'ancien moteur.** Le passage de référence rejoué ici
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
# l'ancien moteur, à l'époque celui du .env
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
| ancien moteur | avant | 34 / 36 | 2 / 4 | 83 | 305 s | 8,5 s |
| ancien moteur | **après, passage 1** | **36 / 36** | **3 / 4** | 78 | 568 s | 15,8 s |
| ancien moteur | **après, passage 2** | **36 / 36** | **3 / 4** | 78 | 334 s | 9,3 s |

**36 / 36 sur les quatre passages, et sur les deux moteurs.** Aucun passage de
référence antérieur n'avait fait mieux que 36 (§11, §14), et les deux passages
du §14 plafonnaient à 35.

Le **coût en appels LLM baisse** — 78 contre 82 et 83 — parce qu'une réponse
servie par la ceinture ne coûte pas d'aller-retour supplémentaire. Le temps de
paroi, lui, monte : les réponses sont **plus longues d'environ 20 %** (354 →
428 caractères en moyenne sous vLLM, 634 → 715 sous l'ancien moteur), puisqu'elles
portent désormais les noms qu'elles laissaient tomber. Le reste de l'écart —
334 s contre 568 s pour deux passages identiques — est la variance de la
machine, qui servait les deux moteurs sur la même carte pendant toute la
mesure : ce tableau ne compare pas des vitesses de serveurs.

Les questions méta sous l'ancien moteur restent servies à **35 sur 36 par l'agent
système** (34 avant), et sous vLLM à 34 sur 36 : la correction n'a déplacé
aucune question vers le planificateur.

### 15.6 Les témoins, et les deux questions de données qui restent

Les témoins ne mesurent pas l'agent système : ils vérifient qu'**il ne prend
pas** ce qui ne lui appartient pas.

| Témoin | vLLM avant | vLLM après | ancien moteur avant | ancien moteur après |
|---|---|---|---|---|
| `temoin-comptage` | correct | correct | correct | correct |
| `temoin-maximum` | correct | correct | correct | correct |
| `temoin-colonnes-a-trous` | à côté | à côté | à côté | **à côté / erreur** |
| `temoin-prediction` | à côté | à côté | **à côté** | **correct** |

Sur les quatre passages d'après, **le nœud système laisse passer les quatre
témoins** : « aucun outil appelé — passe au planificateur ». C'était déjà vrai
sous vLLM ; sous l'ancien moteur, `temoin-prediction` était **pris** par l'agent système,
qui répondait par son registre de modèles au lieu de prédire. L'exemple ajouté
au prompt le rend au planificateur — 3 essais sur 3 avant la batterie, puis les
deux passages complets.

Restent deux échecs, tous deux **en aval du nœud système**, et ce que leur
trace en dit :

- **`temoin-colonnes-a-trous`, sur les deux moteurs.** « Quelles colonnes de la
  table `passengers` contiennent des valeurs manquantes ? » Sous l'ancien moteur, l'agent
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
  modèle servi par vLLM rend ses arguments d'outil en **chaînes**, là où l'ancien moteur
  rend des entiers, et `Literal[1, 2, 3]` refuse `'1'`. C'est un écart de
  typage sur le chemin d'inférence, pas sur le chemin système ; le corriger
  touche `SCHEMAS` et la validation des features, et appelle sa propre mesure.

**Les témoins sous l'ancien moteur sont donc à 3 / 4 et non 4 / 4.** Le quatrième est
`temoin-colonnes-a-trous`, qui ratait déjà avant ce chantier : la correction
n'a rien déplacé vers le planificateur, mais elle n'a pas non plus réparé
l'agent SQL, et ce n'était pas son objet.

### 15.7 Ce qui reste ouvert après le §15

- **`temoin-colonnes-a-trous`** — l'agent SQL, sur les deux moteurs, par deux
  chemins différents. Le seul écart de la batterie qui ne soit pas expliqué
  par ce §15 ;
- ~~**les arguments d'outil en chaînes sous vLLM**~~ — **traité depuis**,
  [§16](#16-literal1-2-3-refusait-1--la-prédiction-qui-nallait-pas-au-bout).
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

## 16. `Literal[1, 2, 3]` refusait `'1'` : la prédiction qui n'allait pas au bout

Le [§15.7](#157-ce-qui-reste-ouvert-après-le-15) laissait deux écarts ouverts.
Celui-ci en ferme un : `temoin-prediction`, correct sous l'ancien moteur et **à côté sous
vLLM**, pour la même question et le même modèle.

> Le `.env` n'a pas été touché ; les mesures
> sous vLLM se font par `DAA_LLM_BASE_URL` et `DAA_LLM_MODEL` à l'exécution.

### 16.1 Le défaut, reproduit sur les deux moteurs avant de toucher à rien

Question de la batterie, conversation neuve, `HEAD d8c0142` :

> Prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80 livres,
> embarquée à Southampton, sans frère, sœur, parent ni enfant à bord.

Le plan rendu par le planificateur, valeur par valeur, avec son type Python :

| Feature | ancien moteur | vLLM |
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
ancien moteur : Prédiction (titanic) : a survécu (probabilité 93.4%)
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
ancien moteur : {"acompte":25.5,"convives":4,"etage":2,"nom":"Dupont","terrasse":true}
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
[§8.3 de MOTEUR.md](MOTEUR.md#83-ce-qui-reste-non-mesuré-côté-agent-système).

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
# l'ancien moteur, à l'époque celui du .env
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
| ancien moteur | §15 (avant ce §16) | 36 / 36 | 3 / 4 | 78 | 568 s, 334 s |
| ancien moteur | **après** | **36 / 36** | **4 / 4** | 78 | 333 s |

**Aucune régression sur les 36 questions méta**, et le coût en appels LLM est
inchangé — 78 des deux côtés, comme au §15. Attendu : la correction est en aval
du nœud système, sur le chemin d'inférence, et ne lui ôte ni ne lui ajoute un
seul aller-retour. Les questions méta restent servies par l'agent système à 35
sur 36 sous l'ancien moteur et 34 sur 36 sous vLLM, exactement comme au §15.

Les durées ne comparent pas les deux serveurs : la même carte les servait tous
les deux.

### 16.7 Les témoins

| Témoin | vLLM §15 | vLLM après | ancien moteur §15 | ancien moteur après |
|---|---|---|---|---|
| `temoin-comptage` | correct | correct | correct | correct |
| `temoin-maximum` | correct | correct | correct | correct |
| `temoin-colonnes-a-trous` | à côté | à côté | à côté / erreur | **correct** |
| `temoin-prediction` | **à côté** | **correct** | correct | correct |

**`temoin-prediction` passe désormais sous vLLM**, avec la réponse mot pour mot
de l'oracle :

> Prédiction (titanic) : a survécu (probabilité 93.4%) — détail : n'a pas
> survécu : 6.6%, a survécu : 93.4%

Deux appels LLM, `capability = predict`, synthèse « modèle (déterministe) » :
même chemin, même coût, même résultat que sous l'ancien moteur.

**`temoin-colonnes-a-trous` reste hors sujet, et il faut le dire proprement.**
Il est passé sous l'ancien moteur à ce passage-ci — le §15.6 avait déjà relevé qu'il
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

---

## 17. La 4e classe n'existe pas, et l'agent prédisait quand même

Les §15 et §16 ont fermé des écarts de *couverture* : une question à laquelle
l'agent ne savait pas répondre, une prédiction qu'un seul moteur rendait.
Celui-ci en ferme un autre, de nature différente : une question à laquelle
l'agent répondait **alors qu'il n'aurait pas dû**.

> « Prédis la survie d'une passagère de **4e classe** de 28 ans, tarif 80
> livres, embarquée à Southampton, sans frère, sœur, parent ni enfant à
> bord. »

La 4e classe n'existe pas : `pclass` vaut 1, 2 ou 3.

| Moteur | Réponse, prompt d'origine |
|---|---|
| ancien moteur | « Je ne peux pas encore lancer la prédiction titanic : pclass … (reçu : 4) » |
| vLLM | **« Prédiction (titanic) : a survécu (probabilité 64,6 %) »** |

Le plan rendu sous vLLM :

```json
"features": {"pclass": "3", "...": "..."},
"reason": "... Note: '4e classe' est interprété comme pclass=3 car les
           valeurs autorisées pour pclass sont 1, 2, 3."
```

**Le planificateur lit les valeurs autorisées dans le prompt et substitue une
valeur légale à l'entrée impossible.** La validation ne peut rien voir : elle
reçoit un `3` parfaitement valide. L'utilisateur reçoit une probabilité sur une
question qu'il n'a pas posée, et la substitution n'apparaît que dans `reason`,
un champ que l'interface ne montre pas.

### 17.1 La leçon de méthode, avant la correction

Le §16 avait testé `pclass='4'` — et l'avait vu refusé :

```python
def test_flux_predict_en_chaines_garde_la_garde(registry):
    """Une classe inexistante reste refusée, chaîne ou pas : relance, pas prédiction."""
```

Ce test **attaque le validateur directement**, en scriptant le plan. Il mesure
donc la dernière porte sur une entrée que **le chemin complet ne fabrique
jamais** : la substitution a lieu en amont, et aucun `4` n'atteint la
validation. Une garde vérifiée sur une entrée que le système ne produit pas ne
garde rien.

D'où la règle appliquée ici : **un cas hostile part d'un message utilisateur et
traverse toute la chaîne.** Les mesures de ce § sont prises par un runner qui
appelle `Orchestrator.ask()` avec la phrase telle qu'un utilisateur l'écrirait,
et relève `plan.features`, `plan.reason` et la réponse rendue.

### 17.2 L'étendue, mesurée champ par champ

Six entrées impossibles, sur les deux moteurs, prompt d'origine. « Refusé en
citant » veut dire : aucune prédiction, et la relance **rend à l'utilisateur la
valeur qu'il a écrite**.

| Cas hostile | Champ | moteur en service | ancien moteur |
|---|---|---|---|
| « 4e classe » | `pclass` (`Literal[1,2,3]`) | **substitué -> `'3'`, prédiction rendue** | refusé, cite `4` |
| « embarquée à Marseille » | `embarked` (`Literal['S','C','Q']`) | **substitué -> `'C'`, prédiction rendue** | refusé, cite `'Marseille'` |
| « de sexe neutre » | `sex` (`Literal['male','female']`) | refusé, cite `'neutral'` | refusé, cite `'neutre'` |
| « deux cent trente ans » | `age` (`ge=0, le=100`) | refusé, cite `230.0` | refusé, cite `230` |
| « tarif mille cinq cents livres » | `fare` (`ge=0, le=600`) | refusé, cite `1500.0` | refusé, cite `1500` |
| « un iris de l'espèce dracula » | — | prédiction rendue | prédiction rendue |

Quatre choses que la mesure apprend, et qu'il aurait été faux d'extrapoler :

- **Seuls les champs `Literal` sont exposés.** Ce sont les seuls dont
  `describe_features` montre les valeurs autorisées au planificateur. Les
  bornes numériques (`ge`/`le`) ne lui sont **pas** montrées : il ne peut pas
  rabattre `age=230` sur 100, et ne l'a jamais fait. Les trois champs `Literal`
  de tout le système sont `sex`, `pclass` et `embarked` — tous sur Titanic ;
  `IrisFeatures` et `CaliforniaHousingFeatures` n'en ont aucun.
- **La substitution demande une valeur légale plausible.** « 4e classe » a un
  voisin (3), « Marseille » en a un (Cherbourg, le port français). « neutre »
  n'en a aucun : il est passé tel quel, et refusé. La substitution n'est donc
  pas un mécanisme uniforme — elle se déclenche là où le modèle croit
  reconnaître ce que l'utilisateur *voulait* dire.
- **Le défaut ne se voyait pas partout.** L'ancien moteur refusait déjà les cinq. Vérifié par
  trois passages sur `pclass` et `embarked` : refus 3 fois sur 3. Un quatrième
  passage, antérieur, avait produit un `pclass=None` avec un `dataset` corrompu
  — **non reproduit**, et rapporté ici comme tel.
- **L'iris n'est pas de cette famille.** `species` est la **cible** du modèle,
  pas une de ses features : le schéma ne la connaît pas, et « dracula » n'a
  jamais été une valeur à substituer. C'est un défaut distinct — une contrainte
  posée sur la sortie, silencieusement ignorée — et il n'est **pas traité ici**.

### 17.3 Le remède : où il est, et où il n'est pas

Il n'est pas dans la validation, qui faisait déjà son travail. Il n'est pas non
plus dans `describe_features` : ce que cette fonction rend est servi au
planificateur (`graph.py:_datasets_description`) **et à l'utilisateur**
(`introspection.py`, « il me faut 7 attribut(s) : … »). Y glisser une consigne
d'extraction la ferait lire par quelqu'un à qui elle n'est pas adressée.

Il est dans `prompts/planner.txt`, à l'endroit exact où la tentation naît :

```
- Pour une feature à valeurs autorisées, procède en deux temps. D'ABORD
  traduis ce que l'utilisateur a dit vers la valeur autorisée qui le désigne :
  « 1re classe », « classe 1 » -> pclass=1 ; « embarquée à Southampton » ->
  embarked='S'. SEULEMENT si aucune valeur autorisée ne désigne ce qu'il a dit
  — « 4e classe », « embarquée à Marseille » — transmets la sienne telle qu'il
  l'a écrite : pclass=4, embarked='Marseille'. Jamais la valeur autorisée la
  plus proche, jamais un champ vide ou omis : c'est le système qui refuse, en
  citant ce que l'utilisateur a écrit.
```

**Une règle à deux faces, et l'ordre entre elles est le remède.** Les
formulations qui posaient les deux faces comme deux interdits concurrents ont
toutes échoué d'un côté ou de l'autre — c'est mesuré au §17.5. Ce qui tient,
c'est de les **ordonner** : traduire d'abord, transmettre seulement à défaut.

### 17.4 Le défaut, après

| Cas hostile | vLLM avant | vLLM après | ancien moteur avant | ancien moteur après |
|---|---|---|---|---|
| « 4e classe » | **prédiction** | refusé, cite `4` | refusé | refusé, cite `4` |
| « embarquée à Marseille » | **prédiction** | refusé, cite `'Marseille'` | refusé | refusé, cite `'Marseille'` |
| « de sexe neutre » | refusé | refusé, cite `'neutre'` | refusé | refusé, cite `'neutre'` |
| « deux cent trente ans » | refusé | refusé, cite `230.0` | refusé | refusé, cite `230` |
| « tarif mille cinq cents livres » | refusé | refusé, cite `1500.0` | refusé | refusé, cite `1500` |
| **Total refusé en citant** | **3 / 5** | **5 / 5** | **4 / 5** | **5 / 5** |

Et la réponse rendue à la question d'ouverture, désormais identique sur les
deux moteurs :

> Je ne peux pas encore lancer la prédiction titanic :
> - pclass (Classe du billet (1re, 2e, 3e)) : Input should be 1, 2 or 3 (reçu : 4)
> Peux-tu me donner ces informations ?

### 17.5 Ne pas casser l'inverse : les formes légitimes

Une correction qui rendrait l'agent littéral casserait l'extraction, qui est sa
raison d'être. Sept formulations légitimes, mesurées **avant et après**, trois
passages par variante et par moteur (les deux moteurs se sont montrés
reproductibles : les trois passages d'une même variante sont identiques).

| Forme légitime | vLLM avant | vLLM après | ancien moteur avant | ancien moteur après |
|---|---|---|---|---|
| « 1re classe » | aboutit | aboutit | aboutit | aboutit |
| « première classe » | aboutit | *sex manquant* | aboutit | aboutit |
| « classe 1 » | *sex manquant* | *sex manquant* | aboutit | aboutit |
| « pclass 1 » | *sex manquant* | *sex manquant* | aboutit | aboutit |
| « pclass 1 », registre mixte | *sex manquant* | aboutit | *incident* | aboutit |
| « embarquée à Southampton » | *embarked omis* | aboutit | aboutit | aboutit |
| « embarquée à Cherbourg » | aboutit | aboutit | aboutit | aboutit |
| **Total** | **3 / 7** | **4 / 7** | **6 / 7** | **7 / 7** |

**`pclass` et `embarked` sont correctement traduits dans les 7 cas, sur les
deux moteurs, avant comme après.** Les quatre écritures de `pclass` — « 1re
classe », « première classe », « classe 1 », « pclass 1 » — donnent toutes
`pclass=1` ; les deux ports donnent `'S'` et `'C'`. Aucune n'est devenue
littérale.

**Les trois cas qui n'aboutissent pas sous vLLM échouent tous sur `sex`**, que
le modèle doit déduire de « une passagère » — jamais sur la forme d'écriture
qu'on mesurait. Ce défaut-là **préexiste** : le prompt d'origine perdait `sex`
sur les deux mêmes cas, à l'identique sur trois passages. Il est sensible à
toute perturbation du prompt, dans les deux sens, et le `reason` montre que le
modèle *sait* : « Toutes les features requises sont présentes ou déduites
(sex=female, pclass=1…) » — pendant que la clé `sex` manque du dictionnaire. Ce
n'est donc pas un défaut de compréhension mais de génération structurée, et il
n'est **pas traité ici**.

### 17.6 Les variantes essayées, et ce qu'elles coûtaient

Quatre rédactions, toutes mesurées sur les deux moteurs plutôt qu'arbitrées à
l'intuition. Toutes ferment le défaut (5/5 hostiles) ; elles se départagent sur
ce qu'elles coûtent aux formes légitimes.

| Rédaction | vLLM légitimes | ancien moteur, légitimes |
|---|---|---|
| prompt d'origine | 3 / 7 | 6 / 7 |
| deux interdits concurrents, « transmettre » en dernier | 5 / 7 | 5 / 7, **incident** sur « 1re classe » |
| idem + « donne son NOM SEUL » pour `dataset` | 3 / 7 | 6 / 7 |
| **ordonnée « D'ABORD / SEULEMENT SI », allégée** (retenue) | **4 / 7** | **7 / 7** |
| idem + « toute feature déduite doit FIGURER » | 2 / 7 | (abandonnée) |

Deux enseignements qui dépassent ce §.

- **Allonger le prompt n'est pas gratuit**, exactement comme élargir
  `Capability` ne l'était pas (cf. `orchestrator/plan.py`). La dernière ligne
  du tableau est la plus parlante : une consigne de *complétude* — « toute
  feature que tu as su déduire doit figurer dans `features` » — a fait tomber
  les légitimes de 4/7 à 2/7. Ajoutée pour récupérer `sex`, elle a rendu le
  modèle plus prudent sur la déduction, et il a cessé de déduire.
- **`dataset` avait besoin de sa propre précision.** La variante sans elle
  faisait rendre `dataset='titanic (classification)'` — le libellé du prompt
  recopié en entier — d'où un `KeyError` remonté à l'utilisateur en
  « incident », 3 fois sur 3 sur « 1re classe ». La contrainte dit désormais
  « choisis `dataset` parmi les modèles listés, **et donne son NOM SEUL** ».

### 17.7 Ce que la suite garde

Deux tests, et aucun ne remplace l'autre.

- `tests/unit/test_prompts.py` — la **règle** est dans le prompt, ses deux
  faces. C'est le lieu du remède ; le test compare sur un gabarit aplati, pour
  qu'un repli de ligne ne fasse pas passer une règle pour absente.
- `tests/unit/orchestrator/test_graph.py` — la valeur transmise ressort
  **citée**, pour les trois champs énumérés, convertible (`'4'`) ou pas
  (`'4e classe'`, également observé). Sans la citation, la relance
  reproche un champ sans dire ce qui clochait, et l'utilisateur redonne la
  même valeur.

Ces tests ne peuvent pas garder le **comportement du modèle** : seul le runner
de ce § le mesure, et c'est pourquoi ses chiffres sont ici.

### 17.8 Pas de régression : la batterie complète, sur les deux moteurs

```bash
# l'ancien moteur, à l'époque celui du .env
uv run python scripts/mesure_surface_conversationnelle.py

# vLLM, par variables d'environnement
DAA_LLM_BASE_URL=http://localhost:8100/v1 \
DAA_LLM_MODEL=google/gemma-4-E4B-it-qat-w4a16-ct \
uv run python scripts/mesure_surface_conversationnelle.py
```

| Moteur | Passage | Méta | Témoins | Appels LLM (36 méta) | Durée des 36 méta |
|---|---|---|---|---|---|
| vLLM | §16 (avant ce §17) | 36 / 36 | 3 / 4 | 78 | 79 s |
| vLLM | **après** | **36 / 36** | **3 / 4** | 78 | 77 s |
| ancien moteur | §16 (avant ce §17) | 36 / 36 | 4 / 4 | 78 | 333 s |
| ancien moteur | **après** | **36 / 36** | 3 / 4 | 78 | 326 s |

**36 / 36 sur les deux moteurs, et 78 appels LLM des deux côtés** — inchangé
depuis le §15. Attendu : la correction ne touche qu'au contenu d'un prompt, pas
au nombre d'allers-retours.

`temoin-prediction` reste **correct sur les deux moteurs**, avec la même
réponse qu'au §16 (2 appels LLM, `capability = predict`, synthèse « modèle
(déterministe) ») : la correction n'a pas fermé le chemin qu'elle venait
d'ouvrir.

**Le témoin de l'ancien moteur passe de 4/4 à 3/4, et il faut le dire sans l'arrondir.**
Le quatrième est `temoin-colonnes-a-trous`, dont les §15.6 et §16.7 avaient
déjà relevé qu'il **n'est pas déterministe sous l'ancien moteur** : compté correct au
passage du §16, à côté ici, où il a consommé 12 appels LLM en tournant sur des
colonnes qu'il croyait absentes. Il rate sous vLLM à tous les passages, par le
chemin connu (`SELECT * … IS NULL`, 179 lignes au lieu de la liste des
colonnes). C'est un défaut de l'agent SQL, antérieur à ce chantier et
**explicitement hors de son périmètre**.

### 17.9 Ce qui reste ouvert après le §17

- **`temoin-colonnes-a-trous`** — le dernier écart de la batterie, sur les deux
  moteurs. Inchangé depuis le §14 ;
- **`sex` déduit de « une passagère », sous vLLM.** Trois formulations
  légitimes sur sept le perdent, de façon stable, avant comme après ce
  chantier. Le modèle le nomme dans `reason` sans l'émettre dans `features` :
  c'est un défaut de génération structurée, pas de compréhension, et le prompt
  y est un mauvais levier — deux rédactions ont fait bouger le chiffre dans les
  deux sens sans qu'aucune ne le fixe ;
- **un `dataset` inconnu remonte en « incident ».** `run_inference` lève un
  `KeyError` que l'orchestrateur ne rattrape qu'en erreur générique, là où la
  bonne réponse serait une relance. La contrainte de nommage rend le cas rare ;
  elle ne le supprime pas, et un modèle qui écrira autre chose le rouvrira ;
- **l'espèce d'iris, contrainte posée sur la CIBLE.** « Un iris de l'espèce
  dracula » rend une prédiction sans broncher, sur les deux moteurs : `species`
  n'est pas une feature, rien ne la valide, et l'agent n'a aucun endroit où
  remarquer que l'utilisateur a contraint ce qu'il était censé prédire ;
- **la substitution n'est fermée que là où le prompt montre des valeurs.**
  Les bornes numériques ne lui sont pas montrées, et c'est ce qui les protège
  aujourd'hui. Le jour où un schéma portera une énumération hors `Literal` — ou
  où `describe_features` montrera les bornes — il faudra **remesurer** plutôt
  que supposer que la règle couvre le nouveau cas.

## 18. Le témoin qui bouclait : une question sur les COLONNES, dans un prompt qui n'en connaissait pas

`temoin-colonnes-a-trous` — « Quelles colonnes de la table `passengers`
contiennent des valeurs manquantes ? » — est le seul témoin de la batterie qui
échouait. Il est relevé sans être corrigé depuis trois chantiers
([§15.6](#156-les-témoins-et-les-deux-questions-de-données-qui-restent),
[§16.7](#167-les-témoins), [§17](#17-la-4e-classe-nexiste-pas-et-lagent-prédisait-quand-même)),
chaque fois renvoyé au suivant comme « un défaut de l'agent SQL, antérieur ».

### 18.1 La boucle, capturée avant de toucher à quoi que ce soit

Les relevés précédents décrivaient le symptôme — « il boucle », « 97 à 112 s » —
sans jamais montrer les requêtes. C'est ce qui manquait pour corriger autrement
qu'à l'aveugle. Les voici, relevées dans la batterie elle-même, adaptateur
instrumenté (`var/chantier-c22/`).

**Ancien moteur — 10 allers-retours, plafond `retrieval_request_limit` épuisé, 73,4 s.**
Six requêtes :

| # | Requête | Rendu |
|---|---|---|
| 1 | `SELECT CASE WHEN COUNT(name) < COUNT(*) THEN 'name' ELSE NULL END AS name_missing, …` | `column "name" does not exist` |
| 2 | `SELECT (COUNT(*) - COUNT(name)) > 0 AS name_missing, …` | idem |
| 3 | identique à la 2 | idem |
| 4 | `SELECT COUNT(name) FROM passengers;` | `891` |
| 5 | `SELECT COUNT(*) FROM passengers;` | `891` |
| 6 | `SELECT COUNT(name) AS name_count, COUNT(age) AS age_count, …` | `column "name" does not exist` |

Deux choses s'y lisent. D'abord **l'agrégat large est perdu en route** : les
requêtes 1, 2, 3 et 6 n'ont plus de `FROM passengers` — le modèle écrit une
expression par colonne, la ligne s'allonge, et la clause finale tombe. Ensuite,
à la 4ᵉ, il **abandonne l'agrégat large pour une requête par colonne** : c'est
la boucle. Dix colonnes, deux requêtes chacune, et le plafond arrive avant la
réponse.

La réponse rendue ne venait alors plus des données du tout :

> Les colonnes suivantes de la table `passengers` contiennent des valeurs
> manquantes : `name`, `age`, `fare`, et `embarked`. Ces colonnes sont les
> seules qui ne sont pas définies comme `NOT NULL` dans le schéma […]

`name` et `fare` n'ont **aucun trou**. Le modèle, à court d'allers-retours,
répond par la nullabilité du SCHÉMA — ce qui est *possible* — là où la question
porte sur ce qui *est*.

**vLLM — une seule requête, et 179 lignes de données :**

```sql
SELECT * FROM passengers WHERE age IS NULL OR sibsp IS NULL OR parch IS NULL
  OR fare IS NULL OR embarked IS NULL;
```

La synthèse déterministe multi-lignes rend alors « 179 lignes retournées — voir
le tableau ci-dessous ». La liste des colonnes n'apparaît nulle part.

### 18.2 Ce qui distingue le cas qui aboutit

Rejoué seul, l'agent écrit parfois la bonne requête : le §15.6 le notait sans
l'expliquer. Quatre rejeux sous l'ancien moteur donnent l'explication, et elle est dans
la **forme du résultat**, pas dans le SQL :

| Requête écrite | Forme | Synthèse | Verdict |
|---|---|---|---|
| `CASE WHEN COUNT(col) < COUNT(*) …` | **1 ligne**, 6 colonnes | « résumé de la récupération » | nomme `age` et `embarked` ✓ |
| `SELECT 'name', COUNT(…) … UNION ALL …` | **6 lignes**, 2 colonnes | « résumé déterministe (multi-lignes) » | « 6 lignes retournées » ✗ |

Le même agrégat, juste dans les deux cas, passe ou tombe selon qu'il tient sur
une ligne. `_synthesize_query` jette la phrase du modèle dès qu'il y a plusieurs
lignes — une règle écrite contre la recopie d'un LISTING, qui frappe ici un
agrégat dont la phrase EST la réponse.

### 18.3 La cause : le prompt ne connaissait que deux sortes de question

`prompts/retrieval.txt` distinguait **lister des lignes** (« en pratique
`SELECT *` ») et **calculer un agrégat**, et il ajoutait :

> Réserve les projections restreintes (une seule colonne) et les agrégats
> (COUNT, AVG…) aux questions qui les demandent **explicitement**.

Mesurer CHAQUE colonne d'une table n'est ni l'une ni l'autre, et aucune
question de cette famille ne réclame un `COUNT` « explicitement ». La consigne
poussait donc activement vers le `SELECT *` — ce que vLLM a fait — ou laissait
le modèle improviser une requête par colonne — ce que l'ancien moteur a fait. **Rien ne
lui disait qu'il pouvait compter plusieurs colonnes d'un coup.**

### 18.4 Le remède, en deux moitiés — et ce qu'il n'est pas

Une réponse spéciale pour « valeurs manquantes » aurait été le retour du
gabarit retiré au §13. La correction nomme la **famille** : compter, agréger,
décrire une table sans la lire ligne à ligne.

**① Le prompt apprend la mesure par colonne.** Un troisième cas, à côté des deux
qui existaient : une seule requête, **une seule ligne**, **toutes** les colonnes
de la table — avec la raison, qui est celle du prompt depuis toujours : *le
schéma dit ce qui est POSSIBLE, la mesure seule dit ce qui EST*. Et
l'interdiction explicite d'une requête par colonne, avec son coût nommé.

**② `QueryResult.to_markdown` rend une ligne unique verticalement.** C'est la
moitié qu'on n'avait pas prévue, et elle a été trouvée en mesurant. Le prompt
corrigé donnait déjà le bon SQL et la bonne donnée :

```
| name_missing | age_missing | sibsp_missing | parch_missing | fare_missing | embarked_missing |
| 0            | 177         | 0             | 0             | 0            | 2                |
```

…et la réponse restait fausse : le modèle nommait `name`, mesuré à 0, à côté de
`age` et `embarked`. **Deux reformulations successives du prompt n'ont rien
changé**, de façon parfaitement stable. Le défaut n'était pas dans la consigne
mais dans ce qu'on donnait à lire : un tableau d'une seule ligne oblige à
aligner de tête un en-tête et une rangée de valeurs sur toute leur largeur.
Rendue `name_missing : 0`, la même valeur ne demande plus aucun alignement, et
la réponse devient exacte du premier coup.

Un agrégat rend presque toujours UNE ligne : c'est la forme normale de tout ce
qui compte, agrège ou décrit — pas un cas particulier. `to_markdown` n'a qu'un
seul consommateur en production, l'outil `run_sql`, c'est-à-dire ce que le
modèle LIT ; le tableau affiché à l'utilisateur est un artefact construit
ailleurs et ne bouge pas.

**Les garde-fous ne bougent pas** : `assert_read_only` est intact,
`retrieval_request_limit` reste à 10, `retrieval_max_rows` reste à 200. La
correction ne desserre rien — elle fait tenir la réponse dans le budget au lieu
d'agrandir le budget.

### 18.5 L'oracle avait un bord manquant, et il comptait juste une réponse fausse

En capturant l'état d'avant, la batterie a rendu **4/4 sous l'ancien moteur** sur la
réponse citée au §18.1 — celle qui nomme `name` et `fare`. `attendus_tous` ne
vérifiait que la PRÉSENCE de `age` et `embarked` ; deux colonnes pleines
nommées en plus ne coûtaient rien.

Une question dont l'oracle est une liste exhaustive se juge sur les deux bords.
`QuestionMeta` gagne donc `interdits`, dérivés de la source comme le reste — les
colonnes SANS trou de `passengers`, jamais écrites à la main. La comparaison se
fait sur un mot entier et non en sous-chaîne : la colonne `sex` vit à
l'intérieur du mot « sexe », que toute réponse française écrit, et un interdit
cherché en sous-chaîne déclarerait fausse une réponse juste.

Le §18.6 est donc mesuré contre un oracle **plus sévère** que celui des §15 à 17.

### 18.6 Le témoin, après — et trois questions voisines

Trois répétitions par question et par moteur, conversation neuve à chaque fois.

| Question | Moteur | Requêtes | Temps | Réponse |
|---|---|---|---|---|
| valeurs manquantes / `passengers` | vLLM | 1 | 3,5 s | `age` et `embarked` — 3/3 |
| | ancien moteur | 1 | 25–30 s | `age` et `embarked` — 3/3 |
| valeurs distinctes par colonne | vLLM | 1 | 5,3 s | les 10 comptes exacts — 3/3 |
| | ancien moteur | 1 | 27 s | corrects — 3/3 |
| la colonne la plus remplie | vLLM | 1 | 4,8 s | les colonnes sans trou — 3/3 |
| | ancien moteur | 1 à 2 | 33 s | 1/3 propre ; 2/3 répondent `passenger_id` |
| valeurs manquantes / `iris` | vLLM | 1 | 3,4 s | « aucune » — 3/3 |
| | ancien moteur | 1 à 2 | 23–26 s | « aucune » — 3/3 |

Le SQL produit, identique dans sa forme sur les deux moteurs :

```sql
SELECT COUNT(*) - COUNT(name) AS name_missing, COUNT(*) - COUNT(age) AS age_missing,
       COUNT(*) - COUNT(sibsp) AS sibsp_missing, COUNT(*) - COUNT(parch) AS parch_missing,
       COUNT(*) - COUNT(fare) AS fare_missing, COUNT(*) - COUNT(embarked) AS embarked_missing
FROM passengers
```

**De 10 allers-retours et 73,4 s à une seule requête.** Et `iris` — une source
fichier, dialecte DuckDB, sans aucun trou — est répondue par la même mécanique :
la correction ne connaît ni la table ni la question.

Reste un écart, honnête : sous l'ancien moteur, « la colonne la plus remplie » repart
deux fois sur trois vers un second `run_sql` à 891 lignes et conclut
`passenger_id`. Ce n'est pas faux — `passenger_id` est bien pleine — mais c'est
le dernier endroit de la famille où le modèle relit des lignes au lieu de lire
sa mesure.

### 18.7 Pas de régression : la batterie complète, sur les deux moteurs

Batterie complète, conversation neuve à chaque question, oracle durci du §18.5.

| | vLLM avant | vLLM après | ancien moteur avant | ancien moteur après |
|---|---|---|---|---|
| Questions méta | 36 / 36 | **36 / 36** | 36 / 36 | **35 / 36** |
| Témoins | 3 / 4 | **4 / 4** | 4 / 4 *(faux vert)* | **4 / 4** |
| Coût, méta | 78 appels | 78 appels | 78 appels | 83 appels |
| `temoin-colonnes-a-trous` | 5 appels | 5 appels | **10 appels, 73,4 s** | **5 appels** |

Le « 4 / 4 » de l'ancien moteur AVANT est celui du §18.5 : la réponse nommait `name` et
`fare`. Sous l'oracle durci, cette réponse-là compte désormais à côté — la
colonne « ancien moteur avant » est donc plus flatteuse que la réalité, et la seule
comparaison honnête est celle du texte rendu, donnée au §18.1.

**L'écart de l'ancien moteur après — `sources-premiere-personne`, et il n'est pas d'ici.**
« sur quoi je peux travailler ? » tombe dans le repli, 3 fois sur 3, avec la
trace `agent système écarté (incident 7d753f5a) — passe au planificateur`. Le
chemin est celui de l'agent système, que ce chantier ne touche pas : ni le
prompt SQL ni `to_markdown` n'y passent. Vérifié plutôt que supposé — **les
mêmes trois rejeux avec les deux fichiers corrigés remis dans leur version
d'avant donnent les mêmes trois replis**. C'est le `systeme_request_limit = 4`
déjà signalé comme serré au [§15.7](#157-ce-qui-reste-ouvert-après-le-15), et
le *fail-open* joue son rôle : le tour repart au planificateur au lieu
d'échouer. À traiter pour lui-même.

### 18.8 Ce que la suite garde

Trois tests, et chacun tient une moitié de ce qui a été mesuré :

- le rendu vertical d'une ligne unique, sur l'exemple exact qui a fait la
  découverte — `0 | 177 | 0 | 0 | 0 | 2` ;
- le rendu tabulaire d'un résultat à UNE colonne, inchangé : il n'y a rien à
  aligner, et le cas qui marchait ne devait pas bouger ;
- la famille nommée dans le prompt — mesure par colonne, une requête, une
  ligne, toutes les colonnes, et la lecture d'un 0.

Suite complète : **875 passés, 99,49 %** (872 avant le chantier).

### 18.9 Ce qui reste ouvert après le §18

- **`sources-premiere-personne` sous l'ancien moteur** — l'agent système écarté sur
  incident, 3 fois sur 3, indépendamment de ce chantier (§18.7). C'est le
  `systeme_request_limit = 4` du §15.7 qui remonte ;
- **« la colonne la plus remplie » sous l'ancien moteur** — deux fois sur trois, un
  second `run_sql` à 891 lignes et une conclusion tirée des lignes plutôt que
  de la mesure. La réponse n'est pas fausse ; le chemin n'est pas celui qu'on
  voulait ;
- **la synthèse déterministe multi-lignes reste un couperet.** Le §18.2 montre
  qu'un agrégat juste, mis en `UNION ALL`, perd sa phrase. Le remède retenu
  contourne le problème — on demande une ligne unique, et on la rend lisible —
  il ne le supprime pas. Le jour où une question de la famille voudra
  légitimement plusieurs lignes, `_synthesize_query` jettera de nouveau la
  réponse ;
- **`fetch_then_predict`** — la source Postgres rend `'3e classe'` là où le
  schéma attend `pclass`. Antérieur, distinct, sur les deux moteurs ;
  explicitement hors du périmètre de ce chantier. **Traité au
  [§19](#19-la-ligne-venait-de-la-base-et-la-prédiction-la-refusait)**, où la
  mesure a montré non pas un symptôme mais trois.

## 19. La ligne venait de la base, et la prédiction la refusait

Relevé depuis quatre chantiers et renvoyé chaque fois au suivant
([§15.6](#156-les-témoins-et-les-deux-questions-de-données-qui-restent),
[§16](#16-literal1-2-3-refusait-1--la-prédiction-qui-nallait-pas-au-bout),
[§17](#17-la-4e-classe-nexiste-pas-et-lagent-prédisait-quand-même),
[§18.9](#189-ce-qui-reste-ouvert-après-le-18)) : `fetch_then_predict` — aller
chercher des lignes en base, puis prédire dessus — échouait sur des lignes
venues de la base elle-même.

> « Prédis la survie des cinq premiers passagers de la base. »

### 19.1 Le défaut reproduit, cinq tirages par moteur

Le relevé précédent nommait un seul symptôme : « la source rend `'3e classe'`
là où le schéma attend `pclass` ». Mesuré, il y en a **trois**, et c'est le
fait central de ce §. Cinq tirages par moteur, avec le SQL réellement exécuté
et la ligne réellement reçue :

| Moteur | Tirages | SQL choisi pour la classe | Ce que la ligne portait | Lignes prédites |
|---|---|---|---|---|
| vLLM | 5/5 | `p.class_id` — **sans alias** | colonne `class_id` | **0/5** |
| ancien moteur | 4/5 | `c.label AS pclass` | `'3e classe'` | **0/5** |
| ancien moteur | 1/5 | `class_id AS pclass` | `3` | 5/5 |

Un sixième tirage, pris avant la série, avait donné `c.level AS pclass` — la
réponse juste, par une troisième route. Quatre requêtes différentes pour une
même question, sur un même schéma.

Ce que la validation disait, mot pour mot :

- sous vLLM — `Aucune des 5 lignes récupérées n'a passé la validation (pclass
  (Classe du billet (1re, 2e, 3e)) : valeur manquante) — pas de prédiction.`
  La colonne s'appelait `class_id`, elle ne portait aucun nom du schéma, elle a
  été écartée du payload ;
- sous l'ancien moteur — même refus, pour une raison opposée : la colonne s'appelait bien
  `pclass`, et valait `'3e classe'`.

**Le défaut n'est donc pas une valeur mal traduite. C'est une correspondance
devinée**, qui tombe juste ou faux selon le moteur, le tirage, et la table sur
laquelle le modèle a posé les yeux.

### 19.2 Deux écarts empilés, qu'il ne faut pas confondre

La base est modélisée en deux tables. `passengers` porte `class_id`, clé
étrangère vers `classes`, qui porte `level` (l'entier) **et** `label` (le
libellé). Le schéma de prédiction, lui, attend `pclass` — le nom du dataset
d'entraînement, pas celui de la base.

- **écart de nom** : `pclass` ne nomme aucune colonne de la source ;
- **écart de représentation** : la même information existe en base sous deux
  formes, `3` et `'3e classe'`, et une seule convient au modèle.

Les deux se règlent séparément : nommer la colonne ne dit pas comment lire sa
valeur, et traduire la valeur ne dit pas dans quelle colonne la prendre.

### 19.3 L'étendue, colonne par colonne

La consigne était de mesurer chaque colonne, et non de supposer que seule
`pclass` divergeait. Relevé sur la base réelle (891 passagers), en confrontant
chaque champ du schéma aux colonnes des deux tables :

| Feature | Type attendu | Colonne de même nom | Domaine lu en base | Verdict |
|---|---|---|---|---|
| `sex` | `Literal['male','female']` | `passengers.sex` | `female`, `male` — 0 NULL | **aligné**, domaine exactement le `Literal` |
| `pclass` | `Literal[1,2,3]` | **aucune** | trois candidates : `passengers.class_id` (1-3), `classes.level` (1-3), `classes.label` (`'1re classe'`…) | **seul écart** — de nom ET de représentation |
| `age` | `float` 0-100 | `passengers.age` | 0,42 → 80 — **177 NULL** | aligné ; les NULL écartent la ligne, comme prévu |
| `sibsp` | `int` 0-10 | `passengers.sibsp` | 0-8 — 0 NULL | aligné, domaine inclus dans les bornes |
| `parch` | `int` 0-10 | `passengers.parch` | 0-6 — 0 NULL | aligné |
| `fare` | `float` 0-600 | `passengers.fare` | 0 → 512,33 — 0 NULL | aligné |
| `embarked` | `Literal['S','C','Q']` | `passengers.embarked` | `C`, `Q`, `S` — **2 NULL** | aligné ; les NULL écartent la ligne |

Sept features, **un seul écart**. Mais le mesurer changeait la correction : si
trois colonnes avaient divergé, une table de traduction par source aurait été
la forme évidente. Une seule, et c'est la *place* de la déclaration qui
compte, pas son volume.

### 19.4 Où la correspondance a sa place, et pourquoi pas ailleurs

Trois candidats étaient à éprouver.

**Le schéma de features — écarté, et mesuré.** `TitanicFeatures` sait qu'il
attend `pclass ∈ {1,2,3}` ; il ne sait pas d'où ça vient, et il ne peut pas le
savoir : le **même** modèle `titanic` est alimenté par deux sources de ce dépôt,
la base Postgres (`classes.level`) et le CSV vendorisé `sources/titanic.csv`
(en-tête `Pclass`), et par trois de plus dans les tests. Un schéma qui
déclarerait la colonne devrait les énumérer toutes. C'est exactement la table en
dur retirée en C13, déplacée d'un cran vers le modèle.

**Le dictionnaire de la source — écarté, et mesuré aussi.** C'est le candidat
sérieux : le dictionnaire existe déjà pour dire ce que les données *veulent
dire*, et c'est bien de cela qu'il s'agit. Il a donc été essayé pour de bon —
le paragraphe qu'un dictionnaire écrirait, ajouté au prompt de l'agent SQL,
sous vLLM :

> « `classes.level` porte l'ENTIER (1, 2, 3) ; `classes.label` porte le libellé
> humain. La feature `pclass` attend l'entier : utilise `classes.level`, jamais
> `classes.label` ni `passengers.class_id`. »

**Et ça marche : 3 tirages sur 3, `c.level AS pclass`.** Le résultat est rapporté
tel quel, parce qu'il est le vrai argument de la décision — et qu'il ne suffit
pas :

1. **la prose persuade, elle ne déclare pas.** Ce 3/3 vaut pour ce moteur, ce
   modèle, cette rédaction. Le §19.1 vient de montrer quatre requêtes
   différentes sur la même consigne : ce projet a changé de moteur il y a un
   chantier, et une correction qui tient par la formulation est une correction
   qu'il faut remesurer à chaque bascule ;
2. **une prose absente ne produit aucun refus.** Une source dont le
   dictionnaire ne dit rien de `pclass` retombe exactement sur le défaut du
   §19.1 — silencieusement ;
3. **le dictionnaire n'atteint même pas l'endroit du défaut.** Il n'est lu
   aujourd'hui que par `orchestrator/systeme.py`, pour répondre au sens d'une
   colonne. L'y brancher ferait entrer dans le prompt SQL tout ce qu'un
   dictionnaire contient par ailleurs — pièges de modélisation, unités,
   historique — pour une seule phrase utile.

**Le catalogue — retenu.** La source y est déclarée par un humain, dans un
fichier, et c'est la source qui *sait* quelle colonne porte quoi. Chaque source
peut donc déclarer `features: {dataset: {feature: colonne}}` :

```yaml
  - type: postgres
    name: titanic
    dsn: postgresql+pg8000://…/titanic
    features:
      titanic:
        sex: passengers.sex
        pclass: classes.level      # l'ENTIER, pas classes.label
        age: passengers.age
        …
```

La différence avec la prose n'est pas de style : cette déclaration est **lue par
le code**, trois fois.

### 19.5 Ce que la déclaration fait, et ce qu'elle refuse

`agents/inference/correspondance.py` en tire trois usages, et c'est ce qui la
distingue d'un dictionnaire :

1. **la consigne SQL devient exacte.** Au lieu de « renvoie des colonnes nommées
   exactement : `sex, pclass, …` », l'agent reçoit `classes.level AS pclass`,
   ligne par ligne. Il ne reste rien à choisir ;
2. **le rapprochement est mécanique.** La ligne reçue est lue par le nom déclaré
   — l'alias demandé, ou le nom nu de la colonne si l'agent l'a omis, à la casse
   près. Elle ne dépend plus de ce que l'agent a bien voulu aliaser ;
3. **l'absence de déclaration est un refus, pas un silence.** Une source du
   catalogue qui ne déclare rien pour un modèle est refusée **avant** d'être
   interrogée.

L'écart de représentation, lui, se déclare en forme longue — et c'est la
deuxième moitié, tenue séparément :

```yaml
        pclass:
          column: classes.label
          values: {"1re classe": 1, "2e classe": 2, "3e classe": 3}
```

**Ce n'est pas un relâchement de la garde.** Une valeur qu'aucune traduction
déclarée ne couvre est laissée **telle quelle**, et le schéma la refuse en
citant ce qu'il a lu — `'troisième classe'` ressort en « Input should be 1, 2 or
3 (reçu : `'troisième classe'`) ». Substituer ici une valeur légale serait
précisément la faute du [§17](#17-la-4e-classe-nexiste-pas-et-lagent-prédisait-quand-même).
Un `age` à 150 venu de la base est refusé comme un `age` à 150 venu de
l'utilisateur, et le lot reste borné par `retrieval_max_rows`.

### 19.6 Ce qui n'a AUCUN fichier où déclarer

Une exception, et une seule : un tableau produit au tour précédent et réinjecté
sous `resultat_1` (« prédis ces lignes ») n'est écrit dans aucun YAML. Ses
colonnes sont celles que la requête précédente a nommées ; on les rapproche par
leur nom, ce qui n'invente rien, et une feature qu'aucune colonne ne porte reste
absente du payload — donc réclamée par le schéma, sous son nom. Les sources du
catalogue, elles, ont un fichier où écrire, et le refus les y renvoie.

### 19.7 La mesure d'après

Même question, même protocole, cinq tirages par moteur :

| Moteur | Avant | Après |
|---|---|---|
| vLLM | 0/5 lignes prédites, 5 tirages sur 5 | **5/5 lignes prédites, 5 tirages sur 5** |
| ancien moteur | 0/5 sur 4 tirages, 5/5 sur 1 | **5/5 lignes prédites, 5 tirages sur 5** |

Les probabilités rendues, identiques sur les deux moteurs — le predict est
déterministe et sans LLM, seul le chemin qui l'alimente changeait :

| # | Passager | Prédiction | Confiance | Issue réelle (base) |
|---|---|---|---|---|
| 1 | Braund, Mr. Owen Harris | n'a pas survécu | 0,9122 | `survived = 0` ✓ |
| 2 | Cumings, Mrs. John Bradley | a survécu | 0,9143 | `survived = 1` ✓ |
| 3 | Heikkinen, Miss. Laina | a survécu | 0,6184 | `survived = 1` ✓ |
| 4 | Futrelle, Mrs. Jacques Heath | a survécu | 0,8810 | `survived = 1` ✓ |
| 5 | Allen, Mr. William Henry | n'a pas survécu | 0,9245 | `survived = 0` ✓ |

Cinq sur cinq. La colonne `survived` a été relue dans la base, et non récitée :
c'est l'oracle, pas un souvenir.

**L'écart de représentation, mesuré à part.** La même base, déclarée sur
`classes.label` avec sa table `values`, sous vLLM : l'agent sélectionne
`c.label AS pclass`, la ligne arrive avec `'3e classe'`, et les cinq
prédictions sont **identiques à celles du tableau ci-dessus**. La table de
détail montre `'3e classe'` — la source dit ce qu'elle dit — pendant que le
modèle a reçu `3`.

### 19.8 Le refus, sur une source qui ne déclare rien

Mesuré sur `tests/catalogues/ambiguite/titanic-en-premier.yaml`, qui déclare une
source `titanic` pointant sur le CSV vendorisé, sans bloc `features` :

```
Je n'ai pas pu répondre : la source 'titanic' ne déclare pas quelles colonnes
alimentent le modèle 'titanic'. Ajoutez-lui dans le catalogue un bloc
`features: {titanic: ...}` avec une entrée par feature attendue (sex, pclass,
age, sibsp, parch, fare, embarked) — sur le modèle `sex: <table>.<colonne>`.
Sans lui, la colonne qui porte chaque feature serait devinée.
```

Trace : `[fetch_predict] correspondance non déclarée`. **Aucune requête n'a été
envoyée à la source** — le refus tombe avant l'ouverture.

Ce cas mérite d'être regardé de près, parce qu'il est le plus tentant : ce CSV
porte `Pclass`, `Sex`, `Age`… Le rapprochement par le nom, insensible à la
casse, **aurait marché**. C'est précisément ce qu'on refuse : ce qui marche par
coïncidence sur une source se trompe sur la suivante, où `pclass` vit dans une
autre table sous un autre nom — et ce § est le compte rendu de cette
coïncidence-là.

Une déclaration **incomplète** ou portant une feature que le modèle n'attend pas
est refusée de la même façon, en nommant ce qui manque ou ce qui est en trop.

### 19.9 Ce que la suite garde

Un fichier de tests pour le module (`tests/unit/inference/test_correspondance.py`,
17 tests) et un test de bout en bout dans le graphe. Ce qu'ils tiennent, et non
ce qu'ils couvrent :

- la forme courte vaut la forme longue, et le catalogue relit la déclaration ;
- la consigne SQL nomme la colonne source **et** son alias ;
- la feature est reconnue sous son alias, sous le nom nu de la colonne déclarée,
  et à la casse près ;
- une colonne déclarée mais absente de la ligne n'est **pas inventée** : le
  schéma la réclame sous son nom ;
- le libellé déclaré est traduit ; **un libellé non déclaré reste tel quel et le
  schéma le refuse en le citant** ;
- un `age` hors bornes venu de la base est refusé comme un autre ;
- les trois refus — rien de déclaré, déclaration incomplète, feature inconnue du
  modèle — nomment chacun ce qu'il faut écrire ;
- de bout en bout : une source du catalogue sans déclaration refuse **sans
  interroger la source**, et la trace le dit.

Une phrase de la consigne SQL est tenue par un test, et elle vient d'une mesure :
« le filtre et le nombre de lignes restent ceux de la demande ci-dessus ». Sans
elle, une consigne de sept lignes recouvrait la demande — sous l'ancien moteur, « les
cinq premiers passagers » repartait **sans `LIMIT`** et ramenait toute la table,
coupée à 200 par `retrieval_max_rows`.

Suite complète : **893 passés, 99,50 %** (875 avant le chantier).

### 19.10 La batterie rejouée, sur les deux moteurs

Ce chantier touche le catalogue et le chaînage de prédiction, pas le routage
des questions méta. La batterie est donc rejouée pour montrer qu'il n'a rien
déplacé, et non pour mesurer autre chose.

| Moteur | Questions méta | Témoins | Coût |
|---|---|---|---|
| vLLM (`google/gemma-4-E4B-it-qat-w4a16-ct`, port 8100) | **36/36** | **4/4** | 78 + 17 appels LLM |
| ancien moteur | **35/36** | **4/4** | 83 + 17 appels LLM |

Le seul écart, `sources-premiere-personne` sous l'ancien moteur, est celui que le
[§18.7](#187-pas-de-régression--la-batterie-complète-sur-les-deux-moteurs) a déjà instruit et rendu à sa
cause : `agent système écarté (incident 240eef48) : The next request would
exceed the request_limit of 4`. C'est le `systeme_request_limit = 4` signalé
serré au [§15.7](#157-ce-qui-reste-ouvert-après-le-15), le *fail-open* joue son
rôle, et il n'est pas arrondi ici.

Le témoin `temoin-colonnes-a-trous` passe sur les deux moteurs, avec l'oracle
durci au §18 — celui qui vérifie aussi les colonnes **interdites**. Il n'a pas
été desserré.

### 19.11 Ce qui reste ouvert après le §19

- **`sources-premiere-personne` sous l'ancien moteur** — inchangé depuis le §18.7, cause
  identifiée (`systeme_request_limit = 4`), à traiter pour lui-même ;
- **la déclaration n'est pas vérifiée contre la source.** Rien ne relit le
  schéma de la base pour confirmer que `classes.level` existe. Une déclaration
  fausse produit un SQL en erreur, que l'agent tente de corriger, puis une
  feature absente — lisible, mais tard. Le catalogue pourrait le dire au
  chargement ;
- **la déclaration est par source ET par dataset.** Deux sources qui exposent la
  même modélisation la répètent. Rien ne le justifie tant qu'il y en a deux ;
- **le relevé d'une source ne choisit toujours pas sa colonne de date** quand il
  y en a plusieurs, cf. [axes-amelioration.md](axes-amelioration.md).

## 20. Un artefact se nomme, se désigne et se rejoue

Le besoin, dans les mots du propriétaire :

> Le code qui génère une image doit pouvoir être rappelé pour être modifié. Si la
> personne dit « je veux que les barres soient bleues au lieu de rouges », on doit
> être en capacité de retrouver qu'on parle de cet artefact qui est le bout de code,
> de le récupérer en contexte, de le modifier et de le réexécuter. Pareil pour les
> jeux de données intermédiaires : les résultats d'une requête, on peut les stocker
> et les lier à une conversation pour pouvoir y revenir dessus.

### 20.1 Ce qui marchait déjà, et où exactement ça s'arrêtait

**« Mets les barres en bleu » fonctionnait.** Le contexte du tour précédent portait
le code produit (`ConversationContext.last_code`), l'agent d'analyse le recevait en
`previous_code`, et il repartait dessus. Mesurer ça n'aurait rien appris.

Ce qui ne fonctionnait pas tient en trois limites, et elles sont dans le code, pas
dans le modèle :

1. **la profondeur était de UN tour.** `ConversationContext` était écrasé à chaque
   tour. La casse n'est donc pas au tour +1, elle est **au tour +2** : « reprends le graphe
   de tout à l'heure », trois tours plus tard, ne trouvait plus rien ;
2. **le code n'était pas un artefact.** `last_code` ne survivait qu'un tour, **et**
   seulement si la source du tour suivant était identique (`last_code_for`). Deux
   limites arbitraires, dont la seconde n'a jamais été justifiée ;
3. **les figures n'étaient pas persistées.** Un `image/png` partait dans la réponse et
   disparaissait. Le propriétaire s'en moque pour l'image — c'est le **code** qui doit
   revenir.

La conséquence est une conversation qui ne se souvient que de son dernier tour, dans
un produit dont le fil, lui, s'affiche en entier.

### 20.2 Ce qui a été bâti

Un **magasin unique** par conversation, trois natures (`kind`) :

| `kind` | ce que c'est | nom | fichier |
|---|---|---|---|
| `table` | résultat de requête, lot de prédiction | `resultat_1`… | `.csv` |
| `figure` | le **code Python** d'une analyse qui a rendu une image | `graphique_1`… | `.py` |
| `code` | le code Python d'une analyse sans image | `analyse_1`… | `.py` |

`figure` n'est pas l'image. Repeindre un PNG ne rend rien ; rejouer son code rend une
image neuve. Chacun porte un **nom**, une **description d'une ligne** et la **question
qui l'a produit**.

Dans le prompt n'entre que le **catalogue** — une ligne chacun, jamais le contenu.
C'est le motif « le système de fichiers comme contexte » : on injecte l'index, on
ouvre à la demande.

Deux outils, et c'est le **modèle** qui décide de les appeler — comme pour les
questions sur le système depuis le [§10](#10-ce-qui-a-remplacé-le-lexique--un-outil-et-le-modèle-qui-décide),
et pour la même raison : « le graphe de tout à l'heure », « le camembert », « ce que tu
m'as sorti avant » sont une famille ouverte, et le [§9](#9-le-lexique-avait-un-plafond-et-il-a-été-mesuré)
a mesuré qu'un lexique y plafonne à 3 formulations sur 10.

- `lire_un_artefact(nom)` — rend le contenu ;
- `rejouer_un_code(nom, modification)` — reprend le code, applique la modification,
  **réexécute** dans le bac à sable, garde-fous inchangés.

Le nœud `rappel` s'insère entre `system` et `plan`, et **se retire sans appeler le
modèle quand le fil n'a rien produit**. Précondition structurelle, pas lexique : le
catalogue est vide, les outils ne pourraient que refuser. C'est aussi ce qui garantit
que la batterie de ce document — une conversation neuve par question — ne le paie ni
n'en voit le comportement.

### 20.3 Le parcours mesure la PROFONDEUR, pas la reprise

`scripts/mesure_rappel_dartefact.py`, huit tours dans **une** conversation, contre le
vrai système — serveur LLM en place, Postgres réel, bac à sable Docker. Deux questions
**sans rapport** sont intercalées entre la figure et sa reprise, délibérément : c'est
le **tour +2** qui est la mesure. Puis on revient à la première figure **cinq tours
plus tard, deux figures plus loin**.

Le verdict est **mécanique**, lu dans la trace et le magasin — quel nœud a répondu,
quel artefact il a nommé, ce que le magasin porte ensuite. Une réponse qui « a l'air »
d'avoir rejoué ne compte pas.

### 20.4 La mesure, sur les deux moteurs

Huit tours, une conversation, `titanic` en Postgres, les figures réellement
exécutées en bac à sable.

| # | Tour | Ce qu'on exige | moteur en service | ancien moteur |
|---|---|---|---|---|
| 1 | *Fais-moi un graphique en barres du nombre de passagers par classe.* | une figure, et son code retenu sous un nom | ✅ `graphique_1` | ✅ `graphique_1` |
| 2 | *Combien de passagers y a-t-il en tout ?* | requête ordinaire, le rappel décline | ✅ | ✅ |
| 3 | *Et quel est l'âge moyen des passagers ?* | requête ordinaire, le rappel décline | ✅ | ✅ |
| **4** | ***Reprends le graphe de tout à l'heure et mets les barres en bleu.*** | **le rejeu du code du tour 1** | ✅ **`rejeu de graphique_1` → `graphique_2`** | ✅ **`rejeu de graphique_1` → `graphique_2`** |
| 5 | *Le premier tableau que tu m'as sorti, redis-moi ce qu'il y avait dedans.* | la relecture d'un tableau, désigné sans son nom | ✅ `lire_un_artefact` → `resultat_1` | ❌ capté par le nœud **système** |
| 6 | *Fais-moi un histogramme des âges des passagers.* | une seconde figure, sous son propre nom | ✅ `graphique_3` | ✅ `graphique_3` |
| **7** | ***Reviens au tout premier graphique, celui par classe, et repasse-le en vert.*** | **le rejeu de la PREMIÈRE figure, 5 tours et 2 figures plus loin** | ✅ **`rejeu de graphique_1` → `graphique_4`** | ✅ **`rejeu de graphique_1` → `graphique_4`** |
| 8 | *Tu peux me remontrer le camembert des ports d'embarquement que tu avais fait ?* | un refus qui dit que cet artefact n'existe pas | ❌ figure neuve | ❌ figure neuve |
| | | | **7/8** | **6/8** |

**Les deux tours qui sont la mesure passent sur les deux moteurs.** Le tour 4 est
celui que le code d'avant ne pouvait pas servir : deux questions sans rapport
séparent la figure de sa reprise, et `last_code` avait été écrasé deux fois. Le tour
7 va plus loin — cinq tours, et une **autre figure** produite entre-temps, donc un
catalogue où deux `graphique_N` se disputent la désignation « le tout premier ». Les
deux moteurs choisissent `graphique_1`.

Le rejeu repasse bien par le bac à sable : le code exécuté est celui de l'artefact,
modifié, et le nouveau code entre au catalogue sous son propre nom — on peut donc
rejouer un rejeu.

Les deux manques sont instruits en [20.6](#206-trois-défauts-que-la-mesure-a-trouvés)
et [20.10](#2010-ce-qui-reste-ouvert-après-le-20). Aucun des deux n'est une invention :
dans les deux cas l'agent produit une réponse vraie, lue dans les données.

### 20.5 Le poids du prompt : ce que le catalogue coûte vraiment

Tokens **rendus par le serveur** (`input_tokens`), pas estimés — l'estimateur local
surestime de 1 à 17 %, ce qui suffit pour couper au bon moment mais pas pour publier
un chiffre.

| Tour | artefacts au magasin | prompt du planificateur (moteur en service) | (ancien moteur) | prompt de l'agent de rappel |
|---|---|---|---|---|
| 1 | 1 | **1 439** | 1 458 | — |
| 2 | 2 | 1 601 | 1 620 | 1 055 |
| 3 | 3 | 1 739 | 1 758 | 1 090 |
| 6 | 5 | 1 822 | 1 841 | 1 172 |
| 8 | 7 | **1 911** | 1 930 | 1 260 |

**+472 tokens pour sept artefacts**, et exactement le même écart sur les deux moteurs.
Le catalogue de ces sept objets pèse 1 489 caractères ; **leur contenu en pèse 6 142 à
8 919** selon le tirage — et l'écart se creuse à chaque figure, parce qu'une ligne de
catalogue est de taille constante quand un fichier de code ne l'est pas. Le prompt du
planificateur reste à 6 % du budget (`DAA_CONTEXT_TOKEN_BUDGET=8000`) et à 6 % de la
fenêtre servie (32 768).

C'est ce que « le système de fichiers comme contexte » veut dire ici : le catalogue
tient parce qu'il ne porte que des noms, et le contenu ne coûte qu'au tour où
quelqu'un le demande.

L'agent de rappel, lui, coûte **un appel LLM de plus par tour** — mais seulement dans
une conversation qui a déjà produit quelque chose. Un fil neuf ne le paie pas : le
nœud se retire avant d'appeler le modèle. C'est ce qui rend la batterie de ce
document — une conversation neuve par question — insensible à ce chantier, et le
[§20.8](#208-pas-de-régression--la-batterie-sur-les-deux-moteurs) le montre au token près.

### 20.6 Trois défauts que la mesure a trouvés

Aucun des trois n'était visible en test unitaire : ils demandent un vrai modèle
devant un vrai catalogue.

**① La sentinelle rendue APRÈS un appel d'outil.** vLLM, tour 5. L'agent a
correctement reconnu « le premier tableau que tu m'as sorti », appelé
`lire_un_artefact('resultat_1')`, reçu `count / 891` — puis répondu `AUTRE`, le mot
réservé au « ce n'est pas pour moi ». L'utilisateur recevait le mot **`AUTRE`** en
guise de réponse. Pire qu'un refus : ce n'en est même pas un. Un tour où un outil a
répondu n'est plus « pas pour moi », et la contradiction se tranche du côté de ce qui
a été lu.

**② La formulation qui ne porte RIEN de ce que l'outil a rendu.** Même tour, tirage
suivant. L'outil avait fait son travail — `count / 891` — et le modèle a répondu
« Je suis désolé, mais je ne peux pas répondre à cette demande ». Il tenait la
réponse et l'a rendue vide. La garde est la transposition de
[`defaut_de_fondation`](#10-ce-qui-a-remplacé-le-lexique--un-outil-et-le-modèle-qui-décide) :
la formulation doit porter au moins **un** jeton de ce que l'outil a rendu — un nom de
colonne, une valeur, le nom de l'artefact. Volontairement généreux : on distingue
« formulé autrement » de « n'a rien formulé du tout », on ne note pas un style. Avec
la garde, l'utilisateur reçoit `resultat_1 (tableau de 1 ligne(s) ; colonnes : count)
: count / 891`. Moins joli, vrai et complet.

**③ Le catalogue muet sur l'éviction, et le rejeu du MAUVAIS artefact.** Le plus
grave des trois, et le seul qu'on ne pouvait voir qu'en resserrant la fenêtre. Avec
`DAA_CONTEXT_CODE_WINDOW=1`, le catalogue de l'agent de rappel ne portait au tour 7
que `graphique_3` — l'histogramme des âges, le plus récent. Il a reçu « reviens au
**tout premier** graphique, celui par classe, et repasse-le en vert », et il a rejoué
celui qu'il voyait :

> `rejeu de graphique_3 — 1 essai(s), 1 figure(s), statut ok`
>
> « Le graphique initial par classe a été recréé en utilisant la couleur verte. Les
> statistiques descriptives de l'âge montrent une moyenne de 29.699118… »

Un histogramme des âges repeint en vert, présenté comme le graphique par classe. **Ça
ressemblait à un rappel et ce n'en était pas un** — exactement ce que le refus existe
pour empêcher. Le catalogue montrait ce qui restait sans dire ce qui était sorti, donc
le modèle croyait tout voir.

Le remède est celui que le planificateur avait déjà (`planner_notice`) : dire
l'éviction. Le catalogue de l'agent de rappel porte maintenant, quand il y a lieu :

> ATTENTION : N objet(s) plus ancien(s) de cette conversation ont été ÉVINCÉS du
> contexte (…) et ne figurent PAS au catalogue ci-dessus. […] tu ne peux ni le relire
> ni le rejouer : DIS-LE, et ne rejoue surtout pas un autre artefact à sa place.

Mesuré après, même parcours, même fenêtre : le rejeu erroné **a disparu**. L'agent
décline, le planificateur refait une figure par classe — qui est correcte — et la
réponse porte l'avis de troncature existant (« Contexte tronqué : 2 des 5 tableaux
intermédiaires… »). Ce qui manque encore est que l'agent **dise** l'éviction lui-même
plutôt que de se retirer ; ce qui est acquis est qu'il ne raconte plus le mauvais
artefact.

### 20.7 Le refus, et le cloisonnement

Le refus distingue **deux cas**, et les confondre reviendrait à dire à quelqu'un
qu'il n'a jamais demandé ce graphique :

- *Aucun artefact ne s'appelle « graphique_9 » dans cette conversation. Artefacts
  disponibles : …*
- *L'artefact « graphique_1 » a bien été produit dans cette conversation, mais il a
  été ÉVINCÉ du contexte de ce tour (fenêtre DAA_CONTEXT_CODE_WINDOW=1) : je ne peux
  ni le relire ni le rejouer. Artefacts encore disponibles : …*

Les deux énumèrent ce qui reste — un refus qui ne dit pas ce qu'on peut demander à la
place oblige à deviner une deuxième fois. Et **c'est le refus qui part à
l'utilisateur, pas la phrase du modèle** : quand tous les outils d'un tour ont refusé,
il n'y a rien à formuler. C'est la reprise de la famille `acfd8f5` par une porte de
plus.

Côté HTTP, le cloisonnement du [§5 de l'audit](AUDIT-2026-09.md) tient sans ajout :
le magasin d'artefacts est ouvert **sous la racine de l'appelant**, donc l'artefact
d'un autre compte n'existe pas de là où on regarde. Les deux routes
(`GET /conversations/{id}/artefacts` et `…/artefacts/{nom}`) rendent le **même 404**
dans les trois cas qui doivent être indiscernables — le fil d'un autre, un fil
inventé, un nom inconnu chez soi. Un message par cas dirait à un inconnu lequel des
trois il vient de toucher.

### 20.8 Pas de régression : la batterie sur les deux moteurs

| Moteur | Questions méta | Témoins | Coût |
|---|---|---|---|
| vLLM (`google/gemma-4-E4B-it-qat-w4a16-ct`, port 8100) | **36/36** | **4/4** | 78 + 17 appels LLM |
| ancien moteur | **35/36** | **4/4** | 83 + 17 appels LLM |

**Identiques au [§19.10](#1910-la-batterie-rejouée-sur-les-deux-moteurs), appels
compris.** Le seul écart reste `sources-premiere-personne` sous l'ancien moteur, et c'est le
même depuis le [§18.7](#187-pas-de-régression--la-batterie-complète-sur-les-deux-moteurs) :
`systeme_request_limit = 4`, le *fail-open* joue son rôle. Le témoin
`temoin-colonnes-a-trous` passe sur les deux moteurs, avec l'oracle durci au §18 —
celui qui vérifie aussi les colonnes **interdites**. Il n'a pas été desserré.

Cette identité n'est pas un heureux hasard : la batterie pose chaque question dans une
conversation **neuve**, donc sans artefact, donc le nœud de rappel s'y retire avant
d'appeler le modèle. Le prompt et le nombre d'appels y sont, littéralement, ceux
d'avant.

Suite complète : **922 passés, 99,54 %** (893 et 99,50 % avant le chantier), dont 26 sur le seul rappel d'artefact.

### 20.9 Une ligne de prompt qui en coûtait deux

Le tour 5 raté sous l'ancien moteur a une cause identifiée : le nœud **système** s'empare de
« Le premier tableau que tu m'as sorti, redis-moi ce qu'il y avait dedans » et répond
par l'inventaire du catalogue. C'est le défaut du [§12](#12-la-source-de-travail-dune-conversation-mesurée-de-bout-en-bout)
par une autre porte, entre deux agents cette fois.

La correction évidente était d'ajouter la frontière au prompt système : « ne te
concerne pas une demande qui porte sur ce que CETTE CONVERSATION a déjà produit ».
Cinq lignes. Mesurée, elle **coûtait deux questions de la batterie, sous vLLM** :

| Question | Sans la ligne | Avec la ligne |
|---|---|---|
| `periode-indirecte` — « De quand datent les données que tu as ? » | `query` : *les âges vont de 0.42 à 80.0 ans* — **correct** | réponse système vague : *les données que j'ai datent de la période couverte par les sources titanic et iris* — **à côté** |
| `capacites-demander-quoi` — « je peux te demander quoi ? » | l'outil de capacités appelé, l'analyse citée — **correct** | liste amputée de l'analyse — **à côté** |

34/36 avec, 36/36 sans, reproduit dans les deux sens. **La ligne a été retirée.**
L'oracle durci au §18 ne se desserre pas, et il y a un enseignement plus général :
*un paragraphe de plus dans un prompt qui tient n'est pas gratuit*. Il déplace
l'attention du modèle, y compris là où on ne regardait pas — et on ne l'aurait pas su
sans rejouer la batterie entière pour une frontière de cinq lignes.

La frontière reste donc portée par le seul agent de rappel, et ce qu'on y perd est
mesuré : 1 tour sur 8, sous l'ancien moteur seulement.

### 20.10 Ce qui reste ouvert après le §20

- **la demande au passé d'un artefact qui n'existe pas** (tour 8, les deux moteurs).
  « Remontre-moi le camembert des ports que tu avais fait » : le modèle décline —
  rien au catalogue n'y ressemble — et le planificateur fabrique un camembert neuf,
  correct. **Rien de faux n'est affirmé** et aucun artefact n'est inventé ; ce qui
  manque est le « je ne l'avais pas fait » avant la figure. Trois formulations de
  prompt ont été essayées, dont une qui demande explicitement d'appeler l'outil avec
  le nom le plus proche : le modèle ne s'en saisit pas quand le catalogue ne porte
  rien de la même famille. À reprendre autrement — peut-être en déterministe, du côté
  du nœud, plutôt que par le prompt ;
- **le nœud système capte encore « le tableau que tu m'as sorti » sous l'ancien moteur**
  (§20.9). La correction par le prompt est disqualifiée, mesures à l'appui ; la
  frontière reste à poser ailleurs ;
- **l'éviction n'est pas DITE quand le modèle décline.** L'avis dans le catalogue a
  supprimé le rejeu erroné (§20.6 ③), mais l'agent se retire au lieu d'annoncer
  « celui-là est sorti du contexte ». L'utilisateur reçoit l'avis de troncature
  générique, qui ne nomme pas ce qu'il demandait ;
- **le rejeu ne vérifie pas que le décor est le même.** La source de l'artefact est
  retenue et remontée ; si elle a disparu du catalogue, on remonte ce qu'on a et le
  code échoue dans le bac à sable avec un message. C'est un choix — mieux vaut un
  échec lisible qu'un refus prématuré — mais rien ne compare le schéma d'alors à
  celui de maintenant ;
- **`sources-premiere-personne` sous l'ancien moteur**, inchangé depuis le §18.7.

## 21. Dire qu'on ne l'a pas fait

Le [§20.10](#2010-ce-qui-reste-ouvert-après-le-20) laissait un trou et le nommait :
la demande **au passé** d'un artefact qui n'existe pas. C'est la dernière poche de
la famille `acfd8f5` — *répondre avec ce qu'on peut produire, au lieu de dire ce qui
manque* — et c'est la plus discrète, parce qu'elle ne produit **rien de faux**.

### 21.1 Le défaut, reproduit sur les deux moteurs

Deux tours, une conversation, contre le vrai système. Le premier produit un
diagramme en barres ; le second demande un camembert qui n'a jamais existé.

> **Tour 1.** *Fais-moi un graphique en barres du nombre de passagers par classe.*
>
> **Tour 2.** *Reprends le camembert des ports d'embarquement que tu m'avais fait.*

| | moteur en service | ancien moteur |
|---|---|---|
| trace du tour 2 | `rappel : aucun outil appelé — passe au planificateur`<br>`plan : analyze sur titanic`<br>`analysis : 1 figure(s), statut ok` | identique |
| réponse | *« Voici le camembert des ports d'embarquement. Les données montrent que le port de S a accueilli 644 passagers, suivi de C avec 168, puis Q avec 77… »* | *« Voici le camembert des ports d'embarquement **que vous avez demandé**. La visualisation est ci-jointe… »* |

Les chiffres sont justes, la figure est correcte, aucun nom d'artefact n'est inventé.
Et pour s'assurer qu'il ne s'agit pas d'un hasard de formulation, le tour 3 pose la
**même demande sans rien désigner** — *« Fais-moi un camembert des ports
d'embarquement »* — et rend, sous vLLM, une réponse **mot pour mot identique** à
celle du tour 2. C'est exactement le problème : rien, dans ce que lit l'utilisateur,
ne distingue une figure retrouvée d'une figure refaite. Il repart en croyant qu'on a
récupéré son travail.

Le nœud de rappel n'a pas tort de décliner : rien au catalogue ne ressemble à un
camembert des ports. Il n'a simplement **aucun outil qui refuse**, donc aucun refus
à servir — les deux refus du [§20.7](#207-le-refus-et-le-cloisonnement) supposent
qu'un NOM ait été prononcé.

### 21.2 Le signal qui sépare les deux demandes

Ces deux messages n'appellent pas la même réponse, et le catalogue ne peut pas les
distinguer — il dit ce qui **existe**, pas ce que la phrase **prétend** :

| message | ce qu'il présuppose | ce qu'il faut faire |
|---|---|---|
| « Reprends le camembert des ports **que tu m'avais fait** » | que la chose existe **et que c'est moi qui l'ai faite** | le dire avant de produire autre chose |
| « **Fais-moi** un camembert des ports » | rien | la produire, sans commentaire |

Le signal est donc dans le **message**, et il est **grammatical** : une
présupposition d'antériorité attribuée à l'agent. Il se compose de deux moitiés, et
`designation_dun_artefact_passe` exige **les deux** :

1. un **marqueur d'antériorité**, sous l'une de trois formes :
   - le **passé attribué à l'agent** — `tu` + un clitique optionnel + un auxiliaire +
     un **participe de production** : « tu m'avais **fait** », « que tu as **tracée** ».
     L'auxiliaire seul ne suffit pas, et c'est le point qui décide de tout : « de
     quand datent les données que tu as ? » porte `tu as` et ne désigne aucun
     artefact ;
   - un **déictique du passé** — « de tout à l'heure », « d'avant », « le précédent »,
     « tantôt », « plus tôt », « la dernière fois », « le même » ;
   - un **verbe de reprise** — « reprends », « remontre », « reviens au », « repasse ».
     `refais` en est exclu : « refais-moi un camembert » se lit aussi bien comme une
     demande neuve, et l'ambiguïté se règle du côté du silence ;
2. une **cible** — un objet produisible (graphique, camembert, tableau, courbe…), un
   anaphorique qui en tient lieu (« celui », « ce que »), ou un nom d'artefact.
   Sans cible, un marqueur de passé ne désigne pas un artefact : « tu m'as dit que »
   parle d'une phrase.

**Ce n'est pas le lexique disqualifié au [§9](#9-le-lexique-avait-un-plafond-et-il-a-été-mesuré).**
Celui-là décidait de la **route** — quel agent traite la demande — et plafonnait à 3
formulations sur 10 parce qu'il fallait reconnaître *ce qu'on veut*. Ici on ne
reconnaît qu'une **construction**, et elle décide seulement d'**ajouter une phrase**
à une réponse qui part de toute façon. Se tromper ne fait perdre ni une réponse ni un
tour : au pire une phrase de trop, au pire une phrase de moins.

### 21.3 Ce qui a été bâti : déterministe, côté nœud

Le §20.10 disait « à reprendre autrement — peut-être en déterministe, du côté du
nœud, plutôt que par le prompt ». C'est ce qui a été fait, et **le prompt n'a pas été
touché** : trois reformulations avaient déjà échoué, et la raison en est structurelle
— on demandait à un modèle d'**affirmer une absence**, et un modèle n'affirme pas une
absence : il produit ce qu'il peut. Le catalogue, lui, SAIT ce qui existe.

Le nœud de rappel avait un seul cas « aucun outil appelé » ; il en recouvrait deux.
Il en a maintenant deux :

```
rappel : aucun outil appelé — passe au planificateur
rappel : aucun outil appelé, désignation sans artefact (« tu m avais fait »)
         — absence dite, puis passe au planificateur
```

La phrase est mise **en tête de la réponse** par la synthèse, comme l'avis de source
du [§12](#12-la-source-de-travail-dune-conversation-mesurée-de-bout-en-bout) et pour
la même raison : la trace n'est pas dépliée par défaut, et ce qu'on veut éviter n'est
pas de produire une figure — c'est de la faire passer pour un rappel.

**On ne bloque personne.** La figure est produite, elle est juste, elle est servie.
Ce qui change tient en une phrase, et c'est elle qui porte tout le mécanisme :

> **Ce qui suit est neuf, pas un rappel.**

Le tour 2 du §21.1 se lit maintenant, sur les deux moteurs :

> Je n'ai pas produit dans cette conversation ce que tu me demandes de reprendre.
> **Ce qui suit est neuf, pas un rappel.** Ce que j'ai produit ici : `graphique_1`.
>
> Voici le camembert des ports d'embarquement. Les données montrent que le port de S
> a accueilli 644 passagers…

Trois cas, trois phrases, et les confondre serait mentir :

| le fil… | ce qui est dit |
|---|---|
| n'a **rien** produit | *Je n'ai encore rien produit dans cette conversation : il n'y a rien à reprendre.* |
| a produit, mais pas ça | *Je n'ai pas produit dans cette conversation ce que tu me demandes de reprendre. […] Ce que j'ai produit ici : …* |
| a des artefacts **ÉVINCÉS** | *Je ne peux pas affirmer ne l'avoir jamais produit : N objet(s) […] ont été ÉVINCÉS du contexte de ce tour (…)* |

Le troisième est le piège, et c'est le même que celui du
[§20.7](#207-le-refus-et-le-cloisonnement) par une autre porte : on ne **peut pas**
affirmer l'absence de ce qu'on ne voit plus. Le dire absent reviendrait à dire à
quelqu'un qu'il n'a jamais demandé ce graphique.

Un fil **vide** est le seul cas où l'aveu ne coûte **aucun appel** : le nœud se retire
avant de solliciter le modèle, puisqu'il n'a pas de catalogue à lui soumettre — et
l'absence y est pourtant la plus certaine de toutes.

### 21.4 Le signal mesuré : rappel et faux positifs

Le détecteur est déterministe : sa précision se **vérifie**, elle ne s'échantillonne
pas. `scripts/mesure_artefact_absent.py` le passe sur un corpus étiqueté, sans le
moindre appel au modèle.

**12/12 désignations reconnues, 0/18 faux positifs.**

| tournure | marqueur trouvé |
|---|---|
| Reprends le camembert des ports d'embarquement **que tu m'avais fait**. | `tu m avais fait` |
| Tu peux me remontrer le camembert **que tu avais fait** ? | `tu avais fait` |
| Reprends le graphe **de tout à l'heure** et mets les barres en bleu. | `tout a l heure` |
| **Reviens** au tout premier graphique, celui par classe, et repasse-le en vert. | `reviens` |
| Le premier tableau **que tu m'as sorti**, redis-moi ce qu'il y avait dedans. | `tu m as sorti` |
| Le graphique **de tantôt**, en vert. | `tantot` |
| Refais **le même** graphique mais en bleu. | `le meme` |
| Je voudrais revoir la courbe **que tu as tracée** plus tôt. | `tu as tracee` |
| Le nuage de points âge/tarif **que tu m'avais sorti**, remets-le-moi. | `tu m avais sorti` |
| **Remontre**-moi **celui d'avant**. | `d avant` |
| Affiche **le précédent**. | `precedent` |
| **Ce que tu m'avais sorti**, tu peux me le remettre ? | `tu m avais sorti` |

**Les trois dernières ne portent aucun mot du catalogue** — pas de « graphique », pas
de « camembert », rien qu'un anaphorique. C'est là qu'un lexique d'objets n'aurait
rien eu à quoi s'accrocher, et c'est pour elles que le signal a dû être grammatical.

Le défaut **symétrique** pèse autant : une tournure innocente prise pour une
désignation ferait payer une phrase de repentir à une demande neuve. Les 18
innocentes du corpus passent, et les six premières sont des questions **réelles** de
la batterie de ce document — celles qui portent `tu as`, c'est-à-dire le piège :

| tournure innocente | pourquoi elle ne déclenche pas |
|---|---|
| De quand datent les données **que tu as** ? | `tu as` sans participe de production |
| **qu'est-ce que tu as** comme données ? | idem — et `données` a dû être retiré des participes, c'est le faux positif le plus coûteux qu'on ait trouvé |
| À quelles bases de données **as-tu** accès ? | l'inversion n'est pas `tu as` |
| **montre-moi ce que tu as** | cible présente, marqueur absent |
| Fais-moi un camembert des ports d'embarquement. | aucun marqueur |
| Ajoute une légende au graphique. | aucun marqueur |

### 21.5 Le parcours, sur les deux moteurs

Neuf tours contre le vrai système — serveur LLM en place, `titanic` en Postgres, bac
à sable Docker. Les six premiers dans **une** conversation ; les trois derniers sont
des anaphores pures posées chacune dans un fil **neuf**, le seul décor où « celui
d'avant » ne peut désigner que du vide, donc le seul où l'oracle soit mécanique.

L'oracle ne lit qu'un fait : la phrase d'aveu est-elle là où elle doit être, et
absente là où elle n'a rien à faire ?

| # | tour | aveu | moteur en service | ancien moteur |
|---|---|---|---|---|
| 1 | *Fais-moi un graphique en barres du nombre de passagers par classe.* | interdit | ✅ | ✅ |
| **2** | ***Reprends le camembert des ports d'embarquement que tu m'avais fait.*** | **exigé** | ✅ **+ figure produite** | ✅ **+ figure produite** |
| 3 | *Fais-moi un histogramme des âges des passagers.* | interdit | ✅ | ✅ |
| **4** | ***Reprends le graphe de tout à l'heure, celui par classe, et mets les barres en bleu.*** | **interdit** | ✅ **`rejeu de graphique_1`** | ✅ **`rejeu de graphique_1`** |
| **5** | ***Le nuage de points âge/tarif que tu m'avais sorti, remets-le-moi.*** | **exigé** | ✅ | ✅ |
| 6 | *Combien de passagers ont survécu ?* | interdit | ✅ | ✅ |
| **7** | ***Remontre-moi celui d'avant.*** (fil neuf) | **exigé** | ✅ **0 appel de rappel** | ✅ |
| **8** | ***Affiche le précédent.*** (fil neuf) | **exigé** | ✅ | ✅ |
| **9** | ***Ce que tu m'avais sorti, tu peux me le remettre ?*** (fil neuf) | **exigé** | ✅ | ✅ |
| | | | **9/9** | **9/9** |

**Le tour 4 est celui qui prouve que le mécanisme n'est pas un bavardage.** Il DÉSIGNE
autant que le tour 2 — « reprends le graphe de tout à l'heure » — mais l'artefact
existe, le rappel aboutit, et **pas un mot** n'est ajouté. L'aveu ne se déclenche que
sur une désignation restée **sans réponse**.

Les tours 3 et 6 sont les témoins du défaut symétrique : une figure neuve et une
requête ordinaire, aucune ne reçoit de commentaire.

### 21.6 Le parcours du §20 rejoué : rien de perdu, un tour gagné

Même parcours qu'au [§20.4](#204-la-mesure-sur-les-deux-moteurs), mêmes huit tours,
même oracle mécanique — seul le tour 8 a changé d'attendu, puisque ce qu'on y exige
n'est plus un refus mais l'absence **dite**.

| # | Tour | moteur en service | ancien moteur |
|---|---|---|---|
| 1 | une figure | ✅ | ✅ |
| 2–3 | deux digressions | ✅ | ✅ |
| **4** | **reprise au tour +2** | ✅ **`rejeu de graphique_1` → `graphique_2`** | ✅ |
| 5 | un tableau désigné sans son nom | ✅ | ✅ |
| 6 | une seconde figure | ✅ | ✅ |
| **7** | **reprise au tour +5, deux figures plus loin** | ✅ **`rejeu de graphique_1` → `graphique_4`** | ✅ |
| **8** | **l'artefact absent** | ✅ **l'absence dite** *(était ❌)* | ✅ **l'absence dite** *(était ❌)* |
| | | **8/8** *(était 7/8)* | **7/8** *(était 6/8)* |

**Le poids du prompt est inchangé**, au token près : 1 439 au premier tour (magasin
vide), 1 911 au dernier (sept artefacts), soit les **+472** du
[§20.5](#205-le-poids-du-prompt--ce-que-le-catalogue-coûte-vraiment). L'aveu ne coûte
rien parce qu'il n'entre dans aucun prompt : il est calculé après coup, à partir du
catalogue qu'on avait déjà.

### 21.7 L'artefact ÉVINCÉ, qui n'est pas l'artefact absent

Le même parcours, fenêtre de code resserrée à un (`DAA_CONTEXT_CODE_WINDOW=1`). Au
tour 7, la première figure a été **évincée** du contexte : elle a bel et bien été
produite, elle n'est simplement plus là.

Le [§20.6 ③](#206-trois-défauts-que-la-mesure-a-trouvés) avait supprimé le rejeu du
mauvais artefact en disant l'éviction **au modèle** ; il restait que l'agent se
**retire** au lieu de l'annoncer, et que l'utilisateur ne reçoive qu'un avis de
troncature générique qui ne nomme pas ce qu'il demandait. C'est précisément ce que
l'aveu comble, avec sa troisième phrase — celle qui ne dit **pas** l'absence :

> Je ne peux pas affirmer ne l'avoir jamais produit : 1 objet(s) plus ancien(s) de
> cette conversation ont été ÉVINCÉS du contexte de ce tour (…), et je ne peux ni les
> relire ni les rejouer. Ce qui suit est neuf, pas un rappel. Artefacts encore
> disponibles : …

### 21.8 Pas de régression : la batterie sur les deux moteurs

| Moteur | Questions méta | Témoins | Coût |
|---|---|---|---|
| vLLM (`google/gemma-4-E4B-it-qat-w4a16-ct`, port 8100) | **36/36** | **4/4** | 78 + 17 appels LLM |
| ancien moteur | **35/36** | **4/4** | 83 + 17 appels LLM |

**Identiques au [§20.8](#208-pas-de-régression--la-batterie-sur-les-deux-moteurs),
appels compris.** C'est la preuve qui compte ici, parce que le [§20.9](#209-une-ligne-de-prompt-qui-en-coûtait-deux)
a montré ce que coûte un paragraphe ajouté à un prompt qui tient : cinq lignes de
frontière valaient **deux questions** de cette batterie, sous vLLM, reproduit dans les
deux sens. Ce chantier-ci n'a touché **aucun prompt** — le mécanisme est entièrement
en aval du modèle — et le compte d'appels identique au dernier près est ce qui le
prouve.

Deux raisons pour que cette identité tienne, et il fallait les deux :

- la batterie pose chaque question dans une conversation **neuve**, donc sans
  artefact ;
- et surtout, **aucune des 40 questions ne déclenche le détecteur** (§21.4). Ce n'est
  pas acquis d'avance : six d'entre elles portent `tu as`, et c'est bien pour elles
  que le participe de production a été exigé. Le mot `rappel` n'apparaît dans **aucune**
  trace de la batterie.

Le seul écart reste `sources-premiere-personne` sous l'ancien moteur, et c'est le même depuis
le [§18.7](#187-pas-de-régression--la-batterie-complète-sur-les-deux-moteurs) :
`systeme_request_limit = 4`, le *fail-open* joue son rôle. Il n'est pas traité ici.

Suite complète : **957 passés, 99,54 %** (922 et 99,54 % avant ce chantier). Les 35
nouveaux sont tous sur le rappel d'artefact : 29 sont le corpus étiqueté du §21.4,
posé en test et non seulement en runner — un signal déterministe se verrouille.

### 21.9 Ce qui reste ouvert après le §21

Le §20.10 laissait cinq points ouverts. Deux sont fermés ici :

- ~~la demande au passé d'un artefact qui n'existe pas~~ — c'est l'objet de ce § ;
- ~~l'éviction n'est pas DITE quand le modèle décline~~ — c'est le §21.7 : elle l'est
  maintenant, et par la même voie.

Restent :

- **le nœud système capte encore « le tableau que tu m'as sorti » sous l'ancien moteur**
  (§20.9), et c'est le seul tour manqué du parcours du §20 sur ce moteur. La
  correction par le prompt est disqualifiée, mesures à l'appui ; la frontière reste à
  poser ailleurs. Il faut noter qu'elle passerait par un détecteur du même genre que
  celui-ci, appliqué au nœud d'avant — mais cette fois pour **router**, et c'est
  exactement ce que le §9 a disqualifié. À instruire pour lui-même ;
- **l'aveu dans un fil vide se colle parfois à un repli.** Mesuré : « Remontre-moi
  celui d'avant » dans une conversation neuve rend *« Je n'ai encore rien produit
  dans cette conversation : il n'y a rien à reprendre. Ce qui suit est neuf, pas un
  rappel. Je n'ai pas bien compris ta demande. »* La première phrase est juste et
  utile, la seconde annonce une suite qui n'en est pas une. Défaut de couture, pas de
  fond ;
- **la désignation exige une cible**, donc « ajoute une légende au graphique » dans un
  fil qui n'en porte aucun ne dit rien. C'est délibéré — sans marqueur d'antériorité,
  la phrase ne prétend pas que la chose existe — mais c'est une frontière posée là et
  non ailleurs, et elle n'a pas été mesurée sur un corpus de tournures implicites ;
- **le rejeu ne vérifie pas que le décor est le même** (§20.10), inchangé ;
- **`sources-premiere-personne` sous l'ancien moteur**, inchangé depuis le §18.7.

## 22. Un critère de routage, et la phrase de renfort qui a coûté une question

Le chantier du routage des questions de SENS
(`docs/sources-de-demonstration.md`) a touché le prompt de l'agent système —
ce que le [§20.9](#209-une-ligne-de-prompt-qui-en-coûtait-deux) et le
[§21.8](#218-pas-de-régression--la-batterie-sur-les-deux-moteurs) désignent
comme l'endroit le plus cher du dépôt. Il a donc coûté, et le prix est ici.

### 22.1 Ce qui a été ajouté, et pourquoi

Une famille entière de questions — « que veut dire cette colonne ? », sous
toutes ses formes — ne parvenait jamais à l'agent système : sondé sur huit
formulations, il répondait `AUTRE` **8 fois sur 8**. Le prompt énonçait pourtant
la bonne propriété. Il la démentait ensuite, par une liste de noms où figurait
« les valeurs manquantes », qui attrapait toute question citant une valeur.

La liste est désormais subordonnée à un **test**, et ce test n'énumère aucune
tournure :

> Ta réponse CHANGERAIT-elle si on remplaçait toutes les lignes de la source par
> d'autres, sans toucher ni à sa structure ni à sa documentation ? Si oui, c'est
> un calcul, et ce n'est pas pour toi.
>
> « Combien de relevés valent -1 ? » se compte. « Qu'est-ce que -1 veut dire
> ici ? » se lit.

### 22.2 La phrase de renfort, et ce qu'elle a cassé

Le critère était accompagné d'une phrase qui l'appliquait à la liste
existante : *« Chacun passe le test ci-dessus : sa réponse change avec les
lignes. »* Elle est fausse pour l'un des éléments de cette liste.

« De quand datent les données que tu as ? », sur `titanic` et `iris` qui ne
portent **aucune** colonne de date, a une réponse qui ne change pas avec les
lignes : il n'y en a pas. Le renfort a fait produire une phrase creuse, et
fausse par omission :

> « Les données que j'ai datent de la période couverte par les sources
> `titanic` et `iris`, comme indiqué dans le catalogue des sources. »

| campagne | questions méta | témoins | la question qui tombe |
|---|---|---|---|
| avec le renfort, 1ʳᵉ | 35/36 | 4/4 | `periode-indirecte` |
| avec le renfort, 2ᵉ | 35/36 | 4/4 | `periode-indirecte`, **réponse identique au mot près** |

**Deux campagnes, le même échec, la même phrase.** C'est ce que la dette `F` a
appris, et il faut le dire dans l'autre sens que la première fois : elle avait
appris à ne pas prendre une régression pour une dérive du modèle ; elle sert ici
à ne pas prendre une régression pour du bruit qu'on aurait pu ignorer. Une
campagne unique aurait laissé le doute.

La phrase est retirée. Le critère reste — c'est lui qui fait basculer les
questions de sens — mais il n'affirme plus, sur des exemples écrits avant lui,
une chose qui n'est vraie que la plupart du temps. **Un critère qui se
généralise à des cas qu'on n'a pas vérifiés cesse d'être un critère.**

## 23. Un message qui ne se suffit pas à lui-même

« Oui ». « Vas-y ». « Et dedans ? ». « Tu ne m'as pas répondu ». Ces
messages-là ne portent pas leur sujet : ils le prennent au tour d'avant. Et
c'est l'agent lui-même qui pose le piège — il termine par une question fermée,
l'utilisateur l'accepte, et il répond « je n'ai pas bien compris ta demande »
suivi de son menu.

Le mécanisme existait déjà et ne suffisait pas : `run_systeme` recevait
`echange_precedent`, et `prompts/systeme.txt` portait une clause « UN MESSAGE
QUI NE SE SUFFIT PAS À LUI-MÊME ». Ce §23 dit pourquoi cela ne suffisait pas,
et la réponse n'est pas celle qu'on aurait devinée.

### 23.1 La cause, montrée par le fil brut — et ce n'était pas la compréhension

Le tour d'avant était **recopié en tête du message de l'utilisateur**, sous une
étiquette explicite. Voici ce que le modèle en faisait, amorce « Quelles
sources de données as-tu ? », continuation « Oui » :

```
ENVOI  [SystemPromptPart] <4795 caractères>
ENVOI  [UserPromptPart]   "TOUR PRÉCÉDENT, pour comprendre ce qui suit — n'y réponds pas.
                           L'utilisateur avait dit : Quelles sources de données as-tu ?
                           Tu avais répondu : J'ai accès aux sources […] Je peux vous donner
                           les détails sur `referentiel` et `facturation`. Souhaitez-vous
                           que je fasse cela ?
                           MESSAGE À TRAITER MAINTENANT : Oui"
RETOUR [TextPart]         "travailler_sur_une_source(source='referentiel')"
```

**Le modèle avait compris.** Il nomme le bon outil et le bon argument. Mais il
l'ÉCRIT au lieu de l'ÉMETTRE : aucun `ToolCallPart`, donc `outils_appeles`
vide, donc « ce n'est pas une question sur le système », donc le tour repart au
planificateur, qui rend le repli. Le signal de routage est l'appel d'outil
([§ plus haut](#9-le-prix-de-lagent-système)), et un appel d'outil écrit en
prose n'en est pas un.

Une seconde forme du même défaut, sur « et dedans ? » après la description de
`telemetrie` : le modèle recopie en texte la réponse du tour d'avant, sans
appeler un seul outil. Il répond juste, et le socle refuse de le servir — à
raison, parce qu'une réponse de mémoire n'est fondée sur rien de regardé.

Ce n'est donc **ni le sens qui manquait, ni la place du rappel**. C'est la
FORME : un bloc de prose qui RACONTE un dialogue ne met pas le modèle dans la
position de continuer un dialogue.

### 23.2 Trois formes, mesurées côte à côte

Trois façons de donner le même tour d'avant, cinq continuations, trois tirages
chacune, sur vLLM. Le score est « un outil a-t-il été appelé » — le seul
signal dont le socle se sert :

| continuation | rappel en TÊTE (l'existant) | rappel en QUEUE | tour d'avant en VRAIS messages |
|---|---|---|---|
| « Oui, je souhaite que tu fasses ça » | 3/3 | 0/3 | 3/3 |
| « Oui » | 0/3 | 0/3 | **3/3** |
| « vas-y » | 0/3 | 0/3 | **3/3** |
| « et dedans ? » | 0/3 | 0/3 | 0/3 |
| « tu ne m'as pas répondu » | 0/3 | 0/3 | 0/3 |

La place du rappel ne décide de rien : en queue, c'est pire. Les **vrais
messages** — `message_history` de `pydantic-ai`, une question et sa réponse —
font émettre de vrais appels d'outil. C'était la piste la moins essayée ;
c'est la seule qui paie.

### 23.3 Le prompt système survit à l'historique, et c'est une ligne

La première version de cette mesure a failli conclure trop vite : l'historique
seul donnait 9/9 là où le prompt système en place donnait 6/9. Le fil brut a
dit pourquoi, et c'est un piège de bibliothèque :

> `UserPromptNode.run` : `if not messages: parts.extend(await self._sys_parts(...))`

`pydantic-ai` n'émet un `system_prompt` que lorsque l'historique est **vide**.
Passer un historique, c'est donc faire tourner l'agent système **sans son
prompt** — sans la règle qui lui interdit d'inventer un nom de source, sans le
test qui sépare ce qui se lit de ce qui se calcule. Le « gain » de 9/9 était
celui d'un agent désarmé.

Le prompt part donc en `instructions`, que `pydantic-ai` réémet à chaque
requête quel que soit l'historique. Un test l'ancre — il fait tourner l'agent
AVEC un historique et vérifie que le prompt est bien dans ce qui part.

La doublure de test suit : elle routait ses réponses sur le `SystemPromptPart`
seul, et lit maintenant les deux formes. Sans cette ligne, ce n'est pas un test
qui tombe, c'est chacun de ceux qui traversent ce nœud.

### 23.4 La clause de continuation : la même idée, au dernier rang

Le prompt portait déjà la clause, au milieu, et le modèle répondait quand même
`AUTRE`. Elle a été **déplacée en fin de prompt** — après le bloc qui ordonne
de répondre `AUTRE` — et reformulée sur ce que le fil brut a montré :
« APPELLE l'outil que tu proposais. Ne l'écris pas, ne le décris pas ».
Mesurée, à historique constant : **6/15 → 9/15**, sans rien perdre sur les
témoins. C'est le dernier lu qui pèse.

Un second paragraphe a été ajouté après mesure d'une régression (§23.6) : une
absence se constate en regardant, jamais en se souvenant.

### 23.5 La batterie, et ce qu'elle a appris sur elle-même

`scripts/mesure_continuation.py` : douze cas, trois situations de tour
précédent, trois tirages — trente-six tours de continuation, chacun précédé de
son tour d'amorce. Elle a été corrigée deux fois, et les deux corrections
valent d'être dites.

**Un tour d'amorce joué ne pose pas de question fermée.** La première campagne
attendait « souhaitez-vous que… ? » et recevait « Quelle source souhaitez-vous
explorer ? » — une question OUVERTE, à laquelle « oui » ne répond à rien. La
situation (a) ne mesurait pas ce qu'elle annonçait. Ses quatre réponses
d'amorce sont désormais **figées**, sur le patron de celle que l'agent a
réellement écrite le jour du défaut. Une question fermée ne se commande pas.

**Un nom attendu trouvé dans un déballage ne prouve rien.** L'oracle
demandait qu'au moins un nom attendu figure dans la réponse. Or à « oui » sur
une proposition portant sur `referentiel` et `facturation`, l'agent d'avant
déballait l'inventaire des CINQ sources : le tour est perdu, et la réponse
contient les deux noms. L'oracle porte maintenant des **interdits** — le
complément des attendus, tiré de la même vérité terrain : ce que le tour
d'avant EXCLUAIT. Deux cas ont basculé de « tenu » à « à côté », et ils
avaient raison de basculer.

C'est la leçon de méthode de ce chantier : **une mesure se corrige avec les
mêmes exigences que le produit.** Les deux corrections abaissent le score
d'avant ET celui d'après ; elles ne servent aucun camp.

### 23.6 Avant et après, trente-six tours

Même batterie, même oracle, même moteur — vLLM seul, sur le catalogue de
démonstration. Trois tirages par cas.

| | avant | après |
|---|---|---|
| **(a) l'agent a posé une question fermée** | 6/12 | **12/12** |
| **(b) l'agent a répondu, l'utilisateur relance** | 9/12 | 9/12 |
| **(c) l'utilisateur conteste** | 3/12 | 3/12 |
| **total** | **18/36** | **24/36** |

Le détail, cas par cas :

| cas | continuation | avant | après | ce qui a changé |
|---|---|---|---|---|
| `a-oui-explicite` | « Oui, je souhaite que tu fasses ça » | 3/3 | 3/3 | — |
| `a-oui-nu` | « Oui » | 0/3 | **3/3** | déballait les cinq sources, sert les deux proposées |
| `a-vas-y` | « vas-y » | 0/3 | **3/3** | déballait les cinq sources, sert les tables promises |
| `a-accord` | « d'accord, fais-le » | 3/3 | 3/3 | — |
| `b-et-dedans` | « et dedans ? » | 0/3 | 0/3 | **dette** (§23.9) |
| `b-et-les-colonnes` | « et les colonnes ? » | 3/3 | 3/3 | — |
| `b-lesquelles` | « lesquelles portent des dates ? » | 3/3 | 3/3 | — |
| `b-et-pour-l-autre` | « et pour `iris` ? » | 3/3 | 3/3 | régressé, puis réparé (§23.7) |
| `c-pas-repondu` | « tu ne m'as pas répondu » | 3/3 | 3/3 | — |
| `c-pas-ce-que-je-demandais` | « ce n'est pas ce que je demandais » | 0/3 | 0/3 | **dette** (§23.9) |
| `c-pas-ma-question` | « tu n'as pas répondu à ma question » | 0/3 | 0/3 | **dette** (§23.9) |
| `c-pas-ca` | « ce n'est pas ça que je voulais savoir » | 0/3 | 0/3 | **dette** (§23.9) |

Ce qui est réparé est la situation (a) **en entier** : ce que l'agent propose,
il le tient maintenant douze fois sur douze. Et il le tient **sans passer par
le planificateur** — `system → synthesize`, un tour direct là où il en fallait
trois pour finir sur un menu.

### 23.7 Une régression, et pourquoi elle valait une seconde mesure

La première version de la clause a **cassé un cas qui marchait** :
`b-et-pour-l-autre`, « et pour `iris` ? » après les attributs de `titanic`,
3/3 → 0/3. Le fil brut :

```
ENVOI  [UserPromptPart] "De quels attributs a besoin le modèle `titanic` pour prédire ?"
ENVOI  [TextPart]       "Pour prédire avec le modèle `titanic` […] il me faut 7 attribut(s) […]"
ENVOI  [UserPromptPart] "et pour `iris` ?"
RETOUR [TextPart]       "Je n'ai pas d'information sur le modèle `iris` dans mon registre."
```

Il en a une. Le modèle a affirmé une ABSENCE sans regarder — l'exact symétrique
de ce que la clause lui interdisait, qui ne visait que les affirmations
positives. Le second paragraphe de la clause le dit : « une absence se constate
en regardant, jamais en se souvenant », et « quand le message demande la même
chose d'une AUTRE cible, appelle l'outil avec cette autre cible ». Mesuré sur
ce seul cas, trois tirages : **0/3 → 3/3**.

Sans la mesure avant/après cas par cas, ce gain net de +6 aurait masqué une
perte de −3 : le total serait passé de 18 à 21, et personne n'aurait su qu'un
cas qui marchait avait cessé de marcher.

### 23.8 La proposition en attente : ce n'est pas un `pending`, et voici pourquoi

La question posée était nette : le mécanisme de `pending` — la prédiction en
attente de features, déjà persistée dans le fil — est-il la bonne forme pour
« une proposition en attente, reprise au tour suivant » ? **Non**, et trois
faits le disent.

**Un `pending` retient un fait STRUCTURÉ ; une proposition est du texte.** La
prédiction en attente naît d'une validation qui échoue : on sait exactement
quel `dataset` et quelles `features` manquent. Une proposition en conversation
est une phrase libre. La retenir demanderait soit de l'analyser après coup —
c'est-à-dire un lexique de tournures, ce que la règle de C33 interdit et que ce
chantier a déjà payé trois fois — soit de contraindre la sortie de l'agent
système, qui est aujourd'hui du texte libre et dont toute la surface mesurée
dans ce document dépend.

**Il n'y a le plus souvent RIEN à retenir.** La première campagne l'a mesuré
sans le chercher : l'agent, à qui le prompt demande de « terminer par une
phrase courte invitant à la suite », termine par une question **ouverte** —
« Quelle source souhaitez-vous explorer ? », « Que souhaitez-vous savoir sur
ces tables ? ». Aucune action n'y est proposée ; un `pending` n'aurait rien à
persister. C'est d'ailleurs pour cette raison que les quatre cas de la
situation (a) ont dû figer leur tour d'amorce (§23.5).

**Et l'état a déjà été essayé, ici même.** Un second champ — « une proposition
attend une réponse » — a vécu sur la source de travail de la conversation, et
il a été retiré après mesure : il créait une dépendance à l'ordre des tours qui
a fait perdre un message de validation sur le parcours mesuré
(`orchestrator/graph.py`, le commentaire au-dessus de `OrchestratorState`).

Le fil, lui, porte déjà l'information, et il la porte **sans état** : la
proposition est dans la réponse du tour d'avant, qui est relue du disque à
chaque tour comme la source de travail et la prédiction en attente. Il ne
manquait que de la donner sous la forme que le modèle sait lire. 12/12 sur la
situation (a) est la réponse expérimentale : **ce que l'agent propose, il le
tient, et rien n'a eu besoin d'être persisté pour ça.**

### 23.9 Ce que la clause a coûté, et la limite qui l'a rendue tenable

Le §22 a appris qu'une phrase de renfort ajoutée au prompt de l'agent système
se paie ailleurs. Ce chantier l'a payé une seconde fois, et au même endroit.

La clause de continuation, telle qu'écrite d'abord, a fait perdre **un tour du
parcours de démonstration** — `verrou-bascule`, la question « Et si l'on regarde
maintenant du côté de la source `facturation`, quelle y est l'énergie totale en
kWh ? » :

| prompt | ce que fait l'agent | score |
|---|---|---|
| avant la clause | `system → plan → retrieval → synthesize` — il calcule | **3/3** |
| avec la clause | `system → synthesize` — il sert le schéma de `facturation` | **0/3** |

Le message commence par « Et si l'on regarde maintenant… ». La clause citait
« et dedans ? » parmi ses exemples : l'agent a classé un CALCUL parmi les
continuations, s'en est emparé, et a répondu par le schéma d'une source à qui
demandait une somme.

La clause porte donc sa propre limite, en dernier :

> **LE TEST, LUI, NE BOUGE PAS.** Un message qui porte SA PROPRE question n'est
> pas une continuation, même s'il commence par « et ». Et un message qui
> continue ton tour en demandant un CALCUL reste un calcul — la réponse est
> `AUTRE`.

Mesuré des deux côtés, et c'est la seule façon d'ajouter une phrase à ce
prompt : `verrou-bascule` **0/3 → 3/3**, et les cinq cas de continuation qui
avaient gagné restent à **15/15**. Une consigne qui n'énonce pas où elle
s'arrête finit par s'appliquer là où elle n'a rien à faire.

### 23.10 Ce qui reste ouvert après le §23

**Quatre cas sur douze restent perdus, avant comme après**, et ils ne sont pas
du même bois :

- `b-et-dedans` — « et dedans ? » après la description de `telemetrie` : le
  modèle recopie en texte la réponse du tour d'avant, sans appeler un seul
  outil. Il répond juste, et le socle refuse de le servir — à raison : le
  contrat est qu'une réponse non fondée sur un outil de CE tour-ci n'est pas
  servie. Le défaut n'est pas dans la ceinture, il est dans le modèle qui
  préfère se souvenir plutôt que regarder.
- `c-pas-ce-que-je-demandais`, `c-pas-ma-question`, `c-pas-ca` — la
  contestation. Le modèle répond `AUTRE` à « ce n'est pas ce que je
  demandais », et le tour finit sur le menu du planificateur ou sur un
  déballage de l'inventaire. **La situation (c) n'a pas bougé d'un tour** : 3/12
  avant, 3/12 après. Le paragraphe qui lui est consacré dans la clause ne l'a
  pas déplacée, et le prompt est déjà l'endroit le plus cher du dépôt — il
  faudra autre chose qu'une phrase de plus.

**Le tour d'avant a cessé d'atteindre les OUTILS.** `SystemeDeps.question`
sert de précision à deux outils qui devinent leur cible (`schema_d_une_source`,
`attributs_d_un_modele`). Elle contenait, par accident, le message COMPOSÉ —
donc le tour d'avant. Elle ne porte plus que le message nu. Aucun cas mesuré
ne s'en plaint, et rien n'a été ajouté pour le compenser : ajouter du code que
rien ne réclame serait retomber dans ce que ce document reproche ailleurs. Mais
c'est un fait dont on ne sait pas encore ce qu'il coûte.

**Ce que l'agent propose reste une question ouverte — au sens propre.** La
consigne « termine par une phrase courte invitant à la suite » lui fait
produire « Quelle source souhaitez-vous explorer ? » bien plus souvent que
« souhaitez-vous que je fasse cela ? ». Le socle tient désormais ce qu'il
propose quand il propose quelque chose ; il propose rarement quelque chose.

## 24. Le huitième outil, et le fait qui manquait dans la fiche

Le §22.2 avait laissé une question ouverte sans le dire : `titanic` et `iris`
ne portent **aucune** colonne de date, et rien dans le socle ne le disait. Le
relevé (`agents/retrieval/faits.py`) lisait pourtant le schéma, constatait
l'absence, et se taisait. Ce silence a fini par coûter une question.

### 24.1 Ce que C51 a fait basculer

`periode-directe` était verte avant C51. C51 a donné un **huitième** outil à
l'agent système — `memoire_de_la_conversation`, pour qu'il sache dire ce que le
fil a fabriqué — et ce point-là est passé à « répondu à côté » : deux campagnes
de chaque côté de C51, même verdict à chaque fois. L'outil est une demande
explicite, et il reste. Une tâche a ensuite tenté de raccourcir sa fiche :
aucun effet, deux tirages. Ce n'était donc pas sa rédaction — c'est qu'il y a
un outil de plus à choisir.

**Deux campagnes du 2026-09-22 sur `c184e3c`, catalogue par défaut :**

| campagne | questions méta | témoins | la question qui tombe |
|---|---|---|---|
| avant, 1ʳᵉ | 35/36 | 4/4 | `periode-directe` |
| avant, 2ᵉ | 35/36 | 4/4 | `periode-directe`, même verdict |

### 24.2 Ce que le relevé a montré — et qu'on n'a pas deviné

Trois hypothèses se réparent à trois endroits différents, et supposer coûte le
détour que ce dépôt a déjà payé. La question a donc d'abord été **instrumentée**
— quel outil est appelé, avec quel argument, ce que l'outil sert, ce que le
modèle en écrit — sur trois tirages, avant toute réparation.

Les trois tirages sont identiques :

    appels d'outil ÉMIS : sources_de_donnees({"cible": "titanic"})

    ce qu'il a SERVI :
      La source **titanic** (postgres) — Base Titanic multi-tables (…)
      2 table(s), 894 ligne(s) (classes : 3, passengers : 891)

    RÉPONSE :
      La source `titanic` porte sur une période non spécifiée dans sa
      description, mais elle contient 2 table(s) et 894 ligne(s).

**Le modèle appelle le bon outil, avec le bon argument.** Il ne se trompe pas de
chemin, et il n'a rien laissé tomber : la fiche qu'il a reçue ne dit **rien** de
la période. Ce n'était donc ni l'hypothèse du mauvais outil, ni celle de la
formulation négligente — c'était un **trou dans les faits**, et « non spécifiée
dans sa description » est ce qu'on écrit quand on n'a rien lu sur un sujet.

**Le huitième outil n'a rien cassé : il a révélé le trou.** Les tableaux des
§3, §7 et §11 disent par où cette question passait quand elle était verte —
capacité `query`, chemin « le planificateur ». Le modèle n'appelait alors **aucun** outil
système ; le tour repartait au planificateur, qui allait lire le schéma pour
écrire son SQL et rendait « il n'y a aucune colonne de date ou d'heure dans le
schéma ». La bonne réponse, obtenue en passant par la seule porte qui, à
l'époque, servait ce fait.

Un outil de plus à choisir a suffi à faire basculer la question vers l'agent
système — qui l'attrape désormais, et c'est ce qu'on veut : elle porte sur une
source, pas sur ses lignes. Ce qu'il sert sur ce sujet, lui, était vide.

### 24.3 La réparation : une absence se constate

Le principe est déjà écrit trois fois dans ce dépôt — une source injoignable
rend la raison de son silence, une conversation sans objet le dit plutôt que de
rendre une chaîne vide. La période était le seul des trois faits relevés à ne
pas le suivre. Elle le suit :

    2 table(s), 894 ligne(s) (classes : 3, passengers : 891)
      — aucune période couverte : la source ne porte aucune colonne de date

Deux absences, distinguées : la source ne date rien, ou la colonne qu'elle porte
est vide. La première clôt la question ; la seconde désigne une donnée manquante
à corriger en amont.

**Aucun prompt, aucune fiche d'outil n'a bougé** — les sept empreintes SHA-256
sont inchangées, et c'est vérifié par `test_aucun_paragraphe_de_prompt_n_a_bouge`
et `test_aucune_fiche_d_outil_n_a_bouge_au_caractere_pres`. C'est la monnaie
qu'on cherchait à récupérer : cinq formulations ont été écrites puis retirées
ici, chacune au prix d'une question de cette batterie. Un fait ajouté à une
fiche ne parle qu'aux tours où cette fiche est servie ; une phrase de prompt
parle à tous.

### 24.4 Ce que ça donne, deux campagnes de chaque côté

| campagne | questions méta | témoins | appels LLM (méta + témoins) |
|---|---|---|---|
| avant, 1ʳᵉ | 35/36 | 4/4 | 83 + 17 |
| avant, 2ᵉ | 35/36 | 4/4 | 83 + 17 |
| après, 1ʳᵉ | **36/36** | 4/4 | 83 + 17 |
| après, 2ᵉ | **36/36** | 4/4 | 83 + 17 |

**Le coût est identique au chiffre près, des deux côtés.** La réparation ne fait
appeler aucun outil de plus : elle remplit celui qui était déjà appelé.

La réponse servie, trois tirages sur trois :

> La source `titanic` est une base de données qui ne porte aucune colonne de
> date, elle ne couvre donc aucune période.

`periode-indirecte` — « de quand datent les données que tu as ? » — reste verte
et continue de passer par le planificateur : elle n'a pas changé de chemin.

### 24.5 Ce que le huitième outil coûte encore

Rien de mesuré sur cette batterie. Ce qu'il avait coûté, c'était un point que le
socle tenait par le **mauvais chemin** : une question sur une source partait au
planificateur, qui la traitait comme un calcul et trouvait la réponse en
chemin. Le point est maintenant gagné par le chemin qui correspond à la
question — l'agent système, deux appels LLM, sans plan ni SQL.

Ce que ça enseigne vaut au-delà de ce point-ci : **un outil de plus ne dégrade
pas une réponse, il change la porte par laquelle elle sort.** Quand une question
bascule d'un chemin à l'autre, ce qu'il faut regarder n'est pas le prompt qui
l'a routée, c'est si le nouveau chemin porte les faits que l'ancien portait.
Ici, il en manquait un.

## 25. L'oracle bénissait une réponse fausse

Le §24 finit sur une phrase qui était fausse au moment où elle a été écrite :

> `periode-indirecte` — « de quand datent les données que tu as ? » — reste verte
> et continue de passer par le planificateur : elle n'a pas changé de chemin.

Elle *restait verte*, en effet. Elle ne restait pas *juste*. Deux campagnes de
surface, **40/40** les deux fois, et l'une des quarante répondait à côté sans
que rien ne le dise.

### 25.1 Ce que la campagne bénissait

| | |
|---|---|
| clé | `periode-indirecte` |
| question | De quand datent les données que tu as ? |
| chemin | `system → plan → retrieval → synthesize`, 5 appels LLM |
| réponse | *Les données disponibles dans la table `passengers` couvrent des **âges** allant de 0.42 à 80.0 ans.* |
| verdict | **répondu correctement** |

On demande des DATES, il répond des ÂGES. Ni `titanic` ni `iris` ne portent la
moindre colonne de date, et la bonne réponse est celle que le §24 vient de
rendre possible sur la formulation directe : *ne porte aucune colonne de date,
elle ne couvre donc aucune période*.

**Deux formulations de la même question, deux chemins, deux réponses, et le même
verdict vert.** Tant que l'oracle bénit celle-là, le 40/40 n'est pas un verdict :
c'est un chiffre.

### 25.2 Ce que l'oracle exigeait, et ce qu'il exige

L'ancien, tel qu'il était écrit :

```python
def oracle_de_periode(self, cle_table):
    if self.temporelles[cle_table]:
        return self.temporelles[cle_table]
    return AVEUX_D_ABSENCE_DE_DATE + self.colonnes[cle_table]
```

Deux défauts, et le second est celui qui a béni :

1. `AVEUX_D_ABSENCE_DE_DATE` était une **liste de dix tournures entières**
   comparées en sous-chaîne — le défaut du §22 dans l'autre sens : une liste
   refuse la onzième façon de dire l'absence ;
2. `+ self.colonnes[cle_table]` acceptait **n'importe quel nom de colonne de la
   table**. Il était là pour prouver qu'on avait regardé le schéma au lieu
   d'inventer des bornes plausibles. Or `age` est une colonne de `passengers`,
   et il vit à l'intérieur du mot « âges », que l'oracle replie en « ages ». La
   réponse sur les âges satisfaisait donc l'oracle **par le mot même qui la
   rendait fausse**.

Une exigence qui accepte dix noms de colonnes n'exige rien.

Le nouveau n'est pas une liste plus longue, ni une liste plus courte : c'est une
**exigence jugée par une fonction**, un troisième bord à côté de `attendus_tous`
et de `interdits`.

```python
@dataclass(frozen=True)
class Exigence:
    manque: str
    juge: Callable[[str], bool]
```

Et deux juges, tous deux de FORME :

- **une date** est un millésime — `(?<!\d)(?:1\d{3}|2[01]\d{2})(?!\d)`. Toute
  écriture d'une date en porte un ; « 0.42 » et « 80.0 » n'en portent aucun ;
- **le constat d'absence** est une négation suivie, dans la même phrase, de ce
  qu'elle nie : les mots d'absence (`pas`, `ni`, `sans`, `rien`, `non`,
  `aucun*`, `dépourvu*`) devant les radicaux du temps (`date`, `period`,
  `temporel`, `chronolog`, `horodat`, `année`, `millésim`). Six
  écritures mesurées passent, dont aucune n'est écrite nulle part —
  « ne porte aucune colonne de date », « aucune de mes sources ne contient de
  colonne temporelle », « dépourvues de dates », « ne sont pas datées »…

**L'ORDRE fait tout le travail**, et c'est ce qui distingue le constat de la
phrase vague. Le constat met sa négation AVANT ce qu'elle nie et nie une
EXISTENCE ; la réponse vague la met après et ne nie qu'une qualité — *« une
période non spécifiée dans sa description »*, celle de deux campagnes avant le
§24, porte « période » et « non » et ne dit toujours pas qu'il n'y a pas de
date. Un oracle qui aurait cherché la seule rencontre des deux mots l'aurait
bénie.

**Et lequel des deux est exigé, c'est la source qui le dit.** Là où une colonne
de date existe, une réponse fondée porte une date, et « aucune période » y
serait faux. Là où il n'y en a aucune, elle constate l'absence, et une date y
serait inventée. L'oracle reste lu dans la source, comme tous les autres de
cette batterie.

**La seconde porte, fermée elle aussi.** `periode-indirecte` admettait une
*clarification* énumérant les sources — et les relevés des §3 et §7 en gardent la
trace : « Sur quelle source veux-tu travailler : titanic, iris ? », **répondu
correctement**. La question ne laisse pourtant rien à choisir : elle porte sur
tout ce que l'agent a, et les deux sources se datent de la même façon. Une
clarification n'y porte ni date ni constat.

### 25.3 Les trente-neuf autres

Un oracle qui laisse passer celle-là en laisse peut-être passer d'autres. Les
trente-neuf autres questions ont été relues une à une. **Trente-quatre tiennent**
— ce sont celles dont l'oracle est une **conjonction exhaustive** tirée de la
source : les dix colonnes de `passengers`, les trois modèles du registre, les
deux sources du catalogue. Une réponse fausse devrait les nommer toutes.

**Cinq ne tiennent pas**, et une seule a été corrigée ici :

| clé | ce que l'oracle accepte | corrigé ? |
|---|---|---|
| `periode-directe` | la même liste de colonnes que `periode-indirecte` | **oui**, même correctif |
| `volumetrie-globale` | l'un des comptes de tables, dont `3` — donc toute réponse portant le chiffre 3, y compris à l'intérieur de `342` ou `13` ; plus une clarification | non |
| `features-nue` | UN nom de dataset, et `titanic` comme `iris` sont aussi des noms de SOURCE : une réponse qui liste les sources satisfait une question sur les attributs | non |
| `features-indirecte` | idem | non |
| `sens-colonne` | le seul mot `classes`, qui est un mot français ordinaire : « class_id désigne une des trois classes » passe sans avoir suivi la clé étrangère | non |

Les quatre non corrigées sont laissées telles quelles et **dites** : deux d'entre
elles portent des questions dont la clarification est la réponse attendue, et
`volumetrie-globale` est déjà connue pour basculer d'une campagne à l'autre sans
que le code bouge. Les resserrer déplacerait quatre repères pour un défaut qui
n'est pas celui-ci.

### 25.4 La réponse : trois hypothèses, une seule mesurée

Trois causes possibles, qui ne se réparent pas au même endroit :

1. l'agent système ne reconnaît pas la question comme étant pour lui ;
2. il la reconnaît, et l'outil ne lui sert pas de quoi répondre — la cause du
   §24, dont le correctif pouvait ne couvrir que le cas où UNE source est visée ;
3. il la reconnaît, reçoit les faits, et sa formulation les laisse tomber.

**Relevé, trois tirages sur trois :**

```
outils    : ()
réponse   : 'AUTRE'
faits     : ''
```

C'est la **première**. Le modèle décline le tour sans appeler le moindre outil,
et le signal de routage étant l'appel d'outil et non le texte, la question repart
au planificateur — qui la classe `query` et fait écrire
`SELECT MIN(p.age), MAX(p.age) FROM passengers p`.

La deuxième est écartée par la mesure et non par le raisonnement : l'inventaire
pluriel porte bien le constat, une ligne par source.

```
- **titanic** (postgres) — Base Titanic multi-tables…
  2 table(s), 894 ligne(s) — aucune période couverte : la source ne porte aucune colonne de date
- **iris** (file) — Dataset Iris…
  1 table(s), 150 ligne(s) — aucune période couverte : la source ne porte aucune colonne de date
```

Le fait existait. Ce qui manquait était qu'on le serve **au tour qui renonce** —
exactement le diagnostic du plancher de `ce_que_le_schema_en_dit`.

### 25.5 Le troisième plancher

Ni prompt ni fiche d'outil, et pas par principe : le §20.9 garde la trace de la
ligne de frontière qui faisait justement capter CETTE question-ci par l'agent
système — et lui faisait rendre une phrase vague, en coûtant une seconde
ailleurs. Cinq formulations ont été écrites puis retirées à cet endroit, chacune
au prix d'une question de cette batterie.

Un plancher, lui, ne parle à personne. C'est la troisième fois qu'on en pose un,
et il obéit aux mêmes règles que les deux précédents : il ne s'applique qu'aux
tours où **aucun outil n'a été appelé**, il ne décide rien de sémantique, et il
ne sert que du texte qui existait déjà.

```python
if deps.outils_appeles or deps.releves is None:
    return
if not introspection.demande_une_periode(deps.question):
    return
faits = deps.releves.tous()
if not faits or not all(releve.sans_periode for releve in faits.values()):
    return
deps.retenir("plancher_de_la_periode", introspection.decrire_les_sources(...))
```

**La garde qui le rend inoffensif est la dernière.** Dès qu'UNE source du
catalogue porte une période, le plancher se retire : la question a alors une
réponse à calculer, et c'est au planificateur de la chercher. Les catalogues de
démonstration et métier datent toutes leurs sources principales — `date_commande`,
`date_signalement`, `horodatage`, `date_émission`, `date_mouvement` — et ce
plancher y est **muet**. Les sept campagnes qui s'y jouent ne peuvent pas bouger,
et c'est une propriété du code, pas une espérance.

**`demande_une_periode` n'est pas le lexique retiré au §9**, et la différence
tient en une phrase : celui-là listait des TOURNURES — « quelles données as-tu ? »,
« tu bosses sur quoi ? » — et la famille des façons de poser une question est
ouverte, donc une liste y est toujours en retard d'une formulation. La famille
des mots par lesquels une langue désigne le temps est close. `quand` en est
exclu, et c'est la seule exclusion délibérée : il ouvre aussi bien une question
de temps qu'une subordonnée de condition — « quand je te donne un âge, tu prédis
quoi ? ».

**Et le radical est `date`, pas `dat`** — quatre tests de la suite l'ont dit
avant qu'on le voie : `dat` reconnaît **dataset**, qui est un mot de ce dépôt,
et « décris le dataset iris » partait au plancher au lieu du planificateur. Un
radical trop court prend le vocabulaire du domaine pour celui du temps. Le prix
est « datation », que `date` ne reconnaît pas, et qu'aucune question mesurée
n'emploie. L'oracle a reçu la même correction, et pour la même raison : « je
n'ai pas de dataset » y comptait pour un constat d'absence de date.

Sur les quarante questions de la batterie, **deux** le déclenchent : les deux de
la famille « période ». Et `periode-directe` appelle déjà son outil, donc le
plancher s'y retire.

### 25.6 Ce que ça donne, deux campagnes de chaque côté, les deux oracles

| campagne | ancien oracle | nouvel oracle | témoins | appels LLM |
|---|---|---|---|---|
| avant, 1ʳᵉ | 36/36 | **35/36** | 4/4 | 83 + 17 |
| avant, 2ᵉ | 36/36 | **35/36** | 4/4 | 83 + 17 |
| après, 1ʳᵉ | 36/36 | **36/36** | 4/4 | **80** + 17 |
| après, 2ᵉ | 36/36 | **36/36** | 4/4 | **80** + 17 |

**L'ancien oracle rend 36/36 dans les quatre cases, et c'est tout le propos** :
il ne distinguait pas la réponse fausse de la juste. Le nouveau les distingue, et
c'est lui qui fait du 40/40 un verdict.

Le coût BAISSE de trois appels : la question passait par cinq appels LLM — plan,
récupération, synthèse — et en coûte deux. Elle a changé de chemin, et c'est le
sien.

La réponse servie, trois tirages sur trois :

> Les sources `titanic` (postgres) et `iris` (file) n'ont aucune période couverte
> car elles ne portent aucune colonne de date.

`system : plancher_de_la_periode — reformulé au second tour (réponse hors sujet)`.
Le modèle a répondu `AUTRE` ; la ceinture l'a écarté, lui a rendu les faits et la
question, et sa seconde formulation les porte. Le plancher n'a rien formulé : il
a servi.

**Aucun prompt, aucune fiche d'outil n'a bougé** — les sept empreintes SHA-256
sont inchangées.

---

## 26. Une régression qui n'en était pas, et ce que ça dit de l'instrument

Le 2026-09-22, le pilote relève ceci sur le catalogue métier, sur une question
qu'aucune campagne ne pose — « ventes ou production ? », posée dans une
conversation neuve, aucune source liée :

| | réponse | longueur |
|---|---|---|
| après C48 | `Sur quelle source veux-tu travailler : ventes, production, stocks, iris, titanic ?` | 82 car. |
| « aujourd'hui » | les cinq fiches complètes, volumes et périodes compris | 2 136 car. |

Deux textes incomparables, une question réparée à C48, et rien entre les deux
qui rougisse. La conclusion allait de soi : quelque chose a glissé entre C48
(`0e1f192`) et C53 (`e126e56`). **Elle est fausse, et ce qui la rend fausse vaut
plus que la réparation qu'elle appelait.**

### 26.1 La bissection : les deux mesures portent sur le même code

Chaque commit candidat a été extrait dans un arbre propre — `git archive`, plus
les sources non suivies par git recopiées, plus le `.env` — et la question posée
telle quelle, catalogue métier, conversation neuve.

| commit | tâche | tirages | 82 car. | 2 136 / 2 276 car. |
|---|---|---|---|---|
| `0e1f192` | C48 | 30 | 28 | **2** |
| `72fe851` | C50 | 10 | 10 | 0 |
| `ec79d79` | C51 | 12 | 12 | 0 |
| `c184e3c` | C51 | 2 | 2 | 0 |
| `970f199` | C52 | 2 | 2 | 0 |
| `e126e56` | C53 | 2 | 2 | 0 |
| `97389aa` | C54 | 3 | 3 | 0 |
| `de660f8` | C54 (tête) | 43 | 41 | **2** |

Les deux extrêmes ont été tirés **en alternance**, vingt fois chacun, un
processus par tirage, pour que la dérive du moteur frappe les deux également :
**20/20 à 82 caractères des deux côtés**. C48 et la tête de `main` sont
indiscernables sur cette question.

### 26.2 Le mécanisme : un plan bistable, pas une régression

Le tour se joue en deux temps, et c'est le premier qui bascule.

1. l'agent système ne trouve rien à appeler et passe la main
   (`system : aucun outil appelé — passe au planificateur`) ;
2. le planificateur rend `capability=query` et **soit** `source='ventes,
   production'` — les deux noms qu'il a lus, empaquetés dans un champ qui en
   attend un —, **soit** `source=''`.

Les deux valeurs prennent deux chemins différents dans les règles du plan, et
les deux chemins sont justes :

- `source='ventes, production'` → `_regle_normaliser_le_nom_de_source` voit une
  désignation qui nomme PLUSIEURS sources déclarées, laisse tomber la phrase
  « la source est introuvable » et rend la question du choix : **82 caractères** ;
- `source=''` → `_regle_choisir_la_source` propose l'inventaire, fiches et faits
  compris : **2 136 caractères**.

La température est à 0. Le prompt est identique : `planner.txt` n'a pas bougé
depuis C48, `Catalog.describe()` non plus, et une conversation neuve n'ajoute ni
contexte de source, ni historique, ni prédiction en attente. Ce qui change d'un
processus à l'autre est **l'état du moteur** — composition des lots, cache de
préfixe — et il suffit à faire basculer un champ qui est, sur cette
formulation-là, exactement à la frontière.

### 26.3 Le fait qui tranche, et qui ne demande aucun modèle

Le texte de l'inventaire est déterministe. Il se mesure sans poser la moindre
question :

| commit | `introspection.proposer_les_sources` sur le catalogue métier |
|---|---|
| `0e1f192` (C48) | **2 136** caractères |
| `c184e3c` (C51) | **2 136** caractères |
| `970f199` (C52) | 2 276 caractères |
| `e126e56` (C53) | 2 276 caractères |
| `de660f8` (tête) | 2 276 caractères |

C52 a allongé chaque fiche de la phrase « aucune période couverte : la source ne
porte aucune colonne de date » (§24.3) : deux sources sans date, cent quarante
caractères de plus. **Les 2 136 caractères relevés « aujourd'hui » ne peuvent pas
sortir de `main` d'aujourd'hui.** Ils sortent d'un arbre à C51 ou avant,
c'est-à-dire du même code que les 82 caractères relevés « après C48 ».

Les deux observations du pilote sont deux tirages d'une même pièce, sur le même
arbre, séparés par un fait qu'aucun des deux ne portait : la pièce a deux faces.

### 26.4 Ce qui a été réparé, et ce qui ne l'a pas été

Rien dans `src/`. Il n'y a pas de régression à défaire, et le comportement
majoritaire — la question qui fait choisir, en une ligne — est le bon.

Ce qui a été réparé est **l'instrument** : la formulation entre dans la batterie
sous la clé `choix-entre-deux-sources`, construite sur les deux premiers noms du
catalogue mesuré pour qu'elle se rejoue ailleurs sans être réécrite. Son oracle
a deux bords — nommer les deux sources, et ne porter AUCUN des comptes de lignes
lus dans les sources, qui sont la signature des fiches déroulées. Aucun seuil de
longueur : un seuil réglé mesure la main qui l'a posé.

**Et elle rougit.** Deux campagnes sur le catalogue par défaut, `titanic ou
iris ?`, 0/2 : le plan sort à `source=''` les deux fois et l'inventaire est
servi. C'est le même bord de la pièce, sur une autre formulation, et il est
désormais compté au lieu d'être découvert par hasard six semaines plus tard.

### 26.5 Le coût de la leçon

Trois relevés de ce document portent un chiffre tiré d'**une** campagne, et
`periode-indirecte` (§25) a tenu deux campagnes à 40/40 sur une réponse fausse.
Ce §26 ajoute le pendant : **un comportement bistable tiré une fois de chaque
côté d'une série de commits ressemble exactement à une régression**, et rien
dans le relevé ne le dit. Ce qui l'a dit ici est un fait déterministe — la
longueur d'un texte que le code compose sans modèle — trouvé en cherchant
pourquoi les deux nombres ne se ressemblaient pas assez.

La règle qui en sort, et elle est la même que pour les campagnes concurrentes :
**un tirage n'est pas une mesure, et deux tirages pris à deux dates ne sont pas
une comparaison.** Ce qui compare, c'est l'alternance — les deux arbres, tour à
tour, dans la même fenêtre de temps.

---

## 27. Les quatre oracles faibles de la surface, et trois formulations de plus

Le §25 a durci l'oracle de la PÉRIODE et en a déclaré quatre autres faibles,
sans y toucher. Les voici jugés sur le FAIT, et non plus sur la présence d'un
mot.

### 27.1 Ce que chacun acceptait

| clé | ce qu'exigeait l'oracle | ce qu'il acceptait donc |
|---|---|---|
| `volumetrie-globale` | un compte de lignes **en sous-chaîne** — `"3"`, `"891"`, `"150"` | `342`, `8915`, n'importe quel nombre d'un chiffre |
| `features-nue` | **un** nom de modèle | `titanic` et `iris` sont aussi des noms de SOURCES, et le catalogue est dans tous les prompts |
| `features-indirecte` | idem | idem |
| `sens-colonne` | la chaîne `"classes"` | « la colonne `class_id` donne les classes des passagers » — la lecture du NOM de la colonne, sans une ligne de schéma |

### 27.2 Ce qu'ils exigent maintenant

Chacun juge un fait, lu là où il est vrai.

- **`volumetrie-globale`** — un compte de lignes parmi ceux que le relevé a
  LUS, reconnu comme un NOMBRE (`nombres`) et non comme une suite de
  caractères. 342 n'est pas 3.
- **`features-nue`, `features-indirecte`** — ou bien les attributs d'un modèle,
  **tous**, ou bien le choix entre **tous** les modèles. Deux réponses justes à
  cette question, et la même exigence sur les deux : être complète. Un nom de
  modèle cité au passage ne l'est ni l'une ni l'autre.
- **`sens-colonne`** — le RENVOI à la table pointée : son nom, et dans la même
  phrase un mot qui dit la clé étrangère. Quatre radicaux (`etrang`, `referen`,
  `renvoi`, `joint`), qui sont les quatre façons de nommer une clé étrangère en
  français, et la table est lue dans l'ontologie de la source
  (`TableInfo.foreign_keys`) — jamais écrite à la main.

Une cinquième marche a bougé avec eux, dans `classer` : **une clarification
ADMISE n'est plus dispensée de l'oracle.** `clarification_admise` autorise à
renvoyer la question ; elle n'autorisait, en fait, à ne rien porter du tout —
sur les trois questions concernées, tout ce qui énumérait les choix comptait
juste sans qu'on lise la réponse.

### 27.3 Les deux oracles sur les mêmes réponses

Les scores d'avant et d'après sont calculés sur **le même texte** : les réponses
des deux campagnes du 2026-09-22 sont rejouées contre les deux oracles. Deux
campagnes rendraient deux jeux de réponses, et l'écart mesurerait autant le
moteur que l'oracle.

| clé | campagne 1 (ancien → nouveau) | campagne 2 (ancien → nouveau) |
|---|---|---|
| `volumetrie-globale` | correct → correct | correct → correct |
| `features-nue` | correct → correct | correct → correct |
| `features-indirecte` | correct → correct | correct → correct |
| `sens-colonne` | correct → correct | correct → correct |

**Aucun ne tombe, et c'est un résultat, pas une absence de résultat** : sur ces
quatre questions-là, le système rendait déjà la réponse fondée — les quatre
réponses portent le compte de lignes, la liste complète, le renvoi à `classes`.
Ce que les anciens oracles bénissaient n'est pas ce que le système a rendu ce
jour-là ; c'est ce qu'il aurait pu rendre sans que rien ne bouge. La preuve que
les nouveaux le refusent ne vient donc pas d'une campagne mais de
`tests/unit/test_mesure_surface_conversationnelle.py`, où les quatre réponses
fausses sont figées et refusées une à une.

### 27.4 Trois formulations qui n'étaient nulle part

| clé | question | pourquoi elle est là | verdict, 2 campagnes |
|---|---|---|---|
| `temoin-dataset-iris` | décris le dataset iris | TÉMOIN du plancher des dates (§25.5) : le mot « dataset » a déjà failli l'y envoyer | 2/2 |
| `temoin-quand-je-te-donne-un-age` | quand je te donne un âge, tu prédis quoi ? | TÉMOIN : « quand » ouvre une hypothèse, pas une période | 2/2 |
| `periode-paraphrase-annee` | tes données, elles sont de quelle année ? | la paraphrase non mesurée, **non réparée** | 2/2 |

Les deux témoins forment une famille à part, `témoin (plancher des dates)` :
celle du §20 protège le chemin des DONNÉES d'un routeur de questions méta,
celle-ci protège deux questions ordinaires d'un plancher. Leur oracle est le
même et il est dérivé : une réponse qui nomme **toutes** les sources du
catalogue est l'inventaire, donc le plancher a parlé.

**Et la paraphrase reste rouge de l'autre côté, là où l'oracle ne regarde pas.**
Elle compte juste — « les données ne contiennent pas d'information explicite sur
l'année de collecte » EST le constat d'absence dû — mais la colonne « Capacité »
du tableau dit `query` et le coût est de **5 appels LLM** contre 2 pour ses deux
sœurs de la famille : elle part au planificateur, l'agent SQL reçoit une
question de dates sur un schéma qui n'en porte aucune, et la phrase qui suit le
constat — « cependant l'âge maximal enregistré est de 80 ans » — est le
comblement que le plancher de C53 existe pour éviter. Elle n'est pas réparée ici,
et elle a maintenant un chiffre.

### 27.5 Le repère de la campagne

Quarante-quatre questions : trente-huit méta et six témoins. Deux campagnes le
2026-09-22, avec les oracles durcis :

| campagne | méta | témoins | appels LLM |
|---|---|---|---|
| 1ʳᵉ | **37/38** | 6/6 | 87 + 22 |
| 2ᵉ | **37/38** | 6/6 | 87 + 22 |

La seule qui manque, les deux fois, est `choix-entre-deux-sources` (§26.4) : le
plan sort à `source=''` et l'inventaire est servi à qui hésitait entre deux
noms. C'est le bord bistable du §26, compté.

## 28. Un témoin qui tenait à une phrase que personne n'avait envoyée

`temoin-quand-je-te-donne-un-age` a été inscrit par C55 pour surveiller le
plancher des dates : « quand » ouvre une hypothèse aussi bien qu'une période, et
`RADICAUX_DU_TEMPS` l'exclut délibérément. Quelques heures plus tard, C56 l'a
fait passer au rouge — 2/2, deux campagnes, déterministe — **sans toucher au
plancher qu'il surveille**.

### Le fil brut des deux côtés

Relevé le 2026-09-23, catalogue par défaut, conversation neuve :

| | sur `f5651f7` (avant C56) | sur `85104d4` (après C56) |
|---|---|---|
| nœuds | `system → plan → inference → synthesize` | `system → plan → synthesize` |
| système | aucun outil appelé — passe au planificateur | **idem** |
| plan | `predict`, `dataset='titanic'` | `query`, aucune source |
| synthèse | inférence | **clarification** |
| réponse | « Je ne peux pas encore lancer la prédiction titanic : sex manquante… » | « J'ai accès à 2 source(s) de données : titanic… iris… » |

Le plancher des dates ne s'est déclenché ni avant ni après : la trace le dit
(`aucun outil appelé`), et l'inventaire n'arrive pas par lui. Il arrive par
`_regle_choisir_la_source`, qui pose le catalogue en question dès qu'une
`query` ressort sans source. **L'oracle `exigence_hors_de_l_inventaire` a donc
attrapé ce qu'il devait attraper, par une porte qu'il ne surveillait pas.**

### La cause est le contrat de sortie, et il y en a deux

C56 a changé trois choses. La troisième — la clé de jointure déclarée
(`9f87d81`) — ne touche pas ce chemin. Les deux autres touchent toutes deux le
JSON Schema que le planificateur lit : le champ `Plan.sources` (`2fa8c8b`) et le
retrait de la `description` que pydantic tirait de la docstring (`aa0c1c4`).

Les quatre cases, mesurées ensemble, planificateur seul, prompt composé comme le
nœud du plan le compose, trois tirages par case, la même question :

| champ `sources` | description tirée de la docstring | capacité rendue |
|---|---|---|
| absent | présente | `predict`, `dataset='titanic'` — **3/3** |
| absent | absente | `query`, sans source — **3/3** |
| présent | présente | `query`, `source='titanic'` — **3/3** |
| présent | absente (le contrat d'aujourd'hui) | `query`, sans source — **3/3** |

**Le vert d'avant tenait à la CONJONCTION des deux**, et l'une d'elles était une
phrase écrite pour qui lit le code, promue en texte de schéma par pydantic sans
que personne l'ait décidé. Retirer l'une OU l'autre suffisait à le perdre. Ce
n'est donc pas « C56 a cassé un témoin » : c'est « un témoin reposait sur un
équilibre que personne ne tenait ».

L'ordre de déclaration n'y est pour rien — `sources` déclaré en dernier rend
`query` 3/3 lui aussi. Et on ne répare pas en rendant une phrase au schéma :
`aa0c1c4` a mesuré ce que cette phrase-là coûte au croisement (6/6 dans chaque
sens), et une troisième rédaction, c'est chercher celle qui plaît au modèle du
jour.

### Ce qui répare : une règle du plan, deux conditions

`_regle_relire_une_requete_en_prediction` relit en `predict` une `query` qui
ressort **sans source**, quand deux conditions sont réunies :

1. le message parle de prédire (`introspection.demande_une_prediction`, un
   radical, `predi`) ;
2. il nomme les attributs d'**un** modèle du registre, et d'un seul
   (`modele_designe_par_ses_features`, lu dans `SCHEMAS`). « un âge » désigne
   `titanic`, qui déclare `age` ; `california_housing` déclare `house_age`, qui
   est un autre mot.

Aucune ne suffit seule, et c'est ce qui tient les voisines : « quel est l'âge du
passager le plus âgé ? » nomme l'attribut et ne parle pas de prédire ; « quelle
est la précision de tes prédictions ? » parle de prédire et ne nomme aucun
attribut.

**Le prix est borné par l'endroit où la règle est posée** : juste avant
`_regle_choisir_la_source`, elle ne voit que les tours qu'on allait renvoyer en
proposition. Ce qui se troque est une question posée à l'utilisateur contre une
réponse — jamais une réponse contre une autre. Un périmètre croisé la ferme, une
source retenue la ferme, un catalogue d'une seule source la ferme.

Ni prompt ni fiche d'outil n'a bougé.

### Le relevé de la surface, deux passes

Mesuré le 2026-09-23, catalogue par défaut (`sources/catalogue.yaml` —
`titanic`, `iris`), moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), conversation neuve par question.

| passe | questions méta | témoins | total |
|---|---|---|---|
| 1 | 37/38 | 6/6 | **43/44** |
| 2 | 38/38 | 6/6 | **44/44** |

**`temoin-quand-je-te-donne-un-age` est vert 2/2**, et par le chemin d'avant :
`predict` sur `titanic`, puis la relance de slot-filling — « Je ne peux pas
encore lancer la prédiction titanic : sex manquante… ». La réponse est celle du
2026-09-22 sur `f5651f7`, au mot près.

**Le seul rouge est `choix-entre-deux-sources`, et il est rouge une passe sur
deux** : le bord bistable que C55 avait décrit est toujours là, et il est
confirmé bistable. À la passe où il tombe, le planificateur laisse `source`
vide et `_regle_choisir_la_source` sert l'inventaire entier, fiches comprises ;
à l'autre, il empaquette les deux noms et la question du choix part en une
ligne. **Ce bord n'est pas réparé ici**, et le dire vaut mieux que de l'arrondir
— la réparation qui s'impose (ne proposer QUE les sources que le message nomme,
comme le fait déjà `_plancher_des_sources_nommees`) touche `_proposer`, donc
`mesure_choix_de_source` et `mesure_ambiguite_de_source`, qui deviendraient dues.
