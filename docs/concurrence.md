# Le produit sous charge : plusieurs utilisateurs en même temps

Mesures du 2026-09-15, machine de développement (NVIDIA L4 23 034 Mio, 22 cœurs,
86 Gio), contre le service en marche : vLLM `0.28.0` sur le port 8100
(`google/gemma-4-E4B-it-qat-w4a16-ct`, `--max-model-len 32768`,
`--gpu-memory-utilization 0.55`) et Ollama `gemma4:e4b` sur le port 11434.
Banc : [`scripts/mesure_concurrence.py`](../scripts/mesure_concurrence.py).

Le produit est multi-utilisateurs depuis les chantiers C4 et C5 — comptes
argon2id, sessions côté serveur, cloisonnement par dossier — et la bascule sur
vLLM a été décidée POUR le parallélisme, contre un Ollama qui sert une requête à
la fois. **Aucune de ces deux choses n'avait été mesurée sous charge.** Les
vingt-huit chantiers précédents ont mesuré séquentiellement : une question, une
réponse, un utilisateur.

Ce document est cette mesure. Il sépare ce qui a été **constaté** de ce qui en
est **déduit**, et donne pour chaque affirmation le chiffre qui la porte.

## Ce qu'on y trouve

| § | Ce qu'on y trouve |
|---|---|
| 1 | le protocole : N utilisateurs **distincts**, et pourquoi ce n'est pas la même chose que N requêtes |
| 2 | latences et débit, par capacité, de 1 à 16 utilisateurs |
| **3** | **le goulot, nommé** — et les quatre candidats écartés par la mesure, pas par le raisonnement |
| **4** | **le défaut que la charge a révélé** : un client HTTP partagé entre boucles d'événements, invisible en séquentiel |
| 5 | le cloisonnement sous charge : le protocole, et ce qu'il prouve |
| 6 | les erreurs par nature, et le palier où chacune apparaît |
| 7 | vLLM contre Ollama : ce que la bascule a réellement apporté |
| 8 | ce qui n'a pas été mesuré, et ce qu'il faudrait pour le faire |

---

## 1. Le protocole

### 1.1 N utilisateurs, pas N requêtes

Un banc qui tire N requêtes sur un même compte mesure le débit et rien d'autre.
Ici, chaque utilisateur a **son compte, sa session, son fil et ses données** :

- un compte `banc_uK` dont le mot de passe est tiré de `secrets` et n'est écrit
  nulle part ;
- une source CSV `mesures_uK`, déclarée dans un catalogue jetable, dont **toutes
  les lignes portent un jeton unique** `JETON-uK-<aléa>` ;
- un fil **neuf par palier** — un fil réutilisé de palier en palier grandirait à
  mesure que la campagne avance (tableaux mémorisés, contexte réinjecté), et les
  derniers paliers seraient plus lents pour deux raisons mêlées. Mesuré au
  premier jet, avant correction du banc : treize tableaux intermédiaires et un
  contexte tronqué dans le fil d'un utilisateur, avant même le palier 16.

Le jeton est le **canari**. Il n'est pas dans le catalogue — donc pas dans le
prompt du planificateur — et ne peut apparaître dans une réponse que si le
fichier de cet utilisateur-là a été lu. Le voir chez un autre serait une fuite,
sans interprétation à faire.

### 1.2 Ce que le banc ne fait pas

Il **n'alloue rien sur la carte**. Il envoie des requêtes HTTP à une instance
d'uvicorn qu'il lance lui-même, laquelle parle au moteur déjà en service. Ni
vLLM ni Ollama ne sont relancés, et aucun de leurs réglages n'est touché.

Tout l'état du banc — comptes, sessions, conversations, catalogue — vit sous un
terrain jetable supprimé en sortie. **Les comptes de test n'existent que là**, et
les supprimer est un `rmtree`, donc une chose qui ne peut pas être oubliée.

### 1.3 Les trois capacités

Elles n'ont pas le même profil, et c'est pour ça qu'on les sépare :

