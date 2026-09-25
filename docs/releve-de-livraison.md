# Relevé de livraison

**Date de la mesure** : 25 septembre 2026, de 07:38 à 08:47 (heure locale).
**Commit mesuré** : `f9c8cbb` — *fix(plan): un fil ne s'enrichit que d'une source
qu'une clé relie*, la tête de `main`, figée.
**Moteur** : `llm_base_url = http://localhost:8100/v1`,
`llm_model = google/gemma-4-E4B-it-qat-w4a16-ct` (vLLM). Un seul moteur, lu dans
le `.env` de l'installation et recopié dans l'arbre de travail.

Rien n'a été réparé. Aucun fichier de `src/`, `prompts/`, `tests/` ou `sources/`
n'a été touché : ce document est le seul ajout.

## Comment les campagnes ont été menées

Toutes **séquentielles**, jamais deux de front. Avant chaque campagne, aucun
autre processus de mesure ne tournait — le contrôle a été fait en excluant le
processus de contrôle lui-même, qui se compte sinon dans son propre `pgrep`.

**Le moteur a-t-il été partagé ?** Aucune autre mesure ne l'a sollicité. Le
service installé (`data-analyst-agent-app-1`), qui pointe sur le même moteur, n'a
journalisé dans la fenêtre que des sondes `/health` et cinq requêtes refusées en
401 : aucune conversation, donc aucun appel au moteur. La carte, elle, était
partagée avec une autre charge (4,4 Gio occupés par un processus étranger au
projet) : cela pèse sur les latences relevées, pas sur les verdicts.

**L'arbre de travail a reçu ce que git ne suit pas** avant toute mesure : le
`.env` de l'installation (recopié, jamais modifié, retiré à la fin) et les six
fichiers de données ignorés — `sources/demonstration/facturation.xlsx`,
`sources/demonstration/telemetrie.duckdb`, `sources/metier/iris.csv`,
`sources/metier/production.duckdb`, `sources/metier/stocks.xlsx`,
`sources/metier/titanic.csv`. Sans eux, la mesure aurait porté sur un catalogue
amputé sans le dire.

**Effet de bord constaté** : `DAA_WORKSPACE_DIR` venant du `.env` de
l'installation, les campagnes ont déposé 173 dossiers de fil dans
`/home/ubuntu/daa-workspaces-persist` (qui en comptait déjà 4 393). Ils n'ont pas
été supprimés : ce dossier appartient au service installé.

## Le tableau des campagnes

| campagne | catalogue | tirages | repère | ce tour |
|---|---|---|---|---|
| `mesure_surface_conversationnelle.py`, passe 1 | `sources/catalogue.yaml` | 1 | 43/44 | **44/44** (38/38 méta, 6/6 témoins) |
| `mesure_surface_conversationnelle.py`, passe 2 | `sources/catalogue.yaml` | 1 | 43/44 | **44/44** (38/38 méta, 6/6 témoins) |
| `mesure_parcours_de_demonstration.py` | `sources/demonstration/catalogue.yaml` | 3 | 144/144 | **127/144** |
| `mesure_questions_metier.py` | `sources/metier/catalogue.yaml` | 3 | 36/36 | **36/36** |
| `mesure_croisement_de_sources.py` | `sources/metier/catalogue.yaml` | 1 | 22/25 | **22/25** |
| `mesure_sources_nommees.py` | `sources/metier/catalogue.yaml` | 1 | 20/20 | **20/20** |
| `mesure_fils_de_prediction.py` | `sources/metier/catalogue.yaml` | 1 | 13/14 | **13/14** |
| `mesure_question_de_sens.py` | `sources/demonstration/catalogue.yaml` | 3 | 36/36 | **36/36** |
| `mesure_provenance_du_sens.py` | `sources/demonstration/catalogue.yaml` | 3 | 30/30 | **30/30** |
| `mesure_ouverture_de_source.py` | `sources/demonstration/catalogue.yaml` | 3 | 48/51 | **45/51** |
| `mesure_classement_sans_lexique.py` | `sources/demonstration/catalogue.yaml` | 1 | 10/10 | **7/10** |
| `mesure_choix_de_source.py` | `sources/catalogue.yaml` | 1 | 6/6 | **6/6** |
| `mesure_ambiguite_de_source.py` | `tests/catalogues/ambiguite/*.yaml` | 5 essais par ordre | 5/5 et 5/5 | **5/5 et 5/5** |
| `mesure_memoire_de_conversation.py` | `sources/catalogue.yaml` (fil A), `sources/demonstration/catalogue.yaml` (fils B à F) | 1 | un relevé, pas un score | **un relevé** |
| `uv run pytest -p no:randomly` | — | — | 1 631 verts, ≥ 85 % | **1 631 verts**, 16 désélectionnés, couverture **98,91 %** |
| `ruff check` | — | — | vert | **vert** |
| `ruff format --check` | — | — | vert | **vert** — 211 fichiers déjà formatés |

