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
def settings(tmp_path) -> Settings:
    """Conversations isolées : chaque test a son propre dossier de travail."""
    return Settings(_env_file=None, workspace_dir=tmp_path / "workspaces")


@pytest.fixture
def client(fake_orchestrator: FakeOrchestrator, settings: Settings) -> TestClient:
    return TestClient(create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings))


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


def test_chat_erreur_transmise(fake_orchestrator: FakeOrchestrator, settings: Settings):
    fake_orchestrator.answer = ChatAnswer(
        answer="Je n'ai pas pu répondre : source inconnue", error="source inconnue"
    )
    client = TestClient(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings)
    )
    body = client.post("/chat", json={"message": "?"}).json()
    assert body["error"] == "source inconnue"
    assert body["answer"].startswith("Je n'ai pas pu répondre")


def test_page_de_chat_servie(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "data-analyst-agent" in response.text
    assert "/chat" in response.text  # la page appelle bien l'API


def test_orchestrateur_construit_une_seule_fois(
    fake_orchestrator: FakeOrchestrator, settings: Settings
):
    compteur = {"n": 0}

    def factory():
        compteur["n"] += 1
        return fake_orchestrator

    client = TestClient(create_app(orchestrator_factory=factory, settings=settings))
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
    reglages = Settings(_env_file=None, workspace_dir=tmp_path / "workspaces")
    orchestrator = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=reglages,
    )
    client = TestClient(create_app(orchestrator_factory=lambda: orchestrator, settings=reglages))

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
    reglages = Settings(_env_file=None, workspace_dir=tmp_path / "workspaces")
    orchestrator = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=reglages,
    )
    client = TestClient(create_app(orchestrator_factory=lambda: orchestrator, settings=reglages))
    body = client.post("/chat", json={"message": "Prédis pour cette passagère..."}).json()
    assert "a survécu" in body["answer"]
    assert body["plan"]["capability"] == "predict"
    assert [step["node"] for step in body["trace"]] == ["plan", "inference", "synthesize"]


# -- barre latérale : lister, reprendre, dupliquer, supprimer ---------------------


def test_conversations_vide_au_demarrage(client: TestClient):
    assert client.get("/conversations").json() == []


def test_conversation_apparait_dans_la_liste_apres_un_message(client: TestClient):
    client.post("/chat", json={"message": "Combien de femmes ?"})

    liste = client.get("/conversations").json()
    assert len(liste) == 1
    assert liste[0]["title"] == "Combien de femmes ?"  # titrée par son 1er message
    assert liste[0]["message_count"] == 2


def test_ouvrir_une_conversation_rend_le_fil(client: TestClient):
    conversation_id = client.post("/chat", json={"message": "Combien de femmes ?"}).json()[
        "conversation_id"
    ]

    fil = client.get(f"/conversations/{conversation_id}").json()
    assert [(m["role"], m["content"]) for m in fil["messages"]] == [
        ("user", "Combien de femmes ?"),
        ("agent", "Il y a 3 femmes."),
    ]
    assert fil["messages"][1]["artifacts"] == [{"mime": "image/png", "data": "cGl4ZWxz"}]


def test_ouvrir_une_conversation_inconnue(client: TestClient):
    assert client.get("/conversations/jamais-vu").status_code == 404


def test_reprise_repasse_le_pending_a_lorchestrateur(
    fake_orchestrator: FakeOrchestrator, settings: Settings
):
    """Reprendre un fil en attente de features doit rendre son contexte à l'agent."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    fake_orchestrator.answer = ChatAnswer(
        answer="Quel âge ?", pending=PendingInference(dataset="titanic", features={"sex": "female"})
    )
    client = TestClient(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings)
    )
    conversation_id = client.post("/chat", json={"message": "Prédis pour une femme"}).json()[
        "conversation_id"
    ]

    client.post("/chat", json={"message": "28 ans", "conversation_id": conversation_id})

    pending_du_2e_tour = fake_orchestrator.calls[-1][2]
    assert pending_du_2e_tour.dataset == "titanic"
    assert pending_du_2e_tour.features == {"sex": "female"}


def test_conversation_survit_a_un_redemarrage(
    fake_orchestrator: FakeOrchestrator, settings: Settings
):
    """Le fil est sur disque : une nouvelle instance d'app le retrouve."""
    premier = TestClient(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings)
    )
    conversation_id = premier.post("/chat", json={"message": "Combien de femmes ?"}).json()[
        "conversation_id"
    ]

    redemarre = TestClient(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings)
    )
    assert [c["id"] for c in redemarre.get("/conversations").json()] == [conversation_id]
    assert redemarre.get(f"/conversations/{conversation_id}").status_code == 200


def test_supprimer_une_conversation(client: TestClient):
    conversation_id = client.post("/chat", json={"message": "Combien de femmes ?"}).json()[
        "conversation_id"
    ]

    assert client.delete(f"/conversations/{conversation_id}").status_code == 204
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{conversation_id}").status_code == 404


def test_supprimer_une_conversation_inconnue(client: TestClient):
    assert client.delete("/conversations/jamais-vu").status_code == 404


def test_dupliquer_une_conversation(client: TestClient):
    conversation_id = client.post("/chat", json={"message": "Combien de femmes ?"}).json()[
        "conversation_id"
    ]

    copie = client.post(f"/conversations/{conversation_id}/duplicate").json()

    assert copie["id"] != conversation_id
    assert copie["title"] == "Combien de femmes ? (copie)"
    assert len(copie["messages"]) == 2
    assert {c["id"] for c in client.get("/conversations").json()} == {conversation_id, copie["id"]}


def test_dupliquer_puis_poursuivre_nimpacte_pas_loriginal(client: TestClient):
    conversation_id = client.post("/chat", json={"message": "Combien de femmes ?"}).json()[
        "conversation_id"
    ]
    copie_id = client.post(f"/conversations/{conversation_id}/duplicate").json()["id"]

    client.post("/chat", json={"message": "Et les hommes ?", "conversation_id": copie_id})

    assert len(client.get(f"/conversations/{copie_id}").json()["messages"]) == 4
    assert len(client.get(f"/conversations/{conversation_id}").json()["messages"]) == 2


def test_dupliquer_une_conversation_inconnue(client: TestClient):
    assert client.post("/conversations/jamais-vu/duplicate").status_code == 404


def test_page_de_chat_porte_la_barre_laterale(client: TestClient):
    page = client.get("/").text
    assert "Nouvelle conversation" in page
    assert "/conversations" in page  # la page sait lister les fils


def test_javascript_de_la_page_est_syntaxiquement_valide():
    """Garde-fou : la page est une chaîne Python, un échappement raté casse tout le script."""
    esprima = pytest.importorskip("esprima")
    import re

    from data_analyst_agent.api.app import CHAT_PAGE

    script = re.search(r"<script>(.*)</script>", CHAT_PAGE, re.S).group(1)
    esprima.parseScript(script)


def test_id_de_conversation_choisi_par_le_client_est_honore(client: TestClient):
    """Un client qui mène ses tours sous son propre id (cf. scripts/live_scenarios.py)
    doit garder le même fil : lui en réattribuer un autre casserait le chaînage."""
    tour1 = client.post("/chat", json={"message": "1er tour", "conversation_id": "mon-fil"}).json()
    assert tour1["conversation_id"] == "mon-fil"

    client.post("/chat", json={"message": "2e tour", "conversation_id": "mon-fil"})

    liste = client.get("/conversations").json()
    assert [c["id"] for c in liste] == ["mon-fil"]  # un seul fil, pas un par tour
    assert liste[0]["message_count"] == 4
