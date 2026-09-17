# Le catalogue métier — les Cycles du Ponant

Le catalogue de démonstration précédent — un réseau de bornes de recharge,
décrit dans [`sources-de-demonstration.md`](sources-de-demonstration.md) — est
réaliste, et c'est tout son défaut. « Relevés de puissance horaires », 547 200
lignes : on ne peut ni le raconter devant un prospect, ni vérifier une réponse
de tête. Il a servi à **durcir** le socle, il porte les campagnes de mesure, et
il reste en place pour ça. Il ne sert pas à **montrer** le produit.

Ce document décrit le catalogue écrit pour être montré : ce qu'il contient,
pourquoi chaque chiffre est celui-là, et ce qu'il a effectivement rendu quand on
l'a éprouvé.

## Le critère de conception, et il n'y en a qu'un

**Une réponse fausse doit se voir SANS avoir à la vérifier.**

Tout le reste en découle. Dix-huit clients et non neuf cents ; douze produits et
non cent cinquante ; cent quatre-vingts commandes et non quarante-huit mille. Un
prospect qui entend « 180 commandes » peut suivre ; s'il entend « 47 912 », il
fait confiance ou il ne fait rien — et une démonstration où l'on ne peut que
faire confiance ne démontre rien.

Le corollaire est le vocabulaire. Les libellés sont en français et parlants, et
les codes se citent de mémoire : `VEL-01`, `M-003`, `E-NAN`. On doit pouvoir
écrire un code dans une question **sans l'avoir sous les yeux** — sans quoi
chaque question commence par une consultation du schéma, et la démonstration
montre l'outil au lieu de montrer le travail.

## Le domaine, en une phrase

**Les Cycles du Ponant fabriquent des vélos à Nantes, les vendent à des
revendeurs, et les stockent dans trois entrepôts.**

C'est tout, et c'est le point. Un domaine qui demande deux phrases coûte deux
phrases à chaque question posée devant quelqu'un.

Il a été choisi pour trois raisons, et non pour son pittoresque :

1. **Il se décline naturellement sur les trois types de source.** Un carnet de
   commandes transactionnel (Postgres), un suivi d'atelier (une base analytique),
   un export d'entrepôt (un classeur). Chaque type est là pour une raison qu'on
   peut énoncer en une phrase devant un prospect.
2. **Il se recoupe tout seul.** Le produit est fabriqué, vendu et stocké : son
   code vit dans les trois bases sans qu'on ait rien à plaquer.
3. **Il porte ses pièges tout seul.** Un statut de commande, une durée d'arrêt
   non close, une colonne de mouvement signée : ce sont les défauts ordinaires
   de ce métier, pas des chausse-trapes fabriquées pour l'occasion.

Les données sont **entièrement synthétiques**. Aucune donnée réelle, aucune
donnée personnelle, aucune entreprise existante — les raisons sociales sont
inventées, les villes sont des noms géographiques.

## Les cinq sources

Déclaration : [`sources/metier/catalogue.yaml`](../sources/metier/catalogue.yaml).
Semis : [`scripts/seed_catalogue_metier.py`](../scripts/seed_catalogue_metier.py).

| Source | Type | Tables | Lignes | Période couverte | Colonne de date désignée |
|---|---|---|---|---|---|
| `ventes` | `postgres` | 4 | **673** (clients 18, produits 12, commandes 180, lignes_commande 463) | 2025-01-02 → 2025-12-31 | `commandes.date_commande` |
| `production` | `duckdb` | 4 | **222** (ateliers 3, machines 9, ordres_fabrication 140, arrets_machine 70) | 2025-01-04 → 2025-12-28 | `ordres_fabrication.date_lancement` |
| `stocks` | `file` (XLSX) | 3 | **519** (entrepots 3, mouvements 480, inventaire 36) | 2025-01-02 → 2025-12-30 | `mouvements.date_mouvement` |
| `iris` | `file` (CSV) | 1 | **150** | — | *(aucune colonne de date)* |
| `titanic` | `file` (CSV) | 1 | **891** | — | *(aucune colonne de date)* |

