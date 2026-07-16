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

from data_analyst_agent.agents.retrieval.sql import DatabaseAdapter, QueryError, QueryResult
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model

SYSTEM_PROMPT = """\
Tu es un expert SQL (dialecte : {dialect}). On te pose une question sur des
données ; tu y réponds en interrogeant la base, en LECTURE SEULE.

Démarche :
1. Appelle get_schema pour connaître les tables, colonnes et relations.
2. Écris UNE requête SELECT qui répond à la question (jointures si besoin).
   - Si on te demande de LISTER / AFFICHER des individus (« donne-moi… »,
     « liste… », « quelles sont les lignes… »), sélectionne TOUTES les colonnes
     pertinentes (en pratique `SELECT *`), pour que le résultat reste
     réutilisable ; n'emploie DISTINCT que si on demande des valeurs uniques.
   - Réserve les projections restreintes (une seule colonne) et les agrégats
     (COUNT, AVG…) aux questions qui les demandent explicitement.
3. Exécute-la avec run_sql.
4. Si run_sql renvoie une erreur SQL, corrige ta requête et réessaie.
5. Quand le résultat est correct, réponds par une TRÈS courte synthèse en
   français (1 à 2 phrases). Le tableau des résultats est affiché séparément à
   l'utilisateur : NE recopie donc PAS les lignes une à une ; contente-toi de
   décrire ce que montre le résultat (et, au besoin, une ou deux valeurs clés
   comme un total). N'invente aucun chiffre.
"""


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

    @property
    def succeeded(self) -> bool:
        return self.result is not None


@dataclass
class RetrievalDeps:
    adapter: DatabaseAdapter
    max_rows: int = 200
    executed: list[ExecutedQuery] = field(default_factory=list)
    last_success: tuple[str, QueryResult] | None = None


def build_retrieval_agent() -> Agent[RetrievalDeps, str]:
    agent: Agent[RetrievalDeps, str] = Agent(deps_type=RetrievalDeps, output_type=str)

    @agent.system_prompt
    def system_prompt(ctx: RunContext[RetrievalDeps]) -> str:
        return SYSTEM_PROMPT.format(dialect=ctx.deps.adapter.dialect)

    @agent.tool
    def list_tables(ctx: RunContext[RetrievalDeps]) -> list[str]:
        """Liste les tables disponibles dans la source."""
        return ctx.deps.adapter.schema().table_names()

    @agent.tool
    def get_schema(ctx: RunContext[RetrievalDeps]) -> str:
        """Schéma complet : tables, colonnes, types, clés étrangères."""
        return ctx.deps.adapter.schema().to_prompt()

    @agent.tool
    def run_sql(ctx: RunContext[RetrievalDeps], query: str) -> str:
        """Exécute une requête SELECT et renvoie le résultat (ou l'erreur SQL)."""
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
) -> RetrievalResult:
    """Répond à une question par une requête SQL sur la source fournie."""
    settings = settings or get_settings()
    deps = RetrievalDeps(adapter=adapter, max_rows=settings.retrieval_max_rows)
    agent = build_retrieval_agent()
    run = agent.run_sync(
        question,
        model=model or build_model(settings),
        deps=deps,
        usage_limits=UsageLimits(request_limit=settings.retrieval_request_limit),
    )
    sql, result = deps.last_success if deps.last_success else (None, None)
    return RetrievalResult(summary=run.output, sql=sql, result=result, executed=deps.executed)
