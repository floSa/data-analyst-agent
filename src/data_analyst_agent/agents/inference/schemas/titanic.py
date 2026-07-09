"""Schéma de features Titanic — source de vérité écrite à la main (CADRAGE §7-③)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TitanicFeatures(BaseModel):
    """Features attendues par le modèle de survie Titanic."""

    model_config = ConfigDict(extra="forbid")

    sex: Literal["male", "female"] = Field(description="Sexe du passager")
    pclass: Literal[1, 2, 3] = Field(description="Classe du billet (1re, 2e, 3e)")
    age: float = Field(ge=0, le=100, description="Âge en années")
    sibsp: int = Field(ge=0, le=10, description="Frères/sœurs + conjoint à bord")
    parch: int = Field(ge=0, le=10, description="Parents + enfants à bord")
    fare: float = Field(ge=0, le=600, description="Prix du billet en livres")
    embarked: Literal["S", "C", "Q"] = Field(
        description="Port d'embarquement (S=Southampton, C=Cherbourg, Q=Queenstown)"
    )
