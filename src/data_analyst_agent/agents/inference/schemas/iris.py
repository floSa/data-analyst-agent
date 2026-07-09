"""Schéma de features Iris (classification de l'espèce)."""

from pydantic import BaseModel, ConfigDict, Field


class IrisFeatures(BaseModel):
    """Mesures en centimètres des sépales et pétales."""

    model_config = ConfigDict(extra="forbid")

    sepal_length: float = Field(ge=0, le=10, description="Longueur du sépale (cm)")
    sepal_width: float = Field(ge=0, le=10, description="Largeur du sépale (cm)")
    petal_length: float = Field(ge=0, le=10, description="Longueur du pétale (cm)")
    petal_width: float = Field(ge=0, le=10, description="Largeur du pétale (cm)")
