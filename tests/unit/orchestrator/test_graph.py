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


# --- multi-tours (slot-filling conversationnel) ----------------------------------


def test_relance_puis_complement_multi_tours(registry: Registry):
    """Tour 1 : features partielles -> relance + pending. Tour 2 : complément -> prédiction."""
    llm1 = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict", dataset="titanic", features={"sex": "female", "pclass": 1}
                )
            )
        ],
    )
    orchestrator1 = orchestrator_with(llm1, registry=registry)
    tour1 = orchestrator1.ask("Prédis la survie d'une femme en 1re classe")
    assert tour1.answer.strip().endswith("?")
    assert tour1.pending is not None
    assert tour1.pending.dataset == "titanic"
    assert tour1.pending.features == {"sex": "female", "pclass": 1}

    # tour 2 : le planificateur n'extrait QUE les nouvelles valeurs du message
    llm2 = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict",
                    dataset="titanic",
                    features={"age": 28, "sibsp": 0, "parch": 0, "fare": 80.0, "embarked": "S"},
                )
            )
        ],
    )
    orchestrator2 = orchestrator_with(llm2, registry=registry)
    tour2 = orchestrator2.ask(
        "Elle a 28 ans, pas de famille à bord, billet à 80 livres, embarquée à Southampton",
        pending=tour1.pending,
    )
    assert tour2.error is None
    assert "a survécu" in tour2.answer  # fusion acquis + complément -> prédiction
    assert tour2.pending is None  # plus rien en attente
    # le contexte multi-tours a bien été donné au planificateur
    assert "CONTEXTE DE CONVERSATION" in llm2.systems_for(PLANNER)[0]
    assert "sex='female'" in llm2.systems_for(PLANNER)[0]


def test_pending_ignore_si_changement_de_sujet(mini_csv: Path, registry: Registry):
    """L'utilisateur digresse : le contexte en attente n'est pas appliqué de force."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
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
    answer = orchestrator.ask(
        "Finalement, combien de lignes dans la table ?",
        pending=PendingInference(dataset="titanic", features={"sex": "female"}),
    )
    assert answer.error is None
    assert answer.answer == "4 lignes."
    assert answer.pending is None  # la digression solde le contexte


def test_fetch_then_predict_degrade_en_predict_sans_source(registry: Registry):
    """Catalogue vide + route fetch_then_predict impossible -> predict + relance."""
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="fetch_then_predict",
                    dataset="titanic",
                    features={"sex": "female", "pclass": 1},
                )
            )
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry)  # catalogue vide
    answer = orchestrator.ask("Prédis la survie d'une femme en 1re classe")
    assert answer.error is None  # pas de crash « catalogue vide »
    assert answer.plan.capability == "predict"
    assert answer.answer.strip().endswith("?")  # relance sur les features manquantes
    assert answer.pending is not None


def test_correction_de_valeur_hors_bornes_multi_tours(registry: Registry):
    """Le complément corrige une valeur invalide de l'acquis (le nouveau prime)."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    pending = PendingInference(dataset="titanic", features={**TITANIC_OK, "age": 250})
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"age": 25}))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Pardon, 25 ans", pending=pending)
    assert answer.error is None
    assert "a survécu" in answer.answer


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


def test_chainage_colonnes_capitalisees(tmp_path: Path, registry: Registry):
    """Les en-têtes capitalisés (CSV/Excel réels) sont mappés malgré la casse."""
    csv = tmp_path / "Passagers.csv"
    csv.write_text(
        "PassengerId,Sex,Pclass,Age,SibSp,Parch,Fare,Embarked\n1,female,1,28,0,0,80.0,S\n",
        encoding="utf-8",
    )
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
                    {"query": "SELECT * FROM passagers WHERE PassengerId = 1"},
                ),
                text("Ligne récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie du passager 1")
    assert answer.error is None
    assert "a survécu" in answer.answer


def test_chainage_indice_de_colonnes_dans_le_prompt(passager_csv: Path, registry: Registry):
    """L'agent SQL reçoit la liste exacte des features attendues (alias forcés)."""
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
                text("Ligne récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=passager_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    orchestrator.ask("Prédis la survie du passager 1")
    retrieval_prompt = llm.prompts_for(RETRIEVAL)[0]
    assert "nommées exactement" in retrieval_prompt
    for field in ("sex", "pclass", "age", "sibsp", "parch", "fare", "embarked"):
        assert field in retrieval_prompt


def test_chainage_en_lot_avec_lignes_invalides(tmp_path: Path, registry: Registry):
    """N lignes récupérées -> prédiction en lot, invalides écartées, détail joint."""
    csv = tmp_path / "groupe.csv"
    csv.write_text(
        "passenger_id,sex,pclass,age,sibsp,parch,fare,embarked\n"
        "1,female,1,28,0,0,80.0,S\n"
        "2,female,2,-5,0,0,20.0,S\n"  # age hors bornes -> écartée
        "3,female,3,40,1,0,8.0,Q\n",
        encoding="utf-8",
    )
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="groupe",
                        dataset="titanic",
                        data_question="Toutes les femmes",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM groupe WHERE sex = 'female'"}),
                text("3 lignes récupérées."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="groupe", path=csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie de toutes les femmes")

    assert answer.error is None
    assert "sur 2 lignes" in answer.answer
    assert "écartée" in answer.answer
    assert "a survécu : 2 (100%)" in answer.answer
    # table de détail : les colonnes récupérées + prediction + confiance
    detail = json.loads(answer.artifacts[0].data)
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    assert len(detail["rows"]) == 3
    assert detail["rows"][0][-2] == "a survécu"
    assert detail["rows"][1][-2].startswith("écartée")
    assert detail["rows"][1][-1] is None
    trace_step = next(s for s in answer.trace if s.node == "fetch_predict")
    assert "2/3" in trace_step.detail


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


def test_source_omise_catalogue_a_une_source(mini_csv: Path, registry: Registry):
    """Le LLM omet parfois la source : repli sur l'unique source du catalogue."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source=None))])
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
    answer = orchestrator.ask("Combien de lignes ?")
    assert answer.error is None
    assert answer.plan.source == "mini"  # repli tracé dans le plan


def test_source_omise_catalogue_multi_sources(
    mini_csv: Path, passager_csv: Path, registry: Registry
):
    """Plusieurs sources et aucun choix : on POSE une question, on ne plante pas."""
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source=None))])
    catalog = Catalog(
        sources=[
            FileSource(name="mini", path=mini_csv),
            FileSource(name="passagers", path=passager_csv),
        ]
    )
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    # clarification, pas d'erreur brute ni de KeyError remontée à l'utilisateur
    assert answer.error is None
    assert "mini" in answer.answer
    assert "passagers" in answer.answer
    assert answer.answer.strip().endswith("?")
    # la capacité n'a pas été exécutée : on s'arrête au plan puis on synthétise
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]
    assert answer.artifacts == []


def test_query_sur_iris_renvoie_les_colonnes(registry: Registry):
    """La source iris est interrogeable en SQL : les attributs remontent."""
    iris = Path(__file__).parents[3] / "sources" / "iris.csv"
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="iris"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM iris LIMIT 5"}),
                text("Colonnes : sepal_length, sepal_width, petal_length, petal_width, species."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="iris", path=iris)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Donne-moi les attributs du dataset iris")
    assert answer.error is None
    table = json.loads(answer.artifacts[0].data)
    assert table["columns"] == [
        "sepal_length",
        "sepal_width",
        "petal_length",
        "petal_width",
        "species",
    ]


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
