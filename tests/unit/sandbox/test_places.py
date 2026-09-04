"""Plafond du nombre de conteneurs sandbox vivants en même temps (audit §2.3).

Borner la mémoire *d'un* conteneur ne dit rien de *combien* il y en a. Dix
analyses simultanées réservaient dix gigaoctets sans que rien ne s'y oppose.
"""

import sys
from pathlib import Path

import pytest

from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import (
    SandboxError,
    SandboxPlaces,
    SandboxSession,
    places_partagees,
)

FAKE_BRIDGE = Path(__file__).parents[2] / "fakes" / "fake_bridge.py"


def make_settings(**overrides) -> Settings:
    defaults = {
        "sandbox_start_timeout": 10.0,
        "sandbox_exec_timeout": 5.0,
        "sandbox_kill_grace": 2.0,
        "sandbox_queue_timeout": 0.2,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def fake_session(places: SandboxPlaces, **settings_overrides) -> SandboxSession:
    return SandboxSession(
        settings=make_settings(**settings_overrides),
        command=[sys.executable, str(FAKE_BRIDGE)],
        places=places,
    )


# -- le compteur lui-même ------------------------------------------------------


def test_le_plafond_est_respecte():
    places = SandboxPlaces(2)

    places.acquerir(0.1)
    places.acquerir(0.1)

    with pytest.raises(SandboxError) as refus:
        places.acquerir(0.1)
    assert "2 en parallèle" in str(refus.value)


def test_une_place_rendue_debloque_le_suivant():
    places = SandboxPlaces(1)
    places.acquerir(0.1)

    places.liberer()

    places.acquerir(0.1)  # ne lève pas : la place est de nouveau libre


def test_le_refus_est_explicite_et_borne_dans_le_temps():
    """Un refus net vaut mieux qu'un onglet qui tourne : l'attente est plafonnée."""
    places = SandboxPlaces(1)
    places.acquerir(0.1)

    with pytest.raises(SandboxError) as refus:
        places.acquerir(0.05)

    assert "occupées" in str(refus.value)
    assert "0.05 s" in str(refus.value)


def test_plafond_a_zero_ne_plafonne_rien():
    """Échappatoire assumée : 0 = pas de compteur, pour qui sait ce qu'il fait."""
    places = SandboxPlaces(0)

    for _ in range(50):
        places.acquerir(0.01)


def test_le_compteur_du_process_est_partage():
    settings = make_settings(sandbox_max_sessions=3)

    assert places_partagees(settings) is places_partagees(settings)
    assert places_partagees(settings).limite == 3


def test_le_compteur_suit_le_plafond_configure():
    """Refabriqué quand le réglage change — ce qui n'arrive qu'entre deux tests."""
    premier = places_partagees(make_settings(sandbox_max_sessions=3))

    second = places_partagees(make_settings(sandbox_max_sessions=5))

    assert second is not premier
    assert second.limite == 5


# -- la session prend et rend sa place ----------------------------------------


def test_une_session_occupe_une_place_puis_la_rend():
    places = SandboxPlaces(1)

    # tant que la première vit, la seconde n'a pas de place
    with fake_session(places), pytest.raises(SandboxError, match="occupées"):
        fake_session(places).start()

    # la première fermée, la place est de nouveau libre
    with fake_session(places) as session:
        assert session.ping() is True


def test_une_double_fermeture_ne_rend_pas_la_place_deux_fois():
    """Sinon le compteur enflerait au-delà du plafond, tour après tour."""
    places = SandboxPlaces(1)
    session = fake_session(places)
    session.start()

    session.close()
    session.close()

    places.acquerir(0.1)
    with pytest.raises(SandboxError):
        places.acquerir(0.05)  # une seule place existe, elle est prise


def test_un_demarrage_rate_ne_retient_pas_la_place():
    """Une commande introuvable ne doit pas amputer le compteur définitivement."""
    places = SandboxPlaces(1)
    session = SandboxSession(
        settings=make_settings(),
        command=["binaire-qui-nexiste-pas"],
        places=places,
    )

    with pytest.raises(SandboxError):
        session.start()

    places.acquerir(0.1)  # la place est restée disponible


def test_un_bridge_qui_ne_dit_pas_ready_rend_sa_place():
    places = SandboxPlaces(1)
    session = SandboxSession(
        settings=make_settings(sandbox_start_timeout=0.5),
        command=[sys.executable, str(FAKE_BRIDGE), "--silent"],
        places=places,
    )

    with pytest.raises(SandboxError, match="pas démarré"):
        session.start()

    places.acquerir(0.1)  # la place est revenue malgré l'échec


def test_un_start_rejoue_ne_prend_pas_deux_places():
    places = SandboxPlaces(1)
    session = fake_session(places)
    session.start()
    try:
        session.start()
    finally:
        session.close()

    places.acquerir(0.1)  # une seule place avait été prise, elle est rendue