Les trois bases métier portent chacune **deux** colonnes de date, et c'est
exactement le cas où la désignation est un choix de métier : `clients.date_creation`
n'est pas la période d'activité, `arrets_machine.date_arret` n'est pas la période
de production, `inventaire.date_inventaire` est un constat daté et non un
historique. `iris` et `titanic` n'en désignent aucune parce qu'ils n'en ont pas.

### `iris` et `titanic` : recopiés, jamais engendrés

Ces deux-là ne sont pas au thème du fabricant de vélos, et ils n'y seront jamais.
Ce sont des **jeux de référence** : leurs chiffres sont dans la littérature, dans
[`models/registry.yaml`](../models/registry.yaml) et dans toutes les campagnes qui
précèdent. Les modifier — ne serait-ce que d'une ligne, ne serait-ce que pour
traduire un en-tête — ferait mentir chacune de ces comparaisons d'un coup, et
sans bruit.

Le semis les **recopie octet pour octet** depuis `sources/`, et vérifie la copie
au `sha256`. Ils sont servis comme **fichiers**, parce que c'est sous cette forme
qu'on fait des statistiques et du ML dessus.

C'est aussi pourquoi ils ne sont pas versionnés dans `sources/metier/` : ce
seraient deux vérités pour un même fichier, et deux vérités finissent toujours
par diverger. Une seule copie dans git, plus une étape de copie vérifiée.

### Ce qui les relie : le produit

`code_produit` vit dans les trois bases métier. C'est la seule clé partagée —
`produit_id`, `client_id`, `machine_id` sont internes à leur base et ne joignent
rien au-dehors.

**« Combien de produits ? » a plusieurs réponses légitimes :**

| Source | Réponse | Pourquoi |
|---|---|---|
| `ventes` | **12** | tout ce qui se vend : 8 vélos et 4 accessoires |
| `stocks` | **12** | tout ce qui se stocke : les mêmes 12 |
| `production` | **8** | les seuls vélos — les 4 accessoires sont **achetés** à un fournisseur |

L'écart est un **fait du métier** : on ne fabrique pas tout ce qu'on vend. Ce
n'est ni une troncature, ni une donnée manquante, et les deux réponses sont
justes. C'est ce qui donne au verrou de source quelque chose à protéger : une
question posée sans nommer la source risque une réponse juste **pour une autre
base que celle qu'on avait en tête**.

Le partage se lit dans le code lui-même — `VEL-**` est fabriqué, `ACC-**` est
acheté — ce qui permet de vérifier l'écart à l'œil, sans requête.

## Les trois pièges de modélisation, assumés et documentés

Un par base, et de **trois familles différentes** : trois fois le même piège
n'éprouverait qu'une seule chose. Chacun est documenté dans le dictionnaire de
sa source, selon
[`rediger-un-dictionnaire-de-source.md`](rediger-un-dictionnaire-de-source.md) —
qui est une contrainte mesurée et non un conseil.

| Nº | Source | Famille | La colonne | Ce qu'on obtient en tombant dedans | Ce qu'on obtient en l'évitant |
|---|---|---|---|---|---|
| 1 | `ventes` | un **code de statut** à filtrer pour une mesure et pas pour une autre | `commandes.statut` | chiffre d'affaires **1 636 093,00 €** | **1 496 743,00 €** (`statut <> 'ANN'`) |
| 2 | `production` | une **valeur sentinelle** qui fausse une moyenne | `arrets_machine.duree_minutes` | durée moyenne d'arrêt **175,99 min** | **202,10 min** (`duree_minutes >= 0`) |
| 3 | `stocks` | une **colonne signée** dont la somme brute répond à une autre question | `mouvements.quantite` | unités sorties **4 293** | **2 279** (`sens = 'SOR'`, en valeur absolue) |

### 1. `ventes.commandes.statut` — le même code, deux traitements

`LIV` livrée (116), `EXP` expédiée (48), `ANN` annulée (16). Les 180 lignes sont
180 commandes **reçues**, quel que soit le statut : un comptage ne se filtre pas.
Mais une commande annulée n'a rien facturé et rien expédié : **toute somme
d'argent ou d'unités vendues** pose `statut <> 'ANN'`.

