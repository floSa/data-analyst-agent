"""Configuration de l'application (pydantic-settings, préfixe d'environnement DAA_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Réglages globaux, surchargeables par variables d'environnement (``DAA_*``) ou ``.env``."""

    model_config = SettingsConfigDict(env_prefix="DAA_", env_file=".env", extra="ignore")

    # --- Sandbox d'exécution (docs/CADRAGE.md §6) ---
    # Commande docker ; surchargez p. ex. avec '["wsl", "docker"]' depuis Windows.
    sandbox_docker_cmd: list[str] = ["docker"]
    sandbox_image: str = "data-analyst-agent-sandbox:0.1"
    sandbox_mem_limit: str = "1g"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_start_timeout: float = 60.0
    sandbox_exec_timeout: float = 30.0
    # Marge accordée au conteneur pour interrompre proprement le kernel avant
    # que l'hôte ne le tue (timeout dur = exec_timeout + kill_grace).
    sandbox_kill_grace: float = 10.0


@lru_cache
def get_settings() -> Settings:
    """Instance partagée des réglages (cache process-wide)."""
    return Settings()
