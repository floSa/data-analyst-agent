"""Mesure les trois bornes du relevé d'une source — AVANT et APRÈS, dans la même exécution.

Les défauts corrigés ici n'ont pas d'oracle textuel et ne demandent aucun modèle :
ils se montrent sur des sources réelles qu'on fait tomber puis revenir. « Avant »
et « après » ne sont donc pas deux versions du code mais deux **réglages** du même
(`ReglagesDuReleve`) : le comportement d'avant est celui qu'on obtient en
désactivant chaque borne, et c'est ce qui rend la comparaison rejouable
indéfiniment — un `git stash` ne se rejoue pas.

Trois bancs, trois défauts :

1. **La fraîcheur.** Une base Postgres absente, puis créée. Sans reprise, la ligne
   reste « volumétrie non relevée » pour la vie du processus — c'est le défaut
   observé le 2026-09-14, Postgres arrêté au démarrage puis relancé. Avec elle,
   elle redevient chiffrée.
2. **Le bornage.** Une source **muette** : un DSN vers une adresse non routable,
   dont le TCP part et ne revient jamais. Sans délai, le relevé attend celui du
   système. Le banc ne l'attend pas non plus — il compte jusqu'à `--patience` et
   dit qu'à ce moment-là c'était toujours bloqué.
3. **L'approximation.** Une table Postgres volumineuse et une base DuckDB, comptées
   exactement puis estimées par le moteur.
4. **La colonne de date.** Une source qui porte DEUX colonnes de date, lue sans
   désignation, avec désignation, puis avec une désignation mal orthographiée. Ici
   « avant » et « après » ne sont pas deux réglages mais deux **déclarations** de la
   même source : c'est le catalogue qui a gagné le droit de dire laquelle de ses
   dates décrit la source (`tests/catalogues/deux-dates/`).

Ce que le banc écrit est jeté à la sortie : deux bases Postgres temporaires
(supprimées) et un fichier `.duckdb` dans un dossier temporaire.

    uv run python scripts/mesure_releve_des_sources.py
    uv run python scripts/mesure_releve_des_sources.py --lignes 5000000 --patience 30
    uv run python scripts/mesure_releve_des_sources.py --seulement dates

Prérequis : un Postgres joignable par les variables `DAA_PG_*` du `.env`. Aucun
serveur LLM : ce runner ne pose aucune question. Le quatrième banc, lui, ne
demande ni l'un ni l'autre — il lit un CSV versionné.
"""

from __future__ import annotations

import argparse
import os
import queue
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb
from sqlalchemy import create_engine, text

from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    DuckDBSource,
    PostgresSource,
    load_catalog,
)
from data_analyst_agent.agents.retrieval.faits import (
    ReglagesDuReleve,
    RelevesDuCatalogue,
    relever,
)
from data_analyst_agent.config import export_env_file

BASE_FRAICHEUR = "daa_banc_fraicheur"
BASE_VOLUMETRIE = "daa_banc_volumetrie"

# Une adresse du réseau privé qui ne route nulle part : la connexion part et
# n'obtient ni réponse ni refus. C'est le cas qui n'a PAS de message d'erreur, et
# le seul où l'absence de délai maximal se paie vraiment.
DSN_MUET = "postgresql+pg8000://sans:reponse@10.255.255.1:5432/nulle_part"

# Le comportement d'AVANT, réglage par réglage : aucune borne.
AVANT = ReglagesDuReleve(delai=0.0, peremption=0.0, reprise=0.0, seuil_approximation=0)


def dsn(base: str) -> str:
    utilisateur = os.environ.get("DAA_PG_USER", "postgres")
    mot_de_passe = os.environ.get("DAA_PG_PASSWORD", "postgres")
    hote = os.environ.get("DAA_PG_HOST", "localhost")
    port = os.environ.get("DAA_PG_PORT", "5432")
    return f"postgresql+pg8000://{utilisateur}:{mot_de_passe}@{hote}:{port}/{base}"


def _administrer(instruction: str) -> None:
    moteur = create_engine(dsn("postgres"), isolation_level="AUTOCOMMIT")
    with moteur.connect() as connexion:
        connexion.execute(text(instruction))
    moteur.dispose()


def titre(texte: str) -> None:
    print(f"\n{'=' * 78}\n{texte}\n{'=' * 78}", flush=True)


# --- banc 1 : la fraîcheur ---------------------------------------------------


