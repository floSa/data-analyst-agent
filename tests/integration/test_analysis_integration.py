"""Intégration Analyse : LLM scripté mais sandbox RÉELLE (Docker requis).

Vérifie le chemin complet génération -> exécution -> MIME, et la boucle
self-debug avec une vraie erreur de kernel.
"""

import base64
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.analysis.agent import run_analysis
from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import ensure_image


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


@pytest.fixture
def ventes_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini_ventes.csv"
    csv.write_text(
        "store_type,commodity_group,revenue\n"
        "grand,Chien,120.0\ngrand,Chien,80.0\ngrand,Chat,60.0\n"
        "petit,Chien,40.0\npetit,Chat,20.0\nonline,Chien,150.0\n"
        "online,Chat,90.0\nonline,Reptile,10.0\n",
        encoding="utf-8",
    )
    return csv


def scripted_model(responses: list[str]) -> FunctionModel:
    remaining = list(responses)

    def responder(messages, info):
        return ModelResponse(parts=[TextPart(remaining.pop(0))])

    return FunctionModel(responder)


def test_stat_et_bar_chart_sur_vrai_sandbox(settings, image, ventes_csv):
    code = """```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('/data/mini_ventes.csv')
part_online = df[df.store_type == 'online'].revenue.sum() / df.revenue.sum() * 100
print(f"part online: {part_online:.1f}")

df.groupby('store_type').revenue.sum().plot.bar()
plt.show()
```"""
    result = run_analysis(
        "Part du e-commerce dans le CA + bar chart par format de magasin",
        data_files={ventes_csv: "mini_ventes.csv"},
        model=scripted_model([code]),
        settings=settings,
    )
    assert result.succeeded, result.execution.error
    assert "part online: 43.9" in result.execution.stdout  # 250 / 570
    images = result.execution.images_png
    assert len(images) == 1
    assert base64.b64decode(images[0]).startswith(b"\x89PNG")


def test_self_debug_avec_vraie_erreur_kernel(settings, image, ventes_csv):
    casse = (
        "```python\nimport pandas as pd\ndf = pd.read_csv('/data/absent.csv')\nprint(len(df))\n```"
    )
    corrige = (
        "```python\nimport pandas as pd\n"
        "df = pd.read_csv('/data/mini_ventes.csv')\nprint(len(df))\n```"
    )
    result = run_analysis(
        "Combien de lignes ?",
        data_files={ventes_csv: "mini_ventes.csv"},
        model=scripted_model([casse, corrige]),
        settings=settings,
    )
    assert result.succeeded, result.execution.error
    assert result.attempts == 2
    assert result.execution.stdout == "8\n"