**Trois tirages de repère n'étaient pas ceux du tableau de commande.** Les
dénominateurs 36/36, 30/30 et 48/51 de `question_de_sens`, `provenance_du_sens`
et `ouverture_de_source` sont des chiffres à **3 tirages** (12, 10 et 17
questions). J'ai donc lancé ces trois campagnes à 3 tirages, et c'est ce que le
tableau rapporte. À 1 tirage, elles donnent respectivement **12/12**, **10/10**
et **15/17**.

**La suite pytest a été mesurée deux fois.** Le premier lancement, mené en
parallèle des campagnes, s'est bloqué une heure sans avancer, sur
`tests/unit/test_llm.py:141`
(`test_temoin_un_client_partage_entre_deux_boucles_finit_par_rompre`) : le fil
principal attend un `join` sur un thread arrêté devant une `threading.Barrier(2)`
que l'autre fil n'atteint jamais. Il a été tué. Relancé seul, machine au repos,
le même test passe et la suite rend 1 631 verts en 2 min 45. Le chiffre rapporté
est celui du lancement propre ; le blocage est noté parce qu'il est reproductible
sous charge.

## Les questions rouges

Chaque question rouge qui ne l'était pas au repère a été **rejouée 3 fois, seule**
— avec les tours de son fil quand l'oracle en dépend, faute de quoi le fil
n'aurait pas de source liée. Classement : *bascule* si elle passe au moins une
fois sur trois, *régression* si elle fait 0/3.

### Les régressions — dix, 0/3 chacune

#### 1. `parcours_de_demonstration` — `Q2 · courte`

- **Message** : « en 2025, combien en statut T ? »
- **Rendu** : « Je passe sur la source `referentiel` — on travaillait sur
  `exploitation`. 2 lignes retournées — voir le tableau ci-dessous. Ce qu'en dit
  le dictionnaire de `referentiel` : > `statut` : `ACT` (en service) ou `RET`
  (retiré)… » — attendu : 42 281.
- **Nœuds** : `system → rappel → plan → retrieval → synthesize`.
- **Dernier commit sur le chemin** (`src/data_analyst_agent/orchestrator/graph.py`,
  qui porte la bascule et `_perimetre_croise`) : `f9c8cbb` — *fix(plan): un fil ne
  s'enrichit que d'une source qu'une clé relie*.

#### 2. `parcours_de_demonstration` — `Q3 · courte`

- **Message** : « ça fait combien de kWh au total sur l'année ? »
- **Rendu** : « La somme totale de la puissance mesurée (en kilowatts) sur
  l'ensemble des relevés où la puissance est positive ou nulle est de 663504.99
  kWh. Données tronquées : telemetrie_releves_puissance coupée(s) à 10000
  lignes… » — attendu : 1 757 519,23.
- **Nœuds** : `system → rappel → plan → retrieval → synthesize`.
- **Dernier commit sur le chemin**
  (`src/data_analyst_agent/agents/retrieval/croisement.py`, qui charge chaque
  table du croisement par `SELECT * … max_rows`, et `graph.py`) : `f9c8cbb`.

#### 3. `parcours_de_demonstration` — `Q4 · courte`

- **Message** : « le top 3 des stations, code et nom »
- **Rendu** : « 3 lignes retournées, classées par `libelle_station` (ordre
  décroissant) — voir le tableau ci-dessous. » — attendu : 1015, 911, 872.
- **Nœuds** : `system → rappel → plan → retrieval → synthesize`.
- **Dernier commit sur le chemin** (`graph.py`) : `f9c8cbb`.

