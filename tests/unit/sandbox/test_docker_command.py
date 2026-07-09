"""Le docker run de la sandbox doit porter TOUT le durcissement (CADRAGE §6)."""

from pathlib import Path

from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import docker_run_command


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_durcissement_complet():
    command = docker_run_command(make_settings())
    assert command[:2] == ["docker", "run"]
    for flag in [
        "--network=none",
        "--memory=1g",
        "--memory-swap=1g",
        "--cpus=1.0",
        "--pids-limit=256",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--rm",
        "--interactive",
    ]:
        assert flag in command, f"flag de durcissement manquant : {flag}"
    assert any(f.startswith("--tmpfs=/tmp") for f in command)
    # l'image est le dernier argument (aucune commande utilisateur injectée)
    assert command[-1] == make_settings().sandbox_image


def test_montages_en_lecture_seule():
    mounts = {Path("/tmp/donnees.csv"): "donnees.csv"}
    command = docker_run_command(make_settings(), mounts=mounts)
    volume_flags = [f for f in command if f.startswith("--volume=")]
    assert volume_flags == [f"--volume={Path('/tmp/donnees.csv')}:/data/donnees.csv:ro"]


def test_commande_docker_personnalisee():
    settings = make_settings(sandbox_docker_cmd=["wsl", "docker"])
    command = docker_run_command(settings)
    assert command[:3] == ["wsl", "docker", "run"]
