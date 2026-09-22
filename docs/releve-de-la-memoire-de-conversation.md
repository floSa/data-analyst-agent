# Le relevé de la mémoire de conversation

Ce document est ÉCRIT PAR `scripts/mesure_memoire_de_conversation.py`. Il n'est
pas rédigé à la main, et toute correction qu'on y apporterait serait perdue à la
prochaine exécution. Il ne juge rien : il relève ce qui EST. C'est la matière
de `docs/memoire-de-conversation.md`, qui, lui, se lit.

Moteur : `http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`)

Catalogues : `sources/catalogue.yaml` (fil A), `sources/demonstration/catalogue.yaml` (fils B à F)

Plafonds : `DAA_RETRIEVAL_MAX_ROWS=200`, `DAA_CONTEXT_ARTIFACT_WINDOW=8`, `DAA_CONTEXT_CODE_WINDOW=8`, `DAA_CONTEXT_TOKEN_BUDGET=8000`

    uv run python scripts/mesure_memoire_de_conversation.py \
        --markdown docs/releve-de-la-memoire-de-conversation.md

## Ce que la campagne établit, en chiffres

### Les chemins : repartir d'un objet, ou refaire

| chemin | tours | part |
|---|---|---|
| repart d'un objet déjà produit | 15 | 24 % |
| relance un SQL sur une source | 42 | 67 % |
| les deux dans le même tour | 0 | 0 % |
| ni l'un ni l'autre | 6 | 10 % |
| rematérialise la base en CSV | 9 | 14 % |
| **total mesuré** | **63** | (hors amorces) |

### Ce que l'inventaire coûte en contexte, et où il entre

Ce qui entre RÉELLEMENT dans un prompt, en caractères :

| nœud | ce qui est injecté | tours concernés | médiane | max |
|---|---|---|---|---|
| `analysis` | `montage_sandbox` | 9 | 196 car. | 224 car. |
| `plan` | `describe` | 33 | 886 car. | 1548 car. |
| `rappel` | `catalogue_du_rappel` | 42 | 481 car. | 1467 car. |

Nœuds du graphe où **aucun texte** de la mémoire de conversation n'est entré pendant toute la campagne : `retrieval`, `inference` (jamais traversé), `fetch_predict` (jamais traversé), `system`, `synthesize`. Cela ne veut pas dire qu'ils l'ignorent : certains reçoivent les tableaux du fil comme des sources interrogeables, sans qu'aucune ligne de catalogue n'entre dans leur prompt (tableau plus bas).

Rapporté au prompt qui la porte : le catalogue des objets pèse **14 % du prompt du planificateur** en médiane sur les 33 tours où il est injecté, et jusqu'à **22 %** (tour F.8 · tirage 1). Les deux termes sont pris en tokens estimés, comme le budget qui les arbitre.

La **pesée du budget** (`fit_to_budget`, nœud `plan`) a redemandé ce même catalogue 66 fois pour le mesurer sans l'envoyer — médiane 886 car., max 1548 car. Elle n'est pas comptée ci-dessus.

Les tableaux du fil exposés comme **sources interrogeables** (`_effective_catalog`), par nœud demandeur :

| nœud | tours où au moins une source éphémère est exposée |
|---|---|
| `analysis` | 6 |
| `plan` | 33 |
| `rappel` | 3 |
| `retrieval` | 27 |
| `system` | 45 |

### Le drapeau `tronque`

| étape du drapeau | compte | où on le lit |
|---|---|---|
| **écrit** — tableaux du magasin portant `tronque: true` | 6 / 42 tableaux produits | `manifest.json` |
| **lu** — tours dont un prompt porte la mention `TRONQUÉ` | 6 | montage `/data/` du nœud `analysis` |
| **dit** — tours dont la réponse servie porte le mot | 12 | texte rendu à l'utilisateur |
| dont **dit sans requête neuve** — la tranche est celle d'un tour passé | 6 | texte rendu à l'utilisateur |

## Les six fils, tour par tour

### Fil A — Repartir du tableau, pas de la base

Catalogue : `sources/catalogue.yaml`. 3 tirage(s), 2 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : le tour 2 repart-il du tableau du tour 1, ou relance-t-il un SQL complet sur la base ? Les DEUX aboutissent peut-être à une bonne figure : ce n'est pas le résultat qu'on mesure ici, c'est le CHEMIN.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur titanic | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | extrais-moi les passagères qui ont survécu | system → plan → retrieval → synthesize | query · source=titanic | oui | resultat_1(200l·tronqué) | 0 car. |
| 1 | 2 | fais-moi un graphique de leur âge | system → rappel → plan → analysis → synthesize | analyze · source=titanic | — | resultat_1(200l·tronqué), graphique_1 | 1188 car. |
| 2 | 0 (amorce) | travaillons sur titanic | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | extrais-moi les passagères qui ont survécu | system → plan → retrieval → synthesize | query · source=titanic | oui | resultat_1(200l·tronqué) | 0 car. |
| 2 | 2 | fais-moi un graphique de leur âge | system → rappel → plan → analysis → synthesize | analyze · source=titanic | — | resultat_1(200l·tronqué), graphique_1 | 1188 car. |
| 3 | 0 (amorce) | travaillons sur titanic | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | extrais-moi les passagères qui ont survécu | system → plan → retrieval → synthesize | query · source=titanic | oui | resultat_1(200l·tronqué) | 0 car. |
| 3 | 2 | fais-moi un graphique de leur âge | system → rappel → plan → analysis → synthesize | analyze · source=titanic | — | resultat_1(200l·tronqué), graphique_1 | 1188 car. |

#### A.0 · tirage 1 — « travaillons sur titanic » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source, sans quoi le tour 1 n'interroge rien.

**0 appel(s) LLM**, 146 ms, source liée après le tour : `titanic`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : titanic — sans appel LLM | 142 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe.
>
> 2 table(s), 894 ligne(s) (classes : 3, passengers : 891)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### A.1 · tirage 1 — « extrais-moi les passagères qui ont survécu »

Ce qu'on cherche : un tableau, et on verra s'il est tronqué.

**5 appel(s) LLM**, 3187 ms, plan : `query · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 460 |
| `plan` | query sur titanic *(prompt : 1540 tokens estimés, 1434 évalués)* | 820 |
| `retrieval` | SELECT * FROM passengers WHERE survived = 1 AND sex = 'female' | 1900 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les passagères qui ont survécu', source='titanic'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM passengers WHERE survived = 1 AND sex = 'female'"` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM passengers WHERE survived = 1 AND sex = 'female'
```

Réponse servie :

> 200 lignes retournées — voir le tableau ci-dessous. (résultat tronqué par la limite de lignes)

#### A.2 · tirage 1 — « fais-moi un graphique de leur âge »

Ce qu'on cherche : le chemin : SQL neuf sur la base, ou lecture du tableau d'avant.

**5 appel(s) LLM**, 17593 ms, plan : `analyze · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | aucun outil appelé — passe au planificateur | 316 |
| `plan` | analyze sur titanic *(prompt : 1868 tokens estimés, 1690 évalués)* | 990 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 14560 |
| `synthesize` | LLM | 1608 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="graphique de l'âge des passagers dans resultat_1", source='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 349 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 643 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 643 car. |
| `plan` | `_peser_le_prompt` | `describe` | 643 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 196 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/classes.csv` | table de la base |
| `/data/passengers.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Charger les données
try:
    df_passengers = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    exit()

# Vérifier les données manquantes dans la colonne 'age'
print(f"Nombre de valeurs NaN dans 'age' avant traitement: {df_passengers['age'].isnull().sum()}")

# Imputer ou supprimer les valeurs manquantes pour l'âge.
# Étant donné qu'il s'agit d'un graphique de distribution, le remplacement par la moyenne est une approche courante.
# Cependant, pour respecter la règle de ne pas laisser de NaN, nous allons les supprimer pour ce graphique simple.
df_clean = df_passengers.dropna(subset=['age'])

# Vérifier le nombre de lignes après nettoyage
print(f"Nombre de lignes après suppression des NaN dans 'age': {len(df_clean)}")

# Créer le histogramme de la distribution de l'âge
plt.figure(figsize=(10, 6))
plt.hist(df_clean['age'], bins=30, edgecolor='black', alpha=0.7)

# Ajouter les titres et les étiquettes
plt.title('Distribution de l\'âge des passagers (Échantillon tronqué)')
plt.xlabel('Âge')
plt.ylabel('Fréquence')
plt.grid(axis='y', alpha=0.5)

# Afficher le graphique
plt.show()

# Afficher quelques statistiques descriptives de l'âge pour le rapport
mean_age = df_clean['age'].mean()
median_age = df_clean['age'].median()
std_age = df_clean['age'].std()

