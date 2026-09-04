"""Les adaptateurs de source sont refermés, quel que soit le nœud (audit §2.3).

``open_source()`` est appelée à CHAQUE exécution de nœud et fabrique à chaque
fois un pool de connexions neuf. Rien ne le refermait : sous charge, le
``max_connections`` du serveur Postgres se remplit et les bases DuckDB en
mémoire s'empilent. Ce fichier vérifie la fermeture sur les trois nœuds qui
ouvrent une source, y compris quand le nœud échoue.

Fichier séparé de ``test_graph.py`` à dessein : la branche Maxizoo le réécrit
presque entièrement (audit §8.3), et ces tests-ci n'ont pas à disparaître avec.
"""

from pathlib import Path

import joblib
import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource, PostgresSource
from data_analyst_agent.agents.retrieval.duckdb_source import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import QueryError
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import SandboxResult
from helpers.doubles import FakeRegressor, ScriptedSandbox
from helpers.scripted_llm import (
    ANALYSIS,
    PLANNER,
    RETRIEVAL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

REGISTRY_YAML = """
models:
  - dataset: maxizoo_sales
    task: regression
    model_path: maxizoo_sales.joblib
    target: quantity
    unit: unités vendues
"""


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeRegressor(), tmp_path / "maxizoo_sales.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return csv


# Le format exact des features de `maxizoo_sales` : le nœud fetch_then_predict
# ne prédit que si le tableau récupéré les porte toutes.
FEATURES_HEADER = (
    "ligne_id,store_type,commodity_group,brand_type,base_price,"
    "day_of_week,month,discount_rate,promo_type,temp_anomaly"
)


@pytest.fixture
def ventes_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "ventes.csv"
    csv.write_text(
        f"{FEATURES_HEADER}\n1,grand,Chien,nationale,49.90,5,11,0.30,produits,0.0\n",
        encoding="utf-8",
    )
    return csv


class AdaptateurEspion:
    """Un adaptateur réel, qui note qu'on l'a refermé."""

    def __init__(self, reel) -> None:
        self._reel = reel
        self.ferme = False
        self.leve_au_schema = False

    @property
    def dialect(self) -> str:
        return self._reel.dialect

    def schema(self):
        if self.leve_au_schema:
            raise QueryError("la source ne répond pas")
        return self._reel.schema()

    def run(self, query: str, max_rows: int = 200):
        return self._reel.run(query, max_rows)

    def close(self) -> None:
        self.ferme = True
        self._reel.close()


def espionner_les_sources(monkeypatch, fichier: Path, **attributs) -> list[AdaptateurEspion]:
    """Remplace ``open_source`` par une fabrique qui garde la trace des adaptateurs.

    Le vrai adaptateur DuckDB est dessous : le test reste un test du graphe, pas
    une mise en scène. ``fichier`` sert de source à toute demande, ce qui permet
    de faire passer une source déclarée Postgres sans base Postgres.
    """
    espions: list[AdaptateurEspion] = []

    def fabrique(_source):
        espion = AdaptateurEspion(DuckDBAdapter.from_file(fichier))
        for nom, valeur in attributs.items():
            setattr(espion, nom, valeur)
        espions.append(espion)
        return espion

    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.open_source", fabrique)
    return espions


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- récupération -------------------------------------------------------------


def test_le_noeud_de_recuperation_referme_sa_source(
    mini_csv: Path, registry: Registry, monkeypatch
):
    espions = espionner_les_sources(monkeypatch, mini_csv)
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}), text("4 lignes.")],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrateur = Orchestrator(
        model=llm.model(), catalog=catalog, registry=registry, settings=make_settings()
    )

    reponse = orchestrateur.ask("Combien de lignes ?")

    assert reponse.error is None
    assert [e.ferme for e in espions] == [True]


def test_une_source_est_refermee_meme_quand_le_noeud_echoue(
    mini_csv: Path, registry: Registry, monkeypatch
):
    """C'est tout l'intérêt de `closing` : l'échec est le cas où la fuite comptait."""
    espions = espionner_les_sources(monkeypatch, mini_csv, leve_au_schema=True)
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrateur = Orchestrator(
        model=llm.model(), catalog=catalog, registry=registry, settings=make_settings()
    )

    reponse = orchestrateur.ask("Combien de lignes ?")

    assert reponse.error is not None  # le nœud a bien échoué
    assert [e.ferme for e in espions] == [True]


# --- analyse sur source SQL ---------------------------------------------------


def test_le_noeud_danalyse_referme_la_base_avant_de_lancer_la_sandbox(
    mini_csv: Path, registry: Registry, monkeypatch
):
    """La base est matérialisée en CSV puis refermée : l'analyse ne lui doit plus rien."""
    espions = espionner_les_sources(monkeypatch, mini_csv)
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="base"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Voici l'analyse.")])
    )
    catalog = Catalog(
        sources=[PostgresSource(name="base", dsn="postgresql+pg8000://u:p@hote:5432/b")]
    )
    orchestrateur = Orchestrator(
        model=llm.model(),
        catalog=catalog,
        registry=registry,
        settings=make_settings(),
        sandbox=ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n")]),
    )

    reponse = orchestrateur.ask("Trace la répartition")

    assert reponse.error is None
    assert [e.ferme for e in espions] == [True]


# --- récupération puis prédiction ---------------------------------------------


def test_le_noeud_fetch_predict_referme_sa_source(
    ventes_csv: Path, registry: Registry, monkeypatch
):
    espions = espionner_les_sources(monkeypatch, ventes_csv)
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(capability="fetch_then_predict", dataset="maxizoo_sales", source="ventes")
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM ventes WHERE ligne_id = 1"}),
                text("Une ligne."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="ventes", path=ventes_csv)])
    orchestrateur = Orchestrator(
        model=llm.model(), catalog=catalog, registry=registry, settings=make_settings()
    )

    reponse = orchestrateur.ask("Prédis les ventes de la ligne 1")

    assert reponse.error is None
    assert [e.ferme for e in espions] == [True]
