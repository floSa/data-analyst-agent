# Rédiger un dictionnaire de source pour cet agent

Une source du catalogue peut déclarer un **dictionnaire** : un fichier Markdown
libre qui dit ce que ses données veulent dire. Le schéma donne les types ; le
dictionnaire donne le sens — les codes, les valeurs sentinelles, les unités, et
les jointures qui ne passent pas par la clé qu'on croit.

Ce texte a **deux lecteurs**. Une personne qui découvre la base, et l'agent :
celui qui écrit le SQL et celui qui écrit le Python le reçoivent tous les deux
dans leur prompt système, avant d'écrire une ligne.

**C'est une contrainte de produit, et elle est mesurée.** Un dictionnaire
impeccable pour le premier lecteur peut faire rendre des chiffres faux au
second. Ce n'est pas un défaut qu'un meilleur prompt répare — sept formulations
de consigne y ont échoué, et le relevé est plus bas. Ce qui décide, c'est le
texte lui-même.

> **À qui s'adresse ce document.** À qui branche une source sur cet agent, chez
> nous ou chez un client. Il ne demande pas d'écrire pour une machine : il
> demande de lever les ambiguïtés qu'un humain lève tout seul.

---

## La règle, en une phrase

**Dis quel filtre se pose pour quelle question, pas seulement ce que les codes
veulent dire.**

Un dictionnaire descriptif — « `T` signifie terminée » — laisse l'agent arbitrer
entre ce qu'il lit et ce qu'il croit, et c'est cet arbitrage qu'il rate. Un
dictionnaire prescriptif — « pour compter les recharges, `WHERE statut = 'T'` ;
pour sommer l'énergie, aucun filtre » — ne laisse rien à arbitrer.

## Les sept règles d'écriture

### 1. Une règle par défaut d'abord, ses exceptions ensuite — jamais l'inverse

L'agent retient ce qui est **mis en avant**. Une section qui énonce son cas le
plus fréquent en tête, en gras, avec un exemple de requête, puis range le
contre-cas dans un paragraphe de fin, sera lue comme si le contre-cas n'existait
pas. C'est le défaut mesuré ci-dessous, et il coûte 26 695,51 kWh sur un total
de 1 757 519,23.

Écris donc, dans cet ordre : la règle qui s'applique **par défaut**, puis
**l'exception**, nommée comme telle, avec le mot de la question qui la
déclenche. Et lis la **règle 7** avant de choisir ce mot : nommer les mots
déclencheurs est utile, et le faire mal fabrique une contradiction.

```markdown
**Règle par défaut : AUCUN filtre sur `statut`.** Tout comptage, tout classement
et toute somme portent sur les 48 000 sessions, les trois codes confondus.

**L'unique exception :** la question demande explicitement les recharges
**abouties** — « abouties », « réussies », « terminées », « au statut `T` ».
On pose alors `WHERE statut = 'T'`, et dans ce cas seulement.
```

### 2. Sépare ce qu'on COMPTE de ce qu'on SOMME

C'est la confusion la plus coûteuse, parce qu'elle est invisible. « Seules les
sessions au statut `T` ont abouti » dit lesquelles **compter** comme un succès ;
elle ne dit rien de ce qu'il faut **sommer**. Une session interrompue n'a pas
abouti et a bel et bien livré des kilowattheures.

Si une colonne de code gouverne un comptage, dis explicitement ce qu'elle fait —
ou ne fait pas — dans une somme, une moyenne et un classement. Chacun
séparément : l'agent ne généralise pas de l'un à l'autre.

### 3. Nomme les valeurs sentinelles, et dis ce qui leur ressemble sans en être

Un `-1`, un `999`, une date à `1900-01-01` : ces valeurs ne sont pas des
mesures, ce sont des absences de mesure écrites dans la colonne. `dropna()` ne
les voit pas et `avg()` les moyenne sans broncher.

Dis-le, et dis aussi **ce qui leur ressemble et qui est vrai** — sans quoi
l'agent sur-corrige et fausse le résultat dans l'autre sens :

