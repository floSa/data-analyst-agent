"""Tests des réglages (pydantic-settings)."""

import os

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
