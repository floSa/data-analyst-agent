# Spike Vanna — texte-vers-SQL, comparé au socle maison (roadmap §11, étape 9)

> **Ce document est un journal de chantier**, daté de septembre 2026 : la comparaison qui a conclu à garder le socle maison. Index : [README.md](README.md).

> **Révision du 8 septembre 2026.** La première version de ce document concluait au
> rejet de Vanna sur la foi d'une chronologie fausse et d'une version obsolète. Elle est
> corrigée ci-dessous. Les mesures de 2026-07-09 sont conservées telles quelles ; c'est
> leur portée qui était surestimée.

## 1. Ce que le spike a réellement mesuré

Date : 2026-07-09. LLM identique des deux côtés : `qwen3-coder:30b` servi par
l'ancien moteur, température 0. Schéma identique aux tests : Titanic
multi-tables (`passengers` + `classes`, clé étrangère). Vanna **0.7.9** +
ChromaDB local + le connecteur de ce moteur.
Entraînement minimal : les 2 DDL + une phrase de documentation.

| Question | SQL | Valeur | Verdict |
|---|---|---|---|
| % de femmes de 1ʳᵉ classe survivantes (golden n°1) | jointure correcte + CASE | 96,8085 (= oracle pandas 96,81) | correct (21 s, chargement modèle compris) |
| Passagers embarqués à Cherbourg | filtre simple | 168 | correct (1 s) |
| Âge moyen des survivants par classe | jointure + GROUP BY | 35,4 / 25,9 / 20,6 | correct (5 s) |

Le socle maison (tools `get_schema` / `run_sql` + auto-correction) produit des requêtes
de même qualité avec le même LLM. **Sur ce périmètre, aucun écart de qualité SQL.**
Ce résultat tient toujours.

## 2. Les deux défauts de méthode

### 2.1 Le spike est arrivé après la décision

Le cahier des charges [CADRAGE.md](../CADRAGE.md) §8 fixait « Text-to-SQL (socle) = tools
maison + SQLAlchemy » dès le **2ᵉ commit du dépôt** (`da77571`), et reléguait Vanna au
rang de « spike comparatif ». Dans l'ordre réel des commits :

| Rang | Commit | Contenu |
|---|---|---|
| 2 | `da77571` | cahier des charges — le socle maison est déjà écrit comme choix |
| 10 | `f3dd981` | agent de récupération maison, catalogue, SQL, DuckDB |
| 14 | `6f66bd1` | orchestrateur LangGraph |
| **18** | `e342a9c` | **spike Vanna** |

La comparaison a été conduite alors que la solution maison existait et fonctionnait.
Elle ne pouvait produire qu'un verdict de confirmation. Ce n'était pas un arbitrage
« reprendre ou construire » : c'était une justification a posteriori.

### 2.2 Le spike a testé une version périmée depuis huit mois

Dates réelles de publication (PyPI) et du dépôt :

| Événement | Date |
|---|---|
| Vanna 0.7.9 — dernière de la lignée RAG | **10 avril 2025** |
| Vanna 2.0.0 — réécriture en framework d'agents | **19 novembre 2025** |
| Vanna 2.0.2 — dernière version publiée | **2 février 2026** |
| Archivage du dépôt `vanna-ai/vanna` | **29 mars 2026** |
| Notre spike | **9 juillet 2026** |

La version courante au moment du spike était la **2.0.2**, vieille de cinq mois. Nous
avons testé la 0.7.9, vieille de quinze mois et remplacée depuis huit.

La version précédente de ce document écrivait : « La version 2.x publiée *après*
l'archivage du dépôt ». **C'est faux** : la 2.x précède l'archivage de quatre mois.
C'est cette phrase erronée qui a écarté la 2.x sans l'ouvrir.

## 3. Relecture du code de Vanna 2.0.2

Dépôt archivé cloné et lu le 2026-09-08 (36 000 lignes Python, licence MIT).

### 3.1 Frictions du spike qui ne s'appliquent plus

| Grief porté contre 0.7.9 | Vrai en 2.0.2 ? |
|---|---|
| ChromaDB obligatoire, ~80 Mo d'embedding ONNX au 1ᵉʳ lancement | **Non.** `integrations/local/agent_memory/in_memory.py` calcule la similarité par Jaccard + `difflib`, sans aucun embedding ni base vectorielle. |
| `chroma-hnswlib` à compiler en C++ sous Windows | **Non.** ChromaDB est un extra parmi neuf backends, tous optionnels. |
| Connecteurs de premier niveau (`vanna.chromadb`, celui du moteur) disparus | **Inexact.** Ils sont passés sous `vanna.integrations.*`, et `legacy/adapter.py` (463 lignes) existe précisément pour envelopper une instance 0.x. |
| Dépendances lourdes | **Non.** Le cœur ne demande que pydantic, pandas, httpx, SQLAlchemy, sqlparse, plotly, click, PyYAML. |

À noter au passage : `vllm` est un extra déclaré du paquet — notre moteur y est
supporté nativement.

### 3.2 Griefs qui restent vrais

