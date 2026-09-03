"""L'API derrière l'authentification : accès, cookie, CSRF, force brute.

Chaque garde-fou a ici un test qui échoue si on le retire.
"""

import re

import pytest
from fastapi.testclient import TestClient

from data_analyst_agent.api.app import ECHEC_CONNEXION, create_app
from data_analyst_agent.auth.accounts import AccountStore
from data_analyst_agent.auth.sessions import SessionStore
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import ChatAnswer
from helpers.auth import (
    BASE_URL,
    HACHEUR_RAPIDE,
    LOGIN,
    client_connecte,
    connecter,
    creer_compte,
    entete_csrf,
    reglages_de_test,
)


class FakeOrchestrator:
    def ask(
        self, question, source=None, pending=None, conversation_id=None, workspace_root=None
    ) -> ChatAnswer:
        return ChatAnswer(answer="Il y a 3 femmes.")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return reglages_de_test(tmp_path)


@pytest.fixture
def app(settings: Settings):
    return create_app(orchestrator_factory=FakeOrchestrator, settings=settings)


@pytest.fixture
def anonyme(app) -> TestClient:
    """Un client qui n'a pas ouvert de session."""
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def mot_de_passe(settings: Settings) -> str:
    return creer_compte(settings)


@pytest.fixture
def connecte(app, settings: Settings, mot_de_passe: str) -> TestClient:
    return client_connecte(app, settings, mot_de_passe)


def jeton_du_formulaire(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]*)"', html).group(1)


# -- accès sans session -----------------------------------------------------------


ROUTES_FERMEES = [
    ("GET", "/"),
    ("GET", "/me"),
    ("POST", "/chat"),
    ("GET", "/conversations"),
    ("GET", "/conversations/quelconque"),
    ("POST", "/conversations/quelconque/duplicate"),
    ("DELETE", "/conversations/quelconque"),
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
]


@pytest.mark.parametrize(("methode", "route"), ROUTES_FERMEES)
def test_sans_session_lapi_repond_401(anonyme: TestClient, methode: str, route: str):
    """Aucune route applicative n'est atteignable sans session."""
    reponse = anonyme.request(methode, route, json={"message": "coucou"})

    assert reponse.status_code == 401
    assert "conversation" not in reponse.text  # rien du contenu ne fuit


def test_health_reste_ouverte(anonyme: TestClient):
    """La sonde de disponibilité n'a pas de session : la lui refuser ferait
    passer le service pour tombé."""
    reponse = anonyme.get("/health")

    assert reponse.status_code == 200
    assert reponse.json()["status"] == "ok"


def test_navigation_sans_session_mene_a_la_page_de_connexion(anonyme: TestClient):
    """Un navigateur demande du HTML : il reçoit la page, pas un JSON d'erreur."""
    reponse = anonyme.get("/", headers={"Accept": "text/html"}, follow_redirects=False)

    assert reponse.status_code == 302
    assert reponse.headers["location"] == "/login"

    page = anonyme.get("/login")
    assert page.status_code == 200
    assert "Se connecter" in page.text


def test_un_fetch_sans_session_recoit_401_et_pas_du_html(anonyme: TestClient):
    """Le script de la page attend du JSON : le rediriger lui servirait du HTML."""
    reponse = anonyme.get("/conversations", headers={"Accept": "application/json"})

    assert reponse.status_code == 401
    assert reponse.json()["detail"]


def test_page_de_connexion_accessible_sans_session(anonyme: TestClient):
    assert anonyme.get("/login").status_code == 200


# -- connexion --------------------------------------------------------------------


def test_connexion_reussie_ouvre_la_session(
    anonyme: TestClient, settings: Settings, mot_de_passe: str
):
    reponse = connecter(anonyme, settings, LOGIN, mot_de_passe, follow_redirects=False)

    assert reponse.status_code == 303  # le navigateur repasse en GET
    assert reponse.headers["location"] == "/"
    assert anonyme.get("/me").json() == {"login": LOGIN}


def test_connexion_deja_ouverte_renvoie_a_lapplication(connecte: TestClient):
    reponse = connecte.get("/login", follow_redirects=False)

    assert reponse.status_code == 302
    assert reponse.headers["location"] == "/"


def test_mauvais_mot_de_passe_refuse(anonyme: TestClient, settings: Settings, mot_de_passe: str):
    reponse = connecter(anonyme, settings, LOGIN, mot_de_passe + "-faux")

    assert reponse.status_code == 401
    assert ECHEC_CONNEXION in reponse.text
    assert anonyme.get("/conversations").status_code == 401  # aucune session ouverte