```markdown
**`-1` est une valeur sentinelle** — le compteur n'a rien remonté — et n'est pas
une puissance : à écarter de toute moyenne. `0` en est une, elle : la borne
répond et ne charge personne.
```

### 4. Une table de correspondance ne remplace pas une règle

Mesuré : une table à quatre entrées disant exactement la même chose que « une
règle, une exception » passait trois questions et en cassait une quatrième ; la
reformuler pour couvrir le classement réparait celle-là et cassait une autre.
L'agent y cherchait **la ligne qui ressemble le plus à sa question**, et une
question nouvelle tombait entre deux lignes.

Une table est un bon **complément** — elle montre les chiffres justes et les
chiffres faux côte à côte, ce qui est précieux pour le lecteur humain. Elle ne
tient pas lieu de règle.

### 5. Le format ne porte aucune autorité

Le gras, les titres, l'ordre des paragraphes : rien de tout cela ne hiérarchise
les consignes pour l'agent. Seul le **contenu des phrases** compte. N'écris pas
une exception en italique en espérant qu'elle passe pour secondaire, et ne
compte pas sur un gras pour qu'une règle l'emporte.

### 6. Mets les pièges là où tu veux, mais tiens le budget

Le dictionnaire est recopié entier dans le prompt tant qu'il tient sous
`DAA_DICTIONARY_MAX_CHARS` (8 000 caractères par défaut, ~2 550 tokens). Au-delà,
il est amputé **par sections entières**, dans l'ordre du document, et
l'amputation est annoncée au modèle comme à l'utilisateur.

Une section qui ne tient pas dans le budget restant est sautée, pas tronquée :
une demi-règle est pire que pas de règle du tout. Mais si ton dictionnaire
dépasse, ce sont les sections de fin qui sautent — et chez nous, les pièges sont
en fin de document. Vérifie donc la taille, ou relève le plafond en connaissance
de cause.

### 7. Un mot qui déclenche une exception ne peut pas être le mot ORDINAIRE de la chose

La règle 1 demande de nommer les mots de la question qui déclenchent
l'exception. Cette règle-ci la borne, et elle a coûté une formulation sur dix.

Le dictionnaire d'`exploitation` disait, en gras, dans le corps de sa section :
« Le nombre de recharges réelles est `WHERE statut = 'T'` ». Et il disait, deux
paragraphes plus bas : « **Règle par défaut : AUCUN filtre sur `statut`.** Tout
comptage, tout classement et toute somme… ». Les deux phrases sont vraies, et
elles se contredisent sur un mot : **recharge**. C'est à la fois le mot ordinaire
d'une ligne de `sessions` — n'importe quel statut — et le mot que le texte
associe au filtre.

« Sur quelles stations y a-t-il eu le plus de **recharges** ? » tombe exactement
sur cette faille. Un lecteur humain lève l'ambiguïté tout seul : il lit
« recharges **réelles** » comme un rétrécissement, et « le plus de recharges »
comme le cas par défaut. Rien ne garantit que le modèle fasse la même lecture, et
un `WHERE statut = 'T'` posé là fausse les comptes du classement — 907 / 818 /
759 au lieu de 1 015 / 911 / 872.

Le remède n'est pas d'allonger la liste des mots déclencheurs, qui est justement
le pari que ce produit a déjà payé deux fois ailleurs. C'est de rendre le mot
ordinaire **inutilisable** comme déclencheur :

- choisis les mots de l'exception parmi ceux que le cas par défaut n'emploie
  JAMAIS. « Abouties », « réussies », « terminées » qualifient un
  aboutissement : elles ne désignent aucun comptage ordinaire. « Recharges »,
  si ;
- et surtout, **dis l'exception comme une propriété** et non comme une liste :
  « la question porte sur l'ABOUTISSEMENT — elle distingue ce qui a réussi de ce
  qui a été tenté ». Une propriété couvre les tournures que personne n'a
  écrites ; une liste couvre celles qu'on a pensées ;
- relis alors le corps de ta section avec cette question : **un mot y
  a-t-il deux sens ?** Si oui, un des deux emplois doit changer de mot. Dans le
  cas ci-dessus, « le nombre de recharges réelles » devient « le nombre de
  sessions **abouties** », et le mot « recharge » n'est plus qu'un synonyme de
  session.

