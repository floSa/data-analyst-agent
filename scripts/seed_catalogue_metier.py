"""Engendre le CATALOGUE MÉTIER — un fabricant de vélos qui produit, vend et stocke.

Le catalogue de démonstration précédent (réseau de bornes de recharge) est
réaliste, et c'est tout son défaut : 547 200 relevés de puissance horaires ne se
racontent pas devant un prospect, et aucune de ses réponses ne se vérifie de
tête. Il a servi à DURCIR le socle — il porte les campagnes de mesure, et il
reste en place pour ça. Il ne sert pas à MONTRER le produit.

Celui-ci est écrit pour être montré. Un seul critère de conception, et il
commande tous les autres : **une réponse fausse doit se voir sans avoir à la
vérifier**. D'où des volumes qui tiennent dans la tête — dix-huit clients, douze
produits, cent quatre-vingts commandes — et des codes qu'on cite de mémoire :
``VEL-01``, ``M-003``, ``E-NAN``. On doit pouvoir écrire un code dans une
question sans l'avoir sous les yeux.

Le domaine tient en une phrase : **les Cycles du Ponant fabriquent des vélos à
Nantes, les vendent à des revendeurs, et les stockent dans trois entrepôts.**

===========================================================================
 Source       Type        Ce qu'elle apporte
===========================================================================
 ventes       postgres    4 tables, 3 clés étrangères — le carnet de commandes
 production   duckdb      4 tables — l'atelier, ses machines et ses arrêts
 stocks       file XLSX   3 feuilles — l'entrepôt, vu par le tableur
 iris         file CSV    150 lignes — le jeu de référence, pour le ML
 titanic      file CSV    891 lignes — le jeu de référence, pour le ML
===========================================================================

**``iris`` et ``titanic`` ne sont pas engendrés.** Ils sont RECOPIÉS tels quels
depuis ``sources/``. Ce sont des jeux de référence : les modifier — ne serait-ce
que d'une ligne, ne serait-ce que pour les « mettre au thème » — ferait mentir
toute comparaison avec la littérature, avec nos propres modèles du registre, et
avec les campagnes d'avant. Ils sont servis comme FICHIERS parce que c'est sous
cette forme qu'on fait des statistiques et du ML dessus.

**Les trois bases métier SE RECOUPENT par le produit.** ``code_produit`` vit
dans les trois, et « combien de produits ? » a plusieurs réponses légitimes :

    ventes       12 — tout ce qui se vend, vélos et accessoires
    stocks       12 — tout ce qui se stocke, idem
    production    8 — les seuls VÉLOS : les quatre accessoires sont ACHETÉS

L'écart est un fait du métier, pas un artifice : on ne fabrique pas tout ce
qu'on vend. C'est lui qui donne au verrou de source quelque chose à protéger —
une question posée sans nommer la source risque une réponse juste pour une autre
base que celle qu'on avait en tête.

**Trois pièges de modélisation, un par base, de trois familles DIFFÉRENTES**,
chacun documenté dans le dictionnaire de sa source
(``sources/metier/dictionnaires/``) :

1. ``ventes.commandes.statut`` — un CODE qu'il faut filtrer pour UNE mesure et
   pas pour une autre. Compter les commandes : aucun filtre, les 180. Sommer de
   l'argent : ``statut <> 'ANN'``, parce qu'une commande annulée ne facture
   rien. Le même code, deux traitements, selon ce qu'on mesure.
2. ``production.arrets_machine.duree_minutes`` — une VALEUR SENTINELLE.
   ``-1`` veut dire « arrêt encore ouvert, durée non close » ; ce n'est pas une
   durée, et ``avg()`` la moyenne sans broncher. ``0`` lui ressemble et EST une
   durée : fausse alerte, remise en route immédiate.
3. ``stocks.mouvements.quantite`` — une COLONNE SIGNÉE. Positive à l'entrée,
   négative à la sortie. ``sum(quantite)`` est la variation NETTE du stock ; ce
   n'est pas « ce qui est sorti », qui est une autre question et un autre
   chiffre. La somme brute répond — mais à une autre question que celle qu'on
   croit poser.

**Les données sont engendrées, pas versionnées**, et le tirage est figé
(``GRAINE``). Deux exécutions rendent les mêmes octets, ce qui se vérifie au
``sha256`` imprimé en fin de semis — sauf le ``.duckdb``, dont le CONTENU est
stable mais pas la représentation binaire (ordre d'écriture des blocs).

**Les oracles sont calculés depuis les données ÉCRITES**, relues dans Postgres,
dans DuckDB et dans le classeur — jamais depuis les paramètres du tirage. La
différence n'est pas théorique : un arrondi, une collision, un tirage qui tombe
pile sur la sentinelle, et le masque annonce un chiffre que la base ne contient
pas. C'est la base que l'agent interroge ; c'est donc elle qui a raison.

    uv run python scripts/seed_catalogue_metier.py

Connexion Postgres : les mêmes variables que le DSN du catalogue (``DAA_PG_*``).
Base cible : ``daa_metier``, recréée à chaque exécution.
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
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from data_analyst_agent.config import export_env_file

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = REPO / "sources" / "metier"
BASE_DUCKDB = CATALOGUE / "production.duckdb"
CLASSEUR = CATALOGUE / "stocks.xlsx"
CSV_IRIS = CATALOGUE / "iris.csv"
CSV_TITANIC = CATALOGUE / "titanic.csv"
SOURCE_IRIS = REPO / "sources" / "iris.csv"
SOURCE_TITANIC = REPO / "sources" / "titanic.csv"

# Figée, et c'est tout l'intérêt : une démonstration qui ne rend pas les mêmes
# chiffres d'une machine à l'autre n'est pas une démonstration, c'est un aléa.
GRAINE = 20260917
BASE_PG = "daa_metier"

# --------------------------------------------------------------------------
# Le domaine, en dur. Douze produits, et c'est une liste qu'on lit d'un oeil —
# c'est le point : « combien de produits ? » se vérifie en comptant les lignes
# ci-dessous, pas en interrogeant la base.
#
# `fabrique` sépare ce que l'atelier SORT de ce qu'on achète pour le revendre.
# C'est de là, et de nulle part ailleurs, que vient l'écart 12 / 8 entre les
# sources : aucun tirage n'y participe.
# --------------------------------------------------------------------------
PRODUITS = [
    # code,     libellé,                         famille,        prix, fabriqué
    ("VEL-01", "Vélo de ville Flâneur", "ville", 549.00, True),
    ("VEL-02", "Vélo de ville Flâneur électrique", "ville", 1890.00, True),
    ("VEL-03", "VTT Sentier", "tout-terrain", 899.00, True),
    ("VEL-04", "VTT Sentier tout suspendu", "tout-terrain", 1450.00, True),
    ("VEL-05", "Vélo de route Échappée", "route", 1290.00, True),
    ("VEL-06", "Gravel Grand Raid", "route", 1690.00, True),
    ("VEL-07", "Vélo cargo Charrette", "utilitaire", 2490.00, True),
    ("VEL-08", "Vélo pliant Origami", "ville", 749.00, True),
    ("ACC-01", "Casque Coquille", "accessoire", 79.00, False),
    ("ACC-02", "Antivol Verrou", "accessoire", 49.00, False),
    ("ACC-03", "Porte-bagages Cargo léger", "accessoire", 39.00, False),
    ("ACC-04", "Sacoche Besace", "accessoire", 59.00, False),
]

# Dix-huit revendeurs. Des raisons sociales inventées, et des villes réelles :
# le libellé doit être PARLANT — on doit pouvoir dire « la commande de Brest »
# à l'oral et savoir de quelle ligne on parle.
CLIENTS = [
    ("CLI-01", "Cycles de la Rade", "Brest", "magasin"),
    ("CLI-02", "Le Guidon Nantais", "Nantes", "magasin"),
    ("CLI-03", "Roue Libre Rennes", "Rennes", "magasin"),
    ("CLI-04", "Vélocité Bordeaux", "Bordeaux", "magasin"),
    ("CLI-05", "Pignon Fixe Lyon", "Lyon", "magasin"),
    ("CLI-06", "La Bécane Lilloise", "Lille", "magasin"),
    ("CLI-07", "Cyclo Montpellier", "Montpellier", "magasin"),
    ("CLI-08", "Atelier du Rayon", "Strasbourg", "magasin"),
    ("CLI-09", "Vélo Passion Tours", "Tours", "magasin"),
    ("CLI-10", "Le Dérailleur", "Grenoble", "magasin"),
    ("CLI-11", "Cyclables du Ponant", "Quimper", "grossiste"),
    ("CLI-12", "Distri-Cycles Ouest", "Angers", "grossiste"),
    ("CLI-13", "Grand Braquet SA", "Toulouse", "grossiste"),
    ("CLI-14", "Nord Cycles Négoce", "Amiens", "grossiste"),
    ("CLI-15", "velo-direct.fr", "Nantes", "en ligne"),
    ("CLI-16", "cyclomarket.fr", "Paris", "en ligne"),
    ("CLI-17", "laboutiqueduvelo.fr", "Lyon", "en ligne"),
    ("CLI-18", "pedalier-online.fr", "Rouen", "en ligne"),
]

# Trois ateliers, neuf machines. `M-003` doit pouvoir s'écrire de mémoire dans
# une question : c'est pour ça qu'il y en a neuf et pas quatre-vingts.
ATELIERS = [
    ("AT-CAD", "Cadrage et soudure", "Nantes"),
    ("AT-PEI", "Peinture et finition", "Nantes"),
    ("AT-ASS", "Assemblage final", "Saint-Herblain"),
]
MACHINES = [
    ("M-001", "AT-CAD", "Banc de soudure TIG nº 1", 2016),
    ("M-002", "AT-CAD", "Banc de soudure TIG nº 2", 2019),
    ("M-003", "AT-CAD", "Cintreuse de tubes", 2014),
    ("M-004", "AT-PEI", "Cabine de peinture poudre", 2018),
    ("M-005", "AT-PEI", "Four de polymérisation", 2018),
    ("M-006", "AT-PEI", "Poste de marquage", 2021),
    ("M-007", "AT-ASS", "Ligne d'assemblage A", 2015),
    ("M-008", "AT-ASS", "Ligne d'assemblage B", 2020),
    ("M-009", "AT-ASS", "Banc de contrôle final", 2022),
]

# Trois entrepôts. Le code porte les trois premières lettres de la ville : il se
# devine, donc il se cite — `E-NAN`, et personne n'a besoin de la table.
ENTREPOTS = [
    ("E-NAN", "Nantes", 4200, "Camille Ferrand"),
    ("E-LYO", "Lyon", 2800, "Hugo Delmas"),
    ("E-LIL", "Lille", 1900, "Nora Vasseur"),
]

MOTIFS_ARRET = [
    "panne mécanique",
    "changement d'outil",
    "maintenance préventive",
    "rupture d'approvisionnement",
    "défaut qualité",
]
MOTIFS_MOUVEMENT_ENTREE = ["réception atelier", "retour client", "transfert entrant"]
MOTIFS_MOUVEMENT_SORTIE = ["expédition client", "transfert sortant", "rebut"]

NB_COMMANDES = 180
NB_ORDRES = 140
NB_ARRETS = 70
NB_MOUVEMENTS = 480

# Les trois statuts de commande, tirés par répétition — la pondération se lit à
# l'oeil, et c'est elle qui fabrique le piège nº 1 : une quinzaine d'annulées
# sur 180, assez pour que l'écart de chiffre d'affaires soit VISIBLE.
STATUTS_COMMANDE = ["LIV"] * 12 + ["EXP"] * 5 + ["ANN"] * 2


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
# ventes — postgres, 4 tables, 3 clés étrangères RÉELLES
# --------------------------------------------------------------------------

DDL_VENTES = [
    """CREATE TABLE clients (
         client_id      INTEGER PRIMARY KEY,
         code_client    TEXT NOT NULL UNIQUE,
         raison_sociale TEXT NOT NULL,
         ville          TEXT NOT NULL,
         canal          TEXT NOT NULL,
         date_creation  DATE NOT NULL)""",
    """CREATE TABLE produits (
         produit_id         INTEGER PRIMARY KEY,
         code_produit       TEXT NOT NULL UNIQUE,
         libelle            TEXT NOT NULL,
         famille            TEXT NOT NULL,
         prix_unitaire_eur  NUMERIC(8,2) NOT NULL)""",
    """CREATE TABLE commandes (
         commande_id      INTEGER PRIMARY KEY,
         code_commande    TEXT NOT NULL UNIQUE,
         client_id        INTEGER NOT NULL REFERENCES clients(client_id),
         date_commande    DATE NOT NULL,
         statut           TEXT NOT NULL,
         montant_total_eur NUMERIC(10,2) NOT NULL)""",
    """CREATE TABLE lignes_commande (
         ligne_id          INTEGER PRIMARY KEY,
         commande_id       INTEGER NOT NULL REFERENCES commandes(commande_id),
         produit_id        INTEGER NOT NULL REFERENCES produits(produit_id),
         quantite          INTEGER NOT NULL,
         prix_unitaire_eur NUMERIC(8,2) NOT NULL,
         montant_ligne_eur NUMERIC(10,2) NOT NULL)""",
]


def inserer_par_paquets(connexion, table: str, colonnes: list[str], lignes: list[tuple]) -> None:
    """INSERT multi-lignes par paquets de 500.

    Le volume de ce catalogue ne l'exigerait pas — 180 commandes s'inséreraient
    une par une sans qu'on le remarque. C'est écrit pareil que dans le semis de
    démonstration pour une raison de lecture : deux scripts frères qui font la
    même chose doivent la faire de la même façon, sinon la différence se lit
    comme une intention.
    """
    noms = ", ".join(colonnes)
    for depart in range(0, len(lignes), 500):
        paquet = lignes[depart : depart + 500]
        valeurs = ", ".join(
            "(" + ", ".join(f":p{i}_{j}" for j in range(len(colonnes))) + ")"
            for i in range(len(paquet))
        )
        parametres = {
            f"p{i}_{j}": ligne[j] for i, ligne in enumerate(paquet) for j in range(len(colonnes))
        }
        connexion.execute(text(f"INSERT INTO {table} ({noms}) VALUES {valeurs}"), parametres)


def semer_les_ventes(tirage: random.Random) -> Engine:
    """``ventes`` : le carnet de commandes — et le piège nº 1, à sa source.

    Le prix d'une ligne est le prix CATALOGUE du produit, sans remise : le
    montant d'une ligne est donc exactement ``quantite * prix_unitaire_eur``, et
    il se vérifie de tête. C'est un choix de démonstration et non de réalisme —
    un barème de remise par canal serait plus vrai et rendrait chaque montant
    invérifiable à l'oeil, ce qui est précisément ce qu'on refuse ici.

    ``commandes.montant_total_eur`` est la somme de ses lignes, posée après leur
    tirage : les deux chemins de calcul du chiffre d'affaires — par l'en-tête ou
    par le détail — donnent le même résultat. Une base de démonstration qui se
    contredit avec elle-même fabrique un faux piège, et on en a déjà trois vrais.
    """
    moteur = recreer_la_base()

    clients = [
        (
            i,
            code,
            raison,
            ville,
            canal,
            date(2022, 1, 1) + timedelta(days=tirage.randint(0, 900)),
        )
        for i, (code, raison, ville, canal) in enumerate(CLIENTS, start=1)
    ]
    produits = [
        (i, code, libelle, famille, prix)
        for i, (code, libelle, famille, prix, _) in enumerate(PRODUITS, start=1)
    ]
    prix_par_id = {p[0]: p[4] for p in produits}

    commandes: list[tuple] = []
    lignes: list[tuple] = []
    ligne_id = 0
    for i in range(1, NB_COMMANDES + 1):
        jour = date(2025, 1, 1) + timedelta(days=tirage.randint(0, 364))
        statut = tirage.choice(STATUTS_COMMANDE)
        # Produits DISTINCTS dans une commande : deux lignes du même produit sur
        # la même commande se verraient comme une anomalie de saisie devant un
        # prospect, et il faudrait l'expliquer au lieu de montrer le produit.
        references = tirage.sample(range(1, len(produits) + 1), tirage.randint(1, 4))
        total = 0.0
        for produit_id in references:
            ligne_id += 1
            quantite = tirage.randint(1, 12) if produit_id > 8 else tirage.randint(1, 6)
            prix = prix_par_id[produit_id]
            montant = round(quantite * prix, 2)
            total += montant
            lignes.append((ligne_id, i, produit_id, quantite, prix, montant))
        commandes.append(
            (i, f"CMD-{i:04d}", tirage.randint(1, len(clients)), jour, statut, round(total, 2))
        )

    with moteur.begin() as connexion:
        for instruction in DDL_VENTES:
            connexion.execute(text(instruction))
        inserer_par_paquets(
            connexion,
            "clients",
            ["client_id", "code_client", "raison_sociale", "ville", "canal", "date_creation"],
            clients,
        )
        inserer_par_paquets(
            connexion,
            "produits",
            ["produit_id", "code_produit", "libelle", "famille", "prix_unitaire_eur"],
            produits,
        )
        inserer_par_paquets(
            connexion,
            "commandes",
            [
                "commande_id",
                "code_commande",
                "client_id",
                "date_commande",
                "statut",
                "montant_total_eur",
            ],
            commandes,
        )
        inserer_par_paquets(
            connexion,
            "lignes_commande",
            [
                "ligne_id",
                "commande_id",
                "produit_id",
                "quantite",
                "prix_unitaire_eur",
                "montant_ligne_eur",
            ],
            lignes,
        )
    return moteur


# --------------------------------------------------------------------------
# production — duckdb, 4 tables, clés étrangères DÉCLARÉES
# --------------------------------------------------------------------------


def semer_la_production(tirage: random.Random) -> None:
    """``production`` : l'atelier — et le piège nº 2, la valeur sentinelle.

    Seuls les huit VÉLOS y passent : les quatre accessoires sont achetés à un
    fournisseur et ne sont jamais lancés en fabrication. C'est de là que vient
    l'écart 12 / 8 entre les sources, et c'est un fait du métier — pas une
    troncature, pas un oubli de semis.

    Le type ``duckdb`` plutôt qu'un fichier de plus : une base DÉCLARE ses clés
    étrangères, donc ses jointures. Un CSV n'a rien à déclarer et les jointures
    seraient à deviner.
    """
    BASE_DUCKDB.unlink(missing_ok=True)
    connexion = duckdb.connect(str(BASE_DUCKDB))
    connexion.execute(
        "CREATE TABLE ateliers ("
        "  atelier_id INTEGER PRIMARY KEY,"
        "  code_atelier VARCHAR NOT NULL UNIQUE,"
        "  libelle VARCHAR NOT NULL,"
        "  site VARCHAR NOT NULL)"
    )
    connexion.execute(
        "CREATE TABLE machines ("
        "  machine_id INTEGER PRIMARY KEY,"
        "  code_machine VARCHAR NOT NULL UNIQUE,"
        "  atelier_id INTEGER NOT NULL REFERENCES ateliers(atelier_id),"
        "  libelle VARCHAR NOT NULL,"
        "  annee_installation INTEGER NOT NULL)"
    )
    connexion.execute(
        "CREATE TABLE ordres_fabrication ("
        "  of_id INTEGER PRIMARY KEY,"
        "  code_of VARCHAR NOT NULL UNIQUE,"
        "  code_produit VARCHAR NOT NULL,"
        "  machine_id INTEGER NOT NULL REFERENCES machines(machine_id),"
        "  date_lancement DATE NOT NULL,"
        "  quantite_produite INTEGER NOT NULL,"
        "  quantite_rebut INTEGER NOT NULL)"
    )
    connexion.execute(
        "CREATE TABLE arrets_machine ("
        "  arret_id INTEGER PRIMARY KEY,"
        "  machine_id INTEGER NOT NULL REFERENCES machines(machine_id),"
        "  date_arret DATE NOT NULL,"
        "  duree_minutes INTEGER NOT NULL,"
        "  motif VARCHAR NOT NULL)"
    )

    index_atelier = {code: i for i, (code, _, _) in enumerate(ATELIERS, start=1)}
    ateliers = [(i, code, libelle, site) for i, (code, libelle, site) in enumerate(ATELIERS, 1)]
    machines = [
        (i, code, index_atelier[atelier], libelle, annee)
        for i, (code, atelier, libelle, annee) in enumerate(MACHINES, start=1)
    ]

    vels = [code for code, _, _, _, fabrique in PRODUITS if fabrique]
    ordres = []
    for i in range(1, NB_ORDRES + 1):
        produit = vels[tirage.randrange(len(vels))]
        quantite = tirage.randint(5, 60)
        ordres.append(
            (
                i,
                f"OF-{i:04d}",
                produit,
                tirage.randint(1, len(machines)),
                date(2025, 1, 1) + timedelta(days=tirage.randint(0, 364)),
                quantite,
                # Le rebut : 0 dans la grande majorité des cas, et c'est un VRAI
                # zéro — l'ordre n'a rien rebuté. Aucune sentinelle ici : le
                # catalogue en porte une seule par base, et celle de `production`
                # est sur la durée d'arrêt.
                tirage.choice([0, 0, 0, 0, 1, 1, 2, 3]),
            )
        )

    arrets = []
    for i in range(1, NB_ARRETS + 1):
        tire = tirage.random()
        if tire < 0.14:
            # -1 : l'arrêt est encore OUVERT, sa durée n'est pas close. Ce n'est
            # pas une durée, et c'est le piège nº 2.
            duree = -1
        elif tire < 0.20:
            # 0 : fausse alerte, remise en route immédiate. Ça RESSEMBLE à la
            # sentinelle et c'est une vraie durée — elle doit entrer dans la
            # moyenne. Sans ce cas-là, « écarter les valeurs <= 0 » marcherait
            # aussi bien que la règle juste, et le piège ne piégerait rien.
            duree = 0
        else:
            duree = tirage.randint(10, 480)
        arrets.append(
            (
                i,
                tirage.randint(1, len(machines)),
                date(2025, 1, 1) + timedelta(days=tirage.randint(0, 364)),
                duree,
                MOTIFS_ARRET[tirage.randrange(len(MOTIFS_ARRET))],
            )
        )

    connexion.executemany("INSERT INTO ateliers VALUES (?, ?, ?, ?)", ateliers)
    connexion.executemany("INSERT INTO machines VALUES (?, ?, ?, ?, ?)", machines)
    connexion.executemany("INSERT INTO ordres_fabrication VALUES (?, ?, ?, ?, ?, ?, ?)", ordres)
    connexion.executemany("INSERT INTO arrets_machine VALUES (?, ?, ?, ?, ?)", arrets)
    connexion.close()


# --------------------------------------------------------------------------
# stocks — classeur à trois feuilles, figé octet pour octet
# --------------------------------------------------------------------------

INSTANT_FIGE = "2026-01-01T00:00:00Z"
DATE_MODIFICATION = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


def figer_le_classeur(chemin: Path) -> None:
    """Réécrit le .xlsx sans rien qui dépende de l'heure de l'exécution.

    Sans ça, deux exécutions du semis rendent deux fichiers différents à
    contenu identique, pour deux raisons distinctes :

    - un ``.xlsx`` est un zip, et chaque entrée y porte sa date d'écriture ;
    - openpyxl réécrit ``dcterms:modified`` à l'instant de la sauvegarde, en
      écrasant la valeur posée sur ``workbook.properties``.

    Les deux sont neutralisées ici, et la propriété qu'on veut — le classeur est
    une fonction de la graine, et de rien d'autre — se vérifie alors au
    ``sha256``. 1980-01-01 est la date plancher du format zip.
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