def test_compte_desactive_refuse(
    anonyme: TestClient, settings: Settings, mot_de_passe: str, tmp_path
):
    AccountStore(settings.auth_accounts_path, hasher=HACHEUR_RAPIDE).set_active(LOGIN, False)

    reponse = connecter(anonyme, settings, LOGIN, mot_de_passe)

    assert reponse.status_code == 401
    assert anonyme.get("/conversations").status_code == 401


def test_meme_message_pour_un_compte_inconnu_et_un_mauvais_mot_de_passe(
    anonyme: TestClient, settings: Settings, mot_de_passe: str
):
    """Non-énumération : la page ne dit pas si le compte existe."""
    inconnu = connecter(anonyme, settings, "jamais-vu", mot_de_passe)
    faux = connecter(anonyme, settings, LOGIN, mot_de_passe + "-faux")
    AccountStore(settings.auth_accounts_path, hasher=HACHEUR_RAPIDE).set_active(LOGIN, False)
    ferme = connecter(anonyme, settings, LOGIN, mot_de_passe)

    assert inconnu.status_code == faux.status_code == ferme.status_code == 401
    messages = {ECHEC_CONNEXION in r.text for r in (inconnu, faux, ferme)}
    assert messages == {True}


def test_le_mot_de_passe_ne_ressort_jamais_dans_la_reponse(
    anonyme: TestClient, settings: Settings, mot_de_passe: str
):
    reponse = connecter(anonyme, settings, LOGIN, mot_de_passe + "-faux")

    assert mot_de_passe not in reponse.text


# -- cookie de session ------------------------------------------------------------


def test_drapeaux_du_cookie_de_session(anonyme: TestClient, settings: Settings, mot_de_passe: str):
    reponse = connecter(anonyme, settings, LOGIN, mot_de_passe, follow_redirects=False)

    (cookie,) = [
        valeur
        for cle, valeur in reponse.headers.multi_items()
        if cle.lower() == "set-cookie" and valeur.startswith(settings.session_cookie_name)
    ]
    assert "HttpOnly" in cookie  # hors de portée d'un script, donc d'une injection
    assert "SameSite=lax" in cookie.replace("Lax", "lax")
    assert "Path=/" in cookie
    assert "Secure" in cookie  # défaut du service : le cookie ne part qu'en HTTPS


def test_secure_du_cookie_desactivable_pour_le_dev_local(tmp_path):
    """Le développement local est en http : `Secure` doit pouvoir tomber, par
    réglage explicite et jamais par défaut."""
    reglages = reglages_de_test(tmp_path, session_cookie_secure=False)
    mot_de_passe = creer_compte(reglages)
    client = TestClient(
        create_app(orchestrator_factory=FakeOrchestrator, settings=reglages),
        base_url="http://testserver",
    )

    reponse = connecter(client, reglages, LOGIN, mot_de_passe, follow_redirects=False)

    (cookie,) = [
        valeur
        for cle, valeur in reponse.headers.multi_items()
        if cle.lower() == "set-cookie" and valeur.startswith(reglages.session_cookie_name)
    ]
    assert "Secure" not in cookie
    assert client.get("/me").json() == {"login": LOGIN}  # et la session fonctionne en http


def test_identifiant_de_session_regenere_a_la_connexion(app, settings: Settings, mot_de_passe: str):
    """Fixation de session : un identifiant posé d'avance ne doit pas survivre à
    la connexion de la victime — sinon l'attaquant qui l'a posé la suit."""
    pose = "identifiant-pose-par-un-attaquant"
    client = TestClient(app, base_url=BASE_URL)
    client.get("/login")
    client.cookies.set(settings.session_cookie_name, pose, "testserver", "/")

    reponse = client.post(
        "/login",
        data={
            "login": LOGIN,
            "motdepasse": mot_de_passe,
            "csrf": client.cookies.get(settings.csrf_cookie_name, ""),
        },
        follow_redirects=False,
    )

    assert reponse.status_code == 303
    (cookie,) = [
        valeur
        for cle, valeur in reponse.headers.multi_items()
        if cle.lower() == "set-cookie" and valeur.startswith(settings.session_cookie_name)
    ]
    assert pose not in cookie  # une NOUVELLE session, pas celle qu'on avait posée
    sessions = SessionStore(
        settings.auth_state_dir, settings.session_idle_timeout, settings.session_absolute_timeout
    )
    assert sessions.resolve(pose) is None