C'est le même raisonnement que les six autres règles, appliqué au VOCABULAIRE
plutôt qu'à l'ordre des paragraphes : le format ne porte aucune autorité
(règle 5), et un mot qui porte deux sens n'en porte aucune non plus.

> **Ce que la mesure dit, et ce qu'elle ne dit pas.** Ce défaut a été observé
> une fois — `F10` du runner de classement, 0/3 le 2026-09-15 — et il ne se
> reproduit plus : 12/12 le 2026-09-16, à code et dictionnaire identiques. Il
> est donc INTERMITTENT, ce qui ne le rend pas moins réel : une contradiction
> dans le texte laisse au modèle un choix qu'il ne devrait pas avoir, et il le
> tranchera différemment d'un tirage à l'autre. La règle est écrite pour cela.
> Le dictionnaire d'`exploitation`, lui, est corrigé — et la correction est
> mesurée en même temps que tout le reste.

---

## Le relevé qui fonde cette contrainte

Moteur : vLLM, `google/gemma-4-E4B-it-qat-w4a16-ct`, `http://localhost:8100/v1`.
Source `exploitation` du catalogue de démonstration. Runner :
[`scripts/mesure_dictionnaire_redige_pour_un_humain.py`](../scripts/mesure_dictionnaire_redige_pour_un_humain.py).

Le dictionnaire d'`exploitation` a été **écrit pour une personne**, puis réécrit
pour lever ses ambiguïtés. Le texte d'avant est dans git
(`git show 2160276:sources/demonstration/dictionnaires/exploitation.md`). Il est
correct : tout ce qu'il dit est vrai, et sa section « pièges » nomme le
contre-cas de l'énergie. Il énonce simplement sa règle de comptage en tête et
son contre-cas quatre lignes plus bas.

### Ce que le texte d'avant rend, avec la consigne en service

Quatre questions, trois tirages chacune. Le verdict lit le **chiffre** rendu et
le **SQL** de la trace : sur un classement, l'absence de chiffre ne dit rien du
filtre, mais le SQL le dit toujours.

| Question | Oracle | Filtre `statut` | Obtenu | Tirages justes |
|---|---|---|---|---|
| « combien de sessions en tout ? » | 48 000 | aucun | 48 000 | **3/3** |
| « quelle énergie totale, en kWh, a été délivrée sur l'année ? » | 1 757 519,23 | aucun | **1 730 823,72** avec `WHERE statut = 'T'` | **0/3** |
| « les trois stations avec le plus de sessions » | 1 015 / 911 / 872 | aucun | les trois, dans l'ordre, sans leurs comptes | 3/3 au SQL |
| « combien de recharges ont réellement abouti ? » | 42 281 | `= 'T'` | 42 281 | **3/3** |

Une seule question fausse, donc — et c'est déjà une question sur quatre, sur
un chiffre qui part en facturation.

### Les sept formulations de consigne, et ce qu'elles donnent

Trois ont été essayées au chantier précédent, quatre ici. Toutes sur la question
de l'énergie, trois tirages chacune, sur le texte d'avant.

| # | Ce qui a été essayé | Résultat |
|---|---|---|
| 1 | la consigne d'origine — « le dictionnaire fait autorité » | 0/3 |
| 2 | la même, renforcée | 0/3 |
| 3 | une consigne délibérément **neutre**, pour tester l'hypothèse inverse | 0/3 |
| 4 | la consigne en service, qui exige de citer la phrase **pour la grandeur demandée** | 0/3 |
| 5 | + « l'exception est écrite APRÈS la règle », avec ses tournures nommées (« attention aussi », « au sens inverse »…) | 0/3 |
| 6 | une **procédure obligatoire** à dérouler en commentaires SQL avant d'écrire | 0/3, et pire : le modèle écrit la procédure et **n'appelle plus l'outil** |
| 7 | un **rappel placé APRÈS le dictionnaire**, là où la récence joue | 0/3 — le filtre sur `statut` disparaît, un filtre sur l'année en cours le remplace, et la réponse devient « énergie nulle » |

