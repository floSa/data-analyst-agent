"""Graphe d'orchestration explicite (LangGraph) : plan -> route -> capacité -> synthèse.

Le pipeline s'inspecte et se trace (state typé, TraceStep par nœud) ; la règle
de routage est du code ; un nœud qui échoue renseigne `error` au lieu de faire
tomber le graphe (CADRAGE §4).
"""

from __future__ import annotations

import json
import logging
import operator
import re
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model

from data_analyst_agent import prompts
from data_analyst_agent.agents.analysis.agent import AnalysisResult, SandboxLike, run_analysis
from data_analyst_agent.agents.inference.accompagnants import (
    absence_daccompagnants,
    champs_daccompagnants,
    sans_la_clause_dabsence,
)
from data_analyst_agent.agents.inference.correspondance import (
    Correspondance,
    CorrespondanceIndisponible,
)
from data_analyst_agent.agents.inference.predict import (
    BatchInferenceOutcome,
    InferenceOutcome,
    run_batch_inference,
    run_inference,
)
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, describe_features, get_schema
from data_analyst_agent.agents.retrieval.agent import RetrievalResult, run_retrieval
from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    FileSource,
    load_catalog,
    open_source,
)
from data_analyst_agent.agents.retrieval.classement import grandeur_du_classement
from data_analyst_agent.agents.retrieval.faits import ReglagesDuReleve, RelevesDuCatalogue
from data_analyst_agent.agents.retrieval.sql import QueryResult
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import modele_du_thread
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.context_budget import (
    CONTEXT_REFUSAL_ERROR,
    CONTEXT_REFUSAL_MESSAGE,
    ContextLimits,
    ContextTrim,
    detect_overflow,
    estimate_tokens,
    exceeds_model_window,
    is_context_refusal,
)
from data_analyst_agent.orchestrator.plan import (
    Plan,
    planner_agent,
    planner_system_prompt,
    planner_template,
)
from data_analyst_agent.orchestrator.rappel import (
    Rejeu,
    aveu_dabsence,
    defaut_de_formulation,
    designation_dun_artefact_passe,
    run_rappel,
)
from data_analyst_agent.orchestrator.systeme import (
    ResultatSysteme,
    ontologies_visees,
    run_systeme,
    servir_la_reponse,
)
from data_analyst_agent.orchestrator.workspace import (
    ConversationWorkspace,
    WorkspaceArtifact,
)
from data_analyst_agent.sandbox.client import MimeOutput

logger = logging.getLogger("data_analyst_agent.orchestrator")

# Ce que lit l'utilisateur quand un nœud tombe sur une exception inattendue.
#
# Le message brut ne peut PAS servir : `_guarded` mettait
# `f"{type(exc).__name__}: {exc}"` dans `error`, et la synthèse l'affichait tel
# quel. Mesuré sur un Postgres injoignable, l'utilisateur recevait
# « InterfaceError: (pg8000.exceptions.InterfaceError) Can't create a connection
# to host 127.0.0.1 and port 65432… » — l'hôte et le port de la base (audit
# §5.1). Ce que ça lui apprend : rien. Ce que ça apprend à qui n'est pas censé
# le savoir : la topologie interne.
#
# Un message par nœud, parce qu'un message unique ne dirait pas *ce qui* a
# échoué, et que « la source n'a pas répondu » et « l'analyse n'a pas abouti »
# n'appellent pas la même réaction. Formulés pour s'enchaîner après « Je n'ai
# pas pu répondre : » (cf. `_synthesize_node`).
ERREURS_UTILISATEUR = {
    "plan": "je n'ai pas réussi à interpréter la demande",
    "retrieval": "la source de données n'a pas pu être interrogée",
    "analysis": "l'analyse n'a pas pu être menée",
    "inference": "la prédiction n'a pas pu être lancée",
    "fetch_predict": "les données de la prédiction n'ont pas pu être récupérées",
    "system": "je n'ai pas pu relire ma propre configuration",
    "rappel": "je n'ai pas pu reprendre ce qui a déjà été produit dans cette conversation",
    "synthesize": "la réponse n'a pas pu être composée",
}
ERREUR_UTILISATEUR_PAR_DEFAUT = "une étape interne a échoué"


def reference_dincident() -> str:
    """Un identifiant court, le même dans la réponse, la trace et les logs.

    Sans lui, masquer le détail technique reviendrait à le perdre : l'utilisateur
    dirait « ça n'a pas marché » et l'exploitant chercherait dans les logs de la
    journée. Avec, il suffit de le citer. Huit hexadécimaux : assez pour ne pas
    collisionner dans un journal, assez court pour être recopié à l'oral.
    """
    return uuid.uuid4().hex[:8]


class TraceStep(BaseModel):
    """Ce qu'a fait un nœud — et ce qui a été coupé de ce qu'il a envoyé.

    Rien ne comptait les tokens ni n'enregistrait de troncature : un utilisateur
    voyait la qualité s'effondrer sans qu'aucun message ne l'explique. Les trois
    derniers champs existent pour que ce ne soit plus jamais silencieux.
    """

    node: str
    detail: str = ""
    duration_ms: int = 0
    prompt_tokens: int | None = None  # estimation locale, décomptée AVANT l'appel
    server_prompt_tokens: int | None = None  # ce que le serveur dit avoir évalué
    truncated: bool = False
    truncation: str = ""  # en clair : ce qui a été coupé, et par quel réglage


class PendingInference(BaseModel):
    """Prédiction en attente de features (multi-tours).

    Renvoyée quand la validation échoue ; le tour suivant la repasse à
    ``ask(pending=...)`` pour que le complément de l'utilisateur soit fusionné
    avec ce qui était déjà connu.
    """

    dataset: str
    features: dict = Field(default_factory=dict)


# La source de travail d'une conversation est un simple NOM, et elle se persiste
# avec le fil comme ``owner`` (cf. `Conversation.source_de_travail`). Elle a
# porté un instant un second champ — « une proposition attend une réponse » —
# retiré après mesure : l'état n'était pas nécessaire, puisque reconnaître un
# choix de source ne demande que de lire le message
# (``introspection.choix_de_source``), et il créait une dépendance à l'ordre
# des tours qui a fait perdre un message de validation sur le parcours mesuré.
#
# Vide = aucune source liée. C'est l'état d'une conversation ANTÉRIEURE à ce
# mécanisme, dont la transcription ne porte pas le champ : elle continue de
# fonctionner exactement comme avant, le planificateur choisissant la source à
# chaque tour. ``None`` (dans le state et dans ``ChatAnswer``) veut dire tout
# autre chose : il n'y a pas de conversation du tout.


class OrchestratorState(TypedDict, total=False):
    question: str
    source_name: str | None
    plan: Plan | None
    retrieval: RetrievalResult | None
    analysis: AnalysisResult | None
    inference: InferenceOutcome | None
    batch: BatchInferenceOutcome | None
    pending_in: PendingInference | None
    pending_out: PendingInference | None
    # Le tour d'avant : (ce que l'utilisateur a dit, ce que l'agent a répondu).
    # Sans lui, « oui », « et dedans ? » ou « tu ne m'as pas répondu » n'ont
    # aucun sens à trouver — et c'est l'agent lui-même qui pose la question
    # fermée à laquelle « oui » répond.
    echange_precedent: tuple[str, str] | None
    source_in: str | None  # la source liée au fil, telle que reçue
    source_out: str | None  # ce que le fil retient de ce tour
    avis_de_source: str  # « je travaille sur X », mis en tête de la réponse
    # « je n'ai pas produit ça », mis en tête quand on PRODUIT au lieu de rappeler
    avis_dabsence: str
    # Ce que le dictionnaire de la source écrit sur les termes que le MESSAGE
    # nomme, mis en PIED de la réponse. Renseigné par les deux nœuds qui
    # répondent en regardant les données, et par eux seuls : c'est le chemin qui
    # n'a pas de ceinture (cf. `introspection.ce_qu_en_dit_le_dictionnaire`).
    dire_du_dictionnaire: str
    system: str | None  # réponse à une question SUR le système
    rappel: str | None  # réponse rendue en rappelant un artefact du fil
    clarification: str | None
    workspace: ConversationWorkspace | None
    answer: str
    error: str | None
    artifacts: Annotated[list[MimeOutput], operator.add]
    trace: Annotated[list[TraceStep], operator.add]


@dataclass(frozen=True)
class PlanContext:
    """Ce que les règles d'ajustement du plan ont le droit de regarder.

    Figé, et calculé une fois pour toutes : les règles s'enchaînent sur un même
    tour et aucune ne modifie ce qu'une autre lit — seul le ``Plan`` passe de
    main en main. Le donner explicitement remplace les lectures répétées du
    state éparpillées dans l'ancien bloc unique, où l'on relisait deux fois le
    même ``workspace`` et recalculait trois fois le même catalogue effectif.

    La distinction entre les deux catalogues n'est pas décorative : deux règles
    voisines ne lisent pas le même, et c'est délibéré (cf. leurs docstrings).
    """

    # source tranchée par l'appelant (paramètre `source` de `ask()`)
    source_imposee: str | None
    pending: PendingInference | None
    workspace: ConversationWorkspace | None
    catalogue_declare: Catalog  # le YAML seul
    catalogue_effectif: Catalog  # + les tableaux mémorisés du fil
    # Le message de ce tour, tel que l'utilisateur l'a écrit. Une règle a besoin
    # de savoir si c'est LUI qui a nommé une source, et non le planificateur :
    # une désignation explicite fait basculer la conversation, une supposition
    # du modèle ne doit pas.
    question: str
    # La source liée au fil, ou ``None`` hors conversation (appel direct à
    # ``ask()`` sans ``conversation_id``) — dans ce cas rien n'est lié ni
    # proposé, et le comportement est celui d'avant ce mécanisme.
    source_de_travail: str | None


class ChatAnswer(BaseModel):
    """Ce que l'API renvoie : texte + objets affichables + trace rejouable."""

    answer: str
    artifacts: list[MimeOutput] = Field(default_factory=list)
    plan: Plan | None = None
    error: str | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    # multi-tours : à repasser tel quel au prochain ask() de la conversation
    pending: PendingInference | None = None
    # La source que la conversation retient après ce tour, à persister avec le
    # fil et à repasser au prochain ``ask()`` — comme ``pending``.
    source_de_travail: str | None = None
    conversation_id: str | None = None  # renseigné par l'API


class SourceDuCatalogue(BaseModel):
    """Une source, telle que la page de chat a besoin de la présenter.

    Le YAML pour le nom, le type et la description ; la source elle-même pour
    ``faits`` — tables, lignes, période. ``lu`` distingue « je n'ai rien à
    dire » de « je n'ai pas pu lire » : une source injoignable doit se voir
    dans la liste, pas en disparaître.
    """

    name: str
    type: str
    description: str = ""
    faits: str = ""
    lu: bool = True


def _json_table(columns: list[str], rows: list[list], truncated: bool) -> MimeOutput:
    """L'artefact « tableau » que la page sait afficher : colonnes, lignes, troncature.

    Un seul point de fabrication. Le contrat est lu par le front, qui n'attend
    que ces trois clés : deux ``json.dumps`` séparés, c'étaient deux occasions
    de renommer une clé d'un côté seulement, ou d'oublier ``ensure_ascii=False``
    — auquel cas les accents partent en ``\u00e9`` dans la moitié des tableaux
    (audit §5.2).
    """
    payload = {"columns": columns, "rows": rows, "truncated": truncated}
    return MimeOutput(mime="application/json", data=json.dumps(payload, ensure_ascii=False))


def _table_artifact(result: QueryResult) -> MimeOutput:
    """Le tableau d'une requête, tel quel."""
    return _json_table(result.columns, result.rows, result.truncated)


