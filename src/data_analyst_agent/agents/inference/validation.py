"""Validation des features + messages de relance (slot-filling, CADRAGE §7-③).

Logique unique : on valide TOUT payload (dump partiel comme formulaire
complet) ; ce qui manque ou déborde devient une liste d'anomalies structurées
et une question de relance en français. Pas de predict tant que ça ne valide
pas.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

ProblemKind = Literal[
    "manquant", "hors_bornes", "valeur_non_autorisee", "type_invalide", "champ_inconnu"
]

_KIND_BY_PYDANTIC_TYPE: dict[str, ProblemKind] = {
    "missing": "manquant",
    "greater_than": "hors_bornes",
    "greater_than_equal": "hors_bornes",
    "less_than": "hors_bornes",
    "less_than_equal": "hors_bornes",
    "literal_error": "valeur_non_autorisee",
    "enum": "valeur_non_autorisee",
    "extra_forbidden": "champ_inconnu",
}


class FeatureIssue(BaseModel):
    field: str
    problem: ProblemKind
    message: str


class ValidationOutcome(BaseModel):
    valid: bool
    features: dict | None = None
    issues: list[FeatureIssue] = Field(default_factory=list)

    @property
    def missing_fields(self) -> list[str]:
        return [i.field for i in self.issues if i.problem == "manquant"]


def _describe_field(schema: type[BaseModel], field: str) -> str:
    info = schema.model_fields.get(field)
    if info is not None and info.description:
        return f" ({info.description})"
    return ""


def _to_issue(schema: type[BaseModel], error: dict) -> FeatureIssue:
    field = str(error["loc"][0]) if error["loc"] else "(racine)"
    kind = _KIND_BY_PYDANTIC_TYPE.get(error["type"], "type_invalide")
    received = error.get("input")
    if kind == "manquant":
        message = f"{field}{_describe_field(schema, field)} : valeur manquante"
    elif kind == "champ_inconnu":
        message = f"{field} : champ inconnu pour ce modèle"
    else:
        message = f"{field}{_describe_field(schema, field)} : {error['msg']} (reçu : {received!r})"
    return FeatureIssue(field=field, problem=kind, message=message)


def validate_features(schema: type[BaseModel], payload: dict) -> ValidationOutcome:
    """Valide un payload contre le schéma du dataset ; anomalies structurées sinon."""
    try:
        instance = schema.model_validate(payload)
    except ValidationError as exc:
        issues = [_to_issue(schema, e) for e in exc.errors()]
        return ValidationOutcome(valid=False, issues=issues)
    return ValidationOutcome(valid=True, features=instance.model_dump())


def format_reask(dataset: str, issues: list[FeatureIssue]) -> str:
    """Question de relance à poser à l'utilisateur pour compléter/corriger."""
    lines = [f"Je ne peux pas encore lancer la prédiction {dataset} :"]
    lines.extend(f"- {issue.message}" for issue in issues)
    lines.append("Peux-tu me donner ces informations ?")
    return "\n".join(lines)
