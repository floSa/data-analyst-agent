# Le relevé de la mémoire de conversation

> **Ce document est un relevé engendré**, gardé comme preuve d'une mesure. Il reste à ce chemin parce que le script qui l'écrit le cite. Index : [historique/README.md](historique/README.md).

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
| repart d'un objet déjà produit | 12 | 19 % |
| relance un SQL sur une source | 42 | 67 % |
| les deux dans le même tour | 0 | 0 % |
| ni l'un ni l'autre | 9 | 14 % |
| rematérialise la base en CSV | 6 | 10 % |
| **total mesuré** | **63** | (hors amorces) |

### Ce que l'inventaire coûte en contexte, et où il entre

Ce qui entre RÉELLEMENT dans un prompt, en caractères :

| nœud | ce qui est injecté | tours concernés | médiane | max |
|---|---|---|---|---|
| `analysis` | `montage_sandbox` | 6 | 370 car. | 428 car. |
| `plan` | `describe` | 30 | 1188 car. | 2658 car. |
| `rappel` | `catalogue_du_rappel` | 39 | 808 car. | 2594 car. |

Et ce qui est MIS À DISPOSITION d'un nœud sans forcément entrer dans son prompt — le texte des objets du fil, que l'agent système ne lit que s'il appelle l'outil qui le rend :

| nœud | ce qui est offert | tours concernés | médiane | max |
|---|---|---|---|---|
| `system` | `objets_du_fil` | 45 | 970 car. | 2658 car. |
| `system` | `sources_transformees` | 45 | 724 car. | 2658 car. |

Nœuds du graphe où **aucun texte** de la mémoire de conversation n'est entré pendant toute la campagne : `retrieval`, `inference` (jamais traversé), `fetch_predict` (jamais traversé), `synthesize`. Cela ne veut pas dire qu'ils l'ignorent : certains reçoivent les tableaux du fil comme des sources interrogeables, sans qu'aucune ligne de catalogue n'entre dans leur prompt (tableau plus bas).

Rapporté au prompt qui la porte : le catalogue des objets pèse **18 % du prompt du planificateur** en médiane sur les 30 tours où il est injecté, et jusqu'à **32 %** (tour F.8 · tirage 2). Les deux termes sont pris en tokens estimés, comme le budget qui les arbitre.

La **pesée du budget** (`fit_to_budget`, nœud `plan`) a redemandé ce même catalogue 60 fois pour le mesurer sans l'envoyer — médiane 1188 car., max 2658 car. Elle n'est pas comptée ci-dessus.

Les tableaux du fil exposés comme **sources interrogeables** (`_effective_catalog`), par nœud demandeur :

| nœud | tours où au moins une source éphémère est exposée |
|---|---|
| `analysis` | 6 |
| `plan` | 30 |
| `rappel` | 3 |
| `retrieval` | 24 |
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
| 1 | 2 | fais-moi un graphique de leur âge | system → rappel → plan → analysis → synthesize | analyze · source=resultat_1 | — | resultat_1(200l·tronqué), graphique_1 | 1536 car. |
| 2 | 0 (amorce) | travaillons sur titanic | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | extrais-moi les passagères qui ont survécu | system → plan → retrieval → synthesize | query · source=titanic | oui | resultat_1(200l·tronqué) | 0 car. |
| 2 | 2 | fais-moi un graphique de leur âge | system → rappel → plan → analysis → synthesize | analyze · source=resultat_1 | — | resultat_1(200l·tronqué), graphique_1 | 1536 car. |
| 3 | 0 (amorce) | travaillons sur titanic | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | extrais-moi les passagères qui ont survécu | system → plan → retrieval → synthesize | query · source=titanic | oui | resultat_1(200l·tronqué) | 0 car. |
| 3 | 2 | fais-moi un graphique de leur âge | system → rappel → plan → analysis → synthesize | analyze · source=resultat_1 | — | resultat_1(200l·tronqué), graphique_1 | 1536 car. |

#### A.0 · tirage 1 — « travaillons sur titanic » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source, sans quoi le tour 1 n'interroge rien.

**0 appel(s) LLM**, 224 ms, source liée après le tour : `titanic`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : titanic — sans appel LLM | 219 |
| `synthesize` | clarification | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Mémoire de conversation injectée dans ce tour :

Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Entendu : on travaille sur **titanic** (postgres) — Base Titanic multi-tables (passengers + classes jointes par clé étrangère class_id) — survie, sexe, âge, tarif, classe.
>
> 2 table(s), 894 ligne(s) (classes : 3, passengers : 891) — aucune période couverte : la source ne porte aucune colonne de date
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### A.1 · tirage 1 — « extrais-moi les passagères qui ont survécu »

Ce qu'on cherche : un tableau, et on verra s'il est tronqué.

**5 appel(s) LLM**, 3217 ms, plan : `query · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 447 |
| `plan` | query sur titanic *(prompt : 1540 tokens estimés, 1434 évalués)* | 862 |
| `retrieval` | SELECT * FROM passengers WHERE survived = 1 AND sex = 'female' | 1901 |
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

**5 appel(s) LLM**, 14381 ms, plan : `analyze · source=resultat_1`, source liée après le tour : `titanic`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 102 |
| `rappel` | aucun outil appelé — passe au planificateur | 333 |
| `plan` | analyze sur resultat_1 *(prompt : 1906 tokens estimés, 1731 évalués)* | 1123 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 10837 |
| `synthesize` | LLM | 1977 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="graphique de l'âge des passagers dans resultat_1", source='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 515 car. |
| `system` | `_system_node` | `sources_transformees` | 515 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 465 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 759 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 759 car. |
| `plan` | `_peser_le_prompt` | `describe` | 759 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 312 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Charger les données
df = pd.read_csv('/data/resultat_1.csv')

# Vérifier les valeurs manquantes dans la colonne 'age'
print(f"Nombre de NaN dans 'age' avant traitement: {df['age'].isnull().sum()}")

# Imputer les valeurs manquantes par la médiane pour éviter les problèmes de traçage
df['age'] = df['age'].fillna(df['age'].median())

# Créer le histogramme de l'âge
plt.figure(figsize=(10, 6))
plt.hist(df['age'], bins=20, edgecolor='black', alpha=0.7)

# Ajouter les titres et les étiquettes
plt.title('Distribution de l\'âge des passagers')
plt.xlabel('Âge')
plt.ylabel('Fréquence')
plt.grid(axis='y', alpha=0.5)

# Afficher le graphique
plt.show()

# Afficher quelques statistiques descriptives de l'âge
print("Statistiques descriptives de l'âge:")
print(df['age'].describe())
```

Réponse servie :

> Un graphique de la distribution de l'âge a été généré (ci-joint). Les statistiques montrent que sur 200 observations, l'âge moyen est de 28.07 ans, avec une médiane de 28.00 ans. Les âges varient de 0.75 à 63 ans.
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
> 2 table(s), 894 ligne(s) (classes : 3, passengers : 891) — aucune période couverte : la source ne porte aucune colonne de date
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### A.1 · tirage 2 — « extrais-moi les passagères qui ont survécu »

Ce qu'on cherche : un tableau, et on verra s'il est tronqué.