#### 4. `parcours_de_demonstration` — `verrou-source-liee · longue`

- **Message** : « Pourrais-tu me donner l'énergie totale, en kWh, pour la source
  sur laquelle nous travaillons actuellement ? »
- **Rendu** : « L'énergie totale enregistrée dans la table
  `exploitation_sessions` est de 357 602,18 kWh. **Calcul pour
  `telemetrie_releves_puissance`** Données tronquées : exploitation_sessions,
  telemetrie_releves_puissance coupée(s) à … » — attendu : 1 757 519,23.
- **Nœuds** : `system → plan → retrieval → synthesize`.
- **Dernier commit sur le chemin** (`croisement.py`, `graph.py`) : `f9c8cbb`.

#### 5. `ouverture_de_source` — `O01`

- **Message** : « on bosse sur exploitation aujourd'hui, tu peux me la sortir ? »
- **Rendu** : « Je travaille sur la source `exploitation`. Le SI d'exploitation du
  réseau de recharge est la source `exploitation` (postgres) qui contient les
  tables `stations`, `bornes`, `clients`… » — la source est bien liée, mais le
  compte attendu (6 tables) n'est pas dit ; l'accueil déterministe
  (`introspection.py`, « Entendu : on travaille sur **X** … n table(s) ») n'a pas
  rendu la phrase.
- **Nœuds** : `system → synthesize`.
- **Dernier commit sur le chemin** (`graph.py`, qui produit « Je travaille sur la
  source X ») : `f9c8cbb`. Sur `introspection.py` : `629d346` — *fix(plan): une
  prédiction relue, pour un témoin qui tenait à une phrase de trop*.

#### 6. `ouverture_de_source` — `V2-tient`

- **Message** : « quelle est l'énergie totale en kWh ? »
- **Rendu** : « L'énergie totale délivrée est de 357602.18 kWh, ce montant est
  calculé en sommant la colonne `energie_kwh` de la table
  `exploitation_sessions`. Données tronquées : exploitation_sessions … » —
  attendu : 1 757 519,23.
- **Nœuds** : `system → plan → retrieval → synthesize`.
- **Dernier commit sur le chemin** (`croisement.py`, `graph.py`) : `f9c8cbb`.

#### 7, 8, 9. `classement_sans_lexique` — `F04`, `F06`, `F09`

- **Messages** : « le top 3 des stations, avec leur code et leur nom » (`F04`),
  « classe-moi les stations par activité et garde les 3 premières, code et nom »
  (`F06`), « palmarès des stations : les 3 premières, leur code et leur nom »
  (`F09`).
- **Rendu** : les trois rendent la même requête —
  `SELECT code_station, libelle_station, nb_points FROM referentiel ORDER BY
  nb_points DESC LIMIT 3` — soit un classement sur `nb_points` de `referentiel`
  au lieu des comptes de sessions ; stations absentes : ST-097, ST-029, ST-016.
  Le SQL est en règle 3/3 : c'est la grandeur classée qui est fausse.
- **Nœuds** : le runner relève le SQL, pas les nœuds ; la source `referentiel`
  n'entre dans la portée que par le périmètre croisé.
- **Dernier commit sur le chemin** (`graph.py`, `_perimetre_croise`) : `f9c8cbb`.

#### 10. `croisement_de_sources` — `vel01-fabrique-vendu`

- **Message** : « pour le VEL-01, combien on en a fabriqué et combien on en a
  vendu ? »
- **Rendu** : « Le VEL-01 a été fabriqué 141 fois, mais seulement 123 unités ont
  été vendues (ce qui correspond au nombre de commandes non annulées). » —
  attendu : 689 fabriqués, 123 vendus. Le chiffre des ventes est juste, celui de
  la fabrication ne l'est pas.
- **Nœuds** : `system → plan → retrieval → synthesize`.
- **Dernier commit sur le chemin** (`croisement.py`, `graph.py`) : `f9c8cbb`.

### Les bascules — quatre

- `parcours_de_demonstration` — `Q3 · longue` (« Peux-tu me calculer l'énergie
  totale délivrée sur l'ensemble de l'année, exprimée en kWh ? ») : **1/3**.
- `parcours_de_demonstration` — `Q4 · longue` (« Pourrais-tu m'indiquer quelles
  sont les trois stations les plus sollicitées… ») : **2/3**.
