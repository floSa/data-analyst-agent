"""Tests des réglages (pydantic-settings)."""

from data_analyst_agent.config import Settings, get_settings


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