print(f"Âge moyen (après nettoyage): {mean_age:.2f}")
print(f"Âge médian (après nettoyage): {median_age:.2f}")
print(f"Écart-type de l'âge (après nettoyage): {std_age:.2f}")
```

Réponse servie :

> Un graphique de l'âge a été généré (ci-joint). Après le nettoyage des données, il y avait 35 valeurs manquantes dans la colonne 'age', laissant 165 lignes. L'âge moyen calculé est de 28.08 ans, avec un écart-type de 13.96 ans.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

#### A.0 · tirage 2 — « travaillons sur titanic » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source, sans quoi le tour 1 n'interroge rien.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `titanic`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : titanic — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe.
>
> 2 table(s), 894 ligne(s) (classes : 3, passengers : 891)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### A.1 · tirage 2 — « extrais-moi les passagères qui ont survécu »

Ce qu'on cherche : un tableau, et on verra s'il est tronqué.

**5 appel(s) LLM**, 3334 ms, plan : `query · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 135 |
| `plan` | query sur titanic *(prompt : 1540 tokens estimés, 1434 évalués)* | 1011 |
| `retrieval` | SELECT * FROM passengers WHERE survived = 1 AND sex = 'female' | 2180 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les passagères qui ont survécu', source='titanic'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM passengers WHERE survived = 1 AND sex = 'female'"` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM passengers WHERE survived = 1 AND sex = 'female'
```

Réponse servie :

> 200 lignes retournées — voir le tableau ci-dessous. (résultat tronqué par la limite de lignes)

#### A.2 · tirage 2 — « fais-moi un graphique de leur âge »

Ce qu'on cherche : le chemin : SQL neuf sur la base, ou lecture du tableau d'avant.

**5 appel(s) LLM**, 17635 ms, plan : `analyze · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 103 |
| `rappel` | aucun outil appelé — passe au planificateur | 88 |
| `plan` | analyze sur titanic *(prompt : 1868 tokens estimés, 1690 évalués)* | 784 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 15208 |
| `synthesize` | LLM | 1442 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="graphique de l'âge des passagers dans resultat_1", source='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 349 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 643 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 643 car. |
| `plan` | `_peser_le_prompt` | `describe` | 643 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 196 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/classes.csv` | table de la base |
| `/data/passengers.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Charger les données
try:
    df_passengers = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    exit()

# Vérifier les données manquantes dans la colonne 'age'
print(f"Nombre de valeurs NaN dans 'age' avant traitement: {df_passengers['age'].isnull().sum()}")

# Imputer ou supprimer les valeurs manquantes pour l'âge.
# Étant donné qu'il s'agit d'un graphique de distribution, le remplacement par la moyenne est une approche courante.
# Cependant, pour respecter la règle de ne pas laisser de NaN, nous allons supprimer les lignes avec NaN dans 'age'
# car l'imputation pourrait biaiser la distribution observée dans cet échantillon tronqué.
df_clean = df_passengers.dropna(subset=['age'])

print(f"Nombre de lignes après suppression des NaN dans 'age': {len(df_clean)}")

# Créer le histogramme de l'âge
plt.figure(figsize=(10, 6))
plt.hist(df_clean['age'], bins=20, edgecolor='black', alpha=0.7)

# Ajouter les titres et les étiquettes
plt.title('Distribution de l\'âge des passagers (Échantillon tronqué)')
plt.xlabel('Âge')
plt.ylabel('Fréquence')
plt.grid(axis='y', alpha=0.5)

# Afficher le graphique
plt.show()

# Afficher quelques statistiques descriptives de l'âge pour le rapport
mean_age = df_clean['age'].mean()
median_age = df_clean['age'].median()
std_age = df_clean['age'].std()

print(f"Moyenne de l'âge (sur les données non-NaN): {mean_age:.2f}")
print(f"Médiane de l'âge (sur les données non-NaN): {median_age:.2f}")
print(f"Écart-type de l'âge (sur les données non-NaN): {std_age:.2f}")
```

Réponse servie :

> Un graphique de l'âge a été généré (ci-joint). Les données traitées comprenaient 165 lignes après suppression des 35 valeurs manquantes dans la colonne 'age'. La moyenne de l'âge observée est de 28.08 ans, avec une médiane de 28.00 ans et un écart-type de 13.96.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

#### A.0 · tirage 3 — « travaillons sur titanic » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source, sans quoi le tour 1 n'interroge rien.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `titanic`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : titanic — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe.
>
> 2 table(s), 894 ligne(s) (classes : 3, passengers : 891)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### A.1 · tirage 3 — « extrais-moi les passagères qui ont survécu »

Ce qu'on cherche : un tableau, et on verra s'il est tronqué.

**5 appel(s) LLM**, 2242 ms, plan : `query · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `plan` | query sur titanic *(prompt : 1540 tokens estimés, 1434 évalués)* | 645 |
| `retrieval` | SELECT * FROM passengers WHERE survived = 1 AND sex = 'female' | 1483 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les passagères qui ont survécu', source='titanic'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM passengers WHERE survived = 1 AND sex = 'female'"` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM passengers WHERE survived = 1 AND sex = 'female'
```

Réponse servie :

> 200 lignes retournées — voir le tableau ci-dessous. (résultat tronqué par la limite de lignes)

#### A.2 · tirage 3 — « fais-moi un graphique de leur âge »

Ce qu'on cherche : le chemin : SQL neuf sur la base, ou lecture du tableau d'avant.

**5 appel(s) LLM**, 21107 ms, plan : `analyze · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 100 |
| `rappel` | aucun outil appelé — passe au planificateur | 87 |
| `plan` | analyze sur titanic *(prompt : 1868 tokens estimés, 1690 évalués)* | 784 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 18398 |
| `synthesize` | LLM | 1726 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="graphique de l'âge des passagers dans resultat_1", source='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 349 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 643 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 643 car. |
| `plan` | `_peser_le_prompt` | `describe` | 643 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 196 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/classes.csv` | table de la base |
| `/data/passengers.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Charger les données
try:
    df_passengers = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    exit()

# Vérifier les données manquantes dans la colonne 'age'
print(f"Nombre de valeurs NaN dans 'age' avant traitement: {df_passengers['age'].isnull().sum()}")

# Imputer ou supprimer les valeurs manquantes pour l'âge.
# Étant donné qu'il s'agit d'un graphique de distribution, le remplacement par la moyenne est une approche courante.
# Cependant, pour respecter la règle de ne pas laisser de NaN, nous allons supprimer les lignes avec NaN dans 'age'
# car l'imputation pourrait biaiser la distribution observée dans cet échantillon tronqué.
df_clean = df_passengers.dropna(subset=['age'])

print(f"Nombre de lignes après suppression des NaN dans 'age': {len(df_clean)}")

# Créer le histogramme de l'âge
plt.figure(figsize=(10, 6))
plt.hist(df_clean['age'], bins=20, edgecolor='black', alpha=0.7)

# Ajouter les titres et les étiquettes
plt.title('Distribution de l\'âge des passagers (Échantillon tronqué)')
plt.xlabel('Âge')
plt.ylabel('Fréquence')
plt.grid(axis='y', alpha=0.5)

# Afficher le graphique
plt.show()

# Afficher quelques statistiques descriptives de l'âge pour le rapport
mean_age = df_clean['age'].mean()
median_age = df_clean['age'].median()
std_age = df_clean['age'].std()

print(f"Moyenne de l'âge (sur les données non-NaN): {mean_age:.2f}")
print(f"Médiane de l'âge (sur les données non-NaN): {median_age:.2f}")
print(f"Écart-type de l'âge (sur les données non-NaN): {std_age:.2f}")
```

Réponse servie :

> Un graphique de l'âge a été généré (ci-joint). Les données traitées comprenaient 165 lignes après suppression des 35 valeurs manquantes dans la colonne 'age'. La moyenne de l'âge observée est de 28.08 ans, avec une médiane de 28.00 ans et un écart-type de 13.96.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

### Fil B — L'inventaire, su et dit

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 4 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : sait-il énumérer ses objets, les distinguer, et dire ce que chacun contient — ou invente-t-il, ou se tait-il ?.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | les 10 interventions les plus longues, avec leur station et  […] | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 1 | 2 | fais-moi un graphique du nombre d'interventions par nature | system → rappel → plan → analysis → synthesize | analyze · source=interventions | — | resultat_1(10l), graphique_1 | 1115 car. |
| 1 | 3 | qu'est-ce que tu as en mémoire dans cette conversation ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | — | resultat_1(10l), graphique_1 | 1351 car. |
| 1 | 4 | et le premier tableau, il contenait quoi ? | system → rappel → synthesize | — | — | resultat_1(10l), graphique_1 | 318 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | les 10 interventions les plus longues, avec leur station et  […] | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | fais-moi un graphique du nombre d'interventions par nature | system → rappel → plan → analysis → synthesize | analyze · source=interventions | — | resultat_1(10l), graphique_1 | 1115 car. |
| 2 | 3 | qu'est-ce que tu as en mémoire dans cette conversation ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | — | resultat_1(10l), graphique_1 | 1351 car. |
| 2 | 4 | et le premier tableau, il contenait quoi ? | system → rappel → synthesize | — | — | resultat_1(10l), graphique_1 | 318 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | les 10 interventions les plus longues, avec leur station et  […] | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | fais-moi un graphique du nombre d'interventions par nature | system → rappel → plan → analysis → synthesize | analyze · source=interventions | — | resultat_1(10l), graphique_1 | 1115 car. |
| 3 | 3 | qu'est-ce que tu as en mémoire dans cette conversation ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | — | resultat_1(10l), graphique_1 | 1351 car. |
| 3 | 4 | et le premier tableau, il contenait quoi ? | system → rappel → synthesize | — | — | resultat_1(10l), graphique_1 | 318 car. |

#### B.0 · tirage 1 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 89 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 84 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### B.1 · tirage 1 — « les 10 interventions les plus longues, avec leur station et leur nature »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 8059 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `plan` | query sur interventions *(prompt : 1794 tokens estimés, 1637 évalués)* | 1484 |
| `retrieval` | SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 6459 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `get_schema` | `—` |
| 2 | `run_sql` | `query='SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.2 · tirage 1 — « fais-moi un graphique du nombre d'interventions par nature »

Ce qu'on cherche : une figure.

**5 appel(s) LLM**, 22076 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 126 |
| `rappel` | aucun outil appelé — passe au planificateur | 321 |
| `plan` | analyze sur interventions *(prompt : 2132 tokens estimés, 1890 évalués)* | 853 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 19079 |
| `synthesize` | LLM | 1686 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="nombre d'interventions par nature", source='interventions'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 331 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 635 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 635 car. |
| `plan` | `_peser_le_prompt` | `describe` | 635 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 149 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt

