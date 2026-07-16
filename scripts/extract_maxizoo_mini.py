"""Extrait un mini-échantillon versionné de la base Maxizoo, pour les tests.

La vraie base fait 180 Mo et se construit depuis un dépôt d'export : ni l'une
ni l'autre n'est disponible sur un clone neuf ou en CI. Les tests ont pourtant
besoin d'une base au grain réel — d'où cet échantillon, lui versionné.

Il n'est pas tiré au hasard : le périmètre est choisi pour que **les 6 pièges du
dictionnaire y survivent**. Un échantillon qui perdrait le magasin ONLINE ou les
lignes à `quantity = 0` laisserait passer exactement les régressions qu'on veut
attraper. Le script vérifie chaque piège et refuse d'écrire s'il en manque un.

    python scripts/extract_maxizoo_mini.py            # relit sources/maxizoo.duckdb

Le résultat (tests/fixtures/maxizoo_mini/) est commité. À régénérer seulement si
l'export change de forme.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

# Fenêtre et périmètre : les plus petits qui gardent les 6 pièges (voir _controles).
DATE_MIN, DATE_MAX = "2025-01-01", "2025-06-30"
STORES = ["ONLINE", "S01", "S12"]  # e-commerce + un grand + Brive (magasin de P060)
SKUS = [
    "SKU001",  # meilleure vente, jamais en promo sur la fenêtre
    "SKU015",  # meilleure vente univers Chat
    "SKU016",  # ciblé par P040
    "SKU022",  # ciblé par P039 ET P040
    "SKU026",  # lancé en 2024-09, donc présent sur toute la fenêtre
    "SKU049",  # lancé le 2025-02-15 : le cold start tombe DANS la fenêtre
    "SKU052",  # ciblé par P042
]
PROMOS = [
    "P039",  # produits, remise 25 %, ciblage SKU
    "P040",  # produits, remise 20 %, ciblage SKU
    "P041",  # seuils : AUCUNE ligne dans promo_scope -> tout le catalogue
    "P042",  # produits, remise 15 %, ciblage SKU
    "P060",  # ouverture_magasin : pas de scope, et un seul magasin (S12)
]

# La météo et l'inflation dépassent volontairement les ventes (piège 6).
WEATHER_MAX, INFLATION_MAX = "2025-12-31", "2025-12"


def _liste_sql(valeurs: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in valeurs)


def _requetes() -> dict[str, str]:
    stores, skus, promos = _liste_sql(STORES), _liste_sql(SKUS), _liste_sql(PROMOS)
    fenetre = f"date BETWEEN DATE '{DATE_MIN}' AND DATE '{DATE_MAX}'"
    return {
        "stores": f"SELECT * FROM stores WHERE store_id IN ({stores}) ORDER BY store_id",
        "store_hours": (
            f"SELECT * FROM store_hours WHERE store_id IN ({stores}) ORDER BY store_id, day_of_week"
        ),
        "products": f"SELECT * FROM products WHERE sku_id IN ({skus}) ORDER BY sku_id",
        "promo_calendar": (
            f"SELECT * FROM promo_calendar WHERE promo_id IN ({promos}) ORDER BY promo_id"
        ),
        # Le scope est restreint aux SKU retenus : une campagne dont tous les SKU
        # ciblés sortent du périmètre deviendrait à tort une campagne « sans
        # ciblage » (piège 5), qui porte sur tout le catalogue. Les SKU choisis
        # garantissent qu'il reste au moins une ligne à P039, P040 et P042.
        "promo_scope": (
            f"SELECT * FROM promo_scope WHERE promo_id IN ({promos}) AND sku_id IN ({skus}) "
            "ORDER BY promo_id, sku_id"
        ),
        "inflation": (
            f"SELECT * FROM inflation WHERE month <= '{INFLATION_MAX}' "
            f"AND month >= '{DATE_MIN[:7]}' ORDER BY month"
        ),
        "weather": (
            f"SELECT * FROM weather WHERE store_id IN ({stores}) "
            f"AND date BETWEEN DATE '{DATE_MIN}' AND DATE '{WEATHER_MAX}' ORDER BY date, store_id"
        ),
        "traffic_daily": (
            f"SELECT * FROM traffic_daily WHERE store_id IN ({stores}) AND {fenetre} "
            "ORDER BY date, store_id"
        ),
        "sales_daily": (
            f"SELECT * FROM sales_daily WHERE store_id IN ({stores}) AND sku_id IN ({skus}) "
            f"AND {fenetre} ORDER BY date, store_id, sku_id"
        ),
        "sales_hourly": (
            f"SELECT * FROM sales_hourly WHERE store_id IN ({stores}) AND {fenetre} "
            "ORDER BY date, store_id, hour"
        ),
    }


def _controles(connection: duckdb.DuckDBPyConnection) -> None:
    """Vérifie que les 6 pièges du dictionnaire survivent à l'échantillonnage."""
    un = lambda sql: connection.execute(sql).fetchone()[0]  # noqa: E731
    pieges = {
        "1. le e-commerce est un magasin": un(
            "SELECT count(*) FROM mini_stores WHERE is_online = 1"
        ),
        "2. quantity = 0 est une vraie ligne": un(
            "SELECT count(*) FROM mini_sales_daily WHERE quantity = 0"
        ),
        "3. absence de ligne = SKU pas encore lancé": un(
            "SELECT count(*) FROM mini_products WHERE launch_date IS NOT NULL"
        ),
        "4. revenue est censuré en rupture": un(
            "SELECT count(*) FROM mini_sales_daily WHERE is_rupture = 1"
        ),
        "5. campagne sans scope = tout le catalogue": un(
            "SELECT count(*) FROM mini_promo_calendar pc LEFT JOIN mini_promo_scope ps "
            "ON ps.promo_id = pc.promo_id WHERE ps.promo_id IS NULL"
        ),
        "6. la base contient du futur": un(
            "SELECT count(*) FROM mini_weather WHERE date > "
            "(SELECT max(date) FROM mini_sales_daily)"
        ),
    }
    for piege, compte in pieges.items():
        if compte == 0:
            raise SystemExit(
                f"L'échantillon perd le piège « {piege} » : 0 ligne le porte. "
                "Élargir le périmètre (STORES / SKUS / PROMOS / fenêtre)."
            )
        print(f"  piège {piege:<45} {compte:>6} lignes")

    # Le cold start doit tomber DANS la fenêtre, sinon le SKU est présent depuis
    # le début de l'échantillon et le piège n°3 ne se démontre plus.
    dans_fenetre = un(
        f"SELECT count(*) FROM mini_products WHERE launch_date > DATE '{DATE_MIN}' "
        f"AND launch_date < DATE '{DATE_MAX}'"
    )
    if dans_fenetre == 0:
        raise SystemExit("Aucun SKU n'est lancé pendant la fenêtre : le cold start est invisible.")


