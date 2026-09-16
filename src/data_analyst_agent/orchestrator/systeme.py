"""L'agent système : le MODÈLE reconnaît la question sur soi, et formule les faits.

Six outils typés (`pydantic-ai`), sur le modèle des trois outils de l'agent
SQL qui fonctionnent déjà avec ``gemma4:e4b``. Chacun rend un texte de
:mod:`data_analyst_agent.orchestrator.introspection`, donc un texte construit
depuis un artefact du dépôt — catalogue, registre, schémas d'attributs,
ontologie réelle de la source. Le modèle décide d'appeler, l'outil rend les
faits, le modèle les formule.

**Le sixième ne DIT pas, il FAIT** : ``travailler_sur_une_source`` retient une
source comme source de travail de la conversation. C'est le second chemin de la
liaison d'une source — le premier, déterministe, ne reconnaît qu'un message
réduit au nom d'une source, et une phrase polie lui échappait. Il est ici et
non dans le planificateur parce que les deux voies ont été mesurées : celle du
planificateur répare toutes les ouvertures et AVALE neuf questions sur douze
qui nommaient une source en en posant une (`docs/sources-de-demonstration.md`,
dette A). Comme les cinq autres, il ne décide de rien tout seul : c'est
l'appelant qui confronte la source demandée au texte de l'utilisateur avant de
lier quoi que ce soit.

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
    # La source que l'outil de liaison a retenue ("" = aucune demande). L'outil
    # ne CHANGE rien de lui-même : il enregistre une demande que l'appelant
    # confrontera au texte de l'utilisateur avant de lier quoi que ce soit.
    source_a_lier: str = ""
    # La source de travail au moment du tour, pour que l'accueil puisse dire
    # celle qu'on QUITTE. "" = aucune, ce qui est le premier tour d'un fil.
    source_de_travail: str = ""

    def retenir(self, outil: str, texte: str) -> str:
        self.outils_appeles.append(outil)
        self.faits.append(texte)
        return texte

    def retenir_la_liaison(self, demandee: str) -> str:
        """Enregistre la source que le modèle veut lier, et rend son accueil.

        Un nom INCONNU ne lève pas : il rend la liste des sources déclarées,
        comme ``_schema_lisible`` rend le schéma complet sur une table inventée.
        Le modèle qui se trompe de nom a besoin de voir les vrais, pas d'un
        refus — et l'outil compte alors comme appelé sans que rien ne soit lié,
        donc le tour repart au planificateur.
        """
        vise = demandee.strip().strip("\"`'").lower()
        trouvee = next((s for s in self.catalogue_declare.sources if s.name.lower() == vise), None)
        if trouvee is None:
            # Les noms entre accents graves, comme partout dans `introspection` :
            # c'est ainsi que la vérification d'après-coup reconnaît ce qu'un
            # fait NOMME, et donc ce qu'une réponse doit reprendre
            # (``defaut_de_fondation``). Sans eux, la formulation du modèle
            # passait en omettant les vraies sources.
            connues = ", ".join(f"`{s.name}`" for s in self.catalogue_declare.sources) or "(aucune)"
            return self.retenir(
                "travailler_sur_une_source",
                f"Source `{demandee}` inconnue. Sources déclarées : {connues}.",
            )
        releve = self.releves.de(trouvee.name) if self.releves is not None else None
        self.source_a_lier = trouvee.name
        # La FICHE, pas l'accueil : l'accueil promet de garder la source pour la
        # suite, et cette promesse n'est tenue que si l'appelant lie vraiment.
        # C'est lui qui la formule, une fois la liaison décidée.
        return self.retenir(
            "travailler_sur_une_source", introspection.fiche_de_source(trouvee, releve)
        )


@dataclass(frozen=True)
class ResultatSysteme:
    """Ce qu'a produit l'agent système, et de quoi le juger.

    ``outils_appeles`` vide = la question n'était pas pour lui.
    """

    reponse: str
    faits: str
    outils_appeles: tuple[str, ...]
    # La source que l'outil de liaison a retenue ("" = aucune demande).
    source_a_lier: str = ""

    @property
    def concerne_le_systeme(self) -> bool:
        return bool(self.outils_appeles)


def build_systeme_agent() -> Agent[SystemeDeps, str]:
    """L'agent et ses six outils : cinq sujets que le dépôt documente, et la liaison."""
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
    def travailler_sur_une_source(ctx: RunContext[SystemeDeps], source: str) -> str:
        """Retient une source comme source de TRAVAIL de la conversation, et l'accueille.

        Appelle-le quand le message désigne une source pour y travailler et ne
        demande rien sur son contenu. Le test, et lui seul : retire le nom de la
        source du message ; s'il ne reste aucune question à laquelle une requête
        ou un calcul répondrait, c'est cet outil. S'il en reste une — même
        incidente, même polie — n'appelle AUCUN outil : réponds AUTRE et laisse
        la question suivre son chemin.

        `source` : le nom de la source, tel qu'il est écrit dans le catalogue.
        """
        return ctx.deps.retenir_la_liaison(source)

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
    source_de_travail: str = "",
) -> ResultatSysteme:
    """Soumet la question à l'agent système et rend ce qu'il en a fait."""
    deps = SystemeDeps(
        catalogue_declare=catalogue_declare,
        catalogue_effectif=catalogue_effectif,
        registre=registre,
        question=question,
        releves=releves,
        source_de_travail=source_de_travail,
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
        source_a_lier=deps.source_a_lier,
    )
