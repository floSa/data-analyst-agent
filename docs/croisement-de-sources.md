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

---

# Les deux verrous, et ce qu'ils cachaient

Le relevé ci-dessus s'arrête sur deux obstacles nommés — l'agent système
intercepte, le planificateur n'empaquette pas toujours — et sur un arbitrage :
ne pas y toucher, parce que le départage naïf coûterait une campagne de
soixante tours. **L'arbitrage était juste sur ce qu'on savait alors. Ce qu'on
savait était incomplet**, et trois mesures l'ont montré.

## Verrou nº 1 — ce n'est pas le modèle qui intercepte

`run_systeme` seul, catalogue métier, trois tirages par message, moteur vLLM
`google/gemma-4-E4B-it-qat-w4a16-ct` sur `http://localhost:8100/v1`.

| famille | message | retenu par |
|---|---|---|
| fiches | « Qu'est-ce que t'appelles source vente, production, stock ? » | `chercher_une_source` — 3/3 |
| fiches | « Ça contient quoi, ventes et stocks ? » | `chercher_une_source` — 3/3 |
| fiches | « Entre ventes et production, qu'y a-t-il dans chacune ? » | `chercher_une_source` ×2 — 3/3 |
| fiches | « resume moi vite fait ventes, stocks, iris » | `chercher_une_source` ×3 — 3/3 |
| fiches | « dis-moi vite ce que je trouve dans production et dans ventes » | `sources_de_donnees` ×2 — 3/3 |
| fiches | « c'est quoi le contenu de production et de stocks ? » | `chercher_une_source` — 3/3 |
| fiches | « titanic et iris, c'est quoi au juste ? » | **le plancher** — 3/3 |
| fiches | « quelle est la différence entre iris et titanic ? » | **le plancher** — 3/3 |
| croisement | « compare la production et les ventes du VEL-04 » | **le plancher** — 3/3 |
| croisement | « compare les quantités de production et de ventes par produit » | **le plancher** — 3/3 |
| croisement | « pour le VEL-04, combien en stocks par rapport aux ventes ? » | **le plancher** — 3/3 |
| témoin | « ventes ou production ? » | rien — 0/3 |

Le plancher ne se déclenche qu'`outils_appeles` VIDE. Les trois questions de
croisement y passent donc **sans que le modèle ait appelé quoi que ce soit** :
il a jugé qu'elles ne portaient pas sur les sources, et un décompte de mots l'a
contredit. À l'inverse, **six des huit messages de fiches sont retenus par le
MODÈLE**, et « Qu'est-ce que t'appelles source vente, production, stock ? » —
la question dont C54 fait la raison de ne pas toucher à ce chemin — est de
ceux-là : elle **n'atteint jamais ce plancher**. La famille réellement en jeu
n'était pas soixante tours, c'était deux questions.

### Le départage ne se lit pas dans le message

« quelle est la différence entre iris et titanic ? » et « compare la production
et les ventes du VEL-04 » nomment chacune deux sources et disent chacune plus
que leurs noms. Elles ne se séparent ni par le décompte des mots, ni par le
vocabulaire du schéma — **aucune des deux ne nomme une table ou une colonne** —
ni par la comparaison, que les deux demandent.

Il se lit dans ce que la question **réclame**, et le composant dont c'est le
métier de le dire est le planificateur.

## Verrou nº 2 — un champ de plus, et son prix mesuré avant d'être payé

`Plan.sources` s'ajoute au contrat de sortie. C'est un **champ**, pas une
cinquième valeur de `Capability` : une valeur de plus élargit ce que le modèle a
le droit de choisir, et c'est cet élargissement-là que `orchestrator/plan.py`
documente comme payant.

Planificateur SEUL, catalogue métier, trois tirages, l'ancien contrat contre le
nouveau :

| message | ancien contrat | avec le champ |
|---|---|---|
| « Combien de commandes avons-nous reçues en 2025 ? » | query · `ventes` | query · `ventes` |
| « Quel chiffre d'affaires avons-nous réalisé en 2025 ? » | query · `ventes` | query · `ventes` |
| « Quel produit s'est le plus vendu en 2025 ? » | query · `ventes` | query · `ventes` |
| « Fais-moi un graphique du CA 2025 par canal de vente. » | analyze · `ventes` | analyze · `ventes` |
| « Quelle est la durée moyenne d'un arrêt machine ? » | query · `production` | query · `production` |
| « Combien de fleurs y a-t-il par espèce ? » | query · `iris` | query · `iris` |
| prédiction titanic, sept features | predict | predict |
| « ventes ou production ? » | `''` 2/3, `ventes, production` 1/3 | `''` **3/3** |

Aucune dégradation, et le témoin du choix devient même plus stable.

### L'union, et pas l'un OU l'autre

`_perimetre_croise` lit `plan.sources` **et** les noms empaquetés dans
`plan.source`. Chaque forme prise seule rend deux ou trois questions sur cinq ;
ensemble, cinq sur cinq. `produites-vs-vendues` le justifie à lui seul :
`source='production'` et `sources=['ventes']` — un nom dans chaque champ, aucun
des deux ne portant le périmètre.

## Ce que le champ n'a PAS fait, et pourquoi

**Premier relevé après le correctif : 9/42, contre 10/42 avant.** Le champ était
inerte : dans le graphe, le planificateur laissait `sources` vide, là où la sonde
qui avait chiffré son prix le voyait rempli 3 tirages sur 3.

Deux mesures justes, deux contrats différents. **La sonde avait défini sa classe
de sortie à la main, donc sans docstring.** Pydantic promeut silencieusement
`__doc__` en `description` du JSON Schema : la docstring de `Plan` — « Décision
de routage + paramètres extraits de la question. », écrite pour qui lit le code —
était lue par le modèle, et elle énonce le champ au **singulier**. Le modèle la
suivait contre la description du champ `sources`, qui dit l'inverse deux lignes
plus bas.

Six tirages par variante, deux contrats identiques au mot près sauf cette
phrase, sur « compare la production et les ventes du VEL-04 » :

| description du modèle | ce que rend le plan |
|---|---|
| la docstring de `Plan` | `source='production'`, `sources=[]` — **6/6** |
| (aucune) | `source=None`, `sources=['production','ventes']` — **6/6** |

Le **titre** du schéma, lui, ne change rien : `Plan` et `PlanB` se comportent
pareil à description égale, ce qui isole la cause sur la docstring seule.

**On retire plutôt qu'on réécrit.** Une troisième rédaction — « une source, ou un
périmètre qui en croise plusieurs » — a été mesurée : elle rend deux questions
sur trois et en perd une que le retrait garde. Réécrire, c'est chercher la phrase
qui plaît au modèle du jour ; retirer, c'est lui rendre le champ tel qu'il est
déclaré. La description du **champ** reste, et elle est mesurée comme payante :
sans elle, trois questions de croisement sur huit perdent leur périmètre.

C'est le seul endroit du dépôt où une documentation est explicitement coupée du
schéma, et `Plan.__get_pydantic_json_schema__` le dit.

## Le troisième obstacle, que les deux premiers cachaient

Le deuxième relevé rend **9/42** lui aussi — et pourtant quelque chose a changé
que le total ne montre pas : le **genre** de l'échec.

| | avant | après les deux verrous |
|---|---|---|
| « aucune donnée regardée » | 7 questions | 3 |
| « chiffre absent ou faux » | 3 questions | 8 |

Les questions **atteignent** désormais le croisement. C'est la **jointure** qui
échoue : « compare la production et les ventes du VEL-04 » rend « 27 626 unités
produites, 2 751 vendues » là où les oracles disent 727 et 125. Un produit
cartésien — qui ne lève aucune erreur et rend une phrase parfaitement lisible.

La cause est structurelle, et c'est la moitié de ce que `prefixer` avait réparé.
C54 a rendu au modèle les clés **internes** de chaque source. Restait la seule
qui compte dans un croisement : **celle qui relie les deux**. Aucun schéma ne la
porte — deux sources séparées ne déclarent pas de contrainte l'une vers l'autre
— et le dictionnaire qui l'écrit en prose (« le seul identifiant partagé est
`code_produit` ») ne suffit pas : C54 l'avait déjà constaté.

### Trois conditions, et les trois se lisent dans les données

`relier_les_sources` déclare une clé qui traverse le périmètre quand, et
seulement quand :

1. la colonne porte le **même nom** des deux côtés — candidate, rien de plus ;
2. elle est une **clé naturelle d'UN côté et d'un seul**, sans NULL ni doublon.
   Deux côtés uniques, ou aucun, et l'on ne sait pas qui référence qui : on se
   tait plutôt que de choisir ;
3. **toutes ses valeurs se retrouvent en face.** C'est ce qui sépare une clé
   d'une homonymie — `libelle` est unique dans `ventes_produits` et existe aussi
   dans `production_ateliers`, et « Assemblage final » n'est pas un produit.

Jamais à l'intérieur d'une source : ses clés sont déjà déclarées, et en inventer
là où le schéma s'est tu serait le contredire.

Sur le catalogue métier, le périmètre `ventes` + `production` gagne **exactement
une** clé — `production_ordres_fabrication.code_produit` vers
`ventes_produits(code_produit)` — et `quantite`, `libelle`, `code_of` n'en
gagnent aucune. À trois sources, `stocks_mouvements` et `stocks_inventaire`
pointent vers la même table, et `code_entrepot`, interne à `stocks`, reste muet.

# Le relevé, avec le banc étendu

Le banc passe de 9 à 14 questions, et le dénominateur de 27 à 42. Deux ajouts
comptent autant que le reste :

- **les deux phrases du pilote dans LEUR condition.** Elles étaient au banc, mais
  sur un fil VIERGE ; le pilote les a posées sur un fil lié à `ventes`.
  `ca-produit-vs-fabrique` est vert sur fil vierge dans le relevé de C54, et le
  pilote la voit répondre « la quantité fabriquée est de 8 unités » sur fil lié.
  Les deux relevés sont justes — ils ne parlent pas du même tour ;
- **trois formulations neuves**, écrites avant de savoir ce qu'elles rendent, dont
  un croisement par DIFFÉRENCE (« quels produits vendons-nous sans les fabriquer
  nous-mêmes ? »), que rien au banc ne préfigurait.

```
DAA_CATALOG_PATH=sources/metier/catalogue.yaml \
  uv run python scripts/mesure_croisement_de_sources.py --tirages 3
```

Moteur : vLLM, `google/gemma-4-E4B-it-qat-w4a16-ct`, `http://localhost:8100/v1`.
Catalogue : `sources/metier/catalogue.yaml`.

| question | fil | avant (`de660f8`) | après | ce qui décide |
|---|---|---|---|---|
| `produites-vs-vendues` | vierge | 0/3 | **0/3** | atteinte ; chiffres faux |
| `vend-plus-quon-produit` | vierge | 0/3 | **3/3** | **la phrase du pilote, réparée** |
| `ca-produit-vs-fabrique` | vierge | 1/3 | **0/3** | atteinte ; 461 absent |
| `vel04-production-ventes` | vierge | 0/3 | **0/3** | atteinte ; chiffres faux |
| `fabrique-vendu-stock` (3 sources) | vierge | 0/3 | **0/3** | atteinte ; piège des annulées |
| `produites-vs-vendues-fil-lie` | `ventes` | 0/3 | **0/3** | atteinte ; chiffres faux |
| `ca-produit-vs-fabrique-fil-lie` | `ventes` | 0/3 | **0/3** | atteinte ; 461 absent |
| `vend-plus-quon-produit-fil-lie` | `ventes` | 0/3 | **0/3** | atteinte ; 4 413 absent |
| `vel01-fabrique-vendu` (neuve) | vierge | 0/3 | **0/3** | le plan ne désigne pas |
| `vendus-sans-fabriquer` (neuve) | vierge | 0/3 | **0/3** | le plan ne désigne pas |
| `total-fabrique-vs-total-vendu` (neuve) | vierge | 0/3 | **0/3** | le plan ne désigne pas |
| **témoin** — une seule source | `ventes` | 3/3 | **3/3** | 1 496 743,00 €, `ventes` seule |
| **témoin** — question de sens | `ventes` | 3/3 | **3/3** | reste une question de sens |
| **témoin** — faire choisir | vierge | 3/3 | **3/3** | fait toujours choisir |

**10/42 → 12/42.** Le total dit moins que le déplacement qu'il cache :

| | avant | après |
|---|---|---|
| jamais atteinte (« aucune donnée regardée ») | 7 questions | **3** |
| atteinte, chiffre faux ou absent | 3 questions | **8** |
| conforme | 1 | **2** |

**Ce qui est réparé.** « est-ce qu'on vend plus que ce qu'on produit ? » — l'une
des deux phrases du relevé d'origine — passe de 0/3 à **3/3** : le croisement est
monté, la clé traversante déclarée, le filtre des annulées appliqué, et le
verdict est fondé sur 4 413 unités fabriquées.

**Ce qui ne l'est pas, et il faut le dire.** Huit questions atteignent désormais
le croisement et rendent un chiffre faux. Le verrou n'est plus le chemin, c'est
la **qualité de la requête** — et c'est un troisième obstacle, distinct des deux
qu'on a levés. Trois questions neuves n'atteignent toujours pas le croisement
parce que le planificateur ne désigne pas de périmètre sur ce tirage-là : le
champ le rend plus fréquent, pas certain.

**La capacité reste donc partielle, et le relevé le dit plutôt que de le taire.**

# Ce qui n'a pas bougé

Campagnes **séquentielles**, jamais de front — et le moteur d'inférence est
unique, ce qui est vérifié avant chaque lancement. Une précision qui a coûté un
relevé : `pgrep -f "scripts/mesure_"` **se compte lui-même**, le motif étant dans
sa propre ligne de commande, et rend « occupé » sur un moteur libre.

| campagne | catalogue | attendu | obtenu |
|---|---|---|---|
| questions métier | `sources/metier/` | 36/36 | **35/36** |
| sources nommées | `sources/metier/` | 60/60 | **60/60** |
| fils de prédiction | `sources/metier/` | 8/8 | **8/8** |
| ouverture de source | `sources/demonstration/` | 48/51 | **48/51** |
| mémoire de conversation | `sources/demonstration/` | le relevé de C51 | **`analyze · source=resultat_1` 3/3** |
| parcours de démonstration | `sources/demonstration/` | 144/144 | **144/144** |
| question de sens | `sources/demonstration/` | 36/36 | **36/36** |
| provenance du sens | le sien | 30/30 | **30/30** |
| surface conversationnelle | par défaut | 40/40 | **40/40** |
| surface conversationnelle (2e passe) | par défaut | 40/40 | **40/40** |

