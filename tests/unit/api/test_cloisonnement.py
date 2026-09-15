"""Chacun ne voit, ne modifie et ne supprime que SES conversations.

Deux comptes, chacun son fil, et pour chaque route la tentative d'accès croisé.
Aucun de ces tests ne passe sans le cloisonnement : avant lui,
`GET /conversations` rendait tous les fils de tous les visiteurs authentifiés
(audit §2.3), et `POST /chat` écrivait dans le `conversation_id` que le client
lui donnait, quel qu'en soit le propriétaire.

Le refus attendu est **404, jamais 403**. Un 403 dirait « ce fil existe, mais
pas pour vous » : c'est un oracle d'existence, gratuit à éviter, et sans
contrepartie — l'utilisateur légitime, lui, ne voit jamais ce cas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_analyst_agent.api.app import create_app
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.graph import ChatAnswer
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace, safe_dir_name
from helpers.auth import client_connecte, creer_compte, reglages_de_test

ALICE = "alice"
BOB = "bob"


class FakeOrchestrator:
    """Répond toujours pareil, et retient la racine qu'on lui a passée."""

    def __init__(self) -> None:
        self.workspace_roots: list[Path | None] = []

    def ask(
        self,
        question: str,
        source: str | None = None,
        pending=None,
        conversation_id=None,
        workspace_root: Path | None = None,
        source_de_travail=None,
    ) -> ChatAnswer:
        self.workspace_roots.append(workspace_root)
        return ChatAnswer(answer=f"réponse à : {question}")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return reglages_de_test(tmp_path)


@pytest.fixture
def orchestrateur() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture
def app(orchestrateur: FakeOrchestrator, settings: Settings):
    return create_app(orchestrator_factory=lambda: orchestrateur, settings=settings)


def _client(app, settings: Settings, login: str) -> TestClient:
    return client_connecte(app, settings, creer_compte(settings, login), login=login)


@pytest.fixture
def alice(app, settings: Settings) -> TestClient:
    return _client(app, settings, ALICE)


@pytest.fixture
def bob(app, settings: Settings) -> TestClient:
    return _client(app, settings, BOB)


def _ouvrir_un_fil(client: TestClient, message: str) -> str:
    """Un tour de conversation ; rend l'identifiant du fil créé."""
    reponse = client.post("/chat", json={"message": message})
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["conversation_id"]


@pytest.fixture
def fil_de_bob(bob: TestClient) -> str:
    return _ouvrir_un_fil(bob, "Le secret de Bob")


# -- lecture ------------------------------------------------------------------


def test_la_liste_ne_montre_que_ses_propres_fils(
    alice: TestClient, bob: TestClient, fil_de_bob: str
) -> None:
    fil_dalice = _ouvrir_un_fil(alice, "La question d'Alice")

    listee = alice.get("/conversations").json()

    assert [c["id"] for c in listee] == [fil_dalice]
    assert fil_de_bob not in [c["id"] for c in listee]


def test_la_liste_ne_fuit_pas_les_titres_des_autres(
    alice: TestClient, bob: TestClient, fil_de_bob: str
) -> None:
    """Le titre d'un fil est son premier message : le résumé fuit autant que le fil."""
    _ouvrir_un_fil(alice, "La question d'Alice")

    titres = [c["title"] for c in alice.get("/conversations").json()]

    assert "Le secret de Bob" not in titres


def test_ouvrir_le_fil_dun_autre_donne_404(alice: TestClient, fil_de_bob: str) -> None:
    reponse = alice.get(f"/conversations/{fil_de_bob}")

    assert reponse.status_code == 404
    assert reponse.json()["detail"] == "conversation inconnue"


def test_ouvrir_un_fil_inexistant_donne_la_meme_reponse(alice: TestClient, fil_de_bob: str) -> None:
    """Indistinguable : c'est ce qui empêche d'énumérer les fils des autres."""
    autrui = alice.get(f"/conversations/{fil_de_bob}")
    inexistant = alice.get("/conversations/jamais-ouvert")

    assert (autrui.status_code, autrui.json()) == (inexistant.status_code, inexistant.json())


# -- suppression --------------------------------------------------------------


def test_supprimer_le_fil_dun_autre_donne_404_et_ne_supprime_rien(
    alice: TestClient, bob: TestClient, fil_de_bob: str
) -> None:
    reponse = alice.delete(f"/conversations/{fil_de_bob}")

    assert reponse.status_code == 404
    assert bob.get(f"/conversations/{fil_de_bob}").status_code == 200


# -- duplication --------------------------------------------------------------


