# Le produit sous charge : plusieurs utilisateurs en même temps

Mesures du 2026-09-15, machine de développement (NVIDIA L4 23 034 Mio, 22 cœurs,
86 Gio), contre le service en marche : vLLM `0.28.0` sur le port 8100
(`google/gemma-4-E4B-it-qat-w4a16-ct`, `--max-model-len 32768`,
`--gpu-memory-utilization 0.55`).
Banc : [`scripts/mesure_concurrence.py`](../scripts/mesure_concurrence.py).

Le produit est multi-utilisateurs depuis les chantiers C4 et C5 — comptes
argon2id, sessions côté serveur, cloisonnement par dossier — et vLLM a été
choisi POUR le parallélisme, parce qu'il sert plusieurs requêtes de front.
**Aucune de ces deux choses n'avait été mesurée sous charge.** Les
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
| 7 | ce que le parallélisme du moteur apporte, sur le produit et sur le moteur seul |
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
d'uvicorn qu'il lance lui-même, laquelle parle au moteur déjà en service. Le
serveur vLLM n'est pas relancé, et aucun de ses réglages n'est touché.

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

La campagne de référence (§2, §5, §6) :

```bash
uv run python scripts/mesure_concurrence.py \
    --epreuve socle --epreuve charge --epreuve cloisonnement --epreuve meme-fil \
    --paliers 1,2,4,8,16 --tours 3 --delai-client 300 \
    --llm-base-url http://localhost:8100/v1 \
    --llm-model google/gemma-4-E4B-it-qat-w4a16-ct \
    --json mesures.json
```

Les deux épreuves qui ne visent pas l'application se lancent à part : celle de la
base (§3.2) a besoin du catalogue du projet, celle des moteurs (§7.2) de deux URL.

```bash
uv run python scripts/mesure_concurrence.py --epreuve postgres \
    --paliers 1,2,4,8,16 --tirs-socle 50

uv run python scripts/mesure_concurrence.py --epreuve moteurs \
    --paliers-moteurs 1,2,4,8 --tokens-moteurs 200 \
    --llm-base-url http://localhost:8100/v1 \
    --llm-model google/gemma-4-E4B-it-qat-w4a16-ct
```

Le moteur est **mutualisé** avec d'autres projets de la machine. Le banc relève
donc, avant chaque palier, ce que le moteur traite déjà (`temoin`) : une mesure
faite sur un moteur occupé n'est pas fausse, elle est autre chose, et il faut
pouvoir le dire. Les campagnes citées ici ont toutes démarré leurs paliers sur
un moteur au repos.

---

## 2. Latences et débit

Trois tours par utilisateur et par palier, un fil neuf à chaque palier, un tour
de chauffe jeté avant le premier — 279 tours en tout. Percentiles **par rang le
plus proche** : sur des échantillons de trois à quarante-huit points, interpoler
inventerait une précision qu'on n'a pas, les valeurs citées ont réellement été
observées.

Les deux dernières colonnes sont la sonde du moteur : combien de requêtes vLLM
traitait **en même temps**, et combien attendaient en file. C'est ce qui
distingue « le moteur est saturé » de « on ne lui envoie rien ».

| capacité | N | requêtes | abouties | p50 (s) | p95 (s) | débit (req/min) | moteur : en vol / en file |
|---|---|---|---|---|---|---|---|
| query | 1 | 3 | 3 | 4,5 | 5,7 | 13,1 | 1 / 0 |
| query | 2 | 6 | 6 | 3,8 | 6,4 | 26,2 | 2 / 0 |
| query | 4 | 12 | 12 | 4,7 | 5,5 | 48,8 | 4 / 0 |
| query | 8 | 24 | 24 | 6,8 | 11,2 | **67,6** | 8 / 1 |
| query | 16 | 48 | 48 | 11,4 | 21,0 | **83,1** | 16 / **12** |
| analyze | 1 | 3 | 3 | 16,0 | 34,0 | 3,5 | 1 / 0 |
| analyze | 2 | 6 | 6 | 13,7 | 28,6 | 8,4 | 2 / 0 |
| analyze | 4 | 12 | 12 | 12,1 | 25,1 | 18,6 | 4 / 0 |
| analyze | 8 | 24 | 24 | 12,3 | 39,8 | **20,8** | 8 / 0 |
| analyze | 16 | 48 | 48 | 32,7 | 76,4 | **21,9** | 16 / 0 |
| predict | 1 | 3 | 3 | 1,7 | 2,9 | 28,9 | 1 / 0 |
| predict | 2 | 6 | 6 | 1,6 | 1,7 | 71,3 | 2 / 0 |
| predict | 4 | 12 | 12 | 1,8 | 1,8 | 134,5 | 4 / 0 |
| predict | 8 | 24 | 24 | 1,9 | 2,1 | 243,4 | 8 / 0 |
| predict | 16 | 48 | 48 | 2,1 | 2,7 | **410,0** | 16 / 0 |