**`sources-nommees` est la campagne qui portait l'arbitrage de C54**, et c'est
elle qu'il fallait regarder : 60/60. Les deux seules questions qui passent par le
plancher — `difference-deux` et `deux-mots-brefs` — y restent à 3/3, servies par
la voie de réparation comme avant. Le plancher a bien consulté le plan, celui-ci
n'a désigné aucun périmètre, et les fiches sont parties.

**La surface conversationnelle est verte deux fois**, et c'est elle qui mesure le
prix d'un contrat de sortie élargi. Une cinquième valeur de `Capability` l'avait
cassée ; un champ facultatif ne lui coûte rien.

**L'écart de `questions-metier` est un tirage sur 36**, et il est hors du
périmètre touché : `ca-par-canal` est une analyse mono-source, dont le code n'a
pas abouti en 59 secondes. Elle ne passe ni par le plan multi-source, ni par le
croisement, ni par le plancher.

**Un mot sur la variance, parce qu'elle s'est vue deux fois.** Le relevé de C54
annonce `ca-produit-vs-fabrique` à 3/3 sur fil vierge ; le même socle, rejoué ici
avant tout changement, en rend 1/3. Un score de banc à trois tirages n'est pas
une constante, et une ligne qui bouge d'un tirage ne prouve rien à elle seule.
C'est pourquoi ce relevé dit le **genre** de l'échec, qui lui a bougé de sept
questions à trois.

## Le banc et le chemin normal ne posaient pas la même question

Le banc rend `vend-plus-quon-produit` à 3/3. Le pilote, sur le chemin qu'un
utilisateur emprunte — catalogue métier, fil déjà lié à `ventes`, conversation
neuve, deux tirages — obtient :

    system → plan → retrieval → synthesize
    plan : query sur `ventes`      retrieval : 0 requête(s), aucune n'a abouti
    « Le dictionnaire indique que la table `production` n'est pas disponible
      dans cette source… il est impossible de comparer. »

**Ce que le banc posait et que le chemin normal ne pose pas : rien. C'est
l'inverse.** Le chemin normal pose une chose de plus — `source_de_travail`. Le
3/3 cité est celui de la variante à fil **vierge** ; la variante à fil lié
existe au banc depuis C56 (`vend-plus-quon-produit-fil-lie`) et elle y était
**0/3**. Les deux mesures sont justes, elles portent sur deux conditions, et
**celle du pilote est celle que l'utilisateur rencontre** : dès qu'une source
est liée au fil, l'API la repasse à chaque tour. Le 12/42 se relit donc en
séparant les deux familles, et ce sont les lignes `-fil-lie` qui disent ce que
vaut la capacité en service.

### Le mécanisme : une phrase au singulier

Un fil lié fait ajouter au prompt du planificateur, par `_contexte_de_source` :

> CONTEXTE DE CONVERSATION : cette conversation travaille sur la source
> 'ventes'. Prends-la comme `source`, sauf si le message en désigne
> explicitement une autre.

Le modèle l'applique. Et `production` n'est **pas** désignée explicitement dans
« est-ce qu'on vend plus que ce qu'on produit ? » : elle l'est par un verbe. Le
plan ressort avec un seul nom, `plan.sources` reste vide, et `_perimetre_croise`
n'a rien à croiser — l'union des deux désignations est vide des deux côtés.

### Ce qui répare : on retire la phrase, une fois

`_relire_sans_la_source_du_fil` repose la même question sans le contexte de
source, et ne garde la seconde lecture **que** si elle désigne un périmètre.
Même discipline que `_relire_sans_la_clause_dabsence` : la première lecture
décide, la seconde ne peut qu'ajouter ce que la clause avait fait tomber. On ne
réécrit pas la phrase — on la retire pour une lecture, ce qui ne demande au
modèle aucune formulation nouvelle.

**Quatre conditions bornent le coût à un appel LLM** sur les seuls tours où le
plan ne fait qu'échoer ce qu'on vient de lui dire : un fil lié ; une capacité
qui interroge une source ; un plan qui désigne exactement la source du fil et
rien d'autre ; et une seconde lecture qui, elle, désigne un périmètre. Une
source **imposée** par l'appelant (`source=`) la ferme, comme elle ferme la
cession du plancher : quelqu'un a tranché, et élargir contre cette décision
serait la défaire en silence.

### Le relevé, après la seconde lecture

Mesuré le 2026-09-23, catalogue métier, trois tirages par question, moteur
`http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`).

**12/42 → 20/42.** Et le total dit moins que le déplacement qu'il cache : ce
sont les lignes `-fil-lie`, celles du chemin normal, qui bougent.

| question | fil | avant (C56) | après | ce qui décide |
|---|---|---|---|---|
| `vend-plus-quon-produit-fil-lie` | `ventes` | 0/3 | **3/3** | — |
| `ca-produit-vs-fabrique-fil-lie` | `ventes` | 0/3 | **3/3** | — |
| `produites-vs-vendues-fil-lie` | `ventes` | 0/3 | **0/3** | atteinte ; chiffres faux |
| `vend-plus-quon-produit` | vierge | 3/3 | **3/3** | — |
| `ca-produit-vs-fabrique` | vierge | — | **2/3** | « annulée » non dit une fois |

**Les deux phrases du pilote, dans SA condition, passent de 0/3 à 3/3.** C'est
exactement ce que la seconde lecture devait rendre, et rien d'autre : sur fil
vierge, où elle ne se déclenche pas, les scores sont inchangés.

**Les trois témoins restent verts 3/3** : « Quel chiffre d'affaires avons-nous
réalisé en 2025 ? » se joue sur `ventes` seule, « Que signifie le statut ANN ? »
reste une question de sens, et « ventes ou production ? » rend toujours la
question du choix sans rien croiser. La relecture ne leur a rien pris : aucune
n'a de périmètre à désigner, donc aucune ne garde la seconde lecture.

**Ce qui n'est pas réparé, et il faut le dire.** Quatre questions n'atteignent
toujours pas le croisement (`system → plan → synthesize`, aucune donnée
regardée), et `produites-vs-vendues` l'atteint avec des chiffres faux. La
capacité reste partielle ; ce commit répare l'écart entre le banc et le chemin
normal, pas la capacité entière.

### Les quatre campagnes dues, et ce qu'elles disent

| campagne | catalogue | repère | relevé du 2026-09-23 |
|---|---|---|---|
| surface conversationnelle (1ʳᵉ passe) | par défaut | 42/44 | **43/44** |
| surface conversationnelle (2ᵉ passe) | par défaut | 42/44 | **44/44** |
| fils de prédiction | `sources/metier/` | 13/14 | **13/14** |
| croisement de sources | `sources/metier/` | 12/42 | **20/42** |
| questions métier | `sources/metier/` | 36/36 | **35/36** |

**`fils-de-prediction` est au repère exact** : le seul fil qui ne tient pas est
`i-inventaire-du-fil`, 0/3, sur le même tour et la même cause qu'avant — le
tableau `resultat_1` du fil n'est pas nommé. Les treize autres tiennent 3/3.

**`questions-metier` rend 35/36, et l'écart est celui que C56 avait déjà
relevé** : `ca-par-canal`, au troisième tirage, une analyse mono-source dont le
code n'aboutit pas dans le délai (58 s). Elle ne passe ni par le plan
multi-source, ni par le croisement, ni par la seconde lecture — et les deux
autres tirages de la même question sont conformes. 210 appels LLM pour 36 tours.

**Aucune autre campagne n'est due** : rien de ce qui a bougé ne touche
l'ouverture de source, la mémoire de conversation, le parcours de démonstration
ni les questions de sens. La seule qui le deviendrait est celle du choix de
source, si l'on réparait le bord bistable en touchant `_proposer` — et c'est
justement pourquoi ce bord est laissé tel quel ici.

---

# Le croisement par l'analyse : le calcul était juste, et il restait dedans

Le relevé du pilote, le 2026-09-23 sur `201f035`, catalogue métier, fil lié à
`ventes` : « compare le chiffre d'affaires par produit avec les quantités
fabriquées » rend « L'analyse n'a pas abouti : le code produit n'a pas pu
s'exécuter après 3 tentative(s). » Trois lectures s'offraient, et elles ne se
réparent pas au même endroit : les deux sources ne sont pas montées ensemble
pour l'analyse ; elles le sont mais le code ne sait pas quel fichier porte
quoi ; elles le sont, il le sait, et la jointure manque de la clé que C56
déclare pour le chemin SQL.

**Aucune des trois.** Le relevé a été refait sur ce dépôt, en espionnant le bac
à sable pour voir CE QUE LE CODE TENTE et ce qu'il rend.

## Ce que le code généré tente, et ce qu'il en advient

Quatre tirages de la phrase du pilote, dans SA condition — fil lié à `ventes`,
catalogue métier, moteur `google/gemma-4-E4B-it-qat-w4a16-ct` sur
`http://localhost:8100/v1` :

```
système → plan → analysis → synthesize
analysis : 1 essai(s), 1 figure(s), statut ok
```

**4 tirages sur 4, le code s'exécute au PREMIER essai.** Il n'y a pas de
deuxième tentative, donc pas de troisième, donc pas d'échec de la boucle de
correction. Et le code qu'il écrit lit les bons fichiers, du premier coup :

```python
df_ventes_lignes_commande = pd.read_csv("/data/ventes_lignes_commande.csv")
df_ventes_commandes = pd.read_csv("/data/ventes_commandes.csv")
df_production_ordres_fabrication = pd.read_csv("/data/production_ordres_fabrication.csv")
...
df_ca_produit_facture = df_ca_produit[df_ca_produit["statut"] != "ANN"]
...
df_comparison = pd.merge(ca_par_produit, quantite_fabrique_par_produit, on="code_produit")
```

Les deux sources sont montées ensemble, préfixées ; le code sait quel fichier
porte quoi ; il filtre les annulées ; il joint sur `code_produit`. Les trois
hypothèses tombent une par une, et la troisième tombe deux fois — la clé du
croisement est bien DÉCLARÉE au chemin SQL, vérifiée hors moteur :

```
production_ordres_fabrication
    FK code_produit -> ventes_produits.code_produit
```

## Le défaut est en aval : ce chemin n'a pas de tableau

Le calcul aboutit. Ce que l'utilisateur reçoit, lui, est ceci :

> La comparaison révèle que 4 produits vendus n'ont pas été enregistrés comme
> produits, notamment les accessoires (ACC-01 à ACC-04). Pour les produits
> listés, les ventes dépassent largement la production pour les vélos.

Une phrase juste, et **pas un seul des chiffres calculés**. La cause tient en
une dissymétrie entre les deux chemins :

| | chemin SQL (`query`) | chemin d'analyse (`analyze`) |
|---|---|---|
| ce qui est calculé | des lignes | un `DataFrame`, imprimé |
| ce qui est SERVI | l'artefact `application/json`, rendu en tableau | une figure PNG |
| ce que dit la phrase | elle commente le tableau | elle résume, **seule** |

Le chemin SQL sert ses lignes, et sa phrase n'a qu'à les commenter. Le chemin
d'analyse rend une image — qui ne se lit pas au chiffre près — et une synthèse
de 1 à 4 phrases, à qui on demande de résumer et qui résume. Le `stdout` du
conteneur, où le tableau est imprimé en toutes lettres, n'allait qu'au modèle de
synthèse. Il n'est jamais ressorti.

**Ce n'est donc pas le croisement qui échoue, c'est la restitution de
l'analyse** — et le défaut ne tient pas à ce qu'il y ait deux sources. Une
analyse mono-source perd ses chiffres exactement pareil.

## Ce qui répare : on sert ce que le code a imprimé

`_avec_ce_que_le_code_a_imprime` ajoute le `stdout` de l'exécution sous la
phrase de synthèse, tel quel, dans un bloc.

**Servi, et non redemandé au modèle.** Une consigne de plus dans le prompt de
synthèse — « cite tous les chiffres » — aurait dépendu d'un modèle qui obéit, et
un chiffre resservi par un modèle est un chiffre qu'il peut abîmer. Le `stdout`
est ce que le code a produit : il est vrai sans qu'on lui fasse confiance. C'est
le choix déjà fait pour le pied du dictionnaire, et pour le résumé déterministe
multi-lignes.

Trois cas où l'on ne fait rien, ou le moins possible : une sortie vide (une
analyse qui ne trace qu'une figure garde sa réponse au caractère près), une
sortie que la phrase contient déjà (on ne sert pas deux fois le même
paragraphe), et une sortie trop longue (coupée à 3 000 caractères, et la coupe
se dit — un extrait servi comme un tout est le défaut qu'on ferme partout
ailleurs).

## Une phrase parasite, et pourquoi on ne la corrige pas dans un prompt

Le même relevé porte un second défaut, sur l'autre chemin. La réponse qui
MARCHE — « est-ce qu'on vend plus que ce qu'on produit ? », 1 828 unités
vendues, annulées exclues — s'ouvrait par :

> Je m'excuse pour la confusion. J'ai déjà exécuté les deux requêtes
> nécessaires dans mes étapes précédentes.

Les chiffres qui suivent sont justes. C'est la forme qui fuit : le modèle
raconte sa boucle interne à quelqu'un qui n'a demandé aucune requête, n'en a vu
aucune, et n'a rien à excuser.

**D'où elle sort.** `RetrievalResult.summary` est le DERNIER message d'un agent
à outils, écrit après une boucle d'appels ; `_synthesize_query` le sert tel quel
quand le résultat tient en une ligne — c'est-à-dire exactement le cas d'un
agrégat comme celui-ci. Les autres cas sont déterministes et ne peuvent pas
fuir.

**Pourquoi pas une consigne de plus.** Une phrase ajoutée au prompt de l'agent
SQL ou à une fiche d'outil aurait tenu sur la tournure qu'on lui aurait
montrée — ce dépôt a déjà payé ce pari deux fois — et l'empreinte SHA-256 qui
couvre les sept prompts et les huit fiches n'aurait plus attesté d'un socle
stable. On coupe donc **ce qui est servi**, et non ce qui est demandé :
`orchestrator/recit.py`.

