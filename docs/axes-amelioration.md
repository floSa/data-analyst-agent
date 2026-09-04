# Axes d'amélioration — data-analyst-agent

Chaque point est ancré dans le code (`fichier:ligne`) avec une correction proposée.

Ce document recense ce qui a été **vu, mesuré et délibérément laissé** pendant le
chantier de durcissement de septembre 2026. Les dix-sept tâches de
[l'audit](AUDIT-2026-09.md) sont traitées ; ce qui suit est ce qui a été relevé en
chemin sans entrer dans leur périmètre. Aucun point n'est spéculatif : chacun a été
constaté sur le code ou mesuré à l'exécution.

---

## Sécurité

### L'anti-force brute compte par adresse, et l'adresse disparaît derrière un frontal

- **Où** : [`api/app.py:264`](../src/data_analyst_agent/api/app.py)
- **Constat** :
  ```python
  adresse = request.client.host if request.client else "inconnue"
  ```
- **Problème** : derrière un reverse proxy, `request.client.host` est l'adresse du
  proxy — la même pour tout le monde. Cinq échecs de connexion, de qui que ce soit,
  verrouillent alors **tous** les visiteurs. `X-Forwarded-For` n'est délibérément pas
  lu : il est forgeable par le client, et le croire aveuglément serait pire que le
  défaut actuel.
- **Correction proposée** : un réglage de proxys de confiance (`DAA_TRUSTED_PROXIES`),
  et ne lire `X-Forwarded-For` que si la connexion vient de l'un d'eux.
- **Statut** : Ouvert — sans effet tant que le service n'est pas derrière un frontal.

### Le plafond de corps ne voit pas une requête en `chunked`

- **Où** : [`api/app.py:229`](../src/data_analyst_agent/api/app.py)
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

### « De quels attributs as-tu besoin ? » n'a pas de route

- **Où** : [`orchestrator/graph.py:703`](../src/data_analyst_agent/orchestrator/graph.py)
  (`_regle_choisir_le_modele`)
- **Problème** : la question tombe dans le repli « je n'ai pas bien compris », alors que
  la liste des features est **déjà** dans le prompt du planificateur. La cause n'est pas
  le prompt : `Capability` n'a pas de valeur pour « décris-moi le modèle », donc la
  demande ne peut littéralement pas être routée. C'est le premier tour d'une
  conversation de prédiction sur deux.
- **Correction proposée** : dans `_regle_choisir_le_modele`, traiter le cas « le message
  demande les features attendues » **avant** le cas « prédiction sans dataset », et
  répondre par `describe_features(SCHEMAS[dataset])`. Cette règle est la bonne place :
  elle voit déjà `self.registry.datasets` et sait déjà formuler une question de
  désambiguïsation.
- **Statut** : Ouvert — point d'atterrissage identifié, correction non écrite.

### Deux politiques différentes face à un fichier corrompu

- **Où** : [`orchestrator/workspace.py:471`](../src/data_analyst_agent/orchestrator/workspace.py)
  (`_load`) contre [`orchestrator/conversations.py:165`](../src/data_analyst_agent/orchestrator/conversations.py) (`load`)
- **Problème** : `ConversationStore.load()` tolère un fichier illisible et rend `None` ;
  `_load()` du manifeste ne le tolère pas et lève. Depuis le passage aux écritures
  atomiques, la corruption ne peut plus venir d'une interruption — mais elle peut venir
  d'un disque, d'une restauration partielle ou d'une édition à la main.
- **Correction proposée** : aligner sur la politique tolérante, et signaler la perte
  plutôt que de la taire.
- **Statut** : Ouvert.

---

## Performance

### `list()` ouvre et valide tous les fils pour n'en rendre qu'un résumé

- **Où** : [`orchestrator/conversations.py:168`](../src/data_analyst_agent/orchestrator/conversations.py)
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

- **Où** : [`orchestrator/workspace.py:374`](../src/data_analyst_agent/orchestrator/workspace.py)
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

### Seul le prompt du planificateur est budgété

- **Où** : [`orchestrator/graph.py:534`](../src/data_analyst_agent/orchestrator/graph.py)
  (`_peser_le_prompt`)
- **Problème** : l'agent SQL, l'agent d'analyse et la synthèse ne décomptent aucun budget.
  Ils restent bornés par leurs limites d'allers-retours, mais un dépassement chez eux
  n'est vu qu'au retour — ou par un refus explicite du serveur.
- **Correction proposée** : remonter le décompte dans le garde-fou commun aux nœuds,
  plutôt que dans le seul nœud de planification.
- **Statut** : Ouvert.

### `safe_dir_name` distingue la casse

- **Où** : [`orchestrator/workspace.py:275`](../src/data_analyst_agent/orchestrator/workspace.py)
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
- **Problème** : les quatre prompts sont éditables sans toucher au code, mais ils vivent
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
| Bascule vers vLLM | Mécanisme prouvé et mesuré ([VLLM.md](VLLM.md)), moteur **toujours Ollama** | Valider le modèle de production, qui ne tient pas sur la L4 en même temps qu'Ollama |
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
| P1 | Anti-force brute par adresse derrière un frontal | Ouvert | Un échec quelconque verrouille tous les comptes |
| P1 | « De quels attributs as-tu besoin ? » sans route | Ouvert | Un tour de prédiction sur deux part en clarification inutile |
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
