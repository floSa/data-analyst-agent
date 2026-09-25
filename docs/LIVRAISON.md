# Livraison — data-analyst-agent

Ce document s'adresse à qui **reçoit** le produit et ne l'a jamais vu.
Il dit ce que l'application fait, ce qui marche, ce qui ne marche pas, et par
quoi continuer.

Les chiffres **du jour de livraison** ne sont pas ici : ils sont dans
[docs/releve-de-livraison.md](releve-de-livraison.md), écrit par la campagne
lancée le jour même. Ce document-ci n'en recopie aucun.

---

## 1. Ce que fait l'application

C'est un **agent conversationnel sur données**, installé sur vos machines.
On lui déclare des sources : un CSV, un classeur Excel, une base Postgres, une
base DuckDB.
On lui pose une question en français, dans une page de chat.
Il **récupère** : il écrit le SQL, jointures comprises, et l'exécute en lecture
seule.
Il **analyse** : il écrit du Python — KPI, statistiques, graphiques — et
l'exécute dans un bac à sable Docker sans réseau.
Il **prédit** : il appelle un modèle de machine learning, après avoir validé et
réclamé les attributs manquants.
Il **répond sur lui-même** : « quelles données as-tu ? », « que sais-tu faire ? »
— à partir des faits du dépôt, jamais de la mémoire du modèle.
Il **reprend ce qu'il a produit** : « remontre-moi le tableau », « reprends le
graphe et mets les barres en bleu ».
La réponse est du texte, plus les objets affichables : un tableau, une figure.
Un seul modèle de langage, joint par un endpoint OpenAI-compatible, servi
localement : **rien ne sort de la machine**.

Chaque compte est cloisonné : hormis `/health`, aucune route n'est atteignable
sans session, et personne ne voit les conversations d'un autre.

Le détail est dans [README.md](../README.md) et
[docs/ARCHITECTURE.md](ARCHITECTURE.md).

---

## 2. Ce qui marche

**Les chiffres du jour sont dans
[docs/releve-de-livraison.md](releve-de-livraison.md).**
Ce qui suit dit ce qui est mesuré, où, et par quelle commande — pas le score.

