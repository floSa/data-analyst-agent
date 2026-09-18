# Le moteur d'inférence : vLLM, ce qui est mesuré

Le projet n'a qu'un moteur : **vLLM**, servi par le dépôt compagnon
[`llm-service`](https://github.com/floSa/llm-service) sur le port hôte **8100**,
avec le modèle **`google/gemma-4-E4B-it-qat-w4a16-ct`**. C'est la page de ce
moteur : les options dont le système dépend, ce qui casse sans elles, les
mesures qui l'établissent, et les pièges pour qui le relance ou le redimensionne.

Bancs d'essai des 2026-09-03 et 2026-09-14, sur la machine de dev (NVIDIA L4
23 034 Mio, vLLM `0.28.0`, image `vllm/vllm-openai:latest`).

Ce document sépare ce qui a été **constaté** (sorties réelles, citées) de ce qui
en est **déduit**. Les déductions sont annoncées comme telles.

> **L'arbitrage du serveur appartient à `llm-service`, pas à ce dépôt.** Les
> bancs cités ici tournaient contre des conteneurs jetables, supprimés à la fin ;
> côté application, tout tient dans `DAA_LLM_BASE_URL` et `DAA_LLM_MODEL`.

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

**Le modèle de ce premier banc n'est pas celui du projet** : il ne s'agissait
que de valider le mécanisme de tool calling. `Qwen/Qwen2.5-7B-Instruct-AWQ` a
été choisi sur deux critères, et deux seulement — il tient dans la mémoire GPU
laissée libre (5,58 Gio de poids), et vLLM a un analyseur adapté à sa famille
(`hermes`). Le modèle réellement servi est établi au [§7](#7-le-modèle-servi--gemma-4-e4b-it-qat-w4a16-ct).
Noter que **`DAA_LLM_MODEL` est un identifiant de dépôt Hugging Face**
(`google/…`) : un serveur vLLM sert **un** modèle, fixé au lancement.

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

| Modèle | `--enable-auto-tool-choice --tool-call-parser hermes` | Résultat |
|---|---|---|
| Qwen2.5-1.5B-Instruct | **non** | HTTP 400 |
| Qwen2.5-1.5B-Instruct | **oui** | `Plan(capability='analyze', dataset='iris', …)` |
| vLLM, Qwen2.5-7B-Instruct-AWQ | **oui** | `Plan(capability='analyze', dataset='iris', data_question='SELECT COUNT(*), species FROM iris GROUP BY species')` |

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

| Modèle | Options | Tools appelés | `grounded` |
|---|---|---|---|
| Qwen2.5-1.5B-Instruct | non | — (HTTP 400) | faux |
| Qwen2.5-1.5B-Instruct | oui | **aucun** | **faux** |
| Qwen2.5-7B-Instruct-AWQ | oui | `get_schema`, `run_sql` | **vrai** |

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

### 3.3 Dépassement de contexte : un refus explicite

**Constaté, avec `--max-model-len 32768` et un prompt de ~40 000 tokens : HTTP
400, prompt rejeté.** Le serveur ne tronque pas en silence, il refuse — et le
dit dans le corps de l'erreur.

Corps d'erreur réel :

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

**`detect_overflow()` reste néanmoins utile**, et n'est pas redondante : elle
constate une troncature *après coup*, en confrontant `prompt_eval_count` à ce
qu'on a envoyé. Un serveur qui tronque au lieu de refuser — une fenêtre mal
déclarée, un proxy qui coupe — ne produit aucune erreur à attraper. Les deux
filets couvrent deux pannes différentes, et le code tient les deux plutôt que
de parier sur l'une.

### 3.4 Ce que `--max-model-len` tient réellement

La L4 fait 23 034 Mio ; un autre service en occupait **4 902 Mio** pendant tout
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
   pas se voit tout de suite, avec la valeur qui tiendrait — jamais au bout de
   trois semaines de service.
2. **Le cache KV est préalloué et ne revient pas.** Le conteneur a tenu
   13 754 Mio à `0,60` du début à la fin, sans rien rendre. La VRAM d'une
   instance vLLM est à compter comme réservée en permanence, pas comme un pic.

**Déduit, non mesuré** : ces chiffres valent pour un 7 B quantifié AWQ. Un
modèle de 30 B en Q4 (~19 Gio de poids, CADRAGE §5) ne laisserait presque rien
pour le cache KV sur cette même carte — la fenêtre tenable serait à mesurer
modèle en main, et le tableau ci-dessus ne s'y extrapole pas. Les chiffres du
modèle réellement servi sont au [§7.5](#75-la-mémoire-et-ce-que---gpu-memory-utilization-ne-borne-pas).

## 4. Ce qui casse sans les bonnes options — résumé

| Ce qu'on oublie | Ce qui se passe |
|---|---|
| `--tool-call-parser` | HTTP 400 `tool_choice="required" requires --tool-call-parser to be set` → **le planificateur ne marche plus du tout** |
| `--enable-auto-tool-choice` | HTTP 400 `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set` → **l'agent SQL ne marche plus du tout** |
| un analyseur inadapté à la famille du modèle | *non mesuré ici* — les appels d'outil ne seraient pas extraits du texte généré |
| `--max-model-len` trop grand pour la mémoire libre | vLLM refuse de démarrer, et annonce la longueur tenable |
| `--api-key` côté serveur, sans `DAA_LLM_API_KEY` côté application | rejet à chaque requête, faute d'en-tête `Authorization` (le champ existe depuis la tâche 8) |

## 5. Ce que ce premier banc ne couvrait pas

Ce banc valide le **mécanisme**, pas un modèle. Les points ci-dessous étaient
ses trous connus ; la plupart sont refermés plus bas, et le renvoi le dit.

1. ~~**Le modèle réellement servi.**~~ **Mesuré** —
   [§7](#7-le-modèle-servi--gemma-4-e4b-it-qat-w4a16-ct). C'est le point qui
   comptait : il faut refaire passer les trois épreuves sur le modèle servi,
   avec l'analyseur de SA famille, et vérifier que l'agent SQL appelle bien ses
   tools en `tool_choice="auto"` — c'est là que le 1,5 B a échoué alors que le
   mécanisme, lui, fonctionnait.
2. **La fenêtre tenable pour ce modèle-là**, et l'accord de
   `DAA_CONTEXT_MODEL_WINDOW` avec le `--max-model-len` retenu — sans quoi la
   détection de débordement mesure une fenêtre qui n'est pas celle servie.
   Tranché au [§7.6](#76-la-fenêtre-tenable-et-daa_context_model_window).
3. **Le message d'erreur rendu à l'utilisateur.** Un 400 de tool calling arrive
   aujourd'hui brut, options de lancement comprises (§3.1). C'est la tâche 10
   du backlog.
4. ~~**La concurrence**, qui est le gain attendu.~~ **Mesurée depuis** —
   [concurrence.md](concurrence.md). Toutes les mesures de ce document sont
   séquentielles : le *continuous batching* n'y est pas tiré. Le plafond est le
   nombre de requêtes concurrentes que le cache KV tient (§3.4), borné en amont
   par le pool de threads de l'application.
5. **La durée de démarrage** : 80 à 110 s ici, poids déjà en cache disque, et
   234 s pour le modèle servi ([§7.7](#77-démarrage-poids-et-durées)). À compter
   dans toute procédure de redémarrage : ce sont des minutes sans moteur.

## 5 bis. Le moteur vit dans `llm-service`, et il sert PLUSIEURS applications

Contrainte d'organisation, pas de technique — et elle prime sur tout le reste de
ce document.

**Le moteur n'appartient pas à cette application.** Il vit dans le dépôt
compagnon [`llm-service`](https://github.com/floSa/llm-service), cloné à côté du
projet : un serveur central, un modèle chargé une seule fois, et le port hôte
**8100**. Tout réglage du serveur — fenêtre, mémoire, analyseur, modèle servi —
se change **là**, jamais dans `data-analyst-agent`.

Côté application, il n'y a que deux variables, et elles suffisent :

| Variable | Valeur en service |
|---|---|
| `DAA_LLM_BASE_URL` | `http://localhost:8100/v1` (`http://host.docker.internal:8100/v1` depuis un conteneur) |
| `DAA_LLM_MODEL` | `google/gemma-4-E4B-it-qat-w4a16-ct` |

Ce sont aussi les **valeurs par défaut du code** : sans `.env`, l'application
vise le service en place et demande un modèle qui y est chargé. Deux tests les
tiennent, fichier `.env` coupé
([test_settings.py](../tests/unit/test_settings.py)) — une campagne ne le
prouverait pas, puisqu'elles tournent toutes avec un `.env` recopié.

**Le serveur est mutualisé.** D'autres applications s'y branchent, et deux
conséquences sont à poser avant d'y toucher, pas pendant :

- **Un serveur vLLM sert UN modèle, fixé au lancement.** Une application qui a
  besoin d'un autre modèle — un modèle d'embedding, par exemple — demande une
  seconde instance, donc un second budget de VRAM qui s'ajoute à celui-ci.
- **Une application qui parle une API non-OpenAI doit changer de client, pas
  seulement d'URL.** vLLM sert `/v1/chat/completions` et rien d'autre.

## 6. Reproduire le banc
## 6. Reproduire le banc

```bash
# 1. l'image (28,8 Go) et le modèle du banc (~8 Go de cache Hugging Face)
docker pull vllm/vllm-openai:latest

# 2. le serveur jetable — cf. §2 pour la commande complète

# 3. les trois épreuves (ou --epreuve planificateur|sql|contexte)
uv run python scripts/vllm_bench.py --base-url http://localhost:8000/v1 --model <modèle>

# 4. le témoin : le serveur en service, sur le même banc
uv run python scripts/vllm_bench.py --base-url http://localhost:8100/v1 \
    --model google/gemma-4-E4B-it-qat-w4a16-ct

# 5. ne rien laisser retenir la mémoire GPU
docker rm -f vllm-bench
```

`scripts/vllm_bench.py` ne lit **aucun** réglage du `.env` : tout passe en
argument, et il ne réessaie pas (`llm_max_retries=0`) — un banc mesure la
première réponse du serveur, il ne la moyenne pas.

**Le service central n'a pas été touché** : vérifié en marche et répondant
avant le banc comme après, avec ses 4 902 Mio de VRAM inchangés du début à la
fin. Le dimensionnement de `--gpu-memory-utilization` a été calculé pour ne
jamais mordre dessus — c'est la précaution à reprendre pour tout banc lancé sur
la carte qui sert déjà.

## 7. Le modèle servi : `gemma-4-E4B-it-qat-w4a16-ct`

Banc du 2026-09-14, même machine (L4 23 034 Mio, vLLM `0.28.0`,
`vllm/vllm-openai:latest`). Il répond au trou n° 1 du
[§5](#5-ce-que-ce-premier-banc-ne-couvrait-pas) : **le banc précédent validait le
mécanisme, pas un modèle.** Celui-ci valide le modèle servi par le central —
`google/gemma-4-E4B-it-qat-w4a16-ct`, quantification QAT officielle de Google au
format compressed-tensors.

L'enjeu tient en une phrase : plusieurs applications sont servies par une seule
instance, et celle qui l'utilisait déjà **ne fait que des complétions simples**.
Le tool calling n'y avait jamais été vérifié, et c'est ce dont dépendent notre
planificateur, notre agent SQL et notre agent système.

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

La capability et la source sont celles qu'on attend de cette question : le
planificateur route en `query` sur `iris`, et le `data_question` est
exploitable tel quel par l'agent SQL.

**Épreuve 2 — l'agent SQL appelle-t-il ses trois tools ? `grounded` est-il
vrai ? Oui.**

```
tools appelés  : ['get_schema', 'run_sql']
grounded       : True
SQL exécuté    : SELECT species, count(*) AS nombre_fleurs FROM iris GROUP BY species;
synthèse       : Il y a 50 fleurs pour chaque espèce ('virginica', 'versicolor' et 'setosa').
```

C'est l'épreuve qui comptait : `tool_choice="auto"`, donc le modèle **décide**
d'appeler ses tools au lieu d'y être forcé. C'est là que le 1,5 B du
[§3.2](#32-lagent-sql-appelle-t-il-ses-trois-tools--grounded-est-il-vrai) avait
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

Même corps d'erreur qu'au [§3.3](#33-dépassement-de-contexte--un-refus-explicite) :
le message ne dépend pas du modèle servi, il est produit par vLLM.

### 7.4 Le mauvais analyseur : pas un 400, un faux négatif silencieux

C'était le risque annoncé — accuser le modèle à tort. **Il est pire que prévu.**
Le même banc, même modèle, même serveur, seul `--tool-call-parser` change :

| Analyseur | Épreuve 1 (planificateur) | Épreuve 2 (agent SQL) |
|---|---|---|
| `gemma4` | **OK** | **OK**, `grounded=True` |
| `hermes` (famille Qwen, celui du §2) | *OK — trompeur* | **ÉCHEC**, `grounded=False` |
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
`grounded` tombe à faux. Un banc lancé avec l'analyseur du §2 aurait conclu
« gemma-4 ne sait pas appeler d'outils » — c'est faux, et rien dans les codes
HTTP ne l'aurait signalé.

Deux conséquences pour qui refera ce banc :

1. **L'épreuve 1 ne détecte pas un mauvais analyseur.** Avec `hermes`, le
   planificateur rend un `Plan` — le chemin `required` passe par un JSON
   contraint qui, lui, ne dépend pas de la syntaxe native. Seule l'épreuve 2,
   en `tool_choice="auto"`, révèle le problème. **Ne jamais valider un
   analyseur sur la seule sortie structurée.**
2. La ligne « analyseur inadapté » du [§4](#4-ce-qui-casse-sans-les-bonnes-options--résumé),
   d'abord marquée *non mesuré*, l'est désormais : ce n'est pas un refus, c'est
   un silence.

### 7.5 La mémoire, et ce que `--gpu-memory-utilization` ne borne pas

Un autre service occupait 4 901 Mio pendant tout le banc, laissant **17,06 Gio
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

**`--gpu-memory-utilization` n'est pas une borne dure**, et c'est le piège le
plus coûteux de cette page. Budget demandé 13,66 Gio, occupation réelle mesurée au `nvidia-smi`
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

**Et le service voisin a survécu à cette OOM** : l'allocation qui échoue est
celle de vLLM, pas la sienne. Vérifié à chaud — conteneur `healthy`, génération
réussie, 4 901 Mio inchangés, **même PID (506387) du début à la fin**. Une OOM
au démarrage d'une instance ne renverse donc pas ce qui tourne déjà sur la
carte.

### 7.6 La fenêtre tenable, et `DAA_CONTEXT_MODEL_WINDOW`

Le modèle déclare `max_position_embeddings = 131072`, avec une attention
glissante de 512 sur l'essentiel de ses 42 couches — d'où un cache KV très
économe. **Les deux fenêtres testées démarrent**, à `0,62` et 3,1 Gio de cache :

| `--max-model-len` | Tokens en cache KV | Requêtes concurrentes |
|---|---|---|
| 32 768 | 150 167 | **4,58 ×** |
| 131 072 (fenêtre native) | 186 796 | **1,43 ×** |

**La fenêtre tenable est donc la fenêtre native du modèle, 131 072** — la VRAM
n'est pas ce qui la limite ici, et c'est une différence nette avec le 7 B du §2.

Ce qui reste un arbitrage, pas une mesure : à 131 072, il ne reste qu'**1,43
requête concurrente**. Or le parallélisme est *la* raison d'avoir choisi ce
serveur ([§5 bis](#5-bis-le-moteur-vit-dans-llm-service-et-il-sert-plusieurs-applications)),
et le central tient **plusieurs** applications. `32 768` garde 4,58 × pour la
même carte.

**Retenu : `--max-model-len 32768`, et `DAA_CONTEXT_MODEL_WINDOW=32768` en
face.** C'est déjà la valeur par défaut du réglage. Les deux valeurs doivent
rester accordées — sans quoi la détection de débordement mesure une fenêtre qui
n'est pas celle servie. **C'est le point à revérifier à chaque fois que
`llm-service` change `--max-model-len` :** l'arbitrage fenêtre / concurrence
appartient à ce dépôt-là, la valeur en face appartient à celui-ci.

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

**Plus du double des 80–110 s du [§5](#5-ce-que-ce-premier-banc-ne-couvrait-pas)**,
mesurées sur le 7 B du §2 : le modèle est plus gros et la compilation pèse à
elle seule 68 s. À compter dans toute procédure de redémarrage de
`llm-service` — quatre minutes pendant lesquelles aucune application n'a de
moteur.

### 7.8 Ce qui n'a pas été mesuré

- ~~**L'agent système et ses cinq tools.**~~ **Mesuré depuis** — [§8](#8-lagent-système-et-ses-cinq-tools-le-trou-du-78-refermé).
  Le nombre de tools ne change rien : le modèle appelle, vLLM extrait. Ce que
  ce trou cachait était ailleurs, et n'était pas une affaire de moteur.
- ~~**La concurrence réelle.**~~ **Mesurée depuis** — [concurrence.md](concurrence.md).
  Toutes les mesures de CE document sont séquentielles ; les « 4,58 × » sont le
  calcul de vLLM sur la taille de son cache, pas un débit observé.
- **La qualité des réponses hors épreuves**, et le multimodal : le modèle
  accepte image, audio et vidéo, dont l'application ne fait rien.
- **La cohabitation avec un modèle d'embedding.** Une application qui en a
  besoin demande une seconde instance
  ([§5 bis](#5-bis-le-moteur-vit-dans-llm-service-et-il-sert-plusieurs-applications)) :
  le budget VRAM ci-dessus ne le compte pas.

## 8. L'agent système et ses cinq tools, le trou du §7.8 refermé

Mesure du 2026-09-14, contre le serveur central (`localhost:8100`,
`google/gemma-4-E4B-it-qat-w4a16-ct`, `--tool-call-parser gemma4
--enable-auto-tool-choice`). Elle répond au premier point du
[§7.8](#78-ce-qui-na-pas-été-mesuré) — **l'agent système et ses cinq tools**,
que le banc ne couvrait pas.

**Verdict : le nombre de tools n'y est pour rien.** Cinq tools au lieu de
trois, aucun argument obligatoire, des descriptions plus courtes : rien de tout
cela n'empêche le modèle d'appeler, ni vLLM d'extraire.

### 8.1 Ce que le fil brut montre

Le symptôme ressemblait pourtant au faux négatif du
[§7.4](#74-le-mauvais-analyseur--pas-un-400-un-faux-négatif-silencieux) :
« Sur quoi peux-tu travailler ? » recevait une paraphrase du prompt système au
lieu de l'inventaire attendu. Le fil, capturé par un `event_hook` httpx sur le
client du nœud système, dit autre chose :

```json
{"message": {"content": null,
             "tool_calls": [{"function": {"name": "capacites_de_l_agent", "arguments": "{}"}}]},
 "finish_reason": "tool_calls", "usage": {"completion_tokens": 14}}
```

`tool_calls` peuplé, `finish_reason` à `tool_calls`, `content` nul — l'inverse
exact de la fuite du §7.4, où l'appel apparaissait *dans le texte*. Les cinq
tools sont offerts avec `tool_choice: "auto"`, et le modèle en choisit un.

La cause est en aval du serveur, dans la formulation : le modèle **condense**
ce que l'outil lui rend. Un inventaire rendu en fin de texte disparaissait dans
le résumé, et la ceinture de l'application ne l'exigeait pas — elle ne
contrôlait que les listes à puces. Le trou était dans la ceinture, pas dans le
moteur. Le détail, la correction et la mesure sont dans
[surface-conversationnelle.md §15](surface-conversationnelle.md#15-la-batterie-de-questions-méta-et-le-trou-quelle-a-découvert).

**Ce qu'il faut en retenir pour un futur banc.** Un `grounded=True` et un
`tool_calls` peuplé prouvent que le mécanisme marche ; ils ne prouvent rien sur
ce que le modèle fait des faits reçus. Le §7.4 avait appris à ne pas valider un
analyseur sur la seule sortie structurée ; le §8 ajoute : **ne pas valider un
serveur sur les seuls appels d'outils.** La longueur des réponses n'est pas un
invariant — elle bouge avec le serveur, avec le modèle et avec sa version — et
une vérification qui ne tenait que par la verbosité tombe avec elle.

### 8.2 La batterie complète

40 questions, conversation neuve à chaque fois
([`scripts/mesure_surface_conversationnelle.py`](../scripts/mesure_surface_conversationnelle.py)) :
36 questions **sur l'agent**, qui passent par ses cinq tools, et 4 **témoins**
sur les données, qui ne doivent pas y passer.

| Passage | Méta | Témoins | Appels LLM (36 méta) | Durée des 36 méta |
|---|---|---|---|---|
| avant correction | 32 / 36 | 2 / 4 | 82 | 70 s |
| après, 2 passages | **36 / 36** | 2 / 4 | 78 | 118 s, 122 s |

**Les 36 questions méta passent.** C'est la réponse à la question que posait le
§7.8 : cinq tools au lieu de trois ne coûtent rien au mécanisme.

**Les durées ne mesurent rien d'utile ici** : la carte servait d'autres charges
pendant la mesure, et deux passages identiques ont pu s'écarter du simple au
double. Ce qui se lit, c'est le **nombre d'appels LLM**, qui ne dépend pas de
la charge : 78 après correction contre 82 avant — la correction a rendu la
batterie moins chère, pas plus.

Les deux témoins qui restent en échec sont en aval du nœud système — l'agent
SQL, et une validation de features qui refusait `'1'` pour un
`Literal[1, 2, 3]`. Ils sont détaillés au
[§15.6](surface-conversationnelle.md#156-les-témoins-et-les-deux-questions-de-données-qui-restent) ;
le second est corrigé depuis, cf. [§8.4](#84-les-arguments-doutil-typés--ce-nest-pas-le-serveur-qui-stringifie),
et `temoin-prediction` passe.

### 8.3 Ce qui reste non mesuré côté agent système

- ~~**Les arguments d'outil typés.**~~ **Mesuré depuis** —
  [§8.4](#84-les-arguments-doutil-typés--ce-nest-pas-le-serveur-qui-stringifie).
  Un tool à arguments `integer`, `number`, `boolean` et `enum` d'entiers rend
  les types déclarés. Les cinq tools de l'agent système n'ont, eux, que des
  arguments texte facultatifs : indemnes par construction ;
- ~~**le plafond d'allers-retours.**~~ **Réglé** : `systeme_request_limit` vaut
  6, et la marge est mesurée gratuite — 78 appels sur la batterie complète à 4,
  5 comme à 6. Un plafond est un budget disponible, pas un budget dépensé ;
- **la concurrence**, toujours : ces 40 questions sont posées en série, comme
  toutes les mesures de ce document. Elle est mesurée dans
  [concurrence.md](concurrence.md).

### 8.4 Les arguments d'outil typés : ce n'est pas le serveur qui stringifie

Le [§8.3](#83-ce-qui-reste-non-mesuré-côté-agent-système) laissait ouvert
« un tool à argument entier ou booléen n'a pas été essayé », et le
[§8.2](#82-la-batterie-complète) imputait au serveur le refus de `'1'` pour un
`Literal[1, 2, 3]`. **Mesuré : l'imputation était fausse.**

Mesure du 2026-09-14, un tool aux arguments explicitement typés — `string`,
`integer`, `number`, `boolean`, et un `integer` sous `enum` (la forme que prend
un `Literal[1, 2, 3]` en JSON Schema) — posé avec `tool_choice="required"`,
température 0. Elle se rejoue :

```bash
uv run python scripts/mesure_typage_des_arguments_d_outil.py
```

> Réserve une table pour Dupont, 4 convives, 25.5 euros d'acompte, en terrasse,
> au 2e étage.

```
{"acompte": 25.5, "convives": 4, "etage": 2, "nom": "Dupont", "terrasse": true}
```

| Argument | Type déclaré | Rendu |
|---|---|---|
| `nom` | `string` | `'Dupont'` |
| `convives` | `integer` | `4` |
| `acompte` | `number` | `25.5` |
| `terrasse` | `boolean` | `True` |
| `etage` | `integer` + `enum` | `2` |

**Quand le JSON Schema déclare le type, le serveur le respecte** — entier,
flottant, booléen, et un entier sous `enum`, qui est justement la forme d'un
`Literal[1, 2, 3]`.

#### Où l'écart se produit réellement

Dans le seul endroit du système où le schéma ne déclare **rien**. `Plan.features`
est un `dict[str, Any]`, ce qui donne :

```json
"features": { "additionalProperties": true, "type": "object" }
```

C'est délibéré : le planificateur extrait des valeurs sans savoir encore de quel
dataset elles relèvent. Sans type annoncé, le serveur devine — ici des chaînes —
et `Literal[1, 2, 3]`, qui compare des valeurs, refusait `'1'`.

Tous les autres champs du plan (`capability`, `source`, `dataset`,
`data_question`, `reason`) sont déclarés `string` : mesurés, indemnes. Les cinq
tools de l'agent système et les trois de l'agent SQL n'ont que des arguments
texte : indemnes par construction.

La correction, l'étendue mesurée sur les trois schémas de features, les cas
hostiles et la batterie complète rejouée sont dans
[surface-conversationnelle.md §16](surface-conversationnelle.md#16-literal1-2-3-refusait-1--la-prédiction-qui-nallait-pas-au-bout).
Depuis, `temoin-prediction` passe avec la réponse mot pour mot de l'oracle.

**Ce qu'il faut en retenir pour un futur banc.** Le §7.4 avait appris à ne pas
valider un analyseur sur la seule sortie structurée ; le §8, à ne pas valider un
serveur sur les seuls appels d'outils. Le §8.4 ajoute la marche d'après : **un
`tool_calls` bien typé ne prouve rien là où le schéma ne dit pas le type.** La
bonne question n'est pas « le serveur rend-il des entiers », c'est « que fait-il
quand on ne lui demande rien » — et la réponse peut changer d'une version à
l'autre. Un schéma qui déclare tout ne laisse pas la question se poser : c'est
la parade la plus simple, quand le typage est connu à l'avance.