**279 tours sur 279 aboutis, aucune erreur, à aucun palier.**

### 2.1 Où le débit cesse de croître

Trois réponses, et **elles sont différentes selon la capacité** — c'est le
premier résultat de ce chantier, et c'est ce qui justifie de ne pas avoir mesuré
« le produit » en bloc.

| capacité | dernier palier qui paie | ce que le palier suivant ajoute |
|---|---|---|
| `predict` | **jamais atteint** — 410 req/min à N=16 | +68 % de N=8 à N=16 |
| `query` | **N=8** | +23 % de N=8 à N=16, pour un p50 qui passe de 6,8 à 11,4 s |
| `analyze` | **N=8** | **+5 %** : le débit ne monte plus, et le p50 est multiplié par 2,7 (12,3 → 32,7 s) |

`predict` est le témoin : il ne fait qu'un appel au planificateur et une
prédiction sklearn en mémoire. Il tient seize utilisateurs sans plier, ce qui
dit que **ni le serveur HTTP, ni le pool de threads, ni le magasin de sessions
ne plafonnent à seize** — le plafond des deux autres capacités est ailleurs.

### 2.2 Ce que ces chiffres valent

Deux campagnes complètes ont été menées après la correction du §4, à quelques
dizaines de minutes d'intervalle, avec le même banc et les mêmes paliers. La
seconde — celle du tableau ci-dessus — a tourné pendant qu'un autre projet de la
machine sollicitait la même carte ; la première avait la carte pour elle.

| capacité | N | 1re campagne | 2e campagne (le tableau) |
|---|---|---|---|
| query | 8 | 109,0 req/min | 67,6 req/min |
| query | 16 | 118,7 req/min | 83,1 req/min |
| analyze | 16 | 22,7 req/min | 21,9 req/min |
| predict | 16 | 411,0 req/min | 410,0 req/min |

Les **valeurs absolues bougent d'un tiers** sur ce que le moteur sert, et pas du
tout sur ce que le bac à sable borne — ce qui est exactement cohérent avec le
§3. La **forme** — où le débit cesse de croître, quel nœud grandit, quelle
sonde monte — est identique dans les deux. C'est elle qui porte les conclusions
de ce document ; les valeurs absolues ne valent que pour cette machine, ce jour,
et ce qu'elle portait d'autre.

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
| 1 | 559 req/s | 599 req/s |
| 4 | 1 097 req/s | 697 req/s |
| 8 | 1 194 req/s | 773 req/s |
| 16 | 1 107 req/s | **687 req/s** (p50 21,6 ms) |

La session **coûte** : `/me` plafonne autour de 700 req/s là où `/health` dépasse
1 000, et son p50 passe de 1,6 à 21,6 ms entre un client et seize. Mais ce
plafond vaut **41 000 requêtes par minute**, quand la capacité applicative la
plus rapide en rend 410. Cent fois moins : le socle n'est pas le goulot, et le
verrou du magasin de sessions non plus.

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

1 300 requêtes SQL par seconde à seize connexions simultanées, en moins de
10 ms au p50. L'application, elle, rend au mieux deux réponses par seconde, et
un tour de `query` émet une poignée d'instructions SQL : deux ordres de grandeur
séparent ce que la base peut servir de ce qu'on lui demande. Postgres est hors
de cause.

### 3.3 Écarté — le verrou par conversation

Il est **par conversation**, pas global : seize utilisateurs sur seize fils
différents ne s'attendent pas, et c'est ce que montre `predict` à N=16 (410
req/min, aucune dégradation). Le cas où il mord — deux tours simultanés sur le
**même** fil — est éprouvé à part : les quatre messages sont persistés, aucun
n'est perdu (§5.3). Ce n'est donc pas un plafond de débit ; c'est une
sérialisation voulue, et elle ne concerne qu'un fil à la fois.

### 3.4 La preuve : où le temps passe, nœud par nœud

