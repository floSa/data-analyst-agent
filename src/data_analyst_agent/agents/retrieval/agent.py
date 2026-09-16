"""Capacité ① — agent text-to-SQL à tools, self-correction sur erreur SQL.

Le modèle dispose de trois tools typés (list_tables, get_schema, run_sql) ;
une erreur SQL lui est renvoyée en texte pour qu'il corrige sa requête —
le nombre total d'allers-retours est borné (retrieval_request_limit).

Le prompt système porte aussi le DICTIONNAIRE de la source quand elle en déclare
un : le schéma dit les types, le dictionnaire dit ce que les valeurs veulent
dire, et c'est au moment d'écrire le WHERE qu'on en a besoin
(cf. `agents/retrieval/dictionnaire`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from data_analyst_agent import prompts
from data_analyst_agent.agents.retrieval.dictionnaire import (
    DictionnaireInjecte,
    bloc_de_prompt,
    preparer,
)
from data_analyst_agent.agents.retrieval.sql import (
    DatabaseAdapter,
    QueryError,
    QueryResult,
    SchemaInfo,
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
    # Ce qui a été coupé du dictionnaire faute de budget ("" = rien). Remonte
    # jusqu'à la trace du tour, et de là jusqu'à l'utilisateur : le défaut
    # qu'on répare ici est un chiffre faux rendu en silence, on ne le remplace
    # pas par un dictionnaire amputé en silence.
    dictionary_notice: str = ""

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
    # Le dictionnaire de la source, déjà taillé au budget. ``None`` = la source
    # n'en déclare pas, ou l'appelant n'en passe pas : le prompt est alors
    # exactement celui d'avant, ce qui est le comportement de `titanic` et
    # `iris` (aucune des deux ne déclare de dictionnaire).
    dictionnaire: DictionnaireInjecte | None = None
    executed: list[ExecutedQuery] = field(default_factory=list)
    last_success: tuple[str, QueryResult] | None = None
    # Outils réellement appelés. Un modèle peut répondre SANS en toucher aucun,
    # de mémoire, sur un jeu de données célèbre (« iris est un ensemble classique
    # de classification floristique… ») : c'est du savoir encyclopédique, pas une
    # lecture de la source, et sur des données privées ce serait de l'invention.
    tools_used: list[str] = field(default_factory=list)


def build_retrieval_agent() -> Agent[RetrievalDeps, str]:
    agent: Agent[RetrievalDeps, str] = Agent(deps_type=RetrievalDeps, output_type=str)

    @agent.system_prompt
    def system_prompt(ctx: RunContext[RetrievalDeps]) -> str:
        return composer_le_prompt(ctx.deps.adapter.dialect, ctx.deps.dictionnaire)

    @agent.tool
    def list_tables(ctx: RunContext[RetrievalDeps]) -> list[str]:
        """Liste les tables disponibles dans la source."""
        ctx.deps.tools_used.append("list_tables")
        return ctx.deps.adapter.schema().table_names()

    @agent.tool
    def get_schema(ctx: RunContext[RetrievalDeps], table_name: str = "") -> str:
        """Schéma : tables, colonnes, types, clés étrangères.

        `table_name` : une table à décrire seule. Laisse vide — le défaut —
        pour le schéma COMPLET, qui est ce qu'il te faut dans presque tous les
        cas (une jointure se lit sur plusieurs tables à la fois).
        """
        ctx.deps.tools_used.append("get_schema")
        return _schema_lisible(ctx.deps.adapter.schema(), table_name)

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


def composer_le_prompt(dialect: str, dictionnaire: DictionnaireInjecte | None) -> str:
    """Le prompt système de l'agent SQL : la démarche, puis le dictionnaire.

    Le dictionnaire vient APRÈS la démarche et non avant : ce qu'on lit en
    dernier est ce qu'on a sous les yeux au moment d'écrire, et l'erreur qu'on
    corrige est une erreur d'écriture de requête. Vide quand la source ne
    déclare rien — le prompt est alors, au caractère près, celui d'avant.
    """
    base = prompts.render(prompts.RETRIEVAL, dialect=dialect)
    bloc = bloc_de_prompt(dictionnaire) if dictionnaire is not None else ""
    return f"{base}\n\n{bloc}" if bloc else base


def _schema_lisible(schema: SchemaInfo, table_name: str) -> str:
    """Le schéma, réduit à ``table_name`` quand elle existe — complet sinon.

    **Un argument accepté plutôt qu'un argument interdit.** L'outil n'en prenait
    aucun ; le modèle lui passait ``{"table_name": ...}``, la validation
    refusait, il réessayait à l'identique, et le budget de reprise valant 1, le
    tour mourait sur « la source de données n'a pas pu être interrogée ».
    Mesuré 9 fois sur 9 sous vLLM, sur trois sources, jamais sous Ollama : un
    même modèle, deux gabarits d'appel d'outil, un défaut qui n'apparaît que
    d'un côté. Interdire l'argument revenait donc à parier sur un gabarit.

    Un nom INCONNU rend le schéma complet plutôt qu'une erreur, et le dit. Le
    modèle qui invente un nom de table a besoin de voir les vrais, pas d'un
    refus : c'est la même boucle que celle de ``run_sql``, qui rend son erreur
    SQL en texte au lieu de lever.
    """
    if not table_name.strip():
        return schema.to_prompt()
    vise = table_name.strip().strip("\"`'").lower()
    for table in schema.tables:
        if table.name.lower() == vise:
            return table.to_ddl()
    connues = ", ".join(schema.table_names()) or "(aucune)"
    return (
        f"Table {table_name!r} inconnue dans cette source. Tables existantes : "
        f"{connues}. Voici le schéma complet :\n\n{schema.to_prompt()}"
    )


def run_retrieval(
    question: str,
    *,
    adapter: DatabaseAdapter,
    model: Model | None = None,
    settings: Settings | None = None,
    dictionary: str | None = None,
) -> RetrievalResult:
    """Répond à une question par une requête SQL sur la source fournie.

    ``dictionary`` est le Markdown déclaré par la source (``dictionary_text()``).
    ``None`` = la source n'en déclare pas. Il est taillé au budget ICI et non
    chez l'appelant : le plafond est un réglage de cet agent, et un appelant qui
    l'oublierait renverrait un prompt sans plafond sans s'en apercevoir.
    """
    settings = settings or get_settings()
    dictionnaire = preparer(dictionary, settings.retrieval_dictionary_max_chars)
    deps = RetrievalDeps(
        adapter=adapter,
        max_rows=settings.retrieval_max_rows,
        dictionnaire=dictionnaire,
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
        dictionary_notice=dictionnaire.avis,
    )