def test_dupliquer_le_fil_dun_autre_donne_404(alice: TestClient, fil_de_bob: str) -> None:
    reponse = alice.post(f"/conversations/{fil_de_bob}/duplicate")

    assert reponse.status_code == 404
    assert alice.get("/conversations").json() == []


def test_la_duplication_de_son_propre_fil_reste_a_soi(alice: TestClient, bob: TestClient) -> None:
    fil = _ouvrir_un_fil(alice, "La question d'Alice")

    copie = alice.post(f"/conversations/{fil}/duplicate").json()

    assert copie["owner"] == ALICE
    assert bob.get(f"/conversations/{copie['id']}").status_code == 404


# -- POST /chat : la route qui écrit -----------------------------------------


def test_un_conversation_id_forge_n_ecrit_pas_dans_le_fil_de_lautre(
    alice: TestClient, bob: TestClient, settings: Settings, fil_de_bob: str
) -> None:
    """Le cas qui compte : l'écriture, pas seulement la lecture."""
    magasin_de_bob = ConversationStore(settings.workspace_dir, BOB)
    avant = magasin_de_bob.dir_of(fil_de_bob).joinpath("transcript.json").read_bytes()

    reponse = alice.post("/chat", json={"message": "je m'invite", "conversation_id": fil_de_bob})

    assert reponse.status_code == 200
    apres = magasin_de_bob.dir_of(fil_de_bob).joinpath("transcript.json").read_bytes()
    assert apres == avant, "le fil de Bob a été modifié par Alice"
    fil = magasin_de_bob.load(fil_de_bob)
    assert [m.content for m in fil.messages] == ["Le secret de Bob", "réponse à : Le secret de Bob"]


def test_un_conversation_id_forge_ne_rend_pas_le_fil_de_lautre(
    alice: TestClient, fil_de_bob: str
) -> None:
    """Alice repart avec un fil neuf, chez elle — pas avec celui de Bob."""
    alice.post("/chat", json={"message": "je m'invite", "conversation_id": fil_de_bob})

    a_elle = alice.get(f"/conversations/{fil_de_bob}").json()

    assert a_elle["owner"] == ALICE
    assert [m["content"] for m in a_elle["messages"]] == ["je m'invite", "réponse à : je m'invite"]


def test_deux_comptes_peuvent_porter_le_meme_identifiant_de_fil(
    alice: TestClient, bob: TestClient
) -> None:
    """L'identifiant est relatif à son propriétaire ; il n'est pas global."""
    alice.post("/chat", json={"message": "chez Alice", "conversation_id": "partage"})
    bob.post("/chat", json={"message": "chez Bob", "conversation_id": "partage"})

    chez_alice = alice.get("/conversations/partage").json()
    chez_bob = bob.get("/conversations/partage").json()

    assert chez_alice["messages"][0]["content"] == "chez Alice"
    assert chez_bob["messages"][0]["content"] == "chez Bob"


# -- le propriétaire ne vient jamais du client -------------------------------


def test_le_client_ne_peut_pas_choisir_le_proprietaire(alice: TestClient) -> None:
    """Un champ `owner` envoyé par le client est sans effet."""
    fil = alice.post("/chat", json={"message": "coucou", "owner": BOB}).json()["conversation_id"]

    assert alice.get(f"/conversations/{fil}").json()["owner"] == ALICE


def test_le_proprietaire_est_ecrit_dans_la_transcription(
    alice: TestClient, settings: Settings
) -> None:
    fil = _ouvrir_un_fil(alice, "coucou")

    chemin = ConversationStore(settings.workspace_dir, ALICE).dir_of(fil) / "transcript.json"

    assert json.loads(chemin.read_text(encoding="utf-8"))["owner"] == ALICE


# -- l'arborescence -----------------------------------------------------------


def test_les_fils_vivent_sous_le_dossier_de_leur_proprietaire(
    alice: TestClient, bob: TestClient, settings: Settings, fil_de_bob: str
) -> None:
    fil_dalice = _ouvrir_un_fil(alice, "La question d'Alice")
    racine = settings.workspace_dir

    assert (racine / safe_dir_name(ALICE) / fil_dalice / "transcript.json").exists()
    assert (racine / safe_dir_name(BOB) / fil_de_bob / "transcript.json").exists()
    assert not (racine / fil_dalice).exists()  # plus rien à la racine commune
    assert sorted(d.name for d in racine.iterdir()) == [safe_dir_name(ALICE), safe_dir_name(BOB)]


def test_les_verrous_sont_cloisonnes_eux_aussi(
    alice: TestClient, bob: TestClient, settings: Settings, fil_de_bob: str
) -> None:
    """`resource_lock` range son `.locks/` à côté de la ressource : sous l'utilisateur."""
    _ouvrir_un_fil(alice, "La question d'Alice")

    assert (settings.workspace_dir / safe_dir_name(ALICE) / ".locks").is_dir()
    assert (settings.workspace_dir / safe_dir_name(BOB) / ".locks").is_dir()
    assert not (settings.workspace_dir / ".locks").exists()


