"""Fixtures partagées de la suite de tests (unit / integration / e2e)."""

import os

import pytest

from data_analyst_agent.config import get_settings


@pytest.fixture(autouse=True)
def environnement_isole():
    """Rend `os.environ` au test suivant tel qu'elle l'a trouvé.

    ``export_env_file()``, appelée par ``get_settings()``, publie *durablement*
    le ``.env`` du poste dans l'environnement du process. Sans cette fixture, il
    suffit qu'un test touche ``get_settings()`` pour que tous les suivants lisent
    la configuration du développeur au lieu des valeurs par défaut : le même test
    passe seul et échoue dans la suite complète, selon l'ordre de collecte de
    pytest (mesuré : ``Settings(_env_file=None).workspace_dir`` valait
    ``/home/ubuntu/daa-workspaces-persist`` au lieu de ``var/workspaces``).

    Le cache de ``get_settings`` est vidé des deux côtés pour la même raison : un
    ``Settings`` construit sous l'environnement d'un test ne doit pas servir au
    suivant.
    """
    avant = dict(os.environ)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
        os.environ.clear()
        os.environ.update(avant)
