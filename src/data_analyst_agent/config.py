"""Configuration de l'application (pydantic-settings, préfixe d'environnement DAA_)."""

import os
import tempfile
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = ".env"


class Settings(BaseSettings):
    """Réglages globaux, surchargeables par variables d'environnement (``DAA_*``) ou ``.env``."""

    model_config = SettingsConfigDict(env_prefix="DAA_", env_file=ENV_FILE, extra="ignore")

    # --- LLM mutualisé (docs/CADRAGE.md §5) ---
    # Un seul modèle langage pour tout le système. Qwen3-Coder n'existe qu'en
    # 30B-A3B (MoE, ~19 Go en Q4) : tient entièrement sur la L4 24 Go de prod,
    # tourne en répartition GPU+RAM sur la machine de dev.
    ollama_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3-coder:30b"
    llm_temperature: float = 0.0

    # --- Agent Récupération (docs/CADRAGE.md §7-①) ---
    catalog_path: Path = Path("sources/catalogue.yaml")
    retrieval_max_rows: int = 200
    # Borne d'allers-retours LLM (tools compris) : coupe les boucles infinies.
    retrieval_request_limit: int = 10

    # --- Agent Analyse (docs/CADRAGE.md §7-②) ---
    analysis_max_attempts: int = 3
    # Nb max de lignes matérialisées par table quand on analyse une source SQL.
    analysis_table_max_rows: int = 10000

    # --- Inférence (docs/CADRAGE.md §7-③) ---
    models_registry_path: Path = Path("models/registry.yaml")

    # --- Mémoire de conversation (objets intermédiaires persistés) ---
    # Chaque conversation persiste ses tableaux intermédiaires (CSV) sous un
    # sous-dossier de ce répertoire ; ils sont réexposés aux tours suivants
    # (sources éphémères, sandbox du code généré).
    workspace_dir: Path = Path(tempfile.gettempdir()) / "daa-workspaces"

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


def export_env_file(env_file: str | Path = ENV_FILE) -> None:
    """Publie les variables du ``.env`` dans l'environnement du process.

    pydantic-settings lit le ``.env`` dans l'objet ``Settings``, mais **ne
    l'exporte pas**. Or toutes les variables du ``.env`` ne sont pas des champs
    de ``Settings`` : celles du DSN du catalogue (``DAA_PG_*``) sont résolues par
    ``os.path.expandvars``, qui ne lit que ``os.environ``. Sans cette passerelle,
    les renseigner dans le ``.env`` — ce que documente ``.env.example`` — reste
    sans effet et la source postgres échoue sur un ``${DAA_PG_PORT}`` littéral.

    L'environnement réel prime : on ne réécrit jamais une variable déjà posée.
    """
    for cle, valeur in dotenv_values(env_file).items():
        if valeur is not None:
            os.environ.setdefault(cle, valeur)


@lru_cache
def get_settings() -> Settings:
    """Instance partagée des réglages (cache process-wide)."""
    export_env_file()
    return Settings()