def semer_les_stocks(tirage: random.Random) -> None:
    """``stocks`` : l'entrepôt vu par le tableur — et le piège nº 3, la colonne signée.

    ``mouvements.quantite`` est SIGNÉE : positive à l'entrée, négative à la
    sortie. C'est la modélisation qu'on trouve dans un export de WMS, et elle
    est parfaitement saine — ce qui la rend piégeuse, c'est qu'une somme brute
    RÉPOND, sans erreur ni avertissement, à une autre question que celle qu'on
    croit poser.

    ``sens`` porte la même information en clair (``ENT`` / ``SOR``). La colonne
    est redondante avec le signe, et c'est volontaire : un tableur d'entrepôt
    l'a toujours, et sa présence donne à l'agent un moyen d'écrire la requête
    juste sans raisonner sur un signe.
    """
    entrepots = [
        {"code_entrepot": code, "ville": ville, "surface_m2": surface, "responsable": chef}
        for code, ville, surface, chef in ENTREPOTS
    ]

    codes_produits = [p[0] for p in PRODUITS]
    codes_entrepots = [e[0] for e in ENTREPOTS]
    mouvements = []
    for i in range(1, NB_MOUVEMENTS + 1):
        entree = tirage.random() < 0.40
        if entree:
            # Les entrées viennent par lots d'atelier : peu nombreuses, grosses.
            quantite = tirage.randint(10, 60)
            sens, motif = "ENT", MOTIFS_MOUVEMENT_ENTREE[tirage.randrange(3)]
        else:
            # Les sorties partent à l'unité ou par petites quantités.
            quantite = -tirage.randint(1, 15)
            sens, motif = "SOR", MOTIFS_MOUVEMENT_SORTIE[tirage.randrange(3)]
        mouvements.append(
            {
                "mouvement_id": i,
                "date_mouvement": date(2025, 1, 1) + timedelta(days=tirage.randint(0, 364)),
                "code_produit": codes_produits[tirage.randrange(len(codes_produits))],
                "code_entrepot": codes_entrepots[tirage.randrange(len(codes_entrepots))],
                "sens": sens,
                "quantite": quantite,
                "motif": motif,
            }
        )

    # L'inventaire : une ligne par couple entrepôt-produit, soit 36. C'est un
    # ÉTAT constaté au 31 décembre, pas la somme des mouvements — les deux ne
    # sont pas censés coïncider, et le dictionnaire le dit.
    inventaire = [
        {
            "code_entrepot": entrepot,
            "code_produit": produit,
            "quantite_en_stock": tirage.randint(0, 120),
            "date_inventaire": date(2025, 12, 31),
        }
        for entrepot in codes_entrepots
        for produit in codes_produits
    ]

    CLASSEUR.unlink(missing_ok=True)
    with pd.ExcelWriter(CLASSEUR, engine="openpyxl") as classeur:
        pd.DataFrame(entrepots).to_excel(classeur, sheet_name="entrepots", index=False)
        pd.DataFrame(mouvements).to_excel(classeur, sheet_name="mouvements", index=False)
        pd.DataFrame(inventaire).to_excel(classeur, sheet_name="inventaire", index=False)
        proprietes = classeur.book.properties
        proprietes.created = datetime(2026, 1, 1)
        proprietes.modified = datetime(2026, 1, 1)
        proprietes.creator = "seed_catalogue_metier"
        proprietes.lastModifiedBy = "seed_catalogue_metier"
    figer_le_classeur(CLASSEUR)


