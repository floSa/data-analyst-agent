"""Doublures partagées : sandbox scriptée et modèles ML factices."""

from __future__ import annotations

import numpy as np

from data_analyst_agent.sandbox.client import SandboxResult


class ScriptedSandbox:
    """Sandbox doublée : rejoue une liste de résultats, enregistre les codes reçus."""

    def __init__(self, outcomes: list[SandboxResult]) -> None:
        self.outcomes = list(outcomes)
        self.executed: list[str] = []
        self.closed = False

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult:
        self.executed.append(code)
        return self.outcomes.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeClassifier:
    """Classifieur binaire déterministe : prédit toujours 1 à 88 %."""

    classes_ = np.array([0, 1])

    def predict(self, features):
        return np.array([1])

    def predict_proba(self, features):
        return np.array([[0.12, 0.88]])


class FakeRegressor:
    def predict(self, features):
        return np.array([4.1391])