**5 appel(s) LLM**, 2512 ms, plan : `query · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 136 |
| `plan` | query sur titanic *(prompt : 1540 tokens estimés, 1434 évalués)* | 807 |
| `retrieval` | SELECT * FROM passengers WHERE survived = 1 AND sex = 'female' | 1561 |
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

**6 appel(s) LLM**, 22766 ms, plan : `analyze · source=resultat_1`, source liée après le tour : `titanic`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 105 |
| `rappel` | aucun outil appelé — passe au planificateur | 89 |
| `plan` | analyze sur resultat_1 *(prompt : 1906 tokens estimés, 1731 évalués)* | 912 |
| `analysis` | 2 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 19749 |
| `synthesize` | LLM | 1900 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="graphique de l'âge des passagers dans resultat_1", source='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 515 car. |
| `system` | `_system_node` | `sources_transformees` | 515 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 465 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 759 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 759 car. |
| `plan` | `_peser_le_prompt` | `describe` | 759 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 312 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Charger les données
df = pd.read_csv('/data/resultat_1.csv')

# Vérifier les valeurs manquantes dans la colonne 'age'
print(f"Nombre de NaN dans 'age' avant traitement: {df['age'].isnull().sum()}")

# Imputer les valeurs manquantes par la médiane pour éviter les problèmes de traçage
df['age'] = df['age'].fillna(df['age'].median())

# Créer le histogramme de l'âge
plt.figure(figsize=(10, 6))
plt.hist(df['age'], bins=20, edgecolor='black', alpha=0.7)

# Ajouter les titres et les étiquettes
plt.title('Distribution de l\'âge des passagers')
plt.xlabel('Âge')
plt.ylabel('Fréquence')
plt.grid(axis='y', alpha=0.5)

# Afficher le graphique
plt.show()

# Afficher quelques statistiques descriptives de l'âge sans utiliser to_markdown
print("Statistiques descriptives de l'âge:")
print(df['age'].describe())
```

Réponse servie :

> Un graphique de la distribution de l'âge a été généré (ci-joint). Les statistiques montrent que sur 200 entrées, l'âge moyen est de 28.07 ans, avec une médiane de 28.00 ans. Les âges varient de 0.75 à 63 ans.
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
> 2 table(s), 894 ligne(s) (classes : 3, passengers : 891) — aucune période couverte : la source ne porte aucune colonne de date
>
> Je garde cette source pour la suite de la conversation. Nomme-en une autre à tout moment et je basculerai dessus.
>
> Que veux-tu savoir ?

#### A.1 · tirage 3 — « extrais-moi les passagères qui ont survécu »

Ce qu'on cherche : un tableau, et on verra s'il est tronqué.

**5 appel(s) LLM**, 2520 ms, plan : `query · source=titanic`, source liée après le tour : `titanic`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 136 |
| `plan` | query sur titanic *(prompt : 1540 tokens estimés, 1434 évalués)* | 804 |
| `retrieval` | SELECT * FROM passengers WHERE survived = 1 AND sex = 'female' | 1572 |
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

**6 appel(s) LLM**, 19398 ms, plan : `analyze · source=resultat_1`, source liée après le tour : `titanic`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 106 |
| `rappel` | aucun outil appelé — passe au planificateur | 92 |
| `plan` | analyze sur resultat_1 *(prompt : 1906 tokens estimés, 1731 évalués)* | 916 |
| `analysis` | 2 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 17031 |
| `synthesize` | LLM | 1241 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="graphique de l'âge des passagers dans resultat_1", source='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 515 car. |
| `system` | `_system_node` | `sources_transformees` | 515 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 465 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 759 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 759 car. |
| `plan` | `_peser_le_prompt` | `describe` | 759 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 312 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/resultat_1.csv` | tableau de la conversation |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Charger les données
df = pd.read_csv('/data/resultat_1.csv')

# Vérifier les valeurs manquantes dans la colonne 'age'
print(f"Nombre de NaN dans 'age' avant traitement: {df['age'].isnull().sum()}")

# Imputer les valeurs manquantes par la médiane pour éviter les problèmes de traçage
df['age'] = df['age'].fillna(df['age'].median())

# Créer le histogramme de l'âge
plt.figure(figsize=(10, 6))
plt.hist(df['age'], bins=20, edgecolor='black', alpha=0.7)

# Ajouter les titres et les étiquettes
plt.title('Distribution de l\'âge des passagers')
plt.xlabel('Âge')
plt.ylabel('Fréquence')
plt.grid(axis='y', alpha=0.5)

# Afficher le graphique
plt.show()

# Afficher quelques statistiques descriptives de l'âge sans utiliser to_markdown
print("Statistiques descriptives de l'âge:")
print(df['age'].describe())
```

Réponse servie :

> Un graphique de la distribution de l'âge a été généré (ci-joint). Les statistiques montrent que sur 200 entrées, l'âge moyen est de 28.07 ans, avec une médiane de 28.00 ans. Les âges varient de 0.75 à 63 ans.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

### Fil B — L'inventaire, su et dit

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 4 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : sait-il énumérer ses objets, les distinguer, et dire ce que chacun contient — ou invente-t-il, ou se tait-il ?.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | les 10 interventions les plus longues, avec leur station et  […] | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 1 | 2 | fais-moi un graphique du nombre d'interventions par nature | system → rappel → plan → analysis → synthesize | analyze · source=interventions | — | resultat_1(10l), graphique_1 | 1652 car. |
| 1 | 3 | qu'est-ce que tu as en mémoire dans cette conversation ? | system → synthesize | — | — | resultat_1(10l), graphique_1 | 0 car. |
| 1 | 4 | et le premier tableau, il contenait quoi ? | system → rappel → synthesize | — | — | resultat_1(10l), graphique_1 | 808 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | les 10 interventions les plus longues, avec leur station et  […] | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | fais-moi un graphique du nombre d'interventions par nature | system → rappel → plan → analysis → synthesize | analyze · source=interventions | — | resultat_1(10l), graphique_1 | 1652 car. |
| 2 | 3 | qu'est-ce que tu as en mémoire dans cette conversation ? | system → synthesize | — | — | resultat_1(10l), graphique_1 | 0 car. |
| 2 | 4 | et le premier tableau, il contenait quoi ? | system → rappel → synthesize | — | — | resultat_1(10l), graphique_1 | 808 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | les 10 interventions les plus longues, avec leur station et  […] | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | fais-moi un graphique du nombre d'interventions par nature | system → rappel → plan → analysis → synthesize | analyze · source=interventions | — | resultat_1(10l), graphique_1 | 1652 car. |
| 3 | 3 | qu'est-ce que tu as en mémoire dans cette conversation ? | system → synthesize | — | — | resultat_1(10l), graphique_1 | 0 car. |
| 3 | 4 | et le premier tableau, il contenait quoi ? | system → rappel → synthesize | — | — | resultat_1(10l), graphique_1 | 808 car. |

#### B.0 · tirage 1 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 90 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | choix de source — passe au planificateur | 0 |
| `plan` | source choisie : interventions — sans appel LLM | 86 |
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

**5 appel(s) LLM**, 11710 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 129 |
| `plan` | query sur interventions *(prompt : 1794 tokens estimés, 1637 évalués)* | 2599 |
| `retrieval` | SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 8974 |
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

**5 appel(s) LLM**, 23573 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 133 |
| `rappel` | aucun outil appelé — passe au planificateur | 336 |
| `plan` | analyze sur interventions *(prompt : 2246 tokens estimés, 1992 évalués)* | 882 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 20557 |
| `synthesize` | LLM | 1654 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="nombre d'interventions par nature", source='interventions'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 724 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 674 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 978 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 978 car. |
| `plan` | `_peser_le_prompt` | `describe` | 978 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt

