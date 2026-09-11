"""La page de chat pilotée par un vrai navigateur (Playwright, chromium headless).

Ces tests existent parce que l'API et la syntaxe du JS ne prouvent RIEN sur ce
que l'utilisateur voit. Cas réel : toutes les réponses d'une conversation
rouverte s'affichaient « (pas de réponse) » — la page lisait `answer` là où un
message relu porte `content` — alors que /conversations renvoyait le bon texte,
que les tableaux s'affichaient à côté, et que le script était valide.

Le serveur est un vrai uvicorn sur un port libre ; l'orchestrateur est doublé
(aucun LLM, aucun Docker), et le magasin est pré-rempli sur disque.
"""

import contextlib
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from argon2 import PasswordHasher

from data_analyst_agent.api.app import create_app
from data_analyst_agent.auth.accounts import AccountStore
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.graph import ChatAnswer, SourceDuCatalogue
from data_analyst_agent.sandbox.client import MimeOutput

# 1x1 PNG transparent
PNG_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="  # noqa: E501
TABLE_JSON = '{"columns": ["sex", "n"], "rows": [["female", 314], ["male", 577]]}'

# Processus pytest séparé : cf. le marqueur `ui` dans pyproject.toml.
pytestmark = pytest.mark.ui

# La page est derrière l'authentification : chaque test ouvre une session par le
# formulaire, comme un utilisateur. Le mot de passe est tiré au hasard à chaque
# exécution — aucun mot de passe de test n'existe dans le dépôt.
LOGIN = "alice"
MOT_DE_PASSE = secrets.token_urlsafe(16)
# argon2 par défaut, c'est 64 Mio par empreinte : inutile de les payer ici.
HACHEUR_RAPIDE = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class FakeOrchestrator:
    """L'orchestrateur doublé : aucun LLM, aucun Docker, aucune source ouverte.

    Il porte tout de même un catalogue — deux sources avec leurs faits « lus » —
    parce que la page en a besoin pour peupler son indicateur de source de
    travail, et que ce que l'indicateur affiche est justement ce qu'on veut
    voir dans un navigateur.
    """

    SOURCES = (
        SourceDuCatalogue(
            name="titanic",
            type="postgres",
            description="Passagers du Titanic.",
            faits="2 table(s), 894 ligne(s) (passengers : 891, classes : 3)",
        ),
        SourceDuCatalogue(
            name="iris",
            type="file",
            description="Mesures florales.",
            faits="1 table(s), 150 ligne(s) (iris : 150)",
        ),
    )

    def ask(
        self,
        question,
        source=None,
        pending=None,
        conversation_id=None,
        workspace_root=None,
        source_de_travail=None,
    ) -> ChatAnswer:
        return ChatAnswer(
            answer="Il y a 891 passagers.",
            artifacts=[MimeOutput(mime="application/json", data=TABLE_JSON)],
            source_de_travail=source_de_travail,
        )

    def inventaire_des_sources(self) -> list[SourceDuCatalogue]:
        return list(self.SOURCES)

    def source_declaree(self, nom: str) -> bool:
        return any(s.name == nom for s in self.SOURCES)

    def accuser_la_source(self, nom: str, precedente: str = "") -> str:
        quittee = f" (on travaillait sur `{precedente}`)" if precedente else ""
        return f"Entendu : on travaille sur **{nom}**{quittee}."


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _reglages(tmp_path: Path, dossier: str) -> Settings:
    """Réglages isolés, avec un compte prêt à ouvrir une session."""
    settings = Settings(
        _env_file=None,
        workspace_dir=tmp_path / dossier,
        auth_accounts_path=tmp_path / "users.yaml",
        auth_state_dir=tmp_path / "auth",
        # Le serveur de test écoute en http sur 127.0.0.1 : un cookie `Secure`
        # serait jeté par le navigateur. C'est exactement le cas que ce réglage
        # existe pour couvrir — le développement local.
        session_cookie_secure=False,
    )
    AccountStore(settings.auth_accounts_path, hasher=HACHEUR_RAPIDE).create(LOGIN, MOT_DE_PASSE)
    return settings


@contextlib.contextmanager
def _servir(settings: Settings) -> Iterator[str]:
    """Un uvicorn réel sur un port libre, arrêté à la sortie."""
    app = create_app(orchestrator_factory=FakeOrchestrator, settings=settings)
    port = _port_libre()
    serveur = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()
    for _ in range(100):
        if serveur.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        serveur.should_exit = True
        fil.join(timeout=5)


def connexion(page, base_url: str) -> None:
    """Ouvre une session par le formulaire, puis atterrit sur la page de chat."""
    page.goto(f"{base_url}/login")
    page.fill("#login", LOGIN)
    page.fill("#motdepasse", MOT_DE_PASSE)
    page.click("#connexion")
    page.wait_for_selector("#journal")


