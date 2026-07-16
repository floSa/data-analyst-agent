"""Schémas Pydantic des features — un par dataset, source de vérité (CADRAGE §7-③)."""

from typing import Literal, get_args, get_origin

from pydantic import BaseModel

from data_analyst_agent.agents.inference.schemas.california import CaliforniaHousingFeatures
from data_analyst_agent.agents.inference.schemas.iris import IrisFeatures
from data_analyst_agent.agents.inference.schemas.titanic import TitanicFeatures

SCHEMAS: dict[str, type[BaseModel]] = {
    "titanic": TitanicFeatures,
    "iris": IrisFeatures,
    "california_housing": CaliforniaHousingFeatures,
}


def get_schema(dataset: str) -> type[BaseModel]:
    try:
        return SCHEMAS[dataset]
    except KeyError:
        known = ", ".join(sorted(SCHEMAS))
        raise KeyError(f"pas de schéma de features pour {dataset!r} — connus : {known}") from None


def field_choices(schema: type[BaseModel], field: str) -> list | None:
    """Valeurs autorisées d'un champ ``Literal`` (sinon ``None``)."""
    info = schema.model_fields.get(field)
    if info is None:
        return None
    annotation = info.annotation
    return list(get_args(annotation)) if get_origin(annotation) is Literal else None


def describe_features(schema: type[BaseModel]) -> str:
    """Décrit les features au planificateur : sens ET valeurs autorisées.

    Le schéma sait qu'``embarked`` est un « Port d'embarquement (S=Southampton,
    C=Cherbourg, Q=Queenstown) » et n'accepte que S/C/Q. N'envoyer que les NOMS
    des champs jetait ce savoir : le modèle ne pouvait pas traduire « embarquée
    à Southampton » en ``embarked='S'`` — jamais on ne le lui avait montré — et
    redemandait indéfiniment une information déjà donnée.
    """
    lignes = []
    for nom, info in schema.model_fields.items():
        precisions = []
        if info.description:
            precisions.append(info.description)
        choix = field_choices(schema, nom)
        if choix:
            precisions.append("valeurs autorisées : " + ", ".join(repr(c) for c in choix))
        suffixe = f" — {' ; '.join(precisions)}" if precisions else ""
        lignes.append(f"    * {nom}{suffixe}")
    return "\n".join(lignes)


__all__ = [
    "SCHEMAS",
    "CaliforniaHousingFeatures",
    "IrisFeatures",
    "TitanicFeatures",
    "describe_features",
    "field_choices",
    "get_schema",
]