**Deux garde-fous, parce que le remède serait sinon pire que le mal.** Une
phrase qui porte un CHIFFRE n'est jamais coupée — « j'ai exécuté une requête qui
rend 1 828 unités » porte 1 828, et 1 828 est la réponse. Et l'on ne coupe qu'en
TÊTE, en s'arrêtant à la première phrase qui n'est pas du récit ; si tout le
texte en est, on le rend intact, parce qu'une réponse vide serait une régression
et non une correction.

Le marqueur est double, et il faut les deux : une première personne (`je`,
`j'`, `mes`…) ET un mot de la mécanique du tour (`étape`, `requête`, `outil`,
`exécuter`, `excuser`, `précédent`…). « Les deux requêtes nécessaires ont été
exécutées » décrit le TRAVAIL et non le narrateur : sans première personne, on
ne coupe pas.

## Le relevé : le score ne bouge pas, et ce qu'il cache bouge beaucoup

```
DAA_CATALOG_PATH=sources/metier/catalogue.yaml \
  uv run python scripts/mesure_croisement_de_sources.py --tirages 1
```

Moteur : `http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`).
Catalogue : `sources/metier/catalogue.yaml`. Quatorze questions, un tirage.

**5/14 → 5/14.** Le total est le même, et il faut le dire ainsi : **ce commit ne
répare pas la campagne.** Ce qu'il répare est en dessous.

| question | fil | avant | après | ce qui décide, après |
|---|---|---|---|---|
| `produites-vs-vendues` | vierge | 0/1 | **0/1** | 689, 727 rendus ; 123, 125 absents |
| `ca-produit-vs-fabrique` | vierge | 0/1 | **0/1** | 461 absent |
| `vel04-production-ventes` | vierge | 0/1 | **0/1** | 727, 125 absents (chemin SQL) |
| `produites-vs-vendues-fil-lie` | `ventes` | 0/1 | **0/1** | 689, 727, 123, 125 absents |
| `ca-produit-vs-fabrique-fil-lie` | `ventes` | 0/1 | **0/1** | 461 absent |
| quatre questions sur fil vierge | vierge | 0/1 | **0/1** | aucune donnée regardée |
| `vend-plus-quon-produit` (×2 fils) | les deux | 1/1 | **1/1** | — |
| **les trois témoins** | | 1/1 | **1/1** | — |

**Les trois témoins restent verts**, et c'est la moitié du relevé : la réponse
s'allonge sur le chemin d'analyse, et rien n'a bougé sur les chemins qui ne
passent pas par lui. `vend-plus-quon-produit` — le chemin SQL, 1 828 unités
vendues, annulées exclues — est conforme sur les deux fils, avant comme après.

### Ce que le score cache

Voici `produites-vs-vendues`, APRÈS, telle que l'utilisateur la reçoit :

```
--- Comparaison des Quantités Produites vs Vendues par Produit ---
Code Produit    |      Vendu |    Produit |      Écart
ACC-03          |        298 |          0 |        298
VEL-08          |        175 |        556 |       -381
VEL-01          |        141 |        689 |       -548
VEL-07          |        137 |        461 |       -324
VEL-04          |        131 |        727 |       -596
```

La table entière, les douze produits, 689 et 727 et 461 compris. Et **141 et
131 pour VEL-01 et VEL-04** : ce sont exactement les deux chiffres du piège du
dictionnaire — les quantités vendues SANS le filtre des annulées, là où les
oracles attendent 123 et 125.

Le tour d'avant rendait, sur le même défaut : « les ventes dépassent largement
la production pour les vélos ». Juste, invisible, invérifiable. **Le même
chiffre faux, le même tour, et il se voit maintenant.** C'est la propriété que
ce dépôt poursuit depuis C51 — « un chiffre faux et plausible, donc
invisible » — appliquée au chemin qui ne l'avait pas.

### Ce qui reste, et où ça se répare

Deux causes distinctes, toutes deux dans le code que le modèle ÉCRIT, et toutes
deux désormais lisibles dans la réponse :

1. **Le filtre des annulées est oublié sur les quantités.** Le dictionnaire de
   `ventes` est bien injecté — le même tour l'applique correctement au chiffre
   d'affaires, et rate la quantité. `produites-vs-vendues`.
2. **Le code imprime un EXTRAIT là où la question demande chaque produit.**
   `ca-produit-vs-fabrique` rend un « Top 5 par quantité fabriquée » où VEL-07
   et ses 461 unités arrivent septièmes : le chiffre n'est pas perdu en route,
   il n'est jamais calculé pour l'affichage.

La seconde se réparerait dans la règle 2 de `prompts/analysis.txt` (« Termine
par des print(...) explicites des valeurs demandées ») — ce qui demande de
mettre à jour l'empreinte SHA-256 du prompt dans le même commit, et rend dues
les cinq campagnes qui passent par l'analyse. Ce n'est pas fait ici.