@pytest.fixture
def app_url(tmp_path: Path):
    """Un uvicorn réel, sur un magasin de conversations pré-rempli."""
    settings = _reglages(tmp_path, "workspaces")
    store = ConversationStore(settings.workspace_dir, LOGIN)
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

    with _servir(settings) as url:
        yield url


def test_reouvrir_une_conversation_affiche_le_texte_des_reponses(page, app_url: str):
    """LE bug : « (pas de réponse) » à la place de chaque réponse relue."""
    connexion(page, app_url)
    page.click(".fil-titre")

    page.wait_for_selector(".message.agent")
    assert "Il y a un total de 891 passagers." in page.inner_text("#journal")
    assert "(pas de réponse)" not in page.inner_text("#journal")


def test_reouvrir_affiche_aussi_questions_tableaux_figures_et_erreurs(page, app_url: str):
    connexion(page, app_url)
    page.click(".fil-titre")
    page.wait_for_selector(".message.agent")

    journal = page.inner_text("#journal")
    assert "sur titanic, combien de passagers au total ?" in journal  # la question
    assert "female" in journal  # le tableau
    assert "314" in journal
    assert "aucune ligne récupérée" in journal  # l'erreur
    assert page.locator("#journal img").count() == 1  # la figure


def test_barre_laterale_liste_la_conversation(page, app_url: str):
    connexion(page, app_url)
    page.wait_for_selector(".fil-titre")

    assert page.locator(".fil-titre").count() == 1
    assert "combien de passagers" in page.inner_text(".fil-titre")


def test_envoyer_un_message_affiche_la_reponse(page, app_url: str):
    """Le chemin live doit rester bon : c'est la même fonction de rendu."""
    connexion(page, app_url)
    page.click("#nouvelle")
    page.fill("#message", "combien de passagers ?")
    page.click("#envoyer")

    page.wait_for_selector(".message.agent")
    journal = page.inner_text("#journal")
    assert "Il y a 891 passagers." in journal
    assert "(pas de réponse)" not in journal


def test_nouvelle_conversation_vide_le_journal(page, app_url: str):
    connexion(page, app_url)
    page.click(".fil-titre")
    page.wait_for_selector(".message.agent")

    page.click("#nouvelle")
    assert page.inner_text("#journal").strip() == ""


def test_supprimer_une_conversation_la_retire_de_la_barre(page, app_url: str):
    connexion(page, app_url)
    page.wait_for_selector(".fil-titre")
    page.on("dialog", lambda dialogue: dialogue.accept())

    page.click(".fil-action[title='Supprimer']")

    page.wait_for_selector(".vide")
    assert page.locator(".fil-titre").count() == 0


def test_dupliquer_une_conversation_lajoute_et_louvre(page, app_url: str):
    connexion(page, app_url)
    page.wait_for_selector(".fil-titre")

    page.click(".fil-action[title='Dupliquer']")

    page.wait_for_function("document.querySelectorAll('.fil-titre').length === 2")
    assert "(copie)" in page.inner_text("#fils")
    # la copie est ouverte, et son texte s'affiche (pas « (pas de réponse) »)
    assert "Il y a un total de 891 passagers." in page.inner_text("#journal")


# --- rendu markdown des réponses -------------------------------------------------


@pytest.fixture
def url_markdown(tmp_path: Path):
    """Un fil dont la réponse est du markdown, comme le LLM en produit vraiment."""
    settings = _reglages(tmp_path, "md")
    store = ConversationStore(settings.workspace_dir, LOGIN)
    c = store.create()
    store.record_turn(
        c.id,
        question="de quels attributs as-tu besoin ?",
        answer=(
            "Voici les attributs disponibles :\n"
            "\n"
            "**Table `passengers` :**\n"
            "*   **Démographie :** `sex` (texte), `age` (nombre décimal).\n"
            "*   **Finances :** `fare` (nombre décimal).\n"
            "\n"
            "Tous les attributs sont présents."
        ),
    )
    with _servir(settings) as url:
        yield url


def test_markdown_rendu_pas_affiche_en_brut(page, url_markdown: str):
    """Les ** et les puces doivent devenir du gras et une liste, pas du texte."""
    connexion(page, url_markdown)
    page.click(".fil-titre")
    page.wait_for_selector(".message.agent")

    journal = page.inner_text("#journal")
    assert "**" not in journal  # plus de balisage brut à l'écran
    assert "*   " not in journal
    assert page.locator(".agent strong").count() >= 2  # le gras est rendu
    assert page.locator(".agent li").count() == 2  # les puces sont une liste
    assert page.locator(".agent code").count() >= 3  # `sex`, `age`, `fare`
    assert "Démographie" in journal  # le contenu, lui, est intact


