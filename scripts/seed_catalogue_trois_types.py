"""Prépare le catalogue de mesure des TROIS NATURES DE SOURCE, à la fois.

Le catalogue réaliste (`tests/catalogues/realiste/`) mesure le choix entre
quatre sources de trois natures — mais ses trois natures sont *postgres*,
*fichier CSV* et *classeur Excel* : deux d'entre elles sont le même type de
catalogue, ``file``. Il ne peut donc rien dire du troisième type, ``duckdb``,
ni du fait qu'un agent ne confond pas des sources ouvertes par trois
adaptateurs différents.

Celui-ci déclare **les trois types en même temps**, un chacun :

- ``commandes`` — ``postgres``, deux tables jointes par ``client_id`` ;
- ``capteurs`` — ``file``, un CSV plat, versionné ;
- ``entrepot`` — ``duckdb``, une base en étoile de trois tables, avec ses clés
  primaires ET étrangères déclarées. C'est ce que ni le CSV ni le classeur ne
  peuvent porter, et c'est la raison d'être du type.

Les trois **se recoupent** — chacune a une colonne de date et une mesure
numérique, deux ont un ``montant`` — pour qu'une question ordinaire soit
réellement ambiguë. Et leurs volumétries sont franchement distinctes
(837 / 111 / 40 052) : c'est sur elles qu'on lit QUELLE source a répondu.

Les données sont engendrées, pas versionnées — une base Postgres ne se
versionne pas, un fichier ``.duckdb`` de plusieurs mégaoctets non plus. Le
tirage est figé (``random.Random(GRAINE)``), donc deux exécutions rendent les
mêmes chiffres. Le CSV, lui, est versionné : il est minuscule et c'est un
oracle. Les oracles sont imprimés à la fin ; ils vont dans le README du
catalogue, qui est versionné.

Connexion : les mêmes variables que le DSN du catalogue (``DAA_PG_*``), avec
les mêmes défauts que ``.env.example``. Base cible : ``daa_trois_types``, créée
si absente.

    uv run python scripts/seed_catalogue_trois_types.py

Idempotent : la base est recréée, le fichier ``.duckdb`` réécrit.
"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from data_analyst_agent.config import export_env_file

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = REPO / "tests" / "catalogues" / "trois-types"
BASE_DUCKDB = CATALOGUE / "entrepot.duckdb"
CSV_CAPTEURS = CATALOGUE / "capteurs.csv"

# Figée, et c'est tout l'intérêt : une mesure d'avant et une mesure d'après ne
# se comparent que si les données sont les mêmes.
GRAINE = 20260914
BASE_PG = "daa_trois_types"

VILLES = ["Nantes", "Lyon", "Lille", "Brest", "Dijon", "Nîmes"]
FAMILLES = ["croquettes", "litière", "jouets", "aquariophilie"]


def dsn(base: str) -> str:
    """Le DSN d'une base, depuis les mêmes variables que le catalogue."""
    utilisateur = os.environ.get("DAA_PG_USER", "postgres")
    mot_de_passe = os.environ.get("DAA_PG_PASSWORD", "postgres")
    hote = os.environ.get("DAA_PG_HOST", "localhost")
    port = os.environ.get("DAA_PG_PORT", "5432")
    return f"postgresql+pg8000://{utilisateur}:{mot_de_passe}@{hote}:{port}/{base}"