# Charger le jeu de données
try:
    df = pd.read_csv('/data/interventions.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/interventions.csv n'a pas été trouvé.")
    exit()

# --- Application des règles de filtrage et de nettoyage ---

# 1. Gestion de 'duree_indispo_min' (Piège n°2)
# Règle : Écarter les lignes où 'duree_indispo_min' est -1, car cela signifie "non renseigné".
# Filtre appliqué : df['duree_indispo_min'] >= 0
df_filtered = df[df['duree_indispo_min'] >= 0].copy()

# 2. Gestion de 'nature' (Piège n°3)
# Règle : Isoler les pannes en filtrant sur les libellés de nature qui ne sont pas "maintenance préventive".
# Les libellés à conserver sont : 'borne hors service', 'câble endommagé', 'écran illisible', 'défaut de paiement carte', 'communication réseau perdue'.
# Le code est relu, la courbe ne l'est pas.
natures_a_conserver = [
    'borne hors service', 
    'câble endommagé', 
    'écran illisible', 
    'défaut de paiement carte', 
    'communication réseau perdue'
]
df_filtered = df_filtered[df_filtered['nature'].isin(natures_a_conserver)].copy()

# 3. Nettoyage des valeurs manquantes dans la colonne catégorielle 'nature'
# Règle : S'assurer qu'il n'y a pas de NaN dans la colonne catégorielle avant le comptage.
# Bien que le filtrage ci-dessus ait réduit les cas, on applique dropna pour garantir la robustesse.
df_filtered.dropna(subset=['nature'], inplace=True)

# --- Agrégation des données ---

# Compter le nombre d'interventions par nature
nature_counts = df_filtered['nature'].value_counts()

# --- Visualisation ---

plt.figure(figsize=(12, 7))
nature_counts.plot(kind='bar', color='skyblue')
plt.title('Nombre d\'Interventions par Nature (Excluant la Maintenance Préventive)')
plt.xlabel('Nature de l\'Intervention')
plt.ylabel('Nombre d\'Interventions')
plt.xticks(rotation=45, ha='right')
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.tight_layout()
plt.show()

# Affichage des résultats agrégés
print("Comptage des interventions par nature (après filtrage) :")
print(nature_counts.to_string())
```

Réponse servie :

> Un graphique illustrant le nombre d'interventions par nature a été produit (ci-joint). Les données montrent que la "communication réseau perdue" représente le plus grand nombre d'interventions avec 138 cas, suivie par "défaut de paiement carte" (135), "écran illisible" (131), "câble endommagé" (127) et "borne hors service" (127).
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.3 · tirage 1 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

Ce qu'on cherche : l'inventaire de ses propres objets.

**2 appel(s) LLM**, 6520 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | memoire_de_la_conversation — formulé par le modèle | 6517 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `memoire_de_la_conversation` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 970 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> *   **Sources TRANSFORMÉES** :
>     *   `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle` (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), `nature` (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), `duree_indispo_min` (entier) — produit par : « les 10 interventions les plus longues, avec leur station et leur nature »
> *   **Code produit dans CETTE conversation** :
>     *   `graphique_1` — code Python d'une figure (1 image(s)) — produit par : « fais-moi un graphique du nombre d'interventions par nature »
>
> Souhaitez-vous que je travaille sur une source spécifique ou que j'utilise ces éléments en mémoire ?

#### B.4 · tirage 1 — « et le premier tableau, il contenait quoi ? »

Ce qu'on cherche : le contenu d'un objet désigné par son rang, pas par son nom.

**4 appel(s) LLM**, 1417 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 201 |
| `rappel` | lire_un_artefact — faits servis (sentinelle rendue alors qu'un outil a été appelé) | 1212 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 970 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 808 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> resultat_1 (tableau de 10 ligne(s) ; colonnes : station_libelle (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), nature (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), duree_indispo_min (entier)) :
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

**5 appel(s) LLM**, 8515 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 104 |
| `plan` | query sur interventions *(prompt : 1794 tokens estimés, 1637 évalués)* | 1481 |
| `retrieval` | SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 6922 |
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

**5 appel(s) LLM**, 21484 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 132 |
| `rappel` | aucun outil appelé — passe au planificateur | 121 |
| `plan` | analyze sur interventions *(prompt : 2246 tokens estimés, 1992 évalués)* | 1048 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 17598 |
| `synthesize` | LLM | 2574 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="nombre d'interventions par nature", source='interventions'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 724 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 674 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 978 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 978 car. |
| `plan` | `_peser_le_prompt` | `describe` | 978 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt

# Charger le jeu de données
try:
    df = pd.read_csv('/data/interventions.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/interventions.csv n'a pas été trouvé.")
    exit()

# --- Application des règles de filtrage et de nettoyage ---

# 1. Gestion de 'duree_indispo_min' (Piège n°2)
# Règle : Écarter les lignes où 'duree_indispo_min' est -1, car cela signifie "non renseigné".
# Le filtre est : WHERE duree_indispo_min >= 0.
df_filtered = df[df['duree_indispo_min'] >= 0].copy()

# 2. Gestion de 'nature' (Piège n°3)
# Règle : Isoler les pannes en filtrant sur les libellés de nature qui ne sont pas "maintenance préventive".
# Les libellés à conserver sont : 'borne hors service', 'câble endommagé', 'écran illisible', 'défaut de paiement carte', 'communication réseau perdue'.
# Le filtre est : nature != 'maintenance préventive'.
df_filtered = df_filtered[df_filtered['nature'] != 'maintenance préventive'].copy()

# 3. Nettoyage des valeurs catégorielles (NaN)
# Règle : Avant d'agréger ou de tracer une variable catégorielle, les NaN doivent être gérés.
# Nous allons supprimer les lignes où 'nature' est NaN, car c'est la variable de regroupement.
df_filtered.dropna(subset=['nature'], inplace=True)

# --- Agrégation des données ---

# Compter le nombre d'interventions par nature
nature_counts = df_filtered['nature'].value_counts()

# --- Visualisation ---

plt.figure(figsize=(12, 7))
nature_counts.plot(kind='bar', color='skyblue')

plt.title('Nombre d\'Interventions par Nature (Excluant la Maintenance Préventive)')
plt.xlabel('Nature de l\'Intervention')
plt.ylabel('Nombre d\'Interventions')
plt.xticks(rotation=45, ha='right')
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.tight_layout()
plt.show()

# Affichage des résultats agrégés
print("Nombre d'interventions par nature (après filtrage) :")
print(nature_counts.to_string())
```

Réponse servie :

> Un graphique illustrant le nombre d'interventions par nature a été produit (ci-joint). Les données montrent que la "communication réseau perdue" représente le plus grand nombre d'interventions avec 138 cas, suivie par "défaut de paiement carte" (135), "écran illisible" (131), "câble endommagé" (127) et "borne hors service" (127).
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.3 · tirage 2 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

Ce qu'on cherche : l'inventaire de ses propres objets.

**2 appel(s) LLM**, 6015 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | memoire_de_la_conversation — formulé par le modèle | 6012 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `memoire_de_la_conversation` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 970 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> *   **Sources TRANSFORMÉES** :
>     *   `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle` (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), `nature` (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), `duree_indispo_min` (entier) — produit par : « les 10 interventions les plus longues, avec leur station et leur nature »
> *   **Code produit dans CETTE conversation** :
>     *   `graphique_1` — code Python d'une figure (1 image(s)) — produit par : « fais-moi un graphique du nombre d'interventions par nature »
>
> Que souhaitez-vous faire avec ces éléments ?

#### B.4 · tirage 2 — « et le premier tableau, il contenait quoi ? »

Ce qu'on cherche : le contenu d'un objet désigné par son rang, pas par son nom.

**4 appel(s) LLM**, 1621 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | lire_un_artefact — formulé par le modèle | 1510 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 970 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 808 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je suis désolé, mais je ne peux pas répondre à cette demande. Elle ne porte pas sur un artefact existant dans le catalogue, ni sur une demande de calcul dérivé à effectuer sur un artefact existant. Elle semble être une instruction de validation ou un retour d'information externe.

#### B.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

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

#### B.1 · tirage 3 — « les 10 interventions les plus longues, avec leur station et leur nature »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 9505 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 105 |
| `plan` | query sur interventions *(prompt : 1794 tokens estimés, 1637 évalués)* | 1881 |
| `retrieval` | SELECT station_libelle, nature, duree_indispo_min FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 7510 |
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

**5 appel(s) LLM**, 22329 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `image/png`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 178 |
| `rappel` | aucun outil appelé — passe au planificateur | 114 |
| `plan` | analyze sur interventions *(prompt : 2246 tokens estimés, 1992 évalués)* | 1036 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 18415 |
| `synthesize` | LLM | 2575 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question="nombre d'interventions par nature", source='interventions'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 724 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 674 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 978 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 978 car. |
| `plan` | `_peser_le_prompt` | `describe` | 978 car. |
| `plan` | `_plan_node` | `sources_ephemeres` | 10 car. |
| `analysis` | `_analysis_node` | `sources_ephemeres` | 10 car. |

Monté sous `/data/` pour le code d'analyse :

| fichier | origine |
|---|---|
| `/data/interventions.csv` | table de la base |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **oui**.

Code produit, tel quel :

```text
import pandas as pd
import matplotlib.pyplot as plt

# Charger le jeu de données
try:
    df = pd.read_csv('/data/interventions.csv')
except FileNotFoundError:
    print("Erreur: Le fichier /data/interventions.csv n'a pas été trouvé.")
    exit()

# --- Application des règles de filtrage et de nettoyage ---

# 1. Gestion de 'duree_indispo_min' (Piège n°2)
# Règle : Écarter les lignes où 'duree_indispo_min' est -1, car cela signifie "non renseigné".
# Le filtre est : WHERE duree_indispo_min >= 0.
df_filtered = df[df['duree_indispo_min'] >= 0].copy()

# 2. Gestion de 'nature' (Piège n°3)
# Règle : Isoler les pannes en filtrant sur les libellés de nature qui ne sont pas "maintenance préventive".
# Les libellés à conserver sont : 'borne hors service', 'câble endommagé', 'écran illisible', 'défaut de paiement carte', 'communication réseau perdue'.
# Le filtre est : nature != 'maintenance préventive'.
df_filtered = df_filtered[df_filtered['nature'] != 'maintenance préventive'].copy()

# 3. Nettoyage des valeurs catégorielles (NaN)
# Règle : Avant d'agréger ou de tracer une variable catégorielle, les NaN doivent être gérés.
# Nous allons supprimer les lignes où 'nature' est NaN, car c'est la variable de regroupement.
df_filtered.dropna(subset=['nature'], inplace=True)

# --- Agrégation des données ---

# Compter le nombre d'interventions par nature
nature_counts = df_filtered['nature'].value_counts()

# --- Visualisation ---

plt.figure(figsize=(12, 7))
nature_counts.plot(kind='bar', color='skyblue')

plt.title('Nombre d\'Interventions par Nature (Excluant la Maintenance Préventive)')
plt.xlabel('Nature de l\'Intervention')
plt.ylabel('Nombre d\'Interventions')
plt.xticks(rotation=45, ha='right')
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.tight_layout()
plt.show()

# Affichage des résultats agrégés
print("Nombre d'interventions par nature (après filtrage) :")
print(nature_counts.to_string())
```

Réponse servie :

> Un graphique illustrant le nombre d'interventions par nature a été produit (ci-joint). Les données montrent que la "communication réseau perdue" représente le plus grand nombre d'interventions avec 138 cas, suivie par "défaut de paiement carte" (135), "écran illisible" (131), "câble endommagé" (127) et "borne hors service" (127).
>
> Ce qu'en dit le dictionnaire de `interventions` :
> > `nature` : Libellé libre du problème : `borne hors service`, `câble endommagé`, `écran illisible`, `défaut de paiement carte`, `communication réseau perdue`, `maintenance préventive`.
> > C'est la même nature de piège que `telemetrie.releves_puissance.puissance_kw`, dans un autre format et sur une autre source : les deux se ressemblent assez pour qu'on croie avoir traité le second en traitant le premier.

#### B.3 · tirage 3 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

Ce qu'on cherche : l'inventaire de ses propres objets.

**2 appel(s) LLM**, 6740 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | memoire_de_la_conversation — formulé par le modèle | 6736 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `memoire_de_la_conversation` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 970 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> *   **Sources TRANSFORMÉES** :
>     *   `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle` (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), `nature` (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), `duree_indispo_min` (entier) — produit par : « les 10 interventions les plus longues, avec leur station et leur nature »
> *   **Code produit dans CETTE conversation** :
>     *   `graphique_1` — code Python d'une figure (1 image(s)) — produit par : « fais-moi un graphique du nombre d'interventions par nature »
>
> Que souhaitez-vous faire avec ces éléments ?

#### B.4 · tirage 3 — « et le premier tableau, il contenait quoi ? »

Ce qu'on cherche : le contenu d'un objet désigné par son rang, pas par son nom.

**4 appel(s) LLM**, 2116 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 102 |
| `rappel` | lire_un_artefact — formulé par le modèle | 2010 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 970 car. |
| `system` | `_system_node` | `sources_transformees` | 724 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 808 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je suis désolé, mais je ne peux pas répondre à cette demande. Elle ne porte pas sur un artefact existant dans le catalogue, ni sur une demande de calcul dérivé à effectuer sur un artefact existant. Elle semble être une instruction de validation ou un retour d'information externe.

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
| `plan` | source choisie : referentiel — sans appel LLM | 62 |
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

**5 appel(s) LLM**, 4044 ms, plan : `query · source=referentiel`, source liée après le tour : `referentiel`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 191 |
| `plan` | query sur referentiel *(prompt : 1791 tokens estimés, 1638 évalués)* | 2251 |
| `retrieval` | SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10 | 1594 |
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

**2 appel(s) LLM**, 5002 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 4999 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 527 car. |
| `system` | `_system_node` | `sources_transformees` | 527 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> J'ai à ma disposition la source `referentiel` qui est un fichier contenant le référentiel des stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station` (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte), et `nb_points` (entier).
>
> Que souhaitez-vous faire avec ces données ?

#### C.0 · tirage 2 — « travaillons sur referentiel » *(amorce, hors comptage)*

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

#### C.1 · tirage 2 — « donne-moi les 10 stations les plus récemment mises en service »

Ce qu'on cherche : un tableau.

**5 appel(s) LLM**, 2914 ms, plan : `query · source=referentiel`, source liée après le tour : `referentiel`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 104 |
| `plan` | query sur referentiel *(prompt : 1791 tokens estimés, 1638 évalués)* | 1432 |
| `retrieval` | SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10 | 1369 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 stations les plus récemment mises en service  […]` |
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

**2 appel(s) LLM**, 3158 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 3155 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 527 car. |
| `system` | `_system_node` | `sources_transformees` | 527 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> J'ai à ma disposition la source `referentiel` qui est un fichier contenant le référentiel des stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station` (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte), et `nb_points` (entier).
>
> Que souhaitez-vous faire avec ces données ?

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

**5 appel(s) LLM**, 2904 ms, plan : `query · source=referentiel`, source liée après le tour : `referentiel`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 104 |
| `plan` | query sur referentiel *(prompt : 1791 tokens estimés, 1638 évalués)* | 1431 |
| `retrieval` | SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10 | 1361 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT * FROM referentiel ORDER BY date_mise_en_service DESC LIMIT 10', reason="Requête SQL directe pour obtenir les 10 stations les plus récemment mises en service  […]` |
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

**2 appel(s) LLM**, 3716 ms, source liée après le tour : `referentiel`.

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 3713 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 527 car. |
| `system` | `_system_node` | `sources_transformees` | 527 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |

Chemin : repart d'un objet = **non**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> J'ai à ma disposition la source `referentiel` qui est un fichier contenant le référentiel des stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station` (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte), et `nb_points` (entier).
>
> Que souhaitez-vous faire avec ces données ?

### Fil D — La tranche

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 2 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : dit-il que le tableau est tronqué, ou donne-t-il le compte de la tranche comme s'il était le total ? Le drapeau existe dans le magasin ; la question est s'il arrive à l'utilisateur.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | liste-moi toutes les interventions de maintenance | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(200l·tronqué) | 0 car. |
| 1 | 2 | combien de lignes dans ce tableau ? | system → rappel → synthesize | analyze · source=interventions | — | resultat_1(200l·tronqué), analyse_1 | 1016 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | liste-moi toutes les interventions de maintenance | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(200l·tronqué) | 0 car. |
| 2 | 2 | combien de lignes dans ce tableau ? | system → rappel → synthesize | analyze · source=interventions | — | resultat_1(200l·tronqué), analyse_1 | 1016 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | liste-moi toutes les interventions de maintenance | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(200l·tronqué) | 0 car. |
| 3 | 2 | combien de lignes dans ce tableau ? | system → rappel → synthesize | analyze · source=interventions | — | resultat_1(200l·tronqué), analyse_1 | 1016 car. |

#### D.0 · tirage 1 — « travaillons sur interventions » *(amorce, hors comptage)*

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

#### D.1 · tirage 1 — « liste-moi toutes les interventions de maintenance »

Ce qu'on cherche : 900 lignes en source, 200 au plafond : le tableau est une tranche.

**5 appel(s) LLM**, 4121 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 2085 |
| `plan` | query sur interventions *(prompt : 1787 tokens estimés, 1630 évalués)* | 911 |
| `retrieval` | SELECT * FROM interventions | 1118 |
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

**6 appel(s) LLM**, 11217 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 10908 |
| `synthesize` | LLM | 194 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='nombre de lignes', nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 638 car. |
| `system` | `_system_node` | `sources_transformees` | 638 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 588 car. |
| `rappel` | `_rejouer_un_code` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 428 car. |

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
    exit()

# La question demande de calculer à partir de ce fichier chargé, sans réinterroger la source.
# La demande finale est le nombre de lignes.

# Afficher le nombre de lignes
nombre_lignes = df.shape[0]

print(f"Nombre de lignes dans resultat_1: {nombre_lignes}")
```

Réponse servie :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

#### D.0 · tirage 2 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 1 ms, source liée après le tour : `interventions`.

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

**5 appel(s) LLM**, 2870 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 1292 |
| `plan` | query sur interventions *(prompt : 1787 tokens estimés, 1630 évalués)* | 633 |
| `retrieval` | SELECT * FROM interventions | 938 |
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

**6 appel(s) LLM**, 11881 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 104 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 11439 |
| `synthesize` | LLM | 331 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='nombre de lignes', nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 638 car. |
| `system` | `_system_node` | `sources_transformees` | 638 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 588 car. |
| `rappel` | `_rejouer_un_code` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 428 car. |

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
    exit()

# La question demande de calculer à partir de ce fichier chargé, sans réinterroger la source.
# La demande finale est le nombre de lignes.

# Afficher le nombre de lignes
nombre_lignes = df.shape[0]

print(f"Nombre de lignes dans resultat_1: {nombre_lignes}")
```

Réponse servie :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

#### D.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 5 ms, source liée après le tour : `interventions`.

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

**5 appel(s) LLM**, 3554 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 1920 |
| `plan` | query sur interventions *(prompt : 1787 tokens estimés, 1630 évalués)* | 634 |
| `retrieval` | SELECT * FROM interventions | 992 |
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

**6 appel(s) LLM**, 11565 ms, plan : `analyze · source=interventions`, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 110 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 11253 |
| `synthesize` | LLM | 191 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='nombre de lignes', nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 638 car. |
| `system` | `_system_node` | `sources_transformees` | 638 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 588 car. |
| `rappel` | `_rejouer_un_code` | `sources_ephemeres` | 10 car. |
| `analysis` | `_decor_de_donnees` | `montage_sandbox` | 428 car. |

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
    exit()

# La question demande de calculer à partir de ce fichier chargé, sans réinterroger la source.
# La demande finale est le nombre de lignes.

# Afficher le nombre de lignes
nombre_lignes = df.shape[0]

print(f"Nombre de lignes dans resultat_1: {nombre_lignes}")
```

Réponse servie :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

### Fil E — Témoin — ce qui ne doit PAS repartir de la mémoire

Catalogue : `sources/demonstration/catalogue.yaml`. 3 tirage(s), 2 tour(s) mesuré(s) par tirage.

Ce qu'on cherche : le tour 2 doit interroger la base, pas le tableau d'à côté.

| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |
|---|---|---|---|---|---|---|---|
| 1 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 1 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 1 | 2 | dans facturation, combien de factures ont été émises en 2025 […] | system → rappel → plan → retrieval → synthesize | query · source=facturation | oui | resultat_1(10l), resultat_2(1l) | 1284 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | dans facturation, combien de factures ont été émises en 2025 […] | system → rappel → plan → retrieval → synthesize | query · source=facturation | oui | resultat_1(10l), resultat_2(1l) | 1284 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | dans facturation, combien de factures ont été émises en 2025 […] | system → rappel → plan → retrieval → synthesize | query · source=facturation | oui | resultat_1(10l), resultat_2(1l) | 1284 car. |

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

**5 appel(s) LLM**, 4705 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 103 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1314 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 3280 |
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

**6 appel(s) LLM**, 3842 ms, plan : `query · source=facturation`, source liée après le tour : `facturation`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 159 |
| `rappel` | aucun outil appelé — passe au planificateur | 459 |
| `plan` | query sur facturation *(prompt : 2175 tokens estimés, 1929 évalués)* | 945 |
| `retrieval` | SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025' | 2270 |
| `synthesize` | résumé de la récupération | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='nombre de factures émises en 2025', source='facturation'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 540 car. |
| `system` | `_system_node` | `sources_transformees` | 540 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 490 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `_peser_le_prompt` | `describe` | 794 car. |
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

**5 appel(s) LLM**, 3516 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1316 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2084 |
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

**6 appel(s) LLM**, 3220 ms, plan : `query · source=facturation`, source liée après le tour : `facturation`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | aucun outil appelé — passe au planificateur | 92 |
| `plan` | query sur facturation *(prompt : 2175 tokens estimés, 1929 évalués)* | 738 |
| `retrieval` | SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025' | 2272 |
| `synthesize` | résumé de la récupération | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='nombre de factures émises en 2025', source='facturation'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 540 car. |
| `system` | `_system_node` | `sources_transformees` | 540 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 490 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `_peser_le_prompt` | `describe` | 794 car. |
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

#### E.1 · tirage 3 — « les 10 interventions les plus longues »

Ce qu'on cherche : un tableau sur interventions.

**5 appel(s) LLM**, 4338 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 154 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 2091 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2085 |
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

**6 appel(s) LLM**, 2672 ms, plan : `query · source=facturation`, source liée après le tour : `facturation`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 106 |
| `rappel` | aucun outil appelé — passe au planificateur | 90 |
| `plan` | query sur facturation *(prompt : 2175 tokens estimés, 1929 évalués)* | 744 |
| `retrieval` | SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025' | 1722 |
| `synthesize` | résumé de la récupération | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='nombre de factures émises en 2025', source='facturation'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT count(*) FROM factures WHERE strftime('%Y', date_emission) = '2025'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 540 car. |
| `system` | `_system_node` | `sources_transformees` | 540 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 490 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `_peser_le_prompt` | `describe` | 794 car. |
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
| 1 | 2 | combien d'interventions par nature ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l) | 1284 car. |
| 1 | 3 | les 5 techniciens qui sont intervenus le plus souvent | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l) | 1869 car. |
| 1 | 4 | combien d'interventions par mois de signalement ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l) | 2316 car. |
| 1 | 5 | les 10 stations les plus souvent en panne | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l) | 2899 car. |
| 1 | 6 | la durée moyenne d'indisponibilité par nature | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l) | 3608 car. |
| 1 | 7 | les interventions signalées en décembre 2025 | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l) | 4231 car. |
| 1 | 8 | combien d'interventions par technicien ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 5072 car. |
| 1 | 9 | reprends le tableau du tout premier tour | system → rappel → synthesize | — | — | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 2585 car. |
| 2 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 2 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 2 | 2 | combien d'interventions par nature ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l) | 1284 car. |
| 2 | 3 | les 5 techniciens qui sont intervenus le plus souvent | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l) | 1869 car. |
| 2 | 4 | combien d'interventions par mois de signalement ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l) | 2316 car. |
| 2 | 5 | les 10 stations les plus souvent en panne | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l) | 2899 car. |
| 2 | 6 | la durée moyenne d'indisponibilité par nature | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l) | 3626 car. |
| 2 | 7 | les interventions signalées en décembre 2025 | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l) | 4249 car. |
| 2 | 8 | combien d'interventions par technicien ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 5090 car. |
| 2 | 9 | reprends le tableau du tout premier tour | system → rappel → synthesize | — | — | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 2594 car. |
| 3 | 0 (amorce) | travaillons sur interventions | system → plan → synthesize | — | — | — | 0 car. |
| 3 | 1 | les 10 interventions les plus longues | system → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l) | 0 car. |
| 3 | 2 | combien d'interventions par nature ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l) | 1284 car. |
| 3 | 3 | les 5 techniciens qui sont intervenus le plus souvent | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l) | 1869 car. |
| 3 | 4 | combien d'interventions par mois de signalement ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l) | 2316 car. |
| 3 | 5 | les 10 stations les plus souvent en panne | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l) | 2899 car. |
| 3 | 6 | la durée moyenne d'indisponibilité par nature | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l) | 3610 car. |
| 3 | 7 | les interventions signalées en décembre 2025 | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l) | 4233 car. |
| 3 | 8 | combien d'interventions par technicien ? | system → rappel → plan → retrieval → synthesize | query · source=interventions | oui | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 5074 car. |
| 3 | 9 | reprends le tableau du tout premier tour | system → rappel → synthesize | — | — | resultat_1(10l), resultat_2(6l), resultat_3(5l), resultat_4(12l), resultat_5(10l), resultat_6(6l), resultat_7(81l), resultat_8(18l) | 2586 car. |

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

