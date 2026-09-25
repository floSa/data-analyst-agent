# Ajouter une source

Ce guide décrit la déclaration d'une nouvelle source de données dans data-analyst-agent, sur un service déjà installé ([INSTALLATION.md](INSTALLATION.md)).

| Section | Contenu |
|---|---|
| [1. Les types de source](#1-les-types-de-source) | Fichier, Postgres, DuckDB, avec un exemple chacun |
| [2. Les champs de la déclaration](#2-les-champs-de-la-déclaration) | Description, clés, date, prédiction, filtre, dictionnaire |
| [3. Emplacement des fichiers](#3-emplacement-des-fichiers) | Où déposer les données, et la contrainte des chemins |
| [4. Prise en compte](#4-prise-en-compte) | Ce qui demande un redémarrage |
| [5. Vérification](#5-vérification) | Les questions à poser après l'ajout |
| [6. Limites](#6-limites) | Plafonds et contraintes à connaître |

Une source se déclare dans le catalogue, un fichier YAML sur la machine du service.
Le format de la déclaration est défini par [`agents/retrieval/catalog.py`](../src/data_analyst_agent/agents/retrieval/catalog.py).
Un catalogue complet, avec les trois types : [`sources/metier/catalogue.yaml`](../sources/metier/catalogue.yaml).

## 1. Les types de source

| `type` | Contenu | Relations |
|---|---|---|
| `file` | un CSV, un `.xlsx` ou un `.xlsm` | aucune |
| `postgres` | une base Postgres | clés étrangères du schéma |
| `duckdb` | un fichier `.duckdb` | clés étrangères du schéma |

- Les trois types s'interrogent en SQL.
- Un CSV devient une table nommée d'après le fichier. Un classeur devient une table par feuille.
- Les noms de tables sont mis en minuscules ; tout caractère autre qu'une lettre ou un chiffre devient `_`.
- Une base DuckDB est ouverte en lecture seule.
- Des données avec des relations se déclarent de préférence en `duckdb` ou `postgres` : les clés y sont explicites.

### Fichier

```yaml
sources:
  - type: file
    name: ventes
    description: >-
      Ventes mensuelles par magasin — date, magasin, référence, quantité,
      chiffre d'affaires. 24 mois, 18 magasins.
    path: ventes.csv          # relatif au catalogue
    dictionary: dictionnaires/ventes.md
    date_reference: date_vente
```

### Postgres

```yaml
  - type: postgres
    name: commandes
    description: >-
      Le carnet de commandes (quatre tables liées par clés étrangères) —
      18 revendeurs, 12 produits, 180 commandes et leurs 463 lignes sur 2025.
    dsn: postgresql+pg8000://${DAA_PG_USER}:${DAA_PG_PASSWORD}@${DAA_PG_HOST}:${DAA_PG_PORT}/ma_base
    dictionary: dictionnaires/commandes.md
    date_reference: commandes.date_commande
```

Les `${VARIABLES}` du DSN sont lues dans le fichier d'environnement du service.
Une variable absente est signalée par son nom.

### DuckDB

```yaml
  - type: duckdb
    name: production
    description: >-
      L'atelier (quatre tables liées par clés étrangères déclarées) —
      3 ateliers, 9 machines, 140 ordres de fabrication, 70 arrêts sur 2025.
    path: production.duckdb   # relatif au catalogue
    dictionary: dictionnaires/production.md
    date_reference: ordres_fabrication.date_lancement
```

## 2. Les champs de la déclaration

| Champ | Obligatoire | Rôle |
|---|---|---|
| `type`, `name` | oui | Type et nom de la source |
| `path` ou `dsn` | oui | Emplacement des données |
| `description` | recommandé | Base du choix de source par l'agent |
| `date_reference` | recommandé | Colonne qui porte la période |
| `dictionary` | recommandé | Sens des données, règles de calcul |
| `features` | pour prédire | Colonnes qui alimentent un modèle |
| `filtre_des_sommes` | selon la source | Filtre imposé aux sommes, contrôlé |

### `description`

- L'agent choisit la source sur ce texte quand la question ne la nomme pas.
- Elle doit dire ce que contient la source et en donner les ordres de grandeur.
- « 18 revendeurs, 180 commandes sur 2025 » est plus utile que « données de vente ».

### Clés entre sources

À l'intérieur d'une source, les clés viennent du schéma.
Entre deux sources, le lien est déduit des données (`relier_les_sources`, [`agents/retrieval/croisement.py`](../src/data_analyst_agent/agents/retrieval/croisement.py)), à trois conditions :

1. la colonne porte le même nom dans les deux sources ;
2. elle est unique et sans valeur nulle d'un seul côté ;
3. toutes ses valeurs de l'autre côté existent en face.

> **Règle : un même identifiant porte le même nom de colonne dans toutes les sources.**

Avec `code_produit` partout, le croisement trouve sa clé.
Avec `code_produit` d'un côté et `ref_article` de l'autre, l'agent doit deviner la jointure, et le risque de chiffres multipliés est réel.

### `date_reference`

- Format : `table.colonne` ou `colonne`.
- À défaut, la première colonne de date du schéma est retenue.
- À déclarer dès que la source porte plusieurs dates : une date de création de compte n'est pas une date d'activité.

### `features`

Correspondance entre les colonnes de la source et les attributs d'un modèle du registre : `{modèle: {attribut: colonne}}`.

```yaml
    features:
      titanic:
        sex: passengers.sex
        pclass: classes.level
        age: passengers.age
```

Quand la source code une valeur autrement que le modèle :

```yaml
        pclass:
          column: classes.label
          values: {"1re classe": 1, "2e classe": 2, "3e classe": 3}
```

Chaque colonne déclarée est vérifiée contre le schéma avant toute requête.

### `filtre_des_sommes`

Filtre que toute somme de certaines colonnes doit appliquer.

```yaml
    filtre_des_sommes:
      colonne: commandes.statut
      exclure: ANN
      sommes:
        - lignes_commande.quantite
        - lignes_commande.montant_ligne_eur
```

- `colonne` porte le filtre, `exclure` la valeur à écarter, `sommes` les colonnes concernées, au format `table.colonne`.
- Les comptages ne sont pas concernés.
- Le SQL et le code produits par l'agent sont contrôlés contre cette règle avant d'être servis ([`agents/retrieval/verification.py`](../src/data_analyst_agent/agents/retrieval/verification.py), [`agents/analysis/consigne.py`](../src/data_analyst_agent/agents/analysis/consigne.py)).
- Le dictionnaire exprime la même règle pour le modèle ; la déclaration la rend vérifiable.

### `dictionary`

- Fichier Markdown, chemin relatif au catalogue.
- Il donne le sens des données : codes, valeurs particulières, unités, filtres à appliquer selon la question.
- Il est transmis aux agents qui écrivent le SQL et le Python.
- Taille maximale transmise : 8 000 caractères (`DAA_DICTIONARY_MAX_CHARS`).
- Règles de rédaction : [rediger-un-dictionnaire-de-source.md](rediger-un-dictionnaire-de-source.md).

## 3. Emplacement des fichiers

- Les chemins relatifs sont résolus depuis le fichier du catalogue.
- Sur le service installé, le catalogue et les données sont sous `/var/lib/data-analyst-agent/sources/` ([INSTALLATION.md](INSTALLATION.md#4-le-dossier-de-données)).
- Les fichiers appartiennent à l'utilisateur `1000:1000`.

**Contrainte des chemins.**
Les analyses s'exécutent dans un conteneur lancé par le Docker de l'hôte, qui monte les fichiers de données.
Un fichier placé hors du dossier de données produit un montage vide, sans message d'erreur.
Tous les fichiers de source doivent donc se trouver sous `DAA_DATA_DIR`.
Détail : [INSTALLATION.md](INSTALLATION.md#le-bac-à-sable-vu-du-conteneur).

## 4. Prise en compte

| Modification | Action |
|---|---|
| Catalogue : source ajoutée, retirée, renommée, description | Redémarrer : `sudo systemctl restart daa` |
| `dictionary`, `date_reference`, `features`, `filtre_des_sommes` | Redémarrer |
| Contenu des données | Aucune |

- Le volume annoncé d'une source (tables, lignes, période) est conservé 15 minutes (`DAA_RELEVE_PEREMPTION`).
- Le service ne redémarre pas les bases externes : chacune doit avoir sa propre politique de redémarrage ([EXPLOITATION.md](EXPLOITATION.md#commander-le-service)).

## 5. Vérification

Dans une nouvelle conversation :

| Question | Résultat attendu | Ce qui est vérifié |
|---|---|---|
| « Quelles sources de données as-tu ? » | La source apparaît, avec son type | Le catalogue est lu |
| « On travaille sur <source>. » | Nombre de tables, de lignes, période couverte | La source s'ouvre ; la bonne colonne de date est utilisée |
| « Combien de <lignes> en <année> ? » | Un chiffre connu à l'avance | La chaîne complète : SQL, exécution, réponse |
| « Que signifie la colonne <X> ? » | Une réponse qui cite le dictionnaire | Le dictionnaire est transmis |
| « Fais-moi un graphique de <X>. » | Une figure | Le bac à sable et le montage des fichiers |

Une erreur de YAML apparaît au redémarrage, dans les journaux : `journalctl -u daa`.

## 6. Limites

- Un croisement charge au plus 10 000 lignes par table (`DAA_ANALYSIS_TABLE_MAX_ROWS`). Au-delà, les sommes portent sur une partie des données ; la réponse l'indique.
- Un tableau affiché est limité à 200 lignes (`DAA_RETRIEVAL_MAX_ROWS`) ; la coupe est indiquée.
- Deux feuilles d'un même classeur ne sont pas reliées entre elles.
- Un dictionnaire de plus de 8 000 caractères est tronqué.
- Le catalogue n'est pas rechargé à chaud.
- Aucune source ne s'ajoute depuis l'interface ([LIVRAISON.md](LIVRAISON.md#4-les-prochaines-étapes)).
