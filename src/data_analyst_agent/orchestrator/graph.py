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
from data_analyst_agent.agents.retrieval.faits import ReglagesDuReleve, RelevesDuCatalogue
from data_analyst_agent.agents.retrieval.sql import QueryResult
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model
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
    defaut_de_formulation,
    run_rappel,
)
from data_analyst_agent.orchestrator.systeme import run_systeme
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
    source_in: str | None  # la source liée au fil, telle que reçue
    source_out: str | None  # ce que le fil retient de ce tour
    avis_de_source: str  # « je travaille sur X », mis en tête de la réponse
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
        self.model = model or build_model(self.settings)
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

    def ask(
        self,
        question: str,
        source: str | None = None,
        pending: PendingInference | None = None,
        conversation_id: str | None = None,
        workspace_root: Path | None = None,
        source_de_travail: str | None = None,
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
            pending=state.get("pending_out"),
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
                    # Là où Ollama tronque en silence, vLLM rejette. Sans ce
                    # branchement, le refus arriverait à l'utilisateur sous la
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

        Un ``plan.source`` qui désigne un **objet du fil** (un tableau
        intermédiaire) n'est pas effacé : ce n'est pas un choix entre sources
        ambiguës, c'est un résultat que la conversation vient de produire.

        Après ``_regle_degrader_faute_de_source``, qui peut retirer à ce tour la
        capacité même qui réclame une source.
        """
        if ctx.source_imposee or ctx.source_de_travail is None:
            return None
        if plan.capability not in self._SOURCE_CAPABILITIES:
            return None
        nommee = introspection.source_nommee(ctx.question, ctx.catalogue_declare)
        declarees = [s.name for s in ctx.catalogue_declare.sources]
        if nommee:
            plan.source = nommee
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
        """
        if not plan.source:
            return None
        resolved = self._match_source_name(plan.source, ctx.catalogue_effectif)
        if resolved is None:
            names = ", ".join(s.name for s in ctx.catalogue_effectif.sources) or "(aucune)"
            return (
                f"La source « {plan.source} » est introuvable. Sur quelle source "
                f"veux-tu travailler : {names} ?"
            )
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

        Déterministe, et c'est assumé : il n'y a rien à formuler. La phrase
        accuse réception d'un nom que l'utilisateur vient d'écrire, en y
        ajoutant ce que le catalogue en dit ; un aller-retour LLM pour la
        reformuler ne changerait pas un fait et ferait attendre l'utilisateur
        avant sa première vraie question.

        Elle dit la source **quittée** s'il y en avait une : un choix qui en
        remplace un autre doit se voir, exactement comme une bascule au milieu
        d'une question (``_lier_la_source``).
        """
        source = self.catalog.get(nom)
        description = source.description.strip() or "sans description"
        quittee = f" (on travaillait sur `{precedente}`)" if precedente else ""
        # Ce qu'on a LU dedans, à l'instant où elle devient la source de
        # travail : c'est le moment où savoir qu'elle pèse 300 lignes et ne
        # couvre aucune date change ce qu'on va lui demander.
        releve = self.releves.de(nom)
        faits = f"\n\n{releve.en_clair()}" if releve is not None and releve.en_clair() else ""
        return (
            f"Entendu : on travaille sur **{nom}** ({source.type}){quittee} — "
            f"{description}{faits}\n\n"
            "Je garde cette source pour la suite de la conversation. Nomme-en une "
            "autre à tout moment et je basculerai dessus.\n\n"
            "Que veux-tu savoir ?"
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
                self._repli_du_planificateur(state),
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
        retenue, avis = self._lier_la_source(plan, ctx)
        if question is not None:
            return self._clarify(plan, question, start, **mesures) | {"source_out": retenue}
        detail = f"{plan.capability}" + (f" sur {plan.source}" if plan.source else "")
        return {
            "plan": plan,
            "source_out": retenue,
            "avis_de_source": avis,
            "trace": [self._step("plan", detail, start, **mesures)],
        }

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
        - la formulation invente un nom, ou en omet un rendu par l'outil : les
          **faits** sont servis tels quels (``defaut_de_fondation``) ;
        - l'agent système lui-même n'a pas abouti : le tour repart au
          planificateur au lieu d'échouer.

        Ce dernier cas est délibérément **fail-open**, et seulement pour ce que
        le MODÈLE rate (sortie invalide, plafond d'allers-retours atteint) : un
        planificateur qui aurait su répondre ne doit pas être privé de la
        question par un incident de ce nœud-ci. Ce qu'un OUTIL rate — une
        source injoignable, un catalogue illisible — n'est pas rattrapé : c'est
        un vrai défaut de configuration, il remonte au garde-fou et il est dit.
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
        outils = ", ".join(resultat.outils_appeles)
        defaut = introspection.defaut_de_fondation(resultat.reponse, resultat.faits)
        if defaut:
            return {
                "system": resultat.faits,
                "trace": [
                    self._step("system", f"{outils} — faits servis tels quels ({defaut})", start)
                ],
            }
        return {
            "system": resultat.reponse,
            "trace": [self._step("system", f"{outils} — formulé par le modèle", start)],
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
        """
        if state.get("pending_in") is not None:
            return "prédiction en attente de features"
        return ""

    def _rejouer_un_code(
        self, state: OrchestratorState, artefact: WorkspaceArtifact, modification: str
    ) -> AnalysisResult:
        """Reprend le code d'un artefact, y applique la modification, le réexécute.

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
        with self._decor_de_donnees(state, source) as (data_files, data_context, _avis):
            return run_analysis(
                modification,
                data_files=data_files,
                data_context=data_context,
                previous_code=self._lire_le_code(state, artefact),
                model=self.model,
                settings=self.settings,
                sandbox=self._sandbox_override,
            )

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
        """
        start = time.monotonic()
        workspace = state.get("workspace")
        if workspace is None or not workspace.catalogue():
            # AUCUNE trace, et c'est délibéré : il n'y a pas d'artefact dans ce
            # fil, donc rien à décider, rien à appeler et rien à observer. Une
            # ligne « rien à rappeler » sur chaque tour de chaque conversation
            # qui n'a rien produit serait du bruit dans une trace qu'on déplie
            # pour comprendre ce qui s'est passé. Un fil qui A des artefacts, lui,
            # laisse toujours une ligne — y compris quand le modèle décline.
            return {}
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
            return {
                "trace": [
                    self._step("rappel", "aucun outil appelé — passe au planificateur", start)
                ]
            }
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
        return {
            "plan": Plan(capability="analyze", source=source or None),
            "analysis": resultat,
            "artifacts": images,
            "trace": [self._step("rappel", detail, start)],
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
                state["question"], adapter=adapter, model=self.model, settings=self.settings
            )
        artifacts = [_table_artifact(outcome.result)] if outcome.result else []
        # mémorise le tableau produit pour le réutiliser aux tours suivants
        self._memorize(state, outcome.result)
        detail = outcome.sql or f"{len(outcome.executed)} requête(s), aucune n'a abouti"
        return {
            "retrieval": outcome,
            "artifacts": artifacts,
            "trace": [self._step("retrieval", detail, start)],
        }

    @staticmethod
    def _memorize(state: OrchestratorState, result: QueryResult | None) -> None:
        """Persiste un tableau non vide dans l'espace de travail de la conversation."""
        workspace = state.get("workspace")
        if workspace is not None and result is not None and result.rows:
            workspace.save_table(result.columns, result.rows, state["question"])

    @staticmethod
    def _mount_workspace(
        state: OrchestratorState, data_files: dict[Path, str], data_context: str
    ) -> str:
        """Ajoute les CSV mémorisés aux fichiers montés et les décrit au code généré."""
        workspace = state.get("workspace")
        if workspace is None or not workspace.injected:
            return data_context
        for host_path, name in workspace.sandbox_files().items():
            data_files.setdefault(host_path, name)
        lines = [
            f"- /data/{a.file} ({a.row_count} lignes ; colonnes : {', '.join(a.columns)})"
            for a in workspace.injected
        ]
        extra = "Objets intermédiaires de la conversation (réutilisables) :\n" + "\n".join(lines)
        return f"{data_context}\n\n{extra}" if data_context else extra

    def _avis_de_troncature(self, tables: list[str]) -> str:
        """Ce qu'on dit d'une table matérialisée AMPUTÉE ("" si rien n'a été coupé).

        ``analysis_table_max_rows`` est le seul endroit du code qui livre à
        l'analyse une donnée incomplète, et il le fait sans laisser de trace
        dans ce qu'il livre : un ``SELECT *`` coupé à 10 000 lignes donne un CSV
        parfaitement lisible où rien ne dit qu'il manque des lignes. Le code
        généré y calcule alors une somme, une moyenne ou un comptage en le
        prenant pour la table entière, et la réponse cite le chiffre sans
        réserve. C'est le seul chemin de ce nœud qui produit un résultat FAUX au
        lieu d'une erreur.

        Un seul message pour deux destinataires : le contexte du code généré,
        pour qu'il sache sur quoi il travaille, et la trace — d'où la réponse
        rendue le reprend (cf. ``_with_context_notices``), parce que la trace
        n'est pas dépliée par défaut.
        """
        if not tables:
            return ""
        return (
            f"Données tronquées : {', '.join(tables)} coupée(s) à "
            f"{self.settings.analysis_table_max_rows} lignes (réglage "
            "DAA_ANALYSIS_TABLE_MAX_ROWS) — tout agrégat qui porte sur elles "
            "(somme, moyenne, comptage) décrit cet échantillon, pas la table entière."
        )

    @contextmanager
    def _decor_de_donnees(self, state: OrchestratorState, source) -> Iterator[tuple]:
        """Ce que le code d'analyse voit sous ``/data/``, et ce qu'on lui en dit.

        Cède ``(fichiers, contexte, avis)`` : les montages du bac à sable, la
        description qui les accompagne dans le prompt, et l'avis de troncature
        s'il y en a un. Le dossier temporaire où les tables SQL sont
        matérialisées ne vit que le temps du bloc.

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
            data_context = self._mount_workspace(state, data_files, data_context)
            yield data_files, data_context, avis

    def _analysis_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        workspace = state.get("workspace")
        source = self._resolve_source(plan, self._effective_catalog(state))
        with self._decor_de_donnees(state, source) as (data_files, data_context, avis):
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
        return {
            "analysis": outcome,
            "artifacts": images,
            "trace": [self._step("analysis", detail, start, truncated=bool(avis), truncation=avis)],
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
            retrieval = run_retrieval(
                data_question + correspondance.consigne_sql(),
                adapter=adapter,
                model=self.model,
                settings=self.settings,
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
        avis = state.get("avis_de_source", "")
        if avis:
            answer = f"{avis}\n\n{answer}" if answer else avis
        return {"answer": answer, "trace": [self._step("synthesize", mode, start)]}

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
            phrase = f"{n} lignes retournées — voir le tableau ci-dessous."
            if result.truncated:
                phrase += " (résultat tronqué par la limite de lignes)"
            return phrase, "résumé déterministe (multi-lignes)"
        return retrieval.summary, "résumé de la récupération"

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
