# Migration Ollama → vLLM : ce qui est mesuré, et ce qui reste à faire

Banc d'essai du 2026-09-03, sur la machine de dev (NVIDIA L4 23 034 Mio, vLLM
`0.28.0`, image `vllm/vllm-openai:latest`). Il répond à la tâche 9 du backlog
([AUDIT-2026-09 §7](AUDIT-2026-09.md)) : **valider le mécanisme de tool calling
de vLLM**, seul vrai risque de la migration.

Ce document sépare ce qui a été **constaté** (sorties réelles, citées) de ce qui
en est **déduit**. Les déductions sont annoncées comme telles.

> **Ollama reste le moteur en service.** Rien ici ne bascule la configuration du
> projet. Le conteneur vLLM du banc a été supprimé à la fin ; le modèle qu'il
> servait n'entre nulle part dans la configuration de l'application.

## 1. Pourquoi c'est le tool calling qui décide

Le système en dépend en **deux** endroits, et pas un seul :

- **le planificateur** — `output_type=Plan` est réalisé par pydantic-ai via un
  appel d'outil (`final_result`), avec `tool_choice="required"` ;
- **l'agent SQL** — trois tools (`list_tables`, `get_schema`, `run_sql`), avec
  `tool_choice="auto"`.

Un serveur peut très bien produire du texte impeccable sans émettre le moindre
`tool_calls` structuré. Dans ce cas le planificateur ne rend pas de `Plan` et
l'agent SQL laisse `grounded` à faux — l'utilisateur lit « Je n'ai pas
interrogé la source ». D'où un banc d'essai, `scripts/vllm_bench.py`, qui
mesure les deux au lieu de les supposer.

## 2. La commande qui a marché

```bash
docker run -d --name vllm-bench --gpus all \
  -v /home/ubuntu/.cache/huggingface:/root/.cache/huggingface \
  -p 8000:8000 --ipc=host \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-7B-Instruct-AWQ \
  --gpu-memory-utilization 0.45 \
  --max-model-len 32768 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

Puis, depuis la racine du dépôt :

```bash
uv run python scripts/vllm_bench.py --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-7B-Instruct-AWQ
```

**Le modèle du banc n'est pas le modèle du projet.** vLLM sert des dépôts
Hugging Face, pas des étiquettes Ollama : il lui fallait un modèle à lui.
`Qwen/Qwen2.5-7B-Instruct-AWQ` a été choisi sur deux critères, et deux
seulement — il tient dans la mémoire GPU laissée libre (5,58 Gio de poids), et
vLLM a un analyseur adapté à sa famille (`hermes`). Il disparaît avec le
conteneur.

Les analyseurs disponibles dans cette image se lisent ainsi :

```bash
docker run --rm --entrypoint python3 vllm/vllm-openai:latest -c \
  "import re,pathlib; s=pathlib.Path('/usr/local/lib/python3.12/dist-packages/vllm/tool_parsers/__init__.py').read_text(); \
   i=s.index('_TOOL_PARSERS_TO_REGISTER'); print(sorted(re.findall(r'^    \"([a-z0-9_]+)\":', s[i:], re.M)))"