def test_jeton_de_session_invente_refuse(anonyme: TestClient, settings: Settings):
    anonyme.cookies.set(settings.session_cookie_name, "un-jeton-que-je-viens-d-inventer")

    assert anonyme.get("/conversations").status_code == 401


# -- expiration et révocation -----------------------------------------------------


def test_session_expiree_refuse(tmp_path):
    """Une session dont le délai d'inactivité est passé ne vaut plus rien."""
    reglages = reglages_de_test(tmp_path, session_idle_timeout=0.0)
    mot_de_passe = creer_compte(reglages)
    client = TestClient(
        create_app(orchestrator_factory=FakeOrchestrator, settings=reglages), base_url=BASE_URL
    )

    connecter(client, reglages, LOGIN, mot_de_passe)

    assert client.get("/conversations").status_code == 401


def test_deconnexion_revoque_la_session(connecte: TestClient, settings: Settings):
    """La révocation est effective côté serveur : recopier le cookie ne sert à rien."""
    jeton = connecte.cookies[settings.session_cookie_name]

    assert connecte.post("/logout").status_code == 204

    assert connecte.get("/conversations").status_code == 401
    # même en reposant le cookie à la main : la session n'existe plus côté serveur
    connecte.cookies.set(settings.session_cookie_name, jeton)
    assert connecte.get("/conversations").status_code == 401


def test_deconnexion_efface_les_cookies(connecte: TestClient, settings: Settings):
    reponse = connecte.post("/logout")

    effaces = [
        valeur for cle, valeur in reponse.headers.multi_items() if cle.lower() == "set-cookie"
    ]
    assert any(settings.session_cookie_name in c for c in effaces)
    assert any(settings.csrf_cookie_name in c for c in effaces)


def test_sessions_revoquees_par_ladministration(connecte: TestClient, settings: Settings, tmp_path):
    """`manage_users.py disable` ferme les sessions ouvertes : l'onglet resté
    ouvert ne doit plus répondre."""
    sessions = SessionStore(
        settings.auth_state_dir, settings.session_idle_timeout, settings.session_absolute_timeout
    )

    assert sessions.revoke_login(LOGIN) == 1

    assert connecte.get("/conversations").status_code == 401


# -- CSRF -------------------------------------------------------------------------


ROUTES_MUTANTES = [
    ("POST", "/chat"),
    ("POST", "/conversations/quelconque/duplicate"),
    ("DELETE", "/conversations/quelconque"),
    ("POST", "/logout"),
]


@pytest.mark.parametrize(("methode", "route"), ROUTES_MUTANTES)
def test_requete_forgee_sans_jeton_refusee(
    app, settings: Settings, mot_de_passe: str, methode: str, route: str
):
    """LE cas du cookie : un autre site déclenche la requête, le cookie de
    session part tout seul. Sans le jeton, ça ne passe pas."""
    forge = TestClient(app, base_url=BASE_URL)
    connecter(forge, settings, LOGIN, mot_de_passe)  # session valide, PAS d'en-tête

    reponse = forge.request(methode, route, json={"message": "supprime tout"})

    assert reponse.status_code == 403
    assert "CSRF" in reponse.json()["detail"]


@pytest.mark.parametrize(("methode", "route"), ROUTES_MUTANTES)
def test_requete_avec_un_faux_jeton_refusee(
    app, settings: Settings, mot_de_passe: str, methode: str, route: str
):
    forge = TestClient(app, base_url=BASE_URL)
    connecter(forge, settings, LOGIN, mot_de_passe)

    reponse = forge.request(
        methode, route, json={"message": "?"}, headers={"X-CSRF-Token": "jeton-invente"}
    )

    assert reponse.status_code == 403


def test_requete_avec_le_bon_jeton_passe(connecte: TestClient):
    assert connecte.post("/chat", json={"message": "Combien de femmes ?"}).status_code == 200


def test_lecture_ne_demande_pas_de_jeton(app, settings: Settings, mot_de_passe: str):
    """Le CSRF vise ce qui modifie l'état : une lecture n'a pas à le porter."""
    lecteur = TestClient(app, base_url=BASE_URL)
    connecter(lecteur, settings, LOGIN, mot_de_passe)

    assert lecteur.get("/conversations").status_code == 200


def test_le_jeton_dune_autre_session_ne_vaut_rien(app, settings: Settings, mot_de_passe: str):
    """Le jeton est lié à LA session : celui du voisin ne sert pas."""
    creer_compte(settings, "bob")
    autre = client_connecte(app, settings, creer_compte(settings, "carol"), login="carol")
    victime = TestClient(app, base_url=BASE_URL)
    connecter(victime, settings, LOGIN, mot_de_passe)

    reponse = victime.post("/chat", json={"message": "?"}, headers=entete_csrf(autre, settings))

    assert reponse.status_code == 403