# Charger le fichier de données spécifié
try:
    df_resultat_1 = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    exit()

# --- Application des règles de filtrage basées sur le Dictionnaire ---

# 1. Filtrage sur 'duree_indispo_min' :
# Règle : "duree_indispo_min = -1 veut dire « non renseigné » ... Le filtre juste est WHERE duree_indispo_min >= 0."
# Application : Écarter les lignes où duree_indispo_min est -1.
df_filtered = df_resultat_1[df_resultat_1['duree_indispo_min'] >= 0].copy()

# 2. Filtrage sur 'nature' :
# Règle : "Une intervention n'est pas une panne de borne ... la seule façon d'isoler les pannes est de filtrer sur ces libellés, en les citant."
# Le dictionnaire liste les natures possibles : 'borne hors service', 'câble endommagé', 'écran illisible', 'défaut de paiement carte', 'communication réseau perdue', 'maintenance préventive'.
# Nous devons filtrer pour ne garder que les pannes (exclure 'maintenance préventive').
natures_panne = [
    'borne hors service', 
    'câble endommagé', 
    'écran illisible', 
    'défaut de paiement carte', 
    'communication réseau perdue'
]
df_filtered = df_filtered[df_filtered['nature'].isin(natures_panne)].copy()

# 3. Nettoyage des valeurs manquantes pour la catégorisation
# Règle : "Écarte-les (dropna) ou remplace-les explicitement (fillna) AVANT de tracer ou d'agréger une variable catégorielle"
# Appliquer dropna sur la colonne 'nature' avant le comptage.
df_filtered.dropna(subset=['nature'], inplace=True)

# --- Agrégation des données ---
# Calcul du nombre d'interventions par nature
counts_by_nature = df_filtered['nature'].value_counts()

# --- Visualisation ---
plt.figure(figsize=(10, 6))
counts_by_nature.plot(kind='bar', color='skyblue')
plt.title('Nombre d\'Interventions par Nature (Pannes uniquement)')
plt.xlabel('Nature de l\'Intervention')
plt.ylabel('Nombre d\'Interventions')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.show()

# Affichage des résultats agrégés
print("Comptage des interventions par nature (après filtrage) :")
print(counts_by_nature.to_string())
```

Réponse servie :

> Un graphique illustrant le nombre d'interventions par nature a été produit (ci-joint). Les données montrent que la nature "écran illisible" représente le plus grand nombre d'interventions avec 3 cas. Les autres natures, "défaut de paiement carte" et "câble endommagé", comptent chacune 2 interventions. Enfin, "borne hors service" et "communication réseau perdue" totalisent 1 intervention chacune.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.3 · tirage 1 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

Ce qu'on cherche : l'inventaire de ses propres objets.

**4 appel(s) LLM**, 5071 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 180 |
| `rappel` | aucun outil appelé — passe au planificateur | 495 |
| `plan` | query sur interventions *(prompt : 2211 tokens estimés, 1954 évalués)* | 2563 |
| `retrieval` | 0 requête(s), aucune n'a abouti | 1823 |
| `synthesize` | réponse non fondée, écartée (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='Quels sont les objets intermédiaires et les codes Python produits dans cette conversation ?', reason="L'utilisateur demande un résumé de ce qui a été produit ou mémo […]` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 465 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 886 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 886 car. |
| `plan` | `_peser_le_prompt` | `describe` | 886 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en affirmer. Reformule en précisant ce que tu veux en savoir (par exemple : « combien de lignes ? », « quelles colonnes ? »).

#### B.4 · tirage 1 — « et le premier tableau, il contenait quoi ? »

Ce qu'on cherche : le contenu d'un objet désigné par son rang, pas par son nom.