# --------------------------------------------------------------------------
# iris et titanic — RECOPIÉS, jamais engendrés
# --------------------------------------------------------------------------


def recopier_les_jeux_de_reference() -> None:
    """Copie octet pour octet depuis ``sources/``. Aucune transformation.

    Ces deux-là sont des jeux de RÉFÉRENCE. Leurs chiffres sont dans la
    littérature, dans nos modèles du registre (``models/registry.yaml``) et dans
    toutes les campagnes qui précèdent. Les modifier — ne serait-ce que pour
    traduire un en-tête ou « les mettre au thème » du fabricant de vélos —
    ferait mentir chacune de ces comparaisons d'un coup, et sans bruit.

    Ils sont donc copiés, et le semis n'a aucun autre droit sur eux. Le
    ``sha256`` de la copie est celui de l'original, et le semis le vérifie.
    """
    for origine, copie in ((SOURCE_IRIS, CSV_IRIS), (SOURCE_TITANIC, CSV_TITANIC)):
        shutil.copyfile(origine, copie)
        if empreinte(origine) != empreinte(copie):  # pragma: no cover - défensif
            raise AssertionError(f"la copie de {origine.name} diffère de son original")


# --------------------------------------------------------------------------
# Les oracles — relus dans les données ÉCRITES
# --------------------------------------------------------------------------