| capacité | ce qu'elle traverse | question du banc |
|---|---|---|
| `query` | planificateur + agent SQL (plusieurs allers-retours) + DuckDB | « quelles sont les valeurs distinctes de la colonne jeton dans `mesures_uK` ? » |
| `analyze` | planificateur + génération de code + **conteneur Docker** | « trace un histogramme de la colonne mesure de `mesures_uK` » |
| `predict` | planificateur + modèle sklearn en mémoire — **ni base ni bac à sable** | « quelle espèce d'iris pour sepal_length=5.1… ? » |

`predict` est le témoin : c'est le chemin qui n'a que le moteur devant lui.

### 1.4 La commande

```bash
uv run python scripts/mesure_concurrence.py \
    --paliers 1,2,4,8,16 --tours 3 --delai-client 300 \
    --llm-base-url http://localhost:8100/v1 \
    --llm-model google/gemma-4-E4B-it-qat-w4a16-ct \
    --json mesures.json
```

Le moteur est **mutualisé** avec d'autres projets de la machine. Le banc relève
donc, avant chaque palier, ce que le moteur traite déjà (`temoin`) : une mesure
faite sur un moteur occupé n'est pas fausse, elle est autre chose, et il faut
pouvoir le dire. Les campagnes citées ici ont toutes démarré leurs paliers sur
un moteur au repos.

---

## 2. Latences et débit

Trois tours par utilisateur et par palier, un fil neuf à chaque palier, un tour
de chauffe jeté avant le premier. Percentiles **par rang le plus proche** : sur
des échantillons de trois à quarante-huit points, interpoler inventerait une
précision qu'on n'a pas — les valeurs citées ont réellement été observées.

Les deux dernières colonnes sont la sonde du moteur : combien de requêtes vLLM
traitait **en même temps**, et combien attendaient en file. C'est ce qui
distingue « le moteur est saturé » de « on ne lui envoie rien ».

| capacité | N | requêtes | abouties | p50 (s) | p95 (s) | débit (req/min) | moteur : en vol / en file |
|---|---|---|---|---|---|---|---|
| query | 1 | 3 | 3 | 3,1 | 3,1 | 20,3 | 1 / 0 |
| query | 2 | 6 | 6 | 2,8 | 3,4 | 42,2 | 2 / 0 |
| query | 4 | 12 | 12 | 3,3 | 3,9 | 74,0 | 4 / 1 |
| query | 8 | 24 | 24 | 4,7 | 5,3 | **109,0** | 8 / 2 |
| query | 16 | 48 | 48 | 7,4 | 13,6 | **118,7** | 16 / 5 |
| analyze | 1 | 3 | 3 | 12,1 | 24,4 | 4,8 | 1 / 0 |
| analyze | 2 | 6 | 6 | 11,7 | 23,7 | 9,9 | 2 / 0 |
| analyze | 4 | 12 | 12 | 12,1 | 25,6 | 18,5 | 4 / 0 |
| analyze | 8 | 24 | 24 | 12,4 | 37,8 | **23,2** | 8 / 0 |
| analyze | 16 | 48 | 47 | 23,7 | 74,0 | **22,7** | 16 / 1 |
| predict | 1 | 3 | 3 | 1,7 | 2,8 | 29,5 | 1 / 0 |
| predict | 2 | 6 | 6 | 1,7 | 1,8 | 71,4 | 2 / 0 |
| predict | 4 | 12 | 12 | 1,8 | 1,9 | 134,8 | 4 / 0 |
| predict | 8 | 24 | 24 | 1,9 | 2,2 | 234,8 | 8 / 0 |
| predict | 16 | 48 | 48 | 2,2 | 2,7 | **411,0** | 16 / 0 |

### Où le débit cesse de croître

Trois réponses, et **elles sont différentes selon la capacité** — c'est le
premier résultat de ce chantier, et c'est ce qui justifie de ne pas avoir mesuré
« le produit » en bloc.