```

47 analyseurs en `0.28.0`, dont `gemma4`, `hermes`, `qwen3_coder`, `qwen3_xml`,
`llama3_json`, `mistral`, `granite`, `deepseek_v3`, `pythonic`. **Vérifier que
la famille du modèle visé y figure AVANT de télécharger quoi que ce soit** :
sans analyseur, on conclut à tort que le mécanisme ne marche pas.

## 3. Les mesures

### 3.1 Le planificateur rend-il un `Plan` structuré ?

**Oui, avec les options — non, sans elles.** C'est la comparaison qui fait la
preuve : même modèle, même question, même banc, seules les deux options
changent.

| Serveur | `--enable-auto-tool-choice --tool-call-parser hermes` | Résultat |
|---|---|---|
| vLLM, Qwen2.5-1.5B-Instruct | **non** | HTTP 400 |
| vLLM, Qwen2.5-1.5B-Instruct | **oui** | `Plan(capability='analyze', dataset='iris', …)` |
| vLLM, Qwen2.5-7B-Instruct-AWQ | **oui** | `Plan(capability='analyze', dataset='iris', data_question='SELECT COUNT(*), species FROM iris GROUP BY species')` |
| Ollama, gemma4:e4b (en service) | sans objet | `Plan(capability='query', source='iris', …)` |

Sans les options, le refus est immédiat et explicite :

```
pydantic_ai.exceptions.ModelHTTPError: status_code: 400, model_name: Qwen/Qwen2.5-1.5B-Instruct,
body: {'message': 'tool_choice="required" requires --tool-call-parser to be set', …}
```

**Correction d'une prévision de l'audit.** [§4.3](AUDIT-2026-09.md) déduisait
que le planificateur lèverait `UnexpectedModelBehavior` et tomberait dans le
repli, donc une demande de clarification à chaque question. **Constaté : c'est
un `ModelHTTPError` 400.** Il ne passe donc pas par le repli de `_plan_node`
mais par `_guarded` ([graph.py:321](../src/data_analyst_agent/orchestrator/graph.py#L321)),
et l'utilisateur reçoit le message d'erreur brut du serveur — options de
lancement de vLLM comprises. Le symptôme est plus bruyant que prévu, et la
cause y est écrite : c'est plutôt une bonne nouvelle pour le diagnostic, une
mauvaise pour ce qui fuit à l'utilisateur (tâche 10 du backlog).

### 3.2 L'agent SQL appelle-t-il ses trois tools ? `grounded` est-il vrai ?

**Oui, avec les options et un modèle capable.**

| Serveur / modèle | Options | Tools appelés | `grounded` |
|---|---|---|---|
| vLLM, Qwen2.5-1.5B-Instruct | non | — (HTTP 400) | faux |
| vLLM, Qwen2.5-1.5B-Instruct | oui | **aucun** | **faux** |
| vLLM, Qwen2.5-7B-Instruct-AWQ | oui | `get_schema`, `run_sql` | **vrai** |
| Ollama, gemma4:e4b (en service) | sans objet | `get_schema`, `run_sql` | vrai |

Sans les options, vLLM refuse d'emblée, avec un message différent de celui du
planificateur — la nuance `auto` / `required` compte :

```
body: {'message': '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set', …}
```

Avec les options et le 7B, la réponse est fondée et juste :

```
tools appelés  : ['get_schema', 'run_sql']
grounded       : True
SQL exécuté    : SELECT species, COUNT(*) FROM iris GROUP BY species
synthèse       : Il y a 50 fleurs pour chaque espèce : virginica, versicolor et setosa.
```

**La ligne 1.5B mérite d'être lue pour ce qu'elle est** : options posées,
serveur prêt, et pourtant aucun tool. Ce n'est **pas** une panne du mécanisme
mais une insuffisance du modèle — vérifié en isolant les deux, sur ce même
serveur 1.5B :

- `tool_choice: "auto"` → réponse en texte libre, `finish_reason: "stop"`,
  aucun `tool_calls` ;
- `tool_choice: "required"` → `tool_calls: [{function: {name: "get_schema",
  arguments: "{}"}}]`.

Le serveur SAIT émettre un appel d'outil ; à 1,5 B, le modèle ne décide pas
d'en émettre un quand on lui laisse le choix. D'où l'importance de ne pas
valider un mécanisme sur un modèle trop petit : c'est exactement le faux
négatif que ce banc devait éviter.

### 3.3 Dépassement de contexte : refus explicite ou troncature silencieuse ?

**Constaté, sur le même banc, même prompt (~40 000 tokens envoyés) :**

| Serveur | Réponse |
|---|---|
| vLLM (`--max-model-len 32768`) | **HTTP 400**, prompt rejeté |
| Ollama (`OLLAMA_CONTEXT_LENGTH=32768`) | **200**, `prompt_eval_count = 32767`, répond quand même |

Corps d'erreur réel de vLLM :

```json
{"message": "This model's maximum context length is 32768 tokens. However, you requested 0 output tokens and your prompt contains at least 32769 input tokens, for a total of at least 32769 tokens. Please reduce the length of the input prompt or the number of requested output tokens. (parameter=input_tokens, value=32769)",
 "type": "BadRequestError", "param": "input_tokens", "code": 400}
