# Croiser deux sources déclarées

`Plan.source` est un `str | None` : un plan porte UNE source. Une question qui
en croise deux n'avait donc **aucun chemin**, et la demande de précision était ce
qu'on servait faute de mieux.

Mesuré le 2026-09-22 sur `e126e56`, catalogue métier, deux formulations, même
issue :

```
« compare les quantités produites et les quantités vendues par produit »
« est-ce qu'on vend plus que ce qu'on produit ? »
   system : aucun outil appelé — passe au planificateur
   plan   : clarification demandée
   → « Sur quelle source veux-tu travailler : ventes, production, stocks,
      iris, titanic ? »
```

Ce catalogue a pourtant été monté POUR ces questions-là : `ventes` (Postgres),
`production` (DuckDB), `stocks` (Excel), et un produit se retrouve dans les
trois — `code_produit` est le seul identifiant qu'elles partagent.

## Ce que le périmètre est, et ce qu'il n'est pas

Une conversation travaille sur **un périmètre cohérent qui s'enrichit**, et ce
périmètre tient en trois choses : la source liée au fil, les tableaux que ce fil
a déjà produits, et un petit apport qu'on ajoute en cours de route. Ce n'est
**pas** la capacité de croiser n'importe quelle paire du catalogue.

**Focaliser vaut mieux que tout exposer.** On monte les sources que le tour
désigne, et elles seules. C51 a chiffré ce que coûte l'inverse : deux fichiers
montés quand un seul est visé, le mauvais choisi 10 fois sur 10, un chiffre faux
et **plausible**, donc invisible.

## L'information ne manquait pas — le code la jetait

Avant d'écrire une ligne, on a demandé au planificateur ce qu'il faisait de ces
questions. Trois tirages chacune, catalogue métier :

| message | `capability` | `source` |
|---|---|---|
| « compare les quantités produites et les quantités vendues par produit » | `analyze` | `production, ventes` (2/3) |
| « est-ce qu'on vend plus que ce qu'on produit ? » | `query` | `ventes, production` (3/3) |
| « compare la production et les ventes du VEL-04 » | `analyze` | `production, ventes` (3/3) |
| « ventes ou production ? » | `query` | `''` (3/3) |

Le planificateur écrit **déjà** les deux noms, empaquetés dans le champ qui en
attend un. `_match_source_name` rend `None` dès qu'il en trouve deux, et le tour
ressortait en demande de précision. C'est le motif de `_mount_workspace` :
*l'information ne manquait pas, l'arbitrage manquait.*

C'est ce qui a décidé du chemin, et c'est ce qui permet de ne toucher **ni aux
sept prompts, ni aux huit fiches d'outils** : les deux empreintes SHA-256 ne
bougent pas d'une ligne.

## Les trois chemins, et pourquoi celui-ci

| chemin | ce qu'il coûte | ce qu'il interdit |
|---|---|---|
| le **bac à sable** reçoit déjà plusieurs fichiers sous `/data/` | matérialise toutes les tables des deux sources pour une jointure sur 12 lignes | ne sert que `analyze` — or la moitié de ces questions routent en `query` |
| deux requêtes qui se **rejoignent en mémoire** (sources éphémères) | trois tours pour une question qui en demande un | `_objets_vises` ne sait désigner qu'UN artefact ; il faudrait un décomposeur, non vérifiable |
| **DuckDB attache plusieurs fichiers** dans une même requête | une matérialisation, déjà pratiquée par `_decor_de_donnees` | rien sur ce catalogue ; une source énorme se paierait, mais on ne monte que ce qui est visé |

Le troisième est retenu : c'est **le plus court qui donne une réponse juste et
vérifiable**. Les tables des sources désignées sont matérialisées dans une
connexion DuckDB, préfixées par le nom de leur source, et l'agent SQL existant
les voit comme un schéma unique — il garde ses trois outils, sa correction
d'erreur et son dictionnaire.

