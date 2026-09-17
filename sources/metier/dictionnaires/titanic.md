# Dictionnaire — `titanic` (fichier CSV, 891 lignes)

La liste des passagers du Titanic, dans la forme qu'en donne le jeu de données
public : 891 passagers, leur signalement et leur survie. **Ce fichier n'a aucun
rapport avec les Cycles du Ponant.** Il est dans ce catalogue parce que c'est un
jeu de **référence**, dont les chiffres sont connus hors de cet agent, et parce
qu'il sert à montrer la prédiction.

Il est **recopié tel quel** depuis `sources/titanic.csv`. Ses en-têtes sont en
anglais et en CamelCase, et ils le restent : les traduire ferait mentir la
comparaison avec la littérature et avec le modèle entraîné du registre.

## Les colonnes

Une table plate, sans clé étrangère, sans date. Une ligne = un passager.

| Colonne | Sens |
|---|---|
| `PassengerId` | Numéro de ligne, de 1 à 891. Sans signification métier. |
| `Survived` | **Cible.** `1` a survécu, `0` n'a pas survécu. C'est un indicateur, pas un compte : on le **somme** pour obtenir le nombre de survivants, on le **moyenne** pour obtenir le taux. |
| `Pclass` | Classe du billet : `1`, `2` ou `3`. `1` est la première classe — le chiffre le plus bas est la classe la plus chère. |
| `Name` | Nom complet, avec le titre de civilité. |
| `Sex` | `male` ou `female`. |
| `Age` | Âge en années. **177 lignes sur 891 sont VIDES.** Voir ci-dessous. |
| `SibSp` | Nombre de frères, soeurs et conjoints à bord. |
| `Parch` | Nombre de parents et enfants à bord. |
| `Ticket` | Numéro de billet, format libre, non unique. |
| `Fare` | Prix du billet, en **livres sterling de 1912**. |
| `Cabin` | Numéro de cabine. **687 lignes sur 891 sont vides** — la cabine n'est renseignée quasiment que pour la première classe. |
| `Embarked` | Port d'embarquement : `S` Southampton, `C` Cherbourg, `Q` Queenstown. **2 lignes sont vides.** |

## Les valeurs manquantes sont VIDES, pas sentinelles

`Age`, `Cabin` et `Embarked` portent des cellules **vides** : ni `-1`, ni `999`,
ni `0`. Une moyenne d'âge les écarte donc toute seule — `avg()` en SQL et
`mean()` en pandas ignorent le vide — et il n'y a **aucun filtre à poser**. Un
`WHERE Age > 0` ajouté par précaution ne changerait rien ici, mais il masquerait
le fait qu'on ne sait pas l'âge de 177 passagers.

En revanche, le **dénominateur** change selon la colonne : l'âge moyen se calcule
sur 714 passagers, pas sur 891. Un taux de survie, lui, se calcule bien sur les
891, puisque `Survived` est complète.

## Ce qu'il faut savoir avant de l'interroger

| Question | Réponse juste |
|---|---|
| combien de passagers ? | **891** |
| combien de survivants ? | **342**, soit **38,4 %** |
| âge moyen ? | **29,70 ans**, sur les 714 âges renseignés |
| taux de survie des femmes / des hommes | **74,2 %** / **18,9 %** |
| taux de survie par classe | 1re **63,0 %**, 2e **47,3 %**, 3e **24,2 %** |

Le sexe et la classe sont les deux variables qui portent l'essentiel du signal.

## La prédiction

La source déclare ses `features` pour le dataset `titanic` du registre
(`models/registry.yaml`), et la correspondance n'est **pas** l'identité : le
modèle attend `sex`, `pclass`, `age`, `sibsp`, `parch`, `fare`, `embarked`,
là où le fichier porte `Sex`, `Pclass`, `Age`, `SibSp`, `Parch`, `Fare`,
`Embarked`. C'est le catalogue qui fait la correspondance, pas l'agent : il n'y
a rien à deviner.
