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

import logging
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from data_analyst_agent import prompts
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, Source, open_source
from data_analyst_agent.agents.retrieval.faits import RelevesDuCatalogue
from data_analyst_agent.orchestrator import introspection

logger = logging.getLogger("data_analyst_agent.orchestrator")

# Ce qu'on garde de la réponse précédente. Elle peut peser un inventaire
# entier, et l'historique repart à chaque aller-retour de la boucle d'outils :
# une réponse non bornée se paierait autant de fois qu'il y a d'outils appelés.
REPONSE_PRECEDENTE_MAX_CARACTERES = 600


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
    # La part des faits qu'une réponse doit reprendre EN ENTIER. Tout, sauf ce
    # qu'un outil a servi pour que le modèle y CHOISISSE — « as-tu une source
    # qui parle de maintenance ? » se répond par une source, pas par cinq.
    faits_a_enumerer: list[str] = field(default_factory=list)
    # Les sources dont la FICHE a été servie, dans l'ordre. Une fiche doit être
    # portée par la réponse — pas seulement citée (cf. ``marques_a_porter``).
    fiches_servies: list[str] = field(default_factory=list)
    outils_appeles: list[str] = field(default_factory=list)
    # La source que l'outil de liaison a retenue ("" = aucune demande). L'outil
    # ne CHANGE rien de lui-même : il enregistre une demande que l'appelant
    # confrontera au texte de l'utilisateur avant de lier quoi que ce soit.
    source_a_lier: str = ""
    # La source de travail au moment du tour, pour que l'accueil puisse dire
    # celle qu'on QUITTE. "" = aucune, ce qui est le premier tour d'un fil.
    source_de_travail: str = ""

    def retenir(
        self, outil: str, texte: str, *, a_enumerer: bool = True, fiches: tuple[str, ...] = ()
    ) -> str:
        """Garde ce qu'un outil a rendu — et dit s'il faut le redire en entier.

        ``a_enumerer=False`` quand les faits sont de la MATIÈRE À CHOISIR et non
        une liste à réciter. La ceinture continue d'interdire d'inventer ; elle
        cesse d'exiger qu'on récite.

        ``fiches`` : les sources dont la FICHE part dans ce texte. Elles sont
        notées parce qu'une fiche se juge autrement qu'une liste — son nom cité
        ne suffit pas, il faut qu'un de ses faits survive à la reformulation
        (``introspection._fiche_citee_sans_un_fait``). L'inventaire du catalogue
        n'en est pas une : il énumère des noms, et les noms SONT sa réponse.
        """
        self.outils_appeles.append(outil)
        self.faits.append(texte)
        if a_enumerer:
            self.faits_a_enumerer.append(texte)
        self.fiches_servies.extend(n for n in fiches if n not in self.fiches_servies)
        return texte

    def marques_a_porter(self) -> dict[str, frozenset[str]]:
        """Ce qui, pour chaque fiche servie, prouve que la réponse l'a lue.

        Les marques sont calculées sur TOUT le catalogue déclaré et non sur les
        seules fiches servies : un mot commun aux cinq fiches n'est la marque
        d'aucune, et le mesurer sur deux fiches seulement en ferait une marque
        dès que les trois autres sont absentes du tour.
        """
        if not self.fiches_servies:
            return {}
        toutes = {s.name: self._fiche(s) for s in self.catalogue_declare.sources}
        marques = introspection.marques_des_fiches(toutes)
        return {nom: marques[nom] for nom in self.fiches_servies if nom in marques}

    def decrire_les_sources(
        self, cible: str = "", *, a_enumerer: bool = True, outil: str = "sources_de_donnees"
    ) -> str:
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

        **Le catalogue ENTIER n'est jamais la réponse à un message qui nomme ses
        sources.** C'est le plancher, et c'est une mesure qui l'a imposé. Le
        modèle SAIT boucler — mesuré le 2026-09-17 sur vLLM, « donne-moi un
        aperçu de ventes, production, stocks et titanic » émet quatre
        ``ToolCallPart`` dans la MÊME réponse, un par source, sans un
        aller-retour de plus. Il ne le fait pas toujours : sur sept
        formulations, cinq passaient encore par un appel qui rend TOUT — les
        unes par `chercher_une_source`, dont l'argument n'est pas lu, les
        autres par cet outil-ci avec `cible` vide. Même effet des deux côtés :
        cinq fiches pour qui en a nommé trois.

        La règle ne porte donc pas sur l'ARGUMENT que le modèle a passé — l'y
        mettre serait traiter cette phrase-ci et pas la suivante — mais sur ce
        que le MESSAGE nomme (``introspection.sources_nommees``). Quand il nomme
        des sources déclarées et que l'appel allait rendre le catalogue, ce sont
        leurs fiches qui partent, et elles sont à énumérer : un ensemble nommé
        n'est pas une matière à choisir, c'est la réponse. 3/21 avant, 18/21
        après, à nombre d'appels d'outil et d'appels LLM INCHANGÉ.

        **Elle est mécanique, et c'est le résultat de la mesure et non un goût.**
        Cinq formulations ont été écrites pour dire la même chose au modèle —
        trois dans la démarche du prompt, deux dans les fiches de ces deux
        outils. Chacune coûtait une question de la surface conversationnelle,
        trois tirages sur trois : `features-familier`, `volumetrie-globale`,
        `periode-directe`. Une phrase de prompt parle à tous les tours, une
        fiche d'outil à tous ceux qui pourraient l'appeler ; ce plancher-ci ne
        parle qu'aux tours où un outil allait rendre le catalogue entier alors
        que le message nommait ses sources. Le relevé complet est dans
        `docs/sources-metier.md`, et `test_aucune_fiche_d_outil_n_a_bouge` fige
        le résultat.

        Elle ne touche à rien de ce qui était déjà mesuré. Un message qui ne
        nomme AUCUNE source déclarée — la recherche par sujet, « as-tu quelque
        chose sur la maintenance ? », ou un nom inconnu — continue de recevoir
        tout le catalogue, à choisir ou à corriger. Et la source de travail
        garde son rang : elle ne parle que là où personne n'est nommé.

        Un nom INCONNU rend le catalogue entier plutôt qu'une erreur, comme
        partout ici : celui qui se trompe de nom a besoin de voir les vrais.

        ``outil`` : le nom de l'outil qui APPELLE, et il n'est pas décoratif.
        Cette méthode sert `sources_de_donnees` et `chercher_une_source`, et
        elle inscrivait le premier dans la trace quel que soit l'appelant. La
        trace disait donc `sources_de_donnees` sur des tours où le modèle avait
        appelé `chercher_une_source` — et c'est ce qui a caché la cause du
        défaut mesuré le 2026-09-17 : « qu'est-ce que t'appelles source vente,
        production, stock ? » était routée sur la RECHERCHE, dont l'argument
        n'est pas lu, qui rend les cinq sources et qui désarme la ceinture. La
        trace montrait l'outil qui restreint ; le fil brut montrait celui qui
        ne restreint pas. Une trace qui nomme un autre outil que celui qu'on a
        appelé fait chercher le défaut là où il n'est pas.
        """
        vise = cible.strip().strip("\"`'")
        faits = self.releves.tous() if self.releves is not None else None
        if vise:
            trouvee = _trouver_la_source(self.catalogue_declare, vise)
            if trouvee is not None:
                return self.retenir(outil, self._fiche(trouvee), fiches=(trouvee.name,))
        nommees = introspection.sources_nommees(self.question, self.catalogue_declare)
        if nommees:
            return self.retenir(
                outil,
                introspection.fiches_des_sources(nommees, faits),
                fiches=tuple(s.name for s in nommees),
            )
        if a_enumerer and self.source_de_travail:
            liee = _trouver_la_source(self.catalogue_declare, self.source_de_travail)
            if liee is not None:
                return self.retenir(outil, self._fiche(liee), fiches=(liee.name,))
        return self.retenir(
            outil,
            introspection.decrire_les_sources(self.catalogue_declare, faits),
            a_enumerer=a_enumerer,
        )

    def _fiche(self, source: Source) -> str:
        """La fiche d'une source, avec ce qu'on a LU dedans quand on l'a lu."""
        releve = self.releves.de(source.name) if self.releves is not None else None
        return introspection.fiche_de_source(source, releve)

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
    # La part de ``faits`` qu'une réponse doit reprendre en entier (cf.
    # ``SystemeDeps.retenir``). Vide = le modèle avait à CHOISIR, pas à réciter.
    faits_a_enumerer: str
    outils_appeles: tuple[str, ...]
    # La source que l'outil de liaison a retenue ("" = aucune demande).
    source_a_lier: str = ""
    # Pour chaque fiche servie, ce qui prouve qu'une réponse l'a lue (cf.
    # ``SystemeDeps.marques_a_porter``). Vide = aucune fiche n'est partie.
    marques_a_porter: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @property
    def concerne_le_systeme(self) -> bool:
        return bool(self.outils_appeles)


