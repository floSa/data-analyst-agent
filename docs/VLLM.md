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