| Ce qui est mesuré | Le banc | Le document |
|---|---|---|
| Les douze questions métier d'un fabricant de vélos | `scripts/mesure_questions_metier.py` | [sources-metier.md](sources-metier.md) |
| Les questions qui **croisent deux sources** | `scripts/mesure_croisement_de_sources.py` | [croisement-de-sources.md](croisement-de-sources.md) |
| Ce que l'agent sait dire **de lui-même** | `scripts/mesure_surface_conversationnelle.py` | [surface-conversationnelle.md](surface-conversationnelle.md) |
| Le choix et le verrou de la source de travail | `scripts/mesure_choix_de_source.py` | [surface-conversationnelle.md](surface-conversationnelle.md) |
| Une source nommée dans la phrase | `scripts/mesure_sources_nommees.py` | [croisement-de-sources.md](croisement-de-sources.md) |
| La proposition quand deux sources conviennent | `scripts/mesure_ambiguite_de_source.py` | [croisement-de-sources.md](croisement-de-sources.md) |
| Le parcours de démonstration, 16 tours | `scripts/mesure_parcours_de_demonstration.py` | [sources-de-demonstration.md](sources-de-demonstration.md) |
| Ce que chaque tour coûte en appels au modèle | `scripts/mesure_contexte.py` | [memoire-de-conversation.md](memoire-de-conversation.md) |
| La suite de tests | `uv run pytest` | [ARCHITECTURE §6](ARCHITECTURE.md#6-stratégie-de-tests) |

Toutes les campagnes se lancent **séquentiellement**, jamais deux de front : le
moteur est unique, et deux mesures en parallèle ne mesurent plus rien.

Le dernier relevé écrit dans le dépôt avant la livraison est celui de **C66**
([croisement-de-sources.md](croisement-de-sources.md)), à la
tête `f78a97e`. Il porte sur le catalogue métier et le catalogue par défaut.
Les deux commits suivants, C67 et C68, ont été **retirés** le jour de la
livraison : voir [releve-de-livraison.md](releve-de-livraison.md) et §4.

**Ce qui est éprouvé sur la machine en service** — HTTPS de bout en bout,
anti-force brute par adresse derrière le mandataire, arrêt et relance de la
pile, rotation des sauvegardes — est relevé au 16 septembre 2026 dans
[EXPLOITATION §Ce qui a été éprouvé](EXPLOITATION.md).

---

## 3. Les limites connues

Elles sont dites franchement.
Aucune n'est une supposition : chacune a été mesurée, et le document qui la
porte est nommé.

### Ce que l'agent peut rendre de faux

- **Une grandeur demandée qui n'existe pas est remplacée par une voisine, en
  silence.** Relevé le 24 septembre 2026, catalogue métier, fil lié à `ventes` :
  « Montre-moi un diagramme en barres du nombre de commandes **par région** »
  rend « un diagramme en barres illustrant le nombre de commandes **par
  ville** … Lyon avec 21 commandes, Strasbourg 18 ». `ventes` ne porte aucune
  région — ses clients ont une `ville`, et rien d'autre.
  C'est la forme la plus difficile à repérer : les chiffres sont justes, le
  graphique est propre, et seule la grandeur a changé. L'utilisateur qui a
  demandé des régions lit des villes sans qu'un mot l'en avertisse.
  Le bon comportement serait de **dire** que la colonne demandée n'existe pas,
  et de nommer celles qui existent — ce que le chemin de prédiction fait déjà
  pour une feature déclarée introuvable.
- **Un « Top 5 » là où la question demande chaque produit.**
  Le code d'analyse imprime un extrait, et la ligne attendue n'y est pas.
  La réparation est dans la règle 2 de `prompts/analysis.txt` ; elle rend dues
  les cinq campagnes qui passent par l'analyse, et n'est pas faite.
  ([croisement-de-sources.md, C59](croisement-de-sources.md))
- **Une question sur trois sources à la fois perd le filtre des annulées.**
  `fabrique-vendu-stock` rend 131 là où l'oracle dit 125 : le filtre est perdu
  sur la troisième source.
  ([croisement-de-sources.md, C66](croisement-de-sources.md))
- **Le contrôle du SQL se tait sur une somme écrite hors d'un `WITH`.**
  Une table que le module ne reconnaît pas dans le schéma — le nom d'un `WITH`
  en est une — le fait rendre « je ne sais pas » plutôt qu'un avis.
  C'est un silence délibéré : un faux positif coûterait plus cher.
  Il est compté (`Lecture.illisibles`).
  ([`agents/retrieval/verification.py`](../src/data_analyst_agent/agents/retrieval/verification.py))

### Ce que l'agent ne regarde pas

- **En conversation neuve, certaines questions croisées ne lisent aucune
  donnée.** `vend-plus-quon-produit` et `vendus-sans-fabriquer` repartent en
  inventaire : le planificateur rend une source vide, et le croisement n'est
  jamais atteint. Sur un fil déjà lié à une source, les mêmes questions
  passent.
  ([croisement-de-sources.md, C66](croisement-de-sources.md))
- **`choix-entre-deux-sources` est bistable.** « titanic ou iris ? » tombe
  rouge une passe sur deux, sans rien changer d'autre.
  ([surface-conversationnelle.md §26.4](surface-conversationnelle.md))

### Ce que la mémoire ne porte pas

- **Une conversation liée à une source ne répond pas sur une autre, même
  quand la question la désigne.** Sur un fil lié à `ventes`, « Combien d'arrêts
  machine avons-nous eus en 2025 ? » reçoit « je n'ai pas accès » : la réponse
  attendue est 70, et `production` la porte. Le planificateur voit bien
  `production` ; la source du fil est reposée par-dessus.
  C'est la limite qu'une réparation avait levée (C67) et que son retrait
  restaure — voir §4, première étape.
  ([`orchestrator/graph.py`, `_relire_sans_la_source_du_fil`](../src/data_analyst_agent/orchestrator/graph.py))
- **Le contexte conversationnel ne retient qu'UN tour.** « et pour les
  femmes ? » marche ; deux tours en arrière est oublié. Le magasin d'artefacts,
  lui, porte aussi loin que le fil.
  ([ARCHITECTURE §8](ARCHITECTURE.md#8-limites-connues-et-pistes-v2))

### Les graphiques

- **La figure sort souvent au troisième et dernier essai.** La marge est
  nulle : un échec de plus, et la question ne rend rien.
  ([sources-metier.md](sources-metier.md))
- **`tabulate` manque à l'image du bac à sable.** `DataFrame.to_markdown()` est
  une tournure naturelle, pandas est annoncé au modèle, et la méthode meurt.
  Le paquet est **proposé** dans
  [`requirements.in`](../src/data_analyst_agent/sandbox/image/requirements.in),
  pas installé : l'ajouter demande de recompiler et reconstruire l'image du bac
  à sable, donc une décision d'exploitation.

### Ce qui n'a jamais été traité

- **Aucun téléchargement** : ni un tableau, ni une figure. Rien ne sort de la
  page de chat en fichier.
- **Aucun ajout de source depuis l'écran.** Une source se déclare dans
  `sources/catalogue.yaml`, à la main, puis le service redémarre.
- **Aucune compaction du contexte.** Ce qui déborde est **évincé** — les plus
  anciens d'abord — et la coupe est annoncée ; rien n'est résumé.
- **Aucun index par le sens.** Aucun embedding, aucune base vectorielle : le
  catalogue et les schémas sont lus tels quels.
- **Aucune mémoire entre conversations.** Chaque fil repart de zéro. Ce qu'un
  fil a appris ne sert pas au suivant.
- **Aucun fichier `LICENSE`.** Le README annonce MIT et `pyproject.toml` ne
  déclare rien : l'annonce est sans portée juridique en l'état.

### Exploitation

- **Le redémarrage de la machine n'a jamais été éprouvé.** L'unité systemd est
  `enabled`, et la pile s'arrête et repart d'un `systemctl stop`/`start` — ce
  que systemd fait au démarrage. Le reboot lui-même reste à faire, sur une
  fenêtre convenue : la machine héberge aussi d'autres services.
- **L'accès depuis un autre poste n'a pas été joué.** Les mesures viennent de
  la machine et de conteneurs posés sur son réseau.
- **La migration des conversations antérieures au cloisonnement par
  utilisateur n'a jamais été exécutée pour de vrai.** Le script est écrit et
  testé sur copie.
- **Aucune donnée client.** Toutes les sources livrées et mesurées sont
  synthétiques : aucune donnée personnelle, aucune entreprise existante.

La dette technique **ancrée `fichier:ligne`**, avec sa correction proposée, est
dans [docs/axes-amelioration.md](axes-amelioration.md). Ce document-ci en est le
résumé pour un lecteur qui reçoit le produit ; celui-là est fait pour celui qui
va y toucher.

---

## 4. Les prochaines étapes

Classées par ce qu'elles apportent à **l'utilisateur**, pas par difficulté.

### 1. Enrichir le périmètre d'une conversation liée

C'est la première, parce que c'est la seule où l'utilisateur voit l'agent
refuser une réponse qu'il a sous la main.

Sur un fil lié à `ventes`, « Combien d'arrêts machine avons-nous eus en 2025 ? »
reçoit « je n'ai pas accès ». La réponse est 70, et `production` la porte.

**Cela a été écrit, mesuré et retiré le jour de la livraison** (C67 et C68,
retirées à C72), et la cause du retrait est la condition à remplir avant de
recommencer. Sur le catalogue de démonstration, l'enrichissement montait un
croisement pour des questions qui n'en demandent aucun ; le croisement est
tronqué à 10 000 lignes par source, et les sommes sortaient donc fausses —
**663 504,99 kWh servis au lieu de 1 757 519,23**, sans qu'un mot de la réponse
ne le dise. Le parcours de démonstration en formulation courte tombait de 16/16
à 11/16, et le classement sans lexique de 3/3 à 0/3.

Deux conditions avant de la remettre :

- **ne croiser que si la question porte sur les DEUX sources.** L'enrichissement
  retiré montait le périmètre dès que la relecture nommait une autre source, sans
  vérifier que la question demandait les deux.
- **mesurer le parcours de démonstration** (`scripts/mesure_parcours_de_demonstration.py`
  sur `sources/demonstration/`, trois formulations) avant de conclure. C67
  n'avait été mesurée que sur le catalogue métier : c'est ce trou-là qui a laissé
  passer les sommes fausses.

### 2. Rendre les chiffres justes là où ils sont encore faux

C'est la seule famille où l'utilisateur reçoit une réponse **fausse et
plausible**, donc invisible.

- Faire imprimer **toutes** les lignes demandées, et non un extrait
  (règle 2 de `prompts/analysis.txt`, plus les cinq campagnes dues).
- Fermer le cas à trois sources, où le filtre des annulées est perdu sur la
  troisième.
- Lire les sommes qu'un `WITH` porte, pour retirer le dernier silence du
  contrôle SQL.
- **Refuser une grandeur qui n'existe pas au lieu de lui substituer une
  voisine** : nommer la colonne absente, et celles qui sont là. La propriété se
  lit dans le schéma, pas dans la question — comme celles qui gardent déjà le
  palmarès et les sommes.

### 3. Donner à l'utilisateur ce qu'il a produit

Il voit ses tableaux et ses figures, il ne peut pas les emporter.

- Le **téléchargement** d'un tableau (CSV) et d'une figure (PNG) depuis la page
  de chat. Les deux vivent déjà sur le disque du fil. La route
  `/conversations/{id}/artefacts/{nom}` rend le code d'une figure et la **tête**
  d'un tableau, en JSON : elle ne rend ni le fichier entier, ni l'image. Il
  manque donc une route de fichier, et le bouton qui l'appelle.

### 4. Rendre le produit configurable sans le réinstaller

- **Ajouter une source depuis l'écran**, plutôt que par un fichier YAML et un
  redémarrage.
- **Surcharger les prompts par déploiement** : ils sont sortis du code mais
  restent dans le paquet, et adapter un cas d'usage impose aujourd'hui de
  réinstaller.

### 5. Faire durer une conversation plus d'un tour

- Élargir le **contexte conversationnel** au-delà du tour précédent, ou le
  compacter plutôt que de l'évincer.
- **Mémoriser les appels d'outils qui ont abouti**, et les proposer sur les
  questions voisines — la piste décrite en
  [ARCHITECTURE §8](ARCHITECTURE.md#8-limites-connues-et-pistes-v2).

### 6. Mettre l'exploitation au clair

- **Rejouer le reboot** de la machine sur une fenêtre convenue.
- **Ouvrir l'accès depuis un poste**, autorité locale installée dans un vrai
  navigateur.
- **Exécuter la migration** des conversations existantes.
- **Poser le fichier `LICENSE`** et le déclarer dans `pyproject.toml`.
- Ajouter `tabulate` à l'image du bac à sable — un échec banal en moins, sans
  marge gagnée pour autant.

### 7. Tenir la charge, si l'échelle change

Rien de ceci ne se manifeste à l'échelle visée (10-20 utilisateurs). À rouvrir
si elle change : budget de tokens sur les autres agents que le planificateur,
`resolve()` qui réécrit les sessions à chaque requête, `list()` en O(n) sur
tous les fils, argon2 face au pool de threads.
Le détail et les mesures sont dans
[axes-amelioration.md](axes-amelioration.md#récapitulatif-priorisé).

---

## 5. Par où entrer dans la documentation

| Si vous voulez… | Lisez |
|---|---|
| installer le service sur une machine nue | [INSTALLATION.md](INSTALLATION.md) |
| commander, exposer, sauvegarder, restaurer | [EXPLOITATION.md](EXPLOITATION.md) |
| comprendre comment c'est construit | [ARCHITECTURE.md](ARCHITECTURE.md) |
| comprendre **comment il répond**, tour par tour | [parcours-de-l-agent.md](parcours-de-l-agent.md) |
| déclarer vos propres sources | [rediger-un-dictionnaire-de-source.md](rediger-un-dictionnaire-de-source.md) |
| savoir ce qui reste à faire, ancré dans le code | [axes-amelioration.md](axes-amelioration.md) |
| les chiffres du jour de livraison | [releve-de-livraison.md](releve-de-livraison.md) |
