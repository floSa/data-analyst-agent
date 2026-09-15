"""Déclarations `features` des sources de test (cf. `inference/correspondance`).

Une source du catalogue doit déclarer quelles colonnes alimentent quel modèle ;
les fixtures des tests sont des sources de catalogue comme les autres. Ces
raccourcis évitent de recopier sept lignes à chaque `FileSource`.
"""

from __future__ import annotations

from data_analyst_agent.agents.inference.schemas import get_schema


def identite(dataset: str) -> dict[str, dict[str, str]]:
    """La déclaration d'une table à plat dont les en-têtes portent les noms du schéma.

    C'est le cas de la plupart des fixtures : un CSV écrit pour le test, avec une
    colonne par feature. La déclaration est l'identité — et elle reste écrite,
    car c'est elle qui dit que ce fichier-là alimente ce modèle-là.
    """
    return {dataset: {feature: feature for feature in get_schema(dataset).model_fields}}


# La base Postgres multi-tables : `pclass` y est l'ENTIER porté par
# `classes.level`, et non le libellé de `classes.label`.
TITANIC_POSTGRES: dict[str, dict[str, str]] = {
    "titanic": {
        "sex": "passengers.sex",
        "pclass": "classes.level",
        "age": "passengers.age",
        "sibsp": "passengers.sibsp",
        "parch": "passengers.parch",
        "fare": "passengers.fare",
        "embarked": "passengers.embarked",
    }
}
