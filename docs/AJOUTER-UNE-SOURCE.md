# Brancher sa propre source

Ce guide s'adresse à qui a **ses données** et veut que l'agent réponde dessus.
Il suppose le service installé ([INSTALLATION.md](INSTALLATION.md)).

Une source se déclare dans un **fichier YAML**, à la main, sur la machine.
Il n'y a pas d'écran pour le faire, et c'est une limite connue
([LIVRAISON.md §3](LIVRAISON.md#3-les-limites-connues)).

Tout ce qui est affirmé ici se lit dans le code, et le fichier est nommé.
Le modèle de la déclaration est
[`agents/retrieval/catalog.py`](../src/data_analyst_agent/agents/retrieval/catalog.py) :
c'est lui qui dit quels champs existent, lesquels sont obligatoires, et ce
qu'ils font.

---

## 1. Les trois types

| `type` | Ce que c'est | Ce qu'il apporte |
|---|---|---|
| `file` | un CSV, un `.xlsx` ou un `.xlsm` | le plus court chemin ; aucune relation déclarée |
| `postgres` | une base Postgres | ses tables **et ses clés étrangères**, donc ses jointures |
| `duckdb` | un fichier `.duckdb` | une **base**, pas un fichier : elle porte aussi ses clés |

Les trois se requêtent en SQL.
Un fichier est chargé en mémoire par DuckDB ; un CSV devient **une table**
nommée d'après le fichier, un classeur devient **une table par feuille**, nommée
d'après la feuille (`agents/retrieval/duckdb_excel.py`, `from_file`).
Les noms sont mis en minuscules et tout ce qui n'est pas une lettre ou un
chiffre devient `_` (`sanitize_table_name`).

### Un fichier

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

### Une base Postgres

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

Le DSN accepte des `${VARIABLES}` d'environnement, résolues depuis le fichier
d'environnement du service.
**Une variable non définie est refusée avec son nom**, au lieu de finir en
erreur de driver illisible (`PostgresSource.resolved_dsn`).

### Une base DuckDB

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

Elle est ouverte **en lecture seule**.
C'est la forme à préférer quand les données ont des relations : un schéma en
étoile dont on tait les clés oblige le modèle à deviner ses jointures
(`DuckDBSource`).

Un exemple complet et vivant des trois types dans le même catalogue :
[`sources/metier/catalogue.yaml`](../sources/metier/catalogue.yaml).

---

## 2. Ce qu'on attend d'une source

### `description` — obligatoire en pratique

Le champ est facultatif pour le modèle de données.
Il ne l'est pas pour le produit : **c'est sur lui que le planificateur choisit
la source** quand l'utilisateur ne la nomme pas.
C'est la seule chose qu'il voit de chaque source avant d'en ouvrir une
(`Catalog.describe`, qui rend une ligne `- nom (type) : description`).

Écrivez-la comme vous la diriez à quelqu'un : ce que la source contient, et
**de quel ordre de grandeur**.
« 18 revendeurs, 180 commandes sur 2025 » vaut mieux que « données de vente ».

### Des clés déclarées

À l'intérieur d'une source, les clés étrangères viennent du schéma : Postgres et
DuckDB les portent, un CSV et un classeur n'en ont pas.
Elles arrivent au modèle telles quelles, et c'est ce qui lui évite d'inventer
une jointure.

**Entre deux sources**, aucun schéma ne peut déclarer quoi que ce soit : deux
bases séparées ne se connaissent pas.
Le lien est alors **trouvé dans les données**, par
[`agents/retrieval/croisement.py`](../src/data_analyst_agent/agents/retrieval/croisement.py),
`relier_les_sources`, à trois conditions :

1. **la colonne porte le même nom des deux côtés** — c'est ce qui la rend
   candidate, et rien de plus ;
2. elle est une **clé naturelle d'un seul côté** : sans `NULL`, sans doublon.
   Deux côtés uniques, ou aucun, et le lien n'est pas posé ;
3. **toutes les valeurs de l'autre côté se retrouvent en face** — c'est ce qui
   sépare une clé d'une homonymie.

D'où la règle, et c'est la plus importante de ce guide :

> **Le même identifiant doit porter le même nom de colonne dans toutes vos
> sources.**

`code_produit` dans les trois bases, et un croisement trouve sa clé.
`code_produit` ici et `ref_article` là, et il ne la trouve pas — le modèle
devine une jointure, et rend un produit cartésien qui ne lève aucune erreur.
Mesuré : « compare la production et les ventes du VEL-04 » a rendu « 27 626
unités produites, 2 751 vendues » là où les oracles disent 727 et 125.

### `date_reference` — la colonne sur laquelle se lit la période

`table.colonne`, ou `colonne` seule.
Sans elle, c'est la **première colonne de date du schéma** : juste, mais choisie
par l'ordre du DDL (`agents/retrieval/faits.py`, `_colonne_de_date`).

Déclarez-la dès que la source porte deux dates.
Une date de création de compte n'est pas une période d'activité.
Une date de constat d'inventaire n'est pas un historique.

### `features` — pour prédire

`{modèle: {feature: colonne}}`.
Ce bloc s'adresse au **code**, pas au modèle de langage : il dit quelle colonne
de *cette* source alimente quelle feature de quel modèle du registre.

Le même modèle `titanic` est alimenté par `classes.level` dans une base
Postgres et par `Pclass` dans un CSV : la source est la seule à le savoir.
Sans la déclaration, la colonne était **devinée**, et le résultat changeait.

```yaml
    features:
      titanic:
        sex: passengers.sex
        pclass: classes.level
        age: passengers.age
```

Forme longue quand la source ne représente pas la valeur comme le modèle
l'attend :

```yaml
        pclass:
          column: classes.label
          values: {"1re classe": 1, "2e classe": 2, "3e classe": 3}
```

Ce qui est déclaré est **relu contre le schéma réel avant toute requête** : une
colonne déclarée qui n'existe pas est nommée, avec celles qui existent, au lieu
de finir en SQL en erreur puis en feature absente.

### `filtre_des_sommes` — un filtre qu'on vérifie

`colonne`, `exclure`, `sommes` — la colonne qui porte le filtre, la valeur à
écarter, et les colonnes dont la **somme** l'exige, chacune en `table.colonne`
(`FiltreDesSommes`).

```yaml
    filtre_des_sommes:
      colonne: commandes.statut
      exclure: ANN
      sommes:
        - lignes_commande.quantite
        - lignes_commande.montant_ligne_eur
```

**Rien sur les comptages, et c'est voulu** : une commande annulée reste une
commande reçue.

Pourquoi le déclarer alors que le dictionnaire le dit déjà en phrases : parce
qu'une phrase lue ne se contrôle pas.
Mesuré, le dictionnaire arrivait entier et le code l'ignorait trois fois sur
trois.
Déclaré ici, le filtre est **relu sur le SQL et sur le code produits** avant que
leurs chiffres soient servis (`agents/retrieval/verification.py`,
`agents/analysis/consigne.py`).

### `dictionary` — ce que les données veulent dire

Un fichier Markdown, relatif au catalogue.
Le schéma donne les types ; le dictionnaire donne le sens — les codes, les
valeurs sentinelles, les unités, et les filtres qui vont avec chaque question.

Il a **deux lecteurs** : une personne, et l'agent — celui qui écrit le SQL et
celui qui écrit le Python le reçoivent dans leur prompt système.

**Comment l'écrire est un sujet à soi seul**, mesuré, et il a son document :
**[rediger-un-dictionnaire-de-source.md](rediger-un-dictionnaire-de-source.md)**.
Lisez-le avant d'en écrire un. La règle en une phrase : *dis quel filtre se pose
pour quelle question, pas seulement ce que les codes veulent dire.*

Un plafond : `DAA_DICTIONARY_MAX_CHARS`, **8 000 caractères** par défaut, au-delà
duquel le texte est coupé dans le prompt (`config.py`). `0` le retire.

---

## 3. Où déposer les fichiers

**À côté du catalogue.** Les `path` et les `dictionary` relatifs sont résolus
par rapport au fichier YAML, pas au dossier courant (`load_catalog`).

Sur le service installé, le catalogue vit sous
`/var/lib/data-analyst-agent/sources/`, et c'est là que vont les fichiers de
données ([INSTALLATION §4](INSTALLATION.md#4-le-dossier-de-données)).
Les fichiers doivent appartenir à `1000:1000` — l'utilisateur du conteneur.

### Le piège des chemins, et il est silencieux

C'est le seul point de ce guide qui demande de comprendre plutôt que de
recopier, et il est expliqué en entier dans
[INSTALLATION § Le bac à sable vu du conteneur](INSTALLATION.md#le-bac-à-sable-vu-du-conteneur).

En deux phrases : quand une question demande une figure ou une statistique,
l'application lance un **conteneur frère** et lui monte les fichiers voulus.
Ce montage est exécuté par le démon Docker **de l'hôte**, qui lit les chemins
avec ses yeux à lui, pas avec ceux de l'application.

> **Un fichier que l'application voit à un chemin qui n'existe pas sur l'hôte
> donne un montage vide — sans erreur.**

D'où la règle : **un seul dossier de données, monté au même chemin absolu des
deux côtés, et tout ce qui peut finir monté vit dessous.**
Déposer un fichier de source ailleurs que sous `DAA_DATA_DIR` casse l'analyse,
et la casse silencieusement.

---

## 4. Ce qu'il faut redémarrer

**Le catalogue est lu une fois par processus.** L'orchestrateur est construit au
premier appel et garde son catalogue (`orchestrator/graph.py`, `load_catalog`).

| Ce que vous changez | Ce qu'il faut faire |
|---|---|
| le catalogue : une source ajoutée, retirée, renommée, sa `description` | **redémarrer** : `sudo systemctl restart daa` |
| un `dictionary`, un `date_reference`, `features`, `filtre_des_sommes` | **redémarrer** — ils sont lus avec le catalogue |
| le **contenu** des données (des lignes ajoutées à une table) | rien : chaque question relit la source |

Un cas à part : ce que l'agent dit du **volume** d'une source — tables, lignes,
période — est relevé au premier inventaire puis gardé
`DAA_RELEVE_PEREMPTION` secondes (**900**, soit quinze minutes) avant d'être
relu (`config.py`, `RelevesDuCatalogue`).
Une source qui vient de grossir peut donc annoncer son ancien compte pendant un
quart d'heure.

Le service ne relance **pas** vos bases.
Une source Postgres qui vit dans un autre conteneur doit avoir sa propre
politique de redémarrage, sans quoi la page de chat reviendra sans ses données
([EXPLOITATION § Commander le service](EXPLOITATION.md#commander-le-service)).

---

## 5. Vérifier qu'elle marche

Trois questions, dans une conversation neuve.
Elles n'éprouvent pas la même chose, et c'est pour ça qu'il en faut trois.

**1. « Quelles sources de données as-tu ? »**

Attendu : votre source **nommée**, avec son type.
Si elle manque, le service n'a pas relu le catalogue — ou le YAML n'est pas
valide, et le démarrage l'a dit dans les journaux.

**2. « On travaille sur <votre source>. »**

Attendu : « Entendu : on travaille sur **<nom>** (<type>) », **le nombre de
tables et de lignes**, et la période couverte.
C'est ce tour qui prouve que la source s'**ouvre** vraiment : le catalogue peut
la déclarer et le fichier être introuvable, ou la base injoignable.
Vérifiez la période : si elle porte sur la mauvaise colonne, c'est
`date_reference` qui manque.

**3. Une question qui compte, dont vous savez la réponse.**

« Combien de <lignes> en <année> ? », sur un chiffre que vous pouvez vérifier
sans l'agent.
C'est le seul tour qui éprouve la chaîne entière — le SQL écrit, exécuté, et la
phrase rendue.

**Une quatrième, si vous avez un dictionnaire** : « Que signifie la colonne
<X> ? »
La réponse doit **citer le dictionnaire** — « selon le dictionnaire de `<source>` ».
Si elle ne donne que le type SQL, le dictionnaire n'est pas arrivé : vérifiez
son chemin, relatif au catalogue.

**Et une cinquième, si vous voulez une figure** : « Fais-moi un graphique de
<quelque chose> ».
C'est elle, et elle seule, qui éprouve le bac à sable et les montages de
fichiers du §3.

---

## 6. Les limites à connaître avant de déclarer

- **Un croisement de deux sources charge chaque table en entier, plafonnée à
  `DAA_ANALYSIS_TABLE_MAX_ROWS` — 10 000 lignes.** Au-delà, la somme est calculée
  sur une tranche. La réponse le dit (« Données tronquées : … coupée(s) à 10000
  lignes »), mais le chiffre, lui, est faux. Mesuré : 663 504,99 kWh servis au
  lieu de 1 757 519,23 (`croisement.py`, `ouvrir_le_croisement` ;
  [releve-de-livraison.md](releve-de-livraison.md)).
- **Un tableau rendu à l'utilisateur est plafonné à `DAA_RETRIEVAL_MAX_ROWS` —
  200 lignes.** La coupe est annoncée dans la réponse.
- **Les questions qui croisent deux sources sont la famille la moins sûre** :
  13 sur 17 au relevé de livraison. Celles qui tombent, et pourquoi, sont dans
  [croisement-de-sources.md](croisement-de-sources.md).
- **Une source de fichier n'a aucune relation.** Deux feuilles d'un même classeur
  ne sont pas jointes : si vos données ont des relations, préférez `duckdb` ou
  `postgres`.
- **Un dictionnaire au-delà de 8 000 caractères est coupé** dans le prompt.
- **Le catalogue n'est pas validé à chaud** : une erreur de YAML se voit au
  redémarrage, dans les journaux (`journalctl -u daa`).
- **Aucune source n'est ajoutable depuis l'écran**, et c'est la prochaine étape
  nº 4 de [LIVRAISON.md §4](LIVRAISON.md#4-les-prochaines-étapes).
