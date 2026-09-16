"""Tests de l'API FastAPI (orchestrateur doublé + un flux réel scripté)."""

from pathlib import Path

import joblib
import pytest
from fastapi.testclient import TestClient

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog
from data_analyst_agent.api.app import create_app
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator, SourceDuCatalogue
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import MimeOutput
from helpers.auth import client_connecte, creer_compte, reglages_de_test
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
        # la racine reçue au dernier appel : c'est elle qui doit être celle de
        # l'utilisateur de la session, et pas la racine commune.
        self.workspace_roots: list[Path | None] = []
        # la source liée au fil, telle que l'API l'a relue du disque : c'est ce
        # qui prouve qu'elle est repassée à chaque tour, comme le `pending`.
        self.sources_de_travail: list[object] = []

    # Le catalogue que l'indicateur de la page affiche. Deux sources, leurs
    # faits déjà « lus » : le double n'ouvre aucune source, il rend ce que
    # l'orchestrateur réel rendrait après relevé.
    SOURCES = (
        SourceDuCatalogue(
            name="titanic",
            type="postgres",
            description="Passagers du Titanic.",
            faits="2 table(s), 894 ligne(s) (passengers : 891, classes : 3)",
        ),
        SourceDuCatalogue(name="iris", type="file", description="Mesures florales."),
    )

    def ask(
        self,
        question: str,
        source: str | None = None,
        pending=None,
        conversation_id=None,
        workspace_root: Path | None = None,
        source_de_travail=None,
        echange_precedent=None,
    ) -> ChatAnswer:
        self.calls.append((question, source, pending))
        self.workspace_roots.append(workspace_root)
        self.sources_de_travail.append(source_de_travail)
        return self.answer

    def inventaire_des_sources(self) -> list[SourceDuCatalogue]:
        return list(self.SOURCES)

    def source_declaree(self, nom: str) -> bool:
        return any(s.name == nom for s in self.SOURCES)

    def accuser_la_source(self, nom: str, precedente: str = "") -> str:
        quittee = f" (on travaillait sur `{precedente}`)" if precedente else ""
        return f"Entendu : on travaille sur **{nom}**{quittee}."


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
    """Conversations, comptes et sessions isolés : chaque test a son dossier."""
    return reglages_de_test(tmp_path)


@pytest.fixture
def mot_de_passe(settings: Settings) -> str:
    """Un compte, et son mot de passe tiré au hasard à chaque exécution."""
    return creer_compte(settings)


@pytest.fixture
def client(
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
) -> TestClient:
    """Client authentifié : toutes les routes exigent une session."""
    app = create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings)
    return client_connecte(app, settings, mot_de_passe)


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


