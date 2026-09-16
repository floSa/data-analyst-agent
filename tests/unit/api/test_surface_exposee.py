"""Ce que l'API expose, et ce qu'elle refuse (audit §6.3).

Trois trous mesurés : la documentation interactive publiait la surface d'attaque
(et chargeait ses assets d'un CDN, contre la contrainte réseau coupé), `message`
n'avait aucune borne, et rien ne limitait le débit — alors qu'un `POST /chat`
déclenche jusqu'à onze appels LLM et un conteneur Docker.
"""

import pytest
from fastapi.testclient import TestClient

from data_analyst_agent.api.app import create_app
from data_analyst_agent.auth.rate_limit import RateLimiter
from data_analyst_agent.orchestrator.graph import ChatAnswer
from helpers.auth import client_connecte, creer_compte, reglages_de_test


class OrchestrateurMuet:
    def ask(
        self,
        question,
        source=None,
        pending=None,
        conversation_id=None,
        workspace_root=None,
        source_de_travail=None,
        echange_precedent=None,
    ):
        return ChatAnswer(answer="ok")


def client_pour(tmp_path, **surcharges) -> TestClient:
    settings = reglages_de_test(tmp_path, **surcharges)
    mot_de_passe = creer_compte(settings)
    app = create_app(orchestrator_factory=OrchestrateurMuet, settings=settings)
    return client_connecte(app, settings, mot_de_passe)


# -- documentation interactive -------------------------------------------------