| capacité | dernier palier qui paie | ce que le palier suivant ajoute |
|---|---|---|
| `predict` | **jamais atteint** — 411 req/min à N=16, croissance encore proche du linéaire | +75 % de N=8 à N=16 |
| `query` | **N=8** | +9 % seulement de N=8 à N=16, pour un p50 qui passe de 4,7 à 7,4 s |
| `analyze` | **N=8** | **−2 %** : le débit ne monte plus du tout, et le p50 double (12,4 → 23,7 s) |

`predict` est le témoin : il ne fait qu'un appel au planificateur et une
prédiction sklearn en mémoire. Il tient seize utilisateurs sans plier, ce qui
dit que **ni le serveur HTTP, ni le pool de threads, ni le magasin de sessions
ne plafonnent à seize** — le plafond des deux autres capacités est ailleurs.

---

## 3. Le goulot, nommé

Cinq candidats étaient nommables d'avance : vLLM, le sémaphore du bac à sable,
le pool de threads de l'application, le verrou par conversation, Postgres.
Quatre sont écartés **par une mesure**, pas par un raisonnement.

### 3.1 Écarté — le socle HTTP, le magasin de sessions, le pool de threads

Deux routes qui ne calculent rien, et tout ce qui les sépare est la session :
`/health` est ouverte, `/me` passe par `SessionStore.resolve`, qui prend un
verrou sur le fichier de sessions, le relit, met à jour `last_seen_at` et le
**réécrit en entier** — à chaque requête authentifiée, de tous les utilisateurs.
Un verrou unique traversé par tout le trafic est un candidat sérieux.

| K clients | `/health` | `/me` (verrou + réécriture du fichier) |
|---|---|---|
| 1 | 605 req/s | 563 req/s |
| 4 | 1 119 req/s | 701 req/s |
| 8 | 1 143 req/s | 713 req/s |
| 16 | 1 065 req/s | **735 req/s** (p50 21,5 ms) |

La session **coûte** — `/me` plafonne à ~700 req/s là où `/health` dépasse
1 000 — mais ce plafond vaut **44 000 requêtes par minute**, contre 119 pour la
capacité applicative la plus rapide hors `predict`. Deux ordres de grandeur
d'écart : le socle n'est pas le goulot, et le verrou du magasin de sessions non
plus.

### 3.2 Écarté — Postgres

Les sources du terrain sont des fichiers lus par DuckDB dans le process : la
base du projet n'est pas sur le chemin du banc. On l'éprouve donc à part, avec
l'adaptateur du produit, K connexions simultanées et la requête la plus
courante.

| K connexions | débit | p50 | p95 |
|---|---|---|---|
| 1 | 607 req/s | 1,3 ms | 1,5 ms |
| 4 | 1 480 req/s | 2,3 ms | 3,0 ms |
| 8 | 1 515 req/s | 4,3 ms | 7,1 ms |
| 16 | **1 323 req/s** | 9,7 ms | 18,6 ms |

79 000 requêtes par minute. Trois ordres de grandeur au-dessus de ce que
l'application consomme. Postgres est hors de cause.

### 3.3 Écarté — le verrou par conversation

Il est **par conversation**, pas global : seize utilisateurs sur seize fils
différents ne s'attendent pas, et c'est ce que montre `predict` à N=16 (411
req/min, aucune dégradation). Le cas où il mord — deux tours simultanés sur le
**même** fil — est éprouvé à part : les quatre messages sont persistés, aucun
n'est perdu (§5.3). Ce n'est donc pas un plafond de débit ; c'est une
sérialisation voulue, et elle ne concerne qu'un fil à la fois.

### 3.4 **Le goulot de `analyze` : le sémaphore du bac à sable**

La réponse porte sa propre trace, un pas par nœud du graphe avec sa durée.
C'est elle qui désigne, au lieu de laisser déduire :