def test_chat_erreur_transmise(
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
):
    fake_orchestrator.answer = ChatAnswer(
        answer="Je n'ai pas pu répondre : source inconnue", error="source inconnue"
    )
    client = client_connecte(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings),
        settings,
        mot_de_passe,
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
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
):
    compteur = {"n": 0}

    def factory():
        compteur["n"] += 1
        return fake_orchestrator

    client = client_connecte(
        create_app(orchestrator_factory=factory, settings=settings), settings, mot_de_passe
    )
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
    reglages = reglages_de_test(tmp_path)
    orchestrator = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=reglages,
    )
    client = client_connecte(
        create_app(orchestrator_factory=lambda: orchestrator, settings=reglages),
        reglages,
        creer_compte(reglages),
    )

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
    reglages = reglages_de_test(tmp_path)
    orchestrator = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=reglages,
    )
    client = client_connecte(
        create_app(orchestrator_factory=lambda: orchestrator, settings=reglages),
        reglages,
        creer_compte(reglages),
    )
    body = client.post("/chat", json={"message": "Prédis pour cette passagère..."}).json()
    assert "a survécu" in body["answer"]
    assert body["plan"]["capability"] == "predict"
    # `system` est le premier nœud de tout tour : il demande au modèle si la
    # question porte sur l'agent lui-même. Ici il décline (aucun outil appelé),
    # et le tour suit son chemin.
    assert [step["node"] for step in body["trace"]] == [
        "system",
        "plan",
        "inference",
        "synthesize",
    ]


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
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
):
    """Reprendre un fil en attente de features doit rendre son contexte à l'agent."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    fake_orchestrator.answer = ChatAnswer(
        answer="Quel âge ?", pending=PendingInference(dataset="titanic", features={"sex": "female"})
    )
    client = client_connecte(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings),
        settings,
        mot_de_passe,
    )
    conversation_id = client.post("/chat", json={"message": "Prédis pour une femme"}).json()[
        "conversation_id"
    ]

    client.post("/chat", json={"message": "28 ans", "conversation_id": conversation_id})

    pending_du_2e_tour = fake_orchestrator.calls[-1][2]
    assert pending_du_2e_tour.dataset == "titanic"
    assert pending_du_2e_tour.features == {"sex": "female"}


def test_la_source_validee_est_persistee_et_repassee_au_tour_suivant(
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
):
    """Elle est portée par la CONVERSATION, comme ``owner`` et ``pending``.

    Ce test tient les deux bouts du chemin : ce que l'orchestrateur rend est
    écrit dans la transcription, et ce que la transcription porte lui est
    repassé au tour d'après. Sans l'un des deux, la source serait redevinée à
    chaque tour — le défaut qu'on corrige.
    """
    fake_orchestrator.answer = ChatAnswer(
        answer="Entendu : on travaille sur titanic.", source_de_travail="titanic"
    )
    client = client_connecte(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings),
        settings,
        mot_de_passe,
    )
    conversation_id = client.post("/chat", json={"message": "titanic"}).json()["conversation_id"]

    # elle est sur le disque, dans le fil
    fil = client.get(f"/conversations/{conversation_id}").json()
    assert fil["source_de_travail"] == "titanic"

    client.post(
        "/chat", json={"message": "combien de lignes ?", "conversation_id": conversation_id}
    )

    assert fake_orchestrator.sources_de_travail[-1] == "titanic"


def test_conversation_survit_a_un_redemarrage(
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
):
    """Le fil est sur disque : une nouvelle instance d'app le retrouve."""
    premier = client_connecte(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings),
        settings,
        mot_de_passe,
    )
    conversation_id = premier.post("/chat", json={"message": "Combien de femmes ?"}).json()[
        "conversation_id"
    ]

    redemarre = client_connecte(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings),
        settings,
        mot_de_passe,
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
    """Garde-fou : une erreur de syntaxe casse tout le script, sans bruit côté serveur."""
    esprima = pytest.importorskip("esprima")
    import re

    from data_analyst_agent.api import pages

    script = re.search(r"<script>(.*)</script>", pages.gabarit(pages.CHAT), re.S).group(1)
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


# --- l'indicateur de source de travail, côté API ------------------------------


def test_l_inventaire_des_sources_porte_ce_qu_on_y_a_lu(client: TestClient):
    """Ce que l'indicateur affiche : le YAML, plus les faits relevés dans la source.

    Les mêmes faits que ceux de l'inventaire proposé en conversation — deux
    inventaires qui divergeraient seraient pires qu'un seul.
    """
    sources = client.get("/sources").json()

    assert [s["name"] for s in sources] == ["titanic", "iris"]
    assert "891" in sources[0]["faits"]
    assert sources[0]["type"] == "postgres"


def test_ouvrir_un_fil_vide_permet_de_choisir_avant_d_ecrire(client: TestClient):
    """Le choix doit atterrir dans un fil, puisque c'est le fil qui porte la
    source : il faut donc qu'un fil puisse exister avant le premier message."""
    fil = client.post("/conversations")

    assert fil.status_code == 201
    assert fil.json()["messages"] == []
    assert fil.json()["source_de_travail"] == ""


def test_choisir_la_source_dans_le_menu_la_lie_au_fil_et_l_inscrit_dedans(client: TestClient):
    """« Un moyen d'en changer sans le taper » — et la trace que ça laisse.

    Le changement est écrit dans la transcription comme un message de l'agent :
    relire un fil dont les réponses changent de données sans que rien ne le
    dise serait exactement ce que la bascule annoncée évite.
    """
    identifiant = client.post("/conversations").json()["id"]

    reponse = client.put(f"/conversations/{identifiant}/source", json={"source": "titanic"})

    assert reponse.status_code == 200
    assert reponse.json()["source_de_travail"] == "titanic"
    fil = client.get(f"/conversations/{identifiant}").json()
    assert fil["source_de_travail"] == "titanic"
    assert [m["role"] for m in fil["messages"]] == ["agent"]
    assert "titanic" in fil["messages"][0]["content"]