La réponse porte sa propre trace, un pas par nœud du graphe avec sa durée. C'est
elle qui **désigne** le goulot, au lieu de laisser déduire — et les trois
capacités donnent trois signatures franchement différentes.

| capacité | N | bout en bout p50 | `plan` | `rappel` | `retrieval` | **`analysis`** | `synthesize` |
|---|---|---|---|---|---|---|---|
| predict | 1 | 1,7 s | 1,6 | — | — | — | 0,0 |
| predict | 16 | 2,1 s | **1,8** | — | — | — | 0,0 |
| query | 1 | 4,5 s | 1,4 | 0,1 | 2,1 | — | 0,0 |
| query | 4 | 4,7 s | 1,5 | 0,2 | 2,4 | — | 0,0 |
| query | 8 | 6,8 s | 2,2 | 0,6 | 3,1 | — | 0,0 |
| query | 16 | 11,4 s | **4,3** | **1,7** | **3,2** | — | 0,0 |
| analyze | 1 | 16,0 s | 1,0 | 0,3 | — | **13,3** | 0,9 |
| analyze | 4 | 12,1 s | 0,7 | 0,4 | — | **10,6** | 0,7 |
| analyze | 8 | 12,3 s | 0,8 | 0,4 | — | **21,0** | 0,7 |
| analyze | 16 | 32,7 s | **0,9** | 0,6 | — | **45,4** | 0,7 |

Trois lectures, et aucune n'a besoin d'un raisonnement :

- **`predict`** : rien ne grandit. Le seul nœud LLM passe de 1,6 à 1,8 s entre
  un utilisateur et seize. Il n'y a pas de goulot à ce palier.
- **`query`** : **tous** les nœuds qui parlent au modèle grandissent ensemble —
  `plan` ×3,1, `rappel` ×17, `retrieval` ×1,5 — et aucun ne domine. C'est la
  signature d'un serveur d'inférence qui répartit son débit entre ses clients.
- **`analyze`** : `plan` ne bouge pas (1,0 → 0,9 s) et `synthesize` non plus
  (0,9 → 0,7 s). **Seul `analysis` explose**, ×3,4 de 1 à 16 — et c'est le seul
  nœud qui ouvre un conteneur.

### 3.5 Le goulot de `analyze` : le sémaphore du bac à sable

Le réglage qui borne `analysis` est `DAA_SANDBOX_MAX_SESSIONS=4` : quatre
conteneurs vivants au plus, les suivants attendent une place, et au bout de
`sandbox_queue_timeout=60 s` ils sont refusés. Trois confirmations
indépendantes :

- la durée du nœud `analysis` décroche **exactement entre N=4 et N=8** — 10,6 s
  puis 21,0 s — c'est-à-dire au palier où le nombre de demandeurs dépasse le
  nombre de places ;
- le débit plafonne au même endroit (18,6 → 20,8 → 21,9 req/min) alors que le
  nombre de demandeurs double deux fois ;
- un refus explicite apparaît à N=16 dans l'une des deux campagnes — « toutes les
  sandboxes sont occupées (4 en parallèle) : pas de place libérée en 60 s »
  (§6).

Et une confirmation par la négative : à N=16 sur `analyze`, la sonde du moteur
ne relève **jamais aucune requête en file**, là où `query` au même palier en
relève douze. Les analyses n'atteignent pas le moteur ensemble — elles attendent
avant, devant le sémaphore.

### 3.6 Le goulot de `query` : vLLM

La sonde le dit directement : `vllm:num_requests_waiting` vaut 0 jusqu'à N=4,
1 à N=8, puis **12 à N=16**. Le moteur met des requêtes en file, et c'est à ce
moment-là que le débit cesse de croître pendant que le p50 double.

**Pourquoi `predict` ne plafonne pas au même endroit** : un tour `predict` fait
un appel au modèle, un tour `query` en fait quatre à six — le planificateur, la
boucle de l'agent SQL, la reprise. Ce n'est pas le nombre de requêtes HTTP qui
sature vLLM, c'est le nombre de jetons, et `query` en consomme plusieurs fois
plus par réponse rendue.

### 3.7 Le résumé