def oracles_des_ventes(moteur: Engine) -> dict:
    """Ce que `ventes` contient VRAIMENT, relu dans Postgres.

    Pas un chiffre ne vient des paramètres du tirage : ``NB_COMMANDES`` dit
    combien on a demandé, la base dit combien il y en a. Les deux coïncident
    ici, et c'est justement pour que ça reste vérifiable qu'on ne le suppose
    pas — le jour où une contrainte d'unicité en rejette une, l'oracle doit
    bouger tout seul.
    """
    with moteur.connect() as connexion:

        def un(sql: str):
            return connexion.execute(text(sql)).scalar()

        return {
            "tables": {
                "clients": un("SELECT count(*) FROM clients"),
                "produits": un("SELECT count(*) FROM produits"),
                "commandes": un("SELECT count(*) FROM commandes"),
                "lignes_commande": un("SELECT count(*) FROM lignes_commande"),
            },
            "produits_distincts": un("SELECT count(DISTINCT code_produit) FROM produits"),
            "commandes_totales": un("SELECT count(*) FROM commandes"),
            "commandes_annulees": un("SELECT count(*) FROM commandes WHERE statut = 'ANN'"),
            "ca_brut": float(un("SELECT sum(montant_total_eur) FROM commandes")),
            "ca_juste": float(
                un("SELECT sum(montant_total_eur) FROM commandes WHERE statut <> 'ANN'")
            ),
            "debut": str(un("SELECT min(date_commande) FROM commandes")),
            "fin": str(un("SELECT max(date_commande) FROM commandes")),
            "top_client": connexion.execute(
                text(
                    "SELECT c.raison_sociale, sum(o.montant_total_eur) AS ca "
                    "FROM commandes o JOIN clients c ON c.client_id = o.client_id "
                    "WHERE o.statut <> 'ANN' GROUP BY c.raison_sociale ORDER BY ca DESC LIMIT 1"
                )
            ).one(),
            "top_produit": connexion.execute(
                text(
                    "SELECT p.code_produit, sum(l.quantite) AS q "
                    "FROM lignes_commande l "
                    "JOIN produits p ON p.produit_id = l.produit_id "
                    "JOIN commandes o ON o.commande_id = l.commande_id "
                    "WHERE o.statut <> 'ANN' GROUP BY p.code_produit ORDER BY q DESC LIMIT 1"
                )
            ).one(),
        }