def _poser_la_base(existe: bool) -> None:
    """La base de banc, présente ou absente. Absente = source injoignable."""
    _administrer(f"DROP DATABASE IF EXISTS {BASE_FRAICHEUR} WITH (FORCE)")
    if not existe:
        return
    _administrer(f"CREATE DATABASE {BASE_FRAICHEUR}")
    moteur = create_engine(dsn(BASE_FRAICHEUR))
    with moteur.begin() as connexion:
        connexion.execute(
            text("CREATE TABLE lignes AS SELECT i AS id FROM generate_series(1, 42) AS i")
        )
    moteur.dispose()


def banc_de_la_fraicheur(reprise: float) -> None:
    """Une source arrêtée, puis revenue. L'oracle est « 42 lignes », pas un texte."""
    titre("① LA FRAÎCHEUR — une source arrêtée, puis revenue")
    source = PostgresSource(name="banc", dsn=dsn(BASE_FRAICHEUR))
    catalogue = Catalog(sources=[source])

    for libelle, reglages in (
        ("AVANT (aucune reprise)", AVANT),
        (f"APRÈS (reprise = {reprise:g} s)", ReglagesDuReleve(reprise=reprise)),
    ):
        # Une péremption nulle relirait à chaque demande et masquerait le défaut :
        # ce qu'on veut montrer, c'est un ÉCHEC gardé, donc on garde le cache.
        garde = ReglagesDuReleve(
            delai=reglages.delai,
            peremption=10_000.0,
            reprise=reglages.reprise if reglages.reprise else 10_000.0,
            seuil_approximation=reglages.seuil_approximation,
        )
        _poser_la_base(False)
        releves = RelevesDuCatalogue(catalogue, garde)
        print(f"\n  {libelle}")
        print(f"    base arrêtée         → {releves.de('banc').en_clair()}")
        _poser_la_base(True)
        print(f"    base revenue, aussitôt → {releves.de('banc').en_clair()}")
        time.sleep(reprise + 0.5 if reglages.reprise else 1.0)
        apres = releves.de("banc")
        verdict = "LA SOURCE EST REVENUE" if apres.lu else "TOUJOURS ANNONCÉE INJOIGNABLE"
        print(f"    puis, plus tard      → {apres.en_clair()}   [{verdict}]")
    _poser_la_base(False)


# --- banc 2 : le bornage -----------------------------------------------------


def _attendre_au_plus(travail, patience: float) -> tuple[bool, float, str]:
    """``travail()`` avec une patience à NOUS. Rend (abouti, secondes, ce qu'il a dit).

    Le banc ne peut pas mesurer « l'absence de délai » en l'attendant : c'est
    justement ce qui n'a pas de fin. Il compte donc jusqu'à ``patience`` et dit
    ce qu'il en était à ce moment-là — ce qui est la mesure honnête d'un blocage.
    """
    boite: queue.Queue = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: boite.put(travail()), daemon=True).start()
    depart = time.monotonic()
    try:
        faits = boite.get(timeout=patience)
    except queue.Empty:
        return False, time.monotonic() - depart, ""
    return True, time.monotonic() - depart, faits.en_clair()


def banc_du_bornage(delai: float, patience: float) -> None:
    titre("② LE BORNAGE — une source muette, dont le TCP ne revient jamais")
    muette = PostgresSource(name="muette", dsn=DSN_MUET)

    print(f"\n  AVANT (aucun délai), patience du banc : {patience:g} s")
    abouti, secondes, dit = _attendre_au_plus(lambda: relever(muette, AVANT), patience)
    if abouti:
        print(f"    → rendu en {secondes:.1f} s : {dit}")
    else:
        print(f"    → TOUJOURS BLOQUÉ au bout de {secondes:.1f} s (le banc renonce, pas le relevé)")

    print(f"\n  APRÈS (délai = {delai:g} s)")
    depart = time.monotonic()
    faits = relever(muette, ReglagesDuReleve(delai=delai))
    print(f"    → rendu en {time.monotonic() - depart:.1f} s : {faits.en_clair()}")


# --- banc 3 : l'approximation ------------------------------------------------


@contextmanager
def base_postgres_volumineuse(lignes: int):
    """Une table Postgres de ``lignes`` lignes, analysée puis jetée."""
    _administrer(f"DROP DATABASE IF EXISTS {BASE_VOLUMETRIE} WITH (FORCE)")
    _administrer(f"CREATE DATABASE {BASE_VOLUMETRIE}")
    moteur = create_engine(dsn(BASE_VOLUMETRIE))
    with moteur.begin() as connexion:
        connexion.execute(
            text(
                "CREATE TABLE mesures AS SELECT i AS id, "
                "(DATE '2020-01-01' + mod(i, 2000)) AS jour, mod(i, 97)::int AS valeur "
                f"FROM generate_series(1, {lignes}) AS i"
            )
        )
    # Sans ANALYZE, `reltuples` vaut -1 et l'estimation se refuse elle-même :
    # c'est la garde qui évite d'afficher un « ~0 » pour une table jamais visitée.
    with moteur.begin() as connexion:
        connexion.execute(text("ANALYZE mesures"))
    moteur.dispose()
    try:
        yield PostgresSource(name="volumetrie", dsn=dsn(BASE_VOLUMETRIE))
    finally:
        _administrer(f"DROP DATABASE IF EXISTS {BASE_VOLUMETRIE} WITH (FORCE)")


