"""Schéma de features Titanic — source de vérité écrite à la main (CADRAGE §7-③)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from data_analyst_agent.agents.inference.schemas.marques import ACCOMPAGNANTS


class TitanicFeatures(BaseModel):
    """Features attendues par le modèle de survie Titanic."""

    model_config = ConfigDict(extra="forbid")

    sex: Literal["male", "female"] = Field(description="Sexe du passager")
    pclass: Literal[1, 2, 3] = Field(description="Classe du billet (1re, 2e, 3e)")
    age: float = Field(ge=0, le=100, description="Âge en années")
    # Les deux compteurs d'ACCOMPAGNANTS, et ils sont marqués comme tels. La
    # marque est un fait sur le jeu de données — ces deux colonnes comptent des
    # personnes qui voyagent avec le passager — et c'est ici qu'un fait sur le
    # jeu de données s'écrit. Ce qu'elle permet : lire « sans famille à bord »
    # comme ce qu'elle est, une valeur pour LES DEUX, là où le modèle n'en
    # renseignait qu'un (cf. `agents/inference/accompagnants`).
    #
    # `json_schema_extra` ne change ni la validation ni ce que le planificateur
    # lit : `describe_features` ne rend que `description` et les valeurs
    # autorisées. Le prompt ne bouge pas d'un caractère.
    sibsp: int = Field(
        ge=0,
        le=10,
        description="Frères/sœurs + conjoint à bord",
        json_schema_extra={ACCOMPAGNANTS: True},
    )
    parch: int = Field(
        ge=0, le=10, description="Parents + enfants à bord", json_schema_extra={ACCOMPAGNANTS: True}
    )
    fare: float = Field(ge=0, le=600, description="Prix du billet en livres")
    embarked: Literal["S", "C", "Q"] = Field(
        description="Port d'embarquement (S=Southampton, C=Cherbourg, Q=Queenstown)"
    )