| capacité | goulot | la mesure qui le désigne |
|---|---|---|
| `predict` | **aucun à N=16** | 410 req/min ; le nœud `plan` passe de 1,6 à 1,8 s ; rien en file chez le moteur |
| `query` | **vLLM** | tous les nœuds LLM grandissent ensemble ; `num_requests_waiting` = 12 à N=16 |
| `analyze` | **le sémaphore du bac à sable (4)** | seul `analysis` grandit, et il décroche entre N=4 et N=8 ; débit plat ; refus explicite à N=16 |

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
une saturation — une saturation aplatit une courbe, elle ne la renverse pas, et
c'est bien ce qu'on observe partout ailleurs dans ce document. La sonde du moteur
disait d'ailleurs que vLLM n'était pas en cause : onze requêtes en vol, **une
seule en file**, là où les campagnes d'après correction en mettent cinq puis
douze au même palier.

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

Deux campagnes **appariées** : même banc, même machine, même moteur, mêmes
paliers, même délai d'abandon (300 s), à une heure d'intervalle. Ce sont les
seuls chiffres de ce document qu'on peut soustraire l'un à l'autre — ceux du §2
viennent d'une troisième campagne, plus tardive, et n'ont pas à être comparés à
ceux-ci (cf. §2.2).

| capacité | N | débit AVANT | débit APRÈS | abouties AVANT | abouties APRÈS |
|---|---|---|---|---|---|
| query | 4 | 55,1 | **74,0** | 12/12 | 12/12 |
| query | 8 | 86,6 | **109,0** | 23/24 | **24/24** |
| query | 16 | 7,3 | **118,7** | 38/48 | **48/48** |
| analyze | 4 | 16,9 | 18,5 | 11/12 | **12/12** |
| analyze | 8 | 23,6 | 23,2 | 22/24 | **24/24** |
| analyze | 16 | 6,2 | **22,7** | 38/48 | **47/48** |
| predict | 16 | 341,5 | **411,0** | 47/48 | **48/48** |

Sur la campagne entière — 279 tours — les échecs passent de **vingt-cinq, de
trois natures** (254 tours aboutis) à **un seul** (278 aboutis), et ce dernier
est un refus voulu (§6).

Une conséquence qu'il faut dire : ce n'est qu'**après** la correction que le
goulot réel devient lisible. Avant, `vllm:num_requests_waiting` ne dépassait
jamais 1, parce que les requêtes se perdaient en réessais au lieu d'atteindre le
moteur. Après, il monte à 5 puis à 12 selon la campagne — et le §3.6 devient
démontrable. Un défaut de concurrence ne cache pas seulement une panne : il cache
la mesure qu'on était venu faire.

---

## 5. Le cloisonnement sous charge

C'est la propriété la plus chère du produit, et elle n'avait été éprouvée qu'au
repos : Alice puis Bob, chacun son tour ([`tests/unit/api/test_cloisonnement.py`](../tests/unit/api/test_cloisonnement.py)).

### 5.1 Le protocole

Seize utilisateurs, seize comptes, seize sessions, seize sources, **seize jetons
distincts**. La campagne complète mène 279 tours. Ensuite, cinq vérifications —
et la **première ne porte pas sur le cloisonnement mais sur le protocole**. Les
chiffres ci-dessous sont ceux de la campagne de référence ; les **trois**
campagnes complètes menées ce jour-là rendent exactement le même verdict, avant
comme après la correction du §4 :

| vérification | ce qu'elle interdit | résultat |
|---|---|---|
| **le canari est actif** | qu'« aucune fuite » soit vrai parce que personne n'a rien lu | **TENU** — 16/16 ont reçu leur propre jeton |
| aucune réponse ne porte le jeton d'un autre | une donnée d'autrui dans ma réponse | **TENU** — 279 réponses relues intégralement (2 147 Kio) |
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

Deux campagnes complètes après correction, 279 tours chacune. **Une seule erreur
sur les 558**, et elle n'est pas une panne :

| nature | palier d'apparition | message rendu |
|---|---|---|
| **plafond de sandbox** | `analyze`, **N=16** — 1 tour sur 48 dans une campagne, 0 sur 48 dans l'autre | « toutes les sandboxes sont occupées (4 en parallèle) : pas de place libérée en 60 s » |

C'est le comportement **voulu** de `DAA_SANDBOX_MAX_SESSIONS=4` avec
`sandbox_queue_timeout=60 s` : au-delà du plafond, une session attend son tour,
et au bout d'une minute elle est refusée explicitement plutôt que laissée en
attente indéfinie.

Qu'il apparaisse dans une campagne et pas dans l'autre n'est pas une
contradiction : à N=16, le nœud `analysis` dure 45 s au p50 et son p95 monte à
76 s, c'est-à-dire que l'attente d'une place frôle le délai de 60 s. **Le seuil
est là**, entre huit et seize analyses simultanées sur cette machine, et de part
et d'autre de ce seuil un refus est une affaire de quelques secondes.