**Et quatre questions sur fil vierge n'atteignent toujours pas le croisement**
(`system → plan → synthesize`, l'inventaire servi) : c'est le bord que C57 a
laissé, le planificateur qui rend `source=''`. Inchangé, et inchangé
volontairement.

### Les campagnes dues

Séquentielles, jamais de front. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue imprimé en tête de chaque
relevé.

| campagne | catalogue | repère (C57) | relevé du 2026-09-23 |
|---|---|---|---|
| croisement de sources (1 tirage) | `sources/metier/` | 5/14 avant ce commit | **5/14** |
| surface conversationnelle (1ʳᵉ passe) | par défaut | 43/44 | **43/44** |
| surface conversationnelle (2ᵉ passe) | par défaut | 44/44 | **43/44** |
| questions métier (3 tirages) | `sources/metier/` | 35/36 | **33/36** |

**Les deux passes de la surface conversationnelle ont le même et unique écart :
`choix-entre-deux-sources` (« titanic ou iris ? »).** C'est le bord bistable que
C57 a laissé en l'état, tombé sur sa face rouge aux deux passes. Les 43 autres
questions sont conformes des deux côtés, et les six témoins sur les DONNÉES sont
6/6 aux deux passes — dont « quand je te donne un âge, tu prédis quoi ? », qui
repart bien en prédiction.

**`questions-metier` rend 33/36, et l'écart entier est `ca-par-canal`**, qui
passe de 2/3 à 0/3. C'est la question que C57 signalait déjà comme
instable — « une analyse mono-source dont le code n'aboutit pas dans le
délai ». Les onze autres questions sont 3/3.

Elle a été sondée, essai par essai, et la cause n'est ni les données ni le
délai :

```
analysis : 3 essai(s), 0 figure(s), statut error
essai 1  ImportError  from matplotlib.ticker import Func
essai 2  ImportError  from matplotlib.ticker import Func as MatplotlibFunc
essai 3  ImportError  Import tabulate failed   (DataFrame.to_markdown)
```

Deux essais sur trois répètent le même symbole inventé — `matplotlib.ticker`
n'expose pas de `Func` — et le troisième bute sur `tabulate`, qui n'est pas dans
l'image du bac à sable alors que `to_markdown()` est une tournure naturelle. Le
correctif de ce commit ne peut rien pour ces tours : il sert ce que le code a
imprimé, et ce code-là n'a rien imprimé. Ce qui les réparerait est ailleurs —
la liste des bibliothèques de `prompts/analysis.txt`, ou l'image du bac à sable.

## Le filtre des annulées oublié sur les quantités (C60)

Le défaut que C58 a rendu visible : « compare les quantités produites et les
quantités vendues par produit » rendait **141 et 131** vendus pour VEL-01 et
VEL-04. 689 et 727 étaient justes ; 141 et 131 sont les quantités vendues SANS
le filtre des commandes annulées. Les oracles, recalculés dans la base :

| produit | vendu, `statut <> 'ANN'` | vendu, brut |
|---|---|---|
| VEL-01 | **123** | 141 |
| VEL-04 | **125** | 131 |

Un chiffre faux et plausible. On ne savait pas pourquoi, et on ne l'a pas
supposé : la cause a été mesurée avant d'écrire une ligne.

### Le relevé, essai par essai

Quatre questions, trois tirages chacune, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue `sources/metier/catalogue.yaml`.
Pour chaque essai de l'analyse : le prompt système et le message EXACTS que
l'agent reçoit, le code écrit, ce que le bac à sable rend.

**`produites-vs-vendues` (fil vierge, `analyze sur ventes, production`)** — 0/3,
les trois tirages identiques :

- le dictionnaire de `ventes` est dans le prompt système, **entier** : 15 866
  caractères pour les deux dictionnaires, sous le budget doublé, aucune section
  écartée ; le piège nº 1 y est, et la phrase « toute somme d'**unités vendues
  ou expédiées** » aussi ;
- le code lit `ventes_lignes_commande.csv` et `ventes_produits.csv`, **jamais
  `ventes_commandes.csv`** — la table où vit `statut` ;
- `statut` et `'ANN'` n'apparaissent **nulle part** dans le code ;
- il écrit lui-même, en commentaire : « Aucune règle spécifique mentionnée pour
  la vente de produits » ;
- essai 1 : `KeyError` ou `to_markdown` sans `tabulate` ; essai 2 : il réussit,
  et imprime 141 et 131.

**`produites-vs-vendues-fil-lie` (fil `ventes`, `analyze sur ventes`)** — 0/3,
mais pour une autre raison : le code **filtre** les annulées trois fois sur
trois (`df[df['statut'] != 'ANN']`, commentaire « Piège 1 ») ; quand il imprime
VEL-01 et VEL-04, c'est 123 et 125. Ce qui manque, c'est `production` : le plan n'attache que `ventes`, et 689
et 727 ne peuvent pas être calculés. Défaut de périmètre, pas de filtre.

**`vel01-fabrique-vendu` et `vel04-production-ventes`** — 0/3 chacune, et elles
ne passent **pas par l'analyse** : `query sur ventes, production`, chemin SQL.
Le SQL joint `production_ordres_fabrication` à `ventes_lignes_commande` par le
produit et **démultiplie les deux côtés** (27 626 « fabriqués » pour VEL-04,
26 871 pour VEL-01) ; le filtre manque aussi, mais le produit cartésien écrase
tout. Le dictionnaire de `ventes` est dans le prompt SQL, entier. Défaut
distinct, hors de cette tâche.

### La cause, telle que mesurée

**Le dictionnaire arrive ; le code l'ignore.** Même source, même règle, même
agent : seule, `ventes` fait filtrer le code trois fois sur trois ; croisée
avec `production`, le code ne joint plus `commandes` et ne filtre plus. Ce
n'est pas un défaut d'acheminement — il n'y avait rien à faire arriver —, et
ce n'est pas une phrase de plus qui l'aurait réparé : la phrase était lue.

Et l'exécution **réussit** : la boucle de correction ne se déclenche que sur
une erreur. Un chiffre faux qui ne lève rien la traversait sans être vu.

### La réparation

`ventes` déclare au catalogue le filtre qu'elle impose à ses sommes
(`filtre_des_sommes` : `commandes.statut`, `exclure: ANN`, et les trois colonnes
dont la somme l'exige). `agents/analysis/consigne.py` vérifie le code produit :
s'il lit une table déclarée, nomme une de ces colonnes, somme, et ne porte
nulle part `'ANN'`, la boucle le renvoie au modèle avec ce fait — même quand il
a réussi.

**Par une propriété du code, comme `agents/retrieval/classement.py` pour le
SQL.** Rien n'est lu dans les tournures de la question, et rien dans le Markdown
du dictionnaire : un dictionnaire de démonstration pose un filtre pour COMPTER
et le refuse pour SOMMER, et un analyseur de texte aurait appris la règle à
l'envers sur l'un des deux. Les prompts et les fiches d'outils ne bougent pas ;
l'empreinte SHA-256 non plus.

**Le comptage n'est jamais touché.** « combien de commandes » → 180, pas 164 :
un comptage ne nomme aucune colonne de mesure (`len`, `size`, `count` sur un
identifiant), donc la propriété ne le voit pas, même quand il additionne ses
propres effectifs. Quatre cas de test le fixent, dont les deux comptages du
dictionnaire.

**Un garde-fou ne jette pas une réponse.** Le dernier calcul réussi est gardé :
si les essais s'épuisent — ou si l'essai de correction plante — c'est lui qui
est servi, avec un avertissement sous la réponse, et non « l'analyse n'a pas
abouti ». Un commentaire qui cite la règle ne compte pas comme filtre : seules
les chaînes du code sont lues.

Ce qu'elle ne sait pas faire : un filtre posé sans écrire la valeur
(`isin(['LIV', 'EXP'])`) est pris pour un oubli. Le coût est un essai de plus,
et au pire un avertissement de trop sous un chiffre juste.

### L'avant/après, les quatre questions

Mêmes conditions, trois tirages, séquentiels.

| question | chemin | avant | après | ce qui est rendu, après |
|---|---|---|---|---|
| `produites-vs-vendues` | analyse, deux sources | **0/3** — VEL-01 141, VEL-04 131 | **3/3** | VEL-01 **123** / 689, VEL-04 **125** / 727, total vendu 1 828 |
| `produites-vs-vendues-fil-lie` | analyse, `ventes` seule | 0/3 — filtre posé, `production` absente | 0/3 | inchangé, même cause |
| `vel01-fabrique-vendu` | SQL | 0/3 — 141, puis 3 384 / 26 871 | 0/3 | 3 384 / 26 871, inchangé |
| `vel04-production-ventes` | SQL | 0/3 — 2 751 / 27 626 | 0/3 | 2 751 / 27 626, inchangé |

Sur les trois tirages après, le déroulé est le même :

```
essai 1  ImportError  `Import tabulate` failed   (to_markdown)
essai 2  ok           somme `quantite` sans 'ANN' → constat renvoyé au modèle
essai 3  ok           joint ventes_commandes.csv, statut != 'ANN' → 123, 125
```

**La marge est nulle** : la correction arrive au troisième et dernier essai,
parce que le premier est perdu sur `tabulate` — l'absence que C59 a laissée à
la décision du propriétaire. Un essai de moins, et c'est l'avertissement qui
serait servi à la place du chiffre juste.

### La sonde du comptage, par le chemin d'analyse

Fil `ventes`, deux tirages. Oracle calculé dans la base : 100 / 44 / 36
commandes (magasin / en ligne / grossiste) sans filtre ; 91 / 38 / 35 si l'on
écartait à tort les annulées.

| question | chemin | rendu | constat envoyé |
|---|---|---|---|
| « Fais-moi un graphique du nombre de commandes par canal » | analyse | **100 / 44 / 36**, 2/2 | aucun |
| « trace le nombre de lignes de commande par produit » | SQL | 39 VEL-01, 38 VEL-04… (463 lignes) | sans objet |

La seconde est partie au SQL : elle ne sonde pas la vérification, elle est
notée pour ce qu'elle est.

### Les campagnes

Séquentielles, jamais de front. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue lu en tête de chaque relevé :
`sources/metier/catalogue.yaml`.

| campagne | repère | après ce commit |
|---|---|---|
| croisement de sources (1 tirage) | 7/14 (C59) | **8/14** |
| questions métier (1 tirage) | 36/36 à 3 tirages | **12/12** |

**Croisement.** `produites-vs-vendues` passe (123 et 125 servis) ; les trois
témoins restent verts. Les six écarts ne tiennent pas au filtre : trois
questions sur fil vierge où le plan ne regarde aucune donnée (`source=''`, le
bord de C57) ; `vel04-production-ventes` et `total-fabrique-vs-total-vendu` sur
le chemin SQL ; `produites-vs-vendues-fil-lie`, où `production` n'est pas
attachée. Un tirage ne tranche rien : le point gagné est celui que les trois
tirages de l'avant/après ont établi, pas un de plus.

**Métier.** Les douze questions passent, dont « combien de commandes » (180,
aucun filtre) et le graphique du CA par canal. La vérification n'y retire aucun
chiffre juste.

### Ce qui reste

- **Le chemin SQL du croisement resserré** (`vel01-…`, `vel04-…`) : un produit
  cartésien entre ordres de fabrication et lignes de commande, puis le filtre
  manquant. La même propriété pourrait se vérifier sur le SQL ; ce n'est pas
  fait ici.
- **Le fil lié à `ventes`** n'attache pas `production` pour une question qui la
  nomme.
- **`tabulate`** coûte un essai à chaque tour de cette question, et c'est lui qui
  ramène la marge à zéro.

## La jointure qui multiplie, et le filtre oublié sur le SQL (C61)

Ce que C60 a laissé ouvert, et nommé comme tel : le chemin SQL du croisement
resserré. « pour le VEL-01, combien on en a fabriqué et combien on en a
vendu ? » rendait **26 871 fabriqués et 3 384 vendus** ; les oracles disent 689
et 123. « compare la production et les ventes du VEL-04 » rendait **27 626 et
2 751** pour 727 et 125. Trois tirages sur trois, la même requête au caractère
près, aucune erreur levée, et une phrase de réponse parfaitement lisible.

### Le relevé, essai par essai

Deux questions, trois tirages chacune, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue `sources/metier/catalogue.yaml`.
Pour chaque essai : le SQL envoyé à `run_sql`, ce que la base a rendu, et le
prompt exact reçu par l'agent.

**Une seule requête par tour**, et c'est elle qui sert les chiffres. Sur les six
tirages, cinq passent par le chemin SQL (`query sur ventes, production`) ; un —
`vel01` au premier tirage — n'a regardé aucune donnée et a rendu l'inventaire
(`system → plan → synthesize`), qui est le bord de C57 et non ce défaut-ci.

**`vel01-fabrique-vendu`**, identique aux deux tirages qui interrogent :

```sql
SELECT SUM(T1.quantite_produite) AS total_fabrique,
       SUM(T3.quantite)          AS total_vendu
FROM production_ordres_fabrication AS T1
INNER JOIN ventes_produits        AS T2 ON T1.code_produit = T2.code_produit
INNER JOIN ventes_lignes_commande AS T3 ON T2.produit_id  = T3.produit_id
WHERE T2.code_produit = 'VEL-01'        -- rend 26 871 et 3 384
```

**`vel04-production-ventes`**, identique aux trois tirages :

```sql
SELECT SUM(T1.quantite_produite) AS production_vel04,
       SUM(T3.quantite)          AS ventes_vel04
FROM production_ordres_fabrication AS T1
INNER JOIN ventes_produits        AS T2 ON T1.code_produit = T2.code_produit
INNER JOIN ventes_lignes_commande AS T3
  ON T2.code_produit = (SELECT code_produit FROM ventes_produits
                        WHERE produit_id = T3.produit_id)
WHERE T2.code_produit = 'VEL-04'        -- rend 27 626 et 2 751
```

Ce que le relevé donne, et qu'on n'a pas supposé :

- **les jointures et leurs clés.** `vel01` relie les ordres de fabrication aux
  produits par `code_produit`, puis les produits aux lignes de commande par
  `produit_id`. Les deux clés sont UNIQUES du côté des produits : chaque
  jointure, prise seule, est irréprochable. C'est leur mise bout à bout qui
  multiplie — deux tables de faits accrochées à la même dimension, et chaque
  ordre apparié à chacune des 39 lignes de commande du produit
  (689 fois 39 = 26 871 ; 141 fois 24 = 3 384). `vel04` fait pire : la
  troisième table n'est reliée que par une sous-requête corrélée, donc par
  aucune égalité de colonnes lisible — un produit cartésien ;
- **`statut` et `'ANN'` n'apparaissent dans aucune des six requêtes.** Le
  filtre des annulées manque partout ; le produit cartésien l'écrase, mais il
  est bien là — 141 et non 123 sous la multiplication ;
- **les deux dictionnaires arrivent entiers.** Le prompt système pèse 18 442
  caractères, il porte sept fois `ANN` et neuf fois `statut`, et il est
  identique aux six tirages. Ce n'est donc pas un défaut d'acheminement : le
  texte est lu, et il n'est pas appliqué.

### La cause, telle que mesurée

**Deux fautes empilées dans la même requête, et aucune ne lève.** L'exécution
réussit ; la boucle de correction ne se déclenche que sur une erreur SQL. Un
chiffre faux d'un facteur quarante la traversait sans être vu — exactement ce
que C60 avait relevé pour le chemin d'analyse, sur l'autre chemin.

Et une phrase de prompt n'y aurait rien fait, pour la même raison qu'en C60 :
le dictionnaire était là, entier, et ce qui manquait n'était pas le texte.

### La réparation

Deux propriétés du SQL produit, vérifiées après chaque requête réussie
(`agents/retrieval/verification.py`). Ni l'une ni l'autre ne regarde la
question : elles sont vraies ou fausses quelle que soit la tournure, comme
`classement` pour le palmarès et `agents/analysis/consigne` pour le code.

**① Une somme lue dans une table ne doit pas être multipliée par une jointure.**
Ça se MESURE : on part de la table dont une colonne est sommée, et l'on n'avance
dans la requête que par une clé qui ne se répète pas du côté où l'on arrive —
trois agrégats demandés à la base, `count(*)`, `count(colonne)`,
`count(DISTINCT colonne)`. Une table atteinte ainsi est une table de DIMENSION :
elle décore la ligne sans la dupliquer. Une table qu'on n'atteint jamais —
parce que sa clé se répète, ou parce qu'aucune égalité de colonnes ne la relie —
apparie plusieurs de ses lignes à chaque ligne sommée, et la somme est
multipliée d'autant. C'est la même lecture des données que
`relier_les_sources`, à une différence près : là-bas une clé doit être sans
NULL, ici seulement sans DOUBLON — un NULL ne multiplie rien.

**② La règle `filtre_des_sommes` vaut aussi pour le SQL.** La déclaration est
celle de C60, au catalogue, inchangée ; `FiltreMonte` dit sous quel nom ses
tables sont montées — `ventes_lignes_commande.csv` pour le bac à sable,
`ventes_lignes_commande` pour une connexion. Une seule déclaration, deux
lectures.

Le fait repart au modèle **par le canal du classement** : appendu au tableau,
jamais à sa place, et borné à une relance par propriété et par récupération —
deux allers-retours au pire, sous `retrieval_request_limit`. Si la relance ne
corrige pas, la réponse est **servie avec l'avertissement dans le texte**
(`_avec_l_avertissement`), jamais en silence et jamais jetée : c'est la règle de
`consigne_notice` pour l'autre chemin.

**Aucun prompt ni aucune fiche d'outil n'a bougé** ; l'empreinte SHA-256 des
sept prompts non plus.

Ce que ça ne sait pas faire, et qui est assumé : partout où la lecture doute —
une table absente du schéma, une sous-requête en guise de table, deux `FROM` de
niveau zéro, une somme d'expression (`SUM(a * b)`) — le module se tait. Un
doute coûte au pire le chiffre d'avant ; un faux positif coûterait un
aller-retour et pourrait pousser à corriger une requête juste.

### L'avant/après, les deux questions

Mêmes conditions, trois tirages, séquentiels, catalogue
`sources/metier/catalogue.yaml`.

| question | avant | après | ce qui est rendu, après |
|---|---|---|---|
| `vel01-fabrique-vendu` | **0/3** — 26 871 / 3 384 (2 tirages), inventaire (1) | **3/3** | **689** fabriqués, **123** vendus |
| `vel04-production-ventes` | **0/3** — 27 626 / 2 751 | **3/3** | **727** fabriqués, **125** vendus |

Le déroulé est le même aux six tirages d'après, et il tient en trois lignes :

```
requête 1  la même qu'avant           26 871 / 3 384  → les DEUX remarques
sonde      count / count / distinct   ventes_lignes_commande.produit_id : 463 pour 12
requête 2  une somme par sous-requête, statut <> 'ANN'   → 689 / 123
```

Le modèle corrige les deux fautes d'un coup, dans la requête suivante : il
agrège les ventes dans une sous-requête corrélée au produit, y joint
`ventes_commandes` et y pose `statut <> 'ANN'`. Sur `vel04`, une seule sonde
suffit — la table n'étant reliée par aucune égalité, il n'y a pas de seconde
cardinalité à mesurer.

### Les trois témoins

Un garde-fou peut détruire une bonne réponse. Chacun a un test unitaire —
`test_temoin_une_jointure_de_dimension_ne_declenche_rien`,
`test_temoin_un_comptage_ne_recoit_ni_l_un_ni_l_autre`,
`test_temoin_une_somme_en_euros_garde_son_filtre`, tous trois mesurés sur une
base DuckDB réelle plutôt que sur une doublure de sonde
(`tests/unit/retrieval/test_verification.py`) — et une sonde sur le produit.

Les trois sondes sont posées sur le chemin SQL réel, fil lié à `ventes`,
catalogue `sources/metier/catalogue.yaml`. Ce qui est relevé : le SQL du modèle,
les cardinalités que la vérification a demandées à la base, et si un
avertissement a été servi.

**① Une jointure juste, par une table de dimension** — « Quel est notre meilleur
client en chiffre d'affaires en 2025 ? »

```sql
SELECT T1.raison_sociale, SUM(T2.montant_total_eur) AS chiffre_affaires
FROM clients AS T1 INNER JOIN commandes AS T2 ON T1.client_id = T2.client_id
WHERE … AND T2.statut <> 'ANN' GROUP BY T1.raison_sociale
ORDER BY chiffre_affaires DESC LIMIT 1
```

Une sonde, une seule : `clients.client_id`, 18 lignes pour 18 valeurs
distinctes — une clé unique, donc une dimension. **Rien n'est déclenché**, et la
réponse est Vélocité Bordeaux, 170 149,00 €. C'est la forme normale d'un
croisement juste, et la signaler aurait coûté un aller-retour sur la moitié des
requêtes du produit. (Le CA par canal — 862 229 / 331 499 / 303 015 — passe,
lui, par le chemin d'ANALYSE : c'est un graphique, et il reste 1/1 aux questions
métier.)

**② Un COMPTAGE ne reçoit jamais le filtre** — « Combien de commandes
avons-nous reçues en 2025 ? » rend **180**, pas 164. Aucune sonde n'est même
posée : les deux propriétés exigent un `SUM(` au niveau zéro, et un comptage
n'en porte aucun. La réponse le dit d'elle-même : « le comptage des commandes ne
fait aucune distinction de statut ».

**③ Une somme en euros sur `ventes` seule garde son filtre** — « Quel chiffre
d'affaires avons-nous réalisé en 2025 ? » rend **1 496 743,00 €**, avec
`statut <> 'ANN'` écrit dans le SQL. Le préfixe est vide hors croisement : la
règle est cherchée sur `commandes`, et non sur `ventes_commandes`.

Sur les quatre campagnes et les trois sondes, **aucun avertissement n'a été
servi à l'utilisateur** : partout où une remarque est partie, la relance a
corrigé.

### Les campagnes

Séquentielles, jamais de front. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue lu en tête de chaque relevé.

| campagne | catalogue | repère | après ce commit |
|---|---|---|---|
| croisement de sources (1 tirage) | `sources/metier/catalogue.yaml` | 8/14 (C60) | **8/14** |
| questions métier (1 tirage) | `sources/metier/catalogue.yaml` | 12/12 (C60) | **12/12** |
| classement sans lexique (1 tirage) | `sources/demonstration/catalogue.yaml` | 10/10 | **10/10** |
| parcours de démonstration (1 tirage) | `sources/demonstration/catalogue.yaml` | 144/144 à 3 tirages | **48/48** (1 tirage) |

**Croisement : le total ne bouge pas, sa composition oui.**
`vel04-production-ventes` et `total-fabrique-vs-total-vendu` passent, tous deux
sur le chemin SQL et tous deux en écart chez C60. Trois questions les
remplacent en écart, et aucune ne tient à ce correctif : `vel01-fabrique-vendu`,
`vendus-sans-fabriquer` et `fabrique-vendu-stock` n'ont **regardé aucune
donnée** (`system → plan → synthesize`, `source=''` — le bord de C57), et
`ca-produit-vs-fabrique` est parti à l'analyse, où le code n'a pas pu s'exécuter
en trois essais. Un tirage ne tranche rien : le gain établi est celui des trois
tirages de l'avant/après.

**Classement.** Il est mesuré parce que `run_sql` est touché : les dix
formulations restent 10/10, et le SQL en règle 10/10.

### Ce qui reste

- **Le bord de C57** — une question de croisement posée sur un fil vierge qui
  repart en inventaire au lieu d'interroger — décide maintenant de trois des
  six écarts du banc de croisement. C'est le premier poste.
- **Le fil lié à `ventes`** n'attache toujours pas `production` pour une
  question qui la nomme.
- **La forme réparée n'est pas vérifiée.** Une requête qui agrège dans des
  sous-requêtes sort du champ de lecture du module, qui se tait : on constate
  la faute, on ne certifie pas la correction.

## La somme écrite en sous-requête ou dans un WITH (C62)

C61 a posé les deux propriétés du SQL. Elles ne lisaient qu'une chose : le
**niveau zéro** de la requête. Le trou est dans la réparation même.

La relance contre la multiplication dit au modèle d'agréger chaque table
séparément, « une sous-requête par table, qui groupe et somme ». C'est la bonne
consigne, il la suit — et la somme qu'il écrit alors n'est plus au niveau zéro.
La forme réparée était devenue la forme aveugle.

Appel direct de `somme_sql_sans_son_filtre` sur `02511fb`, filtre de `ventes`
monté avec le préfixe `ventes_` :

| forme | somme | sur `02511fb` |
|---|---|---|
| plate | `SELECT SUM(T3.quantite) FROM ventes_lignes_commande T3 JOIN …` | repérée |
| sous-requête | `SELECT (SELECT SUM(T3.quantite) FROM … ) AS v, …` | **pas repérée** |
| `WITH` | `WITH v AS (SELECT produit_id, SUM(quantite) … ) SELECT * FROM v` | **pas repérée** |

Le coût est un chiffre faux, plausible et muet : sur « Pour le VEL-02, combien
d'unités avons-nous fabriquées et combien en avons-nous vendues ? », fil vierge
et source non liée, le pilote a relevé un tirage sur sept à **147 vendues** — la
quantité sans le filtre des annulées — quand le juste est 130, sans un mot
d'avertissement.

### Chaque somme est jugée dans SA portée

Une **portée** est un `SELECT` et un seul : la requête principale, chaque
sous-requête du `SELECT`, du `FROM` ou du `WHERE`, le corps de chaque `WITH`.
Le SQL est découpé en portées, et chacune est pesée avec SES tables, SES
jointures et SES sommes — c'est là, et nulle part ailleurs, que se joue la
multiplication d'une somme.

Un filtre compte **là où il agit** : dans la portée qui somme, ou dans une
portée qui l'enferme — un `WHERE` extérieur restreint bien les lignes qu'une
sous-requête du `FROM` a rendues. Jamais dans une portée SŒUR : le filtre posé
dans une sous-requête ne filtre pas celle d'à côté, et les confondre rendrait
muette une somme fautive dès qu'une somme juste est écrite à côté d'elle.

### L'outil : un arbre, parce qu'il en faut un

`agents/retrieval/lecture.py` repère des mots-clés hors de toute parenthèse. Il
voit le niveau zéro, et c'est tout ce qu'il sait faire — c'est exactement ce qui
manque ici. Tenir les portées à la parenthèse près demande un arbre, et le
refaire à la main redonnerait un analyseur SQL, en moins sûr.

D'où **sqlglot** (pur Python, licence MIT, aucune dépendance), entré dans
`uv.lock`. Il ne sert QU'À `verification.py` : `classement.py` garde la lecture
maison, et `lecture.py` reste ce qu'il est.

### Ce qui est signalé, et ce qui ne l'est pas

Un test unitaire par forme, sur une base DuckDB qui porte les vrais rapports —
la sonde est réelle, pas une doublure.

| forme | verdict |
|---|---|
| deux sommes en sous-requête, dont une sans son filtre | **signalée** |
| une somme multipliée écrite dans un `WITH` (deux tables de faits jointes par le produit) | **signalée** |
| le filtre posé dans la sous-requête qui somme | silence |
| un `WITH` qui joint `commandes` et porte `statut`, la requête extérieure qui filtre `statut <> 'ANN'` avant de sommer | silence |
| une somme en euros sur `ventes` seule, filtre posé (CA 2025 = 1 496 743) | silence |
| un comptage (180 commandes, 463 lignes) | silence |
| une jointure de dimension (`commandes` → `clients`, CA par canal) | silence |

**Là où il doute, le module se tait, et il dit combien de fois.** Le nom d'un
`WITH` n'est pas une table du schéma : la portée qui le somme n'a pas de
cardinalité à mesurer, et elle est comptée dans `Lecture.illisibles`. C'est le
prix du dernier silence du tableau — la requête est juste, et on n'en juge rien
plutôt que d'en juger mal. **Sur les trois campagnes : 3 portées sommantes non
lues sur 22 requêtes SQL distinctes.**

### Les campagnes

Séquentielles, jamais de front. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue `sources/metier/catalogue.yaml`
lu en tête de chaque relevé.

| campagne | repère | après ce commit | portées non lues |
|---|---|---|---|
| VEL-02, fil vierge, 7 tirages | avant : 7/7 | **7/7** | — |
| `vel01-fabrique-vendu` + `vel04-production-ventes` (3 tirages) | 3/3 chacune | **3/3** et **3/3** | 0 sur 4 requêtes |
| croisement de sources (1 tirage) | 8/14 | **9/14** | 2 sur 9 requêtes |
| questions métier (1 tirage) | 12/12 | **12/12** | 1 sur 9 requêtes |

**L'avant/après de VEL-02 ne montre rien, et c'est un résultat.** Le défaut
relevé par le pilote est d'un tirage sur sept ; sur les sept tirages d'avant
comme sur les sept d'après, la réponse a été 484 fabriquées et 130 vendues,
sans 147 et sans avertissement. Ce qui est établi ici est donc l'absence de
régression sur le chemin normal, et non la fermeture du trou mesurée en bout de
chaîne : la fermeture, ce sont les tests par forme qui la tiennent.

### Ce qui reste

- **Le bord de C57** tient toujours deux des cinq écarts du banc de croisement
  (`vendus-sans-fabriquer`, `total-fabrique-vs-total-vendu` : `system → plan →
  synthesize`, aucune donnée regardée). C'est le premier poste.
- **Un `WITH` dont la requête extérieure somme** n'est pas jugé : le module ne
  résout pas le nom d'un `WITH` vers ses tables réelles. Il se tait et le dit.
  C'était 3 requêtes sur 22 sur ces campagnes.

## L'inventaire servi à une question croisée, en conversation neuve (C63)

Le pilote pose trois questions dans une conversation NEUVE, sans source liée,
sur le catalogue métier. Elles reçoivent l'inventaire des cinq sources —
« J'ai accès à 5 source(s) de données : … » — au lieu d'une réponse. La trace
est toujours la même : `system → plan → synthesize`, aucune donnée regardée.

C'est le bord nommé « le bord de C57 » à la fin de la section précédente, et
c'est le premier poste qu'elle désignait.

### Étape 1 — la sortie du planificateur, tirage par tirage

Avant toute réparation. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue
`sources/metier/catalogue.yaml`, conversation neuve, `source_de_travail=""`,
5 tirages par question. Ce qui est relevé est la sortie EXACTE du
planificateur et la règle de `graph.py` qui décide du tour.

| question | plan rendu | règle qui décide | reçu |
|---|---|---|---|
| « Pour chaque produit, donne les unités vendues et les unités fabriquées. » | `query`, `source='ventes'`, `sources=[]` — **5/5** | `_regle_choisir_la_source` | l'inventaire |
| « Quels produits a-t-on moins vendus que fabriqués ? Donne les quantités. » | `query`, `source='ventes'`, `sources=[]` — **5/5** | `_regle_choisir_la_source` | l'inventaire |
| « Pour le VEL-02, combien d'unités avons-nous fabriquées et combien en avons-nous vendues ? » | `query`, `source='production, ventes'` — **3/5** | aucune | 484 / 130 |
| (la même) | `query`, `source='production'` — **2/5** | `_regle_choisir_la_source` | l'inventaire |

Les trois témoins, au même relevé : « ventes ou production ? » ressort avec
`source='ventes, production'` 5/5 et reçoit la question du choix ; « quelles
sources as-tu ? » et « qu'est-ce que t'appelles source vente, production,
stock ? » n'atteignent jamais le planificateur — l'agent système les traite
(`system → synthesize`), 5/5 chacune.

### La cause, telle qu'elle est mesurée

**Le plan n'est pas muet.** Le planificateur lit les descriptions, et il écrit
un nom : `ventes` porte les ventes, la description le dit. Ce qui manque à ce
nom, c'est d'être le second — une question qui demande le fabriqué ET le vendu
ne tient pas dans une source.

**Ce qui transforme cette demi-désignation en inventaire est C57.**
`_regle_source_de_la_conversation` efface, sur un fil vierge, toute source que
personne n'a validée — ni l'utilisateur en la nommant, ni le fil en la portant
(`elif plan.source in declarees: plan.source = None`). C'est elle qui rend le
comportement indépendant de l'ordre de déclaration du YAML, et elle reste. Mais
elle efface aussi la MOITIÉ d'un périmètre, et `_regle_choisir_la_source` voit
alors un plan sans source. Un périmètre de deux noms, lui, survit :
`_regle_croiser_les_sources` passe avant et le pose. C'est exactement l'écart
entre les 3/5 de VEL-02 qui répondent et les 2/5 qui non.

### La réparation

Une seconde lecture bornée, `_relire_faute_de_source_designee`, posée dans le
nœud du plan à côté des deux autres. Quand les règles se sont arrêtées sur une
question et que le plan demande des données sans plus désigner personne, on
repose la MÊME question au planificateur avec un **constat sur son propre
plan** : il a classé une demande de données, et il n'a désigné qu'une source au
plus. Pas un mot de la question, pas une tournure, pas une paire de sources
citée en exemple.

On ne garde la seconde lecture que si elle désigne un PÉRIMÈTRE — au moins deux
sources déclarées, au sens de `_perimetre_croise`. **C'est la condition qui ne
défait pas C57** : une source seule redemandée au modèle serait une source
devinée de plus, et elle rouvrirait la dépendance à l'ordre du YAML. Une
demande qui porte sur deux sources, elle, n'est pas ambiguë — elle est double.

**Aucun prompt n'a bougé, et aucune docstring de `Plan` non plus.** Le constat
est ajouté à la suite du prompt composé, comme les trois contextes de
conversation (`_contexte_de_source` et ses voisines) : le gabarit
`prompts/planner.txt` ne bouge pas d'un caractère, et les sept empreintes
SHA-256 de `tests/unit/test_prompts.py` sont inchangées.

**La rédaction du constat a été mesurée, et la première était fausse.** Sur le
planificateur seul, 8 tirages, le constat ajouté au prompt :

| constat | vel02 | par-produit | moins-vendus |
|---|---|---|---|
| « tu n'as désigné AUCUNE source, nomme celle qui porte ce qui est demandé » | `source='production'` 8/8 | — | — |
| « tu n'as désigné qu'une source AU PLUS, énumère toutes celles dont la description couvre une partie de ce qui est demandé » | périmètre 8/8 | périmètre 8/8 | périmètre 8/8 |

La première demandait UNE source, et elle l'obtenait. Le constat retenu garde
de la première lecture ce qu'elle a vraiment fait — au plus une source — et
laisse le compte ouvert.

### L'avant/après

Mêmes conditions, 5 tirages, catalogue `sources/metier/catalogue.yaml` lu en
tête du relevé.

| question | inventaires avant | inventaires après | chiffres rendus |
|---|---|---|---|
| « Pour chaque produit… » | **5/5** | **0/5** | VEL-01 689/123, VEL-04 727/125 — les oracles |
| « Quels produits a-t-on moins vendus… » | **5/5** | **0/5** | les 8 vélos, oracles |
| « Pour le VEL-02… » | **2/5** | **0/5** | 484 / 130 — les oracles, 5/5 |

Les trois témoins, après : « ventes ou production ? » ne regarde aucune donnée
5/5 (la seconde lecture est PAYÉE puis JETÉE — `_perimetre_croise` la refuse
sur un message qui ne dit rien de plus que deux noms) ; « quelles sources
as-tu ? » et « qu'est-ce que t'appelles source vente, production, stock ? »
restent traitées par l'agent système, 5/5, sans jamais atteindre ce chemin.

Les trois questions du pilote entrent au banc
(`scripts/mesure_croisement_de_sources.py`), avec leurs oracles relus dans
Postgres le 2026-09-24 : VEL-02 130 unités vendues (147 annulées comprises),
VEL-01 123 (141), VEL-04 125 (131).

### Les campagnes

Séquentielles, jamais de front. Le catalogue est lu en tête de chaque relevé.

| campagne | catalogue | repère | après ce commit |
|---|---|---|---|
| les 3 questions du pilote + 3 témoins, 5 tirages | métier | 3 questions sur 3 en échec | **inventaire 0/15**, oracles rendus |
| croisement, les 3 questions neuves + 2 témoins (1 tirage) | métier | — | **4/5** |
| `mesure_choix_de_source.py` | par défaut | 6 tours attendus | **6/6** |
| `mesure_ambiguite_de_source.py` | les deux catalogues d'ambiguïté | 5/5 propositions par ordre | **5/5 et 5/5** |
| `mesure_surface_conversationnelle.py` | par défaut | 43/44 | **43/44** |

Le seul rouge de la surface est `choix-entre-deux-sources` (« titanic ou
iris ? »), le bord bistable déjà nommé et non réparé. Le seul rouge du banc de
croisement est `vel02-fabrique-vendu-pilote` sur ce tirage-là : le tour ATTEINT
les données — plus d'inventaire — et rend une quantité vendue qui n'est ni 130
ni le chiffre du piège. Le relevé de 5 tirages du même jour donne 130 cinq fois
sur cinq.

### Ce qui reste

- **La campagne de croisement COMPLÈTE n'a pas été relancée** : le budget de
  moteur de ce tour est parti dans l'étape 1, dans les deux rédactions du
  constat et dans les trois campagnes de non-régression. Le repère 9/14 n'est
  donc pas confronté ici.
- **Un tour qui allait servir l'inventaire paie désormais un appel de plus**,
  qu'il serve ou non. Il est borné à ce tour-là : un tour qui aboutit ne passe
  jamais par cette seconde lecture.
- **La première lecture de VEL-02 est instable** — `production, ventes` ou
  `production` selon le tirage. La seconde lecture rattrape le second cas ;
  elle ne rend pas la première stable.

## Un chiffre faux muet dans un CASE, et un bon résultat jeté (C64)

Deux défauts, mesurés sur le même banc, tous deux au bout du chemin SQL. Le
premier fait taire les deux propriétés de C61 ; le second jette un résultat qui
avait abouti. Ils n'ont rien en commun sauf l'endroit où ils se paient : ce que
l'utilisateur lit.

### Défaut 1 — la somme enrobée n'était plus une somme

« pour le VEL-01, combien on en a fabriqué et combien on en a vendu ? » a rendu
« le VEL-01 a été fabriqué 689 fois, et il a été vendu 141 fois ». L'oracle dit
123 vendus ; **141 est le même produit sans le filtre des annulées**. Aucune
remarque au modèle, aucun avertissement à l'utilisateur. Le SQL que le contrôle
a laissé passer :

```sql
SELECT SUM(CASE WHEN T1.code_produit = 'VEL-01' THEN T2.quantite ELSE 0 END)
       AS total_vendu
FROM ventes_produits AS T1
INNER JOIN ventes_lignes_commande AS T2 ON T1.produit_id = T2.produit_id
WHERE T1.code_produit = 'VEL-01';
```

`_colonnes_sommees` n'acceptait qu'un argument NU : `SUM(colonne)`. Dès que la
colonne était enrobée — un `CASE`, un `COALESCE`, `quantite * prix`, un cast —,
l'argument n'était plus une colonne, la portée entière était comptée illisible,
et les deux propriétés se taisaient. Le module fait le contraire de ce que son
propre texte annonce : il se tait au doute, et il n'y avait aucun doute ici
sur ce qui est sommé.

**La propriété qui répare** : chaque colonne ATTEINTE dans l'argument d'une
somme est une colonne sommée, sous l'expression qui la porte. On descend
jusqu'aux colonnes. On n'entre pas dans les CONDITIONS — `EQ`, `NEQ`, `IN`,
`LIKE`, `AND`… et la condition d'un `WHEN` : ce qui s'y lit filtre les lignes,
il ne dit pas ce qu'on somme. Sans cette réserve, `SUM(CASE WHEN
T1.code_produit = … THEN T2.quantite END)` aurait été jugé « sommé dans
`ventes_produits` », et la propriété de multiplication aurait repris une
requête juste, dans la forme même que le relevé montre.

Un test par forme, sur la base miniature :

| forme | ce qu'elle rend |
|---|---|
| `SUM(CASE WHEN code_produit = … THEN quantite ELSE 0 END)` | filtre manquant SIGNALÉ |
| `SUM(COALESCE(quantite, 0))` | filtre manquant SIGNALÉ |
| `SUM(quantite * montant_ligne_eur)` | filtre manquant SIGNALÉ |
| `SUM(CAST(quantite AS BIGINT))` | filtre manquant SIGNALÉ |
| le même `CASE` avec `WHERE statut <> 'ANN'` | **muet** |
| `SUM(CASE WHEN statut <> 'ANN' THEN quantite END)` | **muet** — le filtre posé DANS le `CASE` est un filtre |
| `COUNT(CASE WHEN … THEN quantite END)` | **muet** — un comptage n'est jamais touché |
| `SUM(CASE WHEN … THEN quantite_produite END)` sur la jointure qui duplique | multiplication SIGNALÉE |

`SUM(quantite * 2)` sur deux tables, la colonne non qualifiée, reste muet : ce
n'est plus l'expression qui fait douter, c'est la table devinée.

### Défaut 2 — un résultat réussi, jeté par la limite

« au total, combien d'unités sont sorties de l'atelier et combien sont parties
en commande ? », **3 fois sur 3, à la requête près**. Le déroulé, relevé requête
par requête :

| essai | issue |
|---|---|
| 1 | `FULL OUTER JOIN … ON T1.code_produit = T2.produit_id` — erreur de conversion |
| 2 | le même, `ON T1.code_produit = T2.code_produit` — colonne inexistante |
| 3 | `LEFT JOIN … GROUP BY 1, 2` — un `GROUP BY` sur des agrégats |
| 4 | **RÉUSSIT** — 180 669 / 20 557, et reçoit les deux remarques de C61 |
| 5 | deux sous-requêtes déjà agrégées, re-sommées : `SUM(T1.quantite_produite)` sur un `FROM (SELECT SUM(…))` — erreur de binder |
| 6 à 9 | **la même requête, à l'identique, quatre fois** — même erreur |
| — | `UsageLimitExceeded: request_limit of 10` |

**C'est bien la remarque qui fait dérailler les essais d'après**, et le relevé
le dit sans le réparer : la relance contre la multiplication demande d'agréger
chaque table séparément, le modèle écrit les sous-requêtes, puis resomme leur
résultat déjà agrégé et ne sait plus en sortir. Réécrire la remarque sur cette
seule lecture serait réparer sans mesure. Ce qui est réparé ici est l'autre
moitié : un tableau attendait, et il était jeté.

C61 a posé la règle — **jamais en silence, jamais jeté** — et c'était le seul
endroit du chemin SQL où l'on jetait. L'exception remontait, le nœud de
récupération la changeait en incident, et l'utilisateur lisait « la source de
données n'a pas pu être interrogée ». `run_retrieval` sert désormais la
DERNIÈRE requête réussie quand le budget s'épuise, avec ce qu'elle a de suspect
et avec le fait qu'elle n'est pas aboutie. Sans une seule réussite, l'exception
repart telle quelle : c'est bien un incident.

### L'avant/après

Les trois questions ENCHAÎNÉES dans un même processus, un fil neuf par
question, 3 passes. Posée seule, `vel01-fabrique-vendu` tombe souvent sur une
autre forme de SQL. Catalogue `sources/metier/catalogue.yaml`, moteur vLLM
`http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`), lus en tête
de chaque relevé.

| | avant | après |
|---|---|---|
| chiffres faux muets (141, 131, 147) | **0/9** | **0/9** |
| incidents | **3/9** | **0/9** |

**Le chiffre faux muet ne s'est pas reproduit dans ce relevé-là** : les trois
passes ont rendu 689 / 123, la forme `CASE` n'est pas ressortie. Le défaut est
tenu par les tests unitaires, forme par forme, et non par cette campagne. Les
trois incidents, eux, sont sortis 3 fois sur 3 avant et 0 fois sur 3 après. Ce
qui est servi à leur place porte les trois avertissements — la limite atteinte,
la somme multipliée, le filtre manquant — et un tableau (180 669 / 20 557) que
la réponse dit surévalué. Le chiffre reste faux ; il n'est plus muet, et il
n'est plus perdu.

### Les campagnes

Séquentielles, jamais deux de front. Le catalogue est lu en tête de chaque
relevé.

| campagne | catalogue | repère | après ce commit |
|---|---|---|---|
| les 3 questions enchaînées, 3 passes | métier | 3 incidents sur 9 | **0 incident sur 9** |
| croisement complet (1 tirage) | métier | 11/17 | **13/17** |
| `mesure_questions_metier.py` (1 tirage) | métier | 12/12 | **12/12** |
| `uv run pytest -p no:randomly` | — | vert | **1 605 verts** |

Les quatre rouges du croisement : `fabrique-vendu-stock` (131, le piège des
annulées sur trois sources), `produites-vs-vendues-fil-lie` (689 et 727
absents), `vendus-sans-fabriquer` (aucune donnée regardée sur ce tirage — la
même question aboutit 3 fois sur 3 dans le relevé enchaîné) et
`total-fabrique-vs-total-vendu`, qui n'est plus un incident mais rend le
chiffre multiplié, avertissements compris.

### Ce qui reste

- **La remarque contre la multiplication mène le modèle dans une impasse** sur
  les deux totaux : sous-requêtes agrégées, puis re-sommées. Relevé, non
  réparé — le réparer demande sa propre mesure.
- **`total-fabrique-vs-total-vendu` ne rend toujours pas 4 413 / 1 828.** Il
  rend un chiffre faux qui se dit faux, ce qui est la règle de C61 et non une
  réponse juste.
- **Le chiffre faux muet n'a pas de mesure de bout en bout** : il est tenu par
  les tests, forme par forme. Une campagne qui le ferait sortir à coup sûr
  demanderait de fixer la forme du SQL, donc de mesurer le banc.

## La requête renvoyée à l'identique après un échec (C65)

C64 laissait une impasse ouverte, et l'avait nommée : la remarque contre la
multiplication pousse le modèle vers la forme réparée — deux sous-requêtes déjà
agrégées —, il re-somme le résultat déjà agrégé, la base refuse, et il n'en
sort plus. `total-fabrique-vs-total-vendu` rendait 180 669 / 20 557 avec trois
avertissements là où les oracles disent **4 413** fabriquées et **1 828**
vendues.

### Étape 1 — le relevé, essai par essai

Moteur : vLLM, `http://localhost:8100/v1`, `google/gemma-4-E4B-it-qat-w4a16-ct`.
Catalogue : `sources/metier/catalogue.yaml`. Conversation neuve, `source_de_travail=""`,
trois passes. Le déroulé est le même aux trois, à un essai près.

| essai | ce que le modèle écrit | ce que la base rend |
|---|---|---|
| 1 | `FULL OUTER JOIN … ON T1.code_produit = T2.produit_id` | `Conversion Error: Could not convert string 'VEL-08' to INT64` |
| 2 | la même, `ON T1.code_produit = T2.code_produit` | `Binder Error: Table "T2" does not have a column named "code_produit"` — `Candidate bindings: "produit_id"` |
| 3 | jointure par `ventes_produits`, `GROUP BY 1, 2` | `Binder Error: GROUP BY clause cannot contain aggregates!` |
| 4 | la même sans le `GROUP BY` | **réussit** — 180 669 / 20 557, et reçoit les deux remarques de C61 |
| 5 | deux sous-requêtes agrégées, `SUM(T1.quantite_produite)` par-dessus | `Binder Error: Values list "T1" does not have a column named "quantite_produite"` — **aucun candidat nommé** |
| 6 | la même, `SUM(quantite_produite)` non qualifié | `Referenced column "quantite_produite" not found in FROM clause!` — `Candidate bindings: "total_produit"` |
| 7 à 9 | **la MÊME requête que l'essai 6, à l'identique** | la même erreur, trois fois de plus, jusqu'à `retrieval_request_limit` |

Les deux `COUNT(*)` qu'on lit entre les essais 4 et 5 dans la trace brute ne
sont pas du modèle : c'est la sonde de cardinalité qui mesure, dans la base, la
multiplication de l'essai 4.

Deux réponses aux deux questions posées :

- **l'erreur de binder ne dit pas toujours ce qui manque.** Celle de l'essai 5
  — celle qui ouvre la boucle — ne nomme aucun candidat. Le schéma ne le dit pas
  davantage : une sous-requête agrégée n'est dans aucun schéma ;
- **une requête renvoyée à l'identique reçoit exactement ce qu'elle a reçu la
  première fois.** Rien, dans ce que le modèle lit, ne distingue « corrige » de
  « tu viens d'écrire exactement ceci ».

### La cause, telle que mesurée

Le modèle ne boucle pas faute de savoir quoi faire : il boucle faute de savoir
**ce que sa propre sous-requête expose**, et faute de savoir qu'il se répète.
Ce sont deux faits, pas deux tournures, et ni l'un ni l'autre ne regarde la
question.

### La réparation

`agents/retrieval/diagnostic` ajoute deux faits au texte d'erreur rendu par
`run_sql`, sans jamais le remplacer. ① Une requête dont la signature — le texte
aux blancs près, casse et guillemets conservés — a déjà échoué dans ce tour le
reçoit. ② Une erreur qui porte sur une colonne reçoit, relation par relation,
les colonnes que la requête expose réellement : celles du schéma pour une table,
les alias de la projection pour une sous-requête ou un `WITH`, et rien du tout
pour une étoile qu'on refuse de déplier.

**Une seule rédaction a été écrite, et elle n'a pas eu de concurrente à
départager** : les deux faits ont suffi, mesurés 3/3 du premier coup. Le
troisième recours prévu — réécrire la phrase de la remarque de multiplication,
muette sur le cas sans clé de regroupement — **n'a pas été exercé**. Il n'y
avait rien à mesurer entre deux rédactions d'un texte qu'on n'a pas eu à
toucher, et `SommeMultipliee.pour_le_modele` est inchangée au caractère près.

| rédaction | score sur `total-fabrique-vs-total-vendu` |
|---|---|
| aucune — l'état de C64 | 0/3 (180 669 / 20 557) |
| les deux faits, première et seule rédaction | **3/3** (4 413 / 1 828) |

### L'avant/après

| | chiffres servis | appels LLM par tour | essais SQL |
|---|---|---|---|
| avant (`670c27a`) | 180 669 / 20 557, 3 passes sur 3 | 13 | 9, dont 4 identiques |
| après | **4 413 / 1 828**, 3 passes sur 3 | **9** | 4 |

Le fait ② est celui qui décide, et la chaîne se lit essai par essai : l'essai 2
reçoit `T1 expose of_id, code_of, code_produit, … ; T2 expose ligne_id,
commande_id, produit_id, quantite, …`, l'essai 3 cesse d'inventer
`T2.code_produit` et passe par `ventes_produits`, l'essai 4 écrit deux
sous-requêtes indépendantes et rend 4 413 / 1 828 — sans une remarque et sans un
avertissement. Le fait ① n'a **jamais eu à se déclencher** dans les trois passes
d'après : la boucle ne s'ouvre plus. Il reste, parce que la propriété est vraie
quelle que soit la question et que rien ne garantit que ce soit la dernière
forme d'impasse.

### Les campagnes

Toutes séquentielles. Catalogue lu en tête de chacune :
`sources/metier/catalogue.yaml`, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`).

| campagne | repère | ce tour |
|---|---|---|
| `vel01-fabrique-vendu` + `vel04-production-ventes`, 3 tirages | 3/3 et 3/3 | **6/6** — 689/123 et 727/125 |
| croisement complet, 1 tirage | 13/17 | **13/17** |
| `scripts/mesure_questions_metier.py`, 1 tirage | 12/12 | **12/12** |
| `uv run pytest -p no:randomly` | 1 621 verts | **1 621 verts** |

Les quatre rouges du croisement sont `fabrique-vendu-stock` (131, le piège des
annulées sur trois sources), `produites-vs-vendues-fil-lie` (689 et 727
absents), `vel01-fabrique-vendu` et `vendus-sans-fabriquer` (aucune donnée
regardée sur ce tirage). `total-fabrique-vs-total-vendu`, rouge à C64, est vert.
`vel01-fabrique-vendu` est vert 3 fois sur 3 dans sa campagne dédiée ci-dessus :
son rouge ici est un tirage de routage, pas le chemin SQL.

### Ce qui reste

- **Le fait ① n'a pas de mesure de bout en bout.** La boucle qu'il ferme ne
  s'ouvre plus sur cette question ; il est tenu par les tests, essai par essai.
- **`fabrique-vendu-stock` garde son 131** : trois sources, et le filtre des
  annulées perdu sur la troisième.
- **`produites-vs-vendues-fil-lie` ne rend toujours pas 689 et 727** — le
  périmètre s'ajoute au fil, la question reste servie sur `ventes` seule.

## La moitié du périmètre perdue sur un fil lié (C66)

Un fil lié à `ventes`. « compare les quantités produites et les quantités
vendues par produit » n'a plus rendu ses chiffres depuis C58 : 0/3. Sur le MÊME
fil, « compare le chiffre d'affaires par produit avec les quantités
fabriquées » et « est-ce qu'on vend plus que ce qu'on produit ? » passent. Ce
n'est donc pas « jamais », et c'est l'écart entre les trois qu'il fallait
mesurer.

### Étape 1 — la sortie du planificateur, tirage par tirage

Avant toute réparation. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue
`sources/metier/catalogue.yaml`, fil lié à `ventes`, 5 tirages par question.
Le plan est capturé AVANT que les règles le mutent, puis relu après.

| question | plan AVANT les règles | plan APRÈS | ce qui décide |
|---|---|---|---|
| « compare les quantités produites et les quantités vendues par produit » | `analyze`, `source='production'`, `sources=[]` — **5/5** | `analyze` sur `ventes` | `_relire_sans_la_source_du_fil` **refuse**, puis `_regle_source_de_la_conversation` repose `ventes` |
| « compare le chiffre d'affaires par produit avec les quantités fabriquées » | `analyze`, `source='ventes'`, `sources=[]` — **5/5** | `analyze` sur `ventes, production` | `_relire_sans_la_source_du_fil` **ouvre** ; la relecture rend `sources=['ventes','production']` 5/5 |
| « est-ce qu'on vend plus que ce qu'on produit ? » | `query`, `source='ventes'`, `sources=[]` — **5/5** | `query` sur `ventes, production` | `_relire_sans_la_source_du_fil` **ouvre** ; la relecture rend `source='ventes, production'` 5/5 |

Cinq tirages sur cinq dans les trois cas : le relevé est déterministe, et il ne
laisse aucune place à un tirage malheureux.

### La cause, telle que mesurée

**Les trois plans nomment UNE source ; ce qui les sépare est LAQUELLE.** Les
deux questions qui passent échoent la source du fil — `ventes` —, et la
condition 3 de `_relire_sans_la_source_du_fil` exigeait exactement cela : « le
plan désigne EXACTEMENT la source du fil ». Celle qui échoue nomme
`production`, c'est-à-dire l'AUTRE moitié du même périmètre. La relecture lui
était refusée, au motif écrit dans sa propre docstring — « un plan qui en
désigne une AUTRE a désobéi à la phrase, donc il l'a lue, et la retirer
n'apprendrait rien ».

Le relevé dit l'inverse : le modèle a lu la phrase dans les trois cas. Ce qu'il
en a fait diffère, et le refus se paie deux règles plus loin —
`_regle_source_de_la_conversation` repose `ventes` par-dessus `production`, la
source que le planificateur avait vue est perdue, et la question est servie sur
la moitié de son périmètre. Sans un mot : le tour interroge `ventes`, y trouve
123 et 125, et ne peut pas calculer 689 et 727.

### La réparation

Un mot retiré à une condition, et rien d'autre : la relecture s'ouvre sur un
plan qui ne désigne QU'UNE source, quelle qu'elle soit, au lieu du seul plan
qui échoe celle du fil. La propriété qu'elle lit est « ce plan a nommé au plus
la moitié d'un périmètre », et elle ne regarde pas lequel des deux noms il
porte.

Les quatre autres conditions ne bougent pas, et ce sont elles qui bornent : la
relecture ne peut qu'AJOUTER — elle n'est gardée que si elle désigne un
périmètre d'au moins deux sources (`_perimetre_croise`, le même décompte
qu'ailleurs) —, une source imposée par l'appelant la ferme, et un fil vierge ne
la paie pas. Un tour ordinaire sur un fil lié garde donc son plan, comme avant.

**Aucun prompt n'a bougé** : ni un fichier de `prompts/`, ni une fiche d'outil,
ni la docstring de `Plan`. Aucune empreinte SHA-256 ne change.

### L'avant/après

Les trois questions du relevé, 5 tirages chacune, valeurs rendues contre les
oracles. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue
`sources/metier/catalogue.yaml`.

| question | avant | après |
|---|---|---|
| `produites-vs-vendues-fil-lie` | **0/3** (C60 → C65) — 689 et 727 absents | **5/5** — 689/123 et 727/125 |
| `ca-produit-vs-fabrique-fil-lie` | vert | **5/5** — 323 700 € pour 461, annulées exclues et dites |
| `vend-plus-quon-produit-fil-lie` | vert | **5/5** — 4 413 fabriquées contre 1 828 vendues |

### Les campagnes

Toutes séquentielles. Catalogue lu en tête de chacune :
`sources/metier/catalogue.yaml`, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`) — sauf les deux dernières, qui lisent
leur propre catalogue.