**Il accueille naturellement le troisième cas** — « j'ai ce fichier Excel que je
peux te donner, il y a un croisement avec la donnée que tu viens de me faire ».
Un fichier déposé est une `FileSource` de plus dans la liste passée à
`ouvrir_le_croisement`, et les tableaux du fil sont **déjà** des sources
éphémères (`workspace.as_sources()`). Les deux autres chemins fermaient cette
porte : le premier n'expose que des CSV sans schéma, le second ne sait désigner
qu'un objet à la fois.

## Deux conditions pour ouvrir un périmètre

L'empaquetage dit que le planificateur a vu deux sources ; il ne dit pas qu'on
lui demande quelque chose **dessus**. On exige donc aussi que le MESSAGE dise
autre chose que des noms de sources — le décompte de `choix_de_source`, au
pluriel (`mots_hors_des_noms`, `MOTS_EN_PLUS_D_UN_CHOIX`), réemployé et non
réécrit : deux décomptes du même fait divergent, et celui-ci décide d'un montage.

C'est ce qui fait tenir le témoin **par construction** : « ventes ou
production ? » nomme deux sources et ne demande rien, donc continue de faire
choisir.

## Ce que le croisement doit porter, et qui a failli manquer

### Les clés étrangères

`CREATE TABLE ... AS SELECT` ne recopie **aucune contrainte**. La première
version arrivait donc au modèle sans une seule clé étrangère — y compris celles
INTERNES à chaque source, qu'il avait gratuitement avant. Les trois croisements
qui aboutissaient rendaient trois jointures inventées, **et aucune n'a levé
d'erreur** :

| ce que l'agent a répondu | le vrai chiffre | la cause |
|---|---|---|
| « 20 557 vendues, 180 669 produites » | 1 828 et 4 413 | produit cartésien |
| « aucun produit trouvé à la fois dans les ventes et dans la production » | les 8 vélos | jointure sur `produit_id`, clé **interne** à `ventes` |
| « quantité produite : 0 pour chaque produit » | 4 413 | même cause |

C'est la propriété que `duckdb_excel` revendique pour une base DuckDB — *« un
schéma en étoile dont on tait les FK oblige le modèle à deviner les jointures »*
— et que le croisement lui retirait en silence. `prefixer()` renomme désormais
les tables **et leurs clés** : `REFERENCES ventes_produits(produit_id)`.

### Le schéma doit nommer les tables comme les fichiers

Sur le chemin d'analyse, les fichiers étaient montés sous
`/data/production_ordres_fabrication.csv` pendant que le DDL annonçait `TABLE
ordres_fabrication`. Le code généré ouvrait le fichier sans préfixe, ne le
trouvait pas, et rendait « quantité produite : 0 » — un zéro, pas une erreur,
dans une phrase qui a l'air d'une réponse. Une ligne de titre annonçant le
préfixe **ne suffit pas** : ce que le modèle recopie est le nom qu'il lit dans le
DDL.

### Les dictionnaires des DEUX sources

`dictionary_max_chars` taille le dictionnaire d'UNE source. `ventes` +
`production` font 12 978 caractères pour un budget de 8 000 : `preparer` garde
les sections dans l'ordre, et ce qui saute est la fin du document — c'est-à-dire
précisément la section des **pièges**. Le budget suit donc le nombre de sources
du périmètre, multiplié et non relevé en dur, pour qu'une installation qui l'a
baissé le voie toujours respecté.

Le piège des commandes annulées (`statut <> 'ANN'`) vaut dans un croisement
comme ailleurs : un chiffre d'affaires croisé qui les compte est faux.

## Les oracles

Tous relus dans les trois sources, et vérifiables de tête sur le catalogue —
180 commandes, 140 ordres de fabrication, 8 vélos fabriqués, 12 produits.

