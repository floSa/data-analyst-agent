"""Registry (YAML + joblib, cache) et prédiction gardée avec modèles factices."""

from pathlib import Path
from typing import Literal

import joblib
import numpy as np
import pytest
from pydantic import BaseModel, ConfigDict, Field

from data_analyst_agent.agents.inference import schemas
from data_analyst_agent.agents.inference.predict import run_batch_inference, run_inference
from data_analyst_agent.agents.inference.registry import ModelEntry, Registry

REGISTRY_YAML = """
models:
  - dataset: maxizoo_sales
    task: regression
    model_path: maxizoo_sales.joblib
    target: quantity
    unit: unités vendues
  - dataset: jouet_classif
    task: classification
    model_path: jouet_classif.joblib
    target: gagnant
    labels:
      "0": "perdant"
      "1": "gagnant"
  - dataset: fantome
    task: classification
    model_path: absent.joblib
    target: y
"""

VENTES_OK = {
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


class JouetFeatures(BaseModel):
    """Schéma d'un dataset de classification purement local aux tests.

    Aucun modèle livré ne classifie : le seul du registre est une régression.
    Le chemin classification de `predict.py` (libellés, probabilités,
    `classes_`) reste pourtant du code exécuté dès qu'on ajoutera un tel
    modèle — on le couvre donc ici plutôt que de le laisser sans filet.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=10, description="Une mesure quelconque")
    categorie: Literal["a", "b"] = Field(description="Une catégorie")


@pytest.fixture(autouse=True)
def _schema_jouet(monkeypatch):
    monkeypatch.setitem(schemas.SCHEMAS, "jouet_classif", JouetFeatures)
    monkeypatch.setitem(schemas.SCHEMAS, "fantome", JouetFeatures)


class FakeClassifier:
    classes_ = np.array([0, 1])

    def predict(self, features):
        return np.ones(len(features), dtype=int)

    def predict_proba(self, features):
        return np.tile([0.12, 0.88], (len(features), 1))


class FakeRegressor:
    def predict(self, features):
        return np.full(len(features), 3.2508)


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "jouet_classif.joblib")
    joblib.dump(FakeRegressor(), tmp_path / "maxizoo_sales.joblib")
    return Registry.load(tmp_path / "registry.yaml")


# --- registry ---------------------------------------------------------------


def test_datasets_lus_depuis_le_yaml(registry: Registry):
    assert registry.datasets == ["fantome", "jouet_classif", "maxizoo_sales"]
    entry = registry.get("maxizoo_sales")
    assert isinstance(entry, ModelEntry)
    assert entry.unit == "unités vendues"


def test_dataset_inconnu(registry: Registry):
    with pytest.raises(KeyError, match="disponibles"):
        registry.get("titanic")


def test_artefact_absent(registry: Registry):
    with pytest.raises(FileNotFoundError, match="entraîner"):
        registry.model("fantome")


def test_cache_du_modele(registry: Registry):
    assert registry.model("maxizoo_sales") is registry.model("maxizoo_sales")


# --- run_inference ------------------------------------------------------------


def test_prediction_regression(registry: Registry):
    outcome = run_inference("maxizoo_sales", VENTES_OK, registry=registry)
    assert outcome.status == "ok"
    assert outcome.prediction.value == pytest.approx(3.2508)
    assert outcome.prediction.unit == "unités vendues"
    assert outcome.prediction.probabilities is None
    assert outcome.reask is None


def test_prediction_classification(registry: Registry):
    outcome = run_inference("jouet_classif", {"x": 1.0, "categorie": "a"}, registry=registry)
    assert outcome.status == "ok"
    prediction = outcome.prediction
    assert prediction.value == 1
    assert prediction.label == "gagnant"
    assert prediction.probabilities == {"perdant": 0.12, "gagnant": 0.88}


def test_features_incompletes_pas_de_predict(tmp_path: Path):
    # registre SANS artefact : si le predict était tenté, model() exploserait
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    registry = Registry.load(tmp_path / "registry.yaml")
    outcome = run_inference("maxizoo_sales", {"store_type": "grand"}, registry=registry)
    assert outcome.status == "invalid"
    assert outcome.prediction is None
    assert "commodity_group" in {i.field for i in outcome.issues}
    assert outcome.reask is not None
    assert "?" in outcome.reask


def test_features_hors_bornes_pas_de_predict(tmp_path: Path):
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    registry = Registry.load(tmp_path / "registry.yaml")
    outcome = run_inference("maxizoo_sales", {**VENTES_OK, "month": 13}, registry=registry)
    assert outcome.status == "invalid"
    assert outcome.issues[0].problem == "hors_bornes"


# --- run_batch_inference --------------------------------------------------------


def test_lot_mixte_valides_et_invalides(registry: Registry):
    payloads = [
        VENTES_OK,
        {**VENTES_OK, "day_of_week": 9},  # hors bornes -> écartée
        {**VENTES_OK, "month": 3},
    ]
    outcome = run_batch_inference("maxizoo_sales", payloads, registry=registry)
    assert outcome.total == 3
    assert outcome.valid_count == 2
    assert outcome.invalid_count == 1
    assert outcome.rows[1].prediction is None
    assert outcome.rows[1].issues[0].problem == "hors_bornes"
    assert outcome.values() == [pytest.approx(3.2508)] * 2
    assert outcome.unit == "unités vendues"


def test_lot_entierement_invalide_ne_charge_pas_le_modele(tmp_path: Path):
    # registre SANS artefact : si le predict était tenté, model() exploserait
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    registry = Registry.load(tmp_path / "registry.yaml")
    outcome = run_batch_inference(
        "maxizoo_sales", [{"store_type": "grand"}, {"store_type": "petit"}], registry=registry
    )
    assert outcome.valid_count == 0
    assert all(row.issues for row in outcome.rows)


def test_lot_classification_compte_les_libelles(registry: Registry):
    payload = {"x": 1.0, "categorie": "a"}
    outcome = run_batch_inference("jouet_classif", [payload, payload], registry=registry)
    assert outcome.valid_count == 2
    assert outcome.label_counts() == {"gagnant": 2}


def test_lot_regression_pas_de_libelles(registry: Registry):
    outcome = run_batch_inference("maxizoo_sales", [VENTES_OK, VENTES_OK], registry=registry)
    assert outcome.label_counts() == {"3.2508": 2}  # pas de libellés en régression