```

**`is_context_refusal()` le reconnaît.** Écrite au commit `aadc160` sans
serveur en face, elle passe sa première confrontation au réel **sans
modification** : le motif `maximum context length` et le statut 400 suffisent.

Et elle ne se déclenche pas à tort sur les deux autres 400 rencontrés ici — les
refus de tool calling —, vérifié en lui repassant leurs corps exacts :

```
False <- "auto" tool choice requires --enable-auto-tool-choice and --
False <- tool_choice="required" requires --tool-call-parser to be set
True  <- This model's maximum context length is 32768 tokens. However…
```

Ce qui, en service, transforme un « `ModelHTTPError: …` » illisible en
« Contexte refusé par le serveur : la requête dépasse la fenêtre du modèle… ».

Côté Ollama, la troncature silencieuse est reconfirmée au passage : 39 993
tokens envoyés, 32 767 déclarés évalués, et une réponse rendue comme si de rien
n'était. C'est ce que `detect_overflow()` sert à constater.

### 3.4 Ce que `--max-model-len` tient réellement

La L4 fait 23 034 Mio ; `ollama-central` en occupait **4 902 Mio** pendant tout
le banc, laissant **17,05 Gio libres** (mesure de vLLM au démarrage).

Avec `Qwen/Qwen2.5-7B-Instruct-AWQ` (poids + hors-torch : 5,58 Gio ; pic
d'activation : 1,04 Gio ; CUDAGraph : 0,53 Gio) :

| `--gpu-memory-utilization` | Budget | Cache KV | Tokens en cache | Requêtes concurrentes à 32 768 tokens |
|---|---|---|---|---|
| 0,35 | 8,06 Gio | 1,1 Gio | — | **refus au démarrage** |
| 0,45 | 9,92 Gio | 3,3 Gio | 61 776 | 1,89 × |
| 0,60 | 13,22 Gio | 6,6 Gio | 123 664 | 3,77 × |
| *plein* (annoncé par vLLM) | 17,05 Gio | 9,76 Gio | — | — |

À `0,35`, vLLM **ne démarre pas** — et dit précisément ce qui tiendrait :

```
ValueError: To serve at least one request with the model's max seq len (32768),
(1.75 GiB KV cache is needed, which is larger than the available KV cache memory (1.1 GiB).
Based on the available memory, the estimated maximum model length is 20512.
```

Deux constats qui comptent pour la migration :

1. **vLLM échoue au démarrage, pas en production.** Une fenêtre qui ne tient
   pas se voit tout de suite, avec la valeur qui tiendrait. C'est très
   au-dessus du silence d'Ollama.
2. **Le cache KV est préalloué et ne revient pas.** Le conteneur a tenu
   13 754 Mio à `0,60` du début à la fin, sans rien rendre. Là où Ollama
   charge/décharge selon `OLLAMA_KEEP_ALIVE`, vLLM réserve.

**Déduit, non mesuré** : ces chiffres valent pour un 7 B quantifié AWQ. Un
`qwen3-coder:30b` Q4 (~19 Gio de poids, CADRAGE §5) ne laisserait presque rien
pour le cache KV sur cette même carte — la fenêtre tenable serait à mesurer
modèle en main, et le tableau ci-dessus ne s'y extrapole pas.

## 4. Ce qui casse sans les bonnes options — résumé

| Ce qu'on oublie | Ce qui se passe |
|---|---|
| `--tool-call-parser` | HTTP 400 `tool_choice="required" requires --tool-call-parser to be set` → **le planificateur ne marche plus du tout** |
| `--enable-auto-tool-choice` | HTTP 400 `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set` → **l'agent SQL ne marche plus du tout** |
| un analyseur inadapté à la famille du modèle | *non mesuré ici* — les appels d'outil ne seraient pas extraits du texte généré |
| `--max-model-len` trop grand pour la mémoire libre | vLLM refuse de démarrer, et annonce la longueur tenable |
| `--api-key` côté serveur, sans `DAA_LLM_API_KEY` côté application | rejet à chaque requête, faute d'en-tête `Authorization` (le champ existe depuis la tâche 8) |

## 5. Ce qui reste à vérifier avant une bascule réelle

Rien de ce qui suit n'a été mesuré ; ce sont les trous connus.

1. **Le modèle réellement servi.** Le banc valide le mécanisme, pas un modèle.
   Il faut refaire passer les trois épreuves sur le modèle qui sera servi en
   production, avec l'analyseur de SA famille, et vérifier que l'agent SQL
   appelle bien ses tools en `tool_choice="auto"` — c'est là que le 1,5 B a
   échoué alors que le mécanisme, lui, fonctionnait.
2. **`DAA_LLM_MODEL` devient un identifiant de dépôt** (`Qwen/…`), plus une
   étiquette `nom:tag`. Un serveur vLLM sert **un** modèle, fixé au lancement :
   pas d'équivalent de `ollama pull`.
3. **La fenêtre tenable pour ce modèle-là**, et l'accord de
   `DAA_CONTEXT_MODEL_WINDOW` avec le `--max-model-len` retenu — sans quoi la
   détection de débordement mesure une fenêtre qui n'est pas celle servie.
4. **Le message d'erreur rendu à l'utilisateur.** Un 400 de tool calling arrive
   aujourd'hui brut, options de lancement comprises (§3.1). C'est la tâche 10
   du backlog, et la migration la rend plus visible.
5. **La concurrence**, qui est le gain attendu. Le *continuous batching* de
   vLLM n'a pas été tiré ici : toutes les mesures sont séquentielles. Le
   plafond réel est le nombre de requêtes concurrentes que le cache KV tient
   (§3.4), et il reste borné en amont par le pool de threads de l'application
   (tâche 7 du backlog).
6. **La durée de démarrage** : 80 à 110 s ici, poids déjà en cache disque. À
   compter dans toute procédure de redémarrage — Ollama, lui, sert dès que le
   conteneur est là.

## 5 bis. La bascule se fait dans `llm-service`, et elle sert DEUX applications

Contrainte d'organisation, pas de technique — et elle prime sur tout le reste de
ce document.

**Le moteur n'appartient pas à cette application.** Il vit dans le repo compagnon
[`llm-service`](https://github.com/floSa/llm-service), cloné à côté du projet :
un serveur central, un modèle chargé une seule fois, et le port hôte 11434. La
bascule vers vLLM doit atterrir **là**, jamais dans `data-analyst-agent`.

**Deux projets s'y branchent aujourd'hui**, et ils ne parlent pas la même API :

| Projet | Pointe sur | Ce que la bascule lui coûte |
|---|---|---|
| `data-analyst-agent` | `http://localhost:11434/**v1**` — compatible OpenAI | vLLM sert exactement cette API : **une URL à changer** (`DAA_LLM_BASE_URL`, l'ancienne `DAA_OLLAMA_BASE_URL` restant acceptée avec un avertissement) |
| Projet RAG (`rag-agent-api`) | `http://ollama-central:11434` — **sans `/v1`**, donc l'API native Ollama | vLLM **ne sert pas** cette API : ce projet devra changer de client, pas seulement d'URL |

Même modèle des deux côtés — `gemma4:e4b` — il n'y a rien à arbitrer là-dessus.

Deux conséquences à poser avant de lancer la migration, pas pendant :

- **Les embeddings.** `nomic-embed-text` est servi par le même Ollama, et le
  projet RAG en dépend. Un serveur vLLM sert **un** modèle, fixé au lancement
  (§5.2) : soit on garde Ollama à côté pour l'embedding, soit on lance une
  seconde instance vLLM. Dans les deux cas c'est un budget de VRAM, et il
  s'ajoute à celui du modèle de génération.
- **Le gain visé est le parallélisme, pas la vitesse brute.** `ollama-central`
  tourne avec `OLLAMA_NUM_PARALLEL=1` : les deux applications font la queue
  l'une derrière l'autre, et c'est ce qui explique les temps de réponse observés
  quand les deux tournent. Une instance vLLM unique les sert en concurrence
  (§5.5). C'est la raison de basculer.

## 6. Reproduire le banc

```bash
# 1. l'image (28,8 Go) et le modèle du banc (~8 Go de cache Hugging Face)
docker pull vllm/vllm-openai:latest

# 2. le serveur jetable — cf. §2 pour la commande complète

# 3. les trois épreuves (ou --epreuve planificateur|sql|contexte)
uv run python scripts/vllm_bench.py --base-url http://localhost:8000/v1 --model <modèle>

# 4. le témoin : le moteur en service, sur le même banc
uv run python scripts/vllm_bench.py --base-url http://localhost:11434/v1 --model gemma4:e4b

# 5. ne rien laisser retenir la mémoire GPU
docker rm -f vllm-bench
```

`scripts/vllm_bench.py` ne lit **aucun** réglage du `.env` : tout passe en
argument, et il ne réessaie pas (`llm_max_retries=0`) — un banc mesure la
première réponse du serveur, il ne la moyenne pas.

**`ollama-central` n'a pas été touché** : vérifié en marche et répondant avant
le banc comme après, avec ses 4 902 Mio de VRAM inchangés du début à la fin.
Le dimensionnement de `--gpu-memory-utilization` a été calculé pour ne jamais
mordre dessus.

## 7. Le modèle du vLLM partagé : `gemma-4-E4B-it-qat-w4a16-ct`

Banc du 2026-09-14, même machine (L4 23 034 Mio, vLLM `0.28.0`,
`vllm/vllm-openai:latest`). Il répond au trou n° 1 du [§5](#5-ce-qui-reste-à-vérifier-avant-une-bascule-réelle) :
**le banc de C7 validait le mécanisme, pas un modèle.** Celui-ci valide le
modèle que servira le vLLM partagé — `google/gemma-4-E4B-it-qat-w4a16-ct`,
quantification QAT officielle de Google au format compressed-tensors.

L'enjeu tient en une phrase : trois applications vont être servies par une
seule instance vLLM, Elivie sert déjà ce modèle mais **ne fait que des
complétions simples**. Le tool calling n'y avait jamais été vérifié, et c'est
ce dont dépendent notre planificateur, notre agent SQL et notre agent système.

> **Rien n'a été basculé.** Ollama reste le moteur en service ; ni `.env` ni
> `config.py` n'ont été touchés. Les conteneurs du banc ont été supprimés.

**Verdict : GO.** Les trois épreuves passent, avec l'analyseur de sa famille.

### 7.1 L'analyseur : `gemma4`, et la raison de ne pas s'arrêter au nom

L'image en propose deux dont le nom évoque gemma — `functiongemma` et
`gemma4`. Le choix a été fait sur le code, pas sur le nom :

| Analyseur | Syntaxe qu'il extrait | Pour quel modèle |
|---|---|---|
| `functiongemma` | `<start_function_call>call:f{…}<end_function_call>` | `google/functiongemma-270m-it` — un autre modèle |
| `gemma4` | `<\|tool_call>call:…` | la famille Gemma 4 |

Et le chat template du modèle, lu dans son dépôt avant tout téléchargement,
tranche : il émet `<|tool_call>`, `<|tool_response>`, `<|tool>`. C'est
`gemma4`.

**Un détail de `Gemma4EngineToolParser` qui compte pour le planificateur.** Il
déclare `supports_required_and_named = False`. Loin d'être un défaut, c'est
délibéré : Gemma 4 émet sa syntaxe native plutôt qu'un JSON contraint, et
forcer le *guided decoding* entrerait en conflit avec elle. Conséquence,
lisible dans `vllm/parser/abstract_parser.py` : un `tool_choice="required"`
bascule sur le **chemin d'extraction automatique**. Donc
**`--enable-auto-tool-choice` est indispensable pour le planificateur aussi**,
et pas seulement pour l'agent SQL comme le laissait croire le [§4](#4-ce-qui-casse-sans-les-bonnes-options--résumé).

### 7.2 La commande qui a marché

```bash
docker run -d --name vllm-bench --gpus all \
  -v /home/ubuntu/.cache/huggingface:/root/.cache/huggingface \
  -p 8000:8000 --ipc=host \
  vllm/vllm-openai:latest \
  --model google/gemma-4-E4B-it-qat-w4a16-ct \
  --gpu-memory-utilization 0.62 \
  --max-model-len 32768 \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4
```

```bash
uv run python scripts/vllm_bench.py --base-url http://localhost:8000/v1 \
    --model google/gemma-4-E4B-it-qat-w4a16-ct
```

### 7.3 Les trois épreuves

**Épreuve 1 — le planificateur rend-il un `Plan` structuré ? Oui.**

```
Plan           : Plan(capability='query', source='iris', dataset=None, features={},
                      data_question='compter le nombre de fleurs par espèce', reason='')
tokens prompt (serveur) : 1203
```

À comparer au témoin Ollama du [§3.1](#31-le-planificateur-rend-il-un-plan-structuré) :
`Plan(capability='query', source='iris', …)`. **Même modèle, même capability,
même source** — le passage d'Ollama à vLLM ne change pas la décision du
planificateur.

**Épreuve 2 — l'agent SQL appelle-t-il ses trois tools ? `grounded` est-il
vrai ? Oui.**

```
tools appelés  : ['get_schema', 'run_sql']
grounded       : True
SQL exécuté    : SELECT species, count(*) AS nombre_fleurs FROM iris GROUP BY species;
synthèse       : Il y a 50 fleurs pour chaque espèce ('virginica', 'versicolor' et 'setosa').
```

C'est l'épreuve qui comptait : `tool_choice="auto"`, donc le modèle **décide**
d'appeler ses tools au lieu d'y être forcé. C'est là que le 1,5 B de C7 avait
échoué alors que le mécanisme, lui, marchait.

**Épreuve 3 — le dépassement de contexte est-il un refus explicite ? Oui, et
`is_context_refusal()` le reconnaît.**

```
exception      : pydantic_ai.exceptions.ModelHTTPError
status_code    : 400
body           : {'message': "This model's maximum context length is 32768 tokens. However, you
                  requested 0 output tokens and your prompt contains at least 32769 input tokens,
                  for a total of at least 32769 tokens. …", 'type': 'BadRequestError',
                  'param': 'input_tokens', 'code': 400}
is_context_refusal : True
```

Même corps d'erreur qu'au [§3.3](#33-dépassement-de-contexte--refus-explicite-ou-troncature-silencieuse) :
le message ne dépend pas du modèle servi, il est produit par vLLM.

### 7.4 Le mauvais analyseur : pas un 400, un faux négatif silencieux

C'était le risque annoncé — accuser le modèle à tort. **Il est pire que prévu.**
Le même banc, même modèle, même serveur, seul `--tool-call-parser` change :

| Analyseur | Épreuve 1 (planificateur) | Épreuve 2 (agent SQL) |
|---|---|---|
| `gemma4` | **OK** | **OK**, `grounded=True` |
| `hermes` (famille Qwen, celui de C7) | *OK — trompeur* | **ÉCHEC**, `grounded=False` |
| `functiongemma` | *non mesuré* | **ÉCHEC**, `grounded=False` |

Aucun HTTP 400 : le serveur démarre, répond 200, et voici ce que l'agent SQL
reçoit comme « synthèse » avec `hermes` — comme avec `functiongemma` :

```
tools appelés  : (aucun)
grounded       : False
SQL exécuté    : None
synthèse       : <|tool_call>call:get_schema{}<tool_call|>
```

**Lire cette ligne pour ce qu'elle est.** Le modèle a parfaitement émis son
appel d'outil, dans sa syntaxe native. C'est l'analyseur qui ne sait pas la
lire : l'appel n'est pas extrait, il **fuit dans le texte de la réponse**, et
`grounded` tombe à faux. Un banc lancé avec l'analyseur de C7 aurait conclu
« gemma-4 ne sait pas appeler d'outils » — c'est faux, et rien dans les codes
HTTP ne l'aurait signalé.

Deux conséquences pour qui refera ce banc :

1. **L'épreuve 1 ne détecte pas un mauvais analyseur.** Avec `hermes`, le
   planificateur rend un `Plan` — le chemin `required` passe par un JSON
   contraint qui, lui, ne dépend pas de la syntaxe native. Seule l'épreuve 2,
   en `tool_choice="auto"`, révèle le problème. **Ne jamais valider un
   analyseur sur la seule sortie structurée.**
2. La ligne « analyseur inadapté » du [§4](#4-ce-qui-casse-sans-les-bonnes-options--résumé),
   marquée *non mesuré* par C7, l'est désormais : ce n'est pas un refus, c'est
   un silence.

### 7.5 La mémoire, et ce que `--gpu-memory-utilization` ne borne pas

`ollama-central` occupait 4 901 Mio pendant tout le banc, laissant **17,06 Gio
libres** sur les 22,04 Gio que vLLM mesure.

| `--gpu-memory-utilization` | Budget annoncé | Résultat |
|---|---|---|
| 0,75 | 16,87 Gio | **OOM pendant la capture CUDAGraph** |
| 0,62 | 13,66 Gio | démarre ; 15,48 Gio réellement occupés |

À `0,62`, le détail donné par vLLM :

```
Free memory on device (17.06/22.04 GiB) on startup. Desired GPU memory utilization is (0.62, 13.66 GiB).
Actual usage is 10.29 GiB for consumed memory (weights + non-torch), 0.27 GiB for peak activation,
and 0.78 GiB for CUDAGraph memory. … Current kv cache memory in use is 3.1 GiB.
```

**Le constat qui manquait à C7 : `--gpu-memory-utilization` n'est pas une borne
dure.** Budget demandé 13,66 Gio, occupation réelle mesurée au `nvidia-smi`
**15,48 Gio** — 1,8 Gio de plus. À `0,75`, ce dépassement mord sur ce qui n'est
pas à vLLM, et l'OOM tombe pendant la capture des CUDAGraph en mode `FULL` :

```
Capturing CUDA graphs (PIECEWISE): 100%|██████████| 51/51
Capturing CUDA graphs (FULL):  57%|█████▋    | 20/35
[rank0] memory allocation failed with OOM on device 0 while trying to allocate 2097152 bytes
        (free: 2031616, total: 23661248512)
RuntimeError: Engine core initialization failed.
```

Noter que le cache KV, lui, avait été correctement dimensionné (5,97 Gio,
288 826 tokens) : **l'échec n'est pas un manque de cache, c'est la capture
CUDAGraph qui déborde après coup.** Contrairement à la fenêtre trop grande du
[§3.4](#34-ce-que---max-model-len-tient-réellement), vLLM n'annonce ici aucune
valeur de repli — il plante, et c'est à l'exploitant de laisser la marge.

**Et `ollama-central` a survécu à cette OOM** : l'allocation qui échoue est
celle de vLLM, pas la sienne. Vérifié à chaud — conteneur `healthy`, génération
réussie, 4 901 Mio inchangés, **même PID (506387) du début à la fin**.

### 7.6 La fenêtre tenable, et `DAA_CONTEXT_MODEL_WINDOW`

Le modèle déclare `max_position_embeddings = 131072`, avec une attention
glissante de 512 sur l'essentiel de ses 42 couches — d'où un cache KV très
économe. **Les deux fenêtres testées démarrent**, à `0,62` et 3,1 Gio de cache :

| `--max-model-len` | Tokens en cache KV | Requêtes concurrentes |
|---|---|---|
| 32 768 | 150 167 | **4,58 ×** |
| 131 072 (fenêtre native) | 186 796 | **1,43 ×** |

**La fenêtre tenable est donc la fenêtre native du modèle, 131 072** — la VRAM
n'est pas ce qui la limite ici, et c'est une différence nette avec le 7 B de
C7.

Ce qui reste un arbitrage, pas une mesure : à 131 072, il ne reste qu'**1,43
requête concurrente**. Or le parallélisme est *la* raison de basculer
([§5 bis](#5-bis-la-bascule-se-fait-dans-llm-service-et-elle-sert-deux-applications))
et le serveur devra tenir **trois** applications. `32 768` garde 4,58 × pour la
même carte, et c'est la fenêtre qu'Ollama sert aujourd'hui — donc aucune
régression.

**Recommandation : `--max-model-len 32768`, et `DAA_CONTEXT_MODEL_WINDOW=32768`
en face.** C'est déjà la valeur par défaut du réglage : à fenêtre inchangée,
**il n'y a rien à modifier côté application**. Les deux valeurs doivent rester
accordées — sans quoi la détection de débordement mesure une fenêtre qui n'est
pas celle servie ([§5.3](#5-ce-qui-reste-à-vérifier-avant-une-bascule-réelle)).
L'arbitrage fenêtre / concurrence appartient à `llm-service`, pas à ce dépôt.

### 7.7 Démarrage, poids, et durées

| Mesure | Valeur |
|---|---|
| Poids à télécharger | **10,72 Gio** (`model.safetensors`, dépôt total 10,75 Gio) |
| Durée du téléchargement | **24,4 s** (mesure vLLM, sans jeton HF) |
| Chargement des poids, une fois en cache | 3,7 s, pour 9,81 Gio en VRAM |
| Compilation `torch.compile` | 68,0 s |
| Capture des CUDAGraph | 12 s, 0,78 Gio |
| `init engine` (profil + cache KV + warmup) | 114,5 s |
| **Démarrage total, poids en cache → serveur prêt** | **234 s** |

**Plus du double des 80–110 s du [§5.6](#5-ce-qui-reste-à-vérifier-avant-une-bascule-réelle)**,
mesurées sur le 7 B de C7 : le modèle est plus gros et la compilation pèse à
elle seule 68 s. À compter dans toute procédure de redémarrage de
`llm-service` — quatre minutes pendant lesquelles les trois applications n'ont
pas de moteur.

### 7.8 Ce qui n'a pas été mesuré

- **L'agent système et ses cinq tools.** Le banc en couvre trois (agent SQL).
  Rien n'indique que le nombre de tools change quoi que ce soit une fois
  l'analyseur correct, mais ce n'est pas mesuré.
- **La concurrence réelle.** Toutes les mesures sont séquentielles, comme
  celles de C7 ([§5.5](#5-ce-qui-reste-à-vérifier-avant-une-bascule-réelle)).
  Les « 4,58 × » sont le calcul de vLLM sur la taille de son cache, pas un
  débit observé à trois applications.
- **La qualité des réponses hors épreuves**, et le multimodal : le modèle
  accepte image, audio et vidéo, dont l'application ne fait rien.
- **La cohabitation avec l'embedding.** `nomic-embed-text` reste à servir pour
  le projet RAG ([§5 bis](#5-bis-la-bascule-se-fait-dans-llm-service-et-elle-sert-deux-applications)) :
  le budget VRAM ci-dessus ne le compte pas.