Trois natures que le banc sait nommer ne se sont **pas** présentées, et
l'absence est un résultat :

| nature jamais vue | pourquoi |
|---|---|
| **limite de requêtes** (429) | le quota est de 20 questions par minute glissante et par compte ; à N=16, chaque utilisateur en pose trois par palier — le plafond du produit est atteint bien avant celui du compte |
| **refus d'authentification** | seize sessions ouvertes, aucune perdue malgré la réécriture du fichier de sessions à chaque requête |
| **verrou de conversation** | il n'apparaît jamais comme une erreur : il sérialise, il ne refuse pas (§5.3) |

### 6.2 Avant correction, pour mémoire

Vingt-cinq échecs sur 279 tours, de trois natures :

| nature | paliers | total |
|---|---|---|
| connexion au moteur (§4) | `query` 8 et 16, `analyze` 4, 8 et 16, `predict` 16 | 15 |
| délai du moteur | `analyze` 16 | 1 |
| abandon du banc à 300 s | `query` 16, `analyze` 16 | 9 |

L'« abandon du banc » n'est pas une panne du produit : c'est le banc qui renonce
au bout de son délai. Il est compté à part pour cette raison — confondre les deux
ferait porter à l'application une erreur qu'on a créée en fixant un chronomètre.
Les neuf abandons sont néanmoins une conséquence du §4 : les tours concernés
avaient dépassé cinq minutes en réessais.

---

## 7. Ce que le parallélisme du moteur apporte, mesuré

Le parallélisme est la raison d'avoir choisi ce serveur ([MOTEUR.md](MOTEUR.md)).
Personne ne l'avait chiffré sur le produit : les tableaux ci-dessous le font.

### 7.1 Sur le produit, à code identique

Même question, même banc, deux tours par utilisateur, un tour de chauffe jeté.
L'application ne nomme aucun moteur ([`llm.py`](../src/data_analyst_agent/llm.py)) :
seule l'URL désigne le serveur, et rien d'autre ne change entre les paliers.

| N utilisateurs | p50 | débit |
|---|---|---|
| 1 | 4,3 s | 13,1 req/min |
| 4 | 3,9 s | 42,6 req/min |

**Quatre analystes qui travaillent ensemble obtiennent 42,6 réponses par minute,
et leur p50 ne se dégrade pas** — 3,9 s à quatre contre 4,3 s tout seul. Le
débit est multiplié par 3,25 pour un quadruplement des demandeurs : c'est ce
qu'on attend d'un serveur qui traite les requêtes de front plutôt qu'en file.
Une mise en file, elle, aurait rendu le p50 à quatre utilisateurs quatre fois
plus grand.

La ventilation par nœud dit que le gain est réparti, et non concentré sur un
nœud rapide qui masquerait le reste :

| N | `plan` | `rappel` | `retrieval` | `system` |
|---|---|---|---|---|
| 4 | 1,2 s | 0,7 s | 2,4 s | 0,2 s |

### 7.2 Sur le moteur seul

K requêtes simultanées de **prompts distincts** — un prompt répété serait servi
par le cache de préfixe et gonflerait le parallélisme apparent — 200 jetons
chacune, un tir de chauffe jeté :

| K simultanées | temps total | débit |
|---|---|---|
| 1 | 3,8 s | 0,26 req/s |
| 2 | 3,4 s | 0,59 req/s |
| 4 | 5,2 s | 0,78 req/s |
| 8 | **5,6 s** | **1,44 req/s** |

Huit requêtes rendues en 5,6 s là où une seule en prend 3,8 : **+47 % de temps
pour huit fois le travail**, et un débit multiplié par 5,5 entre K=1 et K=8.
C'est le *continuous batching* qu'on voit ici, et c'est la propriété sur
laquelle repose le dimensionnement du §2.

**Ce que ces chiffres ne disent pas.** Ils valent pour ce modèle, cette
quantification et cette carte. Un changement de `--max-model-len` déplace le
nombre de requêtes que le cache KV tient
([MOTEUR.md §7.6](MOTEUR.md#76-la-fenêtre-tenable-et-daa_context_model_window)),
donc le palier où ce débit s'effondre — qui n'est pas atteint ici. À refaire
après tout redimensionnement du serveur.

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