@pytest.mark.parametrize("route", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_la_documentation_est_eteinte_par_defaut(tmp_path, route: str):
    """Le défaut est celui d'un service exposé. /openapi.json compte : sans lui,
    éteindre /docs ne cache rien — le schéma complet reste téléchargeable."""
    assert client_pour(tmp_path).get(route).status_code == 404


@pytest.mark.parametrize("route", ["/docs", "/redoc", "/openapi.json"])
def test_la_documentation_se_rallume_par_reglage(tmp_path, route: str):
    """Pour développer, et par décision explicite — jamais par défaut."""
    client = client_pour(tmp_path, api_docs_enabled=True)

    assert client.get(route).status_code == 200


def test_les_routes_applicatives_restent_servies_sans_documentation(tmp_path):
    assert client_pour(tmp_path).get("/health").status_code == 200


# -- taille des entrées --------------------------------------------------------


def test_une_question_trop_longue_est_refusee(tmp_path):
    client = client_pour(tmp_path, chat_message_max_chars=100)

    reponse = client.post("/chat", json={"message": "a" * 101})

    assert reponse.status_code == 413
    assert "trop long" in reponse.json()["detail"]


def test_une_question_a_la_limite_passe(tmp_path):
    client = client_pour(tmp_path, chat_message_max_chars=100)

    assert client.post("/chat", json={"message": "a" * 100}).status_code == 200


def test_un_corps_demesure_est_refuse_avant_detre_lu(tmp_path):
    """Le contrôle porte sur `Content-Length` : rien n'est parsé."""
    client = client_pour(tmp_path, api_max_body_bytes=200)

    reponse = client.post("/chat", json={"message": "a" * 5000})

    assert reponse.status_code == 413
    assert reponse.json()["detail"] == "corps de requête trop volumineux"


def test_le_plafond_de_corps_vaut_aussi_pour_la_porte_ouverte(tmp_path):
    """`/login` n'exige pas de session : c'est justement là qu'un flot arrive."""
    settings = reglages_de_test(tmp_path, api_max_body_bytes=50)
    creer_compte(settings)
    client = TestClient(create_app(settings=settings), base_url="https://testserver")

    reponse = client.post("/login", data={"login": "a" * 200, "motdepasse": "x", "csrf": "y"})

    assert reponse.status_code == 413


# -- débit ---------------------------------------------------------------------


def test_le_debit_de_chat_est_plafonne(tmp_path):
    client = client_pour(tmp_path, chat_rate_limit_requests=3)

    codes = [client.post("/chat", json={"message": "combien ?"}).status_code for _ in range(4)]

    assert codes == [200, 200, 200, 429]


def test_le_refus_dit_quand_revenir(tmp_path):
    client = client_pour(tmp_path, chat_rate_limit_requests=1, chat_rate_limit_window=60.0)
    client.post("/chat", json={"message": "combien ?"})

    reponse = client.post("/chat", json={"message": "et encore ?"})

    assert reponse.status_code == 429
    assert 0 < int(reponse.headers["Retry-After"]) <= 61


def test_le_quota_est_par_compte(tmp_path):
    """Sinon le premier utilisateur venu ferme la porte à tous les autres."""
    settings = reglages_de_test(tmp_path, chat_rate_limit_requests=1)
    mdp_alice = creer_compte(settings, login="alice")
    mdp_bob = creer_compte(settings, login="bob")
    app = create_app(orchestrator_factory=OrchestrateurMuet, settings=settings)
    alice = client_connecte(app, settings, mdp_alice, login="alice")
    bob = client_connecte(app, settings, mdp_bob, login="bob")

    assert alice.post("/chat", json={"message": "q"}).status_code == 200
    assert alice.post("/chat", json={"message": "q"}).status_code == 429
    assert bob.post("/chat", json={"message": "q"}).status_code == 200


# -- le limiteur lui-même ------------------------------------------------------


def test_la_fenetre_glisse(tmp_path):
    """Un compteur remis à zéro à la minute civile laisse passer deux fois le
    quota à cheval sur la minute : la fenêtre glisse, elle ne se réinitialise pas."""
    maintenant = [1000.0]
    limiteur = RateLimiter(tmp_path, 2, 60.0, clock=lambda: maintenant[0])

    assert limiteur.hit("alice") is None
    assert limiteur.hit("alice") is None
    assert limiteur.hit("alice") is not None

    maintenant[0] += 61.0
    assert limiteur.hit("alice") is None


def test_un_refus_ne_repousse_pas_lecheance(tmp_path):
    """Sinon un client qui insiste se bannirait lui-même indéfiniment."""
    maintenant = [1000.0]
    limiteur = RateLimiter(tmp_path, 1, 10.0, clock=lambda: maintenant[0])
    limiteur.hit("alice")

    maintenant[0] += 5.0
    limiteur.hit("alice")  # refusée : ne doit pas être comptée
    maintenant[0] += 5.5  # 10,5 s après la SEULE requête comptée

    assert limiteur.hit("alice") is None


def test_lattente_annoncee_est_celle_qui_reste(tmp_path):
    maintenant = [1000.0]
    limiteur = RateLimiter(tmp_path, 1, 60.0, clock=lambda: maintenant[0])
    limiteur.hit("alice")

    maintenant[0] += 20.0

    assert limiteur.hit("alice") == pytest.approx(40.0)


def test_quota_a_zero_ne_limite_rien(tmp_path):
    limiteur = RateLimiter(tmp_path, 0, 60.0)

    assert all(limiteur.hit("alice") is None for _ in range(50))


def test_un_fichier_detat_illisible_ne_bloque_personne(tmp_path):
    """Un état corrompu ne doit pas fermer le service : il se repart à zéro."""
    limiteur = RateLimiter(tmp_path, 1, 60.0)
    limiteur.hit("alice")
    limiteur.path.write_text("ceci n'est pas du JSON", encoding="utf-8")

    assert limiteur.hit("alice") is None


def test_letat_ne_grandit_pas_indefiniment(tmp_path):
    """Les horodatages sortis de la fenêtre sont purgés à chaque passage."""
    maintenant = [1000.0]
    limiteur = RateLimiter(tmp_path, 10, 10.0, clock=lambda: maintenant[0])
    for compte in range(20):
        limiteur.hit(f"compte-{compte}")

    maintenant[0] += 11.0
    limiteur.hit("alice")

    assert list(limiteur._read()) == ["alice"]