def test_la_source_choisie_dans_le_menu_est_repassee_au_tour_suivant(
    fake_orchestrator: FakeOrchestrator, settings: Settings, mot_de_passe: str
):
    """La boucle complète : le menu écrit dans le fil, le fil alimente le tour.

    C'est ce qui fait que la source vient TOUJOURS du fil : ``POST /chat`` la
    relit du disque, exactement comme quand l'utilisateur l'avait tapée.
    """
    client = client_connecte(
        create_app(orchestrator_factory=lambda: fake_orchestrator, settings=settings),
        settings,
        mot_de_passe,
    )
    identifiant = client.post("/conversations").json()["id"]
    client.put(f"/conversations/{identifiant}/source", json={"source": "iris"})

    client.post("/chat", json={"message": "combien de lignes ?", "conversation_id": identifiant})

    assert fake_orchestrator.sources_de_travail[-1] == "iris"


def test_la_bascule_par_le_menu_dit_la_source_quittee(client: TestClient):
    identifiant = client.post("/conversations").json()["id"]
    client.put(f"/conversations/{identifiant}/source", json={"source": "titanic"})

    reponse = client.put(f"/conversations/{identifiant}/source", json={"source": "iris"})

    assert "on travaillait sur `titanic`" in reponse.json()["message"]


def test_delier_la_source_rend_la_main_a_la_proposition(client: TestClient):
    """Repartir de « aucune » est un état légitime, pas un accident : l'agent
    reproposera son inventaire à la prochaine question qui en demande une."""
    identifiant = client.post("/conversations").json()["id"]
    client.put(f"/conversations/{identifiant}/source", json={"source": "titanic"})

    reponse = client.put(f"/conversations/{identifiant}/source", json={"source": ""})

    assert reponse.json()["source_de_travail"] == ""
    assert "inventaire" in reponse.json()["message"]
    assert client.get(f"/conversations/{identifiant}").json()["source_de_travail"] == ""


def test_une_source_hors_catalogue_est_refusee(client: TestClient):
    """Seule une source DÉCLARÉE se lie. Un tableau intermédiaire de conversation
    est interrogeable, ce n'est pas une source de données : le lier
    remplacerait la source de travail par un résultat de requête."""
    identifiant = client.post("/conversations").json()["id"]

    reponse = client.put(f"/conversations/{identifiant}/source", json={"source": "resultat_1"})

    assert reponse.status_code == 404
    assert client.get(f"/conversations/{identifiant}").json()["source_de_travail"] == ""


def test_choisir_la_source_d_un_fil_inconnu_repond_404(client: TestClient):
    reponse = client.put("/conversations/jamais-vu/source", json={"source": "titanic"})

    assert reponse.status_code == 404


def test_la_page_ne_porte_aucun_bandeau_de_source(client: TestClient):
    """La source de travail ne s'affiche pas et ne se clique pas : on la demande.

    Un menu déroulant a été monté ici, puis un témoin en lecture seule ; les deux
    posaient à l'écran une question que le dialogue pose mieux. L'agent annonce
    ses sources quand on les lui demande et lie celle qu'on lui nomme, et c'est
    le FIL qui en garde la trace. `/sources` reste servi par l'API — il n'est
    simplement plus lu par la page.
    """
    page = client.get("/").text

    assert "Source de travail" not in page
    assert "<select" not in page
    assert 'id="source-liee"' not in page


def test_le_dernier_echange_est_relu_du_fil():
    """Le tour d'avant vient du DISQUE, comme la source liée et la prédiction en attente.

    Le client ne l'envoie pas : `ChatRequest` ne porte pas de champ pour ça, et
    c'est délibéré — un fil se relit, il ne se raconte pas depuis le navigateur.
    Le rôle stocké est « agent » et non « assistant » : l'avoir cherché sous le
    mauvais nom rendait toujours `None`, donc le rappel n'atteignait jamais
    l'agent système, et « oui » restait incompris (mesuré le 2026-09-16).
    """
    from data_analyst_agent.api.app import _dernier_echange
    from data_analyst_agent.orchestrator.conversations import Conversation, Message

    fil = Conversation(
        id="x",
        messages=[
            Message(role="user", content="premiere"),
            Message(role="agent", content="reponse 1"),
            Message(role="user", content="deuxieme"),
            Message(role="agent", content="reponse 2"),
        ],
    )

    assert _dernier_echange(fil) == ("deuxieme", "reponse 2")
    assert _dernier_echange(Conversation(id="y")) is None