C'est la famille « quel filtre pour quelle question », et elle est la plus
coûteuse parce qu'elle est **invisible** : les deux chiffres sont plausibles, et
l'écart de 139 350,00 € ne déclenche ni exception, ni log.

### 2. `production.arrets_machine.duree_minutes` — la sentinelle et son sosie

`-1` veut dire « arrêt encore ouvert, durée non close » : ce n'est pas une durée,
et `avg()` la moyenne sans broncher. **9 arrêts sur 70** sont dans ce cas.

Le dictionnaire nomme aussi **ce qui lui ressemble et qui est vrai** : `0` est une
fausse alerte suivie d'une remise en route immédiate — la machine s'est bien
arrêtée, et elle n'a rien coûté. **8 arrêts sur 70**, et ils **entrent** dans la
moyenne. Sans ce contre-cas, « écarter les valeurs négatives ou nulles » marcherait
aussi bien que la règle juste, et le piège ne piégerait rien.

### 3. `stocks.mouvements.quantite` — la somme qui répond à côté

Positive à l'entrée, négative à la sortie. `sum(quantite)` **répond**, sans erreur
et sans avertissement : il rend la **variation nette** du stock, +4 293. Ce n'est
pas « ce qui est sorti », qui vaut 2 279, ni « ce qui est entré », qui vaut 6 572.

Les trois questions sont légitimes et ont trois réponses différentes. C'est la
famille la plus sournoise des trois, parce qu'aucun des chiffres n'est faux en
soi — c'est leur **appariement à la question** qui l'est.

### Un quatrième écueil, trouvé par la mesure et non par nous

Le premier tirage de la question `ca-par-canal` a rendu une figure et les chiffres
**2 630 871 / 1 017 293 / 949 647**. Ce ne sont ni les bons, ni ceux du piège nº 1 :
c'est une **jointure qui duplique l'en-tête**. `commandes.montant_total_eur` est un
total de niveau commande ; joint à `lignes_commande`, il est apporté autant de
fois que la commande a de lignes, et la somme gonfle de 2,57 — le nombre moyen de
lignes par commande.

Deux choses en sont sorties, et l'ordre compte :

1. **Le dictionnaire de `ventes` porte désormais la règle** (son piège nº 2) :
   une somme d'argent par client ou par canal se calcule sur `commandes` seule ;
   dès qu'on descend au produit, on somme `montant_ligne_eur` et jamais
   `montant_total_eur`.
2. **L'oracle de la question a été resserré.** Il n'exigeait qu'une **figure**,
   et comptait donc ce tour comme juste : il mesurait qu'un graphique **existe**,
   pas qu'il **dise vrai**. Les deux questions à graphique exigent désormais
   leurs chiffres. C'est le défaut le plus embarrassant de ce chantier, et il
   serait passé sans la relecture des réponses brutes.

Ce n'est pas un quatrième piège de famille : c'est une erreur de jointure, que
n'importe quel modèle en étoile porte. Elle est documentée là où elle se produit.

## Les données sont engendrées, pas versionnées

Le tirage est figé (`GRAINE = 20260917`), donc deux exécutions rendent le même
contenu. Ce qui est versionné : le script de semis, le catalogue, et les cinq
dictionnaires. Ce qui ne l'est pas : la base Postgres (elle ne se versionne pas),
le `.duckdb`, le classeur et les deux CSV recopiés — cf.
[`sources/metier/.gitignore`](../sources/metier/.gitignore).

**Les oracles sont calculés depuis les données ÉCRITES**, relues dans Postgres,
dans DuckDB et dans le classeur — jamais depuis les paramètres du tirage. La
différence n'est pas théorique : un arrondi, une collision, un tirage qui tombe
pile sur la sentinelle, et le masque annonce un chiffre que la base ne contient
pas. C'est la base que l'agent interroge ; c'est donc elle qui a raison.

### Preuve : le semis rejoué deux fois

