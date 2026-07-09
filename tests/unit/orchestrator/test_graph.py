"""Orchestrateur : graphe complet avec LLM scripté, sans Docker ni réseau.

DuckDB (in-process) joue les sources SQL ; la sandbox et les modèles ML sont
doublés ; seul le routage, le chaînage et la synthèse sont sous test.
"""

import json
from pathlib import Path

import joblib
import pytest

from data_analyst_agent.agents.inference.predict import InferenceOutcome, Prediction
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import MimeOutput, SandboxResult
from helpers.doubles import FakeClassifier, ScriptedSandbox
from helpers.scripted_llm import (
    ANALYSIS,
    PLANNER,
    RETRIEVAL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

TITANIC_OK = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
    labels:
      "0": "n'a pas survécu"
      "1": "a survécu"
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return csv


@pytest.fixture
def passager_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "passagers.csv"
    csv.write_text(
        "passenger_id,sex,pclass,age,sibsp,parch,fare,embarked\n"
        "1,female,1,28,0,0,80.0,S\n"
        "2,male,3,45,0,0,8.0,S\n",
        encoding="utf-8",
    )
    return csv


def orchestrator_with(llm: ScriptedLLM, **kwargs) -> Orchestrator:
    kwargs.setdefault("catalog", Catalog(sources=[]))
    kwargs.setdefault("settings", make_settings())
    return Orchestrator(model=llm.model(), **kwargs)


# --- predict ------------------------------------------------------------------


def test_flux_predict_complet(registry: Registry):
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features=TITANIC_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Prédis la survie pour sexe=female, classe=1, âge=28...")
    assert answer.error is None
    assert "a survécu" in answer.answer
    assert "88" in answer.answer  # probabilité citée
    assert [s.node for s in answer.trace] == ["plan", "inference", "synthesize"]
    assert answer.plan.capability == "predict"


def test_flux_predict_incomplet_redemande(registry: Registry):
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"sex": "female"}))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Prédis la survie d'une femme")
    assert answer.error is None
    assert "pclass" in answer.answer
    assert answer.answer.strip().endswith("?")
    assert answer.artifacts == []


# --- query --------------------------------------------------------------------


def test_flux_query_sur_fichier(mini_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE sexe = 'f'"}),
                text("Il y a 3 femmes dans la table."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de femmes ?")
    assert answer.error is None
    assert answer.answer == "Il y a 3 femmes dans la table."
    assert len(answer.artifacts) == 1
    table = json.loads(answer.artifacts[0].data)
    assert table["rows"] == [[3]]
    assert "retrieval" in [s.node for s in answer.trace]


# --- analyze ------------------------------------------------------------------


def test_flux_analyze_sur_fichier(mini_csv: Path, registry: Registry):
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                status="ok",
                stdout="taux par classe : 0.66\n",
                results=[MimeOutput(mime="image/png", data="cGl4ZWxz")],
            )
        ]
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="mini"))])
        .script(ANALYSIS, [text("```python\nprint('taux par classe : 0.66')\n```")])
        .script(SYNTHESIS, [text("Voici le bar chart demandé (taux : 0,66).")])
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry, sandbox=sandbox)
    answer = orchestrator.ask("Fais un bar chart de la survie")
    assert answer.error is None
    assert "bar chart" in answer.answer
    assert [a.mime for a in answer.artifacts] == ["image/png"]
    assert sandbox.executed  # le code est bien passé par la sandbox


# --- fetch_then_predict ---------------------------------------------------------


def test_chainage_fetch_then_predict(passager_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="passagers",
                        dataset="titanic",
                        data_question="La ligne du passager 1",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {
                        "query": (
                            "SELECT sex, pclass, age, sibsp, parch, fare, embarked"
                            " FROM passagers WHERE passenger_id = 1"
                        )
                    },
                ),
                text("Ligne du passager 1 récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=passager_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie du passager 1")
    assert answer.error is None
    assert "a survécu" in answer.answer
    nodes = [s.node for s in answer.trace]
    assert nodes == ["plan", "fetch_predict", "synthesize"]


def test_fetch_then_predict_sans_ligne(passager_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="passagers",
                        dataset="titanic",
                        data_question="Le passager 999",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {"query": "SELECT * FROM passagers WHERE passenger_id = 999"},
                ),
                text("Aucune ligne."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=passager_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie du passager 999")
    assert answer.error is not None
    assert "aucune ligne" in answer.error
    assert answer.answer.startswith("Je n'ai pas pu répondre")


# --- chemins d'erreur -----------------------------------------------------------


def test_source_inconnue_reponse_propre(registry: Registry):
    llm = ScriptedLLM().script(
        PLANNER, [plan_response(Plan(capability="query", source="nexiste-pas"))]
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    assert answer.error is not None
    assert "inconnue" in answer.error
    assert answer.answer.startswith("Je n'ai pas pu répondre")


def test_source_forcee_par_l_utilisateur(mini_csv: Path, registry: Registry):
    # la source passée à ask() prime sur celle du plan
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="autre"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}),
                text("4 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien ?", source="mini")
    assert answer.error is None
    assert answer.plan.source == "mini"


# --- unités pures ----------------------------------------------------------------


def test_format_prediction_regression():
    outcome = InferenceOutcome(
        status="ok",
        prediction=Prediction(
            dataset="california_housing",
            task="regression",
            value=4.1391,
            unit="centaines de milliers de dollars",
        ),
    )
    message = Orchestrator._format_prediction(outcome)
    assert "4.1391" in message
    assert "centaines de milliers de dollars" in message


def test_route():
    assert Orchestrator._route({"error": "boom"}) == "error"
    assert Orchestrator._route({}) == "error"
    assert Orchestrator._route({"plan": Plan(capability="analyze")}) == "analyze"


def test_logs_structures_par_noeud(registry: Registry, caplog):
    import logging

    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features=TITANIC_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    with caplog.at_level(logging.INFO, logger="data_analyst_agent.orchestrator"):
        orchestrator.ask("Prédis la survie")
    messages = [record.getMessage() for record in caplog.records]
    assert any("nœud plan : terminé" in m for m in messages)
    assert any("nœud inference : terminé" in m for m in messages)
    assert any("nœud synthesize : terminé" in m for m in messages)
