# Un dictionnaire rédigé pour un humain, mesuré

Ce journal consigne la mesure qui fonde les règles de rédaction des dictionnaires : un dictionnaire correct pour un lecteur humain peut produire des chiffres faux, et aucune formulation du prompt ne corrige ce défaut.

Les règles en vigueur : [rediger-un-dictionnaire-de-source.md](../rediger-un-dictionnaire-de-source.md).

## Le relevé

Moteur : vLLM, `google/gemma-4-E4B-it-qat-w4a16-ct`, `http://localhost:8100/v1`.
Source `exploitation` du catalogue de démonstration. Runner :
[`scripts/mesure_dictionnaire_redige_pour_un_humain.py`](../../scripts/mesure_dictionnaire_redige_pour_un_humain.py).

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
