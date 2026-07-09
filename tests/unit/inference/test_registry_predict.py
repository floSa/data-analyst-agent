"""Registry (YAML + joblib, cache) et prédiction gardée avec modèles factices."""

from pathlib import Path

import joblib
import numpy as np
import pytest

from data_analyst_agent.agents.inference.predict import run_inference
from data_analyst_agent.agents.inference.registry import ModelEntry, Registry

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
    labels:
      "0": "n'a pas survécu"
      "1": "a survécu"
  - dataset: california_housing
    task: regression
    model_path: california_housing.joblib
    target: MedHouseVal
    unit: centaines de milliers de dollars
  - dataset: fantome
    task: classification
    model_path: absent.joblib
    target: y
"""

TITANIC_OK = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}


class FakeClassifier:
    classes_ = np.array([0, 1])

    def predict(self, features):
        return np.array([1])

    def predict_proba(self, features):
        return np.array([[0.12, 0.88]])


class FakeRegressor:
    def predict(self, features):
        return np.array([4.1391])


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    joblib.dump(FakeRegressor(), tmp_path / "california_housing.joblib")
    return Registry.load(tmp_path / "registry.yaml")


# --- registry ---------------------------------------------------------------


def test_datasets_lus_depuis_le_yaml(registry: Registry):
    assert registry.datasets == ["california_housing", "fantome", "titanic"]
    entry = registry.get("titanic")
    assert isinstance(entry, ModelEntry)
    assert entry.labels["1"] == "a survécu"


def test_dataset_inconnu(registry: Registry):
    with pytest.raises(KeyError, match="disponibles"):
        registry.get("boston")


def test_artefact_absent(registry: Registry):
    with pytest.raises(FileNotFoundError, match="entraîner"):
        registry.model("fantome")


def test_cache_du_modele(registry: Registry):
    assert registry.model("titanic") is registry.model("titanic")


# --- run_inference ------------------------------------------------------------


def test_prediction_classification(registry: Registry):
    outcome = run_inference("titanic", TITANIC_OK, registry=registry)
    assert outcome.status == "ok"
    prediction = outcome.prediction
    assert prediction.value == 1
    assert prediction.label == "a survécu"
    assert prediction.probabilities == {"n'a pas survécu": 0.12, "a survécu": 0.88}
    assert outcome.reask is None


def test_prediction_regression(registry: Registry):
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
    assert outcome.prediction.value == pytest.approx(4.1391)
    assert outcome.prediction.unit == "centaines de milliers de dollars"
    assert outcome.prediction.probabilities is None


def test_features_incompletes_pas_de_predict(tmp_path: Path):
    # registre SANS artefact : si le predict était tenté, model() exploserait
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    registry = Registry.load(tmp_path / "registry.yaml")
    outcome = run_inference("titanic", {"sex": "female"}, registry=registry)
    assert outcome.status == "invalid"
    assert outcome.prediction is None
    assert "pclass" in {i.field for i in outcome.issues}
    assert outcome.reask is not None
    assert "?" in outcome.reask


def test_features_hors_bornes_pas_de_predict(tmp_path: Path):
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    registry = Registry.load(tmp_path / "registry.yaml")
    outcome = run_inference("titanic", {**TITANIC_OK, "age": 180}, registry=registry)
    assert outcome.status == "invalid"
    assert outcome.issues[0].problem == "hors_bornes"