def build_systeme_agent() -> Agent[SystemeDeps, str]:
    """L'agent et ses sept outils : cinq sujets documentés, la recherche par sujet, la liaison.

    Le prompt part en ``instructions`` et non en ``system_prompt``, et ce n'est
    pas un détail de style : un ``system_prompt`` n'est émis par ``pydantic-ai``
    que lorsque l'historique de messages est VIDE (``UserPromptNode.run`` :
    ``if not messages: parts.extend(await self._sys_parts(...))``). Or cet
    agent reçoit désormais le tour d'avant comme historique. Des instructions,
    elles, sont réémises à chaque requête quel que soit l'historique — c'est
    exactement ce qu'il faut ici, et c'est mesuré : l'historique passé sans
    instructions fait tourner cet agent SANS son prompt, donc sans la règle qui
    lui interdit d'inventer un nom.
    """
    agent: Agent[SystemeDeps, str] = Agent(
        deps_type=SystemeDeps, output_type=str, instructions=prompts.gabarit(prompts.SYSTEME)
    )

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

        Pour CHERCHER une source par ce dont elle parle, sans en connaître le
        nom, c'est `chercher_une_source`.

        Le volume et la période sont LUS dans chaque source, jamais déduits de
        son nom : c'est la différence entre décrire un catalogue et le raconter.
        """
        return ctx.deps.decrire_les_sources(cible)

    @agent.tool
    def chercher_une_source(ctx: RunContext[SystemeDeps], sujet: str) -> str:
        """Trouve LA source qui parle d'un sujet, quand la question ne la nomme pas.

        « As-tu quelque chose sur la maintenance ? », « des données sur ce que
        les clients ont payé ? », « tu as des trucs sur les pannes ? » : recopie
        dans `sujet` les mots de l'utilisateur. Tu reçois les descriptions de
        toutes les sources et tu désignes CELLE qui répond — une seule, ou
        aucune si aucune ne convient. Tu n'as pas à réciter les autres.

        Un outil à part et non un argument de `sources_de_donnees`, parce que
        mêler les deux a un coût mesuré : l'inventaire doit être repris EN
        ENTIER par la réponse (une liste de sources incomplète est une réponse
        fausse), et une recherche ne le doit pas. Le même outil pour les deux
        désarmait la ceinture sur des questions qui n'avaient rien à voir —
        « de quand datent tes données ? » est repassée de juste à vague, deux
        campagnes sur deux.
        """
        # `sujet` n'est pas lu ici : il sert à dire au MODÈLE quand appeler cet
        # outil, et à écrire dans la trace ce qu'il cherchait. La recherche,
        # c'est lui qui la fait — il a les descriptions sous les yeux, et il
        # reconnaît « ce que les clients ont payé » dans « factures clients »
        # là où une correspondance de mots ne le ferait pas.
        return ctx.deps.decrire_les_sources(a_enumerer=False, outil="chercher_une_source")

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
        # `cible` d'abord, et elle l'est VRAIMENT depuis le 2026-09-17 : elle
        # est passée à part, et non plus seulement collée devant la phrase de
        # l'utilisateur. Collée, elle ne primait pas — « dis-moi ce qu'il y a
        # dans ventes, production et iris » faisait rendre `iris` aux TROIS
        # appels, le nom de la table restant dans la phrase quel que soit
        # l'argument (cf. ``introspection.decrire_le_schema``).
        #
        # La phrase reste donnée en entier : c'est elle qui porte le NIVEAU de
        # détail — « que signifie la colonne class_id ? » nomme sa colonne dans
        # la phrase et sa table dans l'argument.
        precision = f"{cible} {ctx.deps.question}".strip()
        return ctx.deps.retenir(
            "schema_d_une_source",
            introspection.decrire_le_schema(
                precision, _ontologies(precision, ctx.deps), ctx.deps.source_de_travail, cible
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


def _le_tour_precedent_en_messages(echange: tuple[str, str] | None) -> list[ModelMessage]:
    """Le tour d'avant, donné comme de VRAIS messages — et non recopié dans le nouveau.

    **Ce que la forme d'avant coûtait, montré par le fil brut.** Le tour d'avant
    était recopié en tête du message de l'utilisateur, sous l'étiquette « TOUR
    PRÉCÉDENT […] MESSAGE À TRAITER MAINTENANT ». Mesuré sur vLLM le
    2026-09-16, amorce « Quelles sources de données as-tu ? », continuation
    « Oui » : le modèle répond ::

        TextPart("travailler_sur_une_source(source='referentiel')")

    Il a compris — il nomme le bon outil et le bon argument. Mais il l'ÉCRIT au
    lieu de l'ÉMETTRE : aucun ``ToolCallPart``, donc ``outils_appeles`` vide,
    donc « ce n'est pas une question sur le système », donc le tour repart au
    planificateur, qui rend « je n'ai pas bien compris ta demande » suivi du
    menu. Le même tour donné comme deux messages rend un vrai appel d'outil et
    la réponse attendue. Ce n'est pas la compréhension qui manquait, c'est la
    FORME : un bloc de prose qui raconte un dialogue ne met pas le modèle dans
    la position de continuer un dialogue.

    La réponse précédente reste bornée, et pour la raison d'avant : elle peut
    peser un inventaire entier, et l'historique repart à chaque aller-retour de
    la boucle d'outils.

    Le tour d'avant seulement, jamais la transcription : ce qui manque à
    « oui », à « et dedans ? » ou à « tu ne m'as pas répondu » est le tour
    d'avant, et le prompt de cet agent est déjà le plus chargé du socle.
    """
    if echange is None:
        return []
    precedente, reponse = echange
    dite = precedente.strip()
    extrait = reponse.strip()
    if not extrait:
        # Rien à continuer : sans réponse de l'agent, le message qui suit ne
        # reprend rien, et un historique réduit à une question sans réponse
        # coûterait des tokens pour ne rien porter.
        return []
    if len(extrait) > REPONSE_PRECEDENTE_MAX_CARACTERES:
        extrait = extrait[:REPONSE_PRECEDENTE_MAX_CARACTERES] + " […]"
    messages: list[ModelMessage] = []
    if dite:
        messages.append(ModelRequest(parts=[UserPromptPart(content=dite)]))
    messages.append(ModelResponse(parts=[TextPart(content=extrait)]))
    return messages


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


# À partir de combien de sources déclarées nommées un message cesse de pouvoir
# être un calcul. DEUX, et ce n'est pas un réglage : cette installation répond à
# une question sur les données en LIANT une source et en interrogeant celle-là
# (``PlanContext.source``, ``_regle_source_de_la_conversation``). Un message qui
# nomme deux sources déclarées ne désigne donc aucun calcul qu'elle sache faire ;
# ce qu'il demande sur plusieurs sources à la fois ne se lit que dans le
# catalogue. À UNE seule, la propriété tombe — « combien de commandes dans
# ventes ? » nomme `ventes` et se compte, et c'est mesuré : le modèle n'appelle
# aucun outil, et il a raison.
SOURCES_NOMMEES_HORS_DE_PORTEE_D_UN_CALCUL = 2


def _plancher_des_sources_nommees(deps: SystemeDeps) -> None:
    """Le message nomme PLUSIEURS sources déclarées et aucun outil n'a été appelé.

    Le second plancher, et le pendant exact du premier. Celui de
    ``decrire_les_sources`` répare ce qu'un outil SERT quand le modèle l'appelle
    ; celui-ci répare les tours où le modèle ne l'appelle pas du tout.

    **Mesuré le 2026-09-17, catalogue métier.** « titanic et iris, c'est quoi au
    juste ? » : aucun ``ToolCallPart``, réponse ``AUTRE``, et le tour repart au
    planificateur — qui n'a pas de capacité pour « décris-moi ces deux
    sources-là » et rend son repli. C'est la famille laissée à 0/3 en C41.
    « resume moi vite fait ventes, stocks, iris » tombe du même côté pour l'œil,
    par un autre chemin : le modèle appelle bien ``sources_de_donnees`` ET
    répond ``AUTRE``, et c'est la ceinture qui rattrape ce tour-là.

    **Pourquoi mécanique plutôt qu'écrit au modèle.** Cinq formulations ont déjà
    été ajoutées au prompt et aux fiches d'outils pour dire exactement cela ; les
    cinq ont été retirées, chacune coûtant une question de la surface
    conversationnelle, trois tirages sur trois. Un témoin de quatre lignes vides
    au même endroit ne coûtait rien : ce n'est pas la longueur, c'est le contenu.
    Ce plancher-ci ne parle à personne — il ne s'applique qu'aux tours où aucun
    outil n'a été appelé et où le message nomme deux sources du catalogue.

    **Et pourquoi DEUX.** Parce qu'à une seule, la propriété n'est plus vraie :
    « combien de commandes dans ventes ? » nomme une source déclarée et se
    compte sur ses lignes. Le modèle ne l'envoie à aucun outil, et il a raison —
    ce plancher ne doit pas le contredire.

    **Et l'autre bord, trouvé par un test et non par une relecture.** Un message
    réduit aux noms et à ce qui les relie — « ventes ou clients ? » — ne demande
    rien SUR ces sources : il hésite ENTRE elles, et la bonne réponse est la
    question qui fait choisir, celle du premier tour, qui lie la source à la
    conversation. Servir deux fiches à cette place-là répondrait à côté et
    laisserait le fil délié. Le décompte est celui que ``choix_de_source``
    mesure déjà (``MOTS_EN_PLUS_D_UN_CHOIX``), et la frontière est posée sur les
    formulations réellement mesurées : « titanic et iris, c'est quoi au juste ? »
    en dit six de plus, « ventes ou clients ? » un seul.

    Il n'invente pas de réponse : il sert les fiches, celles-là mêmes que l'outil
    aurait servies, et la ceinture fait le reste. ``outils_appeles`` porte alors
    le nom du plancher et non celui d'un outil — il n'y en a pas eu, et une trace
    qui nommerait un outil ferait chercher le défaut là où il n'est pas.
    """
    if deps.outils_appeles:
        return
    nommees = introspection.sources_nommees(deps.question, deps.catalogue_declare)
    if len(nommees) < SOURCES_NOMMEES_HORS_DE_PORTEE_D_UN_CALCUL:
        return
    reste = introspection.mots_hors_des_noms(deps.question, nommees)
    if len(reste) <= introspection.MOTS_EN_PLUS_D_UN_CHOIX:
        return
    faits = deps.releves.tous() if deps.releves is not None else None
    deps.retenir(
        "plancher_des_sources_nommees",
        introspection.fiches_des_sources(nommees, faits),
        fiches=tuple(s.name for s in nommees),
    )


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
    echange_precedent: tuple[str, str] | None = None,
) -> ResultatSysteme:
    """Soumet la question à l'agent système et rend ce qu'il en a fait.

    ``echange_precedent`` : le tour d'avant, ``(ce que l'utilisateur a dit, ce
    que l'agent a répondu)``. L'agent système n'en recevait AUCUN, et un message
    qui renvoie au tour précédent n'a alors aucun sens à trouver. Mesuré le
    2026-09-16, et c'est l'agent lui-même qui pose le piège : il demande
    « souhaitez-vous que je consulte le schéma de `referentiel` et
    `facturation` ? », l'utilisateur répond « oui », et « oui » tout seul ne
    concerne aucun outil — le tour repart au planificateur, qui rend « je n'ai
    pas bien compris ta demande ».

    Il part en ``message_history``, c'est-à-dire comme de VRAIS messages. Le
    recopier en tête du message de l'utilisateur ne suffisait pas, et le fil
    brut dit pourquoi : le modèle écrivait l'appel d'outil en prose au lieu de
    l'émettre (cf. ``_le_tour_precedent_en_messages``).
    """
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
        message_history=_le_tour_precedent_en_messages(echange_precedent),
        usage_limits=UsageLimits(request_limit=request_limit),
    )
    _plancher_des_sources_nommees(deps)
    return ResultatSysteme(
        reponse=run.output,
        faits="\n\n".join(deps.faits),
        faits_a_enumerer="\n\n".join(deps.faits_a_enumerer),
        outils_appeles=tuple(deps.outils_appeles),
        source_a_lier=deps.source_a_lier,
        marques_a_porter=deps.marques_a_porter(),
    )


# --- la seconde chance, avant le repli ---------------------------------------

# Les trois voies par lesquelles un texte peut partir à l'utilisateur. Ce sont
# des noms de VOIE et non des messages : la trace les porte, la mesure les
# compte, l'utilisateur n'en voit aucun.
VOIE_PREMIERE_PASSE = "1re passe"
VOIE_SECONDE_PASSE = "2e passe"
VOIE_REPLI = "repli"


@dataclass(frozen=True)
class ReponseServie:
    """Le texte qui part, et par quelle VOIE il part.

    ``voie`` répond à la seule question que la trace et la mesure se posent :
    l'utilisateur lit-il une formulation du modèle, ou le texte des faits ?
    ``defaut_premiere`` et ``defaut_seconde`` disent ce que la ceinture a
    reproché à chacune des deux formulations — vides quand il n'y avait rien à
    reprocher, ou quand la seconde n'a pas eu lieu.
    """

    texte: str
    voie: str
    defaut_premiere: str = ""
    defaut_seconde: str = ""

    @property
    def detail(self) -> str:
        """Ce que la trace écrit de la voie — et de ce qui l'a décidée.

        « faits servis tels quels » est la formule d'avant, gardée au mot près :
        c'est elle qu'un exploitant cherche dans une trace, et elle dit
        exactement ce qui s'est passé.
        """
        if self.voie == VOIE_PREMIERE_PASSE:
            return "formulé par le modèle"
        if self.voie == VOIE_SECONDE_PASSE:
            return f"reformulé au second tour ({self.defaut_premiere})"
        return (
            "faits servis tels quels "
            f"(1re passe : {self.defaut_premiere} ; 2e passe : {self.defaut_seconde})"
        )


def _demande_de_reparation(question: str, faits: str) -> str:
    """Ce que le tour de réparation reçoit : le message, et les faits déjà lus.

    Composé ici et non dans le gabarit, comme le contexte de la synthèse
    (``Orchestrator._synthesize_analysis``) : le fichier de prompt porte la
    MÉTHODE, qui ne change pas d'un tour à l'autre, et le message porte la
    MATIÈRE, qui ne vaut que pour celui-ci.
    """
    return f"LE MESSAGE DE L'UTILISATEUR\n\n{question}\n\nLES FAITS LUS POUR Y RÉPONDRE\n\n{faits}"


def build_reparation_agent() -> Agent[None, str]:
    """L'agent du tour de réparation : aucun outil, aucune dépendance, un aller.

    **Aucun outil, et c'est la moitié de l'idée.** Il ne peut pas aller chercher
    un fait de plus, donc il ne peut rien apprendre qu'on n'ait pas relevé, donc
    tout ce qu'il écrit se juge à la même ceinture que la première formulation.
    Il ne peut pas non plus boucler : un aller-retour, un seul, et le coût du
    tour de réparation est donc exactement UN appel LLM.

    ``system_prompt`` et non ``instructions``, contrairement à l'agent système :
    il n'y a pas d'historique ici — c'est un tour sans passé, comme la synthèse
    — et la restriction de ``pydantic-ai`` (un ``system_prompt`` n'est émis que
    sur un historique vide) ne le concerne donc pas.
    """
    return Agent(output_type=str, system_prompt=prompts.gabarit(prompts.REPARATION))


def _reformuler(question: str, faits: str, *, model: Model, request_limit: int) -> tuple[str, str]:
    """Le second essai — le texte rendu, et ce qui a empêché de l'obtenir.

    Fail-closed, et à l'inverse du nœud système : là, un incident du modèle rend
    la question au planificateur, qui aurait peut-être su répondre ; ici, le
    repli est DÉJÀ prêt et il est juste. Un tour de réparation qui n'aboutit pas
    ne doit donc rien coûter à l'utilisateur — il sert les faits, comme avant
    ce correctif.
    """
    try:
        run = build_reparation_agent().run_sync(
            _demande_de_reparation(question, faits),
            model=model,
            usage_limits=UsageLimits(request_limit=request_limit),
        )
    except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
        logger.warning("tour de réparation écarté : %s", exc)
        return "", f"tour de réparation écarté ({type(exc).__name__})"
    return run.output, ""


def servir_la_reponse(
    resultat: ResultatSysteme,
    *,
    question: str,
    model: Model,
    request_limit: int,
) -> ReponseServie:
    """Ce que l'utilisateur lit : la formulation du modèle, la seconde, ou les faits.

    **Le repli est un garde-fou, pas une réponse.** Le texte des faits est juste
    et il est fondé — c'est toute sa raison d'être — mais ce n'est pas une
    réponse : « parle-moi de stocks et de titanic, en deux mots » recevait 774
    caractères de fiche, en-tête compris, et « je bosse sur quoi si je prends
    stocks et production ? » en recevait 986, au caractère près ceux que l'outil
    avait rendus. Mesuré par le propriétaire à travers le GRAPHE le 2026-09-17,
    catalogue métier : sur cinq formulations qui nomment plusieurs sources,
    QUATRE recevaient le texte de l'outil et une seule une phrase du modèle.

    **Alors on redemande avant de servir le pavé.** La ceinture a écarté la
    première formulation ; les faits, eux, sont toujours là. On les rend au
    modèle avec la même question et on le laisse recommencer
    (``build_reparation_agent``). Si la seconde formulation est fondée, c'est
    elle qui part. Sinon le repli part, exactement comme avant — la seconde
    chance ne peut donc rien faire perdre.

    **Le coût est borné et il ne se paie que sur les tours rejetés.** Un appel
    LLM de plus, jamais deux : l'agent de réparation n'a aucun outil, donc aucun
    aller-retour à faire. Un tour que la première formulation a passé ne coûte
    rien de plus.

    **Rien n'a été demandé au modèle en général.** Le prompt de l'agent système
    et les sept fiches d'outils sont figés à l'octet près
    (``test_aucun_paragraphe_de_prompt_n_a_bouge``,
    ``test_aucune_fiche_d_outil_n_a_bouge_au_caractere_pres``), et pour une
    raison mesurée : cinq formulations ont été essayées là et retirées, chacune
    coûtant une question de la surface conversationnelle. Le prompt de
    réparation ne parle qu'aux tours DÉJÀ rejetés — il n'existe pas pour les
    autres, et il ne peut donc pas leur coûter quoi que ce soit.
    """
    premier = _defaut_de(resultat, resultat.reponse)
    if not premier:
        return ReponseServie(resultat.reponse, VOIE_PREMIERE_PASSE)
    seconde, incident = _reformuler(
        question, resultat.faits, model=model, request_limit=request_limit
    )
    second = incident or _defaut_de(resultat, seconde)
    if not second:
        return ReponseServie(seconde, VOIE_SECONDE_PASSE, premier)
    return ReponseServie(resultat.faits, VOIE_REPLI, premier, second)


def _defaut_de(resultat: ResultatSysteme, texte: str) -> str:
    """La ceinture, appliquée à un texte — la première formulation ou la seconde.

    Les deux sont jugées par la MÊME fonction et sur les mêmes faits, et c'est
    ce qui rend la seconde chance sans risque : elle ne relâche rien. Un tour de
    réparation qui inventerait un nom, en omettrait un ou citerait une fiche sans
    en dire un fait serait écarté comme la première formulation l'a été.
    """
    return introspection.defaut_de_fondation(
        texte, resultat.faits, resultat.faits_a_enumerer, resultat.marques_a_porter
    )
