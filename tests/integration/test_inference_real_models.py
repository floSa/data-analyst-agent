"""Intégration inférence : les VRAIS artefacts entraînés par notebooks/.

Pas de Docker requis — mais on charge les joblib committés et on vérifie que
les prédictions sont cohérentes et déterministes.
"""

from pathlib import Path

import pytest

from data_analyst_agent.agents.inference.predict import run_inference
from data_analyst_agent.agents.inference.registry import Registry

pytestmark = pytest.mark.integration

REGISTRY_PATH = Path(__file__).parents[2] / "models" / "registry.yaml"

GOLDEN_PASSENGER = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REGISTRY_PATH)


def test_titanic_passagere_golden(registry: Registry):
    outcome = run_inference("titanic", GOLDEN_PASSENGER, registry=registry)
    assert outcome.status == "ok"
    prediction = outcome.prediction
    assert prediction.value == 1
    assert prediction.label == "a survécu"
    proba_survie = prediction.probabilities["a survécu"]
    assert proba_survie > 0.8
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0, abs=1e-3)


def test_titanic_features_incompletes_redemande(registry: Registry):
    outcome = run_inference("titanic", {"sex": "female", "pclass": 1}, registry=registry)
    assert outcome.status == "invalid"
    assert outcome.prediction is None
    assert {i.field for i in outcome.issues} == {"age", "sibsp", "parch", "fare", "embarked"}
    assert "age" in outcome.reask


def test_iris_setosa(registry: Registry):
    payload = {"sepal_length": 5.1, "sepal_width": 3.5, "petal_length": 1.4, "petal_width": 0.2}
    outcome = run_inference("iris", payload, registry=registry)
    assert outcome.status == "ok"
    assert outcome.prediction.label == "setosa"
    assert outcome.prediction.probabilities["setosa"] > 0.9


def test_california_regression_plausible(registry: Registry):
    payload = {
        "med_inc": 8.3252,
        "house_age": 41.0,
        "ave_rooms": 6.98,
        "ave_bedrms": 1.02,
        "population": 322.0,
        "ave_occup": 2.55,
        "latitude": 37.88,
        "longitude": -122.23,
    }
    outcome = run_inference("california_housing", payload, registry=registry)
    assert outcome.status == "ok"
    assert 3.0 < outcome.prediction.value < 6.0
    assert outcome.prediction.unit == "centaines de milliers de dollars"


def test_determinisme(registry: Registry):
    first = run_inference("titanic", GOLDEN_PASSENGER, registry=registry)
    second = run_inference("titanic", GOLDEN_PASSENGER, registry=registry)
    assert first.prediction.probabilities == second.prediction.probabilities
