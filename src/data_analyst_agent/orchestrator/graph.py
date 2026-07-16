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
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent, UnexpectedModelBehavior
from pydantic_ai.models import Model

from data_analyst_agent.agents.analysis.agent import AnalysisResult, SandboxLike, run_analysis
from data_analyst_agent.agents.inference.predict import (
    BatchInferenceOutcome,
    InferenceOutcome,
    run_batch_inference,
    run_inference,
)
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, describe_features, get_schema
from data_analyst_agent.agents.inference.validation import align_keys
from data_analyst_agent.agents.retrieval.agent import RetrievalResult, run_retrieval
from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    DuckDBSource,
    FileSource,
    load_catalog,
    open_source,
)
from data_analyst_agent.agents.retrieval.sql import QueryResult, SchemaInfo
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.plan import Plan, build_planner
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import MimeOutput

logger = logging.getLogger("data_analyst_agent.orchestrator")

SYNTHESIS_SYSTEM_PROMPT = """\
Tu rédiges la réponse finale pour l'utilisateur, en français, à partir du
travail effectué par le système (résultats fournis ci-après). Cite les valeurs
obtenues sans en inventer ; si une figure a été produite, mentionne-la
(« ci-joint »). Reste concis : 1 à 4 phrases.
"""


class TraceStep(BaseModel):
    node: str
    detail: str = ""
    duration_ms: int = 0


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


