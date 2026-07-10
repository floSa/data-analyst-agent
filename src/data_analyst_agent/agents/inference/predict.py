"""Prédiction gardée : valide -> (relance | predict déterministe) — CADRAGE §7-③.

Aucun LLM ici : le calcul est 100 % déterministe. Le LLM (orchestrateur)
formule ensuite la réponse en langage naturel à partir de l'objet Prediction.

Deux modes : unitaire (``run_inference`` — relance si features incomplètes)
et en lot (``run_batch_inference`` — chaque ligne validée, les valides
prédites en un seul appel modèle vectorisé, les invalides écartées et
comptées).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from data_analyst_agent.agents.inference.registry import ModelEntry, Registry
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


def _predict_frame(
    entry: ModelEntry, model: Any, frame: pd.DataFrame, dataset: str
) -> list[Prediction]:
    """Prédit chaque ligne du DataFrame en un seul appel modèle (vectorisé)."""
    predictions: list[Prediction] = []
    if entry.task == "classification":
        raw = model.predict(frame)
        labels = entry.labels or {}
        probas = model.predict_proba(frame) if hasattr(model, "predict_proba") else None
        for i, predicted in enumerate(raw):
            value = predicted.item() if hasattr(predicted, "item") else predicted
            probabilities = None
            if probas is not None:
                probabilities = {
                    labels.get(str(cls), str(cls)): round(float(p), 4)
                    for cls, p in zip(model.classes_, probas[i], strict=True)
                }
            predictions.append(
                Prediction(
                    dataset=dataset,
                    task=entry.task,
                    value=value,
                    label=labels.get(str(value)),
                    probabilities=probabilities,
                )
            )
    else:
        predictions.extend(
            Prediction(
                dataset=dataset, task=entry.task, value=round(float(value), 4), unit=entry.unit
            )
            for value in model.predict(frame)
        )
    return predictions


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
    prediction = _predict_frame(entry, model, pd.DataFrame([outcome.features]), dataset)[0]
    return InferenceOutcome(status="ok", prediction=prediction)


# --- prédiction en lot ----------------------------------------------------------


class BatchRowResult(BaseModel):
    """Le sort d'une ligne du lot : prédite, ou écartée avec ses anomalies."""

    index: int
    prediction: Prediction | None = None
    issues: list[FeatureIssue] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.prediction is not None


class BatchInferenceOutcome(BaseModel):
    """Prédiction en lot : agrégats + détail ligne à ligne."""

    dataset: str
    task: Literal["classification", "regression"]
    unit: str | None = None
    rows: list[BatchRowResult] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def valid_count(self) -> int:
        return sum(1 for row in self.rows if row.valid)

    @property
    def invalid_count(self) -> int:
        return self.total - self.valid_count

    def label_counts(self) -> dict[str, int]:
        """Répartition des classes prédites (classification), décroissante."""
        counts = Counter(
            row.prediction.label or str(row.prediction.value)
            for row in self.rows
            if row.prediction is not None
        )
        return dict(counts.most_common())

    def values(self) -> list[float]:
        """Les valeurs prédites (régression)."""
        return [float(row.prediction.value) for row in self.rows if row.prediction is not None]


def run_batch_inference(
    dataset: str, payloads: list[dict], *, registry: Registry
) -> BatchInferenceOutcome:
    """Valide chaque ligne ; les lignes valides sont prédites en un seul appel.

    Les lignes invalides sont écartées (jamais prédites) et gardent leurs
    anomalies structurées — le lot n'échoue pas pour quelques lignes sales.
    """
    entry = registry.get(dataset)
    schema = get_schema(dataset)
    results = [BatchRowResult(index=i) for i in range(len(payloads))]
    valid_features: list[dict] = []
    valid_positions: list[int] = []
    for i, payload in enumerate(payloads):
        outcome = validate_features(schema, payload)
        if outcome.valid and outcome.features is not None:
            valid_positions.append(i)
            valid_features.append(outcome.features)
        else:
            results[i].issues = outcome.issues
    if valid_features:
        model = registry.model(dataset)
        predictions = _predict_frame(entry, model, pd.DataFrame(valid_features), dataset)
        for position, prediction in zip(valid_positions, predictions, strict=True):
            results[position].prediction = prediction
    return BatchInferenceOutcome(dataset=dataset, task=entry.task, unit=entry.unit, rows=results)