**5 appel(s) LLM**, 3511 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 103 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1313 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2087 |
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

**6 appel(s) LLM**, 3099 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 112 |
| `rappel` | aucun outil appelé — passe au planificateur | 89 |
| `plan` | query sur interventions *(prompt : 2166 tokens estimés, 1919 évalués)* | 698 |
| `retrieval` | SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature; | 2190 |
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
| `system` | `_system_node` | `objets_du_fil` | 540 car. |
| `system` | `_system_node` | `sources_transformees` | 540 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 490 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `_peser_le_prompt` | `describe` | 794 car. |
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

**6 appel(s) LLM**, 4426 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 126 |
| `rappel` | aucun outil appelé — passe au planificateur | 324 |
| `plan` | query sur interventions *(prompt : 2267 tokens estimés, 1999 évalués)* | 1684 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5; | 2282 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 5 techniciens qui sont intervenus le plus souvent', reason="L'utilisateur demande un classement (les 5 plus fréquents) des techniciens dans la source 'interventi […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 841 car. |
| `system` | `_system_node` | `sources_transformees` | 841 car. |
| `system` | `_system_node` | `sources_ephemeres` | 22 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 789 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1080 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1080 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1080 car. |
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

**6 appel(s) LLM**, 5375 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 113 |
| `rappel` | aucun outil appelé — passe au planificateur | 332 |
| `plan` | query sur interventions *(prompt : 2344 tokens estimés, 2076 évalués)* | 969 |
| `retrieval` | SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois; | 3951 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par mois de signalement", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois;"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 1073 car. |
| `system` | `_system_node` | `sources_transformees` | 1073 car. |
| `system` | `_system_node` | `sources_ephemeres` | 34 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1019 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1297 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1297 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1297 car. |
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