La n° 7 est la plus instructive : elle **déplace** l'erreur sans la supprimer.
Le prompt décide de quelle faute est commise ; il ne décide pas qu'aucune ne
l'est.

### Le témoin qui tranche

Même consigne n° 7, même question, même moteur, même moment — seul le
dictionnaire change :

| Dictionnaire | Résultat |
|---|---|
| le texte d'**avant** (écrit pour une personne) | **0/3** |
| le texte **réécrit** (une règle, une exception) | **3/3** — 1 757 519,23 |

La variable qui décide est le **texte de la source**, pas la formulation du
prompt. C'est ce qui fait de cette page une contrainte de produit et non une
tâche de mise au point.

---

## Ce que ça coûte quand on ne le fait pas

Un dictionnaire ambigu ne produit **ni exception, ni log, ni ralentissement**.
Il produit un chiffre faux et plausible, dans une réponse bien formulée, que
personne ne verra passer :

| Question | Ce qu'on croit lire | Ce qui est vrai | Écart |
|---|---|---|---|
| énergie totale délivrée | 1 730 823,72 kWh | 1 757 519,23 kWh | −26 695,51 kWh |
| sessions en tout | 42 281 | 48 000 | −5 719 |
| le palmarès des stations | 907 / 818 / 759 | 1 015 / 911 / 872 | des rangs qui peuvent basculer |

Et côté code d'analyse, où il n'y a même pas de requête à relire :

| Question | Ce qu'on croit lire | Ce qui est vrai |
|---|---|---|
| puissance moyenne relevée | 66,32 kW | 68,33 kW |
| la courbe horaire | une courbe deux kilowatts trop basse | — |

## La liste de contrôle

Avant de brancher une source, relis son dictionnaire en te posant ces questions.

- [ ] Pour chaque colonne de **code**, ai-je dit quel filtre se pose **pour
      quelle question** — et pas seulement ce que les codes signifient ?
- [ ] Ai-je écrit la **règle par défaut avant** l'exception ?
- [ ] Ai-je traité séparément le **comptage**, la **somme**, la **moyenne** et le
      **classement** ?
- [ ] Ai-je nommé les **valeurs sentinelles**, et dit ce qui leur ressemble sans
      en être ?
- [ ] Mes règles tiennent-elles dans des **phrases**, et pas seulement dans un
      tableau de cas ?
- [ ] Les mots qui déclenchent une **exception** sont-ils absents du cas par
      défaut — aucun mot à deux sens dans la même section ?
- [ ] Le fichier tient-il sous `DAA_DICTIONARY_MAX_CHARS` ?

Et pour vérifier plutôt que croire : écris trois ou quatre questions dont tu
connais la réponse hors de l'agent, dont au moins une qui **ne doit pas** être
filtrée, et joue-les. C'est ce que fait
[`scripts/mesure_dictionnaire_redige_pour_un_humain.py`](../scripts/mesure_dictionnaire_redige_pour_un_humain.py),
qui prend un dictionnaire de substitution en argument sans rien écrire dans le
dépôt.

## Où ça vit dans le code

| Quoi | Où |
|---|---|
| La déclaration d'une source | `sources/**/catalogue.yaml`, clé `dictionary` |
| La règle de coupe, le budget, les deux en-têtes | [`src/data_analyst_agent/agents/dictionnaire.py`](../src/data_analyst_agent/agents/dictionnaire.py) |
| Le prompt de l'agent SQL | `agents/retrieval/agent.py`, `composer_le_prompt` |
| Le prompt de l'agent d'analyse | `agents/analysis/agent.py`, `composer_le_prompt` |
| Le plafond | `DAA_DICTIONARY_MAX_CHARS` (8 000) |

Les cinq dictionnaires du catalogue de démonstration sont dans
`sources/demonstration/dictionnaires/` et servent d'exemples travaillés. Celui
d'`exploitation` porte la règle-et-son-exception ; celui de `telemetrie` porte
la sentinelle et ce qui lui ressemble.