| campagne | repère | ce tour |
|---|---|---|
| les 3 questions du relevé, 5 tirages | 0/5, 5/5, 5/5 | **15/15** |
| croisement complet, 1 tirage | 13/17 | **13/17** |
| `scripts/mesure_questions_metier.py`, 1 tirage | 12/12 | **12/12** |
| `uv run pytest -p no:randomly` | 1 621 verts | **1 623 verts** (2 tests ajoutés) |
| `scripts/mesure_choix_de_source.py` — catalogue `sources/catalogue.yaml` (titanic, iris) | 6/6 | **6/6** — le verrou tient sur `titanic`, la bascule vers `iris` est annoncée |
| `scripts/mesure_ambiguite_de_source.py`, 1 essai — catalogues `tests/catalogues/ambiguite/*.yaml` | la proposition dans les deux ordres | **1/1 et 1/1** — proposition dans les deux ordres, indépendante de l'ordre du YAML |

Les trois témoins du croisement sont verts : `temoin-une-seule-source`
(1 496 743 € sur `ventes` seule), `temoin-question-de-sens`, et
`temoin-faire-choisir`, qui fait toujours choisir. Les quatre rouges du
croisement sont `fabrique-vendu-stock` (131, le piège des annulées sur trois
sources), `ca-produit-vs-fabrique-fil-lie`, `vel01-fabrique-vendu` et
`vendus-sans-fabriquer`. Les trois derniers sont des tirages : le premier rend
5/5 dans sa campagne dédiée ci-dessus, les deux autres étaient déjà rouges à
C65 pour n'avoir regardé aucune donnée.

