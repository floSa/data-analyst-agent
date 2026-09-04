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
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent, UnexpectedModelBehavior
from pydantic_ai.models import Model

from data_analyst_agent import prompts
from data_analyst_agent.agents.analysis.agent import AnalysisResult, SandboxLike, run_analysis
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
from data_analyst_agent.agents.retrieval.sql import QueryResult
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model
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
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
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


class ChatAnswer(BaseModel):
    """Ce que l'API renvoie : texte + objets affichables + trace rejouable."""

    answer: str
    artifacts: list[MimeOutput] = Field(default_factory=list)
    plan: Plan | None = None
    error: str | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    # multi-tours : à repasser tel quel au prochain ask() de la conversation
    pending: PendingInference | None = None
    conversation_id: str | None = None  # renseigné par l'API


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
    ) -> ChatAnswer:
        """Répond à une question, dans la mémoire de ``conversation_id`` s'il y en a une.

        ``workspace_root`` est la racine sous laquelle vit cette conversation.
        L'API y passe ``ConversationStore.base_dir``, la racine de l'utilisateur
        de la session : la transcription et les tableaux intermédiaires d'un
        même fil doivent atterrir dans le MÊME dossier, et le seul moyen d'en
        être sûr est que les deux couches lisent la même valeur plutôt que de
        la recalculer chacune de son côté. À défaut, on retombe sur
        ``workspace_dir`` — le cas des appels directs, hors session.
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
        )

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
        builder.add_node("synthesize", self._guarded("synthesize", self._synthesize_node))

        builder.set_entry_point("plan")
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
    def _route(state: OrchestratorState) -> str:
        """Règle de routage : du code, pas du prompt (CADRAGE §4)."""
        if state.get("error") or state.get("plan") is None:
            return "error"
        if state.get("clarification"):
            return "clarify"
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
        """Aucune source choisie et le catalogue en contient plusieurs : on demande.

        Deviner serait répondre sur les mauvaises données sans le dire. Sur le
        catalogue DÉCLARÉ, et non l'effectif : un fil qui a mémorisé des
        tableaux ne doit pas se faire poser la question à chaque tour, alors que
        l'unique source déclarée reste le choix évident.

        Le repli sur l'unique source, lui, appartient au nœud de capacité
        (``_resolve_source``) : il n'y a rien à demander dans ce cas.
        """
        if (
            plan.capability in self._SOURCE_CAPABILITIES
            and not plan.source
            and len(ctx.catalogue_declare.sources) > 1
        ):
            names = ", ".join(s.name for s in ctx.catalogue_declare.sources)
            return f"Sur quelle source veux-tu travailler : {names} ?"
        return None

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

    def _plan_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        system_prompt, mesures = self._peser_le_prompt(state)
        plan = self._demander_un_plan(system_prompt, state, mesures)
        if plan is None:
            return self._clarify(
                Plan(capability="query"),
                "Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux "
                "faire — interroger une source (titanic, iris…), une analyse ou une "
                "visualisation, ou une prédiction — et sur quelles données ?",
                start,
                **mesures,
            )
        ctx = PlanContext(
            source_imposee=state.get("source_name"),
            pending=state.get("pending_in"),
            workspace=state.get("workspace"),
            catalogue_declare=self.catalog,
            catalogue_effectif=self._effective_catalog(state),
        )
        question = self._appliquer_les_regles(plan, ctx)
        if question is not None:
            return self._clarify(plan, question, start, **mesures)
        detail = f"{plan.capability}" + (f" sur {plan.source}" if plan.source else "")
        return {
            "plan": plan,
            "trace": [self._step("plan", detail, start, **mesures)],
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

    def _analysis_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        source = self._resolve_source(plan, self._effective_catalog(state))
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
            # ajustement d'un graphique précédent : on repart de son code
            workspace = state.get("workspace")
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
        return {
            "analysis": outcome,
            "artifacts": images,
            "trace": [self._step("analysis", detail, start, truncated=bool(avis), truncation=avis)],
        }

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

    @staticmethod
    def _expected_columns_hint(dataset: str) -> str:
        """Indique à l'agent SQL les noms de colonnes attendus par le schéma de features.

        Indispensable quand la feature ne porte pas le nom de la colonne en base
        (ex. `pclass` obtenu via une jointure sur `classes.level`) : le LLM doit
        aliaser sa requête sur les noms du schéma.
        """
        fields = ", ".join(get_schema(dataset).model_fields)
        return (
            "\nRenvoie la ou les lignes demandées (une par individu), avec des colonnes "
            f"nommées exactement : {fields} (utilise des alias SQL si nécessaire). "
            "Ajoute si disponible une colonne d'identification (id, nom)."
        )

    def _fetch_predict_node(self, state: OrchestratorState) -> dict:
        """Chaînage ① -> ③ : récupère une ligne, la mappe sur les features, prédit."""
        start = time.monotonic()
        plan = state["plan"]
        data_question = plan.data_question or state["question"]
        source = self._resolve_source(plan, self._effective_catalog(state))
        with closing(open_source(source)) as adapter:
            retrieval = run_retrieval(
                data_question + self._expected_columns_hint(plan.dataset or ""),
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
        # mapping insensible à la casse : les sources (CSV, Excel) gardent
        # souvent des en-têtes capitalisés ("Pclass", "Sex"...)
        schema_fields = set(get_schema(plan.dataset or "").model_fields)
        raw_rows = [
            {
                str(column).lower(): value
                for column, value in zip(retrieval.result.columns, row, strict=True)
            }
            for row in retrieval.result.rows
        ]
        payloads = [
            # ce que l'utilisateur a donné explicitement prime sur la ligne lue
            {**{k: v for k, v in raw.items() if k in schema_fields}, **plan.features}
            for raw in raw_rows
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