| capacité | N | bout en bout p50 | `plan` | `retrieval` | **`analysis`** | `synthesize` |
|---|---|---|---|---|---|---|
| analyze | 1 | 12,2 s | 0,9 | — | **10,4** | 0,6 |
| analyze | 4 | 12,3 s | 0,8 | — | **10,6** | 0,8 |
| analyze | 16 | 47,5 s | 0,9 | — | **44,8** | 1,2 |

De 1 à 16 utilisateurs, `plan` ne bouge pas (0,9 → 0,9 s) et `synthesize`
double à peine (0,6 → 1,2 s) : **les nœuds qui appellent le modèle ne sont pas
ce qui ralentit.** Seul `analysis` explose, ×4,3 — et c'est le seul nœud qui
ouvre un conteneur.

Le réglage qui le borne est `DAA_SANDBOX_MAX_SESSIONS=4` : quatre conteneurs
vivants au plus, les suivants attendent une place, et au bout de
`sandbox_queue_timeout=60 s` ils sont refusés. Deux confirmations
indépendantes :

- le débit plafonne **exactement** entre N=8 et N=16 (23,2 puis 22,7 req/min)
  alors que le nombre de demandeurs double ;
- à N=16, un refus explicite apparaît — « toutes les sandboxes sont occupées
  (4 en parallèle) : pas de place libérée en 60 s » (§6).

Et une confirmation par la négative : à N=16 sur `analyze`, la sonde du moteur
ne relève **jamais plus d'une requête en file**, là où `query` au même palier en
relève cinq. Les analyses n'atteignent pas le moteur ensemble — elles attendent
avant, devant le sémaphore.

### 3.5 **Le goulot de `query` : vLLM**

Le même tableau, pour `query` :

| capacité | N | bout en bout p50 | `plan` | `rappel` | `retrieval` | `system` |
|---|---|---|---|---|---|---|
| query | 1 | 3,0 s | 0,7 | 0,2 | 1,4 | 0,1 |
| query | 4 | 2,7 s | 0,8 | 0,6 | 1,6 | 0,2 |
| query | 16 | 5,9 s | **1,2** | **1,4** | **2,9** | 0,2 |

Ici, **tous** les nœuds qui parlent au modèle grandissent ensemble — `plan`
×1,7, `retrieval` ×2,1, `rappel` ×7 — et aucun ne domine. C'est la signature
d'un serveur d'inférence qui répartit son débit entre ses clients, pas d'une
ressource contendue dans l'application.

La sonde le confirme directement : `vllm:num_requests_waiting` passe de 0 (N≤2)
à 1 (N=4), 2 (N=8) puis **5** (N=16). Le moteur met des requêtes en file
d'attente, et c'est à ce moment-là que le débit cesse de croître (109,0 →
118,7 req/min, +9 %).

