"""Banc d'essai du *tool calling* d'un serveur LLM OpenAI-compatible.

Le système dépend du tool calling en DEUX endroits, et un serveur peut très
bien répondre correctement en texte tout en n'en émettant aucun :

1. le **planificateur** — `output_type=Plan` est réalisé par pydantic-ai via un
   appel d'outil (`final_result`). Sans tool calling : `UnexpectedModelBehavior`,
   repli, et une demande de clarification à chaque question ;
2. l'**agent SQL** — trois tools. Sans tool calling : `grounded` reste faux et
   l'utilisateur lit « Je n'ai pas interrogé la source ».

S'y ajoute le **dépassement de contexte** : là où Ollama tronque en silence,
vLLM rejette par une erreur HTTP. Ce banc confronte `is_context_refusal()` au
corps d'erreur réel du serveur.

Ce script ne touche à AUCUN réglage du projet : tout passe en argument, rien
n'est lu dans le `.env`. Il vise un serveur jetable, pas l'instance en service.

    uv run python scripts/vllm_bench.py --base-url http://localhost:8000/v1 \
        --model Qwen/Qwen2.5-1.5B-Instruct

Épreuves sélectionnables par `--epreuve` (répétable) : planificateur, sql,
contexte. Par défaut, les trois.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from pydantic_ai import Agent, UnexpectedModelBehavior

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, describe_features
from data_analyst_agent.agents.retrieval.agent import run_retrieval
from data_analyst_agent.agents.retrieval.catalog import load_catalog, open_source
from data_analyst_agent.config import Settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.context_budget import is_context_refusal
from data_analyst_agent.orchestrator.plan import planner_agent, planner_system_prompt

EPREUVES = ("planificateur", "sql", "contexte")


def _titre(texte: str) -> None:
    print(f"\n{'=' * 78}\n{texte}\n{'=' * 78}")


def _datasets_description(registry: Registry) -> str:
    """Même description que celle que l'orchestrateur donne au planificateur."""
    lignes: list[str] = []
    for dataset in registry.datasets:
        entry = registry.get(dataset)
        lignes.append(f"- {dataset} ({entry.task}) : features attendues :")
        if dataset in SCHEMAS:
            lignes.append(describe_features(SCHEMAS[dataset]))
    return "\n".join(lignes) or "(aucun modèle)"


def epreuve_planificateur(settings: Settings, question: str) -> bool:
    """Le serveur sait-il rendre une sortie structurée ? (= un appel d'outil)"""
    _titre("ÉPREUVE 1 — planificateur : output_type=Plan (appel d'outil final_result)")
    catalog = load_catalog(settings.catalog_path)
    registry = Registry.load(settings.models_registry_path)
    prompt = planner_system_prompt(catalog.describe(), _datasets_description(registry))
    print(f"question       : {question}")
    print(f"prompt système : {len(prompt)} caractères")
    try:
        resultat = planner_agent(prompt).run_sync(question, model=build_model(settings))
    except UnexpectedModelBehavior as exc:
        print(f"ÉCHEC — pas de Plan structuré : {type(exc).__name__}: {exc}")
        print("→ l'orchestrateur tomberait dans le repli : « Je n'ai pas bien compris »")
        return False
    print(f"Plan           : {resultat.output!r}")
    print(f"tokens prompt (serveur) : {resultat.usage.input_tokens}")
    return True


def epreuve_sql(settings: Settings, source_name: str, question: str) -> bool:
    """L'agent SQL appelle-t-il ses tools, et la réponse est-elle fondée ?"""
    _titre("ÉPREUVE 2 — agent SQL : 3 tools, et `grounded`")
    catalog = load_catalog(settings.catalog_path)
    source = next(s for s in catalog.sources if s.name == source_name)
    print(f"source         : {source_name}\nquestion       : {question}")
    resultat = run_retrieval(
        question, adapter=open_source(source), model=build_model(settings), settings=settings
    )
    print(f"tools appelés  : {resultat.tools_used or '(aucun)'}")
    print(f"grounded       : {resultat.grounded}")
    print(f"SQL exécuté    : {resultat.sql}")
    print(f"synthèse       : {resultat.summary[:400]}")
    if not resultat.grounded:
        print("→ l'utilisateur recevrait « Je n'ai pas interrogé la source »")
    return resultat.grounded


def epreuve_contexte(settings: Settings, tokens: int) -> bool:
    """Le débordement est-il un REFUS explicite, et le code le reconnaît-il ?"""
    _titre("ÉPREUVE 3 — dépassement de contexte : refus explicite vs troncature")
    # ~4 caractères par token : on vise large, le but est de dépasser franchement.
    bourrage = "Le chat dort sur le tapis. " * (tokens * 4 // 27)
    print(f"prompt envoyé  : ~{len(bourrage) // 4} tokens ({len(bourrage)} caractères)")
    try:
        sortie = Agent(system_prompt=bourrage).run_sync("Dis bonjour.", model=build_model(settings))
    except Exception as exc:  # c'est justement l'exception qu'on étudie
        reconnu = is_context_refusal(exc)
        print(f"exception      : {type(exc).__module__}.{type(exc).__name__}")
        print(f"status_code    : {getattr(exc, 'status_code', '(aucun)')}")
        print(f"body           : {getattr(exc, 'body', '(aucun)')!r}")
        print(f"str(exc)       : {str(exc)[:600]}")
        print(f"is_context_refusal : {reconnu}")
        if not reconnu:
            print("→ le refus arriverait brut à l'utilisateur, sans rien qui le relie")
            print("  à la longueur du prompt : is_context_refusal() est à corriger")
        return reconnu
    print("PAS DE REFUS — le serveur a répondu ; il a donc tronqué en silence.")
    print(f"tokens prompt (serveur) : {sortie.usage.input_tokens}")
    print(f"réponse        : {sortie.output[:200]}")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--source", default="iris", help="source du catalogue pour l'épreuve SQL")
    parser.add_argument("--question-plan", default="Combien de fleurs par espèce dans iris ?")
    parser.add_argument("--question-sql", default="Combien de fleurs par espèce ?")
    parser.add_argument("--tokens-contexte", type=int, default=40000)
    parser.add_argument("--epreuve", action="append", choices=EPREUVES)
    args = parser.parse_args(argv)

    settings = Settings(
        _env_file=None,
        llm_base_url=args.base_url,
        llm_model=args.model,
        llm_api_key=args.api_key,
        llm_timeout=args.timeout,
        # Aucun réessai : un banc d'essai mesure la première réponse du serveur,
        # il ne la moyenne pas sur trois tentatives.
        llm_max_retries=0,
    )
    print(f"serveur : {args.base_url}\nmodèle  : {args.model}")

    epreuves = args.epreuve or list(EPREUVES)
    resultats: dict[str, bool] = {}
    for nom in epreuves:
        try:
            if nom == "planificateur":
                resultats[nom] = epreuve_planificateur(settings, args.question_plan)
            elif nom == "sql":
                resultats[nom] = epreuve_sql(settings, args.source, args.question_sql)
            else:
                resultats[nom] = epreuve_contexte(settings, args.tokens_contexte)
        except Exception:  # une épreuve qui casse ne doit pas masquer les suivantes
            traceback.print_exc()
            resultats[nom] = False

    _titre("BILAN")
    for nom, ok in resultats.items():
        print(f"{'OK   ' if ok else 'ÉCHEC'} {nom}")
    return 0 if all(resultats.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