**4 appel(s) LLM**, 1206 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 158 |
| `rappel` | lire_un_artefact — faits servis (sentinelle rendue alors qu'un outil a été appelé) | 1044 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 318 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> resultat_1 (tableau de 10 ligne(s) ; colonnes : station_libelle, nature, duree_indispo_min) :
> station_libelle,nature,duree_indispo_min
> Montpellier — Port,maintenance préventive,2880
> Reims — Stade,défaut de paiement carte,2876
> Le Havre — Marché,borne hors service,2874
> Colmar — Parc des Expos,câble endommagé,2865
> Le Havre — Marché,écran illisible,2848
> Strasbourg — Hôpital,écran illisible,2845
> Évreux — Centre,communication réseau perdue,2844
> Vannes — Port,défaut de paiement carte,2842
> Le Havre — Zone Nord,câble endommagé,2841
> Le Havre — Marché,écran illisible,2837

#### B.0 · tirage 2 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### B.1 · tirage 2 — « les 10 interventions les plus longues, avec leur station et leur nature »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 8764 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 105 |
| `plan` | query sur interventions *(prompt : 1794 tokens estimés, 1637 évalués)* | 1486 |
| `retrieval` | SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 7164 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `get_schema` | `—` |
| 2 | `run_sql` | `query='SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.2 · tirage 2 — « fais-moi un graphique du nombre d'interventions par nature »

Ce qu'on cherche : une figure.

**5 appel(s) LLM**, 25220 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 143 |
| `rappel` | aucun outil appelé — passe au planificateur | 114 |
| `plan` | analyze sur interventions *(prompt : 2132 tokens estimés, 1890 évalués)* | 1041 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 22293 |
| `synthesize` | LLM | 1618 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="nombre d'interventions par nature", source='interventions'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 331 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 635 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 635 car. |
| `plan` | `_peser_le_prompt` | `describe` | 635 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 149 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt

# Charger le fichier de données spécifié
try:
    df_resultat_1 = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    exit()

# --- Application des règles de filtrage basées sur le Dictionnaire ---

# 1. Filtrage sur 'duree_indispo_min' :
# Règle : "duree_indispo_min = -1 veut dire « non renseigné » ... Le filtre juste est WHERE duree_indispo_min >= 0."
# Application : Écarter les lignes où duree_indispo_min est -1.
df_filtered = df_resultat_1[df_resultat_1['duree_indispo_min'] >= 0].copy()

# 2. Filtrage sur 'nature' :
# Règle : "Une intervention n'est pas une panne de borne ... la seule façon d'isoler les pannes est de filtrer sur ces libellés, en les citant."
# Le dictionnaire liste les natures possibles : 'borne hors service', 'câble endommagé', 'écran illisible', 'défaut de paiement carte', 'communication réseau perdue', 'maintenance préventive'.
# Nous devons filtrer pour ne garder que les pannes (exclure 'maintenance préventive').
# Nous allons supposer que toutes les autres natures listées sont des pannes, et exclure explicitement 'maintenance préventive'.
natures_a_garder = [
    'borne hors service', 
    'câble endommagé', 
    'écran illisible', 
    'défaut de paiement carte', 
    'communication réseau perdue'
]
df_filtered = df_filtered[df_filtered['nature'].isin(natures_a_garder)].copy()

# 3. Gestion des valeurs manquantes dans les colon utilisées pour l'agrégation
# Règle : "Écarte-les (dropna) ou remplace-les explicitement (fillna) AVANT de tracer ou d'agréger une variable catégorielle."
# Nous vérifions les colon 'nature' (catégorielle) et 'duree_indispo_min' (bien que filtrée, on sécurise).
df_filtered.dropna(subset=['nature'], inplace=True)

# --- Agrégation ---
# Calcul du nombre d'interventions par nature
nature_counts = df_filtered['nature'].value_counts()

# --- Visualisation ---
plt.figure(figsize=(10, 6))
nature_counts.plot(kind='bar', color='skyblue')
plt.title('Nombre d\'Interventions par Nature (Pannes uniquement)')
plt.xlabel('Nature de l\'Intervention')
plt.ylabel('Nombre d\'Interventions')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.show()

# Affichage des résultats agrégés
print("Comptage des interventions par nature (après filtrage) :")
print(nature_counts.to_string())
```

Réponse servie :

> Un graphique illustrant le nombre d'interventions par nature a été produit (ci-joint). Les données montrent que la nature "écran illisible" représente le plus grand nombre d'interventions avec 3 cas. Les autres natures, "défaut de paiement carte" et "câble endommagé", comptent chacune 2 interventions. Enfin, "borne hors service" et "communication réseau perdue" totalisent 1 intervention chacune.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.3 · tirage 2 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

Ce qu'on cherche : l'inventaire de ses propres objets.

**4 appel(s) LLM**, 2852 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 94 |
| `rappel` | aucun outil appelé — passe au planificateur | 83 |
| `plan` | query sur interventions *(prompt : 2211 tokens estimés, 1954 évalués)* | 1778 |
| `retrieval` | 0 requête(s), aucune n'a abouti | 889 |
| `synthesize` | réponse non fondée, écartée (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='Quels sont les objets intermédiaires et les codes Python produits dans cette conversation ?', reason="L'utilisateur demande un résumé de ce qui a été produit et mémo […]` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 465 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 886 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 886 car. |
| `plan` | `_peser_le_prompt` | `describe` | 886 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en affirmer. Reformule en précisant ce que tu veux en savoir (par exemple : « combien de lignes ? », « quelles colonnes ? »).

#### B.4 · tirage 2 — « et le premier tableau, il contenait quoi ? »

Ce qu'on cherche : le contenu d'un objet désigné par son rang, pas par son nom.

**4 appel(s) LLM**, 1878 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 102 |
| `rappel` | lire_un_artefact — formulé par le modèle | 1772 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 318 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je suis désolé, mais je ne comprends pas votre demande. Pour que je puisse vous aider, pourriez-vous me dire ce que vous souhaitez faire avec le tableau `resultat_1` ? Par exemple, voulez-vous le modifier, en tirer des statistiques, ou autre chose ?

#### B.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 4 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### B.1 · tirage 3 — « les 10 interventions les plus longues, avec leur station et leur nature »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 9378 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 136 |
| `plan` | query sur interventions *(prompt : 1794 tokens estimés, 1637 évalués)* | 2403 |
| `retrieval` | SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 6830 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `get_schema` | `—` |
| 2 | `run_sql` | `query='SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.2 · tirage 3 — « fais-moi un graphique du nombre d'interventions par nature »

Ce qu'on cherche : une figure.

**5 appel(s) LLM**, 23775 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 106 |
| `rappel` | aucun outil appelé — passe au planificateur | 91 |
| `plan` | analyze sur interventions *(prompt : 2132 tokens estimés, 1890 évalués)* | 654 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 21301 |
| `synthesize` | LLM | 1614 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="nombre d'interventions par nature", source='interventions'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 331 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 635 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 635 car. |
| `plan` | `_peser_le_prompt` | `describe` | 635 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 149 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt

# Charger le fichier de données spécifié
try:
    df_resultat_1 = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    exit()

# --- Application des règles de filtrage basées sur le Dictionnaire ---

# 1. Filtrage sur 'duree_indispo_min' :
# Règle : "duree_indispo_min = -1 veut dire « non renseigné » ... Le filtre juste est WHERE duree_indispo_min >= 0."
# Application : Écarter les lignes où duree_indispo_min est -1.
df_filtered = df_resultat_1[df_resultat_1['duree_indispo_min'] >= 0].copy()

# 2. Filtrage sur 'nature' :
# Règle : "Une intervention n'est pas une panne de borne ... la seule façon d'isoler les pannes est de filtrer sur ces libellés, en les citant."
# Le dictionnaire liste les natures possibles : 'borne hors service', 'câble endommagé', 'écran illisible', 'défaut de paiement carte', 'communication réseau perdue', 'maintenance préventive'.
# Nous devons filtrer pour ne garder que les pannes (exclure 'maintenance préventive').
# Nous allons supposer que toutes les autres natures listées sont des pannes, et exclure explicitement 'maintenance préventive'.
natures_a_garder = [
    'borne hors service', 
    'câble endommagé', 
    'écran illisible', 
    'défaut de paiement carte', 
    'communication réseau perdue'
]
df_filtered = df_filtered[df_filtered['nature'].isin(natures_a_garder)].copy()

# 3. Gestion des valeurs manquantes dans les colon utilisées pour l'agrégation
# Règle : "Écarte-les (dropna) ou remplace-les explicitement (fillna) AVANT de tracer ou d'agréger une variable catégorielle."
# Nous vérifions les colon 'nature' (catégorielle) et 'duree_indispo_min' (bien que filtrée, on sécurise).
df_filtered.dropna(subset=['nature'], inplace=True)

# --- Agrégation ---
# Calcul du nombre d'interventions par nature
nature_counts = df_filtered['nature'].value_counts()

# --- Visualisation ---
plt.figure(figsize=(10, 6))
nature_counts.plot(kind='bar', color='skyblue')
plt.title('Nombre d\'Interventions par Nature (Pannes uniquement)')
plt.xlabel('Nature de l\'Intervention')
plt.ylabel('Nombre d\'Interventions')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.show()

# Affichage des résultats agrégés
print("Comptage des interventions par nature (après filtrage) :")
print(nature_counts.to_string())
```

Réponse servie :

> Un graphique illustrant le nombre d'interventions par nature a été produit (ci-joint). Les données montrent que la nature "écran illisible" représente le plus grand nombre d'interventions avec 3 cas. Les autres natures, "défaut de paiement carte" et "câble endommagé", comptent chacune 2 interventions. Enfin, "borne hors service" et "communication réseau perdue" totalisent 1 intervention chacune.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.3 · tirage 3 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

Ce qu'on cherche : l'inventaire de ses propres objets.

**4 appel(s) LLM**, 2829 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 79 |
| `rappel` | aucun outil appelé — passe au planificateur | 74 |
| `plan` | query sur interventions *(prompt : 2211 tokens estimés, 1954 évalués)* | 1781 |
| `retrieval` | 0 requête(s), aucune n'a abouti | 887 |
| `synthesize` | réponse non fondée, écartée (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='Quels sont les objets intermédiaires et les codes Python produits dans cette conversation ?', reason="L'utilisateur demande un résumé de ce qui a été produit et mémo […]` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 465 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 886 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 886 car. |
| `plan` | `_peser_le_prompt` | `describe` | 886 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en affirmer. Reformule en précisant ce que tu veux en savoir (par exemple : « combien de lignes ? », « quelles colonnes ? »).

#### B.4 · tirage 3 — « et le premier tableau, il contenait quoi ? »

Ce qu'on cherche : le contenu d'un objet désigné par son rang, pas par son nom.

**4 appel(s) LLM**, 1592 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | lire_un_artefact — formulé par le modèle | 1481 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 318 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je suis désolé, mais je ne comprends pas votre demande. Pour que je puisse vous aider, pourriez-vous me dire ce que vous souhaitez faire avec le tableau `resultat_1` ? Par exemple, voulez-vous le modifier, en tirer des statistiques, ou autre chose ?

### Fil C — Sources primaires et sources transformées

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 2 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : distingue-t-il les sources du catalogue de celles que la conversation a fabriquées ? Les nomme-t-il toutes les deux ?.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur referentiel | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | donne-moi les 10 stations les plus récemment mises en servic […] | system → plan → retrieval → synthesize | query · source=referentiel | oui | resultat_1(10l) | 0 car. |
| 1 | 2 | quelles données as-tu à ta disposition maintenant ? | system → synthesize | — | — | resultat_1(10l) | 0 car. |
| 2 | 0 (amorce) | travaillons sur referentiel | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | donne-moi les 10 stations les plus récemment mises en servic […] | system → plan → retrieval → synthesize | query · source=referentiel | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | quelles données as-tu à ta disposition maintenant ? | system → synthesize | — | — | resultat_1(10l) | 0 car. |
| 3 | 0 (amorce) | travaillons sur referentiel | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | donne-moi les 10 stations les plus récemment mises en servic […] | system → plan → retrieval → synthesize | query · source=referentiel | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | quelles données as-tu à ta disposition maintenant ? | system → synthesize | — | — | resultat_1(10l) | 0 car. |

#### C.0 · tirage 1 — « travaillons sur referentiel » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 66 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : referentiel — sans appel LLM | 61 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **referentiel** (file) — Référentiel des stations (fichier CSV, une table plate) — code, libellé, région, statut et date de mise en service des 150 stations, DÉMONTÉES COMPRISES. C'est la table de correspondance entre libellés et codes.
>
> 1 table(s), 150 ligne(s) (referentiel : 150) — période couverte : du 2019-01-01 au 2024-06-14 (colonne date_mise_en_service de referentiel)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### C.1 · tirage 1 — « donne-moi les 10 stations les plus récemment mises en service »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 3707 ms, plan : `query · source=referentiel`, source liée après le tour : `referentiel`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 128 |
| `plan` | query sur referentiel *(prompt : 1791 tokens estimés, 1638 évalués)* | 1731 |
| `retrieval` | SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10 | 1840 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10', reason="La demande est une requête directe pour obtenir une liste filtrée et triée de données […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `date_mise_en_service` (ordre décroissant) — voir le tableau ci-dessous.

#### C.2 · tirage 1 — « quelles données as-tu à ta disposition maintenant ? »

Ce qu'on cherche : le catalogue déclaré, le tableau fabriqué, ou les deux.

**2 appel(s) LLM**, 2357 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 2354 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les informations sur les stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> Que souhaitez-vous savoir sur cette source ?

#### C.0 · tirage 2 — « travaillons sur referentiel » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 4 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : referentiel — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **referentiel** (file) — Référentiel des stations (fichier CSV, une table plate) — code, libellé, région, statut et date de mise en service des 150 stations, DÉMONTÉES COMPRISES. C'est la table de correspondance entre libellés et codes.
>
> 1 table(s), 150 ligne(s) (referentiel : 150) — période couverte : du 2019-01-01 au 2024-06-14 (colonne date_mise_en_service de referentiel)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### C.1 · tirage 2 — « donne-moi les 10 stations les plus récemment mises en service »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 3078 ms, plan : `query · source=referentiel`, source liée après le tour : `referentiel`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 109 |
| `plan` | query sur referentiel *(prompt : 1791 tokens estimés, 1638 évalués)* | 1571 |
| `retrieval` | SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10 | 1390 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10', reason="La demande est une requête directe pour obtenir une liste filtrée et triée de données […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `date_mise_en_service` (ordre décroissant) — voir le tableau ci-dessous.

#### C.2 · tirage 2 — « quelles données as-tu à ta disposition maintenant ? »

Ce qu'on cherche : le catalogue déclaré, le tableau fabriqué, ou les deux.

**2 appel(s) LLM**, 1766 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 1763 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les informations sur les stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> Que souhaitez-vous savoir sur cette source ?

#### C.0 · tirage 3 — « travaillons sur referentiel » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : referentiel — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **referentiel** (file) — Référentiel des stations (fichier CSV, une table plate) — code, libellé, région, statut et date de mise en service des 150 stations, DÉMONTÉES COMPRISES. C'est la table de correspondance entre libellés et codes.
>
> 1 table(s), 150 ligne(s) (referentiel : 150) — période couverte : du 2019-01-01 au 2024-06-14 (colonne date_mise_en_service de referentiel)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### C.1 · tirage 3 — « donne-moi les 10 stations les plus récemment mises en service »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 3991 ms, plan : `query · source=referentiel`, source liée après le tour : `referentiel`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 130 |
| `plan` | query sur referentiel *(prompt : 1791 tokens estimés, 1638 évalués)* | 2485 |
| `retrieval` | SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10 | 1368 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10', reason="La demande est une requête directe pour obtenir une liste filtrée et triée de données […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `date_mise_en_service` (ordre décroissant) — voir le tableau ci-dessous.

#### C.2 · tirage 3 — « quelles données as-tu à ta disposition maintenant ? »

Ce qu'on cherche : le catalogue déclaré, le tableau fabriqué, ou les deux.

**2 appel(s) LLM**, 1651 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 1648 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les informations sur les stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> Que souhaitez-vous savoir sur cette source ?

### Fil D — La tranche

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 2 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : dit-il que le tableau est tronqué, ou donne-t-il le compte de la tranche comme s'il était le total ? Le drapeau existe dans le magasin ; la question est s'il arrive à l'utilisateur.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | liste-moi toutes les interventions de maintenance | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(200l·tronqué) | 0 car. |
| 1 | 2 | combien de lignes dans ce tableau ? | system → rappel → synthesize | analyze · source=interventions | — | resultat_1(200l·tronqué), analyse_1 | 608 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | liste-moi toutes les interventions de maintenance | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(200l·tronqué) | 0 car. |
| 2 | 2 | combien de lignes dans ce tableau ? | system → rappel → synthesize | analyze · source=interventions | — | resultat_1(200l·tronqué), analyse_1 | 608 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | liste-moi toutes les interventions de maintenance | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(200l·tronqué) | 0 car. |
| 3 | 2 | combien de lignes dans ce tableau ? | system → rappel → synthesize | analyze · source=interventions | — | resultat_1(200l·tronqué), analyse_1 | 608 car. |

#### D.0 · tirage 1 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### D.1 · tirage 1 — « liste-moi toutes les interventions de maintenance »

Ce qu'on cherche : 900 lignes en source, 200 au plafond : le tableau est une tranche.

**5 appel(s) LLM**, 3055 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 1305 |
| `plan` | query sur interventions *(prompt : 1787 tokens estimés, 1630 évalués)* | 618 |
| `retrieval` | SELECT * FROM interventions | 1125 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='liste toutes les interventions de maintenance', source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions
```

Réponse servie :

> 200 lignes retournées — voir le tableau ci-dessous. (résultat tronqué par la limite de lignes)

#### D.2 · tirage 1 — « combien de lignes dans ce tableau ? »

Ce qu'on cherche : 200 (la tranche), 900 (le tout), ou 200 en le DISANT.

**6 appel(s) LLM**, 14025 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 105 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 12794 |
| `synthesize` | LLM | 1118 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='nombre de lignes', nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 384 car. |
| `rappel` | `_rejouer_un_code` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 224 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd

# Charger le fichier de données spécifié
try:
    df = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    df = None

if df is not None:
    # La demande est de calculer à partir de ce fichier chargé, sans réinterroger la source,
    # et d'imprimer le résultat complet (le DataFrame lui-même).
    # Ensuite, imprimer le nombre de lignes.

    print("--- DataFrame complet (resultat_1) ---")
    print(df)

    # Imprimer le nombre de lignes
    num_lignes = len(df)
    print(f"\nNombre de lignes dans le DataFrame : {num_lignes}")
else:
    print("Opération annulée en raison de l'échec du chargement du DataFrame.")
```

Réponse servie :

> Le tableau contient 200 lignes, comme indiqué par l'information `[200 rows x 7 columns]`. Le nombre de lignes dans le DataFrame est confirmé à 200.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

#### D.0 · tirage 2 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 4 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### D.1 · tirage 2 — « liste-moi toutes les interventions de maintenance »

Ce qu'on cherche : 900 lignes en source, 200 au plafond : le tableau est une tranche.

**5 appel(s) LLM**, 2886 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 1303 |
| `plan` | query sur interventions *(prompt : 1787 tokens estimés, 1630 évalués)* | 625 |
| `retrieval` | SELECT * FROM interventions | 951 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='liste toutes les interventions de maintenance', source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions
```

Réponse servie :

> 200 lignes retournées — voir le tableau ci-dessous. (résultat tronqué par la limite de lignes)

#### D.2 · tirage 2 — « combien de lignes dans ce tableau ? »

Ce qu'on cherche : 200 (la tranche), 900 (le tout), ou 200 en le DISANT.

**6 appel(s) LLM**, 13146 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 12284 |
| `synthesize` | LLM | 748 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='nombre de lignes', nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 384 car. |
| `rappel` | `_rejouer_un_code` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 224 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd

# Charger le fichier de données spécifié
try:
    df = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    df = None

if df is not None:
    # La demande est de calculer à partir de ce fichier chargé, sans réinterroger la source,
    # et d'imprimer le résultat complet (le DataFrame lui-même).
    # Ensuite, imprimer le nombre de lignes.

    print("--- DataFrame complet (resultat_1) ---")
    print(df)

    # Imprimer le nombre de lignes
    num_lignes = len(df)
    print(f"\nNombre de lignes dans le DataFrame : {num_lignes}")
else:
    print("Opération annulée en raison de l'échec du chargement du DataFrame.")
```

Réponse servie :

> Le tableau contient 200 lignes, comme indiqué par l'information `[200 rows x 7 columns]`. Le nombre de lignes dans le DataFrame est confirmé à 200.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

#### D.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### D.1 · tirage 3 — « liste-moi toutes les interventions de maintenance »

Ce qu'on cherche : 900 lignes en source, 200 au plafond : le tableau est une tranche.

**5 appel(s) LLM**, 2892 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 1304 |
| `plan` | query sur interventions *(prompt : 1787 tokens estimés, 1630 évalués)* | 633 |
| `retrieval` | SELECT * FROM interventions | 948 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='liste toutes les interventions de maintenance', source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions
```

Réponse servie :

> 200 lignes retournées — voir le tableau ci-dessous. (résultat tronqué par la limite de lignes)

#### D.2 · tirage 3 — « combien de lignes dans ce tableau ? »

Ce qu'on cherche : 200 (la tranche), 900 (le tout), ou 200 en le DISANT.

**6 appel(s) LLM**, 13062 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 100 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 12212 |
| `synthesize` | LLM | 742 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='nombre de lignes', nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 384 car. |
| `rappel` | `_rejouer_un_code` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 224 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd

# Charger le fichier de données spécifié
try:
    df = pd.read_csv('/data/resultat_1.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/resultat_1.csv n'a pas été trouvé.")
    df = None

if df is not None:
    # La demande est de calculer à partir de ce fichier chargé, sans réinterroger la source,
    # et d'imprimer le résultat complet (le DataFrame lui-même).
    # Ensuite, imprimer le nombre de lignes.

    print("--- DataFrame complet (resultat_1) ---")
    print(df)

    # Imprimer le nombre de lignes
    num_lignes = len(df)
    print(f"\nNombre de lignes dans le DataFrame : {num_lignes}")
else:
    print("Opération annulée en raison de l'échec du chargement du DataFrame.")
```

Réponse servie :

> Le tableau contient 200 lignes, comme indiqué par l'information `[200 rows x 7 columns]`. Le nombre de lignes dans le DataFrame est confirmé à 200.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

### Fil E — Témoin — ce qui ne doit PAS repartir de la mémoire

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 2 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : le tour 2 doit interroger la base, pas le tableau d'à côté.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 1 | 2 | dans facturation, combien de factures ont été émises en 2025 […] | system → rappel → plan → retrieval → synthesize | query · source=facturation | oui | resultat_1(10l), resultat_2(1l) | 1026 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | dans facturation, combien de factures ont été émises en 2025 […] | system → rappel → plan → retrieval → synthesize | query · source=facturation | oui | resultat_1(10l), resultat_2(1l) | 1026 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | dans facturation, combien de factures ont été émises en 2025 […] | system → rappel → plan → retrieval → synthesize | query · source=facturation | oui | resultat_1(10l), resultat_2(1l) | 1026 car. |

#### E.0 · tirage 1 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### E.1 · tirage 1 — « les 10 interventions les plus longues »

Ce qu'on cherche : un tableau sur interventions.

**5 appel(s) LLM**, 4407 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 100 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1310 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2989 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM interventions ORDER BY dure_indisponibilite DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 interventions les plus longues dans la sour […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.

#### E.2 · tirage 1 — « dans facturation, combien de factures ont été émises en 2025 ? »

Ce qu'on cherche : une autre source, sans rapport : le tableau d'à côté ne doit pas servir.

**6 appel(s) LLM**, 3689 ms, plan : `query · source=facturation`, source liée après le tour : `facturation`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 110 |
| `rappel` | aucun outil appelé — passe au planificateur | 319 |
| `plan` | query sur facturation *(prompt : 2132 tokens estimés, 1897 évalués)* | 987 |
| `retrieval` | SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025' | 2264 |
| `synthesize` | résumé de la récupération | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='compter le nombre de factures émises en 2025', source='facturation'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 361 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `_peser_le_prompt` | `describe` | 665 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'
```

Réponse servie :

> Je passe sur la source `facturation` — on travaillait sur `interventions`.
>
> La table `factures` contient 1200 factures émises en 2025, car la colonne `date_emission` est utilisée pour déterminer l'année d'émission.
>
> Ce qu'en dit le dictionnaire de `facturation` :
> > Ces relations **ne sont pas déclarées** : un classeur ne porte ni clé primaire ni clé étrangère. `lignes_facture.facture_id` désigne bien `factures.facture_id`, mais c'est le dictionnaire qui le dit, pas le schéma. C'est exactement ce que la base DuckDB `telemetrie`, elle, sait déclarer.
> > Feuille `factures` — 1 200 lignes

#### E.0 · tirage 2 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### E.1 · tirage 2 — « les 10 interventions les plus longues »

Ce qu'on cherche : un tableau sur interventions.

**5 appel(s) LLM**, 3502 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 102 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1315 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2077 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM interventions ORDER BY dure_indisponibilite DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 interventions les plus longues dans la sour […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.

#### E.2 · tirage 2 — « dans facturation, combien de factures ont été émises en 2025 ? »

Ce qu'on cherche : une autre source, sans rapport : le tableau d'à côté ne doit pas servir.

**6 appel(s) LLM**, 2556 ms, plan : `query · source=facturation`, source liée après le tour : `facturation`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 105 |
| `rappel` | aucun outil appelé — passe au planificateur | 90 |
| `plan` | query sur facturation *(prompt : 2132 tokens estimés, 1897 évalués)* | 790 |
| `retrieval` | SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025' | 1562 |
| `synthesize` | résumé de la récupération | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='compter le nombre de factures émises en 2025', source='facturation'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 361 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `_peser_le_prompt` | `describe` | 665 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'
```

Réponse servie :

> Je passe sur la source `facturation` — on travaillait sur `interventions`.
>
> Il y a eu 1200 factures émises en 2025.
>
> Ce qu'en dit le dictionnaire de `facturation` :
> > Ces relations **ne sont pas déclarées** : un classeur ne porte ni clé primaire ni clé étrangère. `lignes_facture.facture_id` désigne bien `factures.facture_id`, mais c'est le dictionnaire qui le dit, pas le schéma. C'est exactement ce que la base DuckDB `telemetrie`, elle, sait déclarer.
> > Feuille `factures` — 1 200 lignes

#### E.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 4 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### E.1 · tirage 3 — « les 10 interventions les plus longues »

Ce qu'on cherche : un tableau sur interventions.

**5 appel(s) LLM**, 4309 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 104 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1315 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2882 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM interventions ORDER BY dure_indisponibilite DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 interventions les plus longues dans la sour […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.

#### E.2 · tirage 3 — « dans facturation, combien de factures ont été émises en 2025 ? »

Ce qu'on cherche : une autre source, sans rapport : le tableau d'à côté ne doit pas servir.

**6 appel(s) LLM**, 2675 ms, plan : `query · source=facturation`, source liée après le tour : `facturation`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 105 |
| `rappel` | aucun outil appelé — passe au planificateur | 90 |
| `plan` | query sur facturation *(prompt : 2132 tokens estimés, 1897 évalués)* | 791 |
| `retrieval` | SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025' | 1680 |
| `synthesize` | résumé de la récupération | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='compter le nombre de factures émises en 2025', source='facturation'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 361 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `_peser_le_prompt` | `describe` | 665 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'
```

Réponse servie :

> Je passe sur la source `facturation` — on travaillait sur `interventions`.
>
> Il y a eu 1200 factures émises en 2025.
>
> Ce qu'en dit le dictionnaire de `facturation` :
> > Ces relations **ne sont pas déclarées** : un classeur ne porte ni clé primaire ni clé étrangère. `lignes_facture.facture_id` désigne bien `factures.facture_id`, mais c'est le dictionnaire qui le dit, pas le schéma. C'est exactement ce que la base DuckDB `telemetrie`, elle, sait déclarer.
> > Feuille `factures` — 1 200 lignes

### Fil F — Témoin — le fil long

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 9 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : retrouve-t-il encore le tableau du premier tour, et à quel coût en contexte ?.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 1 | 2 | combien d'interventions par nature ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l) | 1026 car. |
| 1 | 3 | les 5 techniciens qui sont intervenus le plus souvent | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l) | 1285 car. |
| 1 | 4 | combien d'interventions par mois de signalement ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l) | 1586 car. |
| 1 | 5 | les 10 stations les plus souvent en panne | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l) | 1869 car. |
| 1 | 6 | la durée moyenne d'indisponibilité par nature | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l) | 2142 car. |
| 1 | 7 | les interventions signalées en décembre 2025 | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l) | 2437 car. |
| 1 | 8 | combien d'interventions par technicien ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 2870 car. |
| 1 | 9 | reprends le tableau du tout premier tour | system → rappel → synthesize | — | — | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 1467 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | combien d'interventions par nature ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l) | 1026 car. |
| 2 | 3 | les 5 techniciens qui sont intervenus le plus souvent | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l) | 1285 car. |
| 2 | 4 | combien d'interventions par mois de signalement ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l) | 1586 car. |
| 2 | 5 | les 10 stations les plus souvent en panne | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l) | 1869 car. |
| 2 | 6 | la durée moyenne d'indisponibilité par nature | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l) | 2142 car. |
| 2 | 7 | les interventions signalées en décembre 2025 | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l) | 2437 car. |
| 2 | 8 | combien d'interventions par technicien ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 2870 car. |
| 2 | 9 | reprends le tableau du tout premier tour | system → rappel → synthesize | — | — | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 1467 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | combien d'interventions par nature ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l) | 1026 car. |
| 3 | 3 | les 5 techniciens qui sont intervenus le plus souvent | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l) | 1285 car. |
| 3 | 4 | combien d'interventions par mois de signalement ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l) | 1586 car. |
| 3 | 5 | les 10 stations les plus souvent en panne | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l) | 1869 car. |
| 3 | 6 | la durée moyenne d'indisponibilité par nature | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l) | 2142 car. |
| 3 | 7 | les interventions signalées en décembre 2025 | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l) | 2437 car. |
| 3 | 8 | combien d'interventions par technicien ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 2870 car. |
| 3 | 9 | reprends le tableau du tout premier tour | system → rappel → synthesize | — | — | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 1467 car. |

#### F.0 · tirage 1 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### F.1 · tirage 1 — « les 10 interventions les plus longues »

Ce qu'on cherche : objet 1.

**5 appel(s) LLM**, 3488 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 102 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1310 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2068 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM interventions ORDER BY dure_indisponibilite DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 interventions les plus longues dans la sour […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.

#### F.2 · tirage 1 — « combien d'interventions par nature ? »

Ce qu'on cherche : objet 2.

**6 appel(s) LLM**, 2866 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 103 |
| `rappel` | aucun outil appelé — passe au planificateur | 90 |
| `plan` | query sur interventions *(prompt : 2123 tokens estimés, 1887 évalués)* | 703 |
| `retrieval` | SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature; | 1961 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par nature", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 361 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `_peser_le_prompt` | `describe` | 665 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature;
```

Réponse servie :

> 6 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### F.3 · tirage 1 — « les 5 techniciens qui sont intervenus le plus souvent »

Ce qu'on cherche : objet 3.

**6 appel(s) LLM**, 5245 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 126 |
| `rappel` | aucun outil appelé — passe au planificateur | 314 |
| `plan` | query sur interventions *(prompt : 2169 tokens estimés, 1923 évalués)* | 1726 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5; | 3072 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 5 techniciens qui sont intervenus le plus souvent', reason="L'utilisateur demande un classement (les 5 plus fréquents) des techniciens, ce qui est une agrégation […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 22 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 497 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 788 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 788 car. |
| `plan` | `_peser_le_prompt` | `describe` | 788 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 22 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 22 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT technicien, COUNT(intervention_id) AS nombre_interventions
FROM interventions
GROUP BY technicien
ORDER BY nombre_interventions DESC
LIMIT 5;
```

Réponse servie :

> 5 lignes retournées, classées par `nombre_interventions` (ordre décroissant) — voir le tableau ci-dessous.

#### F.4 · tirage 1 — « combien d'interventions par mois de signalement ? »

Ce qu'on cherche : objet 4.

**6 appel(s) LLM**, 4133 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 96 |
| `rappel` | aucun outil appelé — passe au planificateur | 317 |
| `plan` | query sur interventions *(prompt : 2222 tokens estimés, 1965 évalués)* | 905 |
| `retrieval` | SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois; | 2807 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="nombre d'interventions par mois de signalement", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois;"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 34 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 654 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 932 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 932 car. |
| `plan` | `_peser_le_prompt` | `describe` | 932 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 34 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 34 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions
FROM interventions
GROUP BY mois
ORDER BY mois;
```

Réponse servie :

> 12 lignes retournées, classées par `mois` (ordre croissant) — voir le tableau ci-dessous.

#### F.5 · tirage 1 — « les 10 stations les plus souvent en panne »

Ce qu'on cherche : objet 5.

**6 appel(s) LLM**, 4746 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 145 |
| `rappel` | aucun outil appelé — passe au planificateur | 498 |
| `plan` | query sur interventions *(prompt : 2263 tokens estimés, 1999 évalués)* | 1062 |
| `retrieval` | SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10; | 3032 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 10 stations les plus souvent en panne', source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 46 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 802 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1067 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1067 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1067 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 46 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 46 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10;
```

Réponse servie :

> 10 lignes retournées, classées par `nombre_panne` (ordre décroissant) — voir le tableau ci-dessous.

#### F.6 · tirage 1 — « la durée moyenne d'indisponibilité par nature »

Ce qu'on cherche : objet 6.

**6 appel(s) LLM**, 8677 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 109 |
| `rappel` | aucun outil appelé — passe au planificateur | 318 |
| `plan` | query sur interventions *(prompt : 2304 tokens estimés, 2038 évalués)* | 1803 |
| `retrieval` | SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature; | 6439 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT nature, AVG(duree_indispo_min) FROM interventions GROUP BY nature', reason="L'utilisateur demande la durée moyenne d'indisponibilité, groupée par la nature de […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 58 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 945 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1197 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1197 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1197 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 58 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 58 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;
```

Réponse servie :

> 6 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### F.7 · tirage 1 — « les interventions signalées en décembre 2025 »

Ce qu'on cherche : objet 7.

**6 appel(s) LLM**, 5317 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 130 |
| `rappel` | aucun outil appelé — passe au planificateur | 324 |
| `plan` | query sur interventions *(prompt : 2353 tokens estimés, 2082 évalués)* | 2338 |
| `retrieval` | SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31' | 2517 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'", reason="L'utilisateur demande une liste d'interventions fil […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 70 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1099 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1338 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1338 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1338 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 70 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 70 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'
```

Réponse servie :

> 81 lignes retournées — voir le tableau ci-dessous.

#### F.8 · tirage 1 — « combien d'interventions par technicien ? »

Ce qu'on cherche : objet 8.

**6 appel(s) LLM**, 2930 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 103 |
| `rappel` | aucun outil appelé — passe au planificateur | 326 |
| `plan` | query sur interventions *(prompt : 2421 tokens estimés, 2142 évalués)* | 880 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien; | 1611 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="nombre d'interventions par technicien", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 82 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1322 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1548 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1548 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1548 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 82 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 82 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;
```

Réponse servie :

> 18 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > La main courante de la maintenance : un signalement par ligne, 900 lignes sur l'année 2025. Saisie par les techniciens, avec ce que ça implique — elle désigne les stations comme on les lit sur le terrain, et laisse des cases vides.
> > `technicien` : Matricule anonymisé, `TECH-nn`.
> > 108 lignes sur 900 portent `-1`, soit environ 12,0 %. Le technicien n'a pas relevé la durée ; le fichier ne sait pas représenter une absence autrement.

#### F.9 · tirage 1 — « reprends le tableau du tout premier tour »

Ce qu'on cherche : huit objets plus tard, le premier est-il encore atteignable ?.

**4 appel(s) LLM**, 2699 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 129 |
| `rappel` | lire_un_artefact — formulé par le modèle | 2566 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 94 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1467 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je ne peux pas "valider" un feedback. Je suis un modèle de langage et je n'ai pas de mécanisme de validation pour les retours utilisateurs.
>
> Si vous souhaitez que je fasse quelque chose avec le tableau `resultat_1` (le tableau des 10 interventions les plus longues), veuillez me donner une instruction claire (par exemple : "Donne-moi la durée moyenne de ces interventions" ou "Quelles sont les stations les plus concernées dans ce tableau ?").

#### F.0 · tirage 2 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 4 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### F.1 · tirage 2 — « les 10 interventions les plus longues »

Ce qu'on cherche : objet 1.

**5 appel(s) LLM**, 4045 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 126 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1568 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2342 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM interventions ORDER BY dure_indisponibilite DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 interventions les plus longues dans la sour […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.

#### F.2 · tirage 2 — « combien d'interventions par nature ? »

Ce qu'on cherche : objet 2.

**6 appel(s) LLM**, 4539 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 149 |
| `rappel` | aucun outil appelé — passe au planificateur | 467 |
| `plan` | query sur interventions *(prompt : 2123 tokens estimés, 1887 évalués)* | 1423 |
| `retrieval` | SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature; | 2490 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par nature", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 361 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `_peser_le_prompt` | `describe` | 665 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature;
```

Réponse servie :

> 6 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### F.3 · tirage 2 — « les 5 techniciens qui sont intervenus le plus souvent »

Ce qu'on cherche : objet 3.

**6 appel(s) LLM**, 4524 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 173 |
| `rappel` | aucun outil appelé — passe au planificateur | 485 |
| `plan` | query sur interventions *(prompt : 2169 tokens estimés, 1923 évalués)* | 1544 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5; | 2313 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 5 techniciens qui sont intervenus le plus souvent', source='resultat_1'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 22 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 497 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 788 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 788 car. |
| `plan` | `_peser_le_prompt` | `describe` | 788 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 22 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 22 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT technicien, COUNT(intervention_id) AS nombre_interventions
FROM interventions
GROUP BY technicien
ORDER BY nombre_interventions DESC
LIMIT 5;
```

Réponse servie :

> 5 lignes retournées, classées par `nombre_interventions` (ordre décroissant) — voir le tableau ci-dessous.

#### F.4 · tirage 2 — « combien d'interventions par mois de signalement ? »

Ce qu'on cherche : objet 4.

**6 appel(s) LLM**, 4776 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 111 |
| `rappel` | aucun outil appelé — passe au planificateur | 314 |
| `plan` | query sur interventions *(prompt : 2222 tokens estimés, 1965 évalués)* | 1031 |
| `retrieval` | SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois; | 3310 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="nombre d'interventions par mois de signalement", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois;"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 34 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 654 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 932 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 932 car. |
| `plan` | `_peser_le_prompt` | `describe` | 932 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 34 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 34 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions
FROM interventions
GROUP BY mois
ORDER BY mois;
```

Réponse servie :

> 12 lignes retournées, classées par `mois` (ordre croissant) — voir le tableau ci-dessous.

#### F.5 · tirage 2 — « les 10 stations les plus souvent en panne »

Ce qu'on cherche : objet 5.

**6 appel(s) LLM**, 6380 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 149 |
| `rappel` | aucun outil appelé — passe au planificateur | 534 |
| `plan` | query sur interventions *(prompt : 2263 tokens estimés, 1999 évalués)* | 1431 |
| `retrieval` | SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10; | 4256 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 10 stations les plus souvent en panne', source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 46 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 802 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1067 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1067 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1067 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 46 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 46 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10;
```

Réponse servie :

> 10 lignes retournées, classées par `nombre_panne` (ordre décroissant) — voir le tableau ci-dessous.

#### F.6 · tirage 2 — « la durée moyenne d'indisponibilité par nature »

Ce qu'on cherche : objet 6.

**6 appel(s) LLM**, 5059 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 109 |
| `rappel` | aucun outil appelé — passe au planificateur | 313 |
| `plan` | query sur interventions *(prompt : 2304 tokens estimés, 2038 évalués)* | 1795 |
| `retrieval` | SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature; | 2833 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT nature, AVG(duree_indispo_min) FROM interventions GROUP BY nature', reason="L'utilisateur demande la durée moyenne d'indisponibilité, groupée par la nature de […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 58 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 945 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1197 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1197 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1197 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 58 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 58 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;
```

Réponse servie :

> 6 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### F.7 · tirage 2 — « les interventions signalées en décembre 2025 »

Ce qu'on cherche : objet 7.

**6 appel(s) LLM**, 4497 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 126 |
| `rappel` | aucun outil appelé — passe au planificateur | 317 |
| `plan` | query sur interventions *(prompt : 2353 tokens estimés, 2082 évalués)* | 2157 |
| `retrieval` | SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31' | 1889 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'", reason="L'utilisateur demande une liste d'interventions fil […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 70 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1099 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1338 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1338 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1338 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 70 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 70 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'
```

Réponse servie :

> 81 lignes retournées — voir le tableau ci-dessous.

#### F.8 · tirage 2 — « combien d'interventions par technicien ? »

Ce qu'on cherche : objet 8.

**6 appel(s) LLM**, 3971 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 129 |
| `rappel` | aucun outil appelé — passe au planificateur | 489 |
| `plan` | query sur interventions *(prompt : 2421 tokens estimés, 2142 évalués)* | 1406 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien; | 1940 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="nombre d'interventions par technicien", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 82 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1322 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1548 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1548 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1548 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 82 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 82 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;
```

Réponse servie :

> 18 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > La main courante de la maintenance : un signalement par ligne, 900 lignes sur l'année 2025. Saisie par les techniciens, avec ce que ça implique — elle désigne les stations comme on les lit sur le terrain, et laisse des cases vides.
> > `technicien` : Matricule anonymisé, `TECH-nn`.
> > 108 lignes sur 900 portent `-1`, soit environ 12,0 %. Le technicien n'a pas relevé la durée ; le fichier ne sait pas représenter une absence autrement.

#### F.9 · tirage 2 — « reprends le tableau du tout premier tour »

Ce qu'on cherche : huit objets plus tard, le premier est-il encore atteignable ?.

**4 appel(s) LLM**, 3856 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 166 |
| `rappel` | lire_un_artefact — formulé par le modèle | 3686 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 94 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1467 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je ne peux pas "valider" un feedback. Je suis un modèle de langage et je n'ai pas de mécanisme de validation pour les retours utilisateurs.
>
> Si vous souhaitez que je fasse quelque chose avec le tableau `resultat_1` (le tableau des 10 interventions les plus longues), veuillez me donner une instruction claire (par exemple : "Donne-moi la durée moyenne de ces interventions" ou "Quelles sont les stations les plus concernées dans ce tableau ?").

#### F.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 3 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 0 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **interventions** (file) — Main courante de la maintenance (fichier CSV, une table plate) — un signalement par ligne, sa nature, sa résolution et la durée d'indisponibilité. La station y est désignée par son LIBELLÉ.
>
> 1 table(s), 900 ligne(s) (interventions : 900) — période couverte : du 2025-01-01 au 2025-12-31 (colonne date_signalement de interventions)
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### F.1 · tirage 3 — « les 10 interventions les plus longues »

Ce qu'on cherche : objet 1.

**5 appel(s) LLM**, 4137 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 128 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1569 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2432 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM interventions ORDER BY dure_indisponibilite DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 interventions les plus longues dans la sour […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10'` |

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10
```

Réponse servie :

> 10 lignes retournées, classées par `duree_indispo_min` (ordre décroissant) — voir le tableau ci-dessous.

#### F.2 · tirage 3 — « combien d'interventions par nature ? »

Ce qu'on cherche : objet 2.

**6 appel(s) LLM**, 4315 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 148 |
| `rappel` | aucun outil appelé — passe au planificateur | 470 |
| `plan` | query sur interventions *(prompt : 2123 tokens estimés, 1887 évalués)* | 1480 |
| `retrieval` | SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature; | 2207 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par nature", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 361 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 665 car. |
| `plan` | `_peser_le_prompt` | `describe` | 665 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature;
```

Réponse servie :

> 6 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### F.3 · tirage 3 — « les 5 techniciens qui sont intervenus le plus souvent »

Ce qu'on cherche : objet 3.

**6 appel(s) LLM**, 3708 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 127 |
| `rappel` | aucun outil appelé — passe au planificateur | 309 |
| `plan` | query sur interventions *(prompt : 2169 tokens estimés, 1923 évalués)* | 969 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5; | 2294 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 5 techniciens qui sont intervenus le plus souvent', source='resultat_1'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 22 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 497 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 788 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 788 car. |
| `plan` | `_peser_le_prompt` | `describe` | 788 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 22 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 22 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT technicien, COUNT(intervention_id) AS nombre_interventions
FROM interventions
GROUP BY technicien
ORDER BY nombre_interventions DESC
LIMIT 5;
```

Réponse servie :

> 5 lignes retournées, classées par `nombre_interventions` (ordre décroissant) — voir le tableau ci-dessous.

#### F.4 · tirage 3 — « combien d'interventions par mois de signalement ? »

Ce qu'on cherche : objet 4.

**6 appel(s) LLM**, 3418 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 111 |
| `rappel` | aucun outil appelé — passe au planificateur | 312 |
| `plan` | query sur interventions *(prompt : 2222 tokens estimés, 1965 évalués)* | 897 |
| `retrieval` | SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois; | 2090 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="nombre d'interventions par mois de signalement", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois;"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 34 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 654 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 932 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 932 car. |
| `plan` | `_peser_le_prompt` | `describe` | 932 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 34 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 34 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions
FROM interventions
GROUP BY mois
ORDER BY mois;
```

Réponse servie :

> 12 lignes retournées, classées par `mois` (ordre croissant) — voir le tableau ci-dessous.

#### F.5 · tirage 3 — « les 10 stations les plus souvent en panne »

Ce qu'on cherche : objet 5.

**6 appel(s) LLM**, 5169 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 97 |
| `rappel` | aucun outil appelé — passe au planificateur | 316 |
| `plan` | query sur interventions *(prompt : 2263 tokens estimés, 1999 évalués)* | 898 |
| `retrieval` | SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10; | 3850 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 10 stations les plus souvent en panne', source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 46 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 802 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1067 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1067 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1067 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 46 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 46 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10;
```

Réponse servie :

> 10 lignes retournées, classées par `nombre_panne` (ordre décroissant) — voir le tableau ci-dessous.

#### F.6 · tirage 3 — « la durée moyenne d'indisponibilité par nature »

Ce qu'on cherche : objet 6.

**6 appel(s) LLM**, 5099 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 109 |
| `rappel` | aucun outil appelé — passe au planificateur | 311 |
| `plan` | query sur interventions *(prompt : 2304 tokens estimés, 2038 évalués)* | 1796 |
| `retrieval` | SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature; | 2874 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT nature, AVG(duree_indispo_min) FROM interventions GROUP BY nature', reason="L'utilisateur demande la durée moyenne d'indisponibilité, groupée par la nature de […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 58 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 945 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1197 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1197 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1197 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 58 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 58 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;
```

Réponse servie :

> 6 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### F.7 · tirage 3 — « les interventions signalées en décembre 2025 »

Ce qu'on cherche : objet 7.

**6 appel(s) LLM**, 4423 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 124 |
| `rappel` | aucun outil appelé — passe au planificateur | 314 |
| `plan` | query sur interventions *(prompt : 2353 tokens estimés, 2082 évalués)* | 2156 |
| `retrieval` | SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31' | 1820 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'", reason="L'utilisateur demande une liste d'interventions fil […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 70 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1099 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1338 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1338 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1338 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 70 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 70 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'
```

Réponse servie :

> 81 lignes retournées — voir le tableau ci-dessous.

#### F.8 · tirage 3 — « combien d'interventions par technicien ? »

Ce qu'on cherche : objet 8.

**6 appel(s) LLM**, 2907 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 104 |
| `rappel` | aucun outil appelé — passe au planificateur | 312 |
| `plan` | query sur interventions *(prompt : 2421 tokens estimés, 2142 évalués)* | 880 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien; | 1602 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="nombre d'interventions par technicien", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 82 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1322 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1548 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1548 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1548 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 82 car. |
| `retrieval` | `_retrieval_node` | `sources_ephemeres` | 82 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **oui**, base rematérialisée = **non**.

SQL émis :

```sql
SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;
```

Réponse servie :

> 18 lignes retournées — voir le tableau ci-dessous.
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > La main courante de la maintenance : un signalement par ligne, 900 lignes sur l'année 2025. Saisie par les techniciens, avec ce que ça implique — elle désigne les stations comme on les lit sur le terrain, et laisse des cases vides.
> > `technicien` : Matricule anonymisé, `TECH-nn`.
> > 108 lignes sur 900 portent `-1`, soit environ 12,0 %. Le technicien n'a pas relevé la durée ; le fichier ne sait pas représenter une absence autrement.

#### F.9 · tirage 3 — « reprends le tableau du tout premier tour »

Ce qu'on cherche : huit objets plus tard, le premier est-il encore atteignable ?.

**4 appel(s) LLM**, 3931 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 127 |
| `rappel` | lire_un_artefact — formulé par le modèle | 3800 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `sources_ephemeres` | 94 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1467 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je ne peux pas "valider" un feedback. Je suis un modèle de langage et je n'ai pas de mécanisme de validation pour les retours utilisateurs.
>
> Si vous souhaitez que je fasse quelque chose avec le tableau `resultat_1` (le tableau des 10 interventions les plus longues), veuillez me donner une instruction claire (par exemple : "Donne-moi la durée moyenne de ces interventions" ou "Quelles sont les stations les plus concernées dans ce tableau ?").

## Ce que l'agent SAIT dire de ses objets, mot pour mot

### B.3 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

**Tirage 1** — nœuds `system → rappel → plan → retrieval → synthesize` :

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en affirmer. Reformule en précisant ce que tu veux en savoir (par exemple : « combien de lignes ? », « quelles colonnes ? »).

**Tirage 2** — nœuds `system → rappel → plan → retrieval → synthesize` :

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en affirmer. Reformule en précisant ce que tu veux en savoir (par exemple : « combien de lignes ? », « quelles colonnes ? »).

**Tirage 3** — nœuds `system → rappel → plan → retrieval → synthesize` :

> Je n'ai pas interrogé la source pour cette question, je ne peux donc rien en affirmer. Reformule en précisant ce que tu veux en savoir (par exemple : « combien de lignes ? », « quelles colonnes ? »).

### B.4 — « et le premier tableau, il contenait quoi ? »

**Tirage 1** — nœuds `system → rappel → synthesize` :

> resultat_1 (tableau de 10 ligne(s) ; colonnes : station_libelle, nature, duree_indispo_min) :
> station_libelle,nature,duree_indispo_min
> Montpellier — Port,maintenance préventive,2880
> Reims — Stade,défaut de paiement carte,2876
> Le Havre — Marché,borne hors service,2874
> Colmar — Parc des Expos,câble endommagé,2865
> Le Havre — Marché,écran illisible,2848
> Strasbourg — Hôpital,écran illisible,2845
> Évreux — Centre,communication réseau perdue,2844
> Vannes — Port,défaut de paiement carte,2842
> Le Havre — Zone Nord,câble endommagé,2841
> Le Havre — Marché,écran illisible,2837

**Tirage 2** — nœuds `system → rappel → synthesize` :

> Je suis désolé, mais je ne comprends pas votre demande. Pour que je puisse vous aider, pourriez-vous me dire ce que vous souhaitez faire avec le tableau `resultat_1` ? Par exemple, voulez-vous le modifier, en tirer des statistiques, ou autre chose ?

**Tirage 3** — nœuds `system → rappel → synthesize` :

> Je suis désolé, mais je ne comprends pas votre demande. Pour que je puisse vous aider, pourriez-vous me dire ce que vous souhaitez faire avec le tableau `resultat_1` ? Par exemple, voulez-vous le modifier, en tirer des statistiques, ou autre chose ?

### C.2 — « quelles données as-tu à ta disposition maintenant ? »

**Tirage 1** — nœuds `system → synthesize` :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les informations sur les stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> Que souhaitez-vous savoir sur cette source ?

**Tirage 2** — nœuds `system → synthesize` :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les informations sur les stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> Que souhaitez-vous savoir sur cette source ?

**Tirage 3** — nœuds `system → synthesize` :

> Je dispose de la source `referentiel` qui est un fichier CSV contenant les informations sur les stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> Que souhaitez-vous savoir sur cette source ?

### D.2 — « combien de lignes dans ce tableau ? »

**Tirage 1** — nœuds `system → rappel → synthesize` :

> Le tableau contient 200 lignes, comme indiqué par l'information `[200 rows x 7 columns]`. Le nombre de lignes dans le DataFrame est confirmé à 200.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

**Tirage 2** — nœuds `system → rappel → synthesize` :

> Le tableau contient 200 lignes, comme indiqué par l'information `[200 rows x 7 columns]`. Le nombre de lignes dans le DataFrame est confirmé à 200.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

**Tirage 3** — nœuds `system → rappel → synthesize` :

> Le tableau contient 200 lignes, comme indiqué par l'information `[200 rows x 7 columns]`. Le nombre de lignes dans le DataFrame est confirmé à 200.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

