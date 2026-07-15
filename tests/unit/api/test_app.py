"""Tests de l'API FastAPI (orchestrateur doublé + un flux réel scripté)."""

import joblib
import pytest
from fastapi.testclient import TestClient

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog
from data_analyst_agent.api.app import create_app
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import MimeOutput
from helpers.doubles import FakeClassifier
from helpers.scripted_llm import PLANNER, ScriptedLLM, plan_response

TITANIC_OK = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}


class FakeOrchestrator:
    def __init__(self, answer: ChatAnswer) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str | None, object]] = []

    def ask(
        self, question: str, source: str | None = None, pending=None, conversation_id=None
    ) -> ChatAnswer:
        self.calls.append((question, source, pending))
        return self.answer


@pytest.fixture
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator(
        ChatAnswer(
            answer="Il y a 3 femmes.",
            artifacts=[MimeOutput(mime="image/png", data="cGl4ZWxz")],
        )
    )


@pytest.fixture
def client(fake_orchestrator: FakeOrchestrator) -> TestClient:
    return TestClient(create_app(orchestrator_factory=lambda: fake_orchestrator))


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_chat_repond_avec_artefacts(client: TestClient, fake_orchestrator: FakeOrchestrator):
    response = client.post("/chat", json={"message": "Combien de femmes ?", "source": "mini"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Il y a 3 femmes."
    assert body["artifacts"] == [{"mime": "image/png", "data": "cGl4ZWxz"}]
    assert body["error"] is None
    assert body["conversation_id"]  # un id est attribué même sans multi-tours
    assert fake_orchestrator.calls == [("Combien de femmes ?", "mini", None)]


def test_chat_message_obligatoire(client: TestClient):
    response = client.post("/chat", json={})
    assert response.status_code == 422


def test_chat_erreur_transmise(fake_orchestrator: FakeOrchestrator):
    fake_orchestrator.answer = ChatAnswer(
        answer="Je n'ai pas pu répondre : source inconnue", error="source inconnue"
    )
    client = TestClient(create_app(orchestrator_factory=lambda: fake_orchestrator))
    body = client.post("/chat", json={"message": "?"}).json()
    assert body["error"] == "source inconnue"
    assert body["answer"].startswith("Je n'ai pas pu répondre")


def test_page_de_chat_servie(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "data-analyst-agent" in response.text
    assert "/chat" in response.text  # la page appelle bien l'API


def test_orchestrateur_construit_une_seule_fois(fake_orchestrator: FakeOrchestrator):
    compteur = {"n": 0}

    def factory():
        compteur["n"] += 1
        return fake_orchestrator

    client = TestClient(create_app(orchestrator_factory=factory))
    client.post("/chat", json={"message": "a"})
    client.post("/chat", json={"message": "b"})
    assert compteur["n"] == 1


def test_conversation_multi_tours_via_api(tmp_path):
    """Relance au tour 1, complément au tour 2 avec le même conversation_id."""
    (tmp_path / "registry.yaml").write_text(
        "models:\n"
        "  - dataset: titanic\n"
        "    task: classification\n"
        "    model_path: titanic.joblib\n"
        "    target: survived\n"
        '    labels: {"0": "n\'a pas survécu", "1": "a survécu"}\n',
        encoding="utf-8",
    )
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    registry = Registry.load(tmp_path / "registry.yaml")
    llm = ScriptedLLM().script(
        PLANNER,
        [
            # tour 1 : extraction partielle
            plan_response(
                Plan(
                    capability="predict", dataset="titanic", features={"sex": "female", "pclass": 1}
                )
            ),
            # tour 2 : uniquement les nouvelles valeurs
            plan_response(
                Plan(
                    capability="predict",
                    dataset="titanic",
                    features={"age": 28, "sibsp": 0, "parch": 0, "fare": 80.0, "embarked": "S"},
                )
            ),
        ],
    )
    orchestrator = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=Settings(_env_file=None),
    )
    client = TestClient(create_app(orchestrator_factory=lambda: orchestrator))

    tour1 = client.post("/chat", json={"message": "Prédis pour une femme en 1re classe"}).json()
    assert tour1["answer"].strip().endswith("?")
    assert tour1["pending"]["dataset"] == "titanic"
    conversation_id = tour1["conversation_id"]

    tour2 = client.post(
        "/chat",
        json={
            "message": "28 ans, seule, billet 80 livres, Southampton",
            "conversation_id": conversation_id,
        },
    ).json()
    assert "a survécu" in tour2["answer"]
    assert tour2["pending"] is None
    assert tour2["conversation_id"] == conversation_id


def test_flux_reel_predict_via_api(tmp_path):
    """Un vrai Orchestrator (LLM scripté) derrière l'API, sans Docker."""
    (tmp_path / "registry.yaml").write_text(
        "models:\n"
        "  - dataset: titanic\n"
        "    task: classification\n"
        "    model_path: titanic.joblib\n"
        "    target: survived\n"
        '    labels: {"0": "n\'a pas survécu", "1": "a survécu"}\n',
        encoding="utf-8",
    )
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    registry = Registry.load(tmp_path / "registry.yaml")
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features=TITANIC_OK))],
    )
    orchestrator = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=Settings(_env_file=None),
    )
    client = TestClient(create_app(orchestrator_factory=lambda: orchestrator))
    body = client.post("/chat", json={"message": "Prédis pour cette passagère..."}).json()
    assert "a survécu" in body["answer"]
    assert body["plan"]["capability"] == "predict"
    assert [step["node"] for step in body["trace"]] == ["plan", "inference", "synthesize"]
