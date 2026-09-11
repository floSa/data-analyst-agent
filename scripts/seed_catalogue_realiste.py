"""Prépare le catalogue de mesure RÉALISTE : deux bases Postgres et un classeur Excel.

Le catalogue d'ambiguïté (`tests/catalogues/ambiguite/`) isole une variable et
une seule : l'ordre de déclaration. C'est ce qu'il faut pour montrer un défaut,
et ce n'est pas ce qu'un exploitant a sous la main. Celui-ci ressemble à un vrai
déploiement — **quatre sources de trois natures** (deux Postgres multi-tables, un
CSV, un classeur Excel à deux feuilles) — et surtout **des colonnes qui se
recoupent** :

- ``sexe`` / ``sex`` vit dans ``rh``, dans ``absences`` et dans ``employes`` ;
- ``departement`` vit dans ``rh``, dans ``absences`` et dans ``employes`` ;
- une colonne de date vit dans trois sources sur quatre, jamais sous le même
  nom (``date_commande``, ``date_embauche``, ``date_debut``).

C'est ce recoupement qui rend une question ordinaire réellement ambiguë :
« combien de femmes ? » a trois réponses différentes, et « sur quelle période
portent les données ? » en a trois aussi.

Les données sont **engendrées, pas versionnées** : deux bases Postgres ne se
versionnent pas, et un classeur binaire non plus. Le tirage est donc figé
(``random.Random(GRAINE)``) pour que deux exécutions rendent les mêmes octets,
et les oracles sont imprimés à la fin — ils vont dans le README du catalogue,
qui est, lui, versionné.

Connexion : les mêmes variables que le DSN du catalogue (``DAA_PG_*``), avec les
mêmes défauts que ``.env.example``. Bases cibles : ``daa_ventes`` et ``daa_rh``,
créées si absentes.

    uv run python scripts/seed_catalogue_realiste.py

Idempotent : les tables existantes sont recréées (DROP puis CREATE), et le
classeur est réécrit.
"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = REPO / "tests" / "catalogues" / "realiste"
CLASSEUR = CATALOGUE / "absences.xlsx"

# Figée, et c'est tout l'intérêt : une mesure d'avant et une mesure d'après ne se
# comparent que sur les mêmes octets.
GRAINE = 20260911

VILLES = ["Nantes", "Rennes", "Lyon", "Lille"]
DEPARTEMENTS = ["RH", "Vente", "Technique", "Finance"]
SEXES = ["female", "male"]
STATUTS = ["livrée", "en cours", "annulée"]
MOTIFS = ["maladie", "congé", "formation"]

DDL_VENTES = """
DROP TABLE IF EXISTS commandes;
DROP TABLE IF EXISTS clients;
CREATE TABLE clients (
    client_id INTEGER PRIMARY KEY,
    nom TEXT NOT NULL,
    ville TEXT NOT NULL,
    sexe TEXT NOT NULL
);
CREATE TABLE commandes (
    commande_id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(client_id),
    date_commande DATE NOT NULL,
    montant NUMERIC(10, 2) NOT NULL,
    statut TEXT NOT NULL
);
"""

DDL_RH = """
DROP TABLE IF EXISTS salaries;
DROP TABLE IF EXISTS services;
CREATE TABLE services (
    service_id INTEGER PRIMARY KEY,
    departement TEXT NOT NULL
);
CREATE TABLE salaries (
    matricule INTEGER PRIMARY KEY,
    nom TEXT NOT NULL,
    sexe TEXT NOT NULL,
    age INTEGER NOT NULL,
    salaire INTEGER NOT NULL,
    service_id INTEGER NOT NULL REFERENCES services(service_id),
    date_embauche DATE NOT NULL
);
"""


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _dsn(database: str) -> str:
    user = _env("DAA_PG_USER", "postgres")
    password = _env("DAA_PG_PASSWORD", "change-me")
    host = _env("DAA_PG_HOST", "localhost")
    port = _env("DAA_PG_PORT", "5432")
    return f"postgresql+pg8000://{user}:{password}@{host}:{port}/{database}"


def _creer_la_base(nom: str) -> None:
    admin = create_engine(_dsn("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        existe = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": nom}
        ).scalar()
        if not existe:
            connection.execute(text(f'CREATE DATABASE "{nom}"'))
    admin.dispose()
    print(f"base '{nom}' prête")


def _executer(engine: Engine, ddl: str) -> None:
    with engine.begin() as connection:
        for instruction in ddl.split(";"):
            if instruction.strip():
                connection.execute(text(instruction))


def seed_ventes(engine: Engine, tirage: random.Random) -> dict:
    """Commandes de 2023-2024, rattachées à des clients. Porte une date."""
    _executer(engine, DDL_VENTES)
    clients = [
        {
            "i": i,
            "nom": f"client{i}",
            "ville": tirage.choice(VILLES),
            "sexe": tirage.choice(SEXES),
        }
        for i in range(1, 121)
    ]
    depart = date(2023, 1, 1)
    commandes = [
        {
            "i": i,
            "c": tirage.randint(1, 120),
            "d": depart + timedelta(days=tirage.randint(0, 729)),
            "m": round(tirage.uniform(15, 2400), 2),
            "s": tirage.choice(STATUTS),
        }
        for i in range(1, 501)
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO clients (client_id, nom, ville, sexe) VALUES (:i, :nom, :ville, :sexe)"
            ),
            clients,
        )
        connection.execute(
            text(
                "INSERT INTO commandes (commande_id, client_id, date_commande, montant, statut)"
                " VALUES (:i, :c, :d, :m, :s)"
            ),
            commandes,
        )
    dates = [c["d"] for c in commandes]
    return {
        "tables": 2,
        "lignes": len(clients) + len(commandes),
        "detail": f"clients : {len(clients)}, commandes : {len(commandes)}",
        "femmes": sum(1 for c in clients if c["sexe"] == "female"),
        "total_sexe": len(clients),
        "periode": f"{min(dates)} → {max(dates)}",
    }


def seed_rh(engine: Engine, tirage: random.Random) -> dict:
    """Salariés rattachés à des services. Porte une date et une colonne ``sexe``."""
    _executer(engine, DDL_RH)
    services = [{"i": i, "d": nom} for i, nom in enumerate(DEPARTEMENTS, start=1)]
    embauche = date(2015, 1, 1)
    salaries = [
        {
            "m": i,
            "nom": f"salarie{i}",
            "sexe": tirage.choice(SEXES),
            "age": tirage.randint(21, 64),
            "salaire": tirage.randint(28000, 92000),
            "s": tirage.randint(1, len(services)),
            "d": embauche + timedelta(days=tirage.randint(0, 3650)),
        }
        for i in range(1, 181)
    ]
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO services (service_id, departement) VALUES (:i, :d)"), services
        )
        connection.execute(
            text(
                "INSERT INTO salaries (matricule, nom, sexe, age, salaire, service_id,"
                " date_embauche) VALUES (:m, :nom, :sexe, :age, :salaire, :s, :d)"
            ),
            salaries,
        )
    dates = [s["d"] for s in salaries]
    return {
        "tables": 2,
        "lignes": len(services) + len(salaries),
        "detail": f"services : {len(services)}, salaries : {len(salaries)}",
        "femmes": sum(1 for s in salaries if s["sexe"] == "female"),
        "total_sexe": len(salaries),
        "periode": f"{min(dates)} → {max(dates)}",
    }


def ecrire_le_classeur(tirage: random.Random) -> dict:
    """Un classeur Excel à DEUX feuilles — donc une source « fichier » multi-tables.

    Deux feuilles et non une : c'est ce qui distingue un classeur d'un CSV pour
    l'agent, et ce qui fait qu'un relevé de volumétrie doit compter par table.
    """
    debut = date(2024, 1, 1)
    absences = pd.DataFrame(
        [
            {
                "matricule": tirage.randint(1, 180),
                "sexe": tirage.choice(SEXES),
                "departement": tirage.choice(DEPARTEMENTS),
                "date_debut": debut + timedelta(days=tirage.randint(0, 364)),
                "jours": tirage.randint(1, 20),
                "motif": tirage.choice(MOTIFS),
            }
            for _ in range(240)
        ]
    )
    postes = pd.DataFrame(
        [{"departement": d, "effectif_cible": tirage.randint(20, 60)} for d in DEPARTEMENTS]
    )
    CLASSEUR.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(CLASSEUR, engine="openpyxl") as classeur:
        absences.to_excel(classeur, sheet_name="absences", index=False)
        postes.to_excel(classeur, sheet_name="postes", index=False)
    return {
        "tables": 2,
        "lignes": len(absences) + len(postes),
        "detail": f"absences : {len(absences)}, postes : {len(postes)}",
        "femmes": int((absences["sexe"] == "female").sum()),
        "total_sexe": len(absences),
        "periode": f"{absences['date_debut'].min()} → {absences['date_debut'].max()}",
    }


def faits_du_csv() -> dict:
    """Le CSV d'employés est VERSIONNÉ (catalogue d'ambiguïté) : on le lit, on ne
    le réécrit pas. Ses octets sont un oracle établi, les refaire le perdrait."""
    employes = pd.read_csv(REPO / "tests" / "catalogues" / "ambiguite" / "employes.csv")
    return {
        "tables": 1,
        "lignes": len(employes),
        "detail": f"employes : {len(employes)}",
        "femmes": int((employes["sex"] == "female").sum()),
        "total_sexe": len(employes),
        "periode": "(aucune colonne de date)",
    }


def main() -> None:
    tirage = random.Random(GRAINE)
    _creer_la_base("daa_ventes")
    _creer_la_base("daa_rh")

    moteur_ventes = create_engine(_dsn("daa_ventes"))
    ventes = seed_ventes(moteur_ventes, tirage)
    moteur_ventes.dispose()

    moteur_rh = create_engine(_dsn("daa_rh"))
    rh = seed_rh(moteur_rh, tirage)
    moteur_rh.dispose()

    absences = ecrire_le_classeur(tirage)
    employes = faits_du_csv()

    print(f"\nclasseur écrit : {CLASSEUR}")
    print("\nLes oracles, à recopier dans le README du catalogue :\n")
    entetes = f"| {'Source':<10} | {'Tables':>6} | {'Lignes':>6} | {'% femmes':>8} | Période |"
    print(entetes)
    print("|---|---|---|---|---|")
    releves = (("ventes", ventes), ("rh", rh), ("employes", employes), ("absences", absences))
    for nom, faits in releves:
        part = 100 * faits["femmes"] / faits["total_sexe"]
        print(
            f"| {nom:<10} | {faits['tables']:>6} | {faits['lignes']:>6} | {part:>7.2f}% "
            f"| {faits['periode']} |"
        )
        print(f"|            |        |        |          | ({faits['detail']}) |")


if __name__ == "__main__":
    main()
