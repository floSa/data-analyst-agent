"""Test de la dépendance qui publie l'utilisateur courant aux routes."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from data_analyst_agent.auth.current_user import CurrentUser, current_user


def requete(**etat) -> Request:
    return Request({"type": "http", "headers": [], "state": etat})


def test_rend_lutilisateur_pose_par_le_middleware():
    assert current_user(requete(user=CurrentUser(login="alice"))).login == "alice"


def test_echoue_ferme_si_la_garde_na_pas_tourne():
    """Filet : une route ajoutée hors du chemin gardé doit refuser, pas servir."""
    with pytest.raises(HTTPException) as refus:
        current_user(requete())

    assert refus.value.status_code == 401