| grandeur | valeur |
|---|---|
| fabriqué, par vélo | VEL-01 **689**, VEL-04 **727**, VEL-07 **461** — total **4 413** |
| vendu (`statut <> 'ANN'`) | VEL-01 **123**, VEL-04 **125** — vélos **1 078**, tous produits **1 828** |
| vendu sans le filtre — le piège | VEL-01 141, VEL-04 131 — vélos 1 180 |
| CA du VEL-07 (`statut <> 'ANN'`) | **323 700 €** — sans le filtre : 341 130 € |
| en stock, VEL-04 | **225** |

**Un oracle qui exige UN mot refuse des réponses justes ; un oracle qui n'exige
rien en bénit de fausses.** Les deux ont été payés ici, dans la même passe :

- il exigeait un artefact `application/json`, que seul le chemin SQL produit :
  trois croisements aboutis, dictionnaires appliqués et chiffres justes, étaient
  comptés en échec pour n'avoir pas rendu un tableau que leur chemin ne rend
  jamais. Le critère est devenu « le tour a-t-il REGARDÉ les données » —
  `retrieval` ou `analysis` dans la trace ;
- il ne demandait à `vend-plus-quon-produit` que sa conclusion : il a compté
  **3/3** une réponse annonçant « 20 557 vendues, 180 669 produites », deux
  chiffres faux d'un facteur 40 et un verdict juste **par accident**. Le verdict
  reste demandé ; le chiffre le fonde.

## Le relevé

```
DAA_CATALOG_PATH=sources/metier/catalogue.yaml \
  uv run python scripts/mesure_croisement_de_sources.py --tirages 3
```

Moteur : vLLM, `google/gemma-4-E4B-it-qat-w4a16-ct`, `http://localhost:8100/v1`.
Catalogue : `sources/metier/catalogue.yaml`. Neuf questions, trois tirages, un
fil neuf par question et par tirage.

| question | fil | avant (`e126e56`) | après | ce qui décide |
|---|---|---|---|---|
| `produites-vs-vendues` | vierge | 0/3 | **0/3** | jamais atteinte — le plan n'empaquette pas |
| `vend-plus-quon-produit` | vierge | 0/3 | **0/3** | atteinte ; jointure inventée par le modèle |
| `ca-produit-vs-fabrique` | vierge | 0/3 | **3/3** | croisement juste, piège du dictionnaire évité |
| `vel04-production-ventes` | vierge | 0/3 | **0/3** | jamais atteinte — l'agent système intercepte |
| `fabrique-vendu-stock` (3 sources) | vierge | 0/3 | **0/3** | jamais atteinte — le plan n'empaquette pas |
| `produites-vs-vendues-fil-lie` | `ventes` | 0/3 | **0/3** | atteinte ; jointure inventée par le modèle |
| **témoin** — une seule source | `ventes` | 3/3 | **3/3** | 1 496 743,00 €, `ventes` seule |
| **témoin** — question de sens | `ventes` | 3/3 | **3/3** | reste une question de sens |
| **témoin** — faire choisir | vierge | 3/3 | **3/3** | « ventes ou production ? » fait toujours choisir |

**9/27 → 12/27.** Les trois témoins étaient déjà verts et le restent : ils ne
pouvaient que se dégrader, et c'est ce qu'on leur demande de prouver.

### Ce qui marche

Le mécanisme fait ce qu'on lui demande **quand il est atteint et que le modèle
joint correctement** : `ca-produit-vs-fabrique` monte `ventes` et `production`,
applique les deux dictionnaires, rend les 461 unités fabriquées du VEL-07 et un
chiffre d'affaires **annulées exclues** — le piège nº 1 dans un croisement.

### Ce qui ne marche pas, et pourquoi on s'arrête là

**Trois questions n'atteignent jamais le croisement**, pour des raisons qui lui
sont extérieures :

