"""Capacité ① — agent text-to-SQL à tools, self-correction sur erreur SQL.

Le modèle dispose de trois tools typés (list_tables, get_schema, run_sql) ;
une erreur SQL lui est renvoyée en texte pour qu'il corrige sa requête —
le nombre total d'allers-retours est borné (retrieval_request_limit).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from data_analyst_agent import prompts
from data_analyst_agent.agents.retrieval.sql import (
    DatabaseAdapter,
    QueryError,
    QueryResult,
)
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model


class ExecutedQuery(BaseModel):
    sql: str
    ok: bool
    error: str | None = None


class RetrievalResult(BaseModel):
    """Issue d'une récupération : la donnée + la trace de ce qui a été exécuté."""

    summary: str
    sql: str | None = None
    result: QueryResult | None = None
    executed: list[ExecutedQuery] = []
    tools_used: list[str] = []  # vide = réponse non fondée sur la source

    @property
    def grounded(self) -> bool:
        """La réponse s'appuie-t-elle sur au moins un regard sur la source ?"""
        return bool(self.tools_used)

    @property
    def succeeded(self) -> bool:
        return self.result is not None


@dataclass
class RetrievalDeps:
    adapter: DatabaseAdapter
    max_rows: int = 200
    executed: list[ExecutedQuery] = field(default_factory=list)
    last_success: tuple[str, QueryResult] | None = None
    # Outils réellement appelés. Un modèle peut répondre SANS en toucher aucun,
    # de mémoire, sur un jeu de données célèbre (« iris est un ensemble classique
    # de classification floristique… ») : c'est du savoir encyclopédique, pas une
    # lecture de la source, et sur des données privées ce serait de l'invention.
    tools_used: list[str] = field(default_factory=list)
    dictionary: str | None = None
    history: str | None = None


def build_retrieval_agent() -> Agent[RetrievalDeps, str]:
    agent: Agent[RetrievalDeps, str] = Agent(deps_type=RetrievalDeps, output_type=str)

    @agent.system_prompt
    def system_prompt(ctx: RunContext[RetrievalDeps]) -> str:
        # Le dictionnaire passe AVANT la question, et non en réponse à un tool :
        # les pièges qu'il décrit doivent être connus au moment d'écrire la
        # requête, pas après. Un modèle qui apprend en 3e tour que le e-commerce
        # est une ligne de `stores` a déjà rendu son top magasins. Le contexte
        # du tour précédent, lui, résout les anaphores : « affiche ceux des
        # autres années » n'a de sens qu'en sachant ce qu'était « ceux ».
        prompt = prompts.render(prompts.RETRIEVAL, dialect=ctx.deps.adapter.dialect)
        if ctx.deps.dictionary:
            prompt += prompts.render(prompts.RETRIEVAL_DICTIONARY, dictionary=ctx.deps.dictionary)
        if ctx.deps.history:
            prompt += prompts.render(prompts.RETRIEVAL_HISTORY, history=ctx.deps.history)
        return prompt

    @agent.tool
    def list_tables(ctx: RunContext[RetrievalDeps]) -> list[str]:
        """Liste les tables disponibles dans la source."""
        ctx.deps.tools_used.append("list_tables")
        return ctx.deps.adapter.schema().table_names()

    @agent.tool
    def get_schema(ctx: RunContext[RetrievalDeps]) -> str:
        """Schéma complet : tables, colonnes, types, clés étrangères."""
        ctx.deps.tools_used.append("get_schema")
        return ctx.deps.adapter.schema().to_prompt()

    @agent.tool
    def run_sql(ctx: RunContext[RetrievalDeps], query: str) -> str:
        """Exécute une requête SELECT et renvoie le résultat (ou l'erreur SQL)."""
        ctx.deps.tools_used.append("run_sql")
        try:
            result = ctx.deps.adapter.run(query, max_rows=ctx.deps.max_rows)
        except QueryError as exc:
            ctx.deps.executed.append(ExecutedQuery(sql=query, ok=False, error=str(exc)))
            return f"ERREUR SQL : {exc}\nCorrige la requête et réessaie."
        ctx.deps.executed.append(ExecutedQuery(sql=query, ok=True))
        ctx.deps.last_success = (query, result)
        return result.to_markdown()

    return agent


def run_retrieval(
    question: str,
    *,
    adapter: DatabaseAdapter,
    model: Model | None = None,
    settings: Settings | None = None,
    dictionary: str | None = None,
    history: str | None = None,
) -> RetrievalResult:
    """Répond à une question par une requête SQL sur la source fournie."""
    settings = settings or get_settings()
    deps = RetrievalDeps(
        adapter=adapter,
        max_rows=settings.retrieval_max_rows,
        dictionary=dictionary,
        history=history,
    )
    agent = build_retrieval_agent()
    run = agent.run_sync(
        question,
        model=model or build_model(settings),
        deps=deps,
        usage_limits=UsageLimits(request_limit=settings.retrieval_request_limit),
    )
    sql, result = deps.last_success if deps.last_success else (None, None)
    return RetrievalResult(
        summary=run.output,
        sql=sql,
        result=result,
        executed=deps.executed,
        tools_used=deps.tools_used,
    )
