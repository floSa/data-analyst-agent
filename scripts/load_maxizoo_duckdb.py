"""Charge la base de démonstration Maxizoo dans un fichier DuckDB.

L'export vit dans un dépôt séparé (branche `export-db` de sales-ops-planning-poc) :

    git clone --branch export-db --single-branch \
        https://github.com/floSa/sales-ops-planning-poc.git base_demo

Ce script y lit `schema.sql` (DDL PostgreSQL, compatible DuckDB tel quel) puis
charge les CSV de `tables/`, contraintes actives. Il recopie aussi le
dictionnaire, que l'agent SQL charge en contexte (voir sources/catalogue.yaml).
Ni la base ni le dictionnaire ne sont versionnés : ils appartiennent à l'export,
et une copie versionnée dériverait de lui en silence.

    python scripts/load_maxizoo_duckdb.py --export ../base_demo

Le `chargement/duckdb.sql` de l'export ferait la même chose, mais il suppose un
CWD précis (ses chemins CSV sont relatifs) et finit par un SELECT de contrôle
dont il ne vérifie rien. On relit donc son DDL, mais on pilote les INSERT ici
pour pouvoir contrôler les volumes et échouer si un compte ne tombe pas juste.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

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

# Volumes publiés par le MANIFEST de l'export. Un chargement qui ne les retrouve
# pas est un chargement partiel : mieux vaut le dire que livrer une base amputée
# sur laquelle l'agent répondrait des chiffres faux avec aplomb.
VOLUMES_ATTENDUS = {
    "stores": 13,
    "store_hours": 91,
    "products": 60,
    "promo_calendar": 56,
    "promo_scope": 349,
    "inflation": 66,
    "weather": 26_130,
    "traffic_daily": 23_738,
    "sales_daily": 1_363_726,
    "sales_hourly": 244_952,
}


def charger(export: Path, cible: Path) -> None:
    schema_sql = export / "schema.sql"
    if not schema_sql.exists():
        raise SystemExit(
            f"{schema_sql} introuvable — --export doit pointer sur le clone de la "
            "branche export-db (celle qui contient schema.sql et tables/)."
        )

    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.unlink(missing_ok=True)  # rechargement = base neuve, pas un empilement

    connection = duckdb.connect(str(cible))
    connection.execute(schema_sql.read_text(encoding="utf-8"))

    for table in TABLES:
        csv = export / "tables" / f"{table}.csv"
        escaped = str(csv).replace("'", "''")
        connection.execute(
            f"INSERT INTO {table} SELECT * FROM read_csv_auto('{escaped}', header=true)"
        )
        (compte,) = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
        attendu = VOLUMES_ATTENDUS[table]
        if compte != attendu:
            raise SystemExit(f"{table} : {compte} lignes chargées, {attendu} attendues.")
        print(f"  {table:<15} {compte:>9,} lignes")

    connection.close()
    taille_mo = cible.stat().st_size / 1_000_000
    print(f"\nBase écrite : {cible} ({taille_mo:.0f} Mo)")

    dictionnaire = cible.parent / "maxizoo_dictionnaire.md"
    dictionnaire.write_text(
        (export / "dictionnaire.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(f"Dictionnaire : {dictionnaire}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export",
        type=Path,
        default=Path("../base_demo"),
        help="clone de la branche export-db (défaut : ../base_demo)",
    )
    parser.add_argument(
        "--cible",
        type=Path,
        default=Path("sources/maxizoo.duckdb"),
        help="fichier DuckDB à produire (défaut : sources/maxizoo.duckdb)",
    )
    args = parser.parse_args(argv)
    charger(args.export.expanduser().resolve(), args.cible.expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