### Ce qui reste

- **`fabrique-vendu-stock` garde son 131** : trois sources, et le filtre des
  annulées perdu sur la troisième. Inchangé depuis C65.
- **`vendus-sans-fabriquer` ne regarde aucune donnée** sur son tirage, comme à
  C65 : le croisement par DIFFÉRENCE n'a toujours pas de mesure stable.
- **La relecture coûte un appel LLM de plus** sur les tours d'un fil lié où le
  plan nomme une source autre que celle du fil. Le coût était déjà payé sur
  ceux qui l'échoent ; il s'étend à ce cas-là, et pas au-delà.

## Une question sur l'autre source seule, sur un fil lié (C67)

Un fil lié à `ventes`. « Combien d'arrêts machine avons-nous eus en 2025 ? » —
oracle 70 — reçoit « Je n'ai pas interrogé la source pour cette question, je ne
peux donc rien en affirmer ». « Combien d'ordres de fabrication ont été lancés
en 2025 ? » — oracle 140 — reçoit « Je n'ai toujours pas accès à la table
`production` ». Sur le MÊME fil, « Combien d'unités de VEL-04 avons-nous
fabriquées, et combien en avons-nous vendues ? » rend 727 et 125.

Ce n'est donc pas « un fil lié ne sort jamais de sa source » : c'est une
question qui ne porte QUE sur l'autre source qui n'en sort pas. C66 avait
réparé la moitié perdue d'un périmètre ; il restait le cas où le tour n'en
demande aucune moitié du fil.

