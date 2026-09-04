"""Fixtures partagées de la suite de tests (unit / integration / e2e)."""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from data_analyst_agent import config
from data_analyst_agent.config import ENV_FILE, get_settings

RACINE = Path(__file__).resolve().parent.parent

# Le `.env` que la suite fait exister : aucun. Chemin ABSOLU, pour qu'un test
# qui change de répertoire courant ne retombe pas par accident sur un fichier
# réel ; inexistant, parce que `dotenv_values` comme pydantic-settings prennent
# un fichier absent pour un fichier vide, sans rien lever.
ENV_FILE_NEUTRE = RACINE / ".env.absent-pendant-les-tests"

# Ce que le `.env` du poste déclare. Lu une fois, à l'import, et depuis la racine
# du dépôt : le fichier ne bouge pas d'une session à l'autre, et un test peut
# très bien changer de répertoire courant (`monkeypatch.chdir`).
CLES_DU_ENV_DU_POSTE = frozenset(dotenv_values(RACINE / ENV_FILE))


def variables_de_lapplication() -> list[str]:
    """Les variables d'environnement qui configurent l'application, ici et maintenant.

    Deux familles : le préfixe des réglages (``DAA_``, cf. ``Settings``) et tout
    ce que le `.env` du poste déclare — la seconde est presque incluse dans la
    première, mais rien n'oblige un `.env` à ne porter que des ``DAA_*``.
    """
    return [cle for cle in os.environ if cle.startswith("DAA_") or cle in CLES_DU_ENV_DU_POSTE]


# Neutralisation dès l'IMPORT, et pas seulement à l'entrée de la fixture : pytest
# importe ce fichier avant de collecter, et la collecte importe
# `data_analyst_agent.api.app`, dont le `app = create_app()` de niveau module
# appelle `get_settings()`. Attendre la première fixture, c'est laisser le `.env`
# du poste entrer avant le premier test — et avec lui alimenter les fixtures de
# portée session, montées avant toute fixture de portée fonction.
config.ENV_FILE = ENV_FILE_NEUTRE
for _cle in variables_de_lapplication():
    del os.environ[_cle]


@pytest.fixture(autouse=True)
def environnement_isole():
    """Coupe la suite de la configuration du poste, puis rend l'environnement intact.

    ``export_env_file()``, appelée par ``get_settings()``, publie *durablement*
    le ``.env`` du poste dans l'environnement du process. Restaurer en sortie ne
    suffit donc pas, et c'est ce qui manquait : **la fuite précède le premier
    test**. Importer ``data_analyst_agent.api.app`` exécute son ``app =
    create_app()`` de niveau module — donc ``get_settings()``, donc
    ``export_env_file()`` — dès la *collecte*. Tout test démarre alors sur un
    ``os.environ`` déjà pollué et le mémorise comme état « avant » : le rendre
    fidèlement à la sortie ne fait que perpétuer la fuite.

    D'où le retrait à l'entrée. Sans lui, ``Settings(_env_file=None)`` — censé
    ignorer le `.env` — lit quand même les valeurs du poste par ``os.environ``
    (mesuré : ``workspace_dir`` valait ``/home/ubuntu/daa-workspaces-persist``
    au lieu de ``var/workspaces``, et le `.env` local rendait *verts* deux tests
    de l'alias déprécié du moteur qui échouaient dès la migration faite).

    Le retrait couvre aussi les ``DAA_*`` exportées dans le shell du
    développeur : un test ne doit pas dépendre de qui le lance.

    Retirer ne suffit pas non plus à lui seul : ``get_settings()`` relit le
    fichier à chaque appel — c'est son rôle — et le republierait au milieu du
    test. Le fichier lui est donc retiré aussi, dès l'import de ce module
    (``ENV_FILE`` pointe un chemin qui n'existe pas). Un test qui veut étudier le
    mécanisme du `.env` repose la constante lui-même (cf. ``test_settings.py``).

    Le cache de ``get_settings`` est vidé des deux côtés pour la même raison : un
    ``Settings`` construit sous l'environnement d'un test ne doit pas servir au
    suivant.
    """
    avant = dict(os.environ)
    for cle in variables_de_lapplication():
        del os.environ[cle]
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
        os.environ.clear()
        os.environ.update(avant)
