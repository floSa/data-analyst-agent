"""Schéma de features California Housing (régression du prix médian)."""

from pydantic import BaseModel, ConfigDict, Field


class CaliforniaHousingFeatures(BaseModel):
    """Caractéristiques d'un îlot résidentiel californien (recensement 1990)."""

    model_config = ConfigDict(extra="forbid")

    med_inc: float = Field(ge=0, le=20, description="Revenu médian (dizaines de milliers de $)")
    house_age: float = Field(ge=0, le=60, description="Âge médian des logements (années)")
    ave_rooms: float = Field(gt=0, le=50, description="Nombre moyen de pièces par foyer")
    ave_bedrms: float = Field(gt=0, le=10, description="Nombre moyen de chambres par foyer")
    population: float = Field(gt=0, le=40000, description="Population de l'îlot")
    ave_occup: float = Field(gt=0, le=50, description="Occupation moyenne par foyer")
    latitude: float = Field(ge=32, le=42, description="Latitude (Californie)")
    longitude: float = Field(ge=-125, le=-113, description="Longitude (Californie)")