def test_la_memoire_du_fil_atterrit_sous_la_meme_racine_que_sa_transcription(
    alice: TestClient, settings: Settings, orchestrateur: FakeOrchestrator
) -> None:
    """Transcription et tableaux intermédiaires doivent partager le dossier.

    C'est pour ça que l'API passe la racine du magasin à l'orchestrateur plutôt
    que de la lui faire recalculer : deux calculs, c'est deux occasions de
    diverger.
    """
    fil = _ouvrir_un_fil(alice, "La question d'Alice")
    racine = orchestrateur.workspace_roots[-1]

    assert racine == settings.workspace_dir / safe_dir_name(ALICE)
    memoire = ConversationWorkspace(racine, fil)
    assert memoire.dir == ConversationStore(settings.workspace_dir, ALICE).dir_of(fil)


# -- les artefacts d'un fil ---------------------------------------------------


def _artefact_dans(settings: Settings, login: str, fil: str) -> None:
    """Fabrique une figure dans le fil de quelqu'un, comme un tour l'aurait fait."""
    espace = ConversationWorkspace(ConversationStore(settings.workspace_dir, login).base_dir, fil)
    espace.save_code("plt.bar([1], [2])", "fais un graphe", source="iris", figures=1)


def test_on_lit_le_catalogue_et_le_contenu_de_SES_artefacts(
    alice: TestClient, settings: Settings
) -> None:
    fil = _ouvrir_un_fil(alice, "La question d'Alice")
    _artefact_dans(settings, ALICE, fil)

    catalogue = alice.get(f"/conversations/{fil}/artefacts").json()
    assert [(a["name"], a["kind"], a["retenu"]) for a in catalogue] == [
        ("graphique_1", "figure", True)
    ]

    contenu = alice.get(f"/conversations/{fil}/artefacts/graphique_1").json()
    assert contenu["content"] == "plt.bar([1], [2])"
    assert contenu["question"] == "fais un graphe"


def test_l_artefact_dun_autre_est_un_404_indistinguable_de_l_inexistant(
    alice: TestClient, settings: Settings, fil_de_bob: str
) -> None:
    """Le cloisonnement est un chemin : le fil de Bob n'existe pas là où Alice regarde.

    Les trois réponses doivent être le MÊME 404 — l'artefact de quelqu'un
    d'autre, un fil inventé, un nom d'artefact inventé chez soi. Un message
    différent par cas dirait à un inconnu lequel des trois il vient de toucher,
    donc lui dirait qu'un fil existe.
    """
    _artefact_dans(settings, BOB, fil_de_bob)
    fil_dalice = _ouvrir_un_fil(alice, "La question d'Alice")

    chez_bob = alice.get(f"/conversations/{fil_de_bob}/artefacts/graphique_1")
    fil_invente = alice.get("/conversations/jamais-ouvert/artefacts/graphique_1")
    catalogue_de_bob = alice.get(f"/conversations/{fil_de_bob}/artefacts")

    assert chez_bob.status_code == 404
    assert (chez_bob.status_code, chez_bob.json()) == (fil_invente.status_code, fil_invente.json())
    assert catalogue_de_bob.status_code == 404
    assert "plt.bar" not in chez_bob.text

    # et un nom inconnu DANS SON PROPRE fil rend le même code, sans dire lequel
    chez_soi = alice.get(f"/conversations/{fil_dalice}/artefacts/graphique_1")
    assert chez_soi.status_code == 404


# -- aucune route ne rend 403 sur un fil d'autrui ----------------------------


def test_aucun_acces_croise_ne_confirme_lexistence_du_fil(
    alice: TestClient, fil_de_bob: str
) -> None:
    """403 dirait « ça existe » ; 404 ne dit rien. Balayage de toutes les routes."""
    reponses = {
        "GET": alice.get(f"/conversations/{fil_de_bob}"),
        "DELETE": alice.delete(f"/conversations/{fil_de_bob}"),
        "DUPLICATE": alice.post(f"/conversations/{fil_de_bob}/duplicate"),
        "ARTEFACTS": alice.get(f"/conversations/{fil_de_bob}/artefacts"),
        "ARTEFACT": alice.get(f"/conversations/{fil_de_bob}/artefacts/graphique_1"),
    }

    assert {methode: r.status_code for methode, r in reponses.items()} == {
        "GET": 404,
        "DELETE": 404,
        "DUPLICATE": 404,
        "ARTEFACTS": 404,
        "ARTEFACT": 404,
    }
