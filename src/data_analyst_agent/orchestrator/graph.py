"""Graphe d'orchestration explicite (LangGraph) : plan -> route -> capacité -> synthèse.

Le pipeline s'inspecte et se trace (state typé, TraceStep par nœud) ; la règle
de routage est du code ; un nœud qui échoue renseigne `error` au lieu de faire
tomber le graphe (CADRAGE §4).
"""

from __future__ import annotations

import json
import logging
import operator
import tempfile
import time
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from data_analyst_agent.agents.analysis.agent import AnalysisResult, SandboxLike, run_analysis
from data_analyst_agent.agents.inference.predict import InferenceOutcome, run_inference
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, get_schema
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
from data_analyst_agent.orchestrator.plan import Plan, build_planner
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


class OrchestratorState(TypedDict, total=False):
    question: str
    source_name: str | None
    plan: Plan | None
    retrieval: RetrievalResult | None
    analysis: AnalysisResult | None
    inference: InferenceOutcome | None
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

    def ask(self, question: str, source: str | None = None) -> ChatAnswer:
        state: OrchestratorState = self.graph.invoke(
            {"question": question, "source_name": source, "artifacts": [], "trace": []}
        )
        return ChatAnswer(
            answer=state.get("answer", ""),
            artifacts=state.get("artifacts", []),
            plan=state.get("plan"),
            error=state.get("error"),
            trace=state.get("trace", []),
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
                "error": "synthesize",
            },
        )
        for node in ("retrieval", "analysis", "inference", "fetch_predict"):
            builder.add_edge(node, "synthesize")
        builder.add_edge("synthesize", END)
        return builder.compile()

    @staticmethod
    def _route(state: OrchestratorState) -> str:
        """Règle de routage : du code, pas du prompt (CADRAGE §4)."""
        if state.get("error") or state.get("plan") is None:
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

    def _resolve_source(self, plan: Plan):
        """La source du plan si elle existe ; sinon repli sans ambiguïté.

        Le LLM omet parfois la source quand la demande semble se suffire
        (constaté en live) : si le catalogue n'en contient qu'une, on la prend ;
        sinon on échoue avec la liste des choix — jamais de devinette.
        """
        if plan.source:
            return self.catalog.get(plan.source)
        if len(self.catalog.sources) == 1:
            only = self.catalog.sources[0]
            plan.source = only.name  # trace et réponse cohérentes
            return only
        names = ", ".join(s.name for s in self.catalog.sources) or "(catalogue vide)"
        raise KeyError(
            f"aucune source choisie et le catalogue en contient plusieurs — précise parmi : {names}"
        )

    def _datasets_description(self) -> str:
        lines = []
        for dataset in self.registry.datasets:
            entry = self.registry.get(dataset)
            fields = ", ".join(SCHEMAS[dataset].model_fields) if dataset in SCHEMAS else "?"
            lines.append(f"- {dataset} ({entry.task}) : features attendues : {fields}")
        return "\n".join(lines) or "(aucun modèle)"

    def _plan_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        planner = build_planner(self.catalog.describe(), self._datasets_description())
        plan = planner.run_sync(state["question"], model=self.model).output
        if state.get("source_name"):
            plan.source = state["source_name"]
        detail = f"{plan.capability}" + (f" sur {plan.source}" if plan.source else "")
        return {
            "plan": plan,
            "trace": [self._step("plan", detail, start)],
        }

    def _retrieval_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        adapter = open_source(self._resolve_source(plan))
        outcome = run_retrieval(
            state["question"], adapter=adapter, model=self.model, settings=self.settings
        )
        artifacts = [_table_artifact(outcome.result)] if outcome.result else []
        detail = outcome.sql or f"{len(outcome.executed)} requête(s), aucune n'a abouti"
        return {
            "retrieval": outcome,
            "artifacts": artifacts,
            "trace": [self._step("retrieval", detail, start)],
        }

    def _analysis_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        source = self._resolve_source(plan)
        with tempfile.TemporaryDirectory(prefix="daa-analysis-") as tmp:
            if isinstance(source, FileSource):
                data_files = {source.path: source.path.name}
                data_context = ""
            else:
                # source SQL : matérialise chaque table en CSV pour la sandbox
                adapter = open_source(source)
                schema = adapter.schema()
                data_files = {}
                for table in schema.tables:
                    result = adapter.run(
                        f"SELECT * FROM {table.name}",
                        max_rows=self.settings.analysis_table_max_rows,
                    )
                    csv_path = Path(tmp) / f"{table.name}.csv"
                    pd.DataFrame(result.rows, columns=result.columns).to_csv(csv_path, index=False)
                    data_files[csv_path] = f"{table.name}.csv"
                data_context = schema.to_prompt()
            outcome = run_analysis(
                state["question"],
                data_files=data_files,
                data_context=data_context,
                model=self.model,
                settings=self.settings,
                sandbox=self._sandbox_override,
            )
        images = [r for r in outcome.execution.results if r.mime == "image/png"]
        detail = (
            f"{outcome.attempts} essai(s), {len(images)} figure(s),"
            f" statut {outcome.execution.status}"
        )
        return {
            "analysis": outcome,
            "artifacts": images,
            "trace": [self._step("analysis", detail, start)],
        }

    def _inference_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        plan = state["plan"]
        outcome = run_inference(plan.dataset or "", plan.features, registry=self.registry)
        return {
            "inference": outcome,
            "trace": [self._step("inference", f"statut {outcome.status}", start)],
        }

    @staticmethod
    def _expected_columns_hint(dataset: str) -> str:
        """Indique à l'agent SQL les noms de colonnes attendus par le schéma de features.

        Indispensable quand la feature ne porte pas le nom de la colonne en base
        (ex. `pclass` obtenu via une jointure sur `classes.level`) : le LLM doit
        aliaser sa requête sur les noms du schéma.
        """
        fields = ", ".join(get_schema(dataset).model_fields)
        return (
            "\nRenvoie UNE seule ligne, avec des colonnes nommées exactement : "
            f"{fields} (utilise des alias SQL si nécessaire)."
        )

    def _fetch_predict_node(self, state: OrchestratorState) -> dict:
        """Chaînage ① -> ③ : récupère une ligne, la mappe sur les features, prédit."""
        start = time.monotonic()
        plan = state["plan"]
        adapter = open_source(self._resolve_source(plan))
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
        # mapping insensible à la casse : les sources (CSV, Excel) gardent
        # souvent des en-têtes capitalisés ("Pclass", "Sex"...)
        row = {
            str(column).lower(): value
            for column, value in zip(
                retrieval.result.columns, retrieval.result.rows[0], strict=True
            )
        }
        schema_fields = set(get_schema(plan.dataset or "").model_fields)
        features = {k: v for k, v in row.items() if k in schema_fields}
        features.update(plan.features)  # ce que l'utilisateur a donné explicitement prime
        inference = run_inference(plan.dataset or "", features, registry=self.registry)
        detail = f"ligne -> {sorted(features)} -> statut {inference.status}"
        return {
            "retrieval": retrieval,
            "inference": inference,
            "trace": [self._step("fetch_predict", detail, start)],
        }

    def _synthesize_node(self, state: OrchestratorState) -> dict:
        start = time.monotonic()
        inference = state.get("inference")

        if state.get("error"):
            answer = f"Je n'ai pas pu répondre : {state['error']}"
            mode = "erreur"
        elif inference is not None and inference.status == "invalid":
            answer = inference.reask or "Il manque des informations pour prédire."
            mode = "relance"
        elif inference is not None and inference.prediction is not None:
            answer = self._format_prediction(inference)
            mode = "modèle (déterministe)"
        elif state.get("retrieval") is not None and state.get("plan").capability == "query":
            answer = state["retrieval"].summary
            mode = "résumé de la récupération"
        elif state.get("analysis") is not None:
            answer = self._synthesize_analysis(state)
            mode = "LLM"
        else:
            answer = "Je n'ai rien produit pour cette question."
            mode = "vide"
        return {"answer": answer, "trace": [self._step("synthesize", mode, start)]}

    def _synthesize_analysis(self, state: OrchestratorState) -> str:
        analysis = state["analysis"]
        if not analysis.succeeded:
            return (
                "L'analyse a échoué après plusieurs tentatives : "
                f"{analysis.execution.error or 'erreur inconnue'}"
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
    def _step(node: str, detail: str, start: float) -> TraceStep:
        return TraceStep(
            node=node, detail=detail, duration_ms=int((time.monotonic() - start) * 1000)
        )