def oracles_de_la_production() -> dict:
    """Ce que `production` contient VRAIMENT, relu dans le .duckdb."""
    connexion = duckdb.connect(str(BASE_DUCKDB), read_only=True)

    def un(sql: str):
        return connexion.execute(sql).fetchone()[0]

    resultat = {
        "tables": {
            "ateliers": un("SELECT count(*) FROM ateliers"),
            "machines": un("SELECT count(*) FROM machines"),
            "ordres_fabrication": un("SELECT count(*) FROM ordres_fabrication"),
            "arrets_machine": un("SELECT count(*) FROM arrets_machine"),
        },
        "produits_distincts": un("SELECT count(DISTINCT code_produit) FROM ordres_fabrication"),
        "arrets_totaux": un("SELECT count(*) FROM arrets_machine"),
        "arrets_ouverts": un("SELECT count(*) FROM arrets_machine WHERE duree_minutes = -1"),
        "arrets_a_zero": un("SELECT count(*) FROM arrets_machine WHERE duree_minutes = 0"),
        "duree_brute": round(un("SELECT avg(duree_minutes) FROM arrets_machine"), 2),
        "duree_juste": round(
            un("SELECT avg(duree_minutes) FROM arrets_machine WHERE duree_minutes >= 0"), 2
        ),
        "produites": un("SELECT sum(quantite_produite) FROM ordres_fabrication"),
        "debut": str(un("SELECT min(date_lancement) FROM ordres_fabrication")),
        "fin": str(un("SELECT max(date_lancement) FROM ordres_fabrication")),
    }
    resultat["top_machine_arrets"] = connexion.execute(
        "SELECT m.code_machine, count(*) AS n FROM arrets_machine a "
        "JOIN machines m ON m.machine_id = a.machine_id "
        "GROUP BY m.code_machine ORDER BY n DESC, m.code_machine LIMIT 1"
    ).fetchone()
    connexion.close()
    return resultat


