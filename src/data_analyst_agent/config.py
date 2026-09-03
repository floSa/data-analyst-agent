"""Configuration de l'application (pydantic-settings, préfixe d'environnement DAA_)."""

import os
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
    # Sous le projet, et non dans /tmp : le contenu de /tmp est purgé (10 jours
    # sur la machine de dev) et lisible par tout compte local, alors qu'on y
    # écrit les questions des utilisateurs et leurs données. Surchargeable par
    # DAA_WORKSPACE_DIR pour pointer un volume dédié en production.
    workspace_dir: Path = Path("var/workspaces")

    # --- Ce qui entre dans le contexte du modèle (docs/AUDIT-2026-09.md §3.4) ---
    # Les tableaux intermédiaires d'une conversation sont réinjectés à chaque
    # tour sur TROIS axes : prompt du planificateur, montages de la sandbox,
    # catalogue effectif. Sans plafond, la liste grandit indéfiniment (mesuré :
    # 100 montages `--volume` et ~13 000 caractères de catalogue à 100 tours).
    # Fenêtre glissante : on réinjecte les N plus récents. 0 = pas de fenêtre.
    # Les objets évincés RESTENT sur le disque : on plafonne ce qu'on injecte,
    # pas ce qu'on conserve.
    context_artifact_window: int = 8
    # Budget de tokens du prompt du planificateur, décompté AVANT l'appel. Il
    # borne ce que la fenêtre seule ne borne pas : un catalogue déclaré volumineux
    # ou une question très longue. Au dépassement, les objets intermédiaires les
    # plus ANCIENS sont retirés jusqu'à ce que ça tienne — la dégradation est
    # ordonnée, jamais subie. À tenir NETTEMENT sous la fenêtre du serveur
    # (`OLLAMA_CONTEXT_LENGTH`, 32768 sur le service central) : le budget ne
    # couvre que le planificateur, les autres agents ajoutent leurs propres tours.
    # 0 désactive le budget.
    context_token_budget: int = 8000
    # Fenêtre de contexte RÉELLEMENT servie par le serveur (Ollama :
    # `OLLAMA_CONTEXT_LENGTH`, 32768 sur le service central — et non les 131 072
    # que déclare gemma4). Elle ne règle rien côté client : elle sert à
    # CONSTATER un débordement, en confrontant `prompt_eval_count` à ce qu'on a
    # envoyé. 0 = fenêtre inconnue, la détection se rabat sur l'écart grossier.
    context_model_window: int = 32768
    # Filet quand la fenêtre est inconnue ou mal déclarée : on signale si le
    # serveur dit avoir évalué moins de cette fraction de notre estimation.
    # 0,4 est très en dessous de ce que l'imprécision du compteur peut
    # expliquer (mesurée, elle ne descend jamais sous 0,85).
    context_overflow_ratio: float = 0.4

    # --- Authentification (login + mot de passe, session côté serveur) ---
    # Magasin de comptes : logins et empreintes argon2id. NON VERSIONNÉ, écrit
    # en 0600, peuplé uniquement par scripts/manage_users.py — il n'y a ni
    # inscription ouverte ni compte par défaut. Cf. users.example.yaml.
    auth_accounts_path: Path = Path("var/users.yaml")
    # État d'authentification : sessions ouvertes et compteurs d'échecs.
    auth_state_dir: Path = Path("var/auth")
    session_cookie_name: str = "daa_session"
    csrf_cookie_name: str = "daa_csrf"
    # Inactivité : ferme un poste laissé ouvert (1 h). Durée absolue : borne une
    # session qu'un onglet maintiendrait vivante indéfiniment (12 h).
    session_idle_timeout: float = 3600.0
    session_absolute_timeout: float = 43200.0
    # Défaut SÛR : le cookie de session ne part que sur HTTPS. À passer à false
    # UNIQUEMENT pour un développement local en http, jamais en service.
    session_cookie_secure: bool = True
    # Anti-force brute : au-delà de N échecs, verrouillage temporisé, compté par
    # compte ET par adresse.
    login_max_failures: int = 5
    login_lockout_seconds: float = 300.0

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