### Étape 1 — la sortie du planificateur, tirage par tirage

Avant toute réparation. Moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue
`sources/metier/catalogue.yaml`, fil lié à `ventes`, 5 tirages par question. Le
plan est capturé AVANT que les règles le mutent, la relecture est relevée telle
qu'elle sort, et le plan est relu après les règles.

| question | plan AVANT les règles | seconde lecture | plan APRÈS | ce qui décide |
|---|---|---|---|---|
| « Combien d'arrêts machine avons-nous eus en 2025 ? » | `query`, `source='production'`, `sources=[]` — **5/5** | `query`, `source='production'` — **5/5** | `query` sur `ventes` | la relecture ne désigne qu'UNE source : `_perimetre_croise` rend `[]`, elle est **jetée**, puis `_regle_source_de_la_conversation` repose `ventes` |
| « Combien d'ordres de fabrication ont été lancés en 2025 ? » | `query`, `source='production'`, `sources=[]` — **5/5** | `query`, `source='production'` — **5/5** | `query` sur `ventes` | idem |
| « Combien d'unités de VEL-04 avons-nous fabriquées, et combien en avons-nous vendues ? » | `query`, `source='ventes'`, `sources=[]` — **5/5** | `query`, `source='production, ventes'` — **5/5** | `query` sur `ventes, production` | la relecture désigne un périmètre : elle est **gardée**, et le tour rend 727 / 125 |

Cinq tirages sur cinq dans les trois cas : le relevé est déterministe.

**Les deux témoins ont été relevés dans la même condition**, et ce sont eux qui
ont dicté la forme de la réparation :

| question | plan AVANT les règles | seconde lecture | ce que ça dit |
|---|---|---|---|
| « Quel chiffre d'affaires avons-nous réalisé en 2025 ? » | `query`, `source='ventes'` — **5/5** | `query`, `source='ventes'` — **5/5** | la relecture relit la source du fil : il n'y a pas de seconde source |
| « Combien de salariés avons-nous ? » | `query`, `source='ventes'` — **5/5** | `query`, `source=''` — **5/5** | délivré de la phrase du fil, le modèle n'invente AUCUNE source à une question que le catalogue ne porte pas |

### La cause, telle que mesurée

Le planificateur voit `production`, et il le dit — dès la première lecture,
malgré la phrase de `_contexte_de_source` qui lui demande de prendre `ventes`.
La seconde lecture, qui ne voit pas cette phrase, le redit. L'information
n'était pas manquante : elle était **jetée**.

Ce qui la jetait est la condition 4 de `_relire_sans_la_source_du_fil` : la
relecture n'était gardée que si elle désignait au moins DEUX sources. Une
question qui ne porte que sur `production` n'en rend qu'une, la relecture était
refusée, et `_regle_source_de_la_conversation` reposait `ventes` par-dessus. Le
tour interrogeait alors le carnet de commandes pour y chercher des arrêts
machine, et répondait qu'il n'en trouvait pas.

### La réparation

Une propriété, et une seule : **une relecture qui désigne une source AUTRE que
celle du fil n'est plus jetée — le périmètre du tour devient le fil PLUS elle**
(`_le_fil_plus_la_source_relue`). Pas un remplacement : un enrichissement. Le
couple monté repasse par `_perimetre_croise`, qui est le même décompte
qu'ailleurs, et il n'est retenu que s'il en ressort.

Deux conditions le bornent, et les deux sont mesurées ci-dessus : une relecture
qui repose la source du fil n'ajoute rien, et une relecture qui ne désigne rien
n'ajoute rien non plus. Une troisième a été payée sur une campagne : **une
source que l'UTILISATEUR nomme ferme ce montage**. Sans elle, « et dans iris,
combien de lignes ? », posé sur un fil lié à `titanic`, montait le couple
`titanic, iris` — la réponse restait juste (150 lignes), mais la conversation
restait liée à `titanic` et la bascule n'était plus ANNONCÉE. Ce qui est
dangereux n'est pas de changer de source, c'est d'en changer en silence.
Quelqu'un qui écrit un nom a tranché : `_regle_source_de_la_conversation`
traite déjà cette bascule, et on lit la désignation avec la fonction qu'elle
emploie (`introspection.source_nommee`).

**Pour CE tour, et rien de plus.** Le périmètre s'écrit empaqueté dans
`plan.source`, qui n'est le nom d'aucune source déclarée : `_lier_la_source` ne
retient qu'un nom du catalogue, la source liée à la conversation reste `ventes`,
et le tour suivant en repart. Aucune phrase d'annonce n'est ajoutée à la
réponse : l'utilisateur voit les sources sur lesquelles elle s'appuie par la
ligne « Ce qu'en dit le dictionnaire de `…` » et par la trace, qui porte
`query sur ventes, production — seconde lecture sans la source du fil`.

**Aucun prompt n'a bougé** : ni un fichier de `prompts/`, ni une fiche d'outil,
ni la docstring de `Plan`. Aucune empreinte SHA-256 ne change.

### L'avant/après

Fil lié à `ventes`, 5 tirages par question, valeurs rendues contre les oracles.
Moteur `http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`),
catalogue `sources/metier/catalogue.yaml`.

