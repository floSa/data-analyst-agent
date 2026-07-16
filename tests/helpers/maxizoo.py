"""Aides Maxizoo partagées par les tests : mini-base réelle et oracle pandas.

L'échantillon de `tests/fixtures/maxizoo_mini/` est un extrait de la vraie base
(S1 2025, 3 magasins, 7 SKU), choisi pour porter les 6 pièges du dictionnaire —
voir `scripts/extract_maxizoo_mini.py`. Il est versionné, contrairement à la
base complète (180 Mo, construite depuis le dépôt d'export) : les tests tournent
donc sur un clone neuf, au grain et avec les contraintes du réel.

Le calcul pandas de référence sert d'oracle aux scénarios golden (CADRAGE §12) :
la valeur renvoyée par le pipeline doit coïncider avec lui.
"""

import re
from pathlib import Path

import duckdb
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

FIXTURE = Path(__file__).parents[1] / "fixtures" / "maxizoo_mini"

# Ordre des dépendances (FK) : une table n'est chargée qu'après ses référencées.
TABLES = [
    "stores",
    "store_hours",
    "products",
    "promo_calendar",
    "promo_scope",
    "inflation",
    "weather",
    "traffic_daily",
    "sales_daily",
    "sales_hourly",
]


def load_table(name: str) -> pd.DataFrame:
    """Une table de l'échantillon, telle qu'elle est sur le disque."""
    return pd.read_csv(FIXTURE / f"{name}.csv")


def golden_ca_2025_par_magasin() -> list[tuple[str, float]]:
    """Oracle pandas du scénario golden n°1 : CA du S1 2025 par magasin, décroissant.

    `Canal Online` doit sortir en tête : c'est le piège n°1 (le e-commerce est
    une ligne de `stores`), et tout l'intérêt de la question.
    """
    sales = load_table("sales_daily")
    stores = load_table("stores")
    joined = sales.merge(stores, on="store_id")
    ca = joined.groupby("store_name")["revenue"].sum().round(2)
    return [(nom, float(valeur)) for nom, valeur in ca.sort_values(ascending=False).items()]


def features_frame() -> pd.DataFrame:
    """Les lignes de l'échantillon, au format des features du modèle de prévision.

    Reproduit en pandas la requête d'entraînement du notebook (jointures
    magasin/produit/météo), pour pouvoir mesurer un effet **sur la population**
    plutôt qu'en un point. Le modèle étant bruité au grain SKU x magasin x jour,
    une comparaison ponctuelle peut donner le signe inverse de l'effet moyen —
    ce n'est pas un défaut du modèle, c'est la variance des données.

    `discount_rate` et `promo_type` sont volontairement absents : c'est ce que
    l'appelant fait varier.
    """
    ventes = load_table("sales_daily").merge(load_table("stores"), on="store_id")
    ventes = ventes.merge(load_table("products"), on="sku_id")
    ventes = ventes.merge(load_table("weather"), on=["store_id", "date"])
    dates = pd.to_datetime(ventes["date"])
    return pd.DataFrame(
        {
            "store_type": ventes["store_type"],
            "commodity_group": ventes["commodity_group"],
            "brand_type": ventes["brand_type"],
            "base_price": ventes["base_price"].astype(float),
            "day_of_week": dates.dt.weekday,  # pandas : 0 = lundi, comme le schéma
            "month": dates.dt.month,
            "temp_anomaly": ventes["temp_anomaly"].astype(float),
        }
    )


def _ddl_statements(dialecte: str) -> list[str]:
    """Le DDL de l'export, découpé en instructions.

    Sur DuckDB il passe tel quel. Sur PostgreSQL aussi — c'est son dialecte
    d'origine ; seuls les index sont retirés, inutiles sur 3 666 lignes et
    autant de temps perdu à chaque montage de conteneur.

    Les commentaires sautent AVANT le découpage : le DDL en contient un qui
    porte un point-virgule (« -- population de la ville ; 0 pour ONLINE »), et
    un split naïf couperait le CREATE TABLE en plein milieu.
    """
    sql = (FIXTURE / "schema.sql").read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if dialecte == "postgresql":
        statements = [s for s in statements if not s.upper().startswith("CREATE INDEX")]
    return statements


def build_duckdb(path: Path) -> Path:
    """Construit une base DuckDB depuis l'échantillon, contraintes actives."""
    connection = duckdb.connect(str(path))
    for statement in _ddl_statements("duckdb"):
        connection.execute(statement)
    for table in TABLES:
        csv = str(FIXTURE / f"{table}.csv").replace("'", "''")
        connection.execute(f"INSERT INTO {table} SELECT * FROM read_csv_auto('{csv}', header=true)")
    connection.close()
    return path


def seed_maxizoo_postgres(engine: Engine) -> None:
    """Crée le schéma en étoile et insère l'échantillon dans un vrai Postgres."""
    with engine.begin() as connection:
        for statement in _ddl_statements("postgresql"):
            connection.execute(text(statement))
        for table in TABLES:
            frame = load_table(table)
            colonnes = ", ".join(frame.columns)
            valeurs = ", ".join(f":{c}" for c in frame.columns)
            lignes = [
                {c: (None if pd.isna(v) else v) for c, v in ligne.items()}
                for ligne in frame.to_dict("records")
            ]
            connection.execute(text(f"INSERT INTO {table} ({colonnes}) VALUES ({valeurs})"), lignes)
