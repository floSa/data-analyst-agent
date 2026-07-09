"""Schémas Pydantic des features — un par dataset, source de vérité (CADRAGE §7-③)."""

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


__all__ = [
    "SCHEMAS",
    "CaliforniaHousingFeatures",
    "IrisFeatures",
    "TitanicFeatures",
    "get_schema",
]