def banc_de_lapproximation(lignes: int, seuil: int) -> None:
    titre(f"③ L'APPROXIMATION — {lignes:_} lignes, comptées puis estimées".replace("_", " "))
    with base_postgres_volumineuse(lignes) as postgres:
        _comparer("postgres", postgres, seuil)
    with tempfile.TemporaryDirectory(prefix="daa-banc-duckdb-") as racine:
        chemin = Path(racine) / "banc.duckdb"
        connexion = duckdb.connect(str(chemin))
        connexion.execute(f"CREATE TABLE mesures AS SELECT i AS id FROM range({lignes}) AS t(i)")
        connexion.close()
        _comparer("duckdb", DuckDBSource(name="volumetrie", path=chemin), seuil)


def _comparer(moteur: str, source, seuil: int) -> None:
    print(f"\n  {moteur}")
    for libelle, reglages in (
        ("AVANT (comptage exact)", AVANT),
        (
            f"APRÈS (estimation au-delà de {seuil:_})".replace("_", " "),
            ReglagesDuReleve(seuil_approximation=seuil),
        ),
    ):
        depart = time.monotonic()
        faits = relever(source, reglages)
        duree = (time.monotonic() - depart) * 1000
        marque = "estimé" if faits.estimees else "compté"
        print(f"    {libelle:44s} {duree:7.1f} ms — {faits.lignes:>9} lignes ({marque})")


CATALOGUES_DEUX_DATES = Path(__file__).resolve().parent.parent / "tests/catalogues/deux-dates"


def banc_de_la_colonne_de_date() -> None:
    """Deux colonnes de date, trois déclarations, trois périodes lues.

    Rien à monter ni à défaire : les trois catalogues pointent le même CSV
    versionné, dont les deux colonnes couvrent des intervalles franchement
    distincts (l'une finit en 2024, l'autre en 2025). La période affichée dit
    donc à elle seule quelle colonne a été lue.
    """
    titre("④ LA COLONNE DE DATE — deux dates dans la source, une seule qui compte")
    for libelle, catalogue in (
        ("AVANT (aucune désignation)", "sans-designation"),
        ("APRÈS (date_livraison désignée)", "livraison-designee"),
        ("APRÈS (désignation mal orthographiée)", "designation-fautive"),
    ):
        source = load_catalog(CATALOGUES_DEUX_DATES / f"{catalogue}.yaml").get("commandes")
        faits = relever(source)
        print(f"\n  {libelle:38s} date_reference : {source.date_reference or '—'}")
        print(f"    {faits.en_clair()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lignes", type=int, default=5_000_000, help="volume du banc d'approximation"
    )
    parser.add_argument(
        "--patience", type=float, default=20.0, help="attente max du banc sur la source muette (s)"
    )
    parser.add_argument(
        "--reprise",
        type=float,
        default=3.0,
        help="délai de reprise joué sur le banc de fraîcheur (s)",
    )
    parser.add_argument(
        "--delai", type=float, default=3.0, help="délai maximal joué sur le banc de bornage (s)"
    )
    parser.add_argument(
        "--seulement",
        choices=("fraicheur", "bornage", "approximation", "dates"),
        nargs="*",
        help="ne monter que ces bancs (défaut : les quatre)",
    )
    args = parser.parse_args()
    voulus = set(args.seulement or ("fraicheur", "bornage", "approximation", "dates"))

    export_env_file()
    defauts = ReglagesDuReleve()
    print(
        f"Réglages par défaut de l'application : délai {defauts.delai:g} s, "
        f"péremption {defauts.peremption:g} s, reprise {defauts.reprise:g} s, "
        f"seuil d'approximation {defauts.seuil_approximation}."
    )
    print("Le banc joue des durées plus courtes pour ne pas durer un quart d'heure.")

    if "fraicheur" in voulus:
        banc_de_la_fraicheur(args.reprise)
    if "bornage" in voulus:
        banc_du_bornage(args.delai, args.patience)
    if "approximation" in voulus:
        banc_de_lapproximation(args.lignes, defauts.seuil_approximation)
    if "dates" in voulus:
        banc_de_la_colonne_de_date()


if __name__ == "__main__":
    main()
