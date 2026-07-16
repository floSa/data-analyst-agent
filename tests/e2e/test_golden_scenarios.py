"""Les 3 scénarios golden (CADRAGE §12), du message utilisateur à la réponse.

Tout est RÉEL — Postgres multi-tables (testcontainers), sandbox Docker durcie,
artefact ML entraîné — sauf le LLM, scripté pour rester déterministe en CI.
La valeur du scénario n°1 est vérifiée contre l'oracle pandas.

Les données sont l'échantillon Maxizoo versionné (S1 2025, 3 magasins, 7 SKU),
choisi pour porter les 6 pièges du dictionnaire : les scénarios ci-dessous
s'appuient sur eux plutôt que sur des questions dont la réponse serait la même
avec ou sans piège.
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, DuckDBSource, PostgresSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import ensure_image
from helpers.maxizoo import (
    build_duckdb,
    golden_ca_2025_par_magasin,
    seed_maxizoo_postgres,
)
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


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _docker_disponible(), reason="démon Docker indisponible"),
]

REPO = Path(__file__).parents[2]

GOLDEN_SQL = """\
SELECT st.store_name, round(sum(s.revenue), 2) AS ca
FROM sales_daily s
JOIN stores st ON st.store_id = s.store_id
GROUP BY st.store_name
ORDER BY ca DESC"""

BAR_CHART_CODE = """\
```python
import pandas as pd
import matplotlib.pyplot as plt

# source Postgres 'maxizoo' matérialisée : une table = un CSV
ventes = pd.read_csv('/data/sales_daily.csv')
magasins = pd.read_csv('/data/stores.csv')
ca = (
    ventes.merge(magasins, on='store_id')
    .groupby('store_name')['revenue'].sum()
    .sort_values(ascending=False)
)
for magasin, valeur in ca.items():
    print(f"{magasin} : {valeur:.0f} EUR")

ca.plot.bar(xlabel='Magasin', ylabel='CA (EUR)', title='CA par magasin, S1 2025')
plt.tight_layout()
plt.show()
```"""

# Un samedi de novembre, croquettes chien de marque nationale en grande surface.
VENTE_A_PREDIRE = {
    "store_type": "grand",
    "commodity_group": "Chien",
    "brand_type": "nationale",
    "base_price": 49.90,
    "day_of_week": 5,
    "month": 11,
    "discount_rate": 0.30,
    "promo_type": "produits",
    "temp_anomaly": 0.0,
}


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REPO / "models" / "registry.yaml")


@pytest.fixture(scope="module")
def catalog(settings: Settings, tmp_path_factory):
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    ensure_image(settings)  # l'image sandbox doit exister pour le scénario n°2
    with PostgresContainer("postgres:16-alpine", driver="pg8000") as container:
        url = container.get_connection_url()
        engine = create_engine(url)
        seed_maxizoo_postgres(engine)
        engine.dispose()
        base = build_duckdb(tmp_path_factory.mktemp("duckdb") / "maxizoo.duckdb")
        yield Catalog(
            sources=[
                PostgresSource(
                    name="maxizoo",
                    description="Base retail animalerie (schéma en étoile)",
                    dsn=url,
                ),
                # La même donnée servie par l'autre moteur : le pipeline doit
                # rendre le même résultat, quel que soit l'adaptateur.
                DuckDBSource(
                    name="maxizoo_duckdb",
                    description="La même base, en fichier DuckDB",
                    path=base,
                ),
            ]
        )


def make_orchestrator(llm: ScriptedLLM, catalog, registry, settings) -> Orchestrator:
    return Orchestrator(model=llm.model(), catalog=catalog, registry=registry, settings=settings)


# --- scénario golden n°1 : requête SQL agrégée (jointure) ------------------------


def _llm_golden_1(source: str) -> ScriptedLLM:
    return (
        ScriptedLLM()
        .script(
            PLANNER,
            [plan_response(Plan(capability="query", source=source, reason="agrégat SQL"))],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("get_schema", {}),
                tool_call("run_sql", {"query": GOLDEN_SQL}),
                text("Le canal en ligne réalise le plus gros chiffre d'affaires."),
            ],
        )
    )


def test_golden_1_ca_par_magasin(catalog, registry, settings):
    orchestrator = make_orchestrator(_llm_golden_1("maxizoo"), catalog, registry, settings)
    answer = orchestrator.ask("Quel magasin réalise le plus gros chiffre d'affaires ?")

    assert answer.error is None
    # la valeur produite par le pipeline coïncide avec l'oracle pandas
    oracle = golden_ca_2025_par_magasin()
    table = json.loads(answer.artifacts[0].data)
    obtenu = [(nom, float(ca)) for nom, ca in table["rows"]]
    assert obtenu == pytest.approx(oracle)
    # Le e-commerce sort premier : un agent qui répondrait « Paris » aurait
    # oublié que le canal en ligne est une ligne de `stores` (piège n°1).
    assert obtenu[0][0] == "Canal Online"
    # Résultat multi-lignes : la synthèse renvoie au tableau au lieu de le
    # recopier — c'est le résumé déterministe, pas la prose du LLM.
    assert "3 lignes" in answer.answer
    assert "tableau" in answer.answer
    assert [s.node for s in answer.trace] == ["plan", "retrieval", "synthesize"]


def test_golden_1_meme_resultat_sur_duckdb(catalog, registry, settings):
    """La même question sur le fichier DuckDB : deux moteurs, un seul résultat."""
    orchestrator = make_orchestrator(_llm_golden_1("maxizoo_duckdb"), catalog, registry, settings)
    answer = orchestrator.ask("Quel magasin réalise le plus gros chiffre d'affaires ?")

    assert answer.error is None
    table = json.loads(answer.artifacts[0].data)
    assert [(nom, float(ca)) for nom, ca in table["rows"]] == pytest.approx(
        golden_ca_2025_par_magasin()
    )


# --- scénario golden n°2 : bar chart en sandbox -----------------------------------


def test_golden_2_bar_chart_ca_par_magasin(catalog, registry, settings):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [plan_response(Plan(capability="analyze", source="maxizoo", reason="figure"))],
        )
        .script(ANALYSIS, [text(BAR_CHART_CODE)])
        .script(
            SYNTHESIS,
            [text("Voici le bar chart du CA par magasin (ci-joint).")],
        )
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask("Fais-moi un bar chart du CA par magasin.")

    assert answer.error is None
    # un objet image/png non vide est produit
    images = [a for a in answer.artifacts if a.mime == "image/png"]
    assert len(images) == 1
    payload = base64.b64decode(images[0].data)
    assert payload.startswith(b"\x89PNG")
    assert len(payload) > 1000
    assert "bar chart" in answer.answer
    analysis_step = next(s for s in answer.trace if s.node == "analysis")
    assert "statut ok" in analysis_step.detail


# --- scénario golden n°3 : prédiction gardée --------------------------------------


def test_golden_3_prediction_de_vente(catalog, registry, settings):
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(capability="predict", dataset="maxizoo_sales", features=VENTE_A_PREDIRE)
            )
        ],
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask(
        "Combien d'unités vendra-t-on : grand magasin, croquettes chien, marque nationale "
        "à 49,90 €, un samedi de novembre, en promo -30 %, par temps de saison ?"
    )

    assert answer.error is None
    assert "Prédiction (maxizoo_sales)" in answer.answer
    assert "unités vendues" in answer.answer
    inference_step = next(s for s in answer.trace if s.node == "inference")
    assert "statut ok" in inference_step.detail


def test_chainage_en_lot_sur_le_catalogue(catalog, registry, settings):
    """« Prédis pour tout le catalogue » : lot réel sur Postgres + vrai modèle.

    Les 7 SKU sont récupérés avec leurs attributs, puis prédits en un appel
    vectorisé. C'est le chaînage SQL -> ML de bout en bout.
    """
    batch_settings = Settings(_env_file=None, retrieval_max_rows=100)
    requete = (
        "SELECT st.store_type, p.commodity_group, p.brand_type, p.base_price,"
        " 5 AS day_of_week, 11 AS month, 0.0 AS discount_rate,"
        " 'aucune' AS promo_type, 0.0 AS temp_anomaly"
        " FROM products p CROSS JOIN stores st WHERE st.store_id = 'S01'"
    )
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="maxizoo",
                        dataset="maxizoo_sales",
                        data_question="Tout le catalogue à Paris un samedi de novembre",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": requete}), text("7 lignes récupérées.")],
        )
    )
    orchestrator = make_orchestrator(llm, catalog, registry, batch_settings)
    answer = orchestrator.ask("Prédis les ventes de tout le catalogue à Paris samedi.")

    assert answer.error is None
    detail = json.loads(answer.artifacts[0].data)
    assert len(detail["rows"]) == 7  # les 7 SKU de l'échantillon
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    assert "Prédiction (maxizoo_sales) sur 7 lignes" in answer.answer
    # toutes les lignes sont valides : aucune n'est écartée
    assert all(not str(row[-2]).startswith("écartée") for row in detail["rows"])


def test_golden_3bis_features_incompletes_redemande(catalog, registry, settings):
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict",
                    dataset="maxizoo_sales",
                    features={"store_type": "grand", "commodity_group": "Chien"},
                )
            )
        ],
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask("Combien de croquettes chien vendra-t-on en grand magasin ?")

    assert answer.error is None
    # le système REDEMANDE, il ne prédit pas
    assert "Prédiction" not in answer.answer
    assert "base_price" in answer.answer
    assert answer.answer.strip().endswith("?")
    inference_step = next(s for s in answer.trace if s.node == "inference")
    assert "statut invalid" in inference_step.detail