def oracles_des_stocks() -> dict:
    """Ce que `stocks` contient VRAIMENT, relu dans le classeur écrit."""
    feuilles = pd.read_excel(CLASSEUR, sheet_name=None)
    mouvements = feuilles["mouvements"]
    sorties = mouvements[mouvements["quantite"] < 0]
    entrees = mouvements[mouvements["quantite"] > 0]
    nantes = mouvements[mouvements["code_entrepot"] == "E-NAN"]
    return {
        "tables": {nom: len(frame) for nom, frame in feuilles.items()},
        "produits_distincts": int(mouvements["code_produit"].nunique()),
        "variation_nette": int(mouvements["quantite"].sum()),
        "unites_sorties": int(-sorties["quantite"].sum()),
        "unites_entrees": int(entrees["quantite"].sum()),
        "nb_sorties": len(sorties),
        "nantes_nette": int(nantes["quantite"].sum()),
        "nantes_sorties": int(-nantes[nantes["quantite"] < 0]["quantite"].sum()),
        "stock_inventaire": int(feuilles["inventaire"]["quantite_en_stock"].sum()),
        "debut": str(pd.to_datetime(mouvements["date_mouvement"]).min().date()),
        "fin": str(pd.to_datetime(mouvements["date_mouvement"]).max().date()),
    }


