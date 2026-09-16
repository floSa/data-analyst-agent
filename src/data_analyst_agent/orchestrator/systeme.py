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
from data_analyst_agent.agents.retrieval.catalog import Catalog, Source, open_source
from data_analyst_agent.agents.retrieval.faits import RelevesDuCatalogue
from data_analyst_agent.orchestrator import introspection


def _trouver_la_source(catalogue: Catalog, nom: str) -> Source | None:
    """La source dont le nom est CELUI-LÀ, à la typographie près.

    `replie` et non `.lower()`, et c'est un défaut mesuré : « C'est quoi la
    source Télémétrie ? » ne trouvait rien — `télémétrie` n'est pas `telemetrie`
    pour une comparaison littérale — et l'outil repliait sur le catalogue
    entier. L'utilisateur écrit le nom d'une source comme un mot français, avec
    ses accents et ses majuscules ; le catalogue l'écrit comme un identifiant.
    Comparer les deux tels quels, c'est exiger de l'utilisateur qu'il tape comme
    un fichier de configuration.

    Le reste du socle repliait déjà (`introspection._nomme`), ce qui donnait le
    pire des cas : la source SE LIAIT (« je travaille sur telemetrie ») et la
    description, elle, ne la reconnaissait pas.
    """
    vise = introspection.replie(nom)
    return next((s for s in catalogue.sources if introspection.replie(s.name) == vise), None)


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

    def decrire_les_sources(self, cible: str = "") -> str:
        """Le catalogue, ou UNE source quand la question ne porte que sur elle.

        **Un outil qui ne sait pas se restreindre fait déballer tout le reste.**
        Mesuré sur le catalogue de démonstration : « de quoi parle la source
        interventions et de quel type est-elle ? » rendait les CINQ fiches, avec
        leurs volumes et leurs périodes, pour une question qui en visait une. On
        ne lit pas cinq paragraphes pour savoir qu'un fichier CSV porte la main
        courante de la maintenance. C'est la même famille que `get_schema` sans
        argument : le modèle demande le détail d'une chose, l'outil n'a que le
        tout à rendre, et c'est l'utilisateur qui paie la différence.

        **Quand rien n'est nommé et qu'une source est LIÉE, c'est d'elle qu'on
        parle.** « et elle contient quoi ? » ne nomme personne : le sujet de la
        phrase est la source de travail, et rendre le catalogue entier à cette
        question-là, c'est répondre à quelqu'un d'autre. Même règle que
        `schema_d_une_source`, qui la tient déjà.

        Un nom INCONNU rend le catalogue entier plutôt qu'une erreur, comme
        partout ici : celui qui se trompe de nom a besoin de voir les vrais.
        """
        vise = cible.strip().strip("\"`'") or self.source_de_travail
        faits = self.releves.tous() if self.releves is not None else None
        if vise.strip():
            trouvee = _trouver_la_source(self.catalogue_declare, vise)
            if trouvee is not None:
                releve = self.releves.de(trouvee.name) if self.releves is not None else None
                return self.retenir(
                    "sources_de_donnees", introspection.fiche_de_source(trouvee, releve)
                )
        return self.retenir(
            "sources_de_donnees",
            introspection.decrire_les_sources(self.catalogue_declare, faits),
        )

    def retenir_la_liaison(self, demandee: str) -> str:
        """Enregistre la source que le modèle veut lier, et rend son accueil.

        Un nom INCONNU ne lève pas : il rend la liste des sources déclarées,
        comme ``_schema_lisible`` rend le schéma complet sur une table inventée.
        Le modèle qui se trompe de nom a besoin de voir les vrais, pas d'un
        refus — et l'outil compte alors comme appelé sans que rien ne soit lié,
        donc le tour repart au planificateur.
        """
        vise = demandee.strip().strip("\"`'").lower()
        if not vise:
            # AUCUN nom écrit. C'est ici que le tour se jouait : le modèle
            # rangeait « tu bosses sur quoi ? » sous cet outil — même verbe —,
            # ne pouvait pas remplir `source`, et abandonnait le tour ENTIER en
            # répondant ``AUTRE``. La question repartait au planificateur, qui
            # la classait `query` et demandait de choisir une source.
            #
            # Un outil qui ne peut pas remplir son argument doit avoir quelque
            # chose à rendre, sinon c'est le tour que le modèle rend. Et ce
            # qu'il rend ici n'est pas un pis-aller : « sur quoi travailles-tu ? »
            # demande l'inventaire, et l'inventaire est la bonne réponse. Rien
            # n'est lié — ``source_a_lier`` reste vide — mais l'outil COMPTE
            # comme appelé, donc l'agent système répond au lieu d'abdiquer.
            faits = self.releves.tous() if self.releves is not None else None
            return self.retenir(
                "travailler_sur_une_source",
                introspection.decrire_les_sources(self.catalogue_declare, faits),
            )
        trouvee = _trouver_la_source(self.catalogue_declare, vise)
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
    def sources_de_donnees(ctx: RunContext[SystemeDeps], cible: str = "") -> str:
        """Les sources déclarées : nom, type, description, volume et période couverte.

        Cet outil ne donne NI les tables, NI les colonnes, NI les types de
        données : pour cela, c'est `schema_d_une_source`.

        `cible` : le nom d'UNE source, quand la question ne porte que sur
        celle-là (« de quoi parle interventions ? »). Laisse vide pour le
        catalogue entier (« quelles sources as-tu ? ») — sauf si une source de
        travail est déjà liée, auquel cas c'est d'elle qu'on parle.

        Le volume et la période sont LUS dans chaque source, jamais déduits de
        son nom : c'est la différence entre décrire un catalogue et le raconter.
        """
        return ctx.deps.decrire_les_sources(cible)

    @agent.tool
    def schema_d_une_source(ctx: RunContext[SystemeDeps], cible: str = "") -> str:
        """Ce qu'une source contient ET ce que son contenu VEUT DIRE.

        Rend deux choses pour ce que la question vise : d'une part les tables,
        les colonnes, leurs types et leurs clés, lus dans la source elle-même ;
        d'autre part ce que le DICTIONNAIRE de cette source écrit sur la
        colonne visée — l'unité dans laquelle elle est exprimée, ce que
        signifie chacun de ses codes, et quelles de ses valeurs n'en sont pas
        (un compteur muet, une saisie absente) et faussent donc un calcul.

        C'est la seule façon de répondre à ce qu'une colonne VEUT DIRE : le DDL
        ne donne qu'un type, et le sens n'est écrit nulle part ailleurs.

        `cible` : ce sur quoi porte la question — un nom de source
        (« titanic »), de table (« passengers ») ou de colonne (« class_id »).
        Laisse vide pour le tour d'horizon de toutes les sources.
        """
        # `cible` d'abord : ce que le modèle a explicitement désigné prime sur
        # ce que la phrase de l'utilisateur laisse deviner.
        precision = f"{cible} {ctx.deps.question}".strip()
        return ctx.deps.retenir(
            "schema_d_une_source",
            introspection.decrire_le_schema(
                precision, _ontologies(precision, ctx.deps), ctx.deps.source_de_travail
            ),
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

        Si le message ne NOMME aucune source, appelle-le avec une `source` VIDE :
        il rend l'inventaire.

        `source` : le nom de la source, tel qu'il est écrit dans le catalogue,
        ou la chaîne vide.
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
