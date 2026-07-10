"""Les 3 scénarios golden (CADRAGE §12), du message utilisateur à la réponse.

Tout est RÉEL — Postgres multi-tables (testcontainers), sandbox Docker durcie,
artefacts ML entraînés — sauf le LLM, scripté pour rester déterministe en CI.
La valeur du scénario n°1 est vérifiée contre l'oracle pandas.
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource, PostgresSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import ensure_image
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
from helpers.titanic import (
    TITANIC_CSV,
    golden_survival_rate_female_first_class,
    seed_titanic_postgres,
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
SELECT round(100.0 * sum(p.survived) / count(*), 2) AS taux
FROM passengers p
JOIN classes c ON c.class_id = p.class_id
WHERE p.sex = 'female' AND c.level = 1"""

BAR_CHART_CODE = """\
```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('/data/titanic.csv')
taux = df.groupby('Pclass')['Survived'].mean().mul(100).round(1)
for classe, valeur in taux.items():
    print(f"classe {classe} : {valeur} %")

taux.plot.bar(xlabel='Classe', ylabel='Taux de survie (%)', title='Survie par classe')
plt.tight_layout()
plt.show()
```"""


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REPO / "models" / "registry.yaml")


@pytest.fixture(scope="module")
def catalog(settings: Settings):
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    ensure_image(settings)  # l'image sandbox doit exister pour le scénario n°2
    with PostgresContainer("postgres:16-alpine", driver="pg8000") as container:
        url = container.get_connection_url()
        engine = create_engine(url)
        seed_titanic_postgres(engine)
        engine.dispose()
        yield Catalog(
            sources=[
                PostgresSource(
                    name="titanic_pg",
                    description="Base Titanic multi-tables (passengers + classes)",
                    dsn=url,
                ),
                FileSource(
                    name="titanic",
                    description="CSV des passagers du Titanic",
                    path=TITANIC_CSV,
                ),
            ]
        )


def make_orchestrator(llm: ScriptedLLM, catalog, registry, settings) -> Orchestrator:
    return Orchestrator(model=llm.model(), catalog=catalog, registry=registry, settings=settings)


# --- scénario golden n°1 : requête SQL agrégée (jointure) ------------------------


def test_golden_1_pct_femmes_premiere_classe(catalog, registry, settings):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [plan_response(Plan(capability="query", source="titanic_pg", reason="agrégat SQL"))],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("get_schema", {}),
                tool_call("run_sql", {"query": GOLDEN_SQL}),
                text("96,81 % des femmes de 1re classe ont survécu."),
            ],
        )
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask(
        "À partir de la table passengers, donne-moi le % de femmes de 1re classe qui ont survécu."
    )

    assert answer.error is None
    # la valeur produite par le pipeline coïncide avec l'oracle pandas
    oracle = golden_survival_rate_female_first_class()
    table = json.loads(answer.artifacts[0].data)
    assert table["rows"][0][0] == pytest.approx(oracle)
    assert oracle == pytest.approx(96.81)
    # et la réponse en langage naturel cite la valeur
    assert "96,81" in answer.answer
    assert [s.node for s in answer.trace] == ["plan", "retrieval", "synthesize"]


# --- scénario golden n°2 : bar chart en sandbox -----------------------------------


def test_golden_2_bar_chart_survie_par_classe(catalog, registry, settings):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [plan_response(Plan(capability="analyze", source="titanic", reason="figure"))],
        )
        .script(ANALYSIS, [text(BAR_CHART_CODE)])
        .script(
            SYNTHESIS,
            [text("Voici le bar chart de la survie par classe (ci-joint).")],
        )
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask("Fais-moi un bar chart de la survie par classe.")

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


def test_golden_3_prediction_passagere(catalog, registry, settings):
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict",
                    dataset="titanic",
                    features={
                        "sex": "female",
                        "pclass": 1,
                        "age": 28,
                        "sibsp": 0,
                        "parch": 0,
                        "fare": 80.0,
                        "embarked": "S",
                    },
                )
            )
        ],
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask(
        "Fais-moi la prédiction pour un passager : sexe=female, classe=1, âge=28, "
        "sibsp=0, parch=0, tarif=80, embarqué à Southampton."
    )

    assert answer.error is None
    # classe + probabilité cohérentes (femme de 1re classe -> survie très probable)
    assert "a survécu" in answer.answer
    assert "probabilité" in answer.answer
    inference_step = next(s for s in answer.trace if s.node == "inference")
    assert "statut ok" in inference_step.detail


def test_chainage_en_lot_toutes_les_femmes(catalog, registry, settings):
    """« Prédis pour toutes les femmes » : lot réel sur Postgres + vrai modèle.

    Les 314 femmes sont récupérées (jointure aliasée sur classes.level) ; les
    lignes à âge/embarked manquants sont écartées par la validation, les autres
    prédites en un appel vectorisé.
    """
    batch_settings = Settings(_env_file=None, retrieval_max_rows=400)
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="titanic_pg",
                        dataset="titanic",
                        data_question="Toutes les femmes de la base",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {
                        "query": (
                            "SELECT p.name, p.sex, c.level AS pclass, p.age, p.sibsp,"
                            " p.parch, p.fare, p.embarked"
                            " FROM passengers p JOIN classes c ON c.class_id = p.class_id"
                            " WHERE p.sex = 'female'"
                        )
                    },
                ),
                text("314 lignes récupérées."),
            ],
        )
    )
    orchestrator = make_orchestrator(llm, catalog, registry, batch_settings)
    answer = orchestrator.ask("Prédis avec le modèle si les femmes du Titanic ont survécu.")

    assert answer.error is None
    detail = json.loads(answer.artifacts[0].data)
    assert len(detail["rows"]) == 314  # toutes les femmes, prédites ou écartées
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    # le modèle jouet doit prédire la survie pour la grande majorité des femmes
    predicted = [row[-2] for row in detail["rows"]]
    survivantes = predicted.count("a survécu")
    ecartees = sum(1 for p in predicted if p.startswith("écartée"))
    assert survivantes > 200
    assert survivantes + ecartees + predicted.count("n'a pas survécu") == 314
    assert "Prédiction (titanic) sur" in answer.answer
    assert "a survécu" in answer.answer


def test_golden_3bis_features_incompletes_redemande(catalog, registry, settings):
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict",
                    dataset="titanic",
                    features={"sex": "female", "pclass": 1},
                )
            )
        ],
    )
    orchestrator = make_orchestrator(llm, catalog, registry, settings)
    answer = orchestrator.ask("Prédis la survie d'une femme en 1re classe.")

    assert answer.error is None
    # le système REDEMANDE, il ne prédit pas
    assert "Prédiction" not in answer.answer
    assert "age" in answer.answer
    assert answer.answer.strip().endswith("?")
    inference_step = next(s for s in answer.trace if s.node == "inference")
    assert "statut invalid" in inference_step.detail