**Pourquoi `predict` ne plafonne pas au même endroit** : un tour `predict` fait
un appel au modèle, un tour `query` en fait quatre à six (le planificateur, puis
la boucle de l'agent SQL, puis la reprise). Ce n'est pas le nombre de requêtes
HTTP qui sature vLLM, c'est le nombre de jetons — et `query` en consomme
plusieurs fois plus par réponse rendue.

### 3.6 Le résumé

| capacité | goulot | la mesure qui le désigne |
|---|---|---|
| `predict` | **aucun à N=16** | 411 req/min, `num_requests_waiting` à 0 |
| `query` | **vLLM** | tous les nœuds LLM grandissent ensemble ; `num_requests_waiting` = 5 à N=16 |
| `analyze` | **le sémaphore du bac à sable (4)** | seul `analysis` grandit (×4,3) ; débit plat 23,2 → 22,7 ; refus explicite à N=16 |

---

## 4. Le défaut que la charge a révélé

C'est le seul point de ce chantier où la mesure a désigné un **défaut du
produit**, et non un plafond assumé. Il est corrigé ; ce paragraphe garde la
mesure d'avant, parce qu'elle est ce qui ne se retrouve pas dans un diff.

### 4.1 Ce qu'on a vu

Première campagne, avant toute correction. Le débit de `query` monte
normalement, puis **s'effondre** au palier 16 :

| capacité | N | débit AVANT (req/min) | abouties AVANT |
|---|---|---|---|
| query | 8 | 86,6 | 23/24 |
| query | 16 | **7,3** | **38/48** |
| analyze | 16 | **6,2** | **38/48** |

Un débit de 7,3 req/min à seize utilisateurs, contre 86,6 à huit. Ce n'est pas
une saturation — une saturation aplatit une courbe, elle ne la renverse pas. Et
la sonde du moteur disait que vLLM n'était pas en cause : onze requêtes en vol,
une seule en file.

Dans les réponses, dix erreurs sur quarante-huit, de deux natures :
`ModelAPIError: Connection error.` et l'abandon du banc au bout de 300 s. Dans
le journal du serveur, la cause :

```
RuntimeError: <asyncio.locks.Event object at 0x…> is bound to a different event loop
  …/httpcore/_async/connection_pool.py, in handle_async_request
  …/httpcore/_backends/anyio.py, line 35, in read
  …/asyncio/locks.py, line 209, in wait
```

### 4.2 La cause

`POST /chat` est déclaré en `def`, pas en `async def` : Starlette le sert dans
son pool de threads. PydanticAI y appelle `run_sync`, qui prend la boucle
d'événements **du thread courant** et la laisse ouverte après coup. Plusieurs
requêtes en même temps, c'est donc **plusieurs boucles vivantes à la fois** —
une par thread.

Or l'orchestrateur, construit une fois pour tout le processus, figeait son
modèle à la construction : un `AsyncOpenAI`, donc **un** client `httpx`, donc
**un** pool de connexions, partagé par toutes ces boucles. Rien n'empêche la
boucle B de reprendre dans le pool une connexion ouverte par la boucle A. Le
socket porte alors un `asyncio.Event` lié à A ; l'attendre depuis B lève, le SDK
OpenAI le rend en `APIConnectionError`, réessaie deux fois — et l'utilisateur
lit « la source de données n'a pas pu être interrogée » alors que le moteur
répond parfaitement aux autres au même instant.

**Le défaut a besoin de deux boucles vivantes en même temps pour se produire.**
C'est exactement ce qu'aucune des mesures des vingt-huit chantiers ne faisait.
Il est reproductible sans le moindre LLM — deux threads, deux boucles, un client
`httpx` partagé, un serveur HTTP local qui garde la connexion ouverte : c'est le
témoin `test_temoin_un_client_partage_entre_deux_boucles_finit_par_rompre`, qui
échoue quatre fois sur cinq.

### 4.3 La correction

**Un modèle — donc un client, donc un pool — par thread.** C'est la plus petite
chose qui suffise : la boucle est par thread, le client l'est donc aussi, et le
pool garde son intérêt (les connexions restent réutilisées *dans* le thread, là
où c'est sûr). Le nombre de clients est borné par la taille du pool de threads
de Starlette.

- [`llm.modele_du_thread`](../src/data_analyst_agent/llm.py) : le cache par
  thread, avec l'empreinte des réglages — un thread qui sert successivement deux
  configurations n'hérite pas de la première ;
- [`orchestrator/graph.py`](../src/data_analyst_agent/orchestrator/graph.py) :
  `Orchestrator.model` devient une propriété. Un modèle **injecté** reste
  partagé — c'est ce qu'un test demande en le passant, et un modèle scripté
  n'ouvre aucune connexion.

### 4.4 Ce que ça a changé

Même banc, même machine, même moteur, même délai d'abandon (300 s) :

| capacité | N | débit AVANT | débit APRÈS | abouties AVANT | abouties APRÈS |
|---|---|---|---|---|---|
| query | 4 | 55,1 | **74,0** | 12/12 | 12/12 |
| query | 8 | 86,6 | **109,0** | 23/24 | **24/24** |
| query | 16 | 7,3 | **118,7** | 38/48 | **48/48** |
| analyze | 4 | 16,9 | 18,5 | 11/12 | **12/12** |
| analyze | 8 | 23,6 | 23,2 | 22/24 | **24/24** |
| analyze | 16 | 6,2 | **22,7** | 38/48 | **47/48** |
| predict | 16 | 341,5 | **411,0** | 47/48 | **48/48** |

Sur la campagne entière — 279 réponses — les erreurs passent de **quinze de
quatre natures** à **une seule**, et cette dernière est un refus voulu (§6).

Une conséquence qu'il faut dire : ce n'est qu'**après** la correction que le
goulot réel devient lisible. Avant, `vllm:num_requests_waiting` ne dépassait
jamais 1, parce que les requêtes se perdaient en réessais au lieu d'atteindre le
moteur. Après, il monte à 5 — et §3.5 devient démontrable.

---

## 5. Le cloisonnement sous charge

C'est la propriété la plus chère du produit, et elle n'avait été éprouvée qu'au
repos : Alice puis Bob, chacun son tour ([`tests/unit/api/test_cloisonnement.py`](../tests/unit/api/test_cloisonnement.py)).

### 5.1 Le protocole

Seize utilisateurs, seize comptes, seize sessions, seize sources, **seize jetons
distincts**. La campagne complète mène 279 tours. Ensuite, cinq vérifications —
et la **première ne porte pas sur le cloisonnement mais sur le protocole** :

| vérification | ce qu'elle interdit | résultat |
|---|---|---|
| **le canari est actif** | qu'« aucune fuite » soit vrai parce que personne n'a rien lu | **TENU** — 16/16 ont reçu leur propre jeton |
| aucune réponse ne porte le jeton d'un autre | une donnée d'autrui dans ma réponse | **TENU** — 279 réponses relues intégralement (2 134 Kio) |
| chacun ne liste que ses propres fils | le fil d'autrui dans ma liste | **TENU** — 16 listes, 93 fils, aucun intrus |
| le fil d'un autre répond 404 | un fil ouvrable par son identifiant, et un 403 qui confirmerait son existence | **TENU** — 480 tentatives croisées, toutes en 404 |
| sur le disque, aucune transcription ne porte le jeton d'un autre | un fichier au mauvais endroit, que l'API ne montrerait pas | **TENU** — 93 transcriptions relues |

Sans la première, les quatre autres seraient vertes par construction : un jeton
qui n'apparaît jamais nulle part ne peut pas apparaître au mauvais endroit. Elle
est vérifiée, et l'audit **échoue** si elle ne l'est pas.

### 5.2 Ce que la recherche couvre

Le jeton est cherché dans **toute** la réponse sérialisée — la phrase, les
tableaux, les images, le plan, chaque ligne de trace — et non dans le seul champ
`answer` : une fuite par un tableau ou par une ligne de trace est affichée à
l'utilisateur comme le reste. Puis sur le disque, dans le texte brut de chaque
`transcript.json`, avec son champ `owner` confronté à la racine où il est rangé.

Le résultat est le même **avant et après** la correction du §4 : le cloisonnement
n'a jamais cédé, y compris pendant l'effondrement de débit. C'est cohérent avec
sa nature — il est un **chemin**, pas un filtre : le magasin est ouvert pour un
utilisateur et n'a pas d'autre racine que la sienne, donc aucune route ne peut
oublier de filtrer.

### 5.3 Deux tours simultanés sur le même fil

Un seul utilisateur, le **même** fil, deux questions parties ensemble : les
quatre messages sont persistés (2 questions + 2 réponses). C'est le verrou par
conversation qui le tient, et c'est bien lui qu'on éprouve ici — pas deux
comptes.

### 5.4 Ce qui garde la propriété maintenant

L'audit du banc demande le vrai moteur et un quart d'heure. Le même invariant est
donc tenu par des tests rapides et déterministes,
[`tests/unit/api/test_concurrence.py`](../tests/unit/api/test_concurrence.py) :
quatre utilisateurs, un orchestrateur factice qui dort juste assez pour que les
requêtes se chevauchent vraiment, et un test **témoin** qui échoue si le
chevauchement n'a pas eu lieu — sans lui, tous les autres passeraient sans rien
prouver.

---

## 6. Les erreurs, par nature et par seuil

Le banc nomme ce qui échoue plutôt que de compter des échecs : « plafond de
sandbox atteint » et « limite de requêtes » se ressemblent vus de loin — « ça
n'a pas pu passer » — et n'appellent pas la même correction, l'un veut de la
mémoire, l'autre un quota plus large.

### 6.1 Après correction

Sur les 279 tours de la campagne complète, **une seule erreur** :

| nature | palier d'apparition | message rendu |
|---|---|---|
| **plafond de sandbox** | `analyze`, **N=16** (1 tour sur 48) | « toutes les sandboxes sont occupées (4 en parallèle) : pas de place libérée en 60 s » |

C'est le comportement **voulu** de `DAA_SANDBOX_MAX_SESSIONS=4` avec
`sandbox_queue_timeout=60 s` : au-delà du plafond, une session attend son tour,
et au bout d'une minute elle est refusée explicitement plutôt que laissée en
attente indéfinie. Le seuil d'apparition est donc mesuré : **entre 8 et 16
analyses simultanées**, sur cette machine, avec ces réglages.

Trois natures que le banc sait nommer ne se sont **pas** présentées, et
l'absence est un résultat :

| nature jamais vue | pourquoi |
|---|---|
| **limite de requêtes** (429) | le quota est de 20 questions par minute glissante et par compte ; à N=16, chaque utilisateur en pose trois par palier — le plafond du produit est atteint bien avant celui du compte |
| **refus d'authentification** | seize sessions ouvertes, aucune perdue malgré la réécriture du fichier de sessions à chaque requête |
| **verrou de conversation** | il n'apparaît jamais comme une erreur : il sérialise, il ne refuse pas (§5.3) |

### 6.2 Avant correction, pour mémoire

| nature | paliers | total |
|---|---|---|
| connexion au moteur (§4) | `query` 8 et 16, `analyze` 4, 8 et 16, `predict` 16 | 14 |
| délai du moteur | `analyze` 16 | 1 |
| abandon du banc à 300 s | `query` 16, `analyze` 16 | 9 |

L'« abandon du banc » n'est pas une panne du produit : c'est le banc qui renonce
au bout de son délai. Il est compté à part pour cette raison — confondre les deux
ferait porter à l'application une erreur qu'on a créée en fixant un chronomètre.
Les neuf abandons sont néanmoins une conséquence du §4 : les tours concernés
avaient dépassé cinq minutes en réessais.

---

## 7. vLLM contre Ollama : ce que la bascule a apporté

C'était l'argument central de la migration ([VLLM.md](VLLM.md)) : Ollama sert
une requête à la fois (`OLLAMA_NUM_PARALLEL=1`, et le runner est bien lancé avec
`-np 1`). Personne ne l'avait chiffré sur le produit.

### 7.1 Sur le produit, à code identique

Seule l'URL du moteur change — c'est tout ce que la bascule touche
([`llm.py`](../src/data_analyst_agent/llm.py) ne nomme aucun moteur). Même
question, même banc, deux tours par utilisateur, un tour de chauffe jeté :

| N utilisateurs | vLLM p50 | vLLM débit | Ollama p50 | Ollama débit | rapport de débit |
|---|---|---|---|---|---|
| 1 | 4,3 s | 13,1 req/min | 85,2 s | 0,6 req/min | **×22** |
| 4 | 3,9 s | 42,6 req/min | 58,3 s | 3,4 req/min | **×12,5** |

Quatre analystes qui travaillent ensemble obtiennent **42,6 réponses par minute
sur vLLM contre 3,4 sur Ollama**. Une question qui revient en quatre secondes
revient en une minute.

La ventilation par nœud dit où va la différence — partout, et proportionnellement :

| moteur | N | `plan` | `rappel` | `retrieval` | `system` |
|---|---|---|---|---|---|
| vLLM | 4 | 1,2 s | 0,7 s | 2,4 s | 0,2 s |
| Ollama | 4 | 14,7 s | 20,1 s | 21,7 s | 16,7 s |

### 7.2 Sur le moteur seul

K requêtes simultanées de **prompts distincts** — un prompt répété serait servi
par le cache de préfixe et gonflerait le parallélisme apparent — 200 jetons
chacune, un tir de chauffe jeté :

| K simultanées | vLLM : temps total | vLLM : débit | Ollama : temps total | Ollama : débit |
|---|---|---|---|---|
| 1 | 3,8 s | 0,26 req/s | 17,7 s | 0,06 req/s |
| 2 | 3,4 s | 0,59 req/s | 24,8 s | 0,08 req/s |
| 4 | 5,2 s | 0,78 req/s | 27,4 s | 0,15 req/s |
| 8 | **5,6 s** | **1,44 req/s** | **43,0 s** | **0,19 req/s** |

Huit requêtes rendues en 5,6 s là où une seule en prend 3,8 : **+47 % de temps
pour huit fois le travail**. Ollama met 43,0 s pour les mêmes huit, contre 17,7 s
pour une seule — **×2,4**. Le débit de vLLM est multiplié par 5,5 entre K=1 et
K=8 ; celui d'Ollama par 3,2.

**Ce que cette comparaison ne prouve pas.** Les deux moteurs ne servent pas le
même artefact — `gemma4:e4b` en Q4\_K\_M via llama.cpp d'un côté, une version
quantifiée w4a16 de l'autre. Une part de l'écart par requête tient au runtime et
à la quantification, pas au parallélisme. Et la latence d'une requête isolée sur
Ollama est instable sur cette machine : elle a été mesurée **plus lente à N=1
qu'à N=4**, deux fois, ce qui n'a pas de sens sous une simple mise en file.
Le rapport par requête est donc à lire comme un ordre de grandeur. Le rapport de
**débit à plusieurs utilisateurs**, lui, est net et c'est celui qui décide.

---

## 8. Ce qui n'a pas été mesuré

- **Plusieurs workers uvicorn.** Tout ce qui précède est mesuré sur **un seul**
  process. Deux conséquences connues et non mesurées : le sémaphore du bac à
  sable est de portée process, donc le plafond réel devient
  `sandbox_max_sessions × nombre de workers` (c'est écrit dans
  [`sandbox/client.py`](../src/data_analyst_agent/sandbox/client.py), ce n'est
  pas vérifié) ; et les verrous `flock` valent entre process, ce qui est testé
  unitairement mais pas sous charge.
- **La durée.** Les paliers durent des minutes, pas des heures. Rien ne dit ce
  que devient la mémoire du serveur, ni le nombre de descripteurs ouverts, après
  une journée à seize utilisateurs — la correction du §4 ouvre un client HTTP
  par thread, ce qui est borné mais pas mesuré dans la durée.
- **Postgres sur le chemin applicatif.** Il est écarté par une mesure directe
  (§3.2), pas par une campagne dont les sources seraient des bases. Le terrain du
  banc n'a que des fichiers ; pour faire mieux il faudrait une base par
  utilisateur du banc, ce qui déborde ce qu'on veut créer sur la machine.
- **Le palier au-delà de 16.** Le sémaphore du bac à sable plafonne `analyze` dès
  8 ; pousser plus loin mesurerait surtout la file d'attente. `predict`, lui,
  n'a pas montré son plafond : à 32 ou 64 utilisateurs, ce serait vLLM, et le
  chiffre manque.
- **Le relevé des sources.** Le catalogue du banc est fait de CSV locaux relevés
  instantanément. Une source lente ou injoignable a ses propres bornes
  (`releve_delai`, `releve_reprise`) qui n'ont jamais été éprouvées à plusieurs.
