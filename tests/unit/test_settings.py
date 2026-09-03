"""Tests des réglages (pydantic-settings) et de l'isolation d'environnement.

Ce fichier testait ``Settings``, pas la sandbox : il vivait sous
``tests/unit/sandbox/``. Renommé plutôt que déplacé tel quel — les dossiers de
tests n'ont pas de ``__init__.py``, deux ``test_config.py`` de même basename
donneraient un « import file mismatch » à pytest.
"""

import os
from pathlib import Path

import pytest

import conftest
from data_analyst_agent.config import Settings, export_env_file, get_settings


def make_settings(**overrides) -> Settings:
    """Settings isolés du .env et de l'environnement du développeur."""
    return Settings(_env_file=None, **overrides)


def test_valeurs_par_defaut():
    settings = make_settings()
    assert settings.sandbox_docker_cmd == ["docker"]
    assert settings.sandbox_image.startswith("data-analyst-agent-sandbox")
    assert settings.sandbox_exec_timeout > 0


def test_surcharge_par_environnement(monkeypatch):
    monkeypatch.setenv("DAA_SANDBOX_IMAGE", "sandbox-perso:dev")
    monkeypatch.setenv("DAA_SANDBOX_CPUS", "2.5")
    settings = Settings(_env_file=None)
    assert settings.sandbox_image == "sandbox-perso:dev"
    assert settings.sandbox_cpus == 2.5


def test_get_settings_est_un_cache():
    assert get_settings() is get_settings()


def test_export_env_file_publie_les_variables_hors_settings(tmp_path, monkeypatch):
    """Les DAA_PG_* du .env doivent atteindre os.environ : le DSN du catalogue est
    résolu par os.path.expandvars, qui ne lit que l'environnement réel."""
    env = tmp_path / ".env"
    env.write_text("DAA_PG_PORT=5432\nDAA_PG_USER=postgres\n", encoding="utf-8")
    monkeypatch.delenv("DAA_PG_PORT", raising=False)
    monkeypatch.delenv("DAA_PG_USER", raising=False)

    export_env_file(env)

    assert os.environ["DAA_PG_PORT"] == "5432"
    assert os.environ["DAA_PG_USER"] == "postgres"


def test_export_env_file_ne_recouvre_pas_lenvironnement_reel(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DAA_PG_PORT=5432\n", encoding="utf-8")
    monkeypatch.setenv("DAA_PG_PORT", "6543")  # posé explicitement : doit primer

    export_env_file(env)

    assert os.environ["DAA_PG_PORT"] == "6543"


def test_workspace_dir_par_defaut_sous_le_projet(monkeypatch):
    """Pas dans /tmp : purgé périodiquement (10 jours sur la machine de dev) et
    lisible par tout compte local, alors qu'on y écrit les questions des
    utilisateurs et les données qu'ils font remonter."""
    # Un développeur peut avoir exporté la variable dans son shell : le défaut ne
    # se lit qu'à vide. La fuite du `.env` dans os.environ, elle, est neutralisée
    # en amont par la fixture d'isolation de tests/conftest.py.
    monkeypatch.delenv("DAA_WORKSPACE_DIR", raising=False)

    defaut = make_settings().workspace_dir

    assert defaut == Path("var/workspaces")
    assert not defaut.is_absolute()  # relatif au projet, comme catalog_path


def test_daa_workspace_dir_prime_sur_le_defaut(monkeypatch, tmp_path):
    """L'instance en service pointe un volume dédié : changer le défaut ne doit
    pas reprendre la main sur la variable d'environnement."""
    monkeypatch.setenv("DAA_WORKSPACE_DIR", str(tmp_path / "persist"))

    assert Settings(_env_file=None).workspace_dir == tmp_path / "persist"


# -- isolation d'environnement (fixture autouse de tests/conftest.py) ----------


def test_get_settings_publie_le_env_du_poste_dans_lenvironnement(tmp_path, monkeypatch):
    """Le comportement à contenir : get_settings() a un effet de bord DURABLE.

    C'est légitime (le DSN du catalogue passe par ``os.path.expandvars``), mais
    ça fait fuir la configuration du poste dans tous les tests suivants.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DAA_WORKSPACE_DIR=/volume/du/poste\n", encoding="utf-8")
    monkeypatch.delenv("DAA_WORKSPACE_DIR", raising=False)
    get_settings.cache_clear()

    get_settings()

    assert os.environ["DAA_WORKSPACE_DIR"] == "/volume/du/poste"
    # et le poison atteint jusqu'aux Settings censés être isolés du .env
    assert Settings(_env_file=None).workspace_dir == Path("/volume/du/poste")


def test_la_fixture_disolation_rend_lenvironnement_au_test_suivant():
    """Sans elle, un test qui lit une valeur par défaut passe seul et échoue dans
    la suite complète, selon l'ordre de collecte de pytest."""
    fixture = conftest.environnement_isole.__wrapped__()
    next(fixture)  # entrée : l'environnement d'origine est mémorisé

    os.environ["DAA_TEMOIN_FUITE"] = "posee-par-le-test"
    os.environ.pop("PATH", None)  # une variable supprimée doit revenir aussi
    with pytest.raises(StopIteration):
        next(fixture)  # sortie de la fixture

    assert "DAA_TEMOIN_FUITE" not in os.environ
    assert "PATH" in os.environ


def test_la_fixture_disolation_vide_le_cache_de_get_settings():
    """Un Settings construit sous l'environnement d'un test ne doit pas servir au
    suivant : le cache est vidé à l'entrée comme à la sortie."""
    premier = get_settings()
    fixture = conftest.environnement_isole.__wrapped__()

    next(fixture)

    assert get_settings() is not premier