def test_html_dans_la_reponse_est_echappe_pas_execute(page, tmp_path: Path):
    """Le texte vient d'un LLM nourri de données : du HTML doit s'AFFICHER, jamais
    s'exécuter. Le rendu markdown ne doit pas ouvrir une porte d'injection."""
    settings = _reglages(tmp_path, "xss")
    store = ConversationStore(settings.workspace_dir, LOGIN)
    c = store.create()
    store.record_turn(
        c.id,
        question="et ça ?",
        answer='Attention <img src=x onerror="window.__pwn=1"> et <script>window.__pwn=2</script>',
    )
    with _servir(settings) as url:
        connexion(page, url)
        page.click(".fil-titre")
        page.wait_for_selector(".message.agent")

        assert page.evaluate("window.__pwn === undefined")  # rien n'a été exécuté
        assert page.locator("#journal img").count() == 0  # la balise n'est pas devenue une image
        assert "<img src=x" in page.inner_text("#journal")  # elle est affichée telle quelle


# --- connexion, dans un vrai navigateur -------------------------------------------


@pytest.fixture
def url_nue(tmp_path: Path):
    """Un serveur avec un compte, mais aucune conversation."""
    with _servir(_reglages(tmp_path, "nu")) as url:
        yield url


def test_sans_session_le_navigateur_arrive_sur_la_page_de_connexion(page, url_nue: str):
    """Ce que voit un visiteur : le formulaire, pas la page de chat ni un JSON."""
    page.goto(url_nue)

    page.wait_for_selector("#connexion")
    assert page.locator("#journal").count() == 0
    assert "Se connecter" in page.inner_text("form")


def test_mauvais_mot_de_passe_reaffiche_le_formulaire_avec_une_erreur(page, url_nue: str):
    page.goto(f"{url_nue}/login")
    page.fill("#login", LOGIN)
    page.fill("#motdepasse", MOT_DE_PASSE + "-faux")
    page.click("#connexion")

    page.wait_for_selector(".erreur")
    assert "Identifiants invalides" in page.inner_text(".erreur")
    assert page.locator("#journal").count() == 0  # toujours dehors


def test_connexion_puis_deconnexion(page, url_nue: str):
    """La déconnexion ramène au formulaire, et revenir n'y change rien."""
    connexion(page, url_nue)
    assert LOGIN in page.inner_text("#compte")

    page.click("#deconnexion")

    page.wait_for_selector("#connexion")
    page.goto(url_nue)
    page.wait_for_selector("#connexion")  # la session est bien fermée côté serveur


# --- l'indicateur de source de travail ----------------------------------------


def test_l_indicateur_liste_les_sources_et_ce_qu_on_y_a_lu(page, app_url: str):
    """L'indicateur est permanent, et son menu porte le catalogue réel.

    Pas seulement des noms : le volume lu dans chaque source est là aussi, en
    infobulle, parce que c'est sur ce texte qu'on choisit.
    """
    connexion(page, app_url)
    page.wait_for_selector("#choix-de-source option[value='titanic']", state="attached")

    assert page.locator("#choix-de-source option").count() == 3  # « aucune » + deux sources
    assert "aucune" in page.locator("#choix-de-source").inner_text()
    titre = page.get_attribute("#choix-de-source option[value='titanic']", "title")
    assert "891" in titre


def test_changer_de_source_dans_le_menu_lie_le_fil_et_l_inscrit_dedans(page, app_url: str):
    """« Un moyen d'en changer sans le taper », et la trace que ça laisse.

    Le changement est écrit dans la transcription : relire un fil dont les
    réponses changent de données sans que rien ne le dise serait exactement ce
    que la bascule annoncée évite.
    """
    connexion(page, app_url)
    page.wait_for_selector("#choix-de-source option[value='iris']", state="attached")
    page.click("#nouvelle")

    page.select_option("#choix-de-source", "iris")

    page.wait_for_selector(".message.agent")
    assert "on travaille sur" in page.inner_text("#journal")
    assert "iris" in page.inner_text("#journal")
    assert page.input_value("#choix-de-source") == "iris"
    assert "150" in page.inner_text("#faits-de-source")


def test_l_indicateur_suit_le_fil_qu_on_rouvre(page, app_url: str):
    """La source vient du FIL : rouvrir une conversation la réaffiche, et une
    conversation neuve repart sur « aucune »."""
    connexion(page, app_url)
    page.wait_for_selector("#choix-de-source option[value='iris']", state="attached")
    page.click("#nouvelle")
    page.select_option("#choix-de-source", "titanic")
    page.wait_for_selector(".message.agent")

    page.click("#nouvelle")
    assert page.input_value("#choix-de-source") == ""

    page.click(".fil-titre")
    page.wait_for_selector(".message.agent")
    assert page.input_value("#choix-de-source") == "titanic"