**6 appel(s) LLM**, 4552 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 112 |
| `rappel` | aucun outil appelé — passe au planificateur | 344 |
| `plan` | query sur interventions *(prompt : 2434 tokens estimés, 2223 évalués)* | 928 |
| `retrieval` | SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10; | 3158 |
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
| `system` | `_system_node` | `objets_du_fil` | 1373 car. |
| `system` | `_system_node` | `sources_transformees` | 1373 car. |
| `system` | `_system_node` | `sources_ephemeres` | 46 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1317 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1582 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1582 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1582 car. |
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

**6 appel(s) LLM**, 6168 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 160 |
| `rappel` | aucun outil appelé — passe au planificateur | 518 |
| `plan` | query sur interventions *(prompt : 2549 tokens estimés, 2329 évalués)* | 2110 |
| `retrieval` | SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature; | 3370 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="Calculer la durée moyenne d'indisponibilité par nature à partir de la source 'interventions'.", reason="L'utilisateur demande une agrégation (moyenne) et un regroupe […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 1736 car. |
| `system` | `_system_node` | `sources_transformees` | 1736 car. |
| `system` | `_system_node` | `sources_ephemeres` | 58 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1678 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1930 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1930 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1930 car. |
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

**6 appel(s) LLM**, 5134 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 122 |
| `rappel` | aucun outil appelé — passe au planificateur | 334 |
| `plan` | query sur interventions *(prompt : 2652 tokens estimés, 2418 évalués)* | 1855 |
| `retrieval` | SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31' | 2813 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="SELECT * FROM interventions WHERE date_signalement LIKE '%2025-12%'", reason="L'utilisateur demande une liste d'interventions filtrées par mois (décembre 2025). Il s […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2056 car. |
| `system` | `_system_node` | `sources_transformees` | 2056 car. |
| `system` | `_system_node` | `sources_ephemeres` | 70 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1996 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2235 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2235 car. |
| `plan` | `_peser_le_prompt` | `describe` | 2235 car. |
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