- `vel04-production-ventes` part en `system → synthesize` : l'agent **système**
  s'en empare parce que le message nomme deux sources, et sert leurs deux
  fiches. Le départage qui le corrigerait — exiger que le message demande autre
  chose que des noms — casserait « qu'est-ce que t'appelles source vente,
  production, stock ? », qui doit justement recevoir les fiches. Une question
  gagnée contre une campagne de 60 tours mise en risque : on ne l'a pas fait.
- `produites-vs-vendues` et `fabrique-vendu-stock` partent en proposition de
  sources : le planificateur n'a pas empaqueté les deux noms sur ce tirage-là.
  Le rendre fiable demanderait un champ de plus dans le contrat de sortie, que
  `orchestrator/plan.py` documente comme payant sur les capacités voisines —
  mesuré, pas supposé.

**Deux questions l'atteignent et le modèle rate la jointure.**
`vend-plus-quon-produit` rend « 21 383 vendues, 180 669 produites » là où les
oracles disent 1 828 et 4 413 : un produit cartésien, malgré les clés étrangères
au schéma et le piège nº 4 du dictionnaire (« le seul identifiant partagé est
`code_produit` »). `produites-vs-vendues-fil-lie` joint sur `produit_id` et rend
0. Le croisement leur a donné tout ce qu'il pouvait leur donner — les tables, les
clés, les deux dictionnaires entiers ; ce qui manque est ailleurs.

**La capacité est donc partielle, et le relevé le dit plutôt que de le taire.**
Un croisement complet sur les six mesurés, et c'est celui qui porte le piège de
modélisation. Les deux formulations du relevé d'origine ne sont pas réparées.

## Ce qui n'a pas bougé

Campagnes **séquentielles**, jamais de front — le parallélisme coûte un tirage
sur contention du bac à sable, et c'est documenté
([sources-metier.md](sources-metier.md)). Lancées dans l'ordre de leur
EXPOSITION au changement : ce qui touche la source de travail et le montage des
données d'abord.

| campagne | catalogue | attendu | obtenu |
|---|---|---|---|
| questions métier | `sources/metier/` | 12/12 | **36/36 tours** |
| sources nommées | `sources/metier/` | 60/60 | **60/60** |
| ouverture de source | `sources/demonstration/` | 48/51 | **48/51** |
| mémoire de conversation | `sources/demonstration/` | le relevé de C51 | plans conformes |
| parcours de démonstration | `sources/demonstration/` | 48/48 | **144/144** |
| surface conversationnelle ×2 | par défaut | 40/40 | **40/40** et **40/40** |
| question de sens | `sources/demonstration/` | 36/36 | **36/36** |
| provenance du sens | le sien | 30/30 | **30/30** |
| fils de prédiction | `sources/metier/` | 8/8 | **8/8** |

**Deux de ces repères ont bougé depuis, et pas parce que le système a changé.**
C55 a inscrit dans les batteries des formulations réparées et jamais mesurées,
et durci quatre oracles de la surface. Les attendus d'une prochaine campagne
sont donc **37/38 méta + 6/6 témoins** pour la surface conversationnelle
(44 questions, `docs/surface-conversationnelle.md` §27.5) et **13/14** pour les
fils de prédiction (14 fils, `docs/parcours-de-l-agent.md`). Les deux manques
sont nommés et non réparés : `choix-entre-deux-sources` (§26) et
`i-inventaire-du-fil` (`docs/memoire-de-conversation.md` §3.4). Lire 40/40 ou
8/8 comme la cible ferait passer pour une régression un oracle devenu plus
strict.

`sources-nommees` porte la justification de n'avoir PAS touché à l'agent
système : « Qu'est-ce que t'appelles source vente, production, stock ? » y fait
3/3 et reçoit bien ses trois fiches. C'est exactement la question qu'un
départage côté message — celui qui aurait réparé `vel04-production-ventes` —
aurait cassée. Une question gagnée contre soixante tours mis en risque.

`memoire-de-conversation` montre `analyze · source=resultat_1` : le chaînage sur
un tableau du fil est intact, et le montage ciblé qu'il exige n'a pas été
élargi.
