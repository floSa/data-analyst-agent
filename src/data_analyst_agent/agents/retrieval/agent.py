"""Capacité ① — agent text-to-SQL à tools, self-correction sur erreur SQL.

Le modèle dispose de trois tools typés (list_tables, get_schema, run_sql) ;
une erreur SQL lui est renvoyée en texte pour qu'il corrige sa requête —
le nombre total d'allers-retours est borné (retrieval_request_limit).

Le prompt système porte aussi le DICTIONNAIRE de la source quand elle en déclare
un : le schéma dit les types, le dictionnaire dit ce que les valeurs veulent
dire, et c'est au moment d'écrire le WHERE qu'on en a besoin
(cf. `agents/dictionnaire`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from data_analyst_agent import prompts
from data_analyst_agent.agents.analysis.consigne import FiltreMonte
from data_analyst_agent.agents.dictionnaire import (
    EN_TETE_SQL,
    DictionnaireInjecte,
    bloc_de_prompt,
    preparer,
)
from data_analyst_agent.agents.retrieval.classement import grandeurs_non_projetees
from data_analyst_agent.agents.retrieval.sql import (
    DatabaseAdapter,
    QueryError,
    QueryResult,
    SchemaInfo,
)
from data_analyst_agent.agents.retrieval.verification import (
    Sonde,
    somme_multipliee,
    somme_sql_sans_son_filtre,
    sonde_de_l_adaptateur,
)
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model

# Ce qu'on renvoie au modèle quand sa requête ordonne le résultat sur une
# grandeur que le SELECT ne rend pas (cf. `agents/retrieval/classement`).
#
# Appendu au résultat, jamais à sa place : le tableau est déjà calculé, il est
# juste, et le retenir pour forcer une correction transformerait un palmarès
# sans ses chiffres en tour mort. Le modèle reste libre de répondre avec ce
# qu'il a — c'est exactement le comportement d'avant ce correctif.
#
# **Sans porte de sortie, et c'est mesuré.** Une première rédaction offrait au
# modèle de garder son résultat « si le tri ne sert qu'à rendre la sortie
# lisible » : il a pris cette sortie 3 fois sur 3 sur la question qui a motivé
# tout ceci, et la relance ne réparait rien. Une consigne qui propose de ne
# rien faire se fait suivre à la lettre. Le coût de l'avoir retirée est qu'un
# tri purement cosmétique paiera un aller-retour — borné à UN par récupération,
# et mesuré à côté sur les 48 questions du parcours.
RELANCE_DE_CLASSEMENT = (
    "REMARQUE — ta requête ordonne le résultat sur {grandeurs}, que le SELECT ne "
    "rend pas. Le tableau ci-dessus montre l'ordre sans montrer ce qui le décide : "
    "il ne dit pas de combien le premier devance le second, et ne se vérifie pas. "
    "Ajoute {grandeurs} au SELECT, en gardant les colonnes déjà présentes, et "
    "rappelle run_sql avant de répondre."
)


# Ce qu'on rend quand le budget d'allers-retours s'épuise APRÈS au moins une
# requête réussie.
#
# **Le défaut : un résultat réussi, jeté** (C64). Mesuré 3 fois sur 3 sur « au
# total, combien d'unités sont sorties de l'atelier et combien sont parties en
# commande ? » : une requête aboutit, reçoit les deux remarques, et les essais
# suivants échouent tous sur une erreur de binder jusqu'à `request_limit`.
# L'exception remontait, le nœud de récupération la changeait en incident, et
# l'utilisateur lisait « la source de données n'a pas pu être interrogée » alors
# qu'un tableau attendait. C61 a posé la règle — jamais en silence, jamais jeté
# — et c'était le seul endroit du chemin SQL où l'on jetait.
#
# On sert donc la DERNIÈRE requête réussie, avec ce qu'elle a de suspect
# (`deps.avertissement`, réécrit à chaque réussite) et avec le fait qu'elle
# n'est pas une réponse aboutie : le modèle cherchait encore quand le budget
# s'est épuisé. Sans une seule requête réussie, il n'y a rien à servir et
# l'exception repart telle quelle : c'est bien un incident.
RESUME_DE_LA_LIMITE = (
    "Le nombre d'allers-retours autorisés a été atteint avant que la requête soit "
    "aboutie. Voici le dernier résultat obtenu."
)
AVERTISSEMENT_DE_LA_LIMITE = (
    "Avertissement sur ce calcul : le nombre d'allers-retours autorisés a été atteint "
    "avant qu'une requête corrigée aboutisse. Ce résultat est le dernier qui ait été "
    "obtenu, et il n'a pas été confirmé."
)


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
    # Ce qui manque encore à la requête qui SERT les chiffres — une somme
    # multipliée par une jointure, une somme sans le filtre que sa source
    # déclare ("" = rien à dire). La relance a été tentée et n'a pas corrigé :
    # on sert le tableau, et on dit ce qu'il a de suspect. Jamais en silence,
    # jamais jeté (cf. `agents/retrieval/verification`).
    avertissement: str = ""
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
    # Les filtres que les sources montées déclarent sur leurs SOMMES
    # (``filtre_des_sommes``). Vide = aucune source du périmètre n'en déclare,
    # et la vérification du filtre ne dit alors jamais rien.
    filtres: list[FiltreMonte] = field(default_factory=list)
    executed: list[ExecutedQuery] = field(default_factory=list)
    last_success: tuple[str, QueryResult] | None = None
    # Outils réellement appelés. Un modèle peut répondre SANS en toucher aucun,
    # de mémoire, sur un jeu de données célèbre (« iris est un ensemble classique
    # de classification floristique… ») : c'est du savoir encyclopédique, pas une
    # lecture de la source, et sur des données privées ce serait de l'invention.
    tools_used: list[str] = field(default_factory=list)
    # Une relance de classement, et une seule, par récupération. Sans ce verrou,
    # un modèle qui passe outre se la verrait resservir à chaque requête
    # suivante, et la boucle mangerait `retrieval_request_limit` sur une
    # remarque qu'il a déjà lue.
    relance_de_classement: bool = False
    # Une relance par propriété du SQL, et une seule, pour la même raison. Deux
    # verrous et non un : une requête peut multiplier ET oublier son filtre, et
    # le modèle qui répare la jointure doit encore pouvoir s'entendre dire le
    # filtre. Deux allers-retours au pire, sous `retrieval_request_limit`.
    relance_de_multiplication: bool = False
    relance_de_filtre: bool = False
    # Ce qui reste à dire de la DERNIÈRE requête réussie — celle qui sert les
    # chiffres. Réécrit à chaque requête réussie : une requête corrigée efface
    # l'avertissement de celle qu'elle remplace.
    avertissement: str = ""
    # La sonde de cardinalité, construite au premier besoin : elle lit le
    # schéma, et une récupération qui n'écrit aucune somme n'a pas à le payer.
    _sonde: Sonde | None = None

    def sonde(self) -> Sonde:
        if self._sonde is None:
            self._sonde = sonde_de_l_adaptateur(self.adapter, self.adapter.schema())
        return self._sonde

    def tables_connues(self) -> dict[str, str]:
        return {t.name.lower(): t.name for t in self.adapter.schema().tables}


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
        rendu = result.to_markdown()
        remarques = _remarques(ctx.deps, query)
        return f"{rendu}\n\n{chr(10).join(remarques)}" if remarques else rendu

    return agent


def _remarques(deps: RetrievalDeps, query: str) -> list[str]:
    """Ce qu'on rend au modèle EN PLUS du tableau, et ce qu'on retient pour l'utilisateur.

    Trois propriétés du SQL, toutes vraies ou fausses quelle que soit la
    question : le palmarès qui porte ses chiffres (`classement`), la somme que
    la jointure ne doit pas multiplier et la somme que sa source oblige à
    filtrer (`verification`).

    **Appendues au résultat, jamais à sa place.** Le tableau est calculé ; le
    retenir pour forcer une correction transformerait un tour en tour mort. Le
    modèle reste libre de répondre avec ce qu'il a.

    **Chacune bornée à une relance par récupération.** Sans ce verrou, un modèle
    qui passe outre se verrait resservir la même remarque à chaque requête, et
    la boucle mangerait ``retrieval_request_limit`` sur un texte qu'il a déjà lu.

    ``deps.avertissement`` est réécrit à CHAQUE requête réussie, et non
    accumulé : il décrit la dernière, qui est celle qui sert les chiffres. Une
    requête corrigée efface donc ce qu'on disait de celle qu'elle remplace.
    """
    remarques: list[str] = []
    avertissements: list[str] = []

    absentes = grandeurs_non_projetees(query)
    if absentes and not deps.relance_de_classement:
        deps.relance_de_classement = True
        grandeurs = " et ".join(f"`{g}`" for g in absentes)
        remarques.append(RELANCE_DE_CLASSEMENT.format(grandeurs=grandeurs))

    connues = deps.tables_connues()
    multipliee = somme_multipliee(query, connues, deps.sonde())
    if multipliee is not None:
        avertissements.append(multipliee.pour_l_utilisateur())
        if not deps.relance_de_multiplication:
            deps.relance_de_multiplication = True
            remarques.append(multipliee.pour_le_modele())

    non_filtree = somme_sql_sans_son_filtre(query, connues, deps.filtres)
    if non_filtree is not None:
        avertissements.append(non_filtree.pour_l_utilisateur())
        if not deps.relance_de_filtre:
            deps.relance_de_filtre = True
            remarques.append(non_filtree.pour_le_modele())

    deps.avertissement = "\n\n".join(avertissements)
    return remarques


def composer_le_prompt(dialect: str, dictionnaire: DictionnaireInjecte | None) -> str:
    """Le prompt système de l'agent SQL : la démarche, puis le dictionnaire.

    Le dictionnaire vient APRÈS la démarche et non avant : ce qu'on lit en
    dernier est ce qu'on a sous les yeux au moment d'écrire, et l'erreur qu'on
    corrige est une erreur d'écriture de requête. Vide quand la source ne
    déclare rien — le prompt est alors, au caractère près, celui d'avant.
    """
    base = prompts.render(prompts.RETRIEVAL, dialect=dialect)
    bloc = bloc_de_prompt(dictionnaire, EN_TETE_SQL) if dictionnaire is not None else ""
    return f"{base}\n\n{bloc}" if bloc else base


def _schema_lisible(schema: SchemaInfo, table_name: str) -> str:
    """Le schéma, réduit à ``table_name`` quand elle existe — complet sinon.

    **Un argument accepté plutôt qu'un argument interdit.** L'outil n'en prenait
    aucun ; le modèle lui passait ``{"table_name": ...}``, la validation
    refusait, il réessayait à l'identique, et le budget de reprise valant 1, le
    tour mourait sur « la source de données n'a pas pu être interrogée ».
    Mesuré 9 fois sur 9, sur trois sources : le modèle passe l'argument dès
    qu'il croit utile de cibler une table. Interdire l'argument revenait à
    parier sur le gabarit d'appel d'outil ; l'accepter ne parie sur rien.

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
    filtres: list[FiltreMonte] | None = None,
) -> RetrievalResult:
    """Répond à une question par une requête SQL sur la source fournie.

    ``dictionary`` est le Markdown déclaré par la source (``dictionary_text()``).
    ``None`` = la source n'en déclare pas. Il est taillé au budget ICI et non
    chez l'appelant : le plafond est un réglage de cet agent, et un appelant qui
    l'oublierait renverrait un prompt sans plafond sans s'en apercevoir.

    ``filtres`` porte les filtres que les sources montées déclarent sur leurs
    SOMMES, sous les noms de tables du périmètre — même déclaration et même
    règle que pour le code d'analyse (``agents/analysis/consigne``). Une requête
    qui somme sans l'un d'eux repart au modèle avec le fait ; si la relance ne
    corrige pas, la réponse est servie AVEC l'avertissement.

    Le budget d'allers-retours épuisé ne jette RIEN de ce qui a abouti : la
    dernière requête réussie est servie, avec ce qu'elle a de suspect et avec
    le fait que le modèle cherchait encore (cf. ``RESUME_DE_LA_LIMITE``).
    """
    settings = settings or get_settings()
    dictionnaire = preparer(dictionary, settings.dictionary_max_chars)
    deps = RetrievalDeps(
        adapter=adapter,
        max_rows=settings.retrieval_max_rows,
        dictionnaire=dictionnaire,
        filtres=list(filtres or []),
    )
    agent = build_retrieval_agent()
    try:
        resume = agent.run_sync(
            question,
            model=model or build_model(settings),
            deps=deps,
            usage_limits=UsageLimits(request_limit=settings.retrieval_request_limit),
        ).output
    except UsageLimitExceeded:
        if deps.last_success is None:
            raise  # rien n'a abouti : il n'y a pas de chiffre à servir
        resume = RESUME_DE_LA_LIMITE
        deps.avertissement = "\n\n".join(
            m for m in (AVERTISSEMENT_DE_LA_LIMITE, deps.avertissement) if m
        )
    sql, result = deps.last_success if deps.last_success else (None, None)
    return RetrievalResult(
        summary=resume,
        sql=sql,
        result=result,
        executed=deps.executed,
        tools_used=deps.tools_used,
        avertissement=deps.avertissement,
        dictionary_notice=dictionnaire.avis,
    )
