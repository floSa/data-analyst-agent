"""Tests d'intégration de la sandbox : exécution réelle dans le conteneur durci.

Nécessitent un démon Docker (WSL, Linux ou CI). La première exécution
construit l'image (long) ; les suivantes réutilisent le cache.
"""

import base64
import shutil
import subprocess

import pytest

from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import SandboxSession, ensure_image


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_disponible(), reason="démon Docker indisponible"),
]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(scope="session")
def image(settings: Settings) -> str:
    return ensure_image(settings)


@pytest.fixture(scope="module")
def sandbox(settings: Settings, image: str):
    with SandboxSession(settings=settings) as session:
        yield session


def test_ping(sandbox: SandboxSession):
    assert sandbox.ping() is True


def test_stdout_simple(sandbox: SandboxSession):
    result = sandbox.execute("print(1 + 1)")
    assert result.status == "ok"
    assert result.stdout == "2\n"


def test_etat_persistant_dans_la_session(sandbox: SandboxSession):
    assert sandbox.execute("x = 41").status == "ok"
    result = sandbox.execute("print(x + 1)")
    assert result.stdout == "42\n"


def test_socle_scientifique_present(sandbox: SandboxSession):
    code = (
        "import pandas, numpy, scipy, statsmodels, sklearn, duckdb, prince, openpyxl\n"
        "print('socle ok')"
    )
    result = sandbox.execute(code, timeout=60)
    assert result.status == "ok", result.error
    assert "socle ok" in result.stdout


def test_bar_chart_matplotlib_en_png(sandbox: SandboxSession):
    code = (
        "import matplotlib.pyplot as plt\n"
        "plt.figure()\n"
        "plt.bar(['a', 'b', 'c'], [3, 1, 2])\n"
        "plt.title('test')\n"
        "plt.show()\n"
    )
    result = sandbox.execute(code, timeout=60)
    assert result.status == "ok", result.error
    images = result.images_png
    assert len(images) == 1
    payload = base64.b64decode(images[0])
    assert payload.startswith(b"\x89PNG")
    assert len(payload) > 1000  # une vraie figure, pas un pixel


def test_erreur_puis_reprise(sandbox: SandboxSession):
    result = sandbox.execute("1 / 0")
    assert result.status == "error"
    assert "ZeroDivisionError" in result.error
    # la session reste utilisable après une erreur
    assert sandbox.execute("print('encore là')").stdout == "encore là\n"


def test_reseau_coupe(sandbox: SandboxSession):
    code = "import socket\nsocket.create_connection(('1.1.1.1', 80), timeout=3)\n"
    result = sandbox.execute(code, timeout=30)
    assert result.status == "error", "la sandbox ne doit avoir AUCUN accès réseau"
    assert "OSError" in result.error or "unreachable" in result.error.lower()


def test_rootfs_en_lecture_seule(sandbox: SandboxSession):
    result = sandbox.execute("open('/usr/pwned', 'w')")
    assert result.status == "error"
    # /tmp (tmpfs) reste utilisable pour le travail temporaire
    ok = sandbox.execute("open('/tmp/scratch.txt', 'w').write('x'); print('ecrit')")
    assert ok.status == "ok"


def test_timeout_interrompt_le_kernel_sans_tuer_la_session(sandbox: SandboxSession):
    result = sandbox.execute("import time\ntime.sleep(60)", timeout=3)
    assert result.status == "timeout"
    # le kernel a été interrompu, pas détruit : la session répond encore
    assert sandbox.execute("print('vivant')").stdout == "vivant\n"


def test_montage_de_fichier_en_lecture_seule(settings: Settings, image: str, tmp_path):
    csv = tmp_path / "mini.csv"
    csv.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    with SandboxSession(settings=settings, mounts={csv: "mini.csv"}) as session:
        result = session.execute(
            "import pandas as pd\ndf = pd.read_csv('/data/mini.csv')\nprint(int(df['a'].sum()))",
            timeout=60,
        )
        assert result.status == "ok", result.error
        assert result.stdout == "4\n"
        # le fichier monté n'est pas modifiable depuis la sandbox
        write_attempt = session.execute("open('/data/mini.csv', 'a').write('x')")
        assert write_attempt.status == "error"