```
uv run python scripts/seed_catalogue_metier.py    # deux fois de suite
```

| Fichier | sha256 (16 car.) — 1re exécution | 2de exécution | Identique |
|---|---|---|---|
| `stocks.xlsx` | `d14dccacf7671f6c` | `d14dccacf7671f6c` | **oui** |
| `iris.csv` | `9cc1c345c71bcc9b` | `9cc1c345c71bcc9b` | **oui** |
| `titanic.csv` | `4a437fde05fe5264` | `4a437fde05fe5264` | **oui** |
| `production.duckdb` (contenu) | `d4de02eccbc2ffa1` | `d4de02eccbc2ffa1` | **oui** |

Le classeur est figé octet pour octet : `figer_le_classeur` neutralise les deux
horodatages qu'openpyxl y laisse — `docProps/core.xml` et la date de chaque
entrée du zip. Le `.duckdb` ne se laisse pas figer (ordre d'écriture des blocs) ;
son **contenu**, si, et c'est ce que la quatrième ligne mesure — le `sha256` des
quatre tables relues dans l'ordre. Postgres n'est pas un fichier : son contenu se
vérifie par les oracles, qui sont relus dans la base.

Les empreintes de `iris.csv` et `titanic.csv` sont celles de leurs originaux dans
`sources/`, et le semis échoue si la copie en diffère.

## Les douze questions de démonstration

Elles sont écrites dans le runner
[`scripts/mesure_questions_metier.py`](../scripts/mesure_questions_metier.py) et
non dans cette page, et l'inverse serait un piège : deux listes de questions
finissent toujours par diverger, et c'est la page qu'on relit tandis que c'est
le runner qui mesure.

Elles couvrent, par construction : **au moins une par source** (cinq sources),
**une jointure sur trois tables**, **deux graphiques**, **trois questions qui
tombent dans un piège** si le dictionnaire n'est pas lu, et **une prédiction**.

```
DAA_CATALOG_PATH=sources/metier/catalogue.yaml \
  uv run python scripts/mesure_questions_metier.py --tirages 3
```

Moteur : vLLM, `google/gemma-4-E4B-it-qat-w4a16-ct`, `http://localhost:8100/v1`.

| # | Question | Source | Ce qu'elle montre | Attendu | Obtenu | Score |
|---|---|---|---|---|---|---|
| 1 | « Combien de commandes avons-nous reçues en 2025 ? » | `ventes` | le comptage qui ne se filtre PAS | 180 | 180 | **3/3** |
| 2 | « Quel chiffre d'affaires avons-nous réalisé en 2025 ? » | `ventes` | **piège nº 1** | 1 496 743,00 € | 1 496 743,00 € | **3/3** |
| 3 | « Quel produit s'est le plus vendu en nombre d'unités en 2025 ? » | `ventes` | **jointure sur trois tables** | ACC-03, 264 | ACC-03, 264 | **3/3** |
| 4 | « Quel est notre meilleur client en chiffre d'affaires ? » | `ventes` | un classement en euros, donc filtré | Vélocité Bordeaux, 170 149,00 € | idem | **3/3** |
| 5 | « Fais-moi un graphique du chiffre d'affaires par canal. » | `ventes` | **graphique** sur Postgres | 862 229 / 331 499 / 303 015 | idem + figure | **3/3** ¹ |
| 6 | « Quelle est la durée moyenne d'un arrêt machine ? » | `production` | **piège nº 2** | 202,10 min | 202,10 min | **3/3** |
| 7 | « Quelle machine a connu le plus d'arrêts en 2025 ? » | `production` | classement sur jointure DuckDB | M-009, 16 | M-009, 16 | **3/3** |
| 8 | « Combien de produits différents fabriquons-nous ? » | `production` | **le recoupement** (8 ici, 12 dans `ventes`) | 8 | 8 | **3/3** |
| 9 | « Combien d'unités sont sorties des entrepôts en 2025 ? » | `stocks` | **piège nº 3** | 2 279 | 2 279 | **3/3** |
| 10 | « Fais-moi un graphique des mouvements par entrepôt. » | `stocks` | **graphique** sur un classeur Excel | 170 / 168 / 142 | idem + figure | **3/3** |
| 11 | « Combien de fleurs y a-t-il par espèce ? » | `iris` | une source de référence, servie comme fichier | 50 / 50 / 50 | 50 / 50 / 50 | **3/3** |
| 12 | « Un homme de 30 ans en 3e classe… aurait-il survécu ? » | `titanic` | **prédiction** par le modèle du registre | n'a pas survécu | n'a pas survécu (91,1 %) | **3/3** |

**12 questions sur 12 à 3/3.**

¹ **Et ce qu'il faut dire sur cette ligne.** Lors du passage groupé — les quatre
campagnes de ce chantier lancées **en parallèle** sur le même serveur, plus leurs
bacs à sable — la question 5 a rendu **35/36 tours** au total : un tirage a échoué
sur « le code produit n'a pas pu s'exécuter après 3 tentatives », après 60
secondes. Ce n'est **pas** un chiffre faux : c'est le bac à sable qui n'a pas
abouti sous contention. Rejouée seule, la même question rend **3/3** avec les
trois bons chiffres. Les deux relevés sont gardés : le 3/3 dit ce que la question
vaut, le 35/36 dit ce que coûte le parallélisme, et effacer le second ferait
passer une limite d'exécution pour une propriété du catalogue.

### Ce que les questions 11 et 12 rendent vraiment

La question 11 répond « 3 lignes retournées — voir le tableau ci-dessous » : le
prompt de l'agent SQL lui **interdit** de recopier les lignes dans sa phrase, et
les trois comptes sont dans le tableau d'artefacts. Un oracle qui ne lirait que
la phrase ferait passer ce tour pour muet — c'est pourquoi le verdict lit **la
phrase ET le tableau**.

La question 12 rend `Prédiction (titanic) : n'a pas survécu (probabilité 91.1 %)`.
Sa première rédaction disait « voyageant seul », et l'agent a **refusé de
deviner** : « je ne peux pas encore lancer la prédiction — parch : valeur
manquante ». C'est le comportement qu'on veut. C'est donc la **question** qui a
été corrigée, pas le produit : elle nomme désormais les sept features. Une
question de démonstration qui laisse une feature implicite mesure la devinette.

## Les trois pièges, éprouvés des deux côtés

Affirmer qu'un dictionnaire sert et ne jamais montrer ce qui se passe quand il
manque, c'est demander qu'on nous croie. Le runner porte donc un témoin —
`--sans-dictionnaire` retire les dictionnaires **en mémoire**, le temps de la
mesure, sans rien écrire dans le dépôt :

```
DAA_CATALOG_PATH=sources/metier/catalogue.yaml \
  uv run python scripts/mesure_questions_metier.py --tirages 3 \
  --sans-dictionnaire --seulement ca-2025 arrets-duree unites-sorties ca-par-canal
```

| Piège | Question | Sans dictionnaire | Avec dictionnaire |
|---|---|---|---|
| nº 1 — code de statut | chiffre d'affaires 2025 | **1 636 093,00 €** — 3/3 | **1 496 743,00 €** — 3/3 |
| nº 2 — sentinelle | durée moyenne d'un arrêt | **176 minutes** — 3/3 | **202,10 minutes** — 3/3 |
| nº 3 — colonne signée | unités sorties des entrepôts | **−2 279** — 3/3 | **2 279** — 3/3 |
| jointure qui duplique | CA par canal | **948 283 / 382 646 / 305 164** — 3/3 | **862 229 / 331 499 / 303 015** — 3/3 |

**0/12 sans dictionnaire, 12/12 avec.** Et le témoin est **parfaitement stable** :
les trois tirages de chaque question rendent le même chiffre faux, au centime. Ce
n'est donc pas une hésitation du modèle qu'on mesure, c'est une **lecture par
défaut** — celle qu'il fait quand rien ne lui dit quel filtre pose quelle
question. C'est exactement ce que
[`rediger-un-dictionnaire-de-source.md`](rediger-un-dictionnaire-de-source.md)
avait établi sur l'autre catalogue, reproduit ici sur trois familles de pièges au
lieu d'une.

Deux détails valent d'être lus :

- **le piège nº 3 rend `−2 279` et non `4 293`.** Sans dictionnaire, l'agent
  filtre bien les sorties mais **garde le signe** : il répond « le total des
  unités sorties est de −2 279 ». Le chiffre est le bon, la grandeur ne l'est
  pas — un nombre d'unités ne peut pas être négatif. C'est un faux plus discret
  que celui qu'on avait prévu, et plus instructif : le dictionnaire ne sert pas
  seulement à choisir un filtre, il sert à choisir ce qu'on **rend**.
- **le CA par canal sans dictionnaire ne fan-oute pas** : il rend
  948 283 / 382 646 / 305 164, c'est-à-dire les bons canaux avec les **commandes
  annulées incluses**. Le fan-out du premier tirage était donc bien un accident
  de rédaction du code d'analyse, et pas la lecture par défaut.

## L'instance, basculée sur ce catalogue

Le service installé servait `sources/demonstration/catalogue.yaml`. Il sert
désormais celui-ci.

| Quoi | Valeur |
|---|---|
| Fichier d'environnement | `/etc/data-analyst-agent/daa.env`, `DAA_CATALOG_PATH` |
| Avant | `/var/lib/data-analyst-agent/sources/demonstration/catalogue.yaml` |
| Après | `/var/lib/data-analyst-agent/sources/metier/catalogue.yaml` |
| Copie de l'ancien | `/etc/data-analyst-agent/daa.env.avant-catalogue-metier` (0600 root) |
| Base Postgres | `daa_metier`, dans le conteneur `daa-postgres` |
| Redémarrage du conteneur Postgres | `unless-stopped` — **déjà posé**, vérifié et laissé tel quel |

Les fichiers de la source (`catalogue.yaml`, les cinq dictionnaires,
`production.duckdb`, `stocks.xlsx`, `iris.csv`, `titanic.csv`) sont installés sous
`/var/lib/data-analyst-agent/sources/metier/`, qui est le dossier bind-monté **au
même chemin** dans le conteneur. L'empreinte du classeur installé est celle du
dépôt, `d14dccacf7671f6c`.

**L'ancien catalogue n'est pas retiré.** Ses fichiers restent sous
`sources/demonstration/`, et il suffit de remettre l'ancienne ligne — ou de
restaurer la copie du fichier d'environnement — pour y revenir. C'est lui qui
porte les campagnes de mesure : le supprimer coûterait la comparaison avec tout
ce qui précède.

### Une conversation complète, rejouée en HTTPS

Sur `https://<hôte>:8443`, à travers le mandataire nginx, avec un compte `sonde-metier`
créé pour l'occasion (mot de passe engendré dans
`/etc/data-analyst-agent/sondes/sonde-metier.pass`, 0600 root — il n'est écrit
nulle part ailleurs). Certificat vérifié contre l'autorité locale
(`ssl_verify_result = 0`), cookies `Secure`, double soumission anti-CSRF.

