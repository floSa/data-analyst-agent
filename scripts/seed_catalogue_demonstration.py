"""Engendre le CATALOGUE DE DÉMONSTRATION — un réseau de recharge électrique.

Le produit se démontrait sur ``titanic`` et ``iris`` : un naufrage de 1912 et
des fleurs mesurées en 1936. Les deux sont d'excellents jeux d'essai et ne
ressemblent à aucun client — ni par leur volumétrie, ni par leur modélisation,
ni par les questions qu'on leur pose. Un prospect qui regarde une démonstration
y cherche SON système d'information : plusieurs bases, plusieurs formats, des
codes maison, des exports de tableur, et des chiffres qui ne tombent pas juste
du premier coup.

D'où ce catalogue : **un seul domaine métier**, l'exploitation d'un réseau de
bornes de recharge pour véhicules électriques, décliné en **cinq sources des
trois types** que le socle sait ouvrir.

===========================================================================
 Source          Type       Ce qu'elle apporte
===========================================================================
 exploitation    postgres   6 tables, clés étrangères réelles — le SI métier
 telemetrie      duckdb     547 820 lignes — la volumétrie qui se voit
 interventions   file CSV   la main courante de la maintenance
 referentiel     file CSV   la table de correspondance libellé → code
 facturation     file XLSX  un classeur à trois feuilles — l'export compta
===========================================================================

**Elles se recoupent, et c'est voulu.** ``code_station`` vit dans quatre
sources sur cinq, ``code_tarif`` dans deux, ``code_client`` dans deux,
``borne_id`` dans deux. Sans ce recoupement, le verrou de source n'aurait rien
à protéger : « combien de stations ? » n'aurait qu'une réponse, et une question
posée sans nommer la source ne risquerait rien. Ici elle en a deux — 120 dans
``exploitation``, 150 dans ``referentiel`` — et l'écart est un fait du métier,
pas un artifice : le référentiel garde les stations démontées, l'exploitation
ne connaît que celles qui tournent.

**Quatre pièges de modélisation, assumés et documentés** (chacun dans le
dictionnaire de sa source, sous ``sources/demonstration/dictionnaires/``) :

1. ``interventions.station_libelle`` est un LIBELLÉ là où on attendrait un
   code. Aucune jointure directe avec ``exploitation`` : il faut passer par
   ``referentiel``, qui est la table de correspondance.
2. ``telemetrie.releves_puissance.puissance_kw`` vaut ``-1.0`` quand le
   compteur n'a rien remonté — une VALEUR SENTINELLE, pas une puissance nulle.
   Une moyenne naïve est fausse, et fausse vers le bas.
3. ``exploitation.sessions.statut`` est un CODE d'une lettre — ``T``, ``I``,
   ``E``. Compter les lignes de la table, ce n'est pas compter les recharges :
   seules les ``T`` ont abouti.
4. ``interventions.duree_indispo_min`` vaut ``-1`` pour « non renseigné ».
   Même piège que le 2, dans un format différent, sur une autre source.

**Les données sont engendrées, pas versionnées.** Le tirage est figé
(``GRAINE``), donc deux exécutions rendent le même contenu. Ce qui est
versionné : ce script, les dictionnaires, le catalogue, et les deux CSV — ils
sont petits, et ce sont des oracles. Ce qui ne l'est pas : la base Postgres
(elle ne se versionne pas), le ``.duckdb`` (18,6 Mo) et le classeur.
Cf. ``sources/demonstration/.gitignore``.

**Le classeur est figé octet pour octet.** openpyxl horodate ce qu'il écrit à
deux endroits — ``docProps/core.xml``, et la date de chaque entrée du zip — et
réécrit ``dcterms:modified`` à la sauvegarde, en écrasant la valeur qu'on lui
pose. Deux exécutions rendraient donc deux fichiers différents à contenu
identique. ``figer_le_classeur`` neutralise les deux : le classeur redevient une
fonction de la graine seule, ce qui se vérifie au ``sha256``. Le ``.duckdb``,
lui, ne se laisse pas figer (ordre d'écriture des blocs) ; son CONTENU, si, et
c'est dit dans le ``.gitignore``.

Connexion Postgres : les mêmes variables que le DSN du catalogue (``DAA_PG_*``),
avec les mêmes défauts que ``.env.example``. Base cible : ``daa_demonstration``,
recréée à chaque exécution.

    uv run python scripts/seed_catalogue_demonstration.py

Les oracles sont imprimés à la fin : ils vont dans
``docs/sources-de-demonstration.md``, qui est versionné.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from data_analyst_agent.config import export_env_file

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = REPO / "sources" / "demonstration"
BASE_DUCKDB = CATALOGUE / "telemetrie.duckdb"
CLASSEUR = CATALOGUE / "facturation.xlsx"
CSV_INTERVENTIONS = CATALOGUE / "interventions.csv"
CSV_REFERENTIEL = CATALOGUE / "referentiel.csv"

# Figée, et c'est tout l'intérêt : une démonstration qui ne rend pas les mêmes
# chiffres d'une machine à l'autre n'est pas une démonstration, c'est un aléa.
GRAINE = 20260915
BASE_PG = "daa_demonstration"

# Le domaine, en dur : six régions, et des quartiers qui servent à fabriquer des
# libellés de station plausibles. Tout est inventé — aucune donnée réelle,
# aucune entreprise existante.
REGIONS = [
    "Bretagne",
    "Pays de la Loire",
    "Normandie",
    "Nouvelle-Aquitaine",
    "Occitanie",
    "Grand Est",
]
VILLES = {
    "Bretagne": ["Rennes", "Brest", "Quimper", "Lorient", "Vannes"],
    "Pays de la Loire": ["Nantes", "Angers", "Le Mans", "Laval", "La Roche"],
    "Normandie": ["Rouen", "Caen", "Le Havre", "Évreux", "Cherbourg"],
    "Nouvelle-Aquitaine": ["Bordeaux", "Limoges", "Poitiers", "Pau", "Angoulême"],
    "Occitanie": ["Toulouse", "Montpellier", "Nîmes", "Perpignan", "Albi"],
    "Grand Est": ["Strasbourg", "Metz", "Reims", "Nancy", "Colmar"],
}
QUARTIERS = [
    "Centre",
    "Gare",
    "Zone Nord",
    "Parc des Expos",
    "Hôpital",
    "Université",
    "Port",
    "Aéroport",
    "Marché",
    "Stade",
]
TYPES_PRISE = ["T2", "CCS", "CHAdeMO"]
PUISSANCES = [7.4, 22.0, 50.0, 150.0, 300.0]
NATURES = [
    "borne hors service",
    "câble endommagé",
    "écran illisible",
    "défaut de paiement carte",
    "communication réseau perdue",
    "maintenance préventive",
]

# Les quatre tarifs. `code_tarif` ne veut rien dire sans cette table : c'est le
# piège nº 3 du dictionnaire de `exploitation`, et il est ici à la source.
TARIFS = [
    (1, "BASE", "Tarif de base, sans engagement", 0.45),
    (2, "HP", "Heures pleines, abonnés", 0.38),
    (3, "HC", "Heures creuses, abonnés", 0.22),
    (4, "ABO", "Forfait mensuel illimité", 0.00),
]

# Trois « payee » sur cinq : le tirage se fait par répétition dans la liste, ce
# qui garde la pondération lisible à l'œil.
STATUTS_DE_PAIEMENT = ["payee", "payee", "payee", "en_attente", "impayee"]

NB_STATIONS_ACTIVES = 120
NB_STATIONS_RETIREES = 30
NB_BORNES = 380
NB_CLIENTS = 900
NB_SESSIONS = 48_000
NB_INTERVENTIONS = 900
NB_FACTURES = 1_200
NB_LIGNES_FACTURE = 4_800
NB_INCIDENTS = 240
# 380 bornes, 1 440 relevés horaires chacune : 547 200 lignes, soit 60 jours pleins.
RELEVES_PAR_BORNE = 1_440


def dsn(base: str) -> str:
    """Le DSN d'une base, depuis les mêmes variables que le catalogue."""
    utilisateur = os.environ.get("DAA_PG_USER", "postgres")
    mot_de_passe = os.environ.get("DAA_PG_PASSWORD", "postgres")
    hote = os.environ.get("DAA_PG_HOST", "localhost")
    port = os.environ.get("DAA_PG_PORT", "5432")
    return f"postgresql+pg8000://{utilisateur}:{mot_de_passe}@{hote}:{port}/{base}"