| Grief | Vérification |
|---|---|
| Aucun garde-fou lecture seule | **Confirmé.** `RunSqlToolArgs` n'est qu'un `sql: str`, et `tools/run_sql.py:128` traite explicitement le cas « non-SELECT queries (INSERT, UPDATE, DELETE, etc.) » en comptant les lignes affectées. Vanna 2.0 exécute les écritures. Notre `assert_read_only` n'a pas d'équivalent. |
| Upstream mort | **Confirmé.** Archivé le 29 mars 2026, soit trois mois *avant* l'ouverture de notre projet. Aucun successeur : les 24 dépôts de l'organisation sont à l'abandon, la dernière activité (`ont-run`) est un framework web sans rapport. |
| Périmètre partiel | **Confirmé.** Vanna couvre le texte-vers-SQL et la restitution. L'analyse (génération de code + bac à sable Docker) et l'inférence gardée (schémas Pydantic, slot-filling, registry) sont hors de son champ. |

### 3.3 Ce que la publicité annonce et que le code ne livre pas

- **« Row-level security »** : ce n'est pas une implémentation, c'est un point
  d'extension. `ToolRegistry.transform_args` laisse réécrire le SQL selon
  l'utilisateur, avec un exemple dans `examples/transform_args_example.py`. Le filtrage
  reste à écrire.
- **La couche utilisateur** est entièrement abstraite : `UserService`, `UserResolver`,
  `RequestContext`, `User` sont des interfaces. Ni hachage de mot de passe, ni sessions,
  ni CSRF, ni verrouillage après échecs. Notre C4 (argon2id, sessions serveur,
  double-submit CSRF, limitation par compte) n'était donc **pas** fourni par Vanna.
- **`core/filter`** n'est pas un filtre de sécurité mais un filtre d'historique de
  conversation pour tenir la fenêtre de contexte — l'équivalent de notre C6.

## 4. Décision révisée

**Le socle maison reste la brique texte-vers-SQL du produit** — mais pour une seule
raison qui tienne : **l'upstream était déjà archivé au moment du choix**, sans
successeur, et adopter Vanna revenait à forker et maintenir 36 000 lignes de code tiers
sur le chemin critique du produit, pour un gain de qualité SQL mesuré à zéro.

Les autres motifs invoqués en juillet (frictions d'installation, dépendances lourdes,
connecteurs disparus) portaient sur une version que nous n'aurions de toute façon pas
utilisée. Ils ne doivent plus être cités.

Ce que le spike aurait dû faire et n'a pas fait : lire la 2.0.2, MIT et lisible, pour en
reprendre les motifs de conception au lieu de les réinventer. C'est encore possible —
un dépôt archivé sous licence MIT reste lisible et réutilisable.

## 5. À reprendre de Vanna 2.0

### 5.1 La mémoire d'usage des tools — la vraie brique manquante

`capabilities/agent_memory/` définit un contrat que nous pouvons transposer tel quel.
Sa forme est meilleure que l'idée que nous en avions gardée :

- Nous avions retenu « mémoriser des paires **question → SQL** ». Vanna mémorise des
  triplets **question → (nom du tool, arguments)**. Cela couvre nos trois capacités —
  `query`, `analyze`, `predict` — et pas seulement le SQL.
- `ToolMemory` porte `question`, `tool_name`, `args`, `timestamp`, `success`, `metadata`,
  et le champ `success` permet d'apprendre des échecs autant que des réussites.
- La recherche est paramétrée par `limit`, `similarity_threshold` (0,7 par défaut) et
  `tool_name_filter`.
- L'implémentation locale se passe d'embeddings (Jaccard + `difflib`) : démarrage
  possible sans base vectorielle, donc compatible on-prem réseau coupé dès le premier
  jour, avec `nomic-embed-text` en amélioration ultérieure et non en prérequis.

### 5.2 Le motif de routage — directement pertinent pour C13

La mémoire n'est pas injectée en RAG avant l'appel : elle est exposée **comme des tools
que le modèle appelle lui-même** (`SaveQuestionToolArgsTool`,
`SearchSavedCorrectToolUsesTool`, `tools/agent_memory.py`). C'est exactement la
correction que C13 apporte au lexique de mots-clés : laisser le modèle décider quand
consulter, plutôt que déclencher sur des chaînes de caractères.

### 5.3 Le contrat de restitution

`ToolResult` sépare `result_for_llm` (le texte rendu au modèle) de `ui_component`
(ce que voit l'utilisateur), avec une variante riche et une variante simple. La séquence
SQL → table → graphe → résumé est un bon modèle pour notre surface conversationnelle.

### 5.4 La journalisation d'audit

`core/audit/base.py` distingue cinq événements — contrôle d'accès à un tool, invocation,
résultat, accès à une fonctionnalité d'interface, réponse du modèle — avec un
assainissement des paramètres avant écriture. Nous n'avons rien de tel ; la
nomenclature est reprenable sans dépendance.

## 6. Ce qu'il faut retenir pour les prochains arbitrages

- Un spike qui arrive après le code ne compare rien : il confirme.
- Vérifier la version courante d'une brique avant de la juger, et dater les faits.
- Un dépôt archivé sous licence permissive n'est pas seulement un risque de dépendance :
  c'est aussi une source de conception, gratuite et sans engagement.
