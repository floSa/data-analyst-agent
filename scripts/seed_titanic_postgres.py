"""Seed de la base Postgres « titanic » (source multi-tables du catalogue).

Crée le schéma à deux tables jointes par clé étrangère — ``classes`` et
``passengers`` (FK ``class_id``) — et l'alimente depuis le CSV vendorisé
``sources/titanic.csv``. C'est ce que la source ``titanic`` du catalogue
attend pour répondre aux questions à jointure.

Connexion : mêmes variables que le DSN du catalogue (``DAA_PG_*``), avec les
mêmes défauts que ``.env.example`` :

    DAA_PG_HOST (localhost) DAA_PG_PORT (5432)
    DAA_PG_USER (postgres)  DAA_PG_PASSWORD (change-me)

Base cible : ``titanic`` (créée si absente).

Lancement :

    uv run python scripts/seed_titanic_postgres.py

Idempotent : les tables existantes sont recréées (DROP puis CREATE).
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REPO = Path(__file__).resolve().parents[1]
TITANIC_CSV = REPO / "sources" / "titanic.csv"
DB_NAME = "titanic"

DDL = """
DROP TABLE IF EXISTS passengers;
DROP TABLE IF EXISTS classes;
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


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _dsn(database: str) -> str:
    user = _env("DAA_PG_USER", "postgres")
    password = _env("DAA_PG_PASSWORD", "change-me")
    host = _env("DAA_PG_HOST", "localhost")
    port = _env("DAA_PG_PORT", "5432")
    return f"postgresql+pg8000://{user}:{password}@{host}:{port}/{database}"


def _ensure_database() -> None:
    """Crée la base ``titanic`` si elle n'existe pas (via la base admin ``postgres``)."""
    admin = create_engine(_dsn("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
            print(f"base '{DB_NAME}' créée")
        else:
            print(f"base '{DB_NAME}' déjà présente")
    admin.dispose()


def seed(engine: Engine) -> None:
    df = pd.read_csv(TITANIC_CSV)
    df.columns = [c.lower() for c in df.columns]
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
    print(f"{len(rows)} passagers insérés dans {DB_NAME}.passengers")


def main() -> None:
    _ensure_database()
    engine = create_engine(_dsn(DB_NAME))
    seed(engine)
    engine.dispose()
    print("seed terminé.")


if __name__ == "__main__":
    main()