**6 appel(s) LLM**, 3005 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 107 |
| `rappel` | aucun outil appelé — passe au planificateur | 342 |
| `plan` | query sur interventions *(prompt : 2788 tokens estimés, 2533 évalués)* | 954 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien; | 1593 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par technicien", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2485 car. |
| `system` | `_system_node` | `sources_transformees` | 2485 car. |
| `system` | `_system_node` | `sources_ephemeres` | 82 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2423 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2649 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2649 car. |
| `plan` | `_peser_le_prompt` | `describe` | 2649 car. |
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

**4 appel(s) LLM**, 4058 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 130 |
| `rappel` | lire_un_artefact — formulé par le modèle | 3924 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2649 car. |
| `system` | `_system_node` | `sources_transformees` | 2649 car. |
| `system` | `_system_node` | `sources_ephemeres` | 94 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2585 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je ne peux pas "valider" ou "corriger" le tableau, car il s'agit d'un résultat de requête que je vous ai présenté.
>
> Que souhaitez-vous faire avec ce tableau ? Par exemple, voulez-vous :
> 1.  Le modifier (ex: "Reprends ce tableau et trie-le par technicien") ?
> 2.  En tirer un chiffre dérivé (ex: "Quel est le total des durées d'indisponibilité pour ce tableau") ?
> 3.  Le comparer à un autre tableau ?

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

**5 appel(s) LLM**, 3902 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 127 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 1569 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 2198 |
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

**6 appel(s) LLM**, 3291 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 111 |
| `rappel` | aucun outil appelé — passe au planificateur | 307 |
| `plan` | query sur interventions *(prompt : 2166 tokens estimés, 1919 évalués)* | 901 |
| `retrieval` | SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature; | 1962 |
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
| `system` | `_system_node` | `objets_du_fil` | 540 car. |
| `system` | `_system_node` | `sources_transformees` | 540 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 490 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `_peser_le_prompt` | `describe` | 794 car. |
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

**6 appel(s) LLM**, 4397 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 124 |
| `rappel` | aucun outil appelé — passe au planificateur | 310 |
| `plan` | query sur interventions *(prompt : 2267 tokens estimés, 1999 évalués)* | 1675 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5; | 2279 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 5 techniciens qui sont intervenus le plus souvent', reason="L'utilisateur demande un classement (les 5 plus fréquents) des techniciens dans la source 'interventi […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 841 car. |
| `system` | `_system_node` | `sources_transformees` | 841 car. |
| `system` | `_system_node` | `sources_ephemeres` | 22 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 789 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1080 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1080 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1080 car. |
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