- `parcours_de_demonstration` — `Q5 · longue` (« Peux-tu me donner le nombre de
  sessions pour chaque région, en les ordonnant… ») : **2/3**.
- `parcours_de_demonstration` — `verrou-source-liee · courte` (« ça fait combien
  de kWh là-dedans ? ») : **2/3**.

### Ce qui était déjà rouge au repère, et l'est resté

Ces questions ne sont pas rejouées : elles ne sont pas des rouges neuves.

- `croisement_de_sources` — `fabrique-vendu-stock` et `vendus-sans-fabriquer`,
  0/1 comme au repère (22/25, inchangé). En revanche `vend-plus-quon-produit`,
  rouge au repère, passe ce tour — et `vel01-fabrique-vendu`, vert au repère,
  tombe : le total est le même, la composition a changé.
- `fils_de_prediction` — `i-inventaire-du-fil`, 0/1, sur le même tour et la même
  cause qu'au repère : « qu'est-ce que tu as en mémoire dans cette conversation ? »
  ne nomme pas le tableau `resultat_1` du fil.

## Ce qui n'a pas tourné

Rien n'a été laissé de côté. Les treize campagnes, la suite pytest et les deux
contrôles `ruff` ont tous été menés, et les rejeux de classement aussi. Le budget
moteur consommé est d'environ 70 minutes, sous le plafond.

---

## Après le retrait de C67 et C68

**Date de la mesure** : 25 septembre 2026, de 09:15 à 09:50 (heure locale).
**Commits mesurés** : la tête de la branche de retrait —
`dfa4ef3` (*revert(plan): un fil lié ne s'enrichit plus de la source que la
relecture désigne*) sur `544ac7a` (*revert(plan): la preuve de clé retombe avec
l'enrichissement qu'elle gardait*), à quoi s'ajoutent les deux commits de
documentation. `src/`, `tests/` et `scripts/` y sont **identiques à `f78a97e`**
(C66), au caractère près.
**Moteur** : `llm_base_url = http://localhost:8100/v1`,
`llm_model = google/gemma-4-E4B-it-qat-w4a16-ct` (vLLM) — le même, lu dans le
`.env` de l'installation, recopié dans l'arbre de travail puis retiré.

**Les scores de `f9c8cbb` ci-dessus ne sont pas réécrits** : ce sont eux qui
justifient le retrait, et les effacer priverait de sa preuve la décision qu'ils
ont fondée.

Comme plus haut : campagnes **séquentielles**, jamais deux de front ; le catalogue,
`llm_base_url` et `llm_model` imprimés en tête de chaque relevé ; et l'arbre de
travail a d'abord reçu le `.env` et les six fichiers de données que git ignore.
La suite pytest a été lancée **seule**, aucune campagne en cours — la précaution
que C70 a payée d'une heure de blocage sur `tests/unit/test_llm.py:141`.

### Le tableau des campagnes

| campagne | catalogue | tirages | repère | ce tour |
|---|---|---|---|---|
| `mesure_parcours_de_demonstration.py`, 3 formulations | `sources/demonstration/catalogue.yaml` | 1 | 48/48 | **48/48**, 207 appels LLM |
| `mesure_classement_sans_lexique.py` | `sources/demonstration/catalogue.yaml` | 1 | 10/10 | **10/10** (SQL en règle 10/10), 61 appels |
| `mesure_ouverture_de_source.py` | `sources/demonstration/catalogue.yaml` | 1 | 15/17 | **16/17**, 56 appels |
| `mesure_questions_metier.py` | `sources/metier/catalogue.yaml` | 1 | 12/12 | **12/12**, 70 appels |
| `mesure_croisement_de_sources.py` | `sources/metier/catalogue.yaml` | 1 | 13/17 (repère de C66) | **13/17**, 108 appels |
| `mesure_sources_nommees.py` | `sources/metier/catalogue.yaml` | 3 (défaut du runner) | 20/20 | **60/60**, soit 20/20 par tirage |
| `mesure_choix_de_source.py` | `sources/catalogue.yaml` | 1 | 6/6 | **6/6**, 26 appels |
| `mesure_ambiguite_de_source.py` | `tests/catalogues/ambiguite/*.yaml` | 1 essai par ordre | 1/1 et 1/1 | **1/1 et 1/1** — proposition dans les deux ordres |
| `uv run pytest -p no:randomly` | — | — | verts, ≥ 85 % | **1 623 verts**, 16 désélectionnés, couverture **98,94 %**, 2 min 48 |
| `ruff check` | — | — | vert | **vert** |
| `ruff format --check` | — | — | vert | **vert** — 213 fichiers déjà formatés |