def main() -> None:
    export_env_file()
    tirage = random.Random(GRAINE)
    print(f"Catalogue : {CATALOGUE}")
    print(f"Base Postgres : {BASE_PG} sur {os.environ.get('DAA_PG_HOST', 'localhost')}\n")

    # L'ORDRE compte : `random.Random` est une séquence, donc déplacer un appel
    # déplace tous les tirages qui suivent. Cet ordre-ci est celui des oracles
    # publiés ; le changer les invalide tous, même si chaque source reste
    # « correcte » prise isolément.
    moteur = semer_les_ventes(tirage)
    semer_la_production(tirage)
    semer_les_stocks(tirage)
    recopier_les_jeux_de_reference()

    ventes = oracles_des_ventes(moteur)
    production = oracles_de_la_production()
    stocks = oracles_des_stocks()
    moteur.dispose()

    def detail(tables: dict) -> str:
        return ", ".join(f"{nom} : {n}" for nom, n in tables.items())

    print("ORACLES — volumétrie (à recopier dans docs/sources-metier.md)")
    print("| Source | Type | Tables | Lignes | Période couverte |")
    print("|---|---|---|---|---|")
    print(
        f"| `ventes` | postgres | {len(ventes['tables'])} | "
        f"{sum(ventes['tables'].values())} ({detail(ventes['tables'])}) | "
        f"{ventes['debut']} → {ventes['fin']} (date_commande) |"
    )
    print(
        f"| `production` | duckdb | {len(production['tables'])} | "
        f"{sum(production['tables'].values())} ({detail(production['tables'])}) | "
        f"{production['debut']} → {production['fin']} (date_lancement) |"
    )
    print(
        f"| `stocks` | file (XLSX) | {len(stocks['tables'])} | "
        f"{sum(stocks['tables'].values())} ({detail(stocks['tables'])}) | "
        f"{stocks['debut']} → {stocks['fin']} (date_mouvement) |"
    )
    print("| `iris` | file (CSV) | 1 | 150 | — (aucune colonne de date) |")
    print("| `titanic` | file (CSV) | 1 | 891 | — (aucune colonne de date) |")

    print("\nORACLES — le recoupement par le produit")
    print(f"- `ventes` : {ventes['produits_distincts']} produits au catalogue")
    print(f"- `stocks` : {stocks['produits_distincts']} produits mouvementés")
    print(
        f"- `production` : {production['produits_distincts']} produits fabriqués "
        "— les quatre accessoires ACC-** sont achetés, jamais lancés en fabrication"
    )

    print("\nORACLES — les trois pièges, le chiffre FAUX et le chiffre JUSTE")
    print("| Piège | Source | Question | En tombant dedans | En l'évitant |")
    print("|---|---|---|---|---|")
    print(
        f"| statut de commande | `ventes` | chiffre d'affaires 2025 | "
        f"{ventes['ca_brut']:.2f} € (les {ventes['commandes_totales']} commandes) | "
        f"**{ventes['ca_juste']:.2f} €** (`statut <> 'ANN'`) |"
    )
    print(
        f"| sentinelle -1 | `production` | durée moyenne d'un arrêt machine | "
        f"{production['duree_brute']:.2f} min (les {production['arrets_totaux']} arrêts) | "
        f"**{production['duree_juste']:.2f} min** (`duree_minutes >= 0`) |"
    )
    print(
        f"| colonne signée | `stocks` | unités sorties des entrepôts | "
        f"{stocks['variation_nette']} (`sum(quantite)`, qui est la variation nette) | "
        f"**{stocks['unites_sorties']}** (`-sum(quantite) WHERE quantite < 0`) |"
    )
    print("\nLe détail de chaque piège :")
    print(
        f"- `ventes` : {ventes['commandes_annulees']} commandes annulées sur "
        f"{ventes['commandes_totales']} ; l'écart de chiffre d'affaires est "
        f"{ventes['ca_brut'] - ventes['ca_juste']:.2f} €."
    )
    print(
        f"- `production` : {production['arrets_ouverts']} arrêts à -1 (encore ouverts) et "
        f"{production['arrets_a_zero']} à 0 (fausse alerte, VRAIE durée) sur "
        f"{production['arrets_totaux']} ; l'écart de moyenne est "
        f"{production['duree_juste'] - production['duree_brute']:.2f} min."
    )
    print(
        f"- `stocks` : {stocks['nb_sorties']} sorties totalisant "
        f"{stocks['unites_sorties']} unités, "
        f"face à {stocks['unites_entrees']} unités entrées ; la variation nette vaut "
        f"{stocks['variation_nette']}."
    )

    print("\nORACLES — quelques réponses justes de plus")
    print(
        f"- `ventes` : meilleur client hors annulées — {ventes['top_client'][0]}, "
        f"{float(ventes['top_client'][1]):.2f} €"
    )
    print(
        f"- `ventes` : produit le plus vendu hors annulées — {ventes['top_produit'][0]}, "
        f"{ventes['top_produit'][1]} unités"
    )
    print(
        f"- `production` : machine la plus arrêtée — {production['top_machine_arrets'][0]}, "
        f"{production['top_machine_arrets'][1]} arrêts"
    )
    print(f"- `production` : total produit sur 2025 — {production['produites']} vélos")
    print(
        f"- `stocks` : total en stock à l'inventaire du 31/12 — {stocks['stock_inventaire']} unités"
    )
    print(
        f"- `stocks` : à E-NAN, {stocks['nantes_sorties']} unités sorties "
        f"(variation nette {stocks['nantes_nette']})"
    )

    print("\nEMPREINTES (sha256, 16 premiers caractères)")
    for chemin in (CLASSEUR, CSV_IRIS, CSV_TITANIC, BASE_DUCKDB):
        taille = chemin.stat().st_size
        print(f"- {chemin.name:18} {empreinte(chemin)}  ({taille / 1e3:.1f} ko)")
    print(
        "\nLe classeur et les deux CSV de référence sont figés : deux exécutions rendent\n"
        "les mêmes octets. Le .duckdb ne l'est pas — son CONTENU l'est, ses octets non\n"
        "(ordre d'écriture des blocs). Postgres n'est pas un fichier : son contenu se\n"
        "vérifie par les oracles ci-dessus, qui sont relus dans la base."
    )


if __name__ == "__main__":
    main()