**6 appel(s) LLM**, 6053 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 111 |
| `rappel` | aucun outil appelé — passe au planificateur | 312 |
| `plan` | query sur interventions *(prompt : 2344 tokens estimés, 2076 évalués)* | 1517 |
| `retrieval` | SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois; | 4104 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par mois de signalement", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois;"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 1073 car. |
| `system` | `_system_node` | `sources_transformees` | 1073 car. |
| `system` | `_system_node` | `sources_ephemeres` | 34 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1019 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1297 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1297 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1297 car. |
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

**6 appel(s) LLM**, 4408 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 112 |
| `rappel` | aucun outil appelé — passe au planificateur | 317 |
| `plan` | query sur interventions *(prompt : 2434 tokens estimés, 2223 évalués)* | 908 |
| `retrieval` | SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10; | 3062 |
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
| `system` | `_system_node` | `objets_du_fil` | 1373 car. |
| `system` | `_system_node` | `sources_transformees` | 1373 car. |
| `system` | `_system_node` | `sources_ephemeres` | 46 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1317 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1582 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1582 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1582 car. |
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

**6 appel(s) LLM**, 8205 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 112 |
| `rappel` | aucun outil appelé — passe au planificateur | 330 |
| `plan` | query sur interventions *(prompt : 2552 tokens estimés, 2331 évalués)* | 2418 |
| `retrieval` | SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature; | 5335 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="Calculer la durée moyenne d'indisponibilité (duree_indispo_min) regroupée par nature (nature) dans la source 'interventions'.", reason="L'utilisateur demande une agr […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 1745 car. |
| `system` | `_system_node` | `sources_transformees` | 1745 car. |
| `system` | `_system_node` | `sources_ephemeres` | 58 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1687 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1939 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1939 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1939 car. |
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

**6 appel(s) LLM**, 5322 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 180 |
| `rappel` | aucun outil appelé — passe au planificateur | 528 |
| `plan` | query sur interventions *(prompt : 2655 tokens estimés, 2420 évalués)* | 2764 |
| `retrieval` | SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31' | 1842 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="SELECT * FROM interventions WHERE date_signalement LIKE '%2025-12%'", reason="L'utilisateur demande une liste d'interventions filtrées par mois (décembre 2025). Il s […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2065 car. |
| `system` | `_system_node` | `sources_transformees` | 2065 car. |
| `system` | `_system_node` | `sources_ephemeres` | 70 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2005 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2244 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2244 car. |
| `plan` | `_peser_le_prompt` | `describe` | 2244 car. |
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

**6 appel(s) LLM**, 3015 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 106 |
| `rappel` | aucun outil appelé — passe au planificateur | 343 |
| `plan` | query sur interventions *(prompt : 2791 tokens estimés, 2535 évalués)* | 953 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien; | 1603 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par technicien", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2494 car. |
| `system` | `_system_node` | `sources_transformees` | 2494 car. |
| `system` | `_system_node` | `sources_ephemeres` | 82 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2432 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2658 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2658 car. |
| `plan` | `_peser_le_prompt` | `describe` | 2658 car. |
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

**4 appel(s) LLM**, 2323 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 136 |
| `rappel` | lire_un_artefact — formulé par le modèle | 2182 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2658 car. |
| `system` | `_system_node` | `sources_transformees` | 2658 car. |
| `system` | `_system_node` | `sources_ephemeres` | 94 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2594 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je ne peux pas "reprendre" le tableau sans une instruction spécifique sur ce que vous souhaitez faire avec lui (par exemple, le trier, calculer une moyenne, le filtrer, etc.).
>
> Le tableau `resultat_1` est celui des "10 interventions les plus longues". Que souhaitez-vous faire avec ces données ?

#### F.0 · tirage 3 — « travaillons sur interventions » *(amorce, hors comptage)*

Ce qu'on cherche : lie la source.

**0 appel(s) LLM**, 2 ms, source liée après le tour : `interventions`.

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

**5 appel(s) LLM**, 6200 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 184 |
| `plan` | query sur interventions *(prompt : 1783 tokens estimés, 1630 évalués)* | 2535 |
| `retrieval` | SELECT * FROM interventions WHERE duree_indispo_min >= 0 ORDER BY duree_indispo_min DESC LIMIT 10 | 3474 |
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

**6 appel(s) LLM**, 3261 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 114 |
| `rappel` | aucun outil appelé — passe au planificateur | 316 |
| `plan` | query sur interventions *(prompt : 2166 tokens estimés, 1919 évalués)* | 896 |
| `retrieval` | SELECT nature, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY nature; | 1927 |
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
| `system` | `_system_node` | `objets_du_fil` | 540 car. |
| `system` | `_system_node` | `sources_transformees` | 540 car. |
| `system` | `_system_node` | `sources_ephemeres` | 10 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 490 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 794 car. |
| `plan` | `_peser_le_prompt` | `describe` | 794 car. |
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

**6 appel(s) LLM**, 4391 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 113 |
| `rappel` | aucun outil appelé — passe au planificateur | 305 |
| `plan` | query sur interventions *(prompt : 2267 tokens estimés, 1999 évalués)* | 1668 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5; | 2297 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='les 5 techniciens qui sont intervenus le plus souvent', reason="L'utilisateur demande un classement (les 5 plus fréquents) des techniciens dans la source 'interventi […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien ORDER BY nombre_interventions DESC LIMIT 5;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 841 car. |
| `system` | `_system_node` | `sources_transformees` | 841 car. |
| `system` | `_system_node` | `sources_ephemeres` | 22 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 789 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1080 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1080 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1080 car. |
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

**6 appel(s) LLM**, 5343 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 110 |
| `rappel` | aucun outil appelé — passe au planificateur | 313 |
| `plan` | query sur interventions *(prompt : 2344 tokens estimés, 2076 évalués)* | 956 |
| `retrieval` | SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois; | 3956 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par mois de signalement", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT strftime('%Y-%m', date_signalement) AS mois, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY mois ORDER BY mois;"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 1073 car. |
| `system` | `_system_node` | `sources_transformees` | 1073 car. |
| `system` | `_system_node` | `sources_ephemeres` | 34 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1019 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1297 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1297 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1297 car. |
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

**6 appel(s) LLM**, 5411 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 142 |
| `rappel` | aucun outil appelé — passe au planificateur | 484 |
| `plan` | query sur interventions *(prompt : 2434 tokens estimés, 2223 évalués)* | 1448 |
| `retrieval` | SELECT station_libelle, COUNT(intervention_id) AS nombre_panne FROM interventions GROUP BY station_libelle ORDER BY nombre_panne DESC LIMIT 10; | 3329 |
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
| `system` | `_system_node` | `objets_du_fil` | 1373 car. |
| `system` | `_system_node` | `sources_transformees` | 1373 car. |
| `system` | `_system_node` | `sources_ephemeres` | 46 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1317 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1582 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1582 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1582 car. |
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

**6 appel(s) LLM**, 8164 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 90 |
| `rappel` | aucun outil appelé — passe au planificateur | 329 |
| `plan` | query sur interventions *(prompt : 2549 tokens estimés, 2330 évalués)* | 2412 |
| `retrieval` | SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature; | 5325 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="Calculer la durée moyenne d'indisponibilité (duree_indispo_min) regroupée par nature (nature) dans la source 'interventions'.", reason="L'utilisateur demande une agr […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT nature, AVG(duree_indispo_min) AS moyenne_duree_indisponibilite FROM interventions WHERE duree_indispo_min >= 0 GROUP BY nature;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 1737 car. |
| `system` | `_system_node` | `sources_transformees` | 1737 car. |
| `system` | `_system_node` | `sources_ephemeres` | 58 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1679 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1931 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 1931 car. |
| `plan` | `_peser_le_prompt` | `describe` | 1931 car. |
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