def test_formulaire_de_connexion_sans_jeton_refuse(anonyme: TestClient, mot_de_passe: str):
    """Le formulaire de connexion aussi : sans le double envoi, un site tiers
    pourrait connecter la victime sur un compte qu'il contrôle."""
    reponse = anonyme.post("/login", data={"login": LOGIN, "motdepasse": mot_de_passe})

    assert reponse.status_code == 403
    assert anonyme.get("/conversations").status_code == 401


def test_formulaire_de_connexion_avec_un_jeton_qui_ne_vient_pas_du_cookie(
    anonyme: TestClient, mot_de_passe: str
):
    anonyme.get("/login")

    reponse = anonyme.post(
        "/login", data={"login": LOGIN, "motdepasse": mot_de_passe, "csrf": "jeton-invente"}
    )

    assert reponse.status_code == 403


# -- anti-force brute -------------------------------------------------------------


def test_verrouillage_apres_n_echecs(tmp_path):
    reglages = reglages_de_test(tmp_path, login_max_failures=3)
    mot_de_passe = creer_compte(reglages)
    client = TestClient(
        create_app(orchestrator_factory=FakeOrchestrator, settings=reglages), base_url=BASE_URL
    )

    for _ in range(reglages.login_max_failures):
        assert connecter(client, reglages, LOGIN, mot_de_passe + "-faux").status_code == 401

    # le BON mot de passe ne passe plus : c'est ce qui arrête un dictionnaire
    refus = connecter(client, reglages, LOGIN, mot_de_passe)
    assert refus.status_code == 429
    assert client.get("/conversations").status_code == 401


def test_verrouillage_par_adresse_sur_dautres_comptes(tmp_path):
    """Un balayage de logins depuis une machine doit se heurter au même mur."""
    reglages = reglages_de_test(tmp_path, login_max_failures=3)
    creer_compte(reglages, "bob")
    client = TestClient(
        create_app(orchestrator_factory=FakeOrchestrator, settings=reglages), base_url=BASE_URL
    )

    for i in range(reglages.login_max_failures):
        connecter(client, reglages, f"victime-{i}", "un-mot-de-passe-quelconque")

    assert connecter(client, reglages, "bob", "peu-importe").status_code == 429


# L'EXPIRATION du verrou n'est pas testée ici : elle demande de faire avancer
# l'horloge, ce que `tests/unit/auth/test_throttle.py` fait avec une horloge
# pilotée. La reproduire à travers l'API n'ajouterait qu'un `sleep` fragile.


def test_connexion_reussie_efface_le_compteur(tmp_path):
    reglages = reglages_de_test(tmp_path, login_max_failures=3)
    mot_de_passe = creer_compte(reglages)
    client = TestClient(
        create_app(orchestrator_factory=FakeOrchestrator, settings=reglages), base_url=BASE_URL
    )
    for _ in range(reglages.login_max_failures - 1):
        connecter(client, reglages, LOGIN, mot_de_passe + "-faux")

    assert (
        connecter(client, reglages, LOGIN, mot_de_passe, follow_redirects=False).status_code == 303
    )

    client.post("/logout", headers=entete_csrf(client, reglages))
    for _ in range(reglages.login_max_failures - 1):
        connecter(client, reglages, LOGIN, mot_de_passe + "-faux")
    # le compteur est reparti de zéro : on n'est pas verrouillé
    assert (
        connecter(client, reglages, LOGIN, mot_de_passe, follow_redirects=False).status_code == 303
    )


# -- page de connexion ------------------------------------------------------------


def test_la_page_porte_un_jeton_et_ne_se_met_pas_en_cache(anonyme: TestClient):
    reponse = anonyme.get("/login")

    assert jeton_du_formulaire(reponse.text)
    assert reponse.headers["cache-control"] == "no-store"


def test_deux_pages_de_connexion_portent_des_jetons_differents(anonyme: TestClient):
    premier = jeton_du_formulaire(anonyme.get("/login").text)
    second = jeton_du_formulaire(anonyme.get("/login").text)

    assert premier != second


def test_la_page_de_chat_affiche_le_compte_connecte(connecte: TestClient):
    page = connecte.get("/").text

    assert LOGIN in page
    assert "Se déconnecter" in page
