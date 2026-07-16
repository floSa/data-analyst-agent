"""La page de chat pilotée par un vrai navigateur (Playwright, chromium headless).

Ces tests existent parce que l'API et la syntaxe du JS ne prouvent RIEN sur ce
que l'utilisateur voit. Cas réel : toutes les réponses d'une conversation
rouverte s'affichaient « (pas de réponse) » — la page lisait `answer` là où un
message relu porte `content` — alors que /conversations renvoyait le bon texte,
que les tableaux s'affichaient à côté, et que le script était valide.

Le serveur est un vrai uvicorn sur un port libre ; l'orchestrateur est doublé
(aucun LLM, aucun Docker), et le magasin est pré-rempli sur disque.
"""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from data_analyst_agent.api.app import create_app
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.graph import ChatAnswer
from data_analyst_agent.sandbox.client import MimeOutput

# 1x1 PNG transparent
PNG_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
TABLE_JSON = '{"columns": ["sex", "n"], "rows": [["female", 314], ["male", 577]]}'

# Processus pytest séparé : cf. le marqueur `ui` dans pyproject.toml.
pytestmark = pytest.mark.ui


class FakeOrchestrator:
    def ask(self, question, source=None, pending=None, conversation_id=None) -> ChatAnswer:
        return ChatAnswer(
            answer="Il y a 891 passagers.",
            artifacts=[MimeOutput(mime="application/json", data=TABLE_JSON)],
        )


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def app_url(tmp_path: Path):
    """Un uvicorn réel, sur un magasin de conversations pré-rempli."""
    settings = Settings(_env_file=None, workspace_dir=tmp_path / "workspaces")
    store = ConversationStore(settings.workspace_dir)
    conversation = store.create()
    store.record_turn(
        conversation.id,
        question="sur titanic, combien de passagers au total ?",
        answer="Il y a un total de 891 passagers.",
        artifacts=[
            MimeOutput(mime="application/json", data=TABLE_JSON),
            MimeOutput(mime="image/png", data=PNG_1x1),
        ],
    )
    store.record_turn(
        conversation.id,
        question="et le passager 999999 ?",
        answer="Je n'ai pas pu répondre : aucune ligne récupérée",
        error="aucune ligne récupérée",
    )

    app = create_app(orchestrator_factory=FakeOrchestrator, settings=settings)
    port = _port_libre()
    serveur = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()
    for _ in range(100):
        if serveur.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    serveur.should_exit = True
    fil.join(timeout=5)


def test_reouvrir_une_conversation_affiche_le_texte_des_reponses(page, app_url: str):
    """LE bug : « (pas de réponse) » à la place de chaque réponse relue."""
    page.goto(app_url)
    page.click(".fil-titre")

    page.wait_for_selector(".message.agent")
    assert "Il y a un total de 891 passagers." in page.inner_text("#journal")
    assert "(pas de réponse)" not in page.inner_text("#journal")


def test_reouvrir_affiche_aussi_questions_tableaux_figures_et_erreurs(page, app_url: str):
    page.goto(app_url)
    page.click(".fil-titre")
    page.wait_for_selector(".message.agent")

    journal = page.inner_text("#journal")
    assert "sur titanic, combien de passagers au total ?" in journal  # la question
    assert "female" in journal and "314" in journal  # le tableau
    assert "aucune ligne récupérée" in journal  # l'erreur
    assert page.locator("#journal img").count() == 1  # la figure


def test_barre_laterale_liste_la_conversation(page, app_url: str):
    page.goto(app_url)
    page.wait_for_selector(".fil-titre")

    assert page.locator(".fil-titre").count() == 1
    assert "combien de passagers" in page.inner_text(".fil-titre")


def test_envoyer_un_message_affiche_la_reponse(page, app_url: str):
    """Le chemin live doit rester bon : c'est la même fonction de rendu."""
    page.goto(app_url)
    page.click("#nouvelle")
    page.fill("#message", "combien de passagers ?")
    page.click("#envoyer")

    page.wait_for_selector(".message.agent")
    journal = page.inner_text("#journal")
    assert "Il y a 891 passagers." in journal
    assert "(pas de réponse)" not in journal


def test_nouvelle_conversation_vide_le_journal(page, app_url: str):
    page.goto(app_url)
    page.click(".fil-titre")
    page.wait_for_selector(".message.agent")

    page.click("#nouvelle")
    assert page.inner_text("#journal").strip() == ""


def test_supprimer_une_conversation_la_retire_de_la_barre(page, app_url: str):
    page.goto(app_url)
    page.wait_for_selector(".fil-titre")
    page.on("dialog", lambda dialogue: dialogue.accept())

    page.click(".fil-action[title='Supprimer']")

    page.wait_for_selector(".vide")
    assert page.locator(".fil-titre").count() == 0


def test_dupliquer_une_conversation_lajoute_et_louvre(page, app_url: str):
    page.goto(app_url)
    page.wait_for_selector(".fil-titre")

    page.click(".fil-action[title='Dupliquer']")

    page.wait_for_function("document.querySelectorAll('.fil-titre').length === 2")
    assert "(copie)" in page.inner_text("#fils")
    # la copie est ouverte, et son texte s'affiche (pas « (pas de réponse) »)
    assert "Il y a un total de 891 passagers." in page.inner_text("#journal")