def extraire(base: Path, cible: Path, ddl_source: Path | None) -> None:
    if not base.exists():
        raise SystemExit(
            f"{base} introuvable — construisez d'abord la base complète avec "
            "`python scripts/load_maxizoo_duckdb.py --export ../base_demo`."
        )
    cible.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(base), read_only=True)

    for table, sql in _requetes().items():
        connection.execute(f"CREATE OR REPLACE TEMP VIEW mini_{table} AS {sql}")
        csv = cible / f"{table}.csv"
        escaped = str(csv).replace("'", "''")
        connection.execute(f"COPY mini_{table} TO '{escaped}' (HEADER, DELIMITER ',')")
        (compte,) = connection.execute(f"SELECT count(*) FROM mini_{table}").fetchone()
        print(f"  {table:<15} {compte:>6} lignes")

    print("\nContrôle des pièges :")
    _controles(connection)
    connection.close()

    if ddl_source is not None and ddl_source.exists():
        # Le DDL est recopié parce que les tests doivent tenir sans le dépôt
        # d'export : ils recréent la base à partir de ces CSV et de ce schéma.
        (cible / "schema.sql").write_text(ddl_source.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nDDL recopié : {cible / 'schema.sql'}")

    total = sum(f.stat().st_size for f in cible.glob("*.csv"))
    print(f"Échantillon écrit : {cible} ({total / 1000:.0f} Ko de CSV)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path("sources/maxizoo.duckdb"))
    parser.add_argument("--cible", type=Path, default=Path("tests/fixtures/maxizoo_mini"))
    parser.add_argument(
        "--ddl",
        type=Path,
        default=Path("../base_demo/schema.sql"),
        help="schema.sql de l'export, recopié dans l'échantillon",
    )
    args = parser.parse_args(argv)
    extraire(args.base.resolve(), args.cible.resolve(), args.ddl.expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