def _table_artifact(result: QueryResult) -> MimeOutput:
    payload = {"columns": result.columns, "rows": result.rows, "truncated": result.truncated}
    return MimeOutput(mime="application/json", data=json.dumps(payload, ensure_ascii=False))


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
        self.graph = self._build_graph()

    # -- API ----------------------------------------------------------------

    def ask(
        self,
        question: str,
        source: str | None = None,
        pending: PendingInference | None = None,
        conversation_id: str | None = None,
    ) -> ChatAnswer:
        # mémoire de conversation : les tableaux intermédiaires produits sont
        # persistés et réexposés aux tours suivants (cf. workspace.py)
        workspace = (
            ConversationWorkspace(self.settings.workspace_dir, conversation_id)
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
            answer=state.get("answer", ""),
            artifacts=state.get("artifacts", []),
            plan=state.get("plan"),
            error=state.get("error"),
            trace=state.get("trace", []),
            pending=state.get("pending_out"),
        )

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
                logger.exception("nœud %s : échec après %d ms", name, duration)
                return {
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": [TraceStep(node=name, detail=f"échec : {exc}", duration_ms=duration)],
                }
            duration = int((time.monotonic() - start) * 1000)
            details = "; ".join(step.detail for step in update.get("trace", []))
            logger.info("nœud %s : terminé en %d ms (%s)", name, duration, details)
            return update

        return wrapper

    # -- nœuds ----------------------------------------------------------------

    def _effective_catalog(self, state: OrchestratorState) -> Catalog:
        """Catalogue du tour : sources déclarées + objets intermédiaires de la conversation."""
        workspace = state.get("workspace")
        if workspace is None or not workspace.artifacts:
            return self.catalog
        return Catalog(sources=[*self.catalog.sources, *workspace.as_sources()])

    @staticmethod
    def _match_source_name(requested: str, catalog: Catalog) -> str | None:
        """Résout un nom de source, tolérant à la décoration du LLM.

        Exact d'abord ; sinon, si un (et un seul) nom de source connu apparaît
        comme mot dans la chaîne demandée (« maxizoo (duckdb) » -> maxizoo),
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

    def _clarify(self, plan: Plan, question: str, start: float) -> dict:
        """Court-circuite vers une question de clarification (réponse propre, pas d'erreur)."""
        return {
            "plan": plan,
            "clarification": question,
            "trace": [self._step("plan", "clarification demandée", start)],
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

    def _plan_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        pending = state.get("pending_in")
        # les objets intermédiaires de la conversation sont décrits en plus des
        # sources déclarées, pour que « prédis ces lignes » les désigne
        sources_description = self.catalog.describe()
        workspace = state.get("workspace")
        workspace_description = workspace.describe() if workspace is not None else None
        if workspace_description:
            sources_description = f"{sources_description}\n\n{workspace_description}"
        planner = build_planner(
            sources_description,
            self._datasets_description(),
            pending_context=self._pending_context(pending),
            history_context=(workspace.describe_context() if workspace is not None else None),
        )
        try:
            plan = planner.run_sync(state["question"], model=self.model).output
        except UnexpectedModelBehavior:
            # le LLM n'a pas su produire un Plan structuré (retries épuisés). Si on
            # a un tour précédent, on suppose un AJUSTEMENT et on reprend sa
            # capacité/source ; sinon on demande de préciser (jamais de crash).
            fallback = self._fallback_plan(workspace)
            if fallback is None:
                # Les sources sont NOMMÉES depuis le catalogue, jamais codées en
                # dur : un message qui citerait des sources disparues enverrait
                # l'utilisateur vers des données qui n'existent plus.
                connues = ", ".join(s.name for s in self.catalog.sources)
                perimetre = f" ({connues})" if connues else ""
                return self._clarify(
                    Plan(capability="query"),
                    "Je n'ai pas bien compris ta demande. Peux-tu préciser ce que tu veux "
                    f"faire — interroger une source{perimetre}, une analyse ou une "
                    "visualisation, ou une prédiction — et sur quelles données ?",
                    start,
                )
            plan = fallback
        if state.get("source_name"):
            plan.source = state["source_name"]
        if plan.capability == "fetch_then_predict" and not self._effective_catalog(state).sources:
            # aucune source à interroger (ni déclarée, ni en mémoire de
            # conversation) : on dégrade en predict, la validation relancera
            # l'utilisateur sur ce qui manque (jamais de crash)
            plan.capability = "predict"
        if pending is not None and plan.capability == "predict":
            # fusion multi-tours : l'acquis d'abord, le nouveau message prime
            plan.dataset = plan.dataset or pending.dataset
            if plan.dataset == pending.dataset:
                plan.features = {**pending.features, **plan.features}
        elif plan.capability == "predict" and workspace is not None:
            # ajustement d'une prédiction déjà ABOUTIE (« et si elle était en 3e
            # classe ? ») : le pending est vidé dès qu'une prédiction réussit, donc
            # sans cet acquis le tour repartait de zéro et redemandait des features
            # déjà données. Même règle que ci-dessus : le nouveau message prime, et
            # seul le MÊME dataset est repris (une digression n'hérite de rien).
            acquis = workspace.last_features_for(plan.dataset)
            if acquis:
                plan.features = {**acquis, **plan.features}
        # source désignée mais introuvable : le LLM décore parfois le nom (ex.
        # « maxizoo (duckdb) » recopié depuis la description au lieu de
        # « maxizoo ») -> on normalise ; si vraiment inconnue, on demande plutôt
        # que de laisser fuir un KeyError brut.
        if plan.source:
            resolved = self._match_source_name(plan.source, self._effective_catalog(state))
            if resolved is None:
                names = (
                    ", ".join(s.name for s in self._effective_catalog(state).sources) or "(aucune)"
                )
                return self._clarify(
                    plan,
                    f"La source « {plan.source} » est introuvable. Sur quelle source "
                    f"veux-tu travailler : {names} ?",
                    start,
                )
            plan.source = resolved
        # ambiguïté de source : la capacité interroge une source, aucune n'est
        # choisie et le catalogue en contient plusieurs -> on demande à
        # l'utilisateur de préciser plutôt que de deviner ou de planter.
        if (
            plan.capability in self._SOURCE_CAPABILITIES
            and not plan.source
            and len(self.catalog.sources) > 1
        ):
            names = ", ".join(s.name for s in self.catalog.sources)
            return self._clarify(plan, f"Sur quelle source veux-tu travailler : {names} ?", start)
        # modèle de prédiction manquant : repli auto s'il n'y en a qu'un, sinon
        # on demande lequel plutôt que de propager un KeyError ('' -> inconnu).
        if plan.capability in self._PREDICT_CAPABILITIES and not plan.dataset:
            datasets = self.registry.datasets
            if len(datasets) == 1:
                plan.dataset = datasets[0]
            elif len(datasets) > 1:
                names = ", ".join(datasets)
                return self._clarify(plan, f"Sur quel modèle veux-tu prédire : {names} ?", start)
        # « prédis ces lignes » : le LLM route parfois en 'predict' sans features
        # au lieu de fetch_then_predict. Si le dernier tableau mémorisé fournit
        # exactement les features du modèle, on chaîne dessus plutôt que de
        # redemander des valeurs déjà affichées.
        workspace = state.get("workspace")
        if (
            plan.capability == "predict"
            and not plan.features
            and plan.dataset in SCHEMAS
            and workspace is not None
            and workspace.artifacts
        ):
            latest = workspace.artifacts[-1]
            needed = set(get_schema(plan.dataset).model_fields)
            if needed <= {c.lower() for c in latest.columns}:
                plan.capability = "fetch_then_predict"
                plan.source = latest.name
                plan.data_question = plan.data_question or f"toutes les lignes de {latest.name}"
        detail = f"{plan.capability}" + (f" sur {plan.source}" if plan.source else "")
        return {
            "plan": plan,
            "trace": [self._step("plan", detail, start)],
        }

    def _retrieval_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        source = self._resolve_source(plan, self._effective_catalog(state))
        adapter = open_source(source)
        outcome = run_retrieval(
            state["question"],
            adapter=adapter,
            model=self.model,
            settings=self.settings,
            dictionary=source.dictionary_text(),
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
    def _duckdb_context(source: DuckDBSource, schema: SchemaInfo) -> str:
        """Décrit au code généré la base montée dans la sandbox.

        Une base DuckDB n'est pas matérialisée en CSV : elle est montée telle
        quelle et requêtée sur place. Ce n'est pas qu'une économie — c'est ce
        qui rend l'analyse JUSTE. Materialiser `sales_daily` (1,4 M de lignes)
        obligerait à la couper à analysis_table_max_rows, et tout agrégat
        porterait alors sur 0,7 % des données sans que rien ne le signale.
        DuckDB étant dans l'image de la sandbox, autant lui laisser le SQL.
        """
        return (
            f"La source est une base DuckDB montée en lecture seule sur "
            f"/data/{source.path.name}. Elle est COMPLÈTE (aucune troncature) : "
            f"interroge-la en SQL plutôt que de tout charger en mémoire.\n\n"
            f"    import duckdb\n"
            f"    con = duckdb.connect('/data/{source.path.name}', read_only=True)\n"
            f'    df = con.execute("SELECT ... FROM ...").df()\n\n'
            f"Schéma :\n{schema.to_prompt()}"
        )

    @staticmethod
    def _mount_workspace(
        state: OrchestratorState, data_files: dict[Path, str], data_context: str
    ) -> str:
        """Ajoute les CSV mémorisés aux fichiers montés et les décrit au code généré."""
        workspace = state.get("workspace")
        if workspace is None or not workspace.artifacts:
            return data_context
        for host_path, name in workspace.sandbox_files().items():
            data_files.setdefault(host_path, name)
        lines = [
            f"- /data/{a.file} ({a.row_count} lignes ; colonnes : {', '.join(a.columns)})"
            for a in workspace.artifacts
        ]
        extra = "Objets intermédiaires de la conversation (réutilisables) :\n" + "\n".join(lines)
        return f"{data_context}\n\n{extra}" if data_context else extra

    def _analysis_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        source = self._resolve_source(plan, self._effective_catalog(state))
        with tempfile.TemporaryDirectory(prefix="daa-analysis-") as tmp:
            if isinstance(source, FileSource):
                data_files = {source.path: source.path.name}
                data_context = ""
            elif isinstance(source, DuckDBSource):
                data_files = {source.path: source.path.name}
                data_context = self._duckdb_context(source, open_source(source).schema())
            else:
                # source SQL : matérialise chaque table en CSV pour la sandbox
                adapter = open_source(source)
                schema = adapter.schema()
                data_files = {}
                tronquees = []
                for table in schema.tables:
                    result = adapter.run(
                        f"SELECT * FROM {table.name}",
                        max_rows=self.settings.analysis_table_max_rows,
                    )
                    if result.truncated:
                        tronquees.append(f"{table.name} ({result.row_count} lignes seulement)")
                    csv_path = Path(tmp) / f"{table.name}.csv"
                    pd.DataFrame(result.rows, columns=result.columns).to_csv(csv_path, index=False)
                    data_files[csv_path] = f"{table.name}.csv"
                data_context = schema.to_prompt()
                if tronquees:
                    # Une table coupée à analysis_table_max_rows produit des
                    # agrégats FAUX qu'aucun garde-fou ne rattrape : « le CA
                    # total » calculé sur 10 000 des 1,4 M de lignes est
                    # crédible, précis au centime, et hors de trois ordres de
                    # grandeur. Le taire serait mentir ; on l'annonce au code
                    # généré pour qu'il le dise à son tour.
                    data_context += (
                        "\n\nATTENTION — extraits TRONQUÉS : "
                        + ", ".join(tronquees)
                        + ". Tout total, moyenne ou comptage porte sur CET EXTRAIT, pas sur la "
                        "table entière. Dis-le explicitement dans ta sortie ; n'annonce jamais "
                        "un agrégat comme s'il valait pour toute la source."
                    )
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
            "trace": [self._step("analysis", detail, start)],
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
        adapter = open_source(self._resolve_source(plan, self._effective_catalog(state)))
        data_question = plan.data_question or state["question"]
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
        # Mapping tolérant à la casse ET aux séparateurs : les sources (CSV,
        # Excel) gardent souvent des en-têtes capitalisés ("StoreType",
        # "Base Price"...). C'est `align_keys` qui sait les ramener aux noms du
        # schéma ; un simple .lower() ne suffit que tant que les features
        # tiennent en un seul mot ("Sex" -> sex), ce qui n'est pas le cas ici
        # ("StoreType" -> storetype, qui ne ressemble plus à rien).
        schema = get_schema(plan.dataset or "")
        schema_fields = set(schema.model_fields)
        raw_rows = [
            dict(zip(retrieval.result.columns, row, strict=True)) for row in retrieval.result.rows
        ]
        payloads = [
            # ce que l'utilisateur a donné explicitement prime sur la ligne lue
            {
                **{k: v for k, v in align_keys(schema, raw).items() if k in schema_fields},
                **plan.features,
            }
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
        payload = {"columns": columns, "rows": rows, "truncated": result.truncated}
        return MimeOutput(mime="application/json", data=json.dumps(payload, ensure_ascii=False))

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
        agent = Agent(system_prompt=SYNTHESIS_SYSTEM_PROMPT)
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
    def _step(node: str, detail: str, start: float) -> TraceStep:
        return TraceStep(
            node=node, detail=detail, duration_ms=int((time.monotonic() - start) * 1000)
        )