class Orchestrator:
    """Façade : construit le graphe et répond aux questions.

    Toutes les dépendances sont injectables (tests) : modèle LLM, catalogue,
    registre de modèles ML, sandbox.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model: Model | None = None,
        catalog: Catalog | None = None,
        registry: Registry | None = None,
        sandbox: SandboxLike | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # Le modèle n'est PAS figé ici. Un orchestrateur vit pour tout le
        # processus et sert les requêtes depuis plusieurs threads, chacun avec sa
        # boucle d'événements : un client HTTP unique verrait ses connexions
        # reprises d'une boucle par une autre (cf. `llm.modele_du_thread`).
        # Un modèle injecté, lui, reste partagé — c'est ce qu'un test demande en
        # le passant, et un modèle scripté n'ouvre aucune connexion.
        self._modele_injecte = model
        self.catalog = catalog if catalog is not None else load_catalog(self.settings.catalog_path)
        # Ce que les sources disent d'elles-mêmes quand on les LIT — tables,
        # lignes, période. Relevé au premier inventaire, pas au démarrage :
        # ouvrir toutes les sources ici ferait payer le démarrage du serveur à
        # qui ne pose aucune question d'inventaire, et le ferait dépendre de la
        # disponibilité de chaque base. Gardé ensuite le temps que disent les
        # réglages — et pas pour la vie du processus : une source revenue doit
        # cesser d'être annoncée injoignable (cf. `ReglagesDuReleve`).
        self.releves = RelevesDuCatalogue(
            self.catalog, ReglagesDuReleve.from_settings(self.settings)
        )
        self.registry = (
            registry if registry is not None else Registry.load(self.settings.models_registry_path)
        )
        self._sandbox_override = sandbox
        # plafond de ce qu'un tour réinjecte dans le contexte du modèle
        self.limits = ContextLimits.from_settings(self.settings)
        self.graph = self._build_graph()

    # -- API ----------------------------------------------------------------

    @property
    def model(self) -> Model:
        """Le modèle à utiliser ICI : celui qu'on a injecté, sinon celui du thread."""
        if self._modele_injecte is not None:
            return self._modele_injecte
        return modele_du_thread(self.settings)

    def ask(
        self,
        question: str,
        source: str | None = None,
        pending: PendingInference | None = None,
        conversation_id: str | None = None,
        workspace_root: Path | None = None,
        source_de_travail: str | None = None,
        echange_precedent: tuple[str, str] | None = None,
    ) -> ChatAnswer:
        """Répond à une question, dans la mémoire de ``conversation_id`` s'il y en a une.

        ``workspace_root`` est la racine sous laquelle vit cette conversation.
        L'API y passe ``ConversationStore.base_dir``, la racine de l'utilisateur
        de la session : la transcription et les tableaux intermédiaires d'un
        même fil doivent atterrir dans le MÊME dossier, et le seul moyen d'en
        être sûr est que les deux couches lisent la même valeur plutôt que de
        la recalculer chacune de son côté. À défaut, on retombe sur
        ``workspace_dir`` — le cas des appels directs, hors session.

        ``source_de_travail`` est la source liée à la conversation, lue dans sa
        transcription et repassée ici à chaque tour — comme ``pending``.
        ``None`` (le défaut) veut dire « pas de conversation, ou pas encore de
        source liée » : le tour se déroule alors comme avant ce mécanisme.
        Distinct de ``source`` : celui-là est une source **imposée par
        l'appelant** pour ce tour-ci, il ne lie rien.
        """
        # mémoire de conversation : les tableaux intermédiaires produits sont
        # persistés et réexposés aux tours suivants (cf. workspace.py)
        racine = workspace_root if workspace_root is not None else self.settings.workspace_dir
        workspace = (
            ConversationWorkspace(racine, conversation_id, limits=self.limits)
            if conversation_id is not None
            else None
        )
        state: OrchestratorState = self.graph.invoke(
            {
                "question": question,
                "source_name": source,
                "pending_in": pending,
                "source_in": source_de_travail,
                "echange_precedent": echange_precedent,
                "workspace": workspace,
                "artifacts": [],
                "trace": [],
            }
        )
        # mémorise ce tour (question + action + code de figure) pour comprendre un
        # éventuel ajustement au tour suivant (« mets des couleurs plus vives »)
        plan = state.get("plan")
        if workspace is not None and plan is not None and not state.get("clarification"):
            analysis = state.get("analysis")
            inference = state.get("inference")
            # les features d'une prédiction RÉUSSIE deviennent la base d'un
            # éventuel ajustement au tour suivant (« et si elle était en 3e ? »)
            aboutie = inference is not None and inference.prediction is not None
            workspace.record_turn(
                question,
                plan.capability,
                plan.source,
                code=analysis.code if analysis is not None else None,
                dataset=plan.dataset if aboutie else None,
                features=dict(inference.features) if aboutie else None,
            )
        return ChatAnswer(
            answer=self._with_context_notices(state.get("answer", ""), state.get("trace", [])),
            artifacts=state.get("artifacts", []),
            plan=state.get("plan"),
            error=state.get("error"),
            trace=state.get("trace", []),
            pending=self._pending_retenu(state, pending),
            source_de_travail=self._source_retenue(state, source_de_travail),
        )

    def inventaire_des_sources(self) -> list[SourceDuCatalogue]:
        """Le catalogue déclaré, augmenté de ce qu'on LIT dans chaque source.

        Sert à l'indicateur de la page de chat : pour choisir une source dans
        un menu, il faut savoir laquelle pèse trois cents lignes et laquelle
        couvre 2024. Les faits sont ceux du relevé mis en cache — ce sont les
        mêmes que ceux de l'inventaire proposé en conversation, et il ne faut
        pas qu'ils puissent diverger.
        """
        faits = self.releves.tous()
        return [
            SourceDuCatalogue(
                name=source.name,
                type=source.type,
                description=source.description.strip(),
                faits=faits[source.name].en_clair() if faits.get(source.name) else "",
                lu=faits[source.name].lu if faits.get(source.name) else False,
            )
            for source in self.catalog.sources
        ]

    def source_declaree(self, nom: str) -> bool:
        """Ce nom désigne-t-il une source DÉCLARÉE du catalogue ?

        Distincte d'un simple ``get`` : c'est la question que pose l'API avant
        de lier une source choisie dans le menu, et un tableau intermédiaire de
        conversation ne doit pas pouvoir y passer — il est interrogeable, ce
        n'est pas une source de données.
        """
        return any(source.name == nom for source in self.catalog.sources)

    @staticmethod
    def _source_retenue(state: OrchestratorState, entree: str | None) -> str | None:
        """Ce que la conversation garde de ce tour à propos de sa source.

        Un seul endroit, pour que la source liée survive à toute branche du
        graphe qui ne s'est pas prononcée : un nœud qui échoue, une
        clarification, une question sur le système ne doivent pas délier ce
        que l'utilisateur a validé.
        """
        retenue = state.get("source_out")
        return retenue if retenue is not None else entree

    @staticmethod
    def _pending_retenu(
        state: OrchestratorState, entree: PendingInference | None
    ) -> PendingInference | None:
        """Ce que la conversation garde de ce tour à propos de sa prédiction.

        Même règle que pour la source, et pour la même raison : **un tour qui ne
        s'est pas prononcé ne défait rien**. Un rappel d'artefact, une question
        de données sans rapport, une clarification, un nœud en échec — aucun de
        ces tours n'a touché à la prédiction, et aucun ne doit donc l'effacer.

        Sans cette ligne, borner le court-circuit du rappel ne faisait que
        déplacer la confiscation : le tableau redevenait atteignable, et c'est
        la prédiction qui se perdait en silence — on ne pouvait plus la
        compléter par « une femme », puisque plus rien n'attendait. Mesuré par
        le test qui suit cette méthode dans l'ordre de lecture, et sur les fils
        `a` et `c` de `scripts/mesure_fils_de_prediction.py`.

        Un tour qui S'EST prononcé, lui, tranche : une prédiction relancée
        renseigne ``pending_out`` (l'acquis, fusionné), une prédiction aboutie
        ne le renseigne pas — et son absence vaut alors effacement, parce qu'il
        n'y a plus rien à attendre. C'est ce que dit ``a_touche_la_prediction``.
        """
        retenu = state.get("pending_out")
        if retenu is not None:
            return retenu
        a_touche_la_prediction = (
            state.get("inference") is not None or state.get("batch") is not None
        )
        return None if a_touche_la_prediction else entree

    @staticmethod
    def _with_context_notices(answer: str, trace: list[TraceStep]) -> str:
        """Ajoute à la réponse ce qui a été coupé du contexte, s'il y a eu coupe.

        Dans la RÉPONSE, et pas seulement dans la trace : la trace est un outil
        de mise au point, elle n'est pas dépliée par défaut. Quelqu'un qui perd
        du contexte doit l'apprendre de l'application — sinon il ne peut que le
        déduire de la qualité des réponses, ce qui est précisément le défaut
        relevé par l'audit (§3.5).

        Un avis identique rendu par deux nœuds n'est écrit qu'une fois.
        """
        avis: list[str] = []
        for step in trace:
            if step.truncation and step.truncation not in avis:
                avis.append(step.truncation)
        if not avis:
            return answer
        return "\n\n".join([answer, *avis]) if answer else "\n\n".join(avis)

    # -- construction du graphe ----------------------------------------------

    def _build_graph(self):
        from langgraph.graph import END, StateGraph

        builder = StateGraph(OrchestratorState)
        builder.add_node("plan", self._guarded("plan", self._plan_node))
        builder.add_node("retrieval", self._guarded("retrieval", self._retrieval_node))
        builder.add_node("analysis", self._guarded("analysis", self._analysis_node))
        builder.add_node("inference", self._guarded("inference", self._inference_node))
        builder.add_node("fetch_predict", self._guarded("fetch_predict", self._fetch_predict_node))
        builder.add_node("system", self._guarded("system", self._system_node))
        builder.add_node("rappel", self._guarded("rappel", self._rappel_node))
        builder.add_node("synthesize", self._guarded("synthesize", self._synthesize_node))

        # Le nœud `system` est en TÊTE, et c'est le changement de forme du
        # graphe : la première question posée n'est plus « quelle capacité ? »
        # mais « est-ce une question sur moi ? ». C'est le modèle qui y répond,
        # en appelant — ou non — un outil de faits (cf. `orchestrator/systeme`).
        # Il n'appelle rien : le tour repart au planificateur comme avant.
        builder.set_entry_point("system")
        builder.add_conditional_edges(
            "system",
            self._apres_le_systeme,
            {"rappel": "rappel", "synthesize": "synthesize"},
        )
        # Puis : « est-ce qu'on parle de quelque chose que j'ai déjà produit ? ».
        # Deuxième question posée, et pas la première : « que sais-tu faire ? »
        # ne doit pas se faire attraper par un fil qui a produit des tableaux.
        # Le nœud se retire sans appeler le modèle quand le fil n'a rien
        # produit — un fil neuf ne paie donc rien (cf. `_rien_a_rappeler`).
        builder.add_conditional_edges(
            "rappel",
            self._apres_le_rappel,
            {"plan": "plan", "synthesize": "synthesize"},
        )
        builder.add_conditional_edges(
            "plan",
            self._route,
            {
                "query": "retrieval",
                "analyze": "analysis",
                "predict": "inference",
                "fetch_then_predict": "fetch_predict",
                "clarify": "synthesize",
                "error": "synthesize",
            },
        )
        for node in ("retrieval", "analysis", "inference", "fetch_predict"):
            builder.add_edge(node, "synthesize")
        builder.add_edge("synthesize", END)
        return builder.compile()

    # capacités qui interrogent une source (donc concernées par l'ambiguïté)
    _SOURCE_CAPABILITIES = ("query", "analyze", "fetch_then_predict")
    # capacités qui appellent un modèle ML (donc un dataset est requis)
    _PREDICT_CAPABILITIES = ("predict", "fetch_then_predict")

    @staticmethod
    def _apres_le_systeme(state: OrchestratorState) -> str:
        """Le nœud système a-t-il répondu, ou la question part-elle au plan ?

        Une seule chose est regardée : ``system`` est-il renseigné. Le nœud le
        renseigne quand le modèle a appelé un outil de faits **et** que sa
        formulation les porte, ou quand il faut servir les faits eux-mêmes. Il
        le laisse vide dans tous les autres cas — question sur les données,
        modèle qui n'a rien appelé, agent système indisponible — et le tour
        continue : d'abord le rappel d'artefact, qui se retire sans rien coûter
        quand le fil n'a rien produit, puis le planificateur.
        """
        if state.get("error"):
            return "synthesize"
        return "synthesize" if state.get("system") is not None else "rappel"

    @staticmethod
    def _apres_le_rappel(state: OrchestratorState) -> str:
        """Le rappel a-t-il abouti, ou la demande part-elle au plan ?

        Une seule chose est regardée, comme après le nœud système : ``rappel``
        est-il renseigné. Le nœud le renseigne quand le modèle a appelé un outil
        de rappel — relire un artefact, en rejouer un — et le laisse vide dans
        tous les autres cas, y compris quand il n'y avait rien à rappeler. Le
        tour suit alors le chemin d'avant, planificateur compris.

        Un REJEU, lui, ne passe pas par ``rappel`` : il renseigne ``analysis``
        et un ``plan`` d'analyse, parce que c'en est une — même synthèse, même
        trace, même mémorisation que n'importe quelle autre analyse.
        """
        if state.get("error"):
            return "synthesize"
        abouti = state.get("rappel") is not None or state.get("analysis") is not None
        return "synthesize" if abouti else "plan"

    @staticmethod
    def _route(state: OrchestratorState) -> str:
        """Règle de routage : du code, pas du prompt (CADRAGE §4).

        La clarification est examinée AVANT l'absence de plan, et ce n'est pas
        un détail d'ordre : la validation d'une source (« titanic ») se répond
        sans planifier quoi que ce soit, donc sans ``Plan``. Comme pour le nœud
        système, ``ChatAnswer.plan`` reste vide dans ce cas — exact plutôt
        qu'incomplet.
        """
        if state.get("error"):
            return "error"
        if state.get("clarification"):
            return "clarify"
        if state.get("plan") is None:
            return "error"
        return state["plan"].capability

    def _guarded(self, name: str, fn):
        """Un nœud qui échoue renseigne `error` au lieu de faire tomber le graphe."""

        def wrapper(state: OrchestratorState) -> dict:
            start = time.monotonic()
            logger.info("nœud %s : démarrage", name)
            try:
                update = fn(state)
            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                incident = reference_dincident()
                logger.exception(
                    "nœud %s : échec après %d ms (incident %s)", name, duration, incident
                )
                if is_context_refusal(exc):
                    # Un prompt trop long n'est pas tronqué : il est rejeté.
                    # Sans ce branchement, le refus arriverait à l'utilisateur
                    # sous la
                    # forme « ModelHTTPError: … », que rien ne relie à la
                    # longueur du prompt — donc rien à faire pour s'en sortir.
                    return {
                        "error": CONTEXT_REFUSAL_ERROR,
                        "trace": [
                            TraceStep(
                                node=name,
                                detail=f"refus du serveur : {exc}",
                                duration_ms=duration,
                                truncated=True,
                                truncation=CONTEXT_REFUSAL_MESSAGE,
                            )
                        ],
                    }
                # Le type et le message de l'exception restent du côté trace et
                # logs ; l'utilisateur reçoit une phrase et une référence.
                return {
                    "error": (
                        f"{ERREURS_UTILISATEUR.get(name, ERREUR_UTILISATEUR_PAR_DEFAUT)} "
                        f"(incident {incident})"
                    ),
                    "trace": [
                        TraceStep(
                            node=name,
                            detail=f"incident {incident} — échec : {type(exc).__name__}: {exc}",
                            duration_ms=duration,
                        )
                    ],
                }
            duration = int((time.monotonic() - start) * 1000)
            details = "; ".join(step.detail for step in update.get("trace", []))
            logger.info("nœud %s : terminé en %d ms (%s)", name, duration, details)
            return update

        return wrapper

    # -- nœuds ----------------------------------------------------------------

    def _effective_catalog(self, state: OrchestratorState) -> Catalog:
        """Catalogue du tour : sources déclarées + objets intermédiaires RÉINJECTÉS.

        ``as_sources()`` ne rend que la fenêtre : un objet évincé n'est pas
        interrogeable ce tour-ci, exactement comme il n'est ni décrit au
        planificateur ni monté dans la sandbox.
        """
        workspace = state.get("workspace")
        if workspace is None or not workspace.injected:
            return self.catalog
        return Catalog(sources=[*self.catalog.sources, *workspace.as_sources()])

    @staticmethod
    def _match_source_name(requested: str, catalog: Catalog) -> str | None:
        """Résout un nom de source, tolérant à la décoration du LLM.

        Exact d'abord ; sinon, si un (et un seul) nom de source connu apparaît
        comme mot dans la chaîne demandée (« titanic (postgres) » -> titanic),
        on le retient. Ambigu ou absent -> None (l'appelant demandera).
        """
        names = [s.name for s in catalog.sources]
        if requested in names:
            return requested
        low = requested.lower()
        hits = [
            n for n in names if re.search(rf"(?<![0-9a-z]){re.escape(n.lower())}(?![0-9a-z])", low)
        ]
        return hits[0] if len(hits) == 1 else None

    def _resolve_source(self, plan: Plan, catalog: Catalog):
        """La source du plan si elle existe ; sinon repli sans ambiguïté.

        Le LLM omet parfois la source quand la demande semble se suffire
        (constaté en live) : si le catalogue n'en contient qu'une, on la prend ;
        sinon on échoue avec la liste des choix — jamais de devinette.
        """
        if plan.source:
            return catalog.get(plan.source)
        if len(catalog.sources) == 1:
            only = catalog.sources[0]
            plan.source = only.name  # trace et réponse cohérentes
            return only
        names = ", ".join(s.name for s in catalog.sources) or "(catalogue vide)"
        raise KeyError(
            f"aucune source choisie et le catalogue en contient plusieurs — précise parmi : {names}"
        )

    def _datasets_description(self) -> str:
        lines = []
        for dataset in self.registry.datasets:
            entry = self.registry.get(dataset)
            if dataset in SCHEMAS:
                lines.append(f"- {dataset} ({entry.task}) : features attendues :")
                lines.append(describe_features(SCHEMAS[dataset]))
            else:
                lines.append(f"- {dataset} ({entry.task}) : features attendues : ?")
        return "\n".join(lines) or "(aucun modèle)"

    @staticmethod
    def _pending_context(pending: PendingInference | None) -> str | None:
        """Décrit au planificateur la prédiction en attente (multi-tours)."""
        if pending is None:
            return None
        known = ", ".join(f"{k}={v!r}" for k, v in pending.features.items()) or "(aucune)"
        try:
            missing = ", ".join(
                field
                for field in get_schema(pending.dataset).model_fields
                if field not in pending.features
            )
        except KeyError:
            missing = "?"
        return (
            f"CONTEXTE DE CONVERSATION : une prédiction '{pending.dataset}' attend des "
            f"informations. Features déjà connues : {known}. Il manque : {missing}.\n"
            "Si le message apporte tout ou partie de ces informations, choisis "
            f"'predict' avec dataset='{pending.dataset}' et mets dans `features` les "
            "NOUVELLES valeurs extraites du message (noms exacts du schéma) — les "
            "valeurs déjà connues seront fusionnées automatiquement. Si le message "
            "change complètement de sujet, ignore ce contexte."
        )

    @staticmethod
    def _contexte_de_source(source_de_travail: str | None) -> str | None:
        """Dit au planificateur sur quelle source la conversation travaille.

        « Le planificateur ne doit plus avoir à la deviner quand elle est déjà
        connue » : c'est ce que fait cette ligne. Elle ne remplace pas
        ``_regle_source_de_la_conversation``, qui repose la source quoi qu'il
        arrive — elle évite au modèle d'avoir à choisir, ce qui lui laisse plus
        d'attention pour la capacité et les features. Le reste du prompt ne
        bouge pas d'un caractère, comme pour le contexte de prédiction en
        attente et celui du tour précédent.
        """
        if not source_de_travail:
            return None
        return (
            "CONTEXTE DE CONVERSATION : cette conversation travaille sur la source "
            f"'{source_de_travail}'. Prends-la comme `source`, sauf si le message "
            "en désigne explicitement une autre."
        )

    @staticmethod
    def _ajoute_avis(mesures: dict, avis: str) -> None:
        """Cumule un constat de troncature dans les mesures du tour.

        Ils se cumulent bel et bien : un tour peut avoir évincé des objets ET
        dépasser la fenêtre du serveur, et l'utilisateur a besoin des deux.
        """
        if not avis:
            return
        mesures["truncated"] = True
        mesures["truncation"] = " ".join(a for a in (mesures["truncation"], avis) if a)

    def _clarify(self, plan: Plan, question: str, start: float, **mesures) -> dict:
        """Court-circuite vers une question de clarification (réponse propre, pas d'erreur).

        ``mesures`` porte ce qui a été mesuré du prompt de ce tour : une
        clarification n'annule pas une troncature, elle doit la dire aussi.
        """
        return {
            "plan": plan,
            "clarification": question,
            "trace": [self._step("plan", "clarification demandée", start, **mesures)],
        }

    @staticmethod
    def _fallback_plan(workspace: ConversationWorkspace | None) -> Plan | None:
        """Repli quand le planificateur échoue : reprendre la dernière action interrogeant
        une source (ajustement d'un tour précédent, ex. « mets des couleurs plus vives »)."""
        if workspace is None:
            return None
        ctx = workspace.context
        if ctx.last_capability in Orchestrator._SOURCE_CAPABILITIES and ctx.last_source:
            return Plan(capability=ctx.last_capability, source=ctx.last_source)
        return None

    # -- le plan et ses règles d'ajustement -----------------------------------

    def _peser_le_prompt(self, state: OrchestratorState) -> tuple[str, dict]:
        """Compose le prompt du planificateur et décompte ce qu'il coûte.

        L'ordre est contraint et non arbitraire : le budget se décompte AVANT
        l'appel, donc avant de savoir ce que la mémoire de conversation pourra
        garder — et le prompt final ne peut être composé qu'après cet arbitrage.

        Rend le prompt et les mesures du tour (tokens estimés, troncatures
        constatées), que la suite du nœud complète et recopie dans la trace.
        """
        workspace = state.get("workspace")
        sources_description = self.catalog.describe()
        datasets_description = self._datasets_description()
        pending_context = self._pending_context(state.get("pending_in"))
        source_context = self._contexte_de_source(state.get("source_in"))
        history_context = workspace.describe_context() if workspace is not None else None
        # Tout ce qui suit est incompressible ici : le gabarit, le catalogue
        # déclaré, les modèles, le tour précédent, la question. Le seul poste qui
        # cède est le catalogue des objets intermédiaires, et il cède par les
        # plus ANCIENS. (Le gabarit est pesé avec ses marqueurs
        # `{sources}`/`{datasets}` : quelques caractères de trop, du bon côté.)
        fixe = estimate_tokens(
            planner_template(),
            sources_description,
            datasets_description,
            history_context,
            pending_context,
            source_context,
            state["question"],
        )
        trim = workspace.fit_to_budget(fixe) if workspace is not None else ContextTrim()
        mesures: dict = {"truncated": trim.truncated, "truncation": trim.message()}
        # les objets intermédiaires RETENUS sont décrits en plus des sources
        # déclarées, pour que « prédis ces lignes » les désigne
        workspace_description = workspace.describe() if workspace is not None else None
        if workspace_description:
            sources_description = f"{sources_description}\n\n{workspace_description}"
        system_prompt = planner_system_prompt(
            sources_description,
            datasets_description,
            pending_context=pending_context,
            history_context=history_context,
            source_context=source_context,
        )
        mesures["prompt_tokens"] = estimate_tokens(system_prompt, state["question"])
        # dernier constat avant l'envoi : au-delà de la fenêtre du serveur, un
        # échec de sortie structurée ne laissera RIEN à mesurer au retour.
        self._ajoute_avis(mesures, exceeds_model_window(mesures["prompt_tokens"], self.limits))
        return system_prompt, mesures

    def _demander_un_plan(
        self, system_prompt: str, state: OrchestratorState, mesures: dict
    ) -> Plan | None:
        """Le plan du planificateur, ou son repli — ``None`` s'il n'y en a pas.

        ``None`` veut dire « je n'ai pas de plan à ajuster » : l'appelant
        demandera de préciser. Jamais d'exception : le planificateur qui rate sa
        sortie structurée est un cas courant, pas un incident.

        ``mesures`` est complété au passage (ce que le serveur dit avoir évalué,
        et le débordement qu'on en déduit).
        """
        planner = planner_agent(system_prompt)
        try:
            resultat = planner.run_sync(state["question"], model=self.model)
        except UnexpectedModelBehavior:
            # le LLM n'a pas su produire un Plan structuré (retries épuisés). Si on
            # a un tour précédent, on suppose un AJUSTEMENT et on reprend sa
            # capacité/source ; sinon l'appelant demande de préciser.
            mesures["server_prompt_tokens"] = None
            return self._fallback_plan(state.get("workspace"))
        # le budget est une prévision, `prompt_eval_count` est une mesure :
        # c'est elle qui prouve qu'un serveur a tronqué sans le dire.
        mesures["server_prompt_tokens"] = resultat.usage.input_tokens
        debordement = detect_overflow(
            mesures["prompt_tokens"], mesures["server_prompt_tokens"], self.limits
        )
        if debordement is not None:
            self._ajoute_avis(mesures, debordement.message())
        return resultat.output

    def _regle_source_imposee(self, plan: Plan, ctx: PlanContext) -> str | None:
        """La source passée à ``ask()`` prime sur celle qu'a choisie le modèle.

        En premier, et c'est le point : l'utilisateur (ou l'appelant d'API) a
        tranché explicitement, toutes les règles suivantes doivent raisonner sur
        SA source, pas sur celle du planificateur.
        """
        if ctx.source_imposee:
            plan.source = ctx.source_imposee
        return None

    def _regle_degrader_faute_de_source(self, plan: Plan, ctx: PlanContext) -> str | None:
        """Pas de source à interroger : ``fetch_then_predict`` retombe en ``predict``.

        Ni déclarée, ni en mémoire de conversation — il n'y a rien à aller
        chercher. On dégrade plutôt que d'échouer : la validation relancera
        l'utilisateur sur les features qui manquent.
        """
        if plan.capability == "fetch_then_predict" and not ctx.catalogue_effectif.sources:
            plan.capability = "predict"
        return None

    def _regle_source_de_la_conversation(self, plan: Plan, ctx: PlanContext) -> str | None:
        """La source liée au fil s'impose au plan ; celle que l'utilisateur NOMME la remplace.

        C'est ce qui fait que le planificateur n'a plus à deviner une source
        déjà connue : il la reçoit dans son contexte (cf.
        ``_contexte_de_source``) et, quoi qu'il en fasse, elle est reposée ici.
        Un tour qui ne parle pas de source travaille donc sur celle qui a été
        validée, au lieu de rouvrir la question à chaque fois.

        **Une source nommée par l'utilisateur l'emporte, et fait basculer la
        conversation.** Refuser aurait obligé à ouvrir un fil pour une question
        d'une ligne ; poser une question de confirmation aurait dépensé un tour
        pour une intention déjà écrite noir sur blanc. La bascule est en
        revanche **annoncée** dans la réponse (``_lier_la_source``) : ce qui est
        dangereux n'est pas de changer de source, c'est de changer sans le dire.

        La désignation est lue dans le TEXTE de l'utilisateur
        (``introspection.source_nommee``) et non dans ``plan.source`` : le
        planificateur choisit une source à chaque tour, souvent au hasard des
        descriptions, et sa supposition ne doit pas faire basculer le travail
        de quelqu'un.

        **Et quand rien ne l'a validée, elle est EFFACÉE.** C'est la correction
        du défaut d'ordre du YAML. La supposition ne faisait déjà plus basculer
        une conversation liée — mais sur un fil qui n'avait encore rien choisi,
        elle passait tout droit : elle devenait la source interrogée, puis la
        source liée, sans que personne ne l'ait demandée. Le comportement
        dépendait alors de l'ordre de déclaration du catalogue, ce qui est
        mesuré (`docs/surface-conversationnelle.md` §14). L'effacer ici plutôt
        que de la contourner en aval est ce qui garantit qu'aucune règle
        suivante, aucun nœud et aucune liaison ne peut la reprendre pour un
        choix : il n'y a plus rien à reprendre.

        **Un ``plan.source`` qui désigne un objet du fil SURVIT à la reposition**,
        et il faut lire cette phrase avec sa borne, ci-dessous. Ce n'est pas un
        choix entre sources ambiguës, c'est un résultat que la conversation
        vient de produire. La branche existait plus bas — ``elif plan.source in
        declarees``, qui n'efface qu'un nom du catalogue — et elle était
        INATTEIGNABLE : ``elif ctx.source_de_travail`` s'exécute avant et
        repose la source liée sans condition, c'est-à-dire à partir du deuxième
        tour de n'importe quelle conversation. Mesuré 0/81 : le planificateur a
        désigné un tableau du fil cinq fois, et les cinq fois il a été écrasé
        (`docs/memoire-de-conversation.md` §3.1).

        **La borne, et elle n'est pas gratuite.** La reposition corrige de
        vraies erreurs du modèle, et le même relevé en montre une : au tour 3
        du fil F, `source='resultat_1'` répondait à « les 5 techniciens qui sont
        intervenus le plus souvent » sur un tableau qui ne porte aucune colonne
        de technicien. Ce qui départage les deux cas n'est ni la tournure ni la
        capacité, c'est de savoir si l'objet peut RÉPONDRE — une colonne que la
        question nomme, que la source porte et que l'objet n'a pas
        (``_objet_du_fil_designe``). Elle sort d'un fil brut et pas d'un goût.

        Après ``_regle_degrader_faute_de_source``, qui peut retirer à ce tour la
        capacité même qui réclame une source.
        """
        if ctx.source_imposee or ctx.source_de_travail is None:
            return None
        if plan.capability not in self._SOURCE_CAPABILITIES:
            return None
        nommee = introspection.source_nommee(ctx.question, ctx.catalogue_declare)
        declarees = [s.name for s in ctx.catalogue_declare.sources]
        objet = self._objet_du_fil_designe(plan, ctx)
        if nommee:
            plan.source = nommee
        elif objet is not None:
            plan.source = objet.name
        elif ctx.source_de_travail:
            plan.source = ctx.source_de_travail
        elif len(declarees) == 1:
            # « S'il n'y en a qu'une, il l'annonce au lieu de poser une question
            # inutile » : on la lie ici pour que ``_lier_la_source`` l'annonce,
            # là où ``_resolve_source`` la choisissait sans le dire.
            plan.source = declarees[0]
        elif plan.source in declarees:
            plan.source = None
        return None

    def _objet_du_fil_designe(self, plan: Plan, ctx: PlanContext) -> WorkspaceArtifact | None:
        """Le tableau du fil que le plan désigne, s'il peut répondre — ``None`` sinon.

        Deux conditions, et la seconde est la borne de
        ``_regle_source_de_la_conversation`` :

        1. ``plan.source`` porte le nom d'un tableau RÉINJECTÉ ce tour-ci. Un
           objet évincé du contexte n'est ni monté ni interrogeable ; le
           désigner ne mènerait qu'à « source introuvable ».
        2. la question ne réclame aucune colonne que l'objet n'a pas
           (``introspection.colonne_hors_de_l_objet``).

        **Le prix est une ouverture de source**, et il est payé au même endroit
        que celui de ``_ce_que_le_schema_en_dit`` : sur le seul tour où la
        question se pose, c'est-à-dire là où le modèle a désigné un objet du
        fil — cinq tours sur quatre-vingt-un dans le relevé de C50. Un tour
        qui ne désigne rien ne lit aucun schéma de plus qu'avant.

        Une source illisible ne disqualifie rien : l'introspection est
        best-effort ici comme partout (cf. ``low_cardinality_values``), et
        refuser une désignation parce qu'une base n'a pas répondu ferait
        dépendre le plan de la disponibilité d'un serveur.
        """
        if not plan.source or ctx.workspace is None:
            return None
        vise = introspection.replie(plan.source).strip()
        objet = next(
            (a for a in ctx.workspace.injected if introspection.replie(a.name).strip() == vise),
            None,
        )
        if objet is None:
            return None
        manquante = introspection.colonne_hors_de_l_objet(
            ctx.question, self._colonnes_de_la_source(ctx), objet.columns
        )
        if manquante:
            logger.info(
                "objet '%s' désigné mais sans la colonne '%s' : on repose la source liée",
                objet.name,
                manquante,
            )
            return None
        return objet

    def _colonnes_de_la_source(self, ctx: PlanContext) -> list[str]:
        """Les colonnes de la source LIÉE, toutes tables confondues ("" si illisible)."""
        try:
            source = ctx.catalogue_declare.get(ctx.source_de_travail or "")
            with closing(open_source(source)) as adapter:
                schema = adapter.schema()
        except Exception:  # best-effort, comme toute introspection ici
            return []
        return [colonne.name for table in schema.tables for colonne in table.columns]

    def _regle_reprendre_les_features_acquises(self, plan: Plan, ctx: PlanContext) -> str | None:
        """Fusionne à une prédiction ce que les tours précédents ont déjà donné.

        Deux acquis possibles, et **jamais les deux** : la prédiction restée en
        attente de features (le cas normal du slot-filling) l'emporte sur celle
        qui avait abouti. Dans les deux cas le nouveau message prime sur
        l'acquis — c'est ce qui permet de corriger une valeur refusée — et seul
        le MÊME dataset est repris : une digression n'hérite de rien.

        Après ``_regle_degrader_faute_de_source``, sans quoi un
        ``fetch_then_predict`` dégradé n'hériterait pas de l'acquis.
        """
        if ctx.pending is not None and plan.capability == "predict":
            plan.dataset = plan.dataset or ctx.pending.dataset
            if plan.dataset == ctx.pending.dataset:
                plan.features = {**ctx.pending.features, **plan.features}
        elif plan.capability == "predict" and ctx.workspace is not None:
            # ajustement d'une prédiction déjà ABOUTIE (« et si elle était en 3e
            # classe ? ») : le pending est vidé dès qu'une prédiction réussit, donc
            # sans cet acquis le tour repartait de zéro et redemandait des features
            # déjà données.
            acquis = ctx.workspace.last_features_for(plan.dataset)
            if acquis:
                plan.features = {**acquis, **plan.features}
        return None

    def _regle_normaliser_le_nom_de_source(self, plan: Plan, ctx: PlanContext) -> str | None:
        """Le nom de source désigné est ramené à un nom du catalogue, ou on demande.

        Le LLM décore parfois le nom (« titanic (postgres) », recopié depuis la
        description au lieu de « titanic ») : on normalise. Si la source est
        vraiment inconnue, on demande — plutôt que de laisser fuir un
        ``KeyError`` brut depuis le nœud de capacité.

        Sur le catalogue EFFECTIF : un tableau intermédiaire du fil est une
        source désignable comme une autre.

        **Une désignation qui nomme PLUSIEURS sources déclarées n'est pas une
        source introuvable**, et c'est un défaut mesuré. « ventes ou
        production ? » faisait rendre au planificateur ``source="ventes,
        production"`` — la concaténation des deux noms qu'il a lus —, et
        l'utilisateur lisait « La source « ventes, production » est introuvable.
        Sur quelle source veux-tu travailler : ventes, production, stocks,
        iris, titanic ? » La question est juste : il hésitait, on lui fait
        choisir. La phrase qui la précède est fausse, et elle se voit — les deux
        sources qu'il vient de nommer sont dans la liste qu'on lui propose au
        même instant. On garde donc la question et on laisse tomber la phrase :
        rien n'est introuvable ici, et le dire décrédibilise le reste du tour.

        Le décompte se fait sur la DÉSIGNATION et non sur le message :
        c'est le planificateur qui a empaqueté deux noms, et c'est cet
        empaquetage-là qu'on reconnaît. Un vrai nom inconnu — « comptabilite »,
        une faute de frappe — n'en nomme aucun et garde sa phrase, qui est alors
        l'information utile.
        """
        if not plan.source:
            return None
        resolved = self._match_source_name(plan.source, ctx.catalogue_effectif)
        if resolved is None:
            names = ", ".join(s.name for s in ctx.catalogue_effectif.sources) or "(aucune)"
            empaquetees = introspection.sources_nommees(plan.source, ctx.catalogue_effectif)
            choix = f"Sur quelle source veux-tu travailler : {names} ?"
            # Pas de seuil à régler : « plusieurs » veut dire au moins deux, et
            # c'est une définition, pas un réglage.
            if len(empaquetees) > 1:
                return choix
            return f"La source « {plan.source} » est introuvable. {choix}"
        plan.source = resolved
        return None

    def _regle_choisir_la_source(self, plan: Plan, ctx: PlanContext) -> str | None:
        """Aucune source retenue et le catalogue en contient plusieurs : on PROPOSE.

        Elle n'a pas changé d'une ligne ; ce qui a changé est le plan qu'elle
        lit. ``_regle_source_de_la_conversation`` y a effacé, juste avant, la
        source que **personne n'avait validée** — ni l'utilisateur en la
        nommant, ni le fil en la portant. « Aucune source retenue » veut donc
        maintenant dire ce qu'il devait dire depuis le début, et la question
        revient dans le cas qui lui échappait : celui où le planificateur avait
        deviné.

        C'est ce qui rend le comportement **indépendant de l'ordre de
        déclaration du YAML**. Il ne l'était pas : titanic écrit en premier,
        5/5 répondaient 35,24 % sans rien demander ; employes en premier, 5/5
        énuméraient les sources et ne répondaient jamais — même question, mêmes
        octets (`tests/catalogues/ambiguite/`,
        `docs/surface-conversationnelle.md` §14).

        Sur le catalogue DÉCLARÉ, et non l'effectif : un fil qui a mémorisé des
        tableaux ne doit pas se faire poser la question à cause d'eux, alors que
        l'unique source déclarée reste le choix évident.

        La proposition n'arrive qu'ICI, une fois le plan connu, parce que c'est
        le seul moment où l'on sait qu'une source est réellement nécessaire —
        « prédis la survie d'une passagère de 1re classe » n'en demande aucune,
        et lui proposer un catalogue serait un tour perdu. **Aucune requête
        n'est lancée** : le tour s'arrête au planificateur, et la réponse est
        liée à la conversation au tour suivant
        (``_court_circuit_du_choix_de_source``).

        Le repli sur l'unique source, lui, est posé par
        ``_regle_source_de_la_conversation`` puis annoncé : il n'y a rien à
        demander dans ce cas.
        """
        if (
            plan.capability not in self._SOURCE_CAPABILITIES
            or plan.source
            or len(ctx.catalogue_declare.sources) <= 1
        ):
            return None
        return self._proposer(ctx)

    def _proposer(self, ctx: PlanContext) -> str:
        """L'inventaire posé comme une question, faits relevés à l'appui.

        Les faits (tables, lignes, période) sont **lus dans les sources** et
        gardés en cache pour la session (``RelevesDuCatalogue``). C'est ici
        qu'ils comptent le plus : ce texte est celui sur lequel quelqu'un
        choisit, et deux descriptions écrites à la main se ressemblent toujours
        plus que deux volumétries.
        """
        return introspection.proposer_les_sources(ctx.catalogue_declare, self.releves.tous())

    def _regle_choisir_le_modele(self, plan: Plan, ctx: PlanContext) -> str | None:
        """Prédiction sans modèle désigné : repli s'il n'y en a qu'un, sinon on demande.

        Laisser `dataset` vide propagerait un ``KeyError`` ('' -> modèle
        inconnu) jusqu'au nœud d'inférence.
        """
        if plan.capability not in self._PREDICT_CAPABILITIES or plan.dataset:
            return None
        datasets = self.registry.datasets
        if len(datasets) == 1:
            plan.dataset = datasets[0]
        elif len(datasets) > 1:
            return f"Sur quel modèle veux-tu prédire : {', '.join(datasets)} ?"
        return None

    def _regle_chainer_sur_le_dernier_tableau(self, plan: Plan, ctx: PlanContext) -> str | None:
        """« Prédis ces lignes » : promeut ``predict`` en ``fetch_then_predict``.

        Le LLM route parfois en 'predict' sans features au lieu de
        fetch_then_predict. Si le dernier tableau mémorisé fournit exactement
        les features du modèle, on chaîne dessus plutôt que de redemander des
        valeurs déjà affichées.

        En dernier, et il faut qu'elle y reste : elle a besoin du dataset
        résolu par ``_regle_choisir_le_modele`` et des features rassemblées par
        ``_regle_reprendre_les_features_acquises`` — c'est leur absence qui
        déclenche le chaînage.
        """
        if (
            plan.capability != "predict"
            or plan.features
            or plan.dataset not in SCHEMAS
            or ctx.workspace is None
            or not ctx.workspace.injected
        ):
            return None
        latest = ctx.workspace.injected[-1]
        needed = set(get_schema(plan.dataset).model_fields)
        if needed <= {c.lower() for c in latest.columns}:
            plan.capability = "fetch_then_predict"
            plan.source = latest.name
            plan.data_question = plan.data_question or f"toutes les lignes de {latest.name}"
        return None

    def _regle_lire_labsence_daccompagnants(self, plan: Plan, ctx: PlanContext) -> str | None:
        """« Sans famille à bord » fixe TOUS les compteurs d'accompagnants, pas un seul.

        Le défaut, mesuré 3 tirages sur 3 sur la phrase d'origine : le
        planificateur rend `parch=0` et omet `sibsp`. Il a bien lu l'absence de
        famille — il l'a portée sur un des deux compteurs. La prédiction
        ressortait `invalid` sur « sibsp : valeur manquante », et le tour
        suivant n'ayant plus la phrase sous la main, aucune réponse ne pouvait
        en sortir : le fil était fermé (fils `d` et `g` de
        `scripts/mesure_fils_de_prediction.py`).

        La cause est l'EXTRACTION, et elle seule : la fusion de l'acquis fait
        son travail, les fils témoins `b` et `e` le montrent 3 tirages sur 3.
        C'est pourquoi on ne répare ni dans le prompt ni dans la fusion, mais
        ici, avec deux moitiés qui se déclarent : le schéma dit quels champs
        comptent des accompagnants, le message dit qu'il n'y en a aucun (cf.
        `agents/inference/accompagnants`).

        **Ce que l'utilisateur a donné prime toujours** : un champ déjà présent
        dans `features` n'est pas touché. La règle ne peut donc qu'AJOUTER une
        valeur que la phrase portait, jamais en écraser une.

        Après ``_regle_choisir_le_modele``, qui résout le dataset : sans lui, on
        ne saurait pas quel schéma interroger. Avant
        ``_regle_chainer_sur_le_dernier_tableau``, qui ne se déclenche que sur
        des features VIDES — l'ordre inverse ferait chaîner sur un tableau une
        prédiction dont la phrase donnait déjà deux valeurs.
        """
        if plan.capability not in self._PREDICT_CAPABILITIES or plan.dataset not in SCHEMAS:
            return None
        if not absence_daccompagnants(ctx.question):
            return None
        for champ in champs_daccompagnants(get_schema(plan.dataset)):
            plan.features.setdefault(champ, 0)
        return None

    # L'ORDRE EST SIGNIFICATIF. Il l'a toujours été — il était simplement
    # implicite, réparti sur cent cinquante lignes d'un seul bloc où rien ne
    # distinguait une règle de la suivante ni ne disait pourquoi celle-ci
    # passait avant celle-là. Chaque règle porte désormais son nom, sa raison
    # d'être (un incident réel, pour la plupart) et sa place dans cette liste,
    # qui est la seule chose à lire pour connaître l'ordre.
    _REGLES_DU_PLAN = (
        _regle_source_imposee,
        _regle_degrader_faute_de_source,
        _regle_source_de_la_conversation,
        _regle_reprendre_les_features_acquises,
        _regle_normaliser_le_nom_de_source,
        _regle_choisir_la_source,
        _regle_choisir_le_modele,
        _regle_lire_labsence_daccompagnants,
        _regle_chainer_sur_le_dernier_tableau,
    )

    def _appliquer_les_regles(self, plan: Plan, ctx: PlanContext) -> str | None:
        """Passe le plan dans les règles, dans l'ordre ; s'arrête à la première question.

        Une règle qui rend une question court-circuite les suivantes : on ne
        continue pas d'ajuster un plan qu'on va renvoyer à l'utilisateur pour
        qu'il le précise.
        """
        for regle in self._REGLES_DU_PLAN:
            question = regle(self, plan, ctx)
            if question is not None:
                return question
        return None

    def _ce_que_le_schema_en_dit(self, state: OrchestratorState) -> str:
        """Le PLANCHER du repli : ce que le schéma dit du terme nommé — "" sinon.

        C'est le pendant, pour les sources sans dictionnaire, du pied que C47 a
        posé sur le chemin des données. Il ne s'agit pas de mieux classer la
        demande : il s'agit de ne pas jeter un fait qu'on a sous la main au
        moment précis où l'on renonce.

        **Mesuré le 2026-09-18, trois tirages sur trois, 0/3.** « pourquoi
        class_id et pas directement la classe ? », `titanic` liée au fil :
        `system → plan → synthesize`, et l'utilisateur reçoit « Je n'ai pas bien
        compris ta demande » suivi de l'inventaire des sources. `titanic` ne
        déclare aucun dictionnaire, donc le plancher de C47 ne peut rien pour
        elle — mais `class_id` est une clé étrangère vers `classes`, le schéma
        le déclare, et l'agent système sait déjà l'écrire : ``sens-colonne`` est
        verte sur la surface. Le défaut n'était pas le fait, c'était qu'il ne
        soit pas saisi.

        **Ni prompt, ni lexique.** Cinq formulations ont été écrites puis
        retirées du prompt du planificateur sur ce projet, chacune au prix d'une
        question de la surface ; les deux empreintes SHA-256 des prompts ne
        bougent pas d'une ligne. Ce qui décide ici est le SCHÉMA lui-même :
        le message nomme une colonne ou une table que l'installation déclare, ou
        il n'en nomme pas.

        **Et il ne parle QUE là où l'on se taisait.** Ce chemin est celui du
        planificateur qui rend ``None`` — la demande n'a été classée ni en
        `query`, ni en `analyze`, ni en `predict`. Un tour qui aboutit ne passe
        jamais ici, et une question de calcul qui ressemble à une question de
        sens reste donc traitée comme un calcul.

        Le prix est une ouverture de source sur un tour qui, lui, n'en ouvrait
        aucune. Il est payé sur un renoncement, c'est-à-dire sur le tour le
        moins fréquent et le moins utile du graphe.
        """
        question = state["question"]
        try:
            ontologies = ontologies_visees(question, self._effective_catalog(state))
        except Exception as exc:  # une source injoignable ne vaut pas un tour perdu
            logger.warning("plancher du repli écarté : %s", exc)
            return ""
        return introspection.ce_que_le_schema_en_dit(
            question, ontologies, state.get("source_in") or ""
        )

    def _repli_du_planificateur(self, state: OrchestratorState) -> str:
        """Ce qu'on répond quand le planificateur n'a pas su classer la demande.

        L'ancien message était absurde : il déclarait ne pas comprendre **en
        nommant les sources** — et il les nommait en dur, « titanic, iris… »
        recopiés dans la chaîne, donc faux dès qu'un déploiement change de
        catalogue. S'il est capable de nommer la source, il est capable de
        répondre à la question qui la demande : le repli rend maintenant
        l'inventaire réel, lu dans le catalogue et le registre.

        Il reste une demande de précision — on n'a effectivement pas compris —
        mais il ne repart plus les mains vides, et il **finit** par la question
        plutôt que de commencer par elle : ce qu'on lit en dernier est ce à
        quoi on répond.
        """
        return (
            "Je n'ai pas bien compris ta demande.\n\n"
            f"{introspection.inventaire(self.catalog, self.registry)}\n"
            "Je peux aussi te dire ce que je sais faire, les tables et les colonnes "
            "d'une source, ou les attributs qu'attend un modèle.\n\n"
            "Que veux-tu faire — interroger une source, une analyse ou une "
            "visualisation, ou une prédiction ?"
        )

    # -- la source de travail de la conversation (partie B) --------------------

    def accuser_la_source(self, nom: str, precedente: str = "") -> str:
        """Ce qu'on répond quand l'utilisateur vient de choisir une source.

        Le texte vit dans ``introspection.accueil_de_source``, avec les autres
        réponses construites depuis les artefacts : l'outil de liaison de
        l'agent système le sert aussi, et deux copies auraient divergé au
        premier mot changé. Ce qui reste ici est l'accès au catalogue et aux
        relevés, qui appartient à l'orchestrateur.
        """
        return introspection.accueil_de_source(
            self.catalog.get(nom), self.releves.de(nom), precedente
        )

    def _choix_de_source(self, state: OrchestratorState) -> str | None:
        """La source que ce message CHOISIT, sans rien demander d'autre.

        ``None`` hors conversation : sans fil, il n'y a rien à lier.

        La reconnaissance est **déterministe** — le nom d'une source du
        catalogue, et le fait que le message ne dise presque rien d'autre
        (``introspection.choix_de_source``). C'est le moment où le choix de
        l'utilisateur devient un fait persisté ; le faire trancher par un
        modèle serait payer un aller-retour pour comparer deux chaînes.

        **Aucun état de conversation n'est consulté**, et c'est une correction :
        la reconnaissance dépendait d'abord d'un drapeau « une proposition
        attend une réponse », posé au tour d'avant. Le parcours mesuré l'a mise
        en défaut — l'agent système avait répondu au premier message en
        énumérant les sources, sans que le drapeau soit posé, et le « titanic »
        du tour suivant s'est fait rendre le catalogue au lieu d'être retenu.
        Un choix de source se lit dans le message, pas dans l'histoire.
        """
        if state.get("source_in") is None:
            return None
        return introspection.choix_de_source(state["question"], self.catalog)

    def _court_circuit_du_choix_de_source(
        self, state: OrchestratorState, start: float
    ) -> dict | None:
        """Le message choisit une source : on la lie et on en accuse réception, sans LLM."""
        choisie = self._choix_de_source(state)
        if choisie is None:
            return None
        precedente = state.get("source_in") or ""
        return {
            "source_out": choisie,
            "clarification": self.accuser_la_source(choisie, precedente),
            "trace": [self._step("plan", f"source choisie : {choisie} — sans appel LLM", start)],
        }

    def _lier_la_source(self, plan: Plan, ctx: PlanContext) -> tuple[str | None, str]:
        """La source que la conversation retient, et ce qu'on en dit à l'utilisateur.

        ``None`` hors conversation : il n'y a rien à lier, et le tour se
        comporte comme avant ce mécanisme.

        L'avis n'est rendu qu'aux deux moments où l'utilisateur doit savoir sur
        quoi on travaille : la **première** fois qu'une source est liée (« s'il
        n'y en a qu'une, il l'annonce au lieu de poser une question inutile »),
        et à chaque **bascule**. Les tours suivants n'en disent rien : répéter
        « je travaille sur titanic » à chaque réponse serait du bruit.

        Le risque d'une bascule n'est pas qu'elle ait lieu, c'est qu'elle ait
        lieu **en silence** — répondre sur d'autres données sans le dire. D'où
        l'avis, mis en tête de la réponse et non dans la trace.

        Une source **imposée par l'appelant** (paramètre ``source`` de
        ``ask()``) ne lie rien : c'est un paramètre d'API pour un tour, pas le
        choix de l'utilisateur.
        """
        liee = ctx.source_de_travail
        if liee is None:
            return None, ""
        if ctx.source_imposee:
            return liee, ""
        # Seule une source DÉCLARÉE se lie : un tableau intermédiaire du fil est
        # interrogeable, ce n'est pas une source de données, et le retenir
        # remplacerait la source de travail par un résultat de requête.
        declarees = [s.name for s in ctx.catalogue_declare.sources]
        retenue = plan.source if plan.source in declarees else ""
        if not retenue or retenue == liee:
            return liee, ""
        if not liee:
            return retenue, f"Je travaille sur la source `{retenue}`."
        return retenue, f"Je passe sur la source `{retenue}` — on travaillait sur `{liee}`."

    # -- le nœud du plan -------------------------------------------------------

    def _plan_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        choix = self._court_circuit_du_choix_de_source(state, start)
        if choix is not None:
            return choix
        system_prompt, mesures = self._peser_le_prompt(state)
        plan = self._demander_un_plan(system_prompt, state, mesures)
        if plan is None:
            return self._clarify(
                Plan(capability="query"),
                self._ce_que_le_schema_en_dit(state) or self._repli_du_planificateur(state),
                start,
                **mesures,
            )
        ctx = PlanContext(
            source_imposee=state.get("source_name"),
            pending=state.get("pending_in"),
            workspace=state.get("workspace"),
            catalogue_declare=self.catalog,
            catalogue_effectif=self._effective_catalog(state),
            question=state["question"],
            source_de_travail=state.get("source_in"),
        )
        question = self._appliquer_les_regles(plan, ctx)
        relue = self._relire_sans_la_clause_dabsence(plan, ctx, system_prompt, state, mesures)
        if relue:
            question = self._appliquer_les_regles(plan, ctx)
        retenue, avis = self._lier_la_source(plan, ctx)
        if question is not None:
            return self._clarify(plan, question, start, **mesures) | {"source_out": retenue}
        detail = f"{plan.capability}" + (f" sur {plan.source}" if plan.source else "")
        if relue:
            detail += f" — seconde lecture sans « {relue} »"
        return {
            "plan": plan,
            "source_out": retenue,
            "avis_de_source": avis,
            "trace": [self._step("plan", detail, start, **mesures)],
        }

    def _relire_sans_la_clause_dabsence(
        self,
        plan: Plan,
        ctx: PlanContext,
        system_prompt: str,
        state: OrchestratorState,
        mesures: dict,
    ) -> str:
        """Repose la MÊME question sans la clause qu'on sait lire — une fois, et sous conditions.

        **Ce que la clause coûte.** Mesuré sur le planificateur, 5 tirages par
        variante, la même phrase à une clause près :

            sans « sans famille à bord » -> ['age', 'embarked', 'fare', 'pclass', 'sex']   5/5
            avec                         -> ['embarked', 'fare', 'pclass', 'sex', 'sibsp'] 4/5

        La clause ne se contente pas de ne remplir qu'un des deux compteurs :
        elle fait PERDRE `age`, que le modèle extrayait sans faillir. Le tour
        ressort `invalid` de toute façon — sur `age` au lieu de `sibsp` — et le
        fil se referme exactement comme avant. Remplir les deux compteurs
        (``_regle_lire_labsence_daccompagnants``) ne suffit donc pas : il faut
        aussi rendre au modèle l'attention que la clause lui prenait.

        Or cette clause, on la lit sans lui. La lui laisser porter, c'est la
        payer deux fois.

        **Quatre conditions, et il les faut toutes** — c'est ce qui borne le
        coût à un appel LLM sur un chemin qui, sans lui, ne rendait rien :

        1. le plan est une prédiction sur un dataset dont on a le schéma ;
        2. il lui manque encore des features — une prédiction complète n'a rien
           à relire ;
        3. le message porte la construction d'absence ;
        4. cette construction s'isole proprement du reste (cf.
           ``sans_la_clause_dabsence``) — sinon on réécrirait la phrase de
           quelqu'un pour la lui reposer, et on ne fait pas ça.

        **La première lecture voit le message ENTIER**, et c'est ce qui rend
        cette seconde sûre : c'est elle qui a décidé de la capacité, sur tout ce
        que la phrase disait. « Combien de passagers sans famille à bord ? » est
        une requête, sa clause est son filtre, et elle n'arrive jamais ici.

        **Ce que la seconde lecture peut faire : AJOUTER.** Les features de la
        première priment — elle a vu la phrase entière. La relecture ne sert
        qu'à récupérer ce que la clause avait fait tomber.

        Rend la clause retirée, pour la trace ; ``""`` quand il n'y a pas eu de
        seconde lecture.
        """
        if plan.capability != "predict" or plan.dataset not in SCHEMAS:
            return ""
        manquantes = set(get_schema(plan.dataset).model_fields) - set(plan.features)
        if not manquantes:
            return ""
        clause = absence_daccompagnants(ctx.question)
        if not clause:
            return ""
        allege = sans_la_clause_dabsence(ctx.question)
        if allege == ctx.question:
            return ""
        second = self._demander_un_plan(system_prompt, {**state, "question": allege}, dict(mesures))
        if second is None or second.capability != "predict":
            return ""
        plan.features = {**second.features, **plan.features}
        return clause

    # -- le nœud système : « est-ce une question sur moi ? » -------------------

    def _tour_deja_engage(self, state: OrchestratorState) -> str:
        """Les tours où le message n'est pas à interpréter — ni à payer.

        Deux messages ne sont pas des questions et n'ont donc rien à faire
        chez l'agent système : les features d'une prédiction en attente, et le
        nom d'une source qu'on choisit. Y faire passer l'agent système
        coûterait un aller-retour pour apprendre ce qu'on sait déjà — et lui
        donnerait l'occasion de s'emparer d'un message qui ne lui est pas
        adressé. C'est arrivé, et c'est mesuré : un « titanic » de validation a
        reçu l'inventaire du catalogue en réponse (§12 de
        `docs/surface-conversationnelle.md`).
        """
        if state.get("pending_in") is not None:
            return "prédiction en attente de features — passe au planificateur"
        if self._choix_de_source(state) is not None:
            return "choix de source — passe au planificateur"
        return ""

    def _system_node(self, state: OrchestratorState) -> dict:
        """« Est-ce une question sur moi ? » — et c'est le MODÈLE qui répond.

        Premier nœud du graphe. L'agent système reçoit la question avec cinq
        outils qui rendent les faits du dépôt (cf. ``orchestrator/systeme``) ;
        s'il en appelle un, la question était sur le système et il en formule
        le résultat. S'il n'appelle rien, le tour repart au planificateur
        exactement comme avant.

        **Ce qui a changé, et pourquoi.** La reconnaissance était un lexique de
        tournures. Mesuré par le propriétaire sur dix formulations naturelles
        de la même question (« quelles données as-tu ? ») : trois
        court-circuitées, sept parties au planificateur, classées `query`, et
        du SQL écrit pour répondre à une question de configuration. La famille
        est ouverte — « c'est quoi ton périmètre ? », « tu bosses sur quoi ? »,
        « montre-moi ce que tu as » — et un lexique est une liste. Le prix payé
        est un appel LLM en tête de CHAQUE question, y compris celles sur les
        données ; il est mesuré et assumé (§9 de
        `docs/surface-conversationnelle.md`).

        **Trois façons de ne pas servir le modèle**, et c'est la ceinture que
        l'ancien chemin déterministe est devenu :

        - aucun outil appelé — la question n'était pas pour lui ;
        - la formulation invente un nom, en omet un rendu par l'outil, ou cite
          une source sans porter un seul fait de sa fiche : on lui rend les
          mêmes faits et la même question pour qu'il RECOMMENCE, et les faits ne
          sont servis tels quels que si la seconde formulation échoue elle aussi
          (``servir_la_reponse``) ;
        - l'agent système lui-même n'a pas abouti : le tour repart au
          planificateur au lieu d'échouer.

        Ce dernier cas est délibérément **fail-open**, et seulement pour ce que
        le MODÈLE rate (sortie invalide, plafond d'allers-retours atteint) : un
        planificateur qui aurait su répondre ne doit pas être privé de la
        question par un incident de ce nœud-ci. Ce qu'un OUTIL rate — une
        source injoignable, un catalogue illisible — n'est pas rattrapé : c'est
        un vrai défaut de configuration, il remonte au garde-fou et il est dit.

        Le tour de réparation, lui, est **fail-closed** et pour la raison
        inverse : son repli est déjà prêt et il est juste, donc un incident n'y
        coûte rien à l'utilisateur (cf. ``systeme._reformuler``).
        """
        start = time.monotonic()
        engage = self._tour_deja_engage(state)
        if engage:
            return {"trace": [self._step("system", engage, start)]}
        try:
            resultat = run_systeme(
                state["question"],
                model=self.model,
                catalogue_declare=self.catalog,
                catalogue_effectif=self._effective_catalog(state),
                registre=self.registry,
                releves=self.releves,
                request_limit=self.settings.systeme_request_limit,
                source_de_travail=state.get("source_in") or "",
                echange_precedent=state.get("echange_precedent"),
            )
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            incident = reference_dincident()
            logger.warning("agent système écarté (incident %s) : %s", incident, exc)
            return {
                "trace": [
                    self._step(
                        "system",
                        f"agent système écarté (incident {incident}) — passe au planificateur",
                        start,
                    )
                ]
            }
        if not resultat.concerne_le_systeme:
            return {
                "trace": [
                    self._step("system", "aucun outil appelé — passe au planificateur", start)
                ]
            }
        liaison = self._liaison_demandee(state, resultat, start)
        if liaison is not None:
            return liaison
        outils = ", ".join(resultat.outils_appeles)
        rendue = servir_la_reponse(
            resultat,
            question=state["question"],
            model=self.model,
            request_limit=self.settings.systeme_request_limit,
        )
        servie = rendue.texte
        detail = f"{outils} — {rendue.detail}"
        retenue, avis = self._lier_la_source_nommee(state)
        if avis:
            servie = f"{avis}\n\n{servie}"
        rendu: dict = {"system": servie, "trace": [self._step("system", detail, start)]}
        if retenue:
            rendu["source_out"] = retenue
        return rendu

    def _lier_la_source_nommee(self, state: OrchestratorState) -> tuple[str, str]:
        """La source que le message NOMME est retenue, même quand c'est l'agent
        système qui répond — et c'est un trou qu'on bouche, pas une règle neuve.

        ``_regle_source_de_la_conversation`` lie déjà la source qu'un message
        nomme, et ``_lier_la_source`` en annonce la bascule. Les deux vivent
        dans le nœud du PLAN. Or l'agent système court-circuite ce nœud : un
        message qui nomme une source et pose une question à laquelle on répond
        en LISANT la configuration n'atteignait donc aucune des deux, et
        repartait sans que rien ne soit lié.

        Mesuré, 3 tirages sur 3, sur « Je souhaiterais consulter la source
        facturation ; peux-tu m'indiquer ce qu'on y trouve ? » : la réponse est
        juste — les trois feuilles et leurs colonnes, ce qui est exactement ce
        qu'on y trouve — et la source reste **déliée**. La question suivante du
        même fil, « combien de factures ? », recevait alors l'inventaire des
        cinq sources : le tour d'après était perdu (`docs/sources-de-demonstration.md`,
        dette D).

        C'est la réponse à l'arbitrage : ce n'est pas « accuser réception » OU
        « répondre », c'est les deux, et la seconde moitié ne coûte rien —
        la désignation est lue dans le texte par ``source_nommee``, sans le
        moindre aller-retour.

        Les mêmes garde-fous que dans le nœud du plan, et pour les mêmes
        raisons : hors conversation (``source_in is None``) il n'y a pas de fil
        à lier ; une source **imposée** par l'appelant est un paramètre d'API et
        ne lie rien ; et une bascule est **annoncée**, parce que ce qui est
        dangereux n'est pas de changer de source mais de changer sans le dire.
        """
        if state.get("source_in") is None or state.get("source_name"):
            return "", ""
        nommee = introspection.source_nommee(state["question"], self.catalog)
        precedente = state.get("source_in") or ""
        if not nommee or nommee == precedente:
            return "", ""
        if not precedente:
            return nommee, f"Je travaille sur la source `{nommee}`."
        return nommee, f"Je passe sur la source `{nommee}` — on travaillait sur `{precedente}`."

    def _liaison_demandee(
        self, state: OrchestratorState, resultat: ResultatSysteme, start: float
    ) -> dict | None:
        """L'agent système a demandé à lier une source : on vérifie, puis on lie.

        C'est le SECOND chemin de la liaison d'une source, et non le
        remplacement du premier. Le court-circuit déterministe de
        ``_court_circuit_du_choix_de_source`` reste devant, intact : un message
        réduit au nom d'une source ne paie toujours aucun aller-retour — il ne
        passe même pas par ce nœud-ci (``_tour_deja_engage``). Ce chemin ne voit
        que ce qui lui échappait : la même intention dite en une phrase, qui
        partait à la récupération et s'y faisait répondre « je n'ai pas
        interrogé la source… reformule ».

        **Deux vérifications avant de lier**, et c'est ce qui empêche ce chemin
        de faire basculer une conversation à l'insu de qui la mène.

        1. La source liée est celle que l'**utilisateur** a nommée, pas celle
           que le modèle a passée à l'outil. Le modèle propose un argument à
           chaque appel, parfois au hasard des descriptions ; la même précaution
           est prise par ``_regle_source_de_la_conversation`` et par
           ``_regle_ouvrir_une_source``, pour la même raison, et elle est
           mesurée (`docs/surface-conversationnelle.md` §14).
        2. Hors conversation (``source_in is None``), il n'y a pas de fil à
           lier : l'appel d'outil ne retient rien et le tour repart au
           planificateur, exactement comme avant ce chemin.

        Échouer l'une ou l'autre ne perd pas le tour : ``None`` rend la main au
        reste du nœud système, qui servira les faits que l'outil a rendus.

        La réponse servie est l'accueil **déterministe** — celui que l'outil a
        rendu, pas la reformulation du modèle. Il n'y a rien à formuler : la
        phrase accuse réception d'un nom et y ajoute le volume lu dans la
        source. La vérification de fondation (``defaut_de_fondation``) ne
        s'applique donc pas ici, faute d'avoir quoi comparer.
        """
        if not resultat.source_a_lier or state.get("source_in") is None:
            return None
        nommee = introspection.source_nommee(state["question"], self.catalog)
        if nommee != resultat.source_a_lier:
            return None
        precedente = state.get("source_in") or ""
        return {
            "system": self.accuser_la_source(nommee, precedente),
            "source_out": nommee,
            "trace": [
                self._step(
                    "system",
                    f"travailler_sur_une_source — source liée : {nommee}",
                    start,
                )
            ],
        }

    # -- le nœud de rappel : « parle-t-on de ce que j'ai déjà produit ? » ------

    @staticmethod
    def _rien_a_rappeler(state: OrchestratorState) -> str:
        """Les tours où ce nœud n'a rien à faire — et ne doit donc rien coûter.

        Le cas du fil vide est traité par l'appelant, avant même cet appel :
        **un fil qui n'a encore rien produit n'a rien à rappeler**, et il ne
        laisse donc aucune trace. Ce n'est pas un lexique déguisé, c'est une
        précondition structurelle — le catalogue est vide, les deux outils ne
        pourraient que refuser, et l'appel au modèle serait payé pour apprendre
        ce que le disque dit déjà. Conséquence mesurable : une question posée
        dans une conversation NEUVE ne paie pas ce nœud, ce qui est exactement
        le cas de la batterie de `scripts/mesure_surface_conversationnelle.py`.

        Reste ce que dit ``_tour_deja_engage`` : un message qui apporte les
        features d'une prédiction en attente n'est pas une question, et le
        laisser passer donnerait à ce nœud l'occasion de s'emparer d'un message
        qui ne lui est pas adressé — c'est le défaut mesuré au §12 de
        `docs/surface-conversationnelle.md`, par une autre porte.

        **Et ce retrait-là est BORNÉ**, parce que sans borne il confisquait le
        fil. Mesuré, 3 tirages sur 3 (fil `a` de
        `scripts/mesure_fils_de_prediction.py`) : un fil produit un tableau,
        une prédiction y reste en attente d'une feature, et « reprends le
        tableau précédent » ne trouve plus rien — ce nœud s'était retiré, le
        planificateur partait en `query` sur un objet qu'il n'interroge pas, la
        récupération ne rendait aucune ligne et l'utilisateur lisait « je n'ai
        pas interrogé la source ». Le tableau était là, le fil l'avait produit,
        et il était devenu inatteignable : il fallait ouvrir une conversation.

        La borne n'est pas un lexique de plus, c'est celui que ce nœud emploie
        DÉJÀ pour décider s'il doit avouer une absence :
        ``designation_dun_artefact_passe``. Un message qui désigne un artefact
        déjà produit — un marqueur d'antériorité ET une cible — ne complète pas
        une prédiction : aucune feature d'aucun schéma n'est un tableau, une
        figure ou un « précédent ». Le retrait vaut donc pour ce qui complète,
        et pour cela seulement.

        Le témoin qui dit que la borne n'a pas débordé est le fil `e` : une
        prédiction en attente, « une femme », et elle aboutit toujours. Ce
        message-là ne désigne rien et ne vise rien — le retrait le couvre
        encore.
        """
        if state.get("pending_in") is None:
            return ""
        if designation_dun_artefact_passe(state["question"]):
            return ""
        return "prédiction en attente de features"

    # Ce qu'on dit au code généré quand ce qu'on lui donne à reprendre est un
    # TABLEAU et non du code. Le fichier est déjà monté et déjà décrit par
    # `_mount_workspace` ; ce gabarit ajoute la seule chose qui manque, et elle
    # est décisive : le calcul porte sur CE tableau-là, pas sur la source.
    #
    # Sans « sans réinterroger la source », le code généré repart volontiers du
    # CSV d'origine — il est monté lui aussi — et le tour répond alors à une
    # autre question que celle qu'on lui a posée : « les pourcentages du tableau
    # précédent » n'est pas « les pourcentages de la source ».
    _CALCUL_SUR_UN_TABLEAU = (
        "Le tableau « {nom} » a déjà été produit dans cette conversation et il "
        "est monté sous /data/{fichier} ({lignes} ligne(s) ; colonnes : "
        "{colonnes}). Charge-le avec pandas et calcule à partir de LUI, sans "
        "réinterroger la source. Imprime le résultat complet.\n\n"
        "Demande : {demande}"
    )

    def _consigne_de_calcul(self, artefact: WorkspaceArtifact, demande: str) -> str:
        return self._CALCUL_SUR_UN_TABLEAU.format(
            nom=artefact.name,
            fichier=artefact.file,
            lignes=artefact.row_count,
            colonnes=", ".join(artefact.columns) or "(inconnues)",
            demande=demande,
        )

    def _rejouer_un_code(
        self, state: OrchestratorState, artefact: WorkspaceArtifact, modification: str
    ) -> AnalysisResult:
        """Reprend le code d'un artefact, y applique la modification, le réexécute.

        **Et un TABLEAU se calcule.** Un tableau n'a pas de code à rejouer, mais
        il a un CSV, et ce CSV est déjà monté sous ``/data/`` par
        ``_mount_workspace``. « reprends le tableau précédent et donne-moi les
        pourcentages » écrit donc du Python qui lit ce fichier-là, et le bac à
        sable rend le chiffre. Le code est absent, la matière ne l'est pas.

        C'est le seul chemin par lequel l'application rendait un chiffre FAUX
        sans que rien ne s'en aperçoive. Mesuré, catalogue métier, sur le
        tableau des commandes par canal (magasin 100, grossiste 36, en ligne 44
        — 180 au total) : le contenu du tableau partait au modèle, qui divisait
        lui-même et rendait 52,63 / 18,95 / 23,08 %. La somme ne fait pas 100 et
        aucune des trois n'est juste ; le tableau, lui, était exact et vérifié.
        Un chiffre dérivé n'est pas une formulation, c'est un résultat : il se
        calcule, et il se vérifie comme le reste — par son exécution.

        **Par le bac à sable, et par lui seul.** ``run_analysis`` est appelée
        exactement comme au premier tour : mêmes montages en lecture seule,
        même image durcie, donc réseau coupé, mémoire et PIDs bornés,
        capabilities retirées — et même sémaphore de places
        (``SandboxPlaces``), puisque c'est ``SandboxSession`` qui le prend. Un
        rejeu n'est pas un chemin de confiance parce que le code vient de nous :
        le code vient d'un modèle, et il a été écrit au tour 1 pour un décor
        qui a pu changer.

        Le code rappelé arrive par ``previous_code`` — le paramètre qui servait
        déjà à l'ajustement du tour immédiatement précédent. Ce qui change n'est
        pas le mécanisme, c'est **d'où vient le code** : d'un artefact nommé, et
        non du dernier tour.

        La source est celle qui avait été interrogée, retenue avec l'artefact.
        Si elle n'est plus au catalogue — renommée, retirée — on remonte le
        décor de la source du tour courant, et à défaut celui du fil ; le code
        échouera peut-être, et il échouera dans le bac à sable avec un message,
        ce qui vaut mieux que de refuser un rejeu qui aurait pu marcher.
        """
        catalogue = self._effective_catalog(state)
        nom = artefact.source or state.get("source_in") or ""
        resolu = self._match_source_name(nom, catalogue) if nom else None
        source = catalogue.get(resolu) if resolu else None
        if source is None and catalogue.sources:
            source = catalogue.sources[0]
        if source is None:
            raise KeyError("aucune source à monter pour rejouer ce code")
        tableau = artefact.est_un_tableau
        # Ce que le rejeu VISE : le tableau qu'on a nommé, et lui seul. Un code,
        # lui, a été écrit pour le décor d'un autre tour et peut lire n'importe
        # lequel des fichiers qui y étaient montés : lui retirer un montage,
        # c'est le faire échouer sur un `FileNotFoundError` que rien n'annonce.
        objets = [artefact] if tableau else list(self._objets_du_fil(state))
        with self._decor_de_donnees(state, source, objets) as (data_files, data_context, avis):
            resultat = run_analysis(
                self._consigne_de_calcul(artefact, modification) if tableau else modification,
                data_files=data_files,
                data_context=data_context,
                # Un tableau n'a pas de code d'origine. Lui en passer un serait
                # lui passer ses propres lignes de CSV — `lire` rend le
                # contenu, pas du Python — et le modèle repartirait d'un
                # « code » qui n'en est pas un.
                previous_code=None if tableau else self._lire_le_code(state, artefact),
                model=self.model,
                settings=self.settings,
                sandbox=self._sandbox_override,
                # Un rejeu réécrit le code : il doit lire le dictionnaire comme
                # le premier jet. Un tableau intermédiaire du fil n'en déclare
                # pas — `dictionary_text()` rend alors `None`, et le prompt est
                # celui d'avant.
                dictionary=source.dictionary_text(),
            )
        # La coupe est une propriété du DÉCOR, et le décor meurt avec le bloc
        # ci-dessus. On l'attache donc au résultat, qui va traverser l'agent de
        # rappel avant d'être rendu — c'est le seul chemin par lequel elle
        # atteint la réponse. Le premier jet, lui, a son avis sous la main
        # (``_analysis_node``) et n'a rien à faire voyager.
        return resultat.model_copy(update={"truncation_notice": avis}) if avis else resultat

    @staticmethod
    def _lire_le_code(state: OrchestratorState, artefact: WorkspaceArtifact) -> str:
        workspace = state["workspace"]
        return workspace.lire(artefact)

    def _rappel_node(self, state: OrchestratorState) -> dict:
        """« Parle-t-on d'un artefact déjà produit ? » — et c'est le MODÈLE qui répond.

        Deux outils, décrits dans ``orchestrator/rappel`` : relire un artefact
        par son nom, en rejouer un avec une modification. Le catalogue du fil
        est dans son prompt — **une ligne par artefact, jamais leur contenu**.
        S'il n'appelle rien, le tour repart au planificateur comme avant.

        **Pourquoi un nœud et non une capacité du planificateur.** Même raison
        qu'au §6 de `docs/surface-conversationnelle.md` pour la capacité
        système : ajouter `replay` à `Capability` la mettrait en concurrence
        avec `query` et `analyze` à CHAQUE question, y compris les neuf sur dix
        qui ne rappellent rien. Ici la question ne se pose que dans un fil qui a
        déjà produit quelque chose.

        **Trois façons de ne pas servir le modèle**, et ce sont les mêmes que
        pour l'agent système :

        - aucun outil appelé — la demande n'était pas pour lui ;
        - tous les outils ont refusé : c'est le REFUS qui est servi, pas la
          phrase du modèle. Un artefact absent ou évincé doit s'entendre comme
          tel, jamais se faire remplacer par une invention (famille `acfd8f5`) ;
        - la formulation cite un nom d'artefact que la conversation n'a jamais
          produit, ou rend la sentinelle alors qu'un outil a répondu : les
          faits sont servis tels quels (``defaut_de_formulation``).

        Un incident du MODÈLE (sortie invalide, plafond d'allers-retours) est
        **fail-open** : le tour repart au planificateur au lieu d'échouer. Ce
        qu'un OUTIL rate — un rejeu qui n'aboutit pas — n'est pas rattrapé : il
        est rendu comme une analyse en échec, et il est dit.

        **Et quand il décline alors que le message désignait quelque chose ?**
        Le premier cas ci-dessus — « aucun outil appelé » — recouvrait deux
        situations très différentes : une question ordinaire qui ne le concerne
        pas, et « reprends le camembert que tu m'avais fait » dans un fil qui
        n'en porte aucun. La seconde laissait le planificateur produire une
        figure neuve sans un mot. C'est ``_aveu_dabsence`` qui les sépare, et il
        le fait **sans le modèle** : la désignation se lit dans le message, et
        l'absence dans le catalogue.
        """
        start = time.monotonic()
        workspace = state.get("workspace")
        if workspace is None:
            # Pas de conversation du tout (``ask()`` sans ``conversation_id``) :
            # aucun magasin, donc rien qui puisse affirmer une absence.
            return {}
        if not workspace.catalogue():
            # AUCUNE trace quand le message ne désigne rien, et c'est délibéré :
            # il n'y a pas d'artefact dans ce fil, donc rien à décider, rien à
            # appeler et rien à observer. Une ligne « rien à rappeler » sur
            # chaque tour de chaque conversation qui n'a rien produit serait du
            # bruit dans une trace qu'on déplie pour comprendre ce qui s'est
            # passé. Un fil qui A des artefacts, lui, laisse toujours une ligne
            # — y compris quand le modèle décline.
            #
            # Un message qui DÉSIGNE un artefact passé, lui, est servi même ici,
            # et sans le moindre appel au modèle : un fil vide est le cas où
            # l'absence est la plus certaine.
            return self._aveu_dabsence(state, start)
        rien = self._rien_a_rappeler(state)
        if rien:
            return {"trace": [self._step("rappel", f"{rien} — passe au planificateur", start)]}
        try:
            resultat = run_rappel(
                state["question"],
                model=self.model,
                workspace=state["workspace"],
                rejouer=lambda artefact, modification: self._rejouer_un_code(
                    state, artefact, modification
                ),
                request_limit=self.settings.rappel_request_limit,
            )
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            incident = reference_dincident()
            logger.warning("agent de rappel écarté (incident %s) : %s", incident, exc)
            return {
                "trace": [
                    self._step(
                        "rappel",
                        f"agent de rappel écarté (incident {incident}) — passe au planificateur",
                        start,
                    )
                ]
            }
        if not resultat.concerne_le_rappel:
            # Le modèle n'a rien rappelé. Si le message DÉSIGNAIT pourtant un
            # artefact passé, c'est ici que le défaut se jouait : le
            # planificateur produisait une figure neuve, correcte, servie sans
            # un mot — et l'utilisateur repartait en croyant qu'on avait
            # retrouvé la sienne.
            return self._aveu_dabsence(state, start, decline=True)
        if resultat.rejeu is not None:
            return self._rendu_du_rejeu(state, resultat.rejeu, start)
        outils = ", ".join(resultat.outils_appeles)
        if resultat.tout_a_ete_refuse:
            return {
                "rappel": "\n\n".join(resultat.refus),
                "trace": [self._step("rappel", f"{outils} — refus servi tel quel", start)],
            }
        defaut = defaut_de_formulation(resultat.reponse, state["workspace"], resultat.attendus)
        if defaut:
            return {
                "rappel": resultat.faits,
                "trace": [self._step("rappel", f"{outils} — faits servis ({defaut})", start)],
            }
        return {
            "rappel": resultat.reponse,
            "trace": [self._step("rappel", f"{outils} — formulé par le modèle", start)],
        }

    def _aveu_dabsence(
        self, state: OrchestratorState, start: float, *, decline: bool = False
    ) -> dict:
        """« Je ne l'ai pas produit » — dit par le CATALOGUE, jamais par le modèle.

        Le tour continue : le planificateur fera la figure, et elle sera juste.
        Ce qu'on ajoute est la seule chose que le modèle ne sait pas dire — que
        ce qui arrive est neuf. La phrase est mise en tête de la réponse par la
        synthèse, comme l'avis de source, et pour la même raison : la trace
        n'est pas dépliée par défaut, et ce qu'on veut éviter n'est pas de
        produire une figure — c'est de la faire passer pour un rappel.

        ``decline`` distingue les deux portes d'entrée dans la trace : un fil
        sans aucun artefact (le nœud ne paie même pas un appel au modèle) et un
        fil où l'agent de rappel a bien tourné puis décliné.
        """
        aveu = aveu_dabsence(state["workspace"], state["question"])
        if not aveu:
            if decline:
                return {
                    "trace": [
                        self._step("rappel", "aucun outil appelé — passe au planificateur", start)
                    ]
                }
            return {}
        marqueur = designation_dun_artefact_passe(state["question"])
        prefixe = "aucun outil appelé, " if decline else ""
        return {
            "avis_dabsence": aveu,
            "trace": [
                self._step(
                    "rappel",
                    f"{prefixe}désignation sans artefact (« {marqueur} ») — absence dite, "
                    "puis passe au planificateur",
                    start,
                )
            ],
        }

    def _rendu_du_rejeu(self, state: OrchestratorState, rejeu: Rejeu, start: float) -> dict:
        """Un rejeu est une ANALYSE, et il est rendu comme telle.

        Le state reçoit un ``plan`` d'analyse et un ``analysis`` : la synthèse,
        l'affichage des figures, la mémorisation du tour et le nouvel artefact
        de code passent alors par les chemins qui existent déjà. Fabriquer ici
        une réponse à part aurait dupliqué les quatre.

        Le ``plan`` est renseigné et non laissé vide : ce tour a bien exécuté du
        code sur une source, et ``ChatAnswer.plan`` doit le dire.
        """
        resultat = rejeu.resultat
        workspace = state["workspace"]
        origine = workspace.retenu(rejeu.artefact)
        source = (origine.source if origine is not None else "") or state.get("source_in") or ""
        images = (
            [r for r in resultat.execution.results if r.mime == "image/png"]
            if resultat.succeeded
            else []
        )
        detail = (
            f"rejeu de {rejeu.artefact} — {resultat.attempts} essai(s), "
            f"{len(images)} figure(s), statut {resultat.execution.status}"
        )
        if not resultat.succeeded:
            detail += f" — {self._cause_lisible(resultat.execution.error)}"
        artefact = self._memoriser_le_code(workspace, resultat, state["question"], source)
        if artefact is not None:
            detail += f" — retenu sous le nom {artefact.name}"
        # Un rejeu réécrit du code, donc il a lu le dictionnaire, donc il a pu
        # le lire amputé : l'avis remonte ici comme il remonte du premier jet.
        # Et il a tourné sur les MÊMES CSV matérialisés, donc sur la même
        # tranche : cet avis-là remonte aussi, et il ne remontait pas. Le tour
        # d'avant disait « sur les 10 000 relevés », le rejeu rendait 290 sans
        # un mot — le même chiffre, redevenu muet en changeant de nœud.
        mesures = {"truncated": False, "truncation": ""}
        self._ajoute_avis(mesures, resultat.truncation_notice)
        self._ajoute_avis(mesures, resultat.dictionary_notice)
        return {
            "plan": Plan(capability="analyze", source=source or None),
            "analysis": resultat,
            "artifacts": images,
            "trace": [self._step("rappel", detail, start, **mesures)],
        }

    def _retrieval_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        # `closing` et non un adaptateur gardé : un nœud est exécuté à chaque
        # question, et un pool de connexions abandonné à chaque fois finit par
        # remplir le `max_connections` du serveur (audit §2.3).
        source = self._resolve_source(plan, self._effective_catalog(state))
        with closing(open_source(source)) as adapter:
            outcome = run_retrieval(
                state["question"],
                adapter=adapter,
                model=self.model,
                settings=self.settings,
                # Ce que la source DÉCLARE vouloir dire. Un tableau
                # intermédiaire de la conversation n'en a pas — `dictionary`
                # vaut alors `None` et le prompt est celui d'avant.
                dictionary=source.dictionary_text(),
            )
        artifacts = [_table_artifact(outcome.result)] if outcome.result else []
        # mémorise le tableau produit pour le réutiliser aux tours suivants
        self._memorize(state, outcome.result)
        detail = outcome.sql or f"{len(outcome.executed)} requête(s), aucune n'a abouti"
        mesures: dict = {"truncated": False, "truncation": ""}
        self._ajoute_avis(mesures, outcome.dictionary_notice)
        return {
            "retrieval": outcome,
            "artifacts": artifacts,
            "dire_du_dictionnaire": introspection.ce_qu_en_dit_le_dictionnaire(
                state["question"], source.dictionary_text(), source.name
            ),
            "trace": [self._step("retrieval", detail, start, **mesures)],
        }

    @staticmethod
    def _memorize(state: OrchestratorState, result: QueryResult | None) -> None:
        """Persiste un tableau non vide dans l'espace de travail de la conversation."""
        workspace = state.get("workspace")
        if workspace is not None and result is not None and result.rows:
            workspace.save_table(
                result.columns, result.rows, state["question"], tronque=result.truncated
            )

    @staticmethod
    def _mount_workspace(
        state: OrchestratorState,
        data_files: dict[Path, str],
        data_context: str,
        objets: list[WorkspaceArtifact],
    ) -> tuple[str, list[str]]:
        """Monte les CSV que CE tour vise, et les décrit au code généré.

        ``objets`` est ce que le tour désigne, et non ce que le fil porte. C'est
        le correctif du défaut le plus grave du relevé de C50 (§3.2) : les deux
        fichiers étaient montés côte à côte — la table de la source et le
        tableau du tour d'avant — et rien ne disait lequel répondait à la
        question. Le code généré ouvrait le second, et l'agent servait « écran
        illisible, 3 cas » là où la source en porte 152. Ce n'était pas un
        chiffre visiblement faux : c'était un chiffre plausible, tiré au sort.

        **L'information ne manquait pas, l'arbitrage manquait.** Les deux
        fichiers étaient bien annoncés au modèle (``_initial_prompt``), et le
        seul des deux qualifié l'était par le mot *réutilisable*. Le montage
        était un décor ; rien ne départageait ses pièces. On ne le départage
        donc pas par une phrase de plus — cinq ont été écrites puis retirées
        sur ce projet, chacune au prix d'une question de la surface — on ne
        monte que ce dont le tour a besoin. Le plan dit ce dont il a besoin :
        sa source, désormais capable de désigner un objet du fil
        (``_regle_source_de_la_conversation``).

        Rend aussi les NOMS de ceux qui sont eux-mêmes une tranche. Un tableau
        intermédiaire est le produit d'une requête, et une requête est coupée à
        ``retrieval_max_rows`` : le CSV mémorisé peut donc être un échantillon,
        et rien dans le CSV ne le dit. Un tour qui le remonte pour y compter
        recommence exactement le défaut que la matérialisation avait, avec un
        tour d'écart en plus — c'est la même coupe, vue au tour suivant.
        """
        workspace = state.get("workspace")
        if workspace is None or not objets:
            return data_context, []
        for artefact in objets:
            data_files.setdefault(workspace.path_of(artefact), artefact.file)
        lines = [
            f"- /data/{a.file} ({a.row_count} lignes{' ; TRONQUÉ' if a.tronque else ''}"
            f" ; colonnes : {', '.join(a.columns)})"
            for a in objets
        ]
        extra = "Objets intermédiaires de la conversation (réutilisables) :\n" + "\n".join(lines)
        contexte = f"{data_context}\n\n{extra}" if data_context else extra
        return contexte, [a.name for a in objets if a.tronque]

    # Ce qu'on dit d'une grandeur calculée sur une TRANCHE, d'où qu'elle vienne.
    #
    # UN seul gabarit pour les deux coupes qui parlent d'ici — la table
    # matérialisée pour l'analyse, et le tableau intermédiaire d'un tour
    # précédent — parce que c'est UNE seule propriété : *une grandeur qui sort
    # d'une tranche porte la mention de sa tranche jusque dans la réponse*.
    #
    # La troisième coupe du socle, `retrieval_max_rows`, n'est PAS ici, et c'est
    # mesuré : la synthèse la dit déjà, dans la même phrase que le tableau
    # (« (résultat tronqué par la limite de lignes) »). Un second message au
    # même endroit serait du bruit, et aucune campagne n'a montré de chiffre
    # muet sur ce chemin. Elle revient en revanche ci-dessous, au tour où elle
    # devient dangereuse : quand un tableau gardé est REMONTÉ pour qu'on y
    # compte, et que la phrase de la synthèse est loin derrière.
    _COUPE = (
        "Données tronquées : {quoi} coupée(s) à {plafond} lignes (réglage "
        "{reglage}) — tout agrégat qui porte sur elles (somme, moyenne, "
        "comptage) décrit cet échantillon, pas {entier}."
    )

    def _avis_de_troncature(self, tables: list[str]) -> str:
        """Ce qu'on dit d'une table matérialisée AMPUTÉE ("" si rien n'a été coupé).

        ``analysis_table_max_rows`` est l'endroit du code qui livre à l'analyse
        une donnée incomplète, et il le fait sans laisser de trace dans ce qu'il
        livre : un ``SELECT *`` coupé à 10 000 lignes donne un CSV parfaitement
        lisible où rien ne dit qu'il manque des lignes. Le code généré y calcule
        alors une somme, une moyenne ou un comptage en le prenant pour la table
        entière, et la réponse cite le chiffre sans réserve. C'est le seul
        chemin de ce nœud qui produit un résultat FAUX au lieu d'une erreur.

        Un seul message pour deux destinataires : le contexte du code généré,
        pour qu'il sache sur quoi il travaille, et la trace — d'où la réponse
        rendue le reprend (cf. ``_with_context_notices``), parce que la trace
        n'est pas dépliée par défaut.
        """
        if not tables:
            return ""
        return self._COUPE.format(
            quoi=", ".join(tables),
            plafond=self.settings.analysis_table_max_rows,
            reglage="DAA_ANALYSIS_TABLE_MAX_ROWS",
            entier="la table entière",
        )

    def _avis_de_tableaux_tronques(self, noms: list[str]) -> str:
        """Ce qu'on dit d'un tableau intermédiaire qui est lui-même une tranche."""
        if not noms:
            return ""
        return self._COUPE.format(
            quoi=", ".join(noms),
            plafond=self.settings.retrieval_max_rows,
            reglage="DAA_RETRIEVAL_MAX_ROWS",
            entier="le résultat entier de la requête qui les a produits",
        )

    @staticmethod
    def _objets_du_fil(state: OrchestratorState) -> list[WorkspaceArtifact]:
        """Tous les tableaux réinjectés du fil (le décor d'un rejeu de CODE)."""
        workspace = state.get("workspace")
        return list(workspace.injected) if workspace is not None else []

    def _objets_vises(self, state: OrchestratorState, plan: Plan) -> list[WorkspaceArtifact]:
        """Le tableau du fil que le PLAN désigne — vide quand il désigne la base.

        Le montage suit le plan, et c'est tout le correctif du §3.2 : un tour
        dont le plan dit `source=interventions` ne voit plus sous ``/data/`` le
        tableau du tour d'avant, donc ne peut plus répondre avec lui sans le
        dire. Un tour dont le plan dit `source=resultat_1` — ce que
        ``_regle_source_de_la_conversation`` laisse désormais passer — voit ce
        tableau-là, et lui seul.

        Le nom est comparé replié : le plan écrit ce que le modèle a écrit, et
        une majuscule ou un accent ne doivent pas décider d'un montage.
        """
        vise = introspection.replie(plan.source or "").strip()
        if not vise:
            return []
        return [
            a for a in self._objets_du_fil(state) if introspection.replie(a.name).strip() == vise
        ]

    @contextmanager
    def _decor_de_donnees(
        self, state: OrchestratorState, source, objets: list[WorkspaceArtifact]
    ) -> Iterator[tuple]:
        """Ce que le code d'analyse voit sous ``/data/``, et ce qu'on lui en dit.

        Cède ``(fichiers, contexte, avis)`` : les montages du bac à sable, la
        description qui les accompagne dans le prompt, et l'avis de troncature
        s'il y en a un. Le dossier temporaire où les tables SQL sont
        matérialisées ne vit que le temps du bloc.

        ``objets`` est la liste — souvent vide — des tableaux du fil que ce
        tour VISE. Elle est passée et non déduite, parce que les deux appelants
        ne visent pas la même chose : le nœud d'analyse vise ce que le plan
        désigne, le rejeu vise l'artefact qu'on lui a nommé. La déduire ici
        reviendrait à remonter tout ce qui traîne, c'est-à-dire au défaut que
        ``_mount_workspace`` corrige.

        Extrait de ``_analysis_node`` parce que le REJEU d'un code en a besoin
        exactement pareil : rejouer ``graphique_1`` sur un bac à sable où
        personne n'a monté les CSV, c'est rejouer un ``FileNotFoundError``. Deux
        copies de ce montage, ce seraient deux décors qui divergent — et un code
        qui marchait au tour 1 échouerait au tour 4 sans que rien ne le dise.
        """
        avis = ""
        with tempfile.TemporaryDirectory(prefix="daa-analysis-") as tmp:
            if isinstance(source, FileSource):
                data_files = {source.path: source.path.name}
                data_context = ""
            else:
                # source SQL : matérialise chaque table en CSV pour la sandbox
                tronquees: list[str] = []
                with closing(open_source(source)) as adapter:
                    schema = adapter.schema()
                    data_files = {}
                    for table in schema.tables:
                        result = adapter.run(
                            f"SELECT * FROM {table.name}",
                            max_rows=self.settings.analysis_table_max_rows,
                        )
                        if result.truncated:
                            tronquees.append(table.name)
                        csv_path = Path(tmp) / f"{table.name}.csv"
                        pd.DataFrame(result.rows, columns=result.columns).to_csv(
                            csv_path, index=False
                        )
                        data_files[csv_path] = f"{table.name}.csv"
                # La base est refermée AVANT l'analyse : le code généré tourne sur
                # les CSV matérialisés, il n'a plus rien à demander à la source, et
                # une analyse dure bien plus longtemps qu'une extraction.
                data_context = schema.to_prompt()
                avis = self._avis_de_troncature(tronquees)
                if avis:
                    data_context = f"{data_context}\n\n{avis}"
            # objets intermédiaires de la conversation : montés aussi pour que le
            # code généré puisse les relire (pd.read_csv('/data/resultat_1.csv'))
            data_context, tronques = self._mount_workspace(state, data_files, data_context, objets)
            # Les deux coupes se CUMULENT, et elles ne sont pas la même : une
            # table de la source amputée à la matérialisation, et un tableau
            # d'un tour précédent qui était déjà un extrait. Un tour peut porter
            # les deux, et l'utilisateur a besoin des deux.
            avis_des_tableaux = self._avis_de_tableaux_tronques(tronques)
            if avis_des_tableaux:
                data_context = f"{data_context}\n\n{avis_des_tableaux}"
                avis = " ".join(a for a in (avis, avis_des_tableaux) if a)
            yield data_files, data_context, avis

    def _analysis_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        workspace = state.get("workspace")
        source = self._resolve_source(plan, self._effective_catalog(state))
        with self._decor_de_donnees(state, source, self._objets_vises(state, plan)) as (
            data_files,
            data_context,
            avis,
        ):
            # ajustement d'un graphique précédent : on repart de son code
            previous_code = workspace.last_code_for(plan.source) if workspace is not None else None
            outcome = run_analysis(
                state["question"],
                data_files=data_files,
                data_context=data_context,
                previous_code=previous_code,
                model=self.model,
                settings=self.settings,
                sandbox=self._sandbox_override,
                # Ce que la source DÉCLARE vouloir dire. Le schéma monté en CSV
                # donne les colonnes et leurs types ; lui seul dit qu'un -1 est
                # l'absence de mesure et qu'un 0 est une mesure.
                dictionary=source.dictionary_text(),
            )
        # Une analyse en échec ne livre PAS ses figures : la tentative ratée laisse
        # des axes vides, et un graphique blanc affiché sous « l'analyse n'a pas
        # abouti » est pire que pas de graphique du tout — il donne à croire que
        # la donnée est vide, alors que c'est le code qui a planté.
        images = (
            [r for r in outcome.execution.results if r.mime == "image/png"]
            if outcome.succeeded
            else []
        )
        detail = (
            f"{outcome.attempts} essai(s), {len(images)} figure(s),"
            f" statut {outcome.execution.status}"
        )
        if not outcome.succeeded:
            # la cause vit ici, pas dans la réponse rendue à l'utilisateur
            detail += f" — {self._cause_lisible(outcome.execution.error)}"
        artefact = self._memoriser_le_code(workspace, outcome, state["question"], plan.source)
        if artefact is not None:
            detail += f" — retenu sous le nom {artefact.name}"
        # Deux amputations possibles sur ce nœud, et elles se cumulent : des
        # LIGNES coupées à la matérialisation, des SECTIONS coupées du
        # dictionnaire. L'une fait compter sur un échantillon, l'autre fait
        # compter sans la règle — et l'utilisateur a besoin des deux.
        mesures = {"truncated": False, "truncation": ""}
        self._ajoute_avis(mesures, avis)
        self._ajoute_avis(mesures, outcome.dictionary_notice)
        return {
            "analysis": outcome,
            "artifacts": images,
            "dire_du_dictionnaire": introspection.ce_qu_en_dit_le_dictionnaire(
                state["question"], source.dictionary_text(), source.name
            ),
            "trace": [self._step("analysis", detail, start, **mesures)],
        }

    @staticmethod
    def _memoriser_le_code(
        workspace: ConversationWorkspace | None,
        outcome: AnalysisResult,
        question: str,
        source: str | None,
    ) -> WorkspaceArtifact | None:
        """Retient le code d'une analyse RÉUSSIE comme artefact nommé du fil.

        C'est ce que le propriétaire demande à pouvoir rappeler : « le code qui
        génère une image doit pouvoir être rappelé pour être modifié ». L'image,
        elle, n'est pas persistée — elle part dans la réponse et y reste. Ce
        qu'on rejoue est le code ; repeindre un PNG ne rendrait rien.

        **Seulement si elle a abouti**, et c'est délibéré : un code qui n'a pas
        tourné n'est pas un artefact, c'est une tentative. Le rappeler
        n'offrirait que de rejouer un échec, et il encombrerait le catalogue de
        lignes qu'on ne peut pas désigner utilement.
        """
        if workspace is None or not outcome.succeeded:
            return None
        figures = len([r for r in outcome.execution.results if r.mime == "image/png"])
        return workspace.save_code(outcome.code, question, source=source or "", figures=figures)

    def _inference_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        outcome = run_inference(plan.dataset or "", plan.features, registry=self.registry)
        update: dict = {
            "inference": outcome,
            "trace": [self._step("inference", f"statut {outcome.status}", start)],
        }
        if outcome.status == "invalid":
            # multi-tours : on retient l'acquis pour fusionner le prochain message
            update["pending_out"] = PendingInference(
                dataset=plan.dataset or "", features=plan.features
            )
        return update

    def _fetch_predict_node(self, state: OrchestratorState) -> dict:
        """Chaînage ① -> ③ : récupère une ligne, la mappe sur les features, prédit.

        Le rapprochement ligne -> features n'est pas deviné : il est DÉCLARÉ par
        la source, dans le catalogue (cf. ``agents/inference/correspondance``).
        Une source qui ne le déclare pas est refusée ici, avant d'être ouverte —
        prédire sur une colonne choisie au hasard coûte plus cher que ne rien
        rendre.

        Et ce que la source DÉCLARE est relu contre son schéma, une fois la
        connexion ouverte et avant la moindre requête : un YAML peut nommer une
        colonne qui n'existe pas, et ça ne se voyait qu'au bout — SQL en erreur,
        correction au jugé de l'agent, feature absente du payload. La connexion est ouverte
        d'abord parce que c'est la source, et elle seule, qui dit ce qu'elle
        porte : c'est le seul aller qu'on paie pour le savoir, et il ne coûte
        aucun appel au modèle.
        """
        start = time.monotonic()
        plan = state["plan"]
        data_question = plan.data_question or state["question"]
        source = self._resolve_source(plan, self._effective_catalog(state))
        # Une source du CATALOGUE doit déclarer ; un tableau du tour précédent,
        # réinjecté par l'espace de travail, n'a aucun YAML où le faire — ses
        # colonnes sont celles que la requête précédente a nommées.
        du_catalogue = any(declaree.name == source.name for declaree in self.catalog.sources)
        try:
            correspondance = (
                Correspondance.declaree(
                    source=source.name,
                    dataset=plan.dataset or "",
                    declarations=source.features,
                )
                if du_catalogue
                else Correspondance.par_le_nom(source=source.name, dataset=plan.dataset or "")
            )
        except CorrespondanceIndisponible as exc:
            return {
                "error": str(exc),
                "trace": [self._step("fetch_predict", "correspondance non déclarée", start)],
            }
        with closing(open_source(source)) as adapter:
            try:
                # Seulement ce qui est DÉCLARÉ. Une correspondance par le nom
                # (un tableau du tour précédent) ne déclare rien : une feature
                # qu'aucune de ses colonnes ne porte est absente du payload et
                # réclamée par le schéma sous son nom, ce qui est le
                # comportement voulu — pas une faute de catalogue à corriger.
                if du_catalogue:
                    correspondance.confronter(adapter.schema())
            except CorrespondanceIndisponible as exc:
                return {
                    "error": str(exc),
                    "trace": [self._step("fetch_predict", "colonne déclarée absente", start)],
                }
            retrieval = run_retrieval(
                data_question + correspondance.consigne_sql(),
                adapter=adapter,
                model=self.model,
                settings=self.settings,
                # Même dictionnaire que pour une simple récupération : les
                # lignes qui alimentent une prédiction sont choisies par du SQL,
                # et un code mal filtré y fait le même dégât — en pire, puisque
                # le chiffre faux ressort en prédiction et non en tableau.
                dictionary=source.dictionary_text(),
            )
        if not retrieval.result or not retrieval.result.rows:
            return {
                "retrieval": retrieval,
                "error": "aucune ligne récupérée pour alimenter la prédiction",
                "trace": [self._step("fetch_predict", "récupération vide", start)],
            }
        payloads = [
            # ce que l'utilisateur a donné explicitement prime sur la ligne lue
            {**correspondance.payload(retrieval.result.columns, row), **plan.features}
            for row in retrieval.result.rows
        ]

        if len(payloads) == 1:
            inference = run_inference(plan.dataset or "", payloads[0], registry=self.registry)
            detail = f"ligne -> {sorted(payloads[0])} -> statut {inference.status}"
            update: dict = {
                "retrieval": retrieval,
                "inference": inference,
                "trace": [self._step("fetch_predict", detail, start)],
            }
            if inference.status == "invalid":
                update["pending_out"] = PendingInference(
                    dataset=plan.dataset or "", features=payloads[0]
                )
            return update

        # plusieurs lignes : prédiction en lot (vectorisée) + table de détail
        batch = run_batch_inference(plan.dataset or "", payloads, registry=self.registry)
        detail_table = self._batch_detail_artifact(retrieval.result, batch)
        detail = f"lot : {batch.valid_count}/{batch.total} lignes prédites"
        return {
            "retrieval": retrieval,
            "batch": batch,
            "artifacts": [detail_table],
            "trace": [self._step("fetch_predict", detail, start)],
        }

    @staticmethod
    def _batch_detail_artifact(result: QueryResult, batch: BatchInferenceOutcome) -> MimeOutput:
        """Table de détail du lot : les colonnes récupérées + prédiction par ligne."""
        columns = [*result.columns, "prediction", "confiance"]
        rows = []
        for source_row, row_result in zip(result.rows, batch.rows, strict=True):
            if row_result.prediction is not None:
                prediction = row_result.prediction
                label = prediction.label or str(prediction.value)
                confidence = (
                    max(prediction.probabilities.values()) if prediction.probabilities else None
                )
            else:
                fields = ", ".join(issue.field for issue in row_result.issues[:3])
                label = f"écartée ({fields})"
                confidence = None
            rows.append([*source_row, label, confidence])
        return _json_table(columns, rows, result.truncated)

    def _synthesize_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        inference = state.get("inference")

        if state.get("error"):
            answer = f"Je n'ai pas pu répondre : {state['error']}"
            mode = "erreur"
        elif state.get("clarification"):
            answer = state["clarification"]
            mode = "clarification"
        elif state.get("system") is not None:
            # Déterministe et rendue TELLE QUELLE : la faire reformuler par le
            # LLM rouvrirait la porte à une réponse qui n'est plus celle du
            # catalogue, et c'est tout ce qu'on cherche à empêcher ici.
            answer = state["system"]
            mode = "système (déterministe)"
        elif state.get("rappel") is not None:
            # Même raison : ce texte est soit ce qu'un outil a rendu, soit un
            # refus exact. Le repasser au LLM ne pourrait que l'abîmer.
            answer = state["rappel"]
            mode = "rappel d'artefact"
        elif inference is not None and inference.status == "invalid":
            answer = inference.reask or "Il manque des informations pour prédire."
            mode = "relance"
        elif inference is not None and inference.prediction is not None:
            answer = self._format_prediction(inference)
            mode = "modèle (déterministe)"
        elif state.get("batch") is not None:
            retrieval = state.get("retrieval")
            truncated = bool(retrieval and retrieval.result and retrieval.result.truncated)
            answer = self._format_batch(state["batch"], truncated=truncated)
            mode = "lot (déterministe)"
        elif state.get("retrieval") is not None and state.get("plan").capability == "query":
            answer, mode = self._synthesize_query(state["retrieval"])
        elif state.get("analysis") is not None:
            answer = self._synthesize_analysis(state)
            mode = "LLM"
        else:
            answer = "Je n'ai rien produit pour cette question."
            mode = "vide"
        # « Je travaille sur la source X » / « je passe sur X » : EN TÊTE de la
        # réponse, et non dans la trace. La trace n'est pas dépliée par défaut,
        # et ce qu'on veut éviter n'est pas de changer de source — c'est de
        # répondre sur d'autres données sans que ça se voie.
        # `avis_dabsence` en second : l'avis de source dit SUR QUOI on a
        # travaillé, l'aveu d'absence dit que ce qui suit est neuf — il doit
        # donc toucher la réponse qu'il qualifie.
        preambules = [state.get("avis_de_source", ""), state.get("avis_dabsence", "")]
        # Et le dictionnaire EN PIED, après la réponse qu'il qualifie : il ne
        # remplace rien, il ajoute ce que la source déclare sur les termes que
        # le message a nommés. Vide partout ailleurs — seuls les deux nœuds qui
        # regardent les données le renseignent (cf. `_pied_du_dictionnaire`).
        pied = self._pied_du_dictionnaire(state, answer)
        answer = "\n\n".join([p for p in (*preambules, answer, pied) if p])
        return {"answer": answer, "trace": [self._step("synthesize", mode, start)]}

    @staticmethod
    def _pied_du_dictionnaire(state: OrchestratorState, answer: str) -> str:
        """Ce que le dictionnaire dit des termes du message — "" s'il n'y a rien à dire.

        **La ceinture du chemin des données, et c'est le chemin qui n'en avait
        pas.** L'agent système compare ce qu'un outil lui a rendu à ce qu'il en
        formule, et sert les faits eux-mêmes quand la formulation ne les porte
        pas (``introspection.defaut_de_fondation``). Le planificateur, l'agent
        SQL et l'agent d'analyse n'ont jamais eu cet équivalent : le
        dictionnaire est injecté dans leur prompt (`agents/dictionnaire`), il
        est lu, et rien ne vérifie qu'il ressorte.

        **Mesuré le 2026-09-18, catalogue de démonstration, trois tirages.** Sur
        les six formulations de `mesure_question_de_sens`, CINQ ne voient jamais
        l'agent système — le fil brut porte « aucun outil appelé — passe au
        planificateur », 3 fois sur 3 pour chacune — et repartent en `query` ou
        en `analyze`. « le statut RET, il recouvre quoi au juste ? » recevait
        « 2 lignes retournées — voir le tableau ci-dessous » ; le tableau
        comptait les statuts sans dire ce que `RET` veut dire.

        **Il ne juge pas la réponse, et c'est délibéré.** Chercher si la
        formulation porte déjà le sens, ce serait mesurer nos tournures — la
        leçon de la dette `E`, payée une fois. Le texte de la source, lui, est
        vrai quoi qu'ait écrit le modèle, et le servir en plus ne peut pas le
        contredire. C'est le choix que le repli de la ceinture fait depuis le
        début : on préfère le texte de l'artefact à la confiance.

        **Et il ne parle qu'aux tours où il a quelque chose à dire** : une
        source sans dictionnaire ne déclenche rien — `titanic` et `iris` n'en
        déclarent aucun, et c'est le volet témoin de la mesure — et un message
        qui ne nomme aucune entrée du dictionnaire non plus.

        La réponse est passée pour une seule raison : ne pas resservir un texte
        que la réponse contient DÉJÀ au caractère près. C'est le cas du repli de
        l'agent système, qui sert l'extrait tel quel — le répéter dessous ferait
        deux fois le même paragraphe, et ce serait un défaut visible.
        """
        pied = state.get("dire_du_dictionnaire") or ""
        return "" if not pied or pied in answer else pied

    @staticmethod
    def _synthesize_query(retrieval: RetrievalResult) -> tuple[str, str]:
        """Réponse d'une requête ``query``.

        Le tableau des lignes est déjà affiché comme artefact ; recopier chaque
        ligne dans le texte fait doublon. On ne fait donc confiance à la
        synthèse (bavarde) du LLM que pour un agrégat d'une ligne. Dès qu'il y a
        plusieurs lignes, on renvoie une phrase brève et déterministe qui
        renvoie au tableau.

        **Zéro ligne est déterministe aussi** : c'est le cas où le LLM n'a rien
        à résumer et raconte le résultat qu'il attendait. Observé en vrai — un
        ``WHERE label LIKE '%First%'`` sur des libellés français ne ramène rien,
        et la synthèse affirmait « le résultat affiche les informations de
        toutes les passagères de première classe ». Un tableau vide doit se dire
        vide : c'est ce qui met l'utilisateur sur la piste du mauvais filtre.

        **Une réponse non fondée est refusée** : si le modèle n'a appelé AUCUN
        outil, il a répondu de mémoire. Observé en vrai sur « décris le dataset
        iris » — zéro requête, et une jolie prose sur « un ensemble classique de
        classification floristique » servie comme une lecture de la source. Sur
        des données privées, ce serait de l'invention pure.
        """
        if not retrieval.grounded:
            return (
                "Je n'ai pas interrogé la source pour cette question, je ne peux donc "
                "rien en affirmer. Reformule en précisant ce que tu veux en savoir "
                "(par exemple : « combien de lignes ? », « quelles colonnes ? »).",
                "réponse non fondée, écartée (déterministe)",
            )
        result = retrieval.result
        if result is not None and result.row_count == 0:
            return (
                "Aucune ligne ne correspond : la requête n'a rien retourné. "
                "Vérifie les critères (valeurs ou libellés attendus).",
                "aucun résultat (déterministe)",
            )
        if result is not None and result.row_count > 1:
            n = result.row_count
            phrase = f"{n} lignes retournées{Orchestrator._sur_quoi_classe(retrieval.sql)} — "
            phrase += "voir le tableau ci-dessous."
            if result.truncated:
                phrase += " (résultat tronqué par la limite de lignes)"
            return phrase, "résumé déterministe (multi-lignes)"
        return retrieval.summary, "résumé de la récupération"

    @staticmethod
    def _sur_quoi_classe(sql: str | None) -> str:
        """« , classées par `x` (ordre décroissant) » — vide si la requête ne classe rien.

        Ce que cette phrase répare : un palmarès rendu « 3 lignes retournées —
        voir le tableau ci-dessous » ne dit pas sur QUOI il classe. Deux
        réponses justes sur deux grandeurs différentes — le nombre de sessions
        d'une station, l'énergie qu'elle a délivrée — s'y lisaient à
        l'identique, et « qui charge le plus ? » n'a pas de réponse unique : les
        deux lectures sont légitimes, la question ne tranche pas. Ce qu'on doit
        à l'utilisateur n'est donc pas une grandeur en particulier, c'est de
        savoir laquelle il lit.

        La grandeur est lue sur le SQL exécuté
        (``classement.grandeur_du_classement``), jamais sur la question : c'est
        la même propriété vérifiable que la projection du ORDER BY, servie par
        l'autre bout. Une consigne de prompt qui aurait demandé au modèle de
        nommer sa grandeur aurait tenu sur les phrases qu'on lui aurait
        montrées — ce produit a déjà payé ce pari deux fois.

        Elle ne s'ajoute qu'au résumé DÉTERMINISTE multi-lignes. Le résumé d'un
        agrégat d'une ligne vient du modèle, qui nomme déjà ce qu'il a calculé,
        et il n'y a pas de classement à une ligne.
        """
        classement = grandeur_du_classement(sql or "")
        if classement is None:
            return ""
        sens = "décroissant" if classement.decroissant else "croissant"
        grandeurs = " puis ".join(f"`{g}`" for g in classement.grandeurs)
        return f", classées par {grandeurs} (ordre {sens})"

    @staticmethod
    def _cause_lisible(erreur: str | None) -> str:
        """La dernière ligne d'un traceback — « TypeError: ... » — sans les 40 autres.

        Un traceback porte sa cause en dernière ligne ; tout ce qui précède est
        la mécanique interne de la sandbox, illisible et anxiogène côté
        utilisateur, mais précieux dans la trace.
        """
        lignes = [ligne.strip() for ligne in (erreur or "").splitlines() if ligne.strip()]
        return lignes[-1] if lignes else "cause inconnue"

    def _synthesize_analysis(self, state: OrchestratorState) -> str:
        analysis = state["analysis"]
        if not analysis.succeeded:
            # JAMAIS le traceback : il était recraché tel quel à l'utilisateur —
            # 40 lignes de pyplot et de pandas pour dire « ça n'a pas marché ».
            # Le détail part dans la trace (cf. _analysis_node), pas dans la réponse.
            return (
                "L'analyse n'a pas abouti : le code produit n'a pas pu s'exécuter "
                f"après {analysis.attempts} tentative(s). Reformule ou précise ta "
                "demande (le détail technique est dans la trace)."
            )
        figures = len([r for r in analysis.execution.results if r.mime == "image/png"])
        context = (
            f"Question : {state['question']}\n\n"
            f"Sorties du code exécuté :\n{analysis.execution.stdout or '(pas de sortie texte)'}\n\n"
            f"Figures produites : {figures}"
        )
        agent = Agent(system_prompt=prompts.gabarit(prompts.SYNTHESIS))
        return agent.run_sync(context, model=self.model).output

    @staticmethod
    def _format_prediction(outcome: InferenceOutcome) -> str:
        prediction = outcome.prediction
        assert prediction is not None
        if prediction.task == "classification":
            label = prediction.label or str(prediction.value)
            parts = [f"Prédiction ({prediction.dataset}) : {label}"]
            if prediction.probabilities:
                best = max(prediction.probabilities.values())
                parts.append(f"(probabilité {best:.1%})")
                details = ", ".join(f"{k} : {v:.1%}" for k, v in prediction.probabilities.items())
                parts.append(f"— détail : {details}")
            return " ".join(parts)
        unit = f" {prediction.unit}" if prediction.unit else ""
        return f"Prédiction ({prediction.dataset}) : {prediction.value}{unit}."

    @staticmethod
    def _format_batch(batch: BatchInferenceOutcome, truncated: bool) -> str:
        if batch.valid_count == 0:
            first = batch.rows[0].issues[0].message if batch.rows and batch.rows[0].issues else ""
            return (
                f"Aucune des {batch.total} lignes récupérées n'a passé la validation"
                f"{f' ({first})' if first else ''} — pas de prédiction."
            )
        parts = [f"Prédiction ({batch.dataset}) sur {batch.valid_count} lignes"]
        if batch.invalid_count:
            parts.append(f"({batch.invalid_count} ligne(s) écartée(s) à la validation)")
        if batch.task == "classification":
            distribution = ", ".join(
                f"{label} : {count} ({count / batch.valid_count:.0%})"
                for label, count in batch.label_counts().items()
            )
            parts.append(f"— {distribution}.")
        else:
            values = batch.values()
            mean = sum(values) / len(values)
            unit = f" {batch.unit}" if batch.unit else ""
            parts.append(
                f"— moyenne {mean:.4g}{unit} (min {min(values):.4g}, max {max(values):.4g})."
            )
        parts.append("Détail ligne à ligne joint.")
        if truncated:
            parts.append("(Résultat tronqué par la limite de lignes.)")
        return " ".join(parts)

    @staticmethod
    def _step(node: str, detail: str, start: float, **mesures) -> TraceStep:
        return TraceStep(
            node=node,
            detail=detail,
            duration_ms=int((time.monotonic() - start) * 1000),
            **mesures,
        )