**6 appel(s) LLM**, 5077 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 176 |
| `rappel` | aucun outil appelé — passe au planificateur | 556 |
| `plan` | query sur interventions *(prompt : 2652 tokens estimés, 2419 évalués)* | 2517 |
| `retrieval` | SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31' | 1819 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="SELECT * FROM interventions WHERE date_signalement LIKE '%2025-12%'", reason="L'utilisateur demande une liste d'interventions filtrées par mois (décembre 2025). Il s […]` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT * FROM interventions WHERE date_signalement >= '2025-12-01' AND date_signalement <= '2025-12-31'"` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2057 car. |
| `system` | `_system_node` | `sources_transformees` | 2057 car. |
| `system` | `_system_node` | `sources_ephemeres` | 70 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 1997 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2236 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2236 car. |
| `plan` | `_peser_le_prompt` | `describe` | 2236 car. |
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

**6 appel(s) LLM**, 3017 ms, plan : `query · source=interventions`, source liée après le tour : `interventions`, artefacts rendus : `application/json`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 108 |
| `rappel` | aucun outil appelé — passe au planificateur | 343 |
| `plan` | query sur interventions *(prompt : 2789 tokens estimés, 2534 évalués)* | 959 |
| `retrieval` | SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien; | 1597 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question="compter le nombre d'interventions par technicien", source='interventions'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query='SELECT technicien, COUNT(intervention_id) AS nombre_interventions FROM interventions GROUP BY technicien;'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2486 car. |
| `system` | `_system_node` | `sources_transformees` | 2486 car. |
| `system` | `_system_node` | `sources_ephemeres` | 82 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2424 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2650 car. |
| `plan` | `fit_to_budget` | `describe_pesee` | 2650 car. |
| `plan` | `_peser_le_prompt` | `describe` | 2650 car. |
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

**4 appel(s) LLM**, 1867 ms, source liée après le tour : `interventions`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 129 |
| `rappel` | lire_un_artefact — formulé par le modèle | 1734 |
| `synthesize` | rappel d'artefact | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `lire_un_artefact` | `nom='resultat_1'` |

Mémoire de conversation injectée dans ce tour :

| nœud | fonction appelante | quoi | taille |
|---|---|---|---|
| `system` | `_system_node` | `objets_du_fil` | 2650 car. |
| `system` | `_system_node` | `sources_transformees` | 2650 car. |
| `system` | `_system_node` | `sources_ephemeres` | 94 car. |
| `rappel` | `system_prompt` | `catalogue_du_rappel` | 2586 car. |

Chemin : repart d'un objet = **oui**, SQL neuf = **non**, base rematérialisée = **non**.

Réponse servie :

> Je ne peux pas "reprendre" le tableau sans une instruction spécifique sur ce que vous souhaitez faire avec. Voulez-vous que je calcule quelque chose à partir de ce tableau, que je le filtre, ou que je le présente différemment ?

## Ce que l'agent SAIT dire de ses objets, mot pour mot

### B.3 — « qu'est-ce que tu as en mémoire dans cette conversation ? »

**Tirage 1** — nœuds `system → synthesize` :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> *   **Sources TRANSFORMÉES** :
>     *   `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle` (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), `nature` (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), `duree_indispo_min` (entier) — produit par : « les 10 interventions les plus longues, avec leur station et leur nature »
> *   **Code produit dans CETTE conversation** :
>     *   `graphique_1` — code Python d'une figure (1 image(s)) — produit par : « fais-moi un graphique du nombre d'interventions par nature »
>
> Souhaitez-vous que je travaille sur une source spécifique ou que j'utilise ces éléments en mémoire ?

**Tirage 2** — nœuds `system → synthesize` :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> *   **Sources TRANSFORMÉES** :
>     *   `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle` (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), `nature` (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), `duree_indispo_min` (entier) — produit par : « les 10 interventions les plus longues, avec leur station et leur nature »
> *   **Code produit dans CETTE conversation** :
>     *   `graphique_1` — code Python d'une figure (1 image(s)) — produit par : « fais-moi un graphique du nombre d'interventions par nature »
>
> Que souhaitez-vous faire avec ces éléments ?

**Tirage 3** — nœuds `system → synthesize` :

> J'ai en mémoire dans cette conversation les éléments suivants :
>
> *   **Sources TRANSFORMÉES** :
>     *   `resultat_1` — tableau de 10 ligne(s) ; colonnes : `station_libelle` (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), `nature` (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), `duree_indispo_min` (entier) — produit par : « les 10 interventions les plus longues, avec leur station et leur nature »
> *   **Code produit dans CETTE conversation** :
>     *   `graphique_1` — code Python d'une figure (1 image(s)) — produit par : « fais-moi un graphique du nombre d'interventions par nature »
>
> Que souhaitez-vous faire avec ces éléments ?

### B.4 — « et le premier tableau, il contenait quoi ? »

**Tirage 1** — nœuds `system → rappel → synthesize` :

> resultat_1 (tableau de 10 ligne(s) ; colonnes : station_libelle (texte : 'Colmar — Parc des Expos', 'Le Havre — Marché', 'Le Havre — Zone Nord', 'Montpellier — Port', 'Reims — Stade', 'Strasbourg — Hôpital', 'Vannes — Port', 'Évreux — Centre'), nature (texte : 'borne hors service', 'communication réseau perdue', 'câble endommagé', 'défaut de paiement carte', 'maintenance préventive', 'écran illisible'), duree_indispo_min (entier)) :
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

> Je suis désolé, mais je ne peux pas répondre à cette demande. Elle ne porte pas sur un artefact existant dans le catalogue, ni sur une demande de calcul dérivé à effectuer sur un artefact existant. Elle semble être une instruction de validation ou un retour d'information externe.

**Tirage 3** — nœuds `system → rappel → synthesize` :

> Je suis désolé, mais je ne peux pas répondre à cette demande. Elle ne porte pas sur un artefact existant dans le catalogue, ni sur une demande de calcul dérivé à effectuer sur un artefact existant. Elle semble être une instruction de validation ou un retour d'information externe.

### C.2 — « quelles données as-tu à ta disposition maintenant ? »

**Tirage 1** — nœuds `system → synthesize` :

> J'ai à ma disposition la source `referentiel` qui est un fichier contenant le référentiel des stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station` (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte), et `nb_points` (entier).
>
> Que souhaitez-vous faire avec ces données ?

**Tirage 2** — nœuds `system → synthesize` :

> J'ai à ma disposition la source `referentiel` qui est un fichier contenant le référentiel des stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station` (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte), et `nb_points` (entier).
>
> Que souhaitez-vous faire avec ces données ?

**Tirage 3** — nœuds `system → synthesize` :

> J'ai à ma disposition la source `referentiel` qui est un fichier contenant le référentiel des stations. Cette source contient 1 table, soit 150 lignes, et couvre la période du 2019-01-01 au 2024-06-14.
>
> J'ai également une source TRANSFORMÉE nommée `resultat_1` qui est un tableau de 10 lignes, avec les colonnes `code_station` (texte), `libelle_station` (texte), `region` (texte), `statut` (texte), `date_mise_en_service` (texte), et `nb_points` (entier).
>
> Que souhaitez-vous faire avec ces données ?

### D.2 — « combien de lignes dans ce tableau ? »

**Tirage 1** — nœuds `system → rappel → synthesize` :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

**Tirage 2** — nœuds `system → rappel → synthesize` :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

**Tirage 3** — nœuds `system → rappel → synthesize` :

> Le tableau contient 200 lignes.
>
> Données tronquées : resultat_1 coupée(s) à 200 lignes (réglage DAA_RETRIEVAL_MAX_ROWS) — tout agrégat qui porte sur elles (somme, moyenne, comptage) décrit cet échantillon, pas le résultat entier de la requête qui les a produits.

