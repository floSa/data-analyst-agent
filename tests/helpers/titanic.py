"""Aides Titanic partagées par les tests : données de référence et seed Postgres.

Le calcul pandas de référence sert d'oracle aux scénarios golden (CADRAGE §12) :
la valeur renvoyée par le pipeline doit coïncider avec lui.
"""

from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

TITANIC_CSV = Path(__file__).parents[2] / "sources" / "titanic.csv"

DDL = """
CREATE TABLE classes (
    class_id INTEGER PRIMARY KEY,
    level INTEGER NOT NULL,
    label TEXT NOT NULL
);
CREATE TABLE passengers (
    passenger_id INTEGER PRIMARY KEY,
    name TEXT,
    sex TEXT NOT NULL,
    age DOUBLE PRECISION,
    sibsp INTEGER,
    parch INTEGER,
    fare DOUBLE PRECISION,
    embarked TEXT,
    class_id INTEGER NOT NULL REFERENCES classes(class_id),
    survived INTEGER NOT NULL
);
"""

CLASSES = [(1, 1, "1re classe"), (2, 2, "2e classe"), (3, 3, "3e classe")]


def load_titanic_df() -> pd.DataFrame:
    """Le CSV Titanic vendorisé, colonnes normalisées en minuscules."""
    df = pd.read_csv(TITANIC_CSV)
    df.columns = [c.lower() for c in df.columns]
    return df


def golden_survival_rate_female_first_class() -> float:
    """Oracle pandas du scénario golden n°1, en pourcentage arrondi à 2 déc."""
    df = load_titanic_df()
    subset = df[(df["sex"] == "female") & (df["pclass"] == 1)]
    return round(float(subset["survived"].mean()) * 100, 2)


def seed_titanic_postgres(engine: Engine) -> None:
    """Crée le schéma multi-tables (passengers + classes, FK) et insère le CSV."""
    df = load_titanic_df()
    with engine.begin() as connection:
        for statement in DDL.split(";"):
            if statement.strip():
                connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO classes (class_id, level, label) VALUES (:i, :l, :t)"),
            [{"i": i, "l": lvl, "t": label} for i, lvl, label in CLASSES],
        )
        rows = [
            {
                "pid": int(r.passengerid),
                "name": r.name,
                "sex": r.sex,
                "age": None if pd.isna(r.age) else float(r.age),
                "sibsp": int(r.sibsp),
                "parch": int(r.parch),
                "fare": None if pd.isna(r.fare) else float(r.fare),
                "embarked": None if pd.isna(r.embarked) else r.embarked,
                "class_id": int(r.pclass),
                "survived": int(r.survived),
            }
            for r in df.itertuples(index=False)
        ]
        connection.execute(
            text(
                "INSERT INTO passengers (passenger_id, name, sex, age, sibsp, parch,"
                " fare, embarked, class_id, survived)"
                " VALUES (:pid, :name, :sex, :age, :sibsp, :parch, :fare, :embarked,"
                " :class_id, :survived)"
            ),
            rows,
        )
