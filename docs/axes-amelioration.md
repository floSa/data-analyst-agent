# Axes d'amélioration — data-analyst-agent

> **Ce document est vivant** : la dette technique ouverte, ancrée `fichier:ligne`, pour qui va toucher au code. Le résumé pour qui reçoit le produit est dans [LIVRAISON.md §3](LIVRAISON.md#3-les-limites-connues).

Chaque point est ancré dans le code (`fichier:ligne`) avec une correction proposée.

**Ce document s'adresse à qui va toucher au code.** Le même état des lieux, écrit
pour qui **reçoit** le produit — les limites en langage ordinaire et les prochaines
étapes classées par ce qu'elles apportent à l'utilisateur — est dans
[LIVRAISON.md](LIVRAISON.md). Les deux ne font pas doublon : celui-ci porte les
ancres, les constats et les corrections proposées ; celui-là porte l'ordre dans
lequel les reprendre.

Ce document recense ce qui a été **vu, mesuré et délibérément laissé** pendant le
chantier de durcissement de septembre 2026. Les dix-sept tâches de
[l'audit](AUDIT-2026-09.md) sont traitées ; ce qui suit est ce qui a été relevé en
chemin sans entrer dans leur périmètre. Aucun point n'est spéculatif : chacun a été
constaté sur le code ou mesuré à l'exécution.

Les points **corrigés depuis** gardent leur entrée, avec ce qui a été mesuré avant et
après et pourquoi la correction a atterri là plutôt qu'à l'endroit d'abord proposé :
c'est la partie qu'on ne retrouve pas dans un diff.

---

## Sécurité

### L'anti-force brute compte par adresse, et l'adresse disparaît derrière un frontal

- **Où** : [`api/app.py:430`](../src/data_analyst_agent/api/app.py),
  [`api/forwarded.py`](../src/data_analyst_agent/api/forwarded.py)
- **Constat, à l'époque** :
  ```python
  adresse = request.client.host if request.client else "inconnue"
  ```
- **Problème** : derrière un reverse proxy, `request.client.host` est l'adresse du
  proxy — la même pour tout le monde. Cinq échecs de connexion, de qui que ce soit,
  verrouillent alors **tous** les visiteurs. `X-Forwarded-For` n'était délibérément
  pas lu : il est forgeable par le client, et le croire aveuglément serait pire que
  le défaut d'alors.
- **Correction proposée** : un réglage de proxys de confiance (`DAA_TRUSTED_PROXIES`),
  et ne lire `X-Forwarded-For` que si la connexion vient de l'un d'eux.
- **Statut** : **Corrigé.** `api/forwarded.py` porte la règle : on ne lit ces
  en-têtes que si le pair immédiat est un mandataire déclaré, et l'on retient la
  première adresse qui n'en est pas un, **en remontant depuis la droite** — le
  mandataire le plus proche écrit en dernier, ce que le client s'est inventé reste
  donc à gauche et n'est jamais atteint. `trusted_proxies` vide (le défaut) : rien
  n'est cru, on rend le pair. Le middleware appelle `adresse_client(request,
  app.state.trusted_proxies)`, et `schema_client` suit la même règle pour
  `X-Forwarded-Proto`. **Éprouvé le 16 septembre 2026** sur la machine en service,
  depuis deux adresses distinctes et avec une tentative d'usurpation :
  [EXPLOITATION.md](EXPLOITATION.md), « L'anti-force brute, depuis deux adresses
  distinctes ».

### Le plafond de corps ne voit pas une requête en `chunked`

- **Où** : [`api/app.py:394`](../src/data_analyst_agent/api/app.py)
- **Constat** :
  ```python
  annonce = request.headers.get("content-length", "")
  if annonce.isdigit() and int(annonce) > reglages.api_max_body_bytes:
  ```
- **Problème** : une requête en `Transfer-Encoding: chunked` n'annonce pas de
  `Content-Length` et passe le contrôle. Elle est ensuite bornée par
  `chat_message_max_chars`, mais après lecture et analyse du corps.
- **Correction proposée** : le plafond dur appartient au serveur en frontal
  (`client_max_body_size` chez nginx, équivalent ailleurs). À défaut, compter les
  octets au fil de la lecture du flux.
- **Statut** : Ouvert, assumé — documenté dans la docstring du middleware.

### La trace rend le détail technique au porteur d'une session

- **Où** : [`orchestrator/graph.py`](../src/data_analyst_agent/orchestrator/graph.py),
  garde-fou `_guarded`
- **Problème** : la réponse utilisateur est normalisée (une phrase + une référence
  d'incident), mais la **trace** renvoyée au client authentifié porte toujours la cause
  complète. C'est conforme à ce qui était demandé — « détail dans la trace et les
  journaux » — mais ce n'est pas un masquage vis-à-vis d'un compte légitime.
- **Correction proposée** : réserver la trace détaillée à un rôle d'administration, une
  fois qu'une notion de rôle existera.
- **Statut** : Ouvert — dépend d'une notion de rôle, absente aujourd'hui.

---

## Correctness

### Les questions SUR le système n'avaient aucune route — dont « De quels attributs as-tu besoin ? »

- **Où** : [`orchestrator/introspection.py`](../src/data_analyst_agent/orchestrator/introspection.py)
  et `Orchestrator._court_circuit_meta` / `_system_node`
  ([`orchestrator/graph.py`](../src/data_analyst_agent/orchestrator/graph.py))
- **Problème** : la question tombait dans le repli « je n'ai pas bien compris », alors que
  la liste des features est **déjà** dans le prompt du planificateur. La cause n'était pas
  le prompt : `Capability` n'avait pas de valeur pour « décris-moi le modèle », donc la
  demande ne pouvait littéralement pas être routée.
- **Ce que la mesure a montré** : le cas n'était pas isolé. Sur vingt-et-une questions
  méta posées au vrai système, **huit tombaient dans le repli et quatre à côté** — les
  familles « capacités » (« que sais-tu faire ? ») et « features non qualifiées » étaient
  à zéro. Tableau complet, avant et après :
  [surface-conversationnelle.md](surface-conversationnelle.md).
- **Correction retenue** : un module `introspection.py` qui construit la réponse depuis
  les **sources de vérité** — catalogue, registre, `SCHEMAS`/`describe_features`,
  ontologie de la source, et son dictionnaire quand elle en déclare un — et un nœud
  `system` du graphe, atteint par un **court-circuit placé avant l'appel au
  planificateur**. Cinq sujets : `sources`, `schema`, `modeles`, `features`, `capacites`.
- **Pourquoi pas dans `_regle_choisir_le_modele`**, comme cette entrée le proposait : les
  règles de `_REGLES_DU_PLAN` ajustent un `Plan` que le LLM a **déjà** rendu. L'élargir
  aurait gardé l'aller-retour, n'aurait rattrapé que les demandes déjà classées en
  `predict` — pas le cas constaté, où le planificateur ne classe rien — et aurait fait
  répondre à des questions une règle dont le nom dit « choisir le modèle ». Il fallait
  une étape **avant** les règles, pas une règle de plus.
- **Résultat mesuré** : 21/21 correctes, **zéro repli**, et **9 appels LLM au lieu de 43**
  — dix-sept des vingt-et-une questions se répondent sans le moindre aller-retour, la
  réponse étant entièrement déterminée par la configuration.
- **Puis rouvert, et re-corrigé.** Le 21/21 mesurait le lexique contre la batterie qui
  avait servi à l'écrire. Sur **quinze reformulations naturelles** de la même famille,
  relevées par le propriétaire en usage réel, il n'en attrapait que trois — « c'est quoi
  ton périmètre ? », « tu bosses sur quoi ? », « montre-moi ce que tu as » partaient au
  planificateur, classées `query`, et recevaient du SQL ou le repli. Un lexique de
  tournures est une liste ; la famille « parle-moi de toi » est ouverte.
- **Correction retenue** : le lexique est retiré, et c'est le **modèle** qui reconnaît la
  question — un agent à cinq **outils** (`orchestrator/systeme.py`) qui rendent les faits
  du dépôt, l'appel d'un outil valant signal de routage. Le déterministe n'est pas jeté :
  il est devenu la matière que le modèle formule, **et** la ceinture servie quand sa
  formulation invente un nom ou en omet un (`defaut_de_fondation`).
- **Résultat mesuré, seconde fois** : **36/36** sur la batterie élargie (contre 24/36),
  **7/7** sur les sept formulations du constat (contre 0/7), et **30 des 36 réponses
  formulées par le modèle** — c'était l'autre moitié du grief, « ce n'est même pas le LLM
  qui répond ». Coût assumé : **un aller-retour de plus sur chaque question, y compris
  celles sur les données**.
- **Statut** : **Corrigé** (deux fois). Détail et rejeux :
  [surface-conversationnelle.md](surface-conversationnelle.md) §9 à §11.

### Le plafond d'allers-retours de l'agent système était serré

- **Où** : [`config.py`](../src/data_analyst_agent/config.py), `systeme_request_limit` ;
  [`orchestrator/systeme.py`](../src/data_analyst_agent/orchestrator/systeme.py),
  `run_systeme`.
- **Constat** : une question méta sur trente-six — « sur quoi je peux travailler ? » —
  pouvait épuiser le plafond (`request_limit of 4`) sans qu'aucune erreur soit levée,
  et retomber dans le repli du planificateur. Signalé aux §15.7, §18 et §19.11 de la
  surface conversationnelle, jamais traité : le plafond est en tête de *chaque* tour,
  et le monter au jugé se paie sur toutes les questions.
- **Ce que la mesure a montré, et qui n'était pas prévu** : ce n'est pas une boucle.
  La question ouvre sur tous les sujets à la fois, et peut demander capacités,
  sources, schéma et modèles avant de formuler, soit six allers-retours. Sondée seule
  à 6, 8 puis 12, elle coûte 6 à chaque fois : le chemin converge. Un plafond plus
  haut n'aurait donc rien laissé filer.
- **Mesuré** — batterie complète, 36 questions méta et 4 témoins
  (`scripts/mesure_surface_conversationnelle.py`) :

  | Plafond | Questions méta | Appels LLM |
  |---|---|---|
  | 4 | 36/36 | 78 |
  | 5 | 36/36 | 78 |
  | **6** | **36/36** | **78** |

  Témoins à 4/4 et 17 appels dans les trois exécutions.
- **Corrigé** (C26) : `systeme_request_limit = 6`. La marge est **gratuite** : même
  total à 4, 5 et 6, et pas une seule question dont le coût bouge. Un plafond n'est
  pas un budget dépensé, c'est un budget disponible, et seule la question qui en a
  besoin le touche. L'atteindre coûte d'ailleurs *plus* cher que de réussir :
  l'échec ajoute le tour du planificateur et celui de la synthèse — d'où
  les 84 appels du plafond 5, le plus cher et le moins bon des trois.
- **Statut** : **Traité** (C26, 2026-09-15).

### La capacité système n'est pas dans `Capability`, et ne doit pas y revenir sans mesure

- **Où** : [`orchestrator/plan.py`](../src/data_analyst_agent/orchestrator/plan.py)
- **Constat** : le réflexe naturel — ajouter `"describe_system"` au `Literal` et le
  décrire dans le prompt — a été essayé et **retiré, deux fois, pour deux raisons
  distinctes** :
  1. annoncé dans le prompt, il coûtait **+134 tokens à chaque requête** et déplaçait
     **quatre questions d'une bonne réponse vers une mauvaise, aucune dans l'autre sens** :
     « sur quelle période portent les données ? » routée en `describe_system` alors que la
     réponse est un `SELECT` ;
  2. laissé dans le seul `Literal`, prompt inchangé, le modèle continuait de le choisir —
     **`Capability` *est* le JSON Schema de la sortie structurée**, lu indépendamment du
     prompt — et son élargissement dégradait l'extraction d'une capacité voisine : `pcass`
     au lieu de `pclass`, donc une relance au lieu d'une prédiction. Reproductible dans
     les deux sens.
- **Ce qui en découle** : le graphe route sur `system_topic` posé dans le state, et non
  sur `plan.capability`. `ChatAnswer.plan` reste donc **vide** pour une question méta —
  exact plutôt qu'incomplet : il n'y a pas eu de planification. Le contrat que lit le
  modèle est inchangé au caractère près, ce qui garantit par construction qu'aucun chemin
  existant n'est affecté.
- **Statut** : Fermé par décision, verrouillé par un test
  (`test_le_contrat_de_sortie_du_llm_reste_a_quatre_capacites`). À rouvrir seulement avec
  une mesure sur un modèle plus solide. Le passage au routage par le modèle (§9 à §11) n'y
  a rien changé : **un outil n'est pas une capacité**, il ne touche ni le `Literal` ni le
  prompt du planificateur, qui n'a pas bougé d'un caractère.

### La source choisie par l'utilisateur était perdue au tour suivant

- **Où** : `Orchestrator._regle_choisir_la_source` et
  [`orchestrator/conversations.py`](../src/data_analyst_agent/orchestrator/conversations.py)
- **Problème** : le catalogue déclare plusieurs sources et le planificateur en devinait
  une **à chaque tour**, sur leurs descriptions. Quand il n'y parvenait pas, une règle
  posait la question — « Sur quelle source veux-tu travailler : titanic, iris ? » — deux
  noms nus, sans un mot de contexte, et **la réponse n'était retenue nulle part** : le
  tour suivant reposait la même question, ou repartait sur une devinette.
- **Correction** : la proposition rend ce que le catalogue dit de chaque source ; la
  réponse est reconnue **par du code** (le nom d'une source, et le fait que le message ne
  dise presque rien d'autre) et **liée à la conversation**, persistée dans
  `transcript.json` comme `owner`. Le planificateur la reçoit dans son contexte et une
  règle la repose au plan. Une source nommée en cours de route **bascule**, et l'avis
  part en tête de la réponse — le danger n'est pas de changer de source, c'est de changer
  sans le dire.
- **Ce que la mesure de bout en bout a montré**, et que la suite unitaire ne pouvait pas
  voir : la reconnaissance dépendait d'abord d'un drapeau posé au tour précédent, et le
  parcours réel l'a mise en défaut deux fois — un « titanic » de validation s'est fait
  rendre l'inventaire du catalogue, et un « et dans iris, combien de lignes ? » a perdu sa
  question. Un choix de source se lit dans le message, pas dans l'histoire ; le drapeau a
  été retiré. Parcours mesuré :
  [surface-conversationnelle.md](surface-conversationnelle.md) §12.
- **Compatibilité** : aucune migration. Une transcription antérieure au champ le reçoit à
  sa valeur par défaut — vide — et le fil se comporte comme avant.
- **Statut** : **Corrigé.**

### Le repli citait les sources en dur

- **Où** : `Orchestrator._repli_du_planificateur`
  ([`orchestrator/graph.py`](../src/data_analyst_agent/orchestrator/graph.py))
- **Problème** : le message déclarait ne pas comprendre **en nommant les sources**
  (« interroger une source (titanic, iris…) ») — l'information était donc disponible à
  l'instant même où le système disait ne pas l'avoir. Et elle était **recopiée dans la
  chaîne**, donc fausse dès qu'un déploiement change de catalogue.
- **Correction** : le repli rend l'inventaire réel, lu dans le catalogue et le registre
  (`introspection.inventaire`), et **finit** par la question au lieu de commencer par
  elle — ce qu'on lit en dernier est ce à quoi on répond.
- **Statut** : **Corrigé.**

### La correspondance déclarée au catalogue n'était pas relue contre la source

- **Où** : [`agents/inference/correspondance.py`](../src/data_analyst_agent/agents/inference/correspondance.py),
  `Correspondance.declaree` ; [`orchestrator/graph.py`](../src/data_analyst_agent/orchestrator/graph.py),
  `_fetch_predict_node`.
- **Constat** : C23 a introduit le bloc `features: {dataset: {feature: colonne}}`
  déclaré par la source, et il a supprimé la devinette. Ce qu'il ne vérifiait pas,
  c'est que la colonne déclarée **existe** : la déclaration était relue contre le
  schéma de *features* (complète ? aucune feature inconnue ?) et jamais contre le
  schéma de la *source*.
- **Problème** : une déclaration est du texte dans un YAML. Un `classes.levelx`
  au lieu de `classes.level` partait en consigne SQL, et ce qui suivait dépendait
  de ce que le modèle voulait bien en faire.
- **Mesuré, avant correction**, sur la vraie base Postgres `titanic` et sous vLLM
  (confrontation neutralisée, question « Prédis la survie des cinq premiers
  passagers ») — **deux visages, et le premier est le pire** :

  | Déclaration fausse | Ce qui sort | Appels LLM | Requêtes sur la source |
  |---|---|---|---|
  | `classes.levelx` | « a survécu : 3 (60 %) » — une prédiction **juste**, 4 tirages sur 4 : l'agent SQL a silencieusement réparé la faute en `c.level` | 5 | 1 |
  | `classes.rang_du_billet` | « aucune des 5 lignes n'a passé la validation (`pclass` : reçu `'3e classe'`) », 2 tirages sur 2 : l'agent a choisi `c.label` | 5 | 1 |

  Le premier cas est celui qu'on n'avait pas vu : la déclaration fausse ne produit
  **aucun symptôme**. Le modèle devine la bonne colonne, ça marche, et la
  déclaration devient du texte mort que rien n'applique — exactement la devinette
  que C23 avait retirée, revenue par la porte de service. Le second est le symptôme
  du §19.11, et son message accuse les **données** (« reçu `'3e classe'` ») là où la
  faute est dans le catalogue.
- **Corrigé** (C26) : `Correspondance.confronter(schema)`, appelée dans
  `_fetch_predict_node` une fois la connexion ouverte et **avant la requête**. Le
  refus nomme la colonne introuvable, propose la colonne réelle qui lui ressemble
  (distance d'édition sur les noms **nus** des deux côtés — la table qualifiante
  gonfle la ressemblance de ce qui partage son préfixe et noie celle d'une
  déclaration nue ; muette si rien ne ressemble, une suggestion tirée au hasard
  coûterait la confiance qu'on gagne à ne rien deviner), liste les colonnes
  de la source et dit que rien n'a été interrogé. Une déclaration **qualifiée** est
  lue sur ses deux derniers segments et les deux doivent tomber juste :
  `classes.sex` est refusé comme `passengers.levelx`, parce que ce n'est pas cette
  table qui porte la colonne ; se rabattre sur le seul nom laisserait passer la
  moitié des fautes. Une déclaration **nue** se compare aux noms de colonnes de
  toutes les tables : c'est l'écriture de la source à une table, qui n'a aucune
  raison de se qualifier. Toutes les colonnes fautives sont dites
  d'un coup : rendre une faute à la fois ferait corriger le YAML trois fois de
  suite sans qu'aucune contrainte ne l'impose.
- **Mesuré, après correction**, mêmes questions et même base : les deux
  déclarations sont refusées avant la requête — **2 appels LLM** (le tour de
  routage, la synthèse de l'erreur) et **0 requête de données**, contre 5 et 1. Le
  message nomme `classes.levelx`, propose `classes.level`, liste les treize
  colonnes de la source et dit que rien n'a été interrogé. Pour
  `classes.rang_du_billet`, aucune proposition : rien ne lui ressemble, et se taire
  vaut mieux que suggérer au hasard.
- **Ce qu'elle coûte** : une lecture du schéma, et rien d'autre — 27,7 ms sur la base
  Postgres `titanic` (médiane de sept, valeurs distinctes des colonnes texte
  comprises), 1,8 ms sur le CSV `iris` ; la confrontation elle-même pèse ~20 µs.
  Aucun appel au modèle, aucune requête de données. C'est une lecture que l'agent
  SQL fait de toute façon à son premier outil : le cas fautif l'économise, le cas
  juste la paie deux fois.
- **Ce qui a été délimité** : la confrontation ne porte que sur la correspondance
  **déclarée**. Un tableau du tour précédent réinjecté sous `resultat_1` ne déclare
  rien — ses colonnes sont rapprochées par leur nom, et une feature qu'aucune ne
  porte doit rester réclamée par le schéma. La confronter aurait transformé un
  chaînage qui marche en faute de catalogue à corriger dans un fichier qui n'existe
  pas.
- **Ce que la correction a démenti** : deux tests supposaient une déclaration
  impossible — un `classes.level` déclaré sur un CSV à plat, et une identité
  complète déclarée sur une source à deux colonnes. Aucun des deux ne pouvait
  arriver en vrai, et tous deux passaient. Ils mesurent maintenant ce qu'ils
  prétendaient mesurer : la consigne SQL sur une colonne qui existe vraiment, et
  une ligne incomplète parce que la **requête** n'a ramené qu'une colonne des sept
  demandées — pas parce que la source ne les porte pas.
- **Statut** : **Traité** (C26, 2026-09-15). Relevé en mesurant C23, hors de son
  périmètre.

### Le code d'une analyse n'était pas un artefact, et ne survivait pas un tour

- **Où** : [`orchestrator/workspace.py:566`](../src/data_analyst_agent/orchestrator/workspace.py)
  (`last_code_for`), [`orchestrator/rappel.py`](../src/data_analyst_agent/orchestrator/rappel.py)
- **Constat** : la mémoire d'une conversation persistait les **tableaux** ; le code
  d'analyse, lui, vivait dans un unique `ConversationContext.last_code` **écrasé à
  chaque tour**, et n'était réutilisable que si la source du tour suivant était la
  même (`last_code_for`). Les figures n'étaient pas persistées du tout.
- **Problème** : « mets les barres en bleu » juste après un graphique marchait ; la
  même phrase **deux tours plus tard** ne trouvait plus rien, et rien ne le disait —
  le planificateur fabriquait une figure neuve, correcte, présentée comme une
  reprise. C'est le pire des trois cas : ni erreur, ni refus, une réponse fausse qui
  ressemble à la bonne.
- **Corrigé** (C24) : un **magasin unique à trois natures** (`table`, `code`,
  `figure`), chaque objet nommé, décrit et persisté au manifeste ; un nœud `rappel`
  à deux outils (`lire_un_artefact`, `rejouer_un_code`) placé entre le nœud système
  et le planificateur ; une fenêtre de contexte propre au code
  (`DAA_CONTEXT_CODE_WINDOW`), distincte de celle des tableaux — les faire partager
  une fenêtre de huit ferait évincer la figure du tour 1 au bout de quatre tours qui
  produisent chacun un tableau, c'est-à-dire exactement le défaut qu'on corrige.
- **Mesuré** sur `scripts/mesure_rappel_dartefact.py`, huit tours, `titanic` en
  Postgres, figures réellement exécutées en bac à sable :
  - tour 4, « reprends le graphe de tout à l'heure et mets les barres en bleu »
    **deux questions sans rapport après la figure** — le cas que `last_code`, écrasé
    deux fois, ne pouvait pas servir : `rejeu de graphique_1` ;
  - tour 7, « reviens au tout premier graphique » **cinq tours et une autre figure
    plus loin**, donc un catalogue où deux `graphique_N` se disputent la
    désignation : `graphique_1` est choisi ;
  - le coût du catalogue dans le prompt : **1 439 tokens** au tour 1, magasin vide,
    **1 911 au tour 8** avec sept artefacts — *+472 tokens pour sept objets*, tokens
    rendus par le serveur et non estimés. Le contenu n'entre jamais dans le prompt.
- **Ce que la mesure a trouvé en chemin, et qui a été corrigé ensuite** (C25) : le
  tour 8 — « remontre-moi le camembert des ports que tu avais fait », dans un fil qui
  n'en porte aucun — échouait. Aucun outil n'est appelé, donc
  aucun refus ne part, et la figure neuve passe pour un rappel. La désignation d'un
  artefact passé se lit désormais **dans le message** (grammaire) et l'absence
  **dans le catalogue**, hors du modèle ; quand les deux se rencontrent, l'aveu est
  mis en tête et le tour continue.
- **Ce qui reste ouvert** : le tour 5 (« le premier tableau que tu m'as sorti, redis-moi
  ce qu'il y avait dedans ») peut être capté par le nœud **système** au lieu du nœud
  de rappel — instruit en
  [surface-conversationnelle §20.10](surface-conversationnelle.md).
- **Statut** : **Traité** (C24 puis C25, 2026-09-15). Détail :
  [ARCHITECTURE §4.12](ARCHITECTURE.md#412-les-artefacts-nommés-dune-conversation--relire-rejouer),
  [surface-conversationnelle §20 et §21](surface-conversationnelle.md).

### Deux politiques différentes face à un fichier corrompu

- **Où** : [`orchestrator/workspace.py:582`](../src/data_analyst_agent/orchestrator/workspace.py)
  (`_load`) contre [`orchestrator/conversations.py:161`](../src/data_analyst_agent/orchestrator/conversations.py) (`load`)
- **Problème** : `ConversationStore.load()` tolère un fichier illisible et rend `None` ;
  `_load()` du manifeste ne le tolère pas et lève. Depuis le passage aux écritures
  atomiques, la corruption ne peut plus venir d'une interruption — mais elle peut venir
  d'un disque, d'une restauration partielle ou d'une édition à la main.
- **Correction proposée** : aligner sur la politique tolérante, et signaler la perte
  plutôt que de la taire.
- **Statut** : Ouvert.

---

## Performance

### Le relevé d'une source n'était borné ni en temps, ni dans la durée

- **Où** : [`agents/retrieval/faits.py`](../src/data_analyst_agent/agents/retrieval/faits.py),
  `RelevesDuCatalogue`, appelé au premier inventaire de la session.
- **Constat** : décrire une source, c'est un `count(*)` par table plus un `min`/`max`
  sur sa colonne de date. Aucun délai maximal, aucune limite de volume, et un relevé
  gardé jusqu'au redémarrage.
- **Problème n°1, le relevé n'était jamais rafraîchi.** Observé en vrai le
  2026-09-14 : Postgres arrêté au démarrage, la réponse de liaison annonçait
  « volumétrie non relevée » et continuait de l'annoncer alors que la base répondait
  de nouveau depuis plusieurs minutes.
- **Problème n°2, le relevé n'était pas borné en temps.** Une source **muette** — dont
  le TCP part et ne revient jamais — retenait l'inventaire entier sans plafond
  (mesuré : toujours bloqué au bout de 75 s, le temps du délai TCP du système).
- **Corrigé** (C18) par quatre bornes réglables (`DAA_RELEVE_*`, cf.
  [ARCHITECTURE §7](ARCHITECTURE.md#7-configuration-daa_)) :
  une **reprise** courte pour une source injoignable (30 s), une **péremption** du
  relevé réussi (15 min), un **délai maximal** par source (10 s) qui dégrade la ligne
  en injoignable avec sa raison, et une **estimation** du moteur (`reltuples`)
  au-dessus de 100 000 lignes plutôt qu'un `count(*)` exact — le chiffre s'affiche
  alors avec un `~`, jamais fondu dans un total présenté comme exact. Le délai est
  tenu par un fil démon : `concurrent.futures` joint ses fils à la sortie de
  l'interpréteur, et le process aurait refusé de s'arrêter tant que la source n'a pas
  répondu. Tout cela est rejouable — `scripts/mesure_releve_des_sources.py` monte les
  trois bancs et joue chaque défaut avant/après dans la même exécution.
- **Ce que la mesure a démenti** : le coût redouté du premier inventaire sur la grosse
  base. Sur la base DuckDB de 1,66 M de lignes et dix tables, il tient en **83,5 ms**
  (médiane de neuf relevés, cache disque vidé), comptages compris — DuckDB répond
  `count(*)` depuis ses métadonnées, et les dix comptages pèsent 2,7 ms à eux tous.
  Le bornage le porte à 95,0 ms, soit ~11 ms pour le fil qui tient le délai.
  **Conséquence sur l'approximation** : elle n'est appliquée qu'à Postgres, qui balaie
  vraiment (88 ms contre 1,5 ms de `reltuples`). L'appliquer à DuckDB coûtait *plus*
  cher que compter (27,6 ms contre 13,9 ms) pour un chiffre moins sûr — c'était
  dégrader sans contrepartie. Ce n'est donc pas elle qui justifie le bornage : c'est
  la source qui ne répond pas.
- **Statut** : **Traité** (C18, 2026-09-14). Relevé en mesurant C15, hors de son
  périmètre.

### La période affichée est celle de la première colonne de date, sans choix

- **Où** : [`agents/retrieval/faits.py`](../src/data_analyst_agent/agents/retrieval/faits.py),
  `_colonne_de_date`.
- **Problème** : une source qui porte plusieurs colonnes de date — date de commande,
  date de livraison — voit sa période lue sur la première rencontrée. La colonne est
  nommée dans la réponse, donc rien n'est faux ; mais rien ne dit non plus que c'est
  celle qui compte pour le métier.
- **Correction proposée** : laisser le dictionnaire de la source désigner sa colonne
  de date de référence, et retomber sur la première à défaut.
- **Corrigé** (C26) — **pas au dictionnaire, au catalogue**. Le `dictionary` est un
  Markdown qui s'adresse au modèle de langage ; y désigner une colonne obligerait à
  lire de la prose pour prendre une décision de code, et une source qui n'en déclare
  aucun n'aurait nulle part où le dire. La désignation vit donc là où vit déjà
  `features`, pour la raison qui a fait naître `features` : ce champ s'adresse au
  code. Un champ `date_reference` **facultatif** sur la source — `table.colonne` ou
  `colonne` seule, insensible à la casse — et le défaut d'avant inchangé quand rien
  n'est désigné.
- **Ce qui a été ajouté en chemin** : une désignation que le schéma ne porte pas ne
  fait pas tomber le relevé — les tables, les lignes et une période restent bonnes,
  et un inventaire qui disparaît pour une faute de frappe dans un YAML serait une
  punition démesurée — mais elle **se dit**, avec les colonnes de date réelles.
  Sans ce message, le repli sur la première colonne serait indiscernable d'une
  source qui ne désigne rien : la correction se serait payée d'un nouveau silence.
- **Mesuré** sur `tests/catalogues/deux-dates/` — une source de 120 lignes portant
  `date_commande` **et** `date_livraison`, dont les intervalles diffèrent des deux
  côtés, et trois déclarations sur les mêmes octets
  (`uv run python scripts/mesure_releve_des_sources.py --seulement dates`) :

  | Déclaration | Période rendue |
  |---|---|
  | aucune | du 2024-02-11 au 2024-11-30 (`date_commande`) |
  | `date_reference: date_livraison` | du 2024-02-22 au 2025-01-07 (`date_livraison`) |
  | `date_reference: date_livraision` (faute de frappe) | du 2024-02-11 au 2024-11-30 (`date_commande`) **+ « colonne de date de référence déclarée introuvable »**, colonnes réelles nommées |
- **Statut** : **Traité** (C26, 2026-09-15).

### `list()` ouvre et valide tous les fils pour n'en rendre qu'un résumé

- **Où** : [`orchestrator/conversations.py:175`](../src/data_analyst_agent/orchestrator/conversations.py)
- **Constat** :
  ```python
  for dossier in self.base_dir.iterdir():
      conversation = self._read(dossier / self.TRANSCRIPT)
  ```
- **Problème** : coût linéaire en nombre de fils **et** en taille de chaque fil, à chaque
  affichage de la liste. Le cloisonnement par utilisateur a réduit le dénominateur, il ne
  l'a pas supprimé.
- **Correction proposée** : un fichier de résumé par conversation, écrit au même moment
  que le transcript avec les mêmes primitives atomiques.
- **Statut** : Ouvert.

### `fit_to_budget` est en O(n²) dans le cas dégénéré

- **Où** : [`orchestrator/workspace.py:485`](../src/data_analyst_agent/orchestrator/workspace.py)
- **Problème** : la boucle recalcule `describe()` en entier à chaque objet retiré.
  **Mesuré : 126 ms pour 1 000 objets**, fenêtre d'artefacts désactivée et budget serré.
  Avec la fenêtre par défaut (8), la boucle fait au plus huit tours et le cas ne se
  présente pas.
- **Correction proposée** : décompter le coût de chaque objet une fois, puis retrancher —
  au lieu de reconstruire la chaîne complète à chaque tour.
- **Statut** : Ouvert, sans impact aux réglages par défaut.

### Chaque requête authentifiée réécrit le fichier des sessions

- **Où** : [`auth/sessions.py:136`](../src/data_analyst_agent/auth/sessions.py)
- **Problème** : `resolve()` met à jour `last_seen_at` — c'est ce qui fait courir le délai
  d'inactivité depuis la dernière requête — au prix d'une écriture sous verrou exclusif à
  chaque appel. Volontairement simple pour une instance on-premise ; c'est le premier
  point à revoir si la charge monte.
- **Correction proposée** : ne réécrire que si `last_seen_at` a vieilli d'un seuil
  (une minute suffit à tenir un délai d'inactivité à l'heure).
- **Statut** : Ouvert, assumé — documenté dans la docstring.

### argon2 réserve 64 Mio par vérification, face à quarante threads

- **Où** : [`auth/accounts.py:113`](../src/data_analyst_agent/auth/accounts.py)
- **Problème** : les paramètres par défaut d'argon2id coûtent **64 Mio** par vérification.
  Le pool de threads de FastAPI en compte **40** : une rafale de connexions simultanées
  peut réserver ~2,5 Gio. Non plafonné.
- **Correction proposée** : un sémaphore sur la vérification de mot de passe, sur le
  modèle de celui posé sur les sessions sandbox.
- **Statut** : Ouvert.

---

## Dette et portabilité

### Le catalogue n'acceptait que `postgres` et `file`

- **Où** : [`agents/retrieval/catalog.py`](../src/data_analyst_agent/agents/retrieval/catalog.py)
- **Problème** : entre « un serveur Postgres » et « un fichier » manquait la forme qu'a
  n'importe quel gros jeu de données local — une base DuckDB (`.duckdb`). Passée par la
  porte `file`, elle aurait perdu ce qui en fait une base : un CSV et un classeur n'ont
  aucune contrainte à déclarer, donc un schéma en étoile serait arrivé au modèle **sans
  ses clés étrangères**, à charge pour lui de deviner les jointures.
- **Corrigé** (C18) : un troisième type `duckdb`, avec son adaptateur
  (`DuckDBAdapter.from_database`) — ouverture en lecture seule, pour que l'API et un
  notebook puissent ouvrir la même base sans se voler le verrou disque ; relecture des
  contraintes par `duckdb_constraints()` (clés primaires **et** étrangères) ; et le
  verrou d'accès à l'hôte posé dans `__init__`, seul point de passage commun aux deux
  portes — `read_only` protège la base, pas le disque autour.
- **Mesuré** : les trois types déclarés dans le même catalogue et exercés dans la même
  conversation, **6/6 tours justes** — `tests/catalogues/trois-types/` et
  `scripts/mesure_trois_types_de_source.py`. Le portage est par ailleurs éprouvé sur une
  base réelle de 1,66 M de lignes et dix tables.
- **Statut** : **Traité** (C18, 2026-09-14).

### Seul le prompt du planificateur est budgété

- **Où** : [`orchestrator/graph.py:753`](../src/data_analyst_agent/orchestrator/graph.py)
  (`_peser_le_prompt`)
- **Problème** : l'agent SQL, l'agent d'analyse et la synthèse ne décomptent aucun budget.
  Ils restent bornés par leurs limites d'allers-retours, mais un dépassement chez eux
  n'est vu qu'au retour — ou par un refus explicite du serveur.
- **Correction proposée** : remonter le décompte dans le garde-fou commun aux nœuds,
  plutôt que dans le seul nœud de planification.
- **Statut** : Ouvert.

### `safe_dir_name` distingue la casse

- **Où** : [`orchestrator/workspace.py:351`](../src/data_analyst_agent/orchestrator/workspace.py)
- **Problème** : `A` et `a` produisent deux noms de dossier distincts, qui collisionneraient
  sur un système de fichiers insensible à la casse (macOS, Windows). Sans effet ici — les
  logins sont repliés en amont par `normalize_login`, et les identifiants de conversation
  sont hexadécimaux — mais c'est une **hypothèse de déploiement Linux**, pas une garantie
  du code.
- **Correction proposée** : encoder aussi les majuscules, ou refuser explicitement les
  systèmes de fichiers insensibles à la casse au démarrage.
- **Statut** : Ouvert, hypothèse assumée.

### Les prompts sont sortis du code mais restent dans le paquet

- **Où** : [`prompts/`](../src/data_analyst_agent/prompts)
- **Problème** : les six prompts (planificateur, agent SQL, agent d'analyse, synthèse,
  agent système, agent de rappel) sont éditables sans toucher au code, mais ils vivent
  dans le wheel. Un déploiement ne peut pas les adapter à son cas d'usage sans réinstaller.
  Sur un socle destiné à plusieurs cas d'usage clients, c'est la limite qu'on rencontrera
  en premier.
- **Correction proposée** : un `DAA_PROMPTS_DIR` qui surcharge par déploiement, avec repli
  sur le fichier embarqué.
- **Statut** : Ouvert.

### Aucun fichier `LICENSE`

- **Où** : racine du dépôt
- **Problème** : le README annonce MIT pour le code applicatif, et
  [`pyproject.toml`](../pyproject.toml) ne déclare aucune licence. Sans fichier `LICENSE`,
  l'annonce n'a pas de valeur juridique.
- **Correction proposée** : ajouter un `LICENSE` MIT et le champ `license` du manifeste —
  ou changer l'annonce du README si l'intention est autre. **Décision du propriétaire.**
- **Statut** : Ouvert.

---

## Chantiers décidés, non lancés

| Chantier | État | Ce qu'il reste à faire |
|---|---|---|
| Migration des conversations réelles | Script écrit et testé sur copie, **jamais exécuté en vrai** | `scripts/migrate_workspace_owner.py --workspace /home/ubuntu/daa-workspaces-persist --owner floSa --appliquer` |
| Verrou disque DuckDB sur la branche de démonstration | Corrigé sur `main` (`d51c474`), **absent de `Maxizoo`** | Un `cherry-pick` ; le correctif n'exige aucun compte |

---

## Instabilité observée

### Un test à seuil de temps qui rate sous charge

- **Où** : [`tests/integration/test_sandbox_docker.py`](../tests/integration/test_sandbox_docker.py)
  (`test_timeout_interrompt_le_kernel_sans_tuer_la_session`)
- **Problème** : observé en échec **une fois** sur une exécution complète, puis vert seul,
  vert sur la version précédente en exécution complète, et vert sur les deux exécutions
  suivantes. C'est un test de délai sur un vrai conteneur : il rate quand Docker est
  chargé. Ce n'est pas une régression.
- **Correction proposée** : marge de tolérance dépendante de la charge, ou marquer le test
  comme sensible à l'environnement.
- **Statut** : Ouvert, à surveiller.

---

## Récapitulatif priorisé

| Priorité | Item | État | Impact |
|---|---|---|---|
| P0 | Verrou DuckDB absent de la branche `Maxizoo` | Ouvert | Le SQL généré y lit les fichiers de l'hôte |
| — | Anti-force brute par adresse derrière un frontal | **Corrigé** | Était : un échec quelconque verrouillait tous les comptes. Devenu un compteur par appelant réel, `X-Forwarded-For` cru des seuls mandataires déclarés — deux compteurs distincts et une usurpation refusée, mesurés le 16 septembre 2026 |
| — | Questions SUR le système sans route | **Corrigé** | Était : 8 replis et 4 réponses à côté sur 21 questions méta. Devenu 21/21, et 9 appels LLM au lieu de 43 |
| — | Catalogue limité à `postgres` et `file` | **Corrigé** | Un troisième type `duckdb`, qui apporte les clés étrangères qu'aucun fichier ne déclare. Mesuré 6/6 sur les trois types à la fois |
| — | Relevé jamais rafraîchi | **Corrigé** | Était : une source revenue restait « non relevée » toute la session. Devenue re-tentée au bout de 30 s, et périmée au bout de 15 min |
| — | Relevé non borné en temps | **Corrigé** | Était : une source muette bloquait l'inventaire sans plafond (mesuré : > 75 s). Devenue dégradée en injoignable au bout de 10 s |
| — | Plafond de l'agent système trop serré | **Corrigé** | Était : une question perdue par épuisement du plafond, sans erreur levée. Devenu 36/36, pour le même nombre d'appels |
| — | Période lue sur la première colonne de date venue | **Corrigé** | La source désigne sa colonne de référence ; une désignation fausse se dit au lieu de retomber en silence |
| — | Code d'analyse non persisté, reprise impossible au-delà d'un tour | **Corrigé** | Était : « mets les barres en bleu » deux tours plus tard fabriquait une figure neuve présentée comme une reprise. Devenu un magasin d'artefacts nommés — rejeu réussi au tour +2 et au tour +5, pour +472 tokens de prompt à sept artefacts |
| — | Un artefact désigné et jamais produit ne se disait pas | **Corrigé** | Était : une figure neuve rendue en silence sur « remontre-moi le camembert que tu avais fait ». L'absence est avouée en tête de réponse, et le tour continue |
| — | Correspondance déclarée jamais relue contre la source | **Corrigé** | Était : une colonne déclarée inexistante était silencieusement réparée par l'agent SQL (4 tirages sur 4), ou rendait une erreur qui accusait les données. Refusée avant la requête, 2 appels au lieu de 5 |
| P1 | Migration des conversations réelles jamais exécutée | Ouvert | Les fils existants restent hors de l'arborescence par utilisateur |
| P1 | Aucun fichier `LICENSE` | Ouvert | L'annonce MIT du README est sans portée |
| P2 | argon2 non plafonné face au pool de threads | Ouvert | Une rafale de connexions réserve ~2,5 Gio |
| P2 | `resolve()` réécrit les sessions à chaque requête | Ouvert | Contention d'écriture quand la charge monte |
| P2 | `list()` en O(n) sur tous les fils | Ouvert | Latence croissante de l'écran d'accueil |
| P2 | Budget de tokens limité au planificateur | Ouvert | Les autres agents débordent sans avertissement en amont |
| P2 | Prompts non surchargeables par déploiement | Ouvert | Adapter un cas d'usage impose de réinstaller |
| P3 | Corps non borné en `Transfer-Encoding: chunked` | Ouvert, assumé | Le frontal est la bonne place pour le plafond dur |
| P3 | Trace détaillée rendue au porteur de session | Ouvert | Nécessite une notion de rôle, absente |
| P3 | Tolérance à la corruption asymétrique | Ouvert | Un manifeste abîmé lève au lieu de dégrader |
| P3 | `fit_to_budget` en O(n²) dégénéré | Ouvert | 126 ms pour 1 000 objets, hors réglages par défaut |
| P3 | `safe_dir_name` sensible à la casse | Ouvert, assumé | Hypothèse de déploiement Linux |
| P3 | Test de délai sandbox instable sous charge | Ouvert | Faux rouge occasionnel en intégration |