| question | oracle | avant | après |
|---|---|---|---|
| « Combien d'arrêts machine avons-nous eus en 2025 ? » | 70 | **0/5** — « je n'ai pas interrogé la source » | **5/5** — 70 |
| « Combien d'ordres de fabrication ont été lancés en 2025 ? » | 140 | **0/5** — « je n'ai toujours pas accès à la table `production` » | **5/5** — 140 |
| « Combien d'unités de VEL-04 … fabriquées, et combien … vendues ? » | 727 / 125 | 5/5 | **5/5** — 727 / 125 |
| « Combien de salariés avons-nous ? » | aucune source de plus | 5/5 — `query` sur `ventes` | **5/5** — `query` sur `ventes`, aucune source montée en plus |

### Les campagnes

Toutes séquentielles, jamais deux de front. Catalogue lu en tête de chacune :
`sources/metier/catalogue.yaml`, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`) — sauf les deux qui lisent leur propre
catalogue, nommé dans la ligne.

| campagne | repère | ce tour |
|---|---|---|
| les 3 questions du relevé + « combien de salariés », 5 tirages | 0/5, 0/5, 5/5, 5/5 | **20/20** |
| croisement complet, 1 tirage | 13/17 | **15/20** — les 17 d'avant : **12/17** ; les 3 questions ajoutées par C67 : **3/3** |
| `scripts/mesure_questions_metier.py`, 1 tirage | 12/12 | **12/12** |
| `scripts/mesure_choix_de_source.py` — catalogue `sources/catalogue.yaml` (titanic, iris) | 6/6 | **6/6** — le verrou tient sur `titanic`, la bascule vers `iris` est annoncée |
| `scripts/mesure_ambiguite_de_source.py`, 1 essai — catalogues `tests/catalogues/ambiguite/*.yaml` | 1/1 et 1/1 | **1/1 et 1/1** — proposition dans les deux ordres |
| `scripts/mesure_sources_nommees.py`, 1 tirage | 60/60 à 3 tirages | **20/20** |
| `uv run pytest -p no:randomly` | 1 623 verts | **1 627 verts** (4 tests de plus) |
| `ruff check`, `ruff format --check` | verts | **verts** |

Trois questions sont entrées au banc du croisement : `arrets-2025-fil-lie`,
`ordres-2025-fil-lie` et le témoin `temoin-hors-du-catalogue`. Le repère passe
donc de 17 à 20 questions, et la colonne « les 17 d'avant » reste comparable.

Les quatre témoins du croisement sont verts : `temoin-une-seule-source`
(1 496 743 € sur `ventes` seule), `temoin-question-de-sens`,
`temoin-faire-choisir` qui fait toujours choisir, et le nouveau
`temoin-hors-du-catalogue`, qui reste sur `ventes` sans monter quoi que ce soit
de plus.

### Ce qui reste

- **`vend-plus-quon-produit` est tombé à 0/3, et ce n'est pas C67.** Les deux
  variantes — fil vierge et fil lié — rendent 0/3 sur ce tour. Le même banc,
  aux mêmes réglages, sur `f78a97e` sans la réparation : **0/3 lui aussi**. Le
  périmètre est monté dans les deux cas (`ventes_lignes_commande` et
  `production_ordres_fabrication` sont dans la réponse) ; ce qui manque est le
  calcul, que le modèle remplace par la description de ce qu'il faudrait faire.
  C'est une instabilité de la question, antérieure à ce tour.
- **`fabrique-vendu-stock` garde son 131**, et **`vendus-sans-fabriquer` ne
  regarde aucune donnée** : inchangés depuis C65.
- **`vel01-fabrique-vendu` rouge sur son tirage** : déjà rouge à C65 et C66.
- **Rien n'est persisté d'un tour à l'autre.** Le périmètre enrichi vaut pour le
  tour, et la source liée à la conversation ne bouge pas. Faut-il qu'un fil
  puisse se lier à DEUX sources ? C'est une décision du propriétaire, et elle
  n'a pas été prise ici.
- **La relecture ne coûte aucun appel de plus qu'à C66.** Elle était déjà
  ouverte sur ces tours-là ; ce qui change est ce qu'on fait de sa réponse.

## Un fil ne s'enrichit que d'une source RELIÉE (C68)

C67 a rendu au tour la source que la relecture désigne : sur un fil lié, le
périmètre devient « le fil PLUS elle ». Le pilote l'a mesuré, et la troisième
question de son relevé montre le prix de cette générosité.

| fil | question | périmètre monté | réponse |
|---|---|---|---|
| `ventes` | « Quelle machine a eu le plus d'arrêts en 2025 ? » | `ventes, production` | M-009, 16 arrêts — **voulu** |
| `ventes` | « Combien de références avons-nous en stock au dernier inventaire ? » | `ventes, stocks` | 12 — **voulu** |
| `titanic` | « Combien de fleurs de l'espèce setosa y a-t-il ? » | `iris, titanic` | 50, et « le dictionnaire de `iris, titanic` » — **le fouillis** |

Le chiffre est juste dans les trois cas. Le troisième périmètre n'a aucun
sens : rien ne relie des passagers à des fleurs. « C'est pour éviter que ça
soit un fouillis sans nom. »

### La cause, telle que mesurée

`_le_fil_plus_la_source_relue` ajoutait la source relue à celle du fil sans
jamais demander ce que les deux avaient en commun. Les deux premiers cas et le
troisième passaient par le même code, dans le même état : le couple était monté
parce que la relecture avait nommé une autre source, et pour aucune autre
raison.

### La propriété qui les sépare, et elle était déjà lue dans les données

`agents/retrieval/croisement.py` sait depuis C63 dire quelle clé traverse un
périmètre : une colonne de même nom des deux côtés, clé naturelle d'UN côté
seulement, dont toutes les valeurs de l'autre côté se retrouvent en face
(`relier_les_sources`). C'est ce décompte qu'on interroge — pas un second, qui
divergerait du premier. Relevé le 2026-09-25, catalogue
`sources/metier/catalogue.yaml`, toutes les paires, `max_rows=10000` :

| paire | clé prouvée | verdict | coût |
|---|---|---|---|
| `ventes` + `production` | `production_ordres_fabrication.code_produit` → `ventes_produits` | reliées | 0,36 s |
| `ventes` + `stocks` | `stocks_mouvements.code_produit` et `stocks_inventaire.code_produit` → `ventes_produits` | reliées | 0,34 s |
| `titanic` + `iris` | aucune colonne commune | **étrangères** | 0,23 s |
| `production` + `stocks` | aucune clé prouvée | étrangères | 0,12 s |
| `ventes` + `titanic`, `ventes` + `iris`, `production` + `iris`, `production` + `titanic`, `stocks` + `iris`, `stocks` + `titanic` | — | étrangères | 0,07 à 0,23 s |

### La réparation

Deux phrases. **Une clé au moins relie la source du fil à la source relue : on
enrichit, comme C67.** Aucune : c'est une **BASCULE** vers la source relue,
traitée exactement comme quand l'utilisateur nomme une source — la conversation
change de source liée, et `_lier_la_source` l'annonce.

Ni enrichissement muet, ni refus. La question porte sur l'autre source : on y
répond, et on dit qu'on a changé. Ce qui est dangereux n'est pas de changer de
source, c'est d'en changer en silence.

La bascule circule dans `PlanContext.bascule_relue`, et
`_regle_source_de_la_conversation` la laisse passer au même endroit qu'une
source nommée par l'utilisateur : c'est le même fait — quelque chose a tranché
pour ce tour, et la source du fil ne se repose pas par-dessus.

**Le prix est l'ouverture des deux sources au moment du plan** — 0,35 s par
paire sur le catalogue métier —, et il est gardé en cache pour la vie du
processus (`_une_cle_relie`) : les données d'une source ne changent pas d'un
tour à l'autre pendant une session, et la paire est la même pour tous les fils.
Le premier tour qui pose la question paie, les suivants lisent. Il n'est payé
que là où la question se pose : un tour sans relecture, ou dont la relecture
repose la source du fil, n'ouvre rien.

**Une source injoignable ne prouve aucune clé**, donc bascule. C'est le bord
sûr : une bascule est ANNONCÉE, là où un enrichissement supposé sur une source
qu'on n'a pas su lire serait muet.

**Aucun prompt n'a bougé** : ni un fichier de `prompts/`, ni une fiche d'outil,
ni la docstring de `Plan`. Aucune empreinte SHA-256 ne change.

### L'avant/après

Sept questions, 3 tirages chacune, moteur `http://localhost:8100/v1`
(`google/gemma-4-E4B-it-qat-w4a16-ct`), catalogue
`sources/metier/catalogue.yaml`. « Avant » est le comportement de C67, obtenu en
faisant répondre « oui » à la preuve de clé : le couple est alors monté sans que
rien ne le fonde, exactement comme avant ce tour.

| fil | question | périmètre du tour | bascule | avant | après |
|---|---|---|---|---|---|
| `titanic` | « Combien de fleurs de l'espèce setosa y a-t-il ? » | `iris` seule | **annoncée** | **0/3** — 50, mais sur `iris, titanic`, et le fil restait sur `titanic` | **3/3** — « Je passe sur la source `iris` — on travaillait sur `titanic`. Il y a 50 fleurs de l'espèce setosa. » |
| `ventes` | « Quelle machine a eu le plus d'arrêts en 2025 ? » | `ventes, production` | non | 3/3 — M-009, 16 | **3/3** — M-009, 16 |
| `ventes` | « Combien de références avons-nous en stock au dernier inventaire ? » | `ventes, stocks` | non | 3/3 — 12 | **3/3** — 12 |
| `titanic` | « Combien de passagers ont survécu ? » | `titanic` seule | non | 3/3 — 342 | **3/3** — 342 |
| `ventes` | « Quel chiffre d'affaires avons-nous réalisé en 2025 ? » | `ventes` seule | non | 3/3 — 1 496 743,00 € | **3/3** — 1 496 743,00 € |
| `ventes` | « Combien de salariés avons-nous ? » | `ventes` seule | non | 3/3 — aucune source inventée | **3/3** — aucune source inventée |
| `titanic` | « et dans iris, combien de lignes ? » | `iris` seule | **annoncée** (source NOMMÉE) | 3/3 — 150 | **3/3** — 150, chemin inchangé |

**18/21 avant, 21/21 après.** Les deux cas « voulus » gardent leur périmètre et
leurs chiffres ; le fouillis devient une bascule annoncée ; les quatre témoins
ne bougent pas.

**La latence ajoutée**, mesurée hors LLM sur le catalogue métier,
`max_rows=10000` : `ventes`+`production` **347 ms**, `ventes`+`stocks`
**422 ms**, `titanic`+`iris` **98 ms** — une fois par paire, puis zéro (cache).
Dans les tours mesurés, le premier tirage d'une paire reliée paie +1,6 à +2,0 s
et les suivants retombent dans le bruit du modèle (±1,5 s d'un tirage à
l'autre).

### Les campagnes

Toutes séquentielles, jamais deux de front. Le moteur n'a servi aucune autre
mesure pendant ce tour (une seule connexion sur le port 8100). Catalogue lu en
tête de chacune : `sources/metier/catalogue.yaml`, moteur
`http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`) — sauf les
deux qui lisent leur propre catalogue, nommé dans la ligne.

| campagne | repère | ce tour |
|---|---|---|
| les 6 témoins + le cas `setosa`, 3 tirages | 18/21 (comportement C67) | **21/21** |
| croisement complet, 1 tirage | 15/20 | **22/25** — les 20 d'avant : **17/20** ; les 5 questions ajoutées ici : **5/5** |
| `scripts/mesure_questions_metier.py`, 1 tirage | 12/12 | **12/12** |
| `scripts/mesure_choix_de_source.py` — catalogue `sources/catalogue.yaml` (titanic, iris) | 6/6 | **6/6** — le verrou tient sur `titanic`, la bascule vers `iris` est annoncée |
| `scripts/mesure_sources_nommees.py`, 1 tirage | 20/20 | **20/20** |
| `scripts/mesure_ambiguite_de_source.py`, 5 essais — catalogues `tests/catalogues/ambiguite/*.yaml` | 1/1 et 1/1 | **5/5 et 5/5** — proposition dans les deux ordres |
| `uv run pytest -p no:randomly` | 1 627 verts | **1 631 verts** (4 tests de plus) |
| `ruff check`, `ruff format --check` | verts | **verts** |

Cinq questions sont entrées au banc du croisement : `machine-arrets-fil-lie` et
`references-en-stock-fil-lie` (les paires RELIÉES), `setosa-sur-fil-titanic`
(les ÉTRANGÈRES), et deux témoins — `temoin-survivants-titanic` et
`temoin-bascule-nommee`. Le repère passe de 20 à 25 questions, et la colonne
« les 20 d'avant » reste comparable.

Le banc a gagné un oracle : `source_apres` — la source à laquelle le fil est lié
APRÈS le tour, et l'exigence que la bascule soit écrite dans la réponse. Sans
lui, `setosa` passait : le chiffre était juste.

### Ce qui reste

- **`vend-plus-quon-produit` (fil vierge) reste 0/1**, et
  **`vendus-sans-fabriquer` ne regarde toujours aucune donnée** : inchangés
  depuis C65-C67, sans rapport avec ce tour. La variante sur fil lié, elle,
  est verte.
- **`fabrique-vendu-stock` garde son défaut** : la question à TROIS sources
  perd un chiffre. Rien ici ne la vise — la preuve de clé se lit sur une paire,
  et `production` + `stocks` n'en portent aucune.
- **La preuve se lit sur une PAIRE.** Le fil et la source relue, rien d'autre.
  Un périmètre à trois monté par le planificateur lui-même
  (`_perimetre_croise`) n'est pas soumis à cette preuve : ce qu'un tour DÉSIGNE
  n'a pas à être justifié, c'est ce qu'on lui AJOUTE qui doit l'être.
- **Deux sources reliées par une clé peuvent quand même n'avoir rien à se
  dire.** La clé prouve qu'elles parlent des mêmes objets ; elle ne dit pas que
  la question porte sur les deux. C'est `_perimetre_croise` qui garde ce bord,
  comme avant.
- **Le cache vit avec le processus.** Une source dont les données changent en
  cours de session garderait son verdict. Les sources de ce socle sont lues,
  jamais écrites ; si cela changeait, il faudrait une péremption.