### Les dix régressions de `f9c8cbb` sont levées

Les dix questions rouges relevées plus haut repassent, et c'est le résultat de ce
tour :

- `parcours_de_demonstration` — `Q2`, `Q3`, `Q4`, `Q5` et `verrou-source-liee`
  rendent leurs chiffres dans les **trois** formulations. `Q3 · courte` (« ça fait
  combien de kWh au total sur l'année ? ») rend **1 757 519,23 kWh**, là où
  `f9c8cbb` servait 663 504,99 sur un croisement tronqué à 10 000 lignes. Plus
  aucune mention de données tronquées sur ces tours.
- `classement_sans_lexique` — `F04`, `F06` et `F09` classent sur
  `nombre_sessions` et non plus sur le `nb_points` de `referentiel` : **3/3**,
  et 10/10 sur la campagne entière.
- `ouverture_de_source` — `V2-tient` (« quelle est l'énergie totale en kWh ? »)
  rend 1 757 519,23 ; le volet `verrou` est **3/3**.
- `croisement_de_sources` — `vel01-fabrique-vendu`, rouge sur `f9c8cbb`, repasse.

Les quatre **bascules** de `f9c8cbb` (`Q3 · longue`, `Q4 · longue`, `Q5 · longue`,
`verrou-source-liee · courte`) sont vertes ce tour, dans leur formulation comme
dans les deux autres.

### Ce qui reste rouge — deux questions, 0/3 chacune

Rejouées trois fois, seules, comme les rouges de la première mesure.

#### `croisement_de_sources` — `ca-produit-vs-fabrique`, fil vierge

- **Message** : « compare le chiffre d'affaires par produit avec les quantités
  fabriquées »
- **Verdict** : **0/3** — le 461 attendu pour VEL-07 (fabrications, annulées
  exclues) n'est jamais rendu.
- **Ce n'est pas un effet du retrait.** Le total de la campagne, 13/17, est
  exactement le repère de C66 ; c'est sa **composition** qui a bougé d'un
  tirage : `vel01-fabrique-vendu` était rouge au repère et passe ce tour, celle-ci
  était verte et tombe. Le relevé de C66 note déjà que ces quatre rouges-là sont
  des tirages. Les trois autres — `fabrique-vendu-stock` (131 au lieu de 125,
  trois sources), `ca-produit-vs-fabrique-fil-lie` et `vendus-sans-fabriquer` —
  étaient rouges au repère et le restent.

#### `ouverture_de_source` — `O01`

- **Message** : « on bosse sur exploitation aujourd'hui, tu peux me la sortir ? »
- **Verdict** : **0/3** — la source est bien liée à `exploitation`, mais le
  compte de 6 tables n'est pas dit ; l'accueil déterministe
  (`introspection.py`, « Entendu : on travaille sur **X** … n table(s) ») ne rend
  pas la phrase, et le modèle formule à sa place.
- **La campagne est au-dessus de son repère** — 16/17 contre 15/17 à un tirage —
  donc deux questions étaient rouges au repère et une seule l'est ici. Cette
  question est classée régression par la règle du rejeu, et la règle est
  appliquée telle quelle ; mais `O01` ne peut pas être une rouge neuve dans une
  campagne qui gagne un point. Le savoir demanderait de rejouer `f78a97e`, ce qui
  n'a pas été fait.

### Ce qui n'a pas tourné

Rien n'a été laissé de côté. Les huit campagnes du tableau de commande, les deux
rejeux, la suite pytest et les deux contrôles `ruff` ont tous été menés. Le budget
moteur consommé est d'environ **35 minutes**, sous les 50 visées.

Aucun fichier de `src/`, `scripts/`, `tests/` ou `sources/` n'a été réparé : les
seuls changements de cette branche sont les deux retraits et la documentation
qu'ils rendent due.
