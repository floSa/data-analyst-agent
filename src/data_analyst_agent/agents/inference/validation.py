"""Validation des features + messages de relance (slot-filling, CADRAGE §7-③).

Logique unique : on valide TOUT payload (dump partiel comme formulaire
complet) ; ce qui manque ou déborde devient une liste d'anomalies structurées
et une question de relance en français. Pas de predict tant que ça ne valide
pas.
"""

from __future__ import annotations

import re
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


def _canonical(name: str) -> str:
    """Forme comparable d'un nom de feature : « MedInc », « med_inc » → « medinc »."""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def align_keys(schema: type[BaseModel], payload: dict) -> dict:
    """Réaligne les clés du payload sur les noms de champs du schéma.

    L'utilisateur (et donc le LLM qui le recopie) écrit les features avec leur
    nom d'usage — « MedInc=8.3 », le nom canonique du dataset California chez
    scikit-learn — alors que le schéma les déclare en snake_case. Sans ce
    réalignement, la validation réclamait « med_inc : valeur manquante » pour
    une valeur que l'utilisateur venait de donner, et refusait « MedInc » comme
    champ inconnu : une impasse, le même reproche à chaque tour.

    Un nom exact prime toujours ; un nom sans correspondance est laissé tel quel
    pour rester signalé comme champ inconnu.
    """
    champs = set(schema.model_fields)
    par_canon = {_canonical(champ): champ for champ in champs}
    aligne = {cle: valeur for cle, valeur in payload.items() if cle in champs}
    for cle, valeur in payload.items():
        if cle in champs:
            continue
        champ = par_canon.get(_canonical(str(cle)))
        if champ is None:
            aligne[cle] = valeur  # vraiment inconnu : le schéma le dira
        elif champ not in aligne:
            aligne[champ] = valeur
    return aligne


def validate_features(schema: type[BaseModel], payload: dict) -> ValidationOutcome:
    """Valide un payload contre le schéma du dataset ; anomalies structurées sinon."""
    payload = align_keys(schema, payload)
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