def recreer_la_base() -> Engine:
    """DROP puis CREATE : le seed est rejouable sans état résiduel."""
    admin = create_engine(dsn("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connexion:
        connexion.execute(text(f"DROP DATABASE IF EXISTS {BASE_PG} WITH (FORCE)"))
        connexion.execute(text(f"CREATE DATABASE {BASE_PG}"))
    admin.dispose()
    return create_engine(dsn(BASE_PG))


def semer_postgres(tirage: random.Random) -> tuple[int, int, str, str]:
    """``commandes`` : 60 clients, 777 commandes réparties sur 2023-2024."""
    moteur = recreer_la_base()
    debut = date(2023, 1, 1)
    clients = [(i, tirage.choice(VILLES), tirage.choice(["F", "M"])) for i in range(1, 61)]
    commandes = [
        (
            i,
            tirage.randint(1, 60),
            debut + timedelta(days=tirage.randint(0, 729)),
            round(tirage.uniform(5, 400), 2),
        )
        for i in range(1, 778)
    ]
    with moteur.begin() as connexion:
        connexion.execute(
            text("CREATE TABLE clients (client_id INTEGER PRIMARY KEY, ville TEXT, sexe TEXT)")
        )
        connexion.execute(
            text(
                "CREATE TABLE commandes ("
                "  commande_id INTEGER PRIMARY KEY,"
                "  client_id INTEGER REFERENCES clients(client_id),"
                "  date_commande DATE, montant NUMERIC(10,2))"
            )
        )
        for ligne in clients:
            connexion.execute(
                text("INSERT INTO clients VALUES (:a, :b, :c)"),
                {"a": ligne[0], "b": ligne[1], "c": ligne[2]},
            )
        for ligne in commandes:
            connexion.execute(
                text("INSERT INTO commandes VALUES (:a, :b, :c, :d)"),
                {"a": ligne[0], "b": ligne[1], "c": ligne[2], "d": ligne[3]},
            )
    moteur.dispose()
    jours = sorted(c[2] for c in commandes)
    return len(clients), len(commandes), str(jours[0]), str(jours[-1])


def semer_le_csv(tirage: random.Random) -> tuple[int, str, str]:
    """``capteurs`` : 111 relevés horodatés. Petit, plat, et VERSIONNÉ."""
    debut = date(2025, 3, 1)
    lignes = ["horodatage,capteur,temperature"]
    for i in range(111):
        jour = debut + timedelta(days=i)
        temperature = round(tirage.uniform(-4, 31), 1)
        lignes.append(f"{jour},capteur_{i % 7},{temperature}")
    CSV_CAPTEURS.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return 111, str(debut), str(debut + timedelta(days=110))


def semer_duckdb(tirage: random.Random) -> tuple[dict[str, int], str, str]:
    """``entrepot`` : une étoile de trois tables, clés étrangères DÉCLARÉES.

    Les contraintes ne sont pas décoratives : c'est par elles que le schéma
    arrive au modèle avec ses jointures, et c'est la seule chose qu'un CSV ou
    un classeur ne peut pas porter.
    """
    BASE_DUCKDB.unlink(missing_ok=True)
    connexion = duckdb.connect(str(BASE_DUCKDB))
    connexion.execute("CREATE TABLE magasins (magasin_id INTEGER PRIMARY KEY, ville VARCHAR)")
    connexion.execute(
        "CREATE TABLE articles (  article_id INTEGER PRIMARY KEY, libelle VARCHAR, famille VARCHAR)"
    )
    connexion.execute(
        "CREATE TABLE ventes ("
        "  vente_id INTEGER PRIMARY KEY,"
        "  magasin_id INTEGER REFERENCES magasins(magasin_id),"
        "  article_id INTEGER REFERENCES articles(article_id),"
        "  jour DATE, montant DOUBLE)"
    )
    for i in range(1, 13):
        connexion.execute(
            "INSERT INTO magasins VALUES (?, ?)", [i, f"{VILLES[i % len(VILLES)]} {i}"]
        )
    for i in range(1, 41):
        connexion.execute(
            "INSERT INTO articles VALUES (?, ?, ?)",
            [i, f"article {i:02d}", FAMILLES[i % len(FAMILLES)]],
        )
    debut = date(2024, 1, 1)
    ventes = [
        (
            i,
            tirage.randint(1, 12),
            tirage.randint(1, 40),
            debut + timedelta(days=tirage.randint(0, 365)),
            round(tirage.uniform(1, 90), 2),
        )
        for i in range(1, 40_001)
    ]
    connexion.executemany("INSERT INTO ventes VALUES (?, ?, ?, ?, ?)", ventes)
    connexion.close()
    jours = sorted(v[3] for v in ventes)
    return {"magasins": 12, "articles": 40, "ventes": 40_000}, str(jours[0]), str(jours[-1])


def main() -> None:
    export_env_file()
    tirage = random.Random(GRAINE)
    print(f"Base Postgres : {BASE_PG} sur {os.environ.get('DAA_PG_HOST', 'localhost')}")
    clients, commandes, pg_debut, pg_fin = semer_postgres(tirage)
    capteurs, csv_debut, csv_fin = semer_le_csv(tirage)
    tables, dk_debut, dk_fin = semer_duckdb(tirage)

    print("\nOracles — à recopier dans tests/catalogues/trois-types/README.md")
    print("| Source | Type | Tables | Lignes | Période couverte |")
    print("|---|---|---|---|---|")
    print(
        f"| `commandes` | postgres | 2 | {clients + commandes} "
        f"(clients : {clients}, commandes : {commandes}) | {pg_debut} → {pg_fin} |"
    )
    print(f"| `capteurs` | file (CSV) | 1 | {capteurs} | {csv_debut} → {csv_fin} |")
    detail = ", ".join(f"{t} : {n}" for t, n in tables.items())
    print(
        f"| `entrepot` | duckdb | {len(tables)} | {sum(tables.values())} ({detail}) "
        f"| {dk_debut} → {dk_fin} |"
    )
    print(f"\nBase DuckDB : {BASE_DUCKDB} ({BASE_DUCKDB.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
