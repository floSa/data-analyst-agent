"""Registry léger des modèles ML métier (YAML + joblib) — CADRAGE §7-③.

Un fichier ``models/registry.yaml`` décrit dataset -> {artefact, tâche, cibles}.
Cible d'évolution : MLflow Model Registry (Apache-2.0), même interface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import joblib
import yaml
from pydantic import BaseModel


class ModelEntry(BaseModel):
    dataset: str
    task: Literal["classification", "regression"]
    model_path: Path  # relatif au registry.yaml
    target: str
    labels: dict[str, str] | None = None  # classe -> libellé humain
    unit: str | None = None  # unité de la valeur prédite (régression)
    description: str = ""


class Registry:
    """Charge les entrées du YAML et met en cache les artefacts joblib."""

    def __init__(self, entries: list[ModelEntry], base_dir: Path) -> None:
        self._entries = {entry.dataset: entry for entry in entries}
        self._base_dir = base_dir
        self._cache: dict[str, Any] = {}

    @classmethod
    def load(cls, registry_path: Path) -> Registry:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        entries = [ModelEntry.model_validate(raw) for raw in data.get("models", [])]
        return cls(entries, registry_path.parent)

    @property
    def datasets(self) -> list[str]:
        return sorted(self._entries)

    def get(self, dataset: str) -> ModelEntry:
        try:
            return self._entries[dataset]
        except KeyError:
            known = ", ".join(self.datasets) or "(registre vide)"
            raise KeyError(f"modèle inconnu : {dataset!r} — disponibles : {known}") from None

    def model(self, dataset: str) -> Any:
        """L'estimateur scikit-learn du dataset (chargé une seule fois)."""
        if dataset not in self._cache:
            entry = self.get(dataset)
            artefact_path = self._base_dir / entry.model_path
            if not artefact_path.exists():
                raise FileNotFoundError(
                    f"artefact absent : {artefact_path} — entraîner via notebooks/"
                )
            self._cache[dataset] = joblib.load(artefact_path)
        return self._cache[dataset]
