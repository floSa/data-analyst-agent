"""Prédiction gardée : valide -> (relance | predict déterministe) — CADRAGE §7-③.

Aucun LLM ici : le calcul est 100 % déterministe. Le LLM (orchestrateur)
formule ensuite la réponse en langage naturel à partir de l'objet Prediction.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import get_schema
from data_analyst_agent.agents.inference.validation import (
    FeatureIssue,
    format_reask,
    validate_features,
)


class Prediction(BaseModel):
    dataset: str
    task: Literal["classification", "regression"]
    value: float | int | str
    label: str | None = None  # libellé humain de la classe prédite
    unit: str | None = None  # unité (régression)
    probabilities: dict[str, float] | None = None  # libellé -> probabilité


class InferenceOutcome(BaseModel):
    """Soit une prédiction, soit une relance structurée — jamais les deux."""

    status: Literal["ok", "invalid"]
    prediction: Prediction | None = None
    issues: list[FeatureIssue] = Field(default_factory=list)
    reask: str | None = None


def run_inference(dataset: str, payload: dict, *, registry: Registry) -> InferenceOutcome:
    """Valide les features puis prédit. Features incomplètes => relance, pas de predict."""
    entry = registry.get(dataset)
    schema = get_schema(dataset)
    outcome = validate_features(schema, payload)
    if not outcome.valid:
        return InferenceOutcome(
            status="invalid",
            issues=outcome.issues,
            reask=format_reask(dataset, outcome.issues),
        )

    model = registry.model(dataset)
    features_frame = pd.DataFrame([outcome.features])

    if entry.task == "classification":
        predicted = model.predict(features_frame)[0]
        labels = entry.labels or {}
        probabilities = None
        if hasattr(model, "predict_proba"):
            raw_probabilities = model.predict_proba(features_frame)[0]
            probabilities = {
                labels.get(str(cls), str(cls)): round(float(p), 4)
                for cls, p in zip(model.classes_, raw_probabilities, strict=True)
            }
        value = predicted.item() if hasattr(predicted, "item") else predicted
        prediction = Prediction(
            dataset=dataset,
            task=entry.task,
            value=value,
            label=labels.get(str(value)),
            probabilities=probabilities,
        )
    else:
        value = float(model.predict(features_frame)[0])
        prediction = Prediction(
            dataset=dataset,
            task=entry.task,
            value=round(value, 4),
            unit=entry.unit,
        )
    return InferenceOutcome(status="ok", prediction=prediction)