Cinq tours, un seul fil :

| # | Message | Réponse | Source liée | Artefact |
|---|---|---|---|---|
| 1 | « Bonjour, quelles sources de données as-tu ? » | « J'ai accès aux sources suivantes : `ventes`, `production`, `stocks`, `iris`, et `titanic`. » | *(aucune)* | — |
| 2 | « On va travailler sur ventes. » | « Entendu : on travaille sur **ventes** (postgres) […] 4 tables, 673 lignes […] période du 2025-01-02 au 2025-12-31. Je garde cette source. » | `ventes` | — |
| 3 | « Quel chiffre d'affaires avons-nous réalisé en 2025 ? » | « **1 496 743,00 €** […] dont le statut n'est pas 'ANN', conformément à la règle stipulant que les sommes en euros doivent exclure les commandes annulées. » | `ventes` | tableau |
| 4 | « Fais-moi un graphique du chiffre d'affaires 2025 par canal de vente. » | « […] "magasin" **862 229,0 EUR**, "en ligne" **331 499,0**, "grossiste" **303 015,0**. » | `ventes` | **figure PNG** |
| 5 | « Et maintenant, dans stocks, combien d'unités sont sorties des entrepôts en 2025 ? » | « **Je passe sur la source `stocks`** — on travaillait sur `ventes`. […] **2 279** […] en sommant la valeur absolue de `quantite` où le `sens` est 'SOR'. » | `stocks` | tableau |

