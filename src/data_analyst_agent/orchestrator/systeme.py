"""L'agent système : le MODÈLE reconnaît la question sur soi, et formule les faits.

Cinq outils typés (`pydantic-ai`), sur le modèle des trois outils de l'agent
SQL qui fonctionnent déjà avec ``gemma4:e4b``. Chacun rend un texte de
:mod:`data_analyst_agent.orchestrator.introspection`, donc un texte construit
depuis un artefact du dépôt — catalogue, registre, schémas d'attributs,
ontologie réelle de la source. Le modèle décide d'appeler, l'outil rend les
faits, le modèle les formule.

**Ce qui a remplacé le lexique, et pourquoi.** La reconnaissance était un
lexique de tournures écrites à la main (`introspection.LEXIQUE`, retiré). Il
était précis et pas exhaustif, et c'était son plafond : mesuré par le
propriétaire sur dix formulations naturelles de la MÊME question (« quelles
données as-tu ? »), il en court-circuitait trois et laissait les sept autres
partir au planificateur, qui les classait `query` et écrivait du SQL pour
répondre à une question de configuration — « tu as acces à quelles données »,
« c'est quoi ton périmètre ? », « tu bosses sur quoi ? »… Aucune ligne de
lexique n'aurait couvert cette famille : elle est ouverte, et un lexique est
une liste.

**Le signal de routage est l'appel d'outil, pas le texte.** Le prompt demande
au modèle de répondre ``AUTRE`` quand la question porte sur les données et non
sur l'agent, mais rien ne repose sur ce mot : ``outils_appeles`` vide veut dire
« ce n'est pas une question sur le système », et le tour repart au
planificateur comme avant. Un modèle qui paraphrase la consigne, ou qui répond
de mémoire sans rien regarder, ne peut donc pas se faire servir.

**Et ce qu'il formule est vérifié.** Les faits rendus par les outils sont
conservés (``faits``) : l'appelant compare la formulation du modèle à ce
qu'elle est censée porter (``introspection.defaut_de_fondation``) et sert les
faits eux-mêmes si elle invente un nom ou en omet un. Le déterministe n'est pas
jeté — il est devenu à la fois la matière du chemin principal et sa ceinture.

L'entrée/sortie vit ici et non dans ``introspection``, qui reste pur : c'est
l'outil ``schema_d_une_source`` qui ouvre les connexions, et lui seul — les
quatre autres sujets sont entièrement déterminés par la configuration.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from data_analyst_agent import prompts
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, open_source
from data_analyst_agent.agents.retrieval.faits import RelevesDuCatalogue
from data_analyst_agent.orchestrator import introspection


@dataclass
class SystemeDeps:
    """Ce que les outils ont le droit de lire.

    Deux catalogues, et la distinction n'est pas décorative — c'est la même que
    dans ``PlanContext``. Le **déclaré** pour énumérer les sources : un tableau
    intermédiaire de la conversation n'est pas une source de données, et
    l'annoncer comme telle induirait en erreur. L'**effectif** pour aller lire
    un schéma, parce qu'un tableau mémorisé est bel et bien interrogeable.
    """

    catalogue_declare: Catalog
    catalogue_effectif: Catalog
    registre: Registry
    # Les faits lus dans les sources — tables, lignes, période — relevés une
    # fois et gardés (cf. ``agents/retrieval/faits``). ``None`` = on n'a rien
    # à lire : l'inventaire est alors celui du YAML seul, ce qui est le
    # comportement d'avant. Il n'invente jamais rien pour combler.
    releves: RelevesDuCatalogue | None = None
    # Le message de l'utilisateur, tel quel. Il complète l'argument que le
    # modèle passe à l'outil de schéma : « class_id » seul ne désigne pas une
    # colonne quand deux sources sont déclarées, alors que la phrase qui
    # l'entoure nomme sa table. Mesuré — le modèle a passé `cible="passengers"`
    # à « que signifie la colonne class_id ? » et a reçu la table entière au
    # lieu de la fiche de la colonne.
    question: str = ""
    # Ce que chaque outil a rendu, dans l'ordre des appels. C'est la matière de
    # la vérification d'après-coup, et le repli servi si elle échoue.
    faits: list[str] = field(default_factory=list)
    outils_appeles: list[str] = field(default_factory=list)

    def retenir(self, outil: str, texte: str) -> str:
        self.outils_appeles.append(outil)
        self.faits.append(texte)
        return texte


@dataclass(frozen=True)
class ResultatSysteme:
    """Ce qu'a produit l'agent système, et de quoi le juger.

    ``outils_appeles`` vide = la question n'était pas pour lui.
    """

    reponse: str
    faits: str
    outils_appeles: tuple[str, ...]

    @property
    def concerne_le_systeme(self) -> bool:
        return bool(self.outils_appeles)


def build_systeme_agent() -> Agent[SystemeDeps, str]:
    """L'agent et ses cinq outils, un par sujet que le dépôt sait documenter."""
    agent: Agent[SystemeDeps, str] = Agent(deps_type=SystemeDeps, output_type=str)

    @agent.system_prompt
    def system_prompt(ctx: RunContext[SystemeDeps]) -> str:
        return prompts.gabarit(prompts.SYSTEME)

    @agent.tool
    def capacites_de_l_agent(ctx: RunContext[SystemeDeps]) -> str:
        """Ce que cet agent sait faire, et sur quoi (« que sais-tu faire ? »)."""
        return ctx.deps.retenir(
            "capacites_de_l_agent",
            introspection.decrire_les_capacites(ctx.deps.catalogue_declare, ctx.deps.registre),
        )

    @agent.tool
    def sources_de_donnees(ctx: RunContext[SystemeDeps]) -> str:
        """Les sources déclarées : nom, type, description, volume et période couverte.

        Le volume et la période sont LUS dans chaque source, jamais déduits de
        son nom : c'est la différence entre décrire un catalogue et le raconter.
        """
        faits = ctx.deps.releves.tous() if ctx.deps.releves is not None else None
        return ctx.deps.retenir(
            "sources_de_donnees",
            introspection.decrire_les_sources(ctx.deps.catalogue_declare, faits),
        )

    @agent.tool
    def schema_d_une_source(ctx: RunContext[SystemeDeps], cible: str = "") -> str:
        """Tables, colonnes, types et clés d'une source, lus dans la source elle-même.

        `cible` : ce sur quoi porte la question — un nom de source
        (« titanic »), de table (« passengers ») ou de colonne (« class_id »).
        Laisse vide pour le tour d'horizon de toutes les sources.
        """
        # `cible` d'abord : ce que le modèle a explicitement désigné prime sur
        # ce que la phrase de l'utilisateur laisse deviner.
        precision = f"{cible} {ctx.deps.question}".strip()
        return ctx.deps.retenir(
            "schema_d_une_source",
            introspection.decrire_le_schema(precision, _ontologies(precision, ctx.deps)),
        )

    @agent.tool
    def modeles_de_prediction(ctx: RunContext[SystemeDeps]) -> str:
        """Les modèles de prédiction du registre : tâche, cible, classes, unité."""
        return ctx.deps.retenir(
            "modeles_de_prediction",
            introspection.decrire_les_modeles(ctx.deps.registre),
        )

    @agent.tool
    def attributs_d_un_modele(ctx: RunContext[SystemeDeps], modele: str = "") -> str:
        """Les attributs qu'un modèle attend pour prédire, avec le sens de chacun.

        `modele` : le nom du modèle (« titanic »). Laisse vide pour le tour
        d'horizon de ce que chaque modèle attend.
        """
        # Même complément que pour le schéma, et pour la même raison : « il te
        # faut quoi pour deviner l'espèce d'un iris ? » nomme le modèle dans la
        # phrase, et le modèle laisse parfois l'argument vide. Le tour
        # d'horizon reste rendu quand rien ne désigne un modèle en particulier.
        precision = f"{modele} {ctx.deps.question}".strip()
        return ctx.deps.retenir(
            "attributs_d_un_modele",
            introspection.decrire_les_features(
                ctx.deps.registre, introspection.dataset_vise(precision, ctx.deps.registre)
            ),
        )

    return agent