def recreer_la_base() -> Engine:
    """DROP puis CREATE : le semis est rejouable sans état résiduel."""
    admin = create_engine(dsn("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connexion:
        connexion.execute(text(f"DROP DATABASE IF EXISTS {BASE_PG} WITH (FORCE)"))
        connexion.execute(text(f"CREATE DATABASE {BASE_PG}"))
    admin.dispose()
    return create_engine(dsn(BASE_PG))


def empreinte(chemin: Path) -> str:
    """SHA-256 d'un fichier, tronqué — c'est la preuve de rejouabilité."""
    return hashlib.sha256(chemin.read_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Le référentiel : engendré EN PREMIER, parce que tout le reste en dérive.
# --------------------------------------------------------------------------


def engendrer_les_stations(tirage: random.Random) -> list[dict]:
    """Les 150 stations du réseau : 120 en service, 30 démontées.

    Une seule liste, et deux vues dessus — c'est ce qui rend les sources
    *cohérentes entre elles* plutôt que simplement voisines. ``exploitation``
    ne verra que les 120 actives, ``referentiel`` les 150 : l'écart est un fait
    du métier, et c'est lui qu'on met à l'épreuve du verrou de source.
    """
    # Tirage SANS REMISE dans les 300 couples ville-quartier possibles. Le
    # libellé doit être unique, et pas approximativement : c'est la clé par
    # laquelle la main courante de la maintenance rejoint le reste du catalogue
    # (piège nº 1). Un tirage avec remise donnait 116 libellés pour 150
    # stations, et la « table de correspondance » n'en était plus une — la
    # jointure multipliait les lignes au lieu de les rattacher.
    couples = [
        (region, ville, quartier)
        for region in REGIONS
        for ville in VILLES[region]
        for quartier in QUARTIERS
    ]
    tires = tirage.sample(couples, NB_STATIONS_ACTIVES + NB_STATIONS_RETIREES)
    stations = []
    for i, (region, ville, quartier) in enumerate(tires, start=1):
        stations.append(
            {
                "station_id": i,
                "code_station": f"ST-{i:03d}",
                # Le libellé, et sa forme exacte : c'est par LUI que la main
                # courante de la maintenance désigne une station.
                "libelle_station": f"{ville} — {quartier}",
                "region": region,
                "statut": "ACT" if i <= NB_STATIONS_ACTIVES else "RET",
                "date_mise_en_service": date(2019, 1, 1) + timedelta(days=tirage.randint(0, 2_000)),
                "nb_points": tirage.choice([2, 2, 4, 4, 6, 8]),
            }
        )
    return stations


def semer_le_referentiel(stations: list[dict]) -> tuple[int, str, str]:
    """``referentiel`` : les 150 stations, VERSIONNÉ, et c'est un oracle.

    Une seule colonne de date : la source n'a donc rien à désigner, et le
    catalogue ne lui met pas de ``date_reference``. C'est volontaire — la
    désignation doit se lire là où elle sert, pas partout par précaution.
    """
    libelles = {s["libelle_station"] for s in stations}
    if len(libelles) != len(stations):
        # Vérifié ici et pas ailleurs : c'est le fichier qui PORTE la
        # correspondance, donc c'est lui qui doit refuser d'être ambigu.
        raise AssertionError(
            f"libellés de station non uniques : {len(libelles)} pour {len(stations)} stations — "
            "la correspondance avec interventions.station_libelle multiplierait les lignes."
        )
    lignes = ["code_station,libelle_station,region,statut,date_mise_en_service,nb_points"]
    for s in stations:
        lignes.append(
            f"{s['code_station']},{s['libelle_station']},{s['region']},"
            f"{s['statut']},{s['date_mise_en_service']},{s['nb_points']}"
        )
    CSV_REFERENTIEL.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    jours = sorted(s["date_mise_en_service"] for s in stations)
    return len(stations), str(jours[0]), str(jours[-1])


# --------------------------------------------------------------------------
# exploitation — postgres, six tables, clés étrangères réelles
# --------------------------------------------------------------------------

DDL_EXPLOITATION = [
    "CREATE TABLE regions (region_id INTEGER PRIMARY KEY, nom TEXT NOT NULL)",
    """CREATE TABLE stations (
         station_id           INTEGER PRIMARY KEY,
         code_station         TEXT NOT NULL UNIQUE,
         nom                  TEXT NOT NULL,
         region_id            INTEGER NOT NULL REFERENCES regions(region_id),
         date_mise_en_service DATE NOT NULL,
         nb_points            INTEGER NOT NULL)""",
    """CREATE TABLE bornes (
         borne_id              INTEGER PRIMARY KEY,
         station_id            INTEGER NOT NULL REFERENCES stations(station_id),
         code_borne            TEXT NOT NULL UNIQUE,
         puissance_nominale_kw NUMERIC(6,1) NOT NULL,
         type_prise            TEXT NOT NULL)""",
    """CREATE TABLE clients (
         client_id        INTEGER PRIMARY KEY,
         code_client      TEXT NOT NULL UNIQUE,
         segment          TEXT NOT NULL,
         date_inscription DATE NOT NULL)""",
    """CREATE TABLE tarifs (
         tarif_id     INTEGER PRIMARY KEY,
         code_tarif   TEXT NOT NULL UNIQUE,
         libelle      TEXT NOT NULL,
         prix_kwh_eur NUMERIC(5,2) NOT NULL)""",
    """CREATE TABLE sessions (
         session_id    INTEGER PRIMARY KEY,
         borne_id      INTEGER NOT NULL REFERENCES bornes(borne_id),
         client_id     INTEGER NOT NULL REFERENCES clients(client_id),
         tarif_id      INTEGER NOT NULL REFERENCES tarifs(tarif_id),
         debut_session TIMESTAMP NOT NULL,
         fin_session   TIMESTAMP NOT NULL,
         energie_kwh   NUMERIC(8,2) NOT NULL,
         statut        CHAR(1) NOT NULL)""",
]


def inserer_par_paquets(connexion, table: str, colonnes: list[str], lignes: list[tuple]) -> None:
    """INSERT multi-lignes par paquets de 1 000.

    Une ligne à la fois, 48 000 sessions prennent des minutes : le coût n'est
    pas dans Postgres, il est dans les 48 000 allers-retours. Par paquets, le
    semis tient en quelques secondes — et le contenu est le même, puisque
    l'ordre des lignes ne change pas.
    """
    noms = ", ".join(colonnes)
    for depart in range(0, len(lignes), 1_000):
        paquet = lignes[depart : depart + 1_000]
        valeurs = ", ".join(
            "(" + ", ".join(f":p{i}_{j}" for j in range(len(colonnes))) + ")"
            for i in range(len(paquet))
        )
        parametres = {
            f"p{i}_{j}": ligne[j] for i, ligne in enumerate(paquet) for j in range(len(colonnes))
        }
        connexion.execute(text(f"INSERT INTO {table} ({noms}) VALUES {valeurs}"), parametres)


def semer_exploitation(tirage: random.Random, stations: list[dict]) -> dict:
    """``exploitation`` : le SI métier, six tables et cinq clés étrangères.

    Seules les stations ACTIVES y entrent : une base d'exploitation ne connaît
    pas le matériel démonté. C'est de là que vient l'écart avec le référentiel.
    """
    moteur = recreer_la_base()
    actives = [s for s in stations if s["statut"] == "ACT"]

    regions = [(i + 1, nom) for i, nom in enumerate(REGIONS)]
    index_region = {nom: i + 1 for i, nom in enumerate(REGIONS)}
    lignes_stations = [
        (
            s["station_id"],
            s["code_station"],
            s["libelle_station"],
            index_region[s["region"]],
            s["date_mise_en_service"],
            s["nb_points"],
        )
        for s in actives
    ]
    bornes = [
        (
            i,
            actives[tirage.randrange(len(actives))]["station_id"],
            f"BRN-{i:04d}",
            tirage.choice(PUISSANCES),
            tirage.choice(TYPES_PRISE),
        )
        for i in range(1, NB_BORNES + 1)
    ]
    clients = [
        (
            i,
            f"CLI-{i:04d}",
            tirage.choice(["particulier", "particulier", "particulier", "flotte", "collectivite"]),
            date(2021, 1, 1) + timedelta(days=tirage.randint(0, 1_600)),
        )
        for i in range(1, NB_CLIENTS + 1)
    ]

    debut_annee = datetime(2025, 1, 1)
    sessions = []
    for i in range(1, NB_SESSIONS + 1):
        # 88 % abouties, 9 % interrompues, 3 % en erreur. Le piège nº 3 est là :
        # `count(*)` sur cette table n'est PAS le nombre de recharges.
        tire = tirage.random()
        statut = "T" if tire < 0.88 else ("I" if tire < 0.97 else "E")
        depart = debut_annee + timedelta(minutes=tirage.randint(0, 365 * 24 * 60 - 1))
        duree = tirage.randint(12, 240) if statut != "E" else tirage.randint(1, 4)
        if statut == "T":
            energie = round(tirage.uniform(4.0, 78.0), 2)
        elif statut == "I":
            energie = round(tirage.uniform(0.5, 12.0), 2)
        else:
            energie = 0.0
        sessions.append(
            (
                i,
                tirage.randint(1, NB_BORNES),
                tirage.randint(1, NB_CLIENTS),
                tirage.randint(1, len(TARIFS)),
                depart,
                depart + timedelta(minutes=duree),
                energie,
                statut,
            )
        )

    with moteur.begin() as connexion:
        for instruction in DDL_EXPLOITATION:
            connexion.execute(text(instruction))
        inserer_par_paquets(connexion, "regions", ["region_id", "nom"], regions)
        inserer_par_paquets(
            connexion,
            "stations",
            ["station_id", "code_station", "nom", "region_id", "date_mise_en_service", "nb_points"],
            lignes_stations,
        )
        inserer_par_paquets(
            connexion,
            "bornes",
            ["borne_id", "station_id", "code_borne", "puissance_nominale_kw", "type_prise"],
            bornes,
        )
        inserer_par_paquets(
            connexion,
            "clients",
            ["client_id", "code_client", "segment", "date_inscription"],
            clients,
        )
        inserer_par_paquets(
            connexion, "tarifs", ["tarif_id", "code_tarif", "libelle", "prix_kwh_eur"], TARIFS
        )
        inserer_par_paquets(
            connexion,
            "sessions",
            [
                "session_id",
                "borne_id",
                "client_id",
                "tarif_id",
                "debut_session",
                "fin_session",
                "energie_kwh",
                "statut",
            ],
            sessions,
        )
    moteur.dispose()

    horodatages = sorted(s[4] for s in sessions)
    return {
        "tables": {
            "regions": len(regions),
            "stations": len(lignes_stations),
            "bornes": len(bornes),
            "clients": len(clients),
            "tarifs": len(TARIFS),
            "sessions": len(sessions),
        },
        "debut": str(horodatages[0]),
        "fin": str(horodatages[-1]),
        "clients_lignes": clients,
        "bornes_lignes": bornes,
    }


# --------------------------------------------------------------------------
# telemetrie — duckdb, 547 820 lignes
# --------------------------------------------------------------------------


def semer_telemetrie(tirage: random.Random, bornes: list[tuple], stations: list[dict]) -> dict:
    """``telemetrie`` : la volumétrie qui se voit — 547 200 relevés horaires.

    Le gros de la table est tiré par numpy et non par ``random`` : 547 200
    lignes construites une par une en Python coûtent des dizaines de secondes
    pour rien. ``default_rng`` est graine par graine reproductible, ce qui est
    la seule propriété qu'on lui demande.

    La sentinelle ``-1.0`` (piège nº 2) est posée ici, sur 3 % des relevés :
    le compteur n'a rien remonté. Ce n'est pas une puissance nulle, et une
    moyenne qui la prendrait pour telle tirerait le chiffre vers le bas.
    """
    BASE_DUCKDB.unlink(missing_ok=True)
    code_par_station = {s["station_id"]: s["code_station"] for s in stations}
    connexion = duckdb.connect(str(BASE_DUCKDB))
    connexion.execute(
        "CREATE TABLE bornes_suivies ("
        "  borne_id INTEGER PRIMARY KEY,"
        "  code_borne VARCHAR NOT NULL,"
        "  code_station VARCHAR NOT NULL,"
        "  puissance_nominale_kw DOUBLE NOT NULL)"
    )
    connexion.execute(
        "CREATE TABLE releves_puissance ("
        "  releve_id BIGINT PRIMARY KEY,"
        "  borne_id INTEGER NOT NULL REFERENCES bornes_suivies(borne_id),"
        "  horodatage TIMESTAMP NOT NULL,"
        "  puissance_kw DOUBLE NOT NULL,"
        "  temperature_c DOUBLE NOT NULL)"
    )
    connexion.execute(
        "CREATE TABLE incidents_reseau ("
        "  incident_id INTEGER PRIMARY KEY,"
        "  borne_id INTEGER NOT NULL REFERENCES bornes_suivies(borne_id),"
        "  debut_incident TIMESTAMP NOT NULL,"
        "  fin_incident TIMESTAMP NOT NULL,"
        "  cause VARCHAR NOT NULL)"
    )

    suivies = pd.DataFrame(
        [
            {
                "borne_id": b[0],
                "code_borne": b[2],
                "code_station": code_par_station[b[1]],
                "puissance_nominale_kw": float(b[3]),
            }
            for b in bornes
        ]
    )
    connexion.execute("INSERT INTO bornes_suivies SELECT * FROM suivies")

    alea = np.random.default_rng(GRAINE)
    total = NB_BORNES * RELEVES_PAR_BORNE
    depart = np.datetime64("2025-07-01T00:00:00")
    releves = pd.DataFrame(
        {
            "releve_id": np.arange(1, total + 1, dtype="int64"),
            "borne_id": np.repeat(
                np.array([b[0] for b in bornes], dtype="int32"), RELEVES_PAR_BORNE
            ),
            "horodatage": depart
            + np.tile(np.arange(RELEVES_PAR_BORNE), NB_BORNES) * np.timedelta64(1, "h"),
            "puissance_kw": np.round(alea.uniform(0.0, 150.0, total), 2),
            "temperature_c": np.round(alea.normal(18.0, 7.0, total), 1),
        }
    )
    # Deux masques, et c'est le coeur du piège : une borne à l'arrêt remonte
    # 0.0 — le matériel répond, il ne charge personne — tandis qu'un compteur
    # muet remonte -1.0, qui n'est pas une puissance du tout. Les deux se
    # ressemblent dans un tableau et ne se traitent pas pareil. Tirés par
    # masques plutôt qu'au fil de la construction : le tirage reste une
    # fonction de la graine seule.
    tirage_etat = alea.random(total)
    arrets = tirage_etat < 0.08
    muets = (tirage_etat >= 0.08) & (tirage_etat < 0.11)
    releves.loc[arrets, "puissance_kw"] = 0.0
    releves.loc[muets, "puissance_kw"] = -1.0
    connexion.execute("INSERT INTO releves_puissance SELECT * FROM releves")

    causes = ["coupure secteur", "défaut modem", "surchauffe", "mise à jour logicielle"]
    incidents = []
    for i in range(1, NB_INCIDENTS + 1):
        debut = datetime(2025, 7, 1) + timedelta(minutes=tirage.randint(0, 60 * 24 * 60 - 1))
        incidents.append(
            (
                i,
                bornes[tirage.randrange(len(bornes))][0],
                debut,
                debut + timedelta(minutes=tirage.randint(15, 600)),
                tirage.choice(causes),
            )
        )
    connexion.executemany("INSERT INTO incidents_reseau VALUES (?, ?, ?, ?, ?)", incidents)

    # Comptés dans la DONNÉE écrite, et non dans les masques qui l'ont produite :
    # `np.round(uniform(0, 150), 2)` rend parfois 0.00 tout seul, et ces relevés-là
    # sont des bornes à l'arrêt comme les autres. Le masque en annonçait 44 187,
    # la base en contient 44 197 — et c'est la base qui a raison, puisque c'est
    # elle que l'agent interroge.
    muets_total = int((releves["puissance_kw"] == -1.0).sum())
    arrets_total = int((releves["puissance_kw"] == 0.0).sum())
    connexion.close()
    fin = depart + np.timedelta64(RELEVES_PAR_BORNE - 1, "h")
    return {
        "tables": {
            "bornes_suivies": len(suivies),
            "releves_puissance": total,
            "incidents_reseau": NB_INCIDENTS,
        },
        "debut": str(pd.Timestamp(depart)),
        "fin": str(pd.Timestamp(fin)),
        "muets": muets_total,
        "arrets": arrets_total,
    }


# --------------------------------------------------------------------------
# interventions — CSV versionné, et le piège du libellé
# --------------------------------------------------------------------------


def semer_les_interventions(tirage: random.Random, stations: list[dict]) -> dict:
    """``interventions`` : la main courante — et le piège nº 1, à sa source.

    La station y est désignée par son LIBELLÉ, jamais par son code. C'est la
    modélisation qu'on trouve dans une main courante réelle : elle est saisie
    par des humains qui lisent le nom sur la borne, pas la clé du référentiel.
    Une jointure avec ``exploitation`` n'est donc possible qu'en passant par
    ``referentiel``, et le dictionnaire le dit.

    Les stations DÉMONTÉES y figurent aussi : elles ont été maintenues avant de
    l'être. Joindre par le code depuis ``exploitation`` en perd silencieusement
    une part — c'est ça, le piège.
    """
    lignes = [
        "intervention_id,station_libelle,date_signalement,date_resolution,"
        "nature,duree_indispo_min,technicien"
    ]
    debut = date(2025, 1, 1)
    non_renseignees = 0
    for i in range(1, NB_INTERVENTIONS + 1):
        station = stations[tirage.randrange(len(stations))]
        signalement = debut + timedelta(days=tirage.randint(0, 364))
        resolution = signalement + timedelta(days=tirage.randint(0, 21))
        # -1 : « non renseigné ». Piège nº 4 — même nature que la sentinelle de
        # la télémétrie, dans un format différent et sur une autre source.
        if tirage.random() < 0.12:
            duree = -1
            non_renseignees += 1
        else:
            duree = tirage.randint(20, 2_880)
        lignes.append(
            f"{i},{station['libelle_station']},{signalement},{resolution},"
            f"{tirage.choice(NATURES)},{duree},TECH-{tirage.randint(1, 18):02d}"
        )
    CSV_INTERVENTIONS.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return {"lignes": NB_INTERVENTIONS, "non_renseignees": non_renseignees}


# --------------------------------------------------------------------------
# facturation — classeur à trois feuilles, figé octet pour octet
# --------------------------------------------------------------------------


INSTANT_FIGE = "2026-01-01T00:00:00Z"
DATE_MODIFICATION = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


def figer_le_classeur(chemin: Path) -> None:
    """Réécrit le .xlsx sans rien qui dépende de l'heure de l'exécution.

    Sans ça, deux exécutions du semis rendent deux fichiers différents à
    contenu identique, pour deux raisons distinctes :

    - un ``.xlsx`` est un zip, et chaque entrée y porte sa date d'écriture ;
    - openpyxl réécrit ``dcterms:modified`` à l'instant de la sauvegarde, en
      écrasant la valeur posée sur ``workbook.properties`` — la seule des deux
      propriétés qui ne se laisse pas fixer en amont.

    Les deux sont neutralisées ici. La propriété qu'on veut — le classeur est
    une fonction de la graine, et de rien d'autre — se vérifie alors au
    ``sha256``, ce qui est la seule vérification qui ne se discute pas.

    1980-01-01 est la date plancher du format zip : la plus ancienne qu'il sait
    écrire, donc la plus neutre.
    """
    with zipfile.ZipFile(chemin) as archive:
        entrees = [(info.filename, archive.read(info.filename)) for info in archive.infolist()]
    provisoire = chemin.with_suffix(".xlsx.fige")
    with zipfile.ZipFile(provisoire, "w", zipfile.ZIP_DEFLATED) as sortie:
        for nom, contenu in entrees:
            if nom == "docProps/core.xml":
                contenu = DATE_MODIFICATION.sub(
                    rb"\g<1>" + INSTANT_FIGE.encode() + rb"\g<2>", contenu
                )
            info = zipfile.ZipInfo(nom, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            sortie.writestr(info, contenu)
    shutil.move(str(provisoire), str(chemin))


def semer_la_facturation(tirage: random.Random, clients: list[tuple], stations: list[dict]) -> dict:
    """``facturation`` : l'export compta — trois feuilles, deux colonnes de date.

    Deux colonnes de date sur la feuille des factures (``date_emission`` et
    ``date_echeance``) : c'est exactement le cas où ``date_reference`` sert. Sans
    elle, la période couverte serait celle de la première colonne de date du
    schéma, choisie par l'ordre des colonnes et non par le métier.
    """
    actives = [s for s in stations if s["statut"] == "ACT"]
    factures = []
    for i in range(1, NB_FACTURES + 1):
        emission = date(2025, 1, 31) + timedelta(days=30 * ((i - 1) % 12))
        montant_ht = round(tirage.uniform(12.0, 940.0), 2)
        factures.append(
            {
                "facture_id": i,
                "code_client": clients[tirage.randrange(len(clients))][1],
                "periode": f"2025-{((i - 1) % 12) + 1:02d}",
                "date_emission": emission,
                "date_echeance": emission + timedelta(days=30),
                "montant_ht_eur": montant_ht,
                "montant_ttc_eur": round(montant_ht * 1.20, 2),
                "statut_paiement": tirage.choice(STATUTS_DE_PAIEMENT),
            }
        )
    lignes = []
    for i in range(1, NB_LIGNES_FACTURE + 1):
        montant = round(tirage.uniform(2.0, 260.0), 2)
        lignes.append(
            {
                "ligne_id": i,
                "facture_id": tirage.randint(1, NB_FACTURES),
                "code_station": actives[tirage.randrange(len(actives))]["code_station"],
                "code_tarif": tirage.choice([t[1] for t in TARIFS]),
                "energie_kwh": round(tirage.uniform(1.0, 220.0), 2),
                "montant_ht_eur": montant,
            }
        )
    postes = [
        {"code_tarif": t[1], "libelle": t[2], "prix_kwh_eur": t[3], "tva_pct": 20.0} for t in TARIFS
    ]

    CLASSEUR.unlink(missing_ok=True)
    with pd.ExcelWriter(CLASSEUR, engine="openpyxl") as classeur:
        pd.DataFrame(factures).to_excel(classeur, sheet_name="factures", index=False)
        pd.DataFrame(lignes).to_excel(classeur, sheet_name="lignes_facture", index=False)
        pd.DataFrame(postes).to_excel(classeur, sheet_name="postes_tarifaires", index=False)
        # Les propriétés du document portent l'heure de création : figées ici,
        # avant l'écriture, sans quoi `figer_le_classeur` n'y pourrait rien.
        proprietes = classeur.book.properties
        proprietes.created = datetime(2026, 1, 1)
        proprietes.modified = datetime(2026, 1, 1)
        proprietes.creator = "seed_catalogue_demonstration"
        proprietes.lastModifiedBy = "seed_catalogue_demonstration"
    figer_le_classeur(CLASSEUR)

    emissions = sorted(f["date_emission"] for f in factures)
    return {
        "tables": {
            "factures": len(factures),
            "lignes_facture": len(lignes),
            "postes_tarifaires": len(postes),
        },
        "debut": str(emissions[0]),
        "fin": str(emissions[-1]),
    }


def main() -> None:
    export_env_file()
    tirage = random.Random(GRAINE)
    print(f"Base Postgres : {BASE_PG} sur {os.environ.get('DAA_PG_HOST', 'localhost')}")

    stations = engendrer_les_stations(tirage)
    ref_lignes, ref_debut, ref_fin = semer_le_referentiel(stations)
    exploitation = semer_exploitation(tirage, stations)
    telemetrie = semer_telemetrie(tirage, exploitation["bornes_lignes"], stations)
    interventions = semer_les_interventions(tirage, stations)
    facturation = semer_la_facturation(tirage, exploitation["clients_lignes"], stations)

    def detail(tables: dict[str, int]) -> str:
        return ", ".join(f"{nom} : {n}" for nom, n in tables.items())

    print("\nOracles — à recopier dans docs/sources-de-demonstration.md")
    print("| Source | Type | Tables | Lignes | Période couverte |")
    print("|---|---|---|---|---|")
    print(
        f"| `exploitation` | postgres | {len(exploitation['tables'])} | "
        f"{sum(exploitation['tables'].values())} ({detail(exploitation['tables'])}) | "
        f"{exploitation['debut']} → {exploitation['fin']} |"
    )
    print(
        f"| `telemetrie` | duckdb | {len(telemetrie['tables'])} | "
        f"{sum(telemetrie['tables'].values())} ({detail(telemetrie['tables'])}) | "
        f"{telemetrie['debut']} → {telemetrie['fin']} |"
    )
    print(
        f"| `interventions` | file (CSV) | 1 | {interventions['lignes']} | "
        "2025-01-01 → 2025-12-31 (date_signalement) |"
    )
    print(f"| `referentiel` | file (CSV) | 1 | {ref_lignes} | {ref_debut} → {ref_fin} |")
    print(
        f"| `facturation` | file (XLSX) | {len(facturation['tables'])} | "
        f"{sum(facturation['tables'].values())} ({detail(facturation['tables'])}) | "
        f"{facturation['debut']} → {facturation['fin']} |"
    )

    print("\nPièges de modélisation, tels que semés")
    print(f"- relevés à puissance_kw = -1 (compteur muet) : {telemetrie['muets']}")
    print(f"- relevés à puissance_kw = 0 (borne à l'arrêt) : {telemetrie['arrets']}")
    print(
        f"- interventions à duree_indispo_min = -1 (non renseigné) : "
        f"{interventions['non_renseignees']}"
    )

    print("\nEmpreintes des fichiers engendrés (sha256, 16 premiers caractères)")
    for chemin in (CSV_REFERENTIEL, CSV_INTERVENTIONS, CLASSEUR, BASE_DUCKDB):
        taille = chemin.stat().st_size
        print(f"- {chemin.name:22} {empreinte(chemin)}  ({taille / 1e6:.2f} Mo)")
    print(
        "\nLes deux CSV et le classeur sont figés : deux exécutions rendent les mêmes\n"
        "octets. Le .duckdb ne l'est pas — son contenu l'est, ses octets non."
    )


if __name__ == "__main__":
    main()