Les cinq tours demandés sont là — inventaire, choix de source, question SQL,
graphique, bascule de source — et les deux pièges rencontrés en chemin sont
évités, avec la règle du dictionnaire **citée dans la réponse**. Le tour 5 annonce
la bascule avant de répondre : c'est le comportement du verrou de source, et c'est
ce qu'on veut voir devant un prospect.

## Aucune régression

| Campagne | Catalogue | Résultat |
|---|---|---|
| `mesure_questions_metier.py --tirages 3` | `metier` | **12 questions sur 12 à 3/3** (35/36 tours en passage groupé, cf. ¹) |
| `mesure_questions_metier.py --sans-dictionnaire` | `metier` | **0/12** — le témoin |
| `mesure_parcours_de_demonstration.py --tirages 1` | `demonstration` | **48/48 tours conformes** |
| `mesure_surface_conversationnelle.py` | **par défaut** | **35/36** questions méta, **4/4** témoins |
| `uv run pytest -q` | — | **1 264 passés, 99,59 %** (1 234 avant ce chantier, + 30 nouveaux) |

### Deux relevés qu'il faut lire en entier

**Le parcours de l'ancien catalogue a d'abord rendu 30/48, et ce n'était pas une
régression.** Ce chantier travaille dans un **worktree**, et
`sources/demonstration/` y contient ce que git versionne — pas `telemetrie.duckdb`
ni `facturation.xlsx`, qui sont engendrés et ignorés. Les 18 tours en échec
portaient tous la même erreur (« la source de données n'a pas pu être
interrogée », « je n'ai pas pu relire ma propre configuration ») et pas un seul
chiffre faux. Semis de l'ancien catalogue dans le worktree, rejeu : **48/48**.
Le premier chiffre est gardé ici parce qu'il coûte une demi-heure à qui le
retrouvera sans avertissement.

**La surface conversationnelle rend 35/36 là où le document en annonce 36/36.**
La question manquante est `volumetrie-globale` — « Quelle est la taille de tes
données ? » — à laquelle l'agent a répondu par une relance (« je peux te donner
le nombre de tables et de lignes pour chacune, souhaites-tu le détail ? ») là où
l'oracle attend les volumes. Ce chantier **n'a touché aucun code** : il ajoute un
catalogue, un script de semis, un runner et un fichier de tests. Le catalogue par
défaut, sur lequel cette campagne tourne, est inchangé. C'est donc un
intermittent, sur une question dont la réponse de référence est elle-même une
relance — et il est signalé ici plutôt que tu, parce qu'un 35/36 non expliqué
dans une page vaut moins qu'un 35/36 expliqué.

## Où ça vit

| Quoi | Où |
|---|---|
| La déclaration | [`sources/metier/catalogue.yaml`](../sources/metier/catalogue.yaml) |
| Les cinq dictionnaires | [`sources/metier/dictionnaires/`](../sources/metier/dictionnaires/) |
| Le semis, à graine fixe, et les oracles | [`scripts/seed_catalogue_metier.py`](../scripts/seed_catalogue_metier.py) |
| Les douze questions, et le témoin | [`scripts/mesure_questions_metier.py`](../scripts/mesure_questions_metier.py) |
| Ce que la suite unitaire en tient | [`tests/catalogues/test_catalogue_metier.py`](../tests/catalogues/test_catalogue_metier.py) |
| Comment écrire un dictionnaire | [`rediger-un-dictionnaire-de-source.md`](rediger-un-dictionnaire-de-source.md) |
| L'autre catalogue, qui reste | [`sources-de-demonstration.md`](sources-de-demonstration.md) |