def _ontologies(precision: str, deps: SystemeDeps) -> list[introspection.Ontologie]:
    """Ce que les sources disent d'elles-mêmes — celle qui est visée, ou toutes.

    Quand ``precision`` désigne une source, une seule connexion est ouverte. Sinon
    on les ouvre toutes : « quelles colonnes dans la table passengers ? » ne
    nomme aucune source et n'est pourtant pas ambiguë, et le catalogue compte
    une poignée d'entrées par construction.
    """
    catalogue = deps.catalogue_effectif
    visee = introspection.source_visee(precision, catalogue)
    sources = [visee] if visee is not None else list(catalogue.sources)
    ontologies = []
    for source in sources:
        with closing(open_source(source)) as adapter:
            schema = adapter.schema()
        ontologies.append(introspection.Ontologie(source, schema, source.dictionary_text()))
    return ontologies


def run_systeme(
    question: str,
    *,
    model: Model,
    catalogue_declare: Catalog,
    catalogue_effectif: Catalog,
    registre: Registry,
    request_limit: int,
    releves: RelevesDuCatalogue | None = None,
) -> ResultatSysteme:
    """Soumet la question à l'agent système et rend ce qu'il en a fait."""
    deps = SystemeDeps(
        catalogue_declare=catalogue_declare,
        catalogue_effectif=catalogue_effectif,
        registre=registre,
        question=question,
        releves=releves,
    )
    run = build_systeme_agent().run_sync(
        question,
        model=model,
        deps=deps,
        usage_limits=UsageLimits(request_limit=request_limit),
    )
    return ResultatSysteme(
        reponse=run.output,
        faits="\n\n".join(deps.faits),
        outils_appeles=tuple(deps.outils_appeles),
    )
