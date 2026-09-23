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
