"""Outillage des tests d'API derrière l'authentification.

Toutes les routes (sauf ``/health``) exigent une session : un client de test
doit donc se connecter comme le ferait un navigateur. Il n'y a pas de porte
dérobée « pour les tests » — une porte de test est une porte.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from data_analyst_agent.auth.accounts import AccountStore
from data_analyst_agent.config import Settings

# argon2 par défaut, c'est 64 Mio et 3 passes par empreinte : la CRÉATION des
# comptes de test est affaiblie pour ne pas les payer. La VÉRIFICATION, elle,
# reste celle de l'application — les paramètres sont dans l'empreinte.
HACHEUR_RAPIDE = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

LOGIN = "alice"

# TestClient parle en https : le cookie de session est `Secure` par défaut, et
# un client http le jetterait au lieu de le renvoyer. On teste donc bien la
# configuration par défaut, celle du service.
BASE_URL = "https://testserver"


def reglages_de_test(tmp_path: Path, **surcharges) -> Settings:
    """Réglages isolés : conversations, comptes et état d'authentification sous tmp_path."""
    defauts = {
        "workspace_dir": tmp_path / "workspaces",
        "auth_accounts_path": tmp_path / "users.yaml",
        "auth_state_dir": tmp_path / "auth",
    }
    return Settings(_env_file=None, **(defauts | surcharges))


def creer_compte(settings: Settings, login: str = LOGIN) -> str:
    """Ouvre un compte et rend son mot de passe, TIRÉ AU HASARD.

    Aucun mot de passe de test n'est écrit dans le dépôt : il ne peut donc pas
    se retrouver sur une instance en service.
    """
    mot_de_passe = secrets.token_urlsafe(16)
    AccountStore(settings.auth_accounts_path, hasher=HACHEUR_RAPIDE).create(login, mot_de_passe)
    return mot_de_passe


def connecter(
    client: TestClient, settings: Settings, login: str, mot_de_passe: str, **options
) -> httpx.Response:
    """Suit le parcours réel : la page pose le jeton, le formulaire le renvoie."""
    client.get("/login")
    return client.post(
        "/login",
        data={
            "login": login,
            "motdepasse": mot_de_passe,
            "csrf": client.cookies.get(settings.csrf_cookie_name, ""),
        },
        **options,
    )


def entete_csrf(client: TestClient, settings: Settings) -> dict[str, str]:
    """L'en-tête que la page ajoute aux requêtes qui modifient l'état."""
    return {"X-CSRF-Token": client.cookies.get(settings.csrf_cookie_name, "")}


def client_connecte(app, settings: Settings, mot_de_passe: str, login: str = LOGIN) -> TestClient:
    """Un TestClient qui a ouvert une session, comme le ferait un navigateur.

    L'en-tête anti-CSRF est posé une fois pour toutes sur le client : c'est
    exactement ce que fait la page, qui le relit du cookie et l'ajoute à chaque
    requête modifiant l'état.
    """
    client = TestClient(app, base_url=BASE_URL)
    connecter(client, settings, login, mot_de_passe)
    client.headers.update(entete_csrf(client, settings))
    return client
