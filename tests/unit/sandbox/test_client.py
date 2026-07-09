"""Tests du client sandbox contre un faux bridge (aucun Docker requis)."""

import base64
import sys
from pathlib import Path

import pytest

from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import SandboxError, SandboxResult, SandboxSession

FAKE_BRIDGE = Path(__file__).parents[2] / "fakes" / "fake_bridge.py"


def make_settings(**overrides) -> Settings:
    defaults = {
        "sandbox_start_timeout": 10.0,
        "sandbox_exec_timeout": 5.0,
        "sandbox_kill_grace": 2.0,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def fake_session(*extra_args: str, **settings_overrides) -> SandboxSession:
    return SandboxSession(
        settings=make_settings(**settings_overrides),
        command=[sys.executable, str(FAKE_BRIDGE), *extra_args],
    )


def test_execute_ok():
    with fake_session() as session:
        result = session.execute("print('bonjour')")
    assert result.status == "ok"
    assert result.stdout == "ok\n"
    assert result.error is None


def test_ping():
    with fake_session() as session:
        assert session.ping() is True


def test_sortie_png_decodable():
    with fake_session() as session:
        result = session.execute("FAKE_PNG")
    assert result.status == "ok"
    images = result.images_png
    assert len(images) == 1
    payload = base64.b64decode(images[0])
    assert payload.startswith(b"\x89PNG")


def test_erreur_remontee():
    with fake_session() as session:
        result = session.execute("FAKE_ERROR")
    assert result.status == "error"
    assert "ZeroDivisionError" in result.error


def test_timeout_cote_bridge():
    with fake_session() as session:
        result = session.execute("FAKE_TIMEOUT")
    assert result.status == "timeout"
    assert "interrompue" in result.error


def test_timeout_exterieur_tue_le_conteneur():
    # Le faux bridge dort 30 s ; timeout 0.2 s + grâce 0.5 s => on tue.
    with fake_session(sandbox_kill_grace=0.5) as session:
        result = session.execute("FAKE_SLEEP", timeout=0.2)
    assert result.status == "timeout"
    assert "tué" in result.error


def test_crash_du_processus():
    with fake_session(sandbox_kill_grace=0.5) as session:
        result = session.execute("FAKE_CRASH", timeout=1.0)
    assert result.status == "timeout"  # aucune réponse : traité comme mort


def test_bruit_non_json_ignore():
    with fake_session() as session:
        result = session.execute("FAKE_NOISE")
    assert result.status == "ok"
    assert result.stdout == "ok\n"


def test_reponse_orpheline_ignoree():
    with fake_session() as session:
        result = session.execute("FAKE_ORPHAN")
    assert result.status == "ok"
    assert result.stdout == "ok\n"  # pas la réponse "orphelin"


def test_demarrage_sans_ready_leve():
    session = fake_session("--silent", sandbox_start_timeout=0.5)
    with pytest.raises(SandboxError, match="pas démarré"):
        session.start()


def test_execute_sans_start_leve():
    session = fake_session()
    with pytest.raises(SandboxError, match="non démarrée"):
        session.execute("print(1)")


def test_commande_introuvable():
    session = SandboxSession(
        settings=make_settings(),
        command=["programme-inexistant-xyz"],
    )
    with pytest.raises(SandboxError, match="introuvable"):
        session.start()


def test_result_model_valide_le_statut():
    with pytest.raises(ValueError, match="status"):
        SandboxResult.model_validate({"status": "n-importe-quoi"})
