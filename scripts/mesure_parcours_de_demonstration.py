"""Le parcours de démonstration, rejoué depuis un message utilisateur.

Les seize tours de `docs/sources-de-demonstration.md` — trois ouvertures qui
nomment une source, les sept questions métier, les trois tours du verrou de
source, les deux questions sur le sens d'une colonne piégeuse, et la réserve de
forme. Ce parcours était rejoué à la main d'un chantier à l'autre ; il est ici
un runner, pour la raison qui vaut pour tous les autres de ce dossier : une
campagne qu'on refait à la main ne se compare qu'à ce dont on se souvient.

**Chaque tour est jugé mécaniquement**, et sur ce que l'utilisateur voit
vraiment : la phrase de réponse ET le tableau d'artefacts. Le prompt de l'agent
SQL lui INTERDIT de recopier les lignes dans sa phrase — ne lire que la phrase
ferait passer pour muet un tour qui a rendu ses chiffres.

**Le protocole imite l'API, pas un banc d'essai.** ``source_de_travail`` vaut
toujours une chaîne, jamais ``None`` : avec ``None``, le court-circuit
déterministe de `_choix_de_source` ne se déclenche pas, un message qui ne porte
qu'un nom de source ne lie rien, et le banc fabrique un défaut que le produit
n'a pas. C'est arrivé ; c'est écrit dans le document.

    uv run python scripts/mesure_parcours_de_demonstration.py
    uv run python scripts/mesure_parcours_de_demonstration.py --markdown /tmp/p.md

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, le
catalogue semé, Postgres joignable, le serveur LLM en place.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur, nombres

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator


@dataclass(frozen=True)
class Tour:
    """Un tour du parcours, et ce qui décide de son verdict.

    ``fil`` groupe les tours d'une même conversation — le verrou de source n'a
    de sens que sur trois tours enchaînés. ``source`` est la source de travail
    passée à l'entrée, comme l'API la passe.
    """

    cle: str
    fil: str
    message: str
    source: str = ""
    # Ce que la réponse (ou le tableau) doit porter : des nombres, à la
    # tolérance près, et des fragments de texte.
    nombres: tuple[float, ...] = ()
    fragments: tuple[str, ...] = ()
    # Ce qu'elle ne doit PAS porter — la valeur du voisin, pour le verrou.
    interdits: tuple[float, ...] = ()
    attendu: str = ""


PARCOURS: tuple[Tour, ...] = (
    # --- trois ouvertures : un message qui ne porte qu'un nom de source -------
    Tour(
        cle="ouverture-exploitation",
        fil="ouverture-exploitation",
        message="exploitation",
        fragments=("exploitation",),
        nombres=(6.0,),
        attendu="la source est annoncée avec ses 6 tables",
    ),
    Tour(
        cle="ouverture-telemetrie",
        fil="ouverture-telemetrie",
        message="telemetrie",
        fragments=("telemetrie",),
        nombres=(3.0,),
        attendu="la source est annoncée avec ses 3 tables",
    ),
    Tour(
        cle="ouverture-facturation",
        fil="ouverture-facturation",
        message="facturation",
        fragments=("facturation",),
        nombres=(3.0,),
        attendu="la source est annoncée avec ses 3 feuilles",
    ),
    # --- les sept questions métier -------------------------------------------
    Tour(
        cle="Q1",
        fil="metier-exploitation",
        message="combien de sessions de recharge y a-t-il en tout ?",
        source="exploitation",
        nombres=(48000.0,),
        attendu="48 000",
    ),
    Tour(
        cle="Q2",
        fil="metier-exploitation",
        message="combien de sessions ont le statut T en 2025 ?",
        source="exploitation",
        nombres=(42281.0,),
        attendu="42 281",
    ),
    Tour(
        cle="Q3",
        fil="metier-exploitation",
        message="quelle énergie totale, en kWh, a été délivrée sur l'année ?",
        source="exploitation",
        nombres=(1757519.23,),
        attendu="1 757 519,23 kWh",
    ),
    Tour(
        cle="Q4",
        fil="metier-exploitation",
        message=(
            "quelles sont les trois stations avec le plus de sessions ? donne leur code et leur nom"
        ),
        source="exploitation",
        nombres=(1015.0, 911.0, 872.0),
        fragments=("ST-097", "ST-029", "ST-016"),
        attendu="les trois stations, dans l'ordre, avec les comptes",
    ),
    Tour(
        cle="Q5",
        fil="metier-exploitation",
        message="combien de sessions par région ? classe-les de la plus active à la moins active",
        source="exploitation",
        nombres=(9673.0, 9500.0, 8866.0, 7664.0, 6372.0, 5925.0),
        attendu="les six régions, dans l'ordre",
    ),
    Tour(
        cle="Q6",
        fil="metier-telemetrie",
        message="combien de lignes dans la table releves_puissance ?",
        source="telemetrie",
        nombres=(547200.0,),
        attendu="547 200",
    ),
    Tour(
        cle="Q7",
        fil="metier-facturation",
        message="combien de factures as-tu, et quel est le montant total hors taxes facturé ?",
        source="facturation",
        nombres=(1200.0, 574408.10),
        attendu="1 200 factures, 574 408,10 € HT",
    ),
    # --- le verrou de source, sur une colonne qui existe des deux côtés -------
    # Son premier tour est une ouverture de plus, et il lui faut la sienne : le
    # verrou se mesure sur trois tours ENCHAÎNÉS, et emprunter le fil d'une
    # ouverture déjà jouée mesurerait autre chose.
    Tour(
        cle="verrou-ouverture",
        fil="verrou",
        message="exploitation",
        fragments=("exploitation",),
        nombres=(6.0,),
        attendu="la source est liée au fil du verrou",
    ),
    Tour(
        cle="verrou-source-liee",
        fil="verrou",
        message="quelle est l'énergie totale en kWh dans cette source ?",
        source="exploitation",
        nombres=(1757519.23,),
        interdits=(531098.10,),
        attendu="1 757 519,23 — celle de la source liée, et pas celle du voisin",
    ),
    Tour(
        cle="verrou-bascule",
        fil="verrou",
        message="et dans facturation, quelle est l'énergie totale en kWh ?",
        source="exploitation",
        nombres=(531098.10,),
        interdits=(1757519.23,),
        fragments=("facturation",),
        attendu="531 098,10 après une bascule annoncée",
    ),
    # --- le sens d'une colonne piégeuse, qui ne vient que du dictionnaire -----
    Tour(
        cle="sens-statut",
        fil="sens-statut",
        message="que signifie la colonne statut de la table sessions ?",
        source="exploitation",
        fragments=("dictionnaire", "'T'", "'I'", "'E'"),
        attendu="les trois codes, cités du dictionnaire",
    ),
    Tour(
        cle="sens-puissance",
        fil="sens-puissance",
        message="que veut dire la colonne puissance_kw dans la source telemetrie ?",
        source="telemetrie",
        fragments=("dictionnaire", "sentinelle"),
        attendu="la sentinelle, citée du dictionnaire",
    ),
    # --- la réserve de forme : la question DOIT nommer sa source -------------
    Tour(
        cle="reserve-de-forme",
        fil="reserve",
        message="que veut dire la colonne puissance_kw ?",
        fragments=("source",),
        attendu="routée comme un inventaire — la question ne nomme pas sa source",
    ),
)


@dataclass
class Releve:
    tour: Tour
    reponse: str
    tableau: str
    noeuds: list[str]
    valeurs: list[float] = field(default_factory=list)
    verdict: str = ""
    pourquoi: str = ""
    appels_llm: int = 0
    duree_ms: int = 0


def juger(tour: Tour, reponse: ChatAnswer, valeurs: list[float], texte: str) -> tuple[str, str]:
    if reponse.error:
        return "échec", f"erreur : {reponse.error}"
    manquants = [c for c in tour.nombres if not any(abs(v - c) <= 0.5 for v in valeurs)]
    if manquants:
        return "échec", f"chiffre(s) absent(s) : {', '.join(f'{c:g}' for c in manquants)}"
    presents = [c for c in tour.interdits if any(abs(v - c) <= 0.5 for v in valeurs)]
    if presents:
        return "échec", f"chiffre du voisin : {', '.join(f'{c:g}' for c in presents)}"
    absents = [f for f in tour.fragments if f.lower() not in texte.lower()]
    if absents:
        return "échec", f"fragment(s) absent(s) : {', '.join(absents)}"
    return "conforme", tour.attendu


def poser(orchestrateur: Orchestrator, tour: Tour, fil: str, liees: dict[str, str]) -> Releve:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        tour.message,
        conversation_id=fil,
        # Toujours une CHAÎNE, jamais None : c'est ce que l'API passe, et le
        # court-circuit de choix de source en dépend. La source retenue par le
        # tour précédent du même fil prime sur celle déclarée par le tour.
        source_de_travail=liees.get(tour.fil, tour.source),
    )
    duree = int((time.monotonic() - depart) * 1000)
    liees[tour.fil] = reponse.source_de_travail or liees.get(tour.fil, tour.source)
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    verdict, pourquoi = juger(tour, reponse, valeurs, texte)
    return Releve(
        tour=tour,
        reponse=reponse.answer,
        tableau=tableau,
        noeuds=[s.node for s in reponse.trace],
        valeurs=valeurs,
        verdict=verdict,
        pourquoi=pourquoi,
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )


def rapport(releves: list[Releve], reglages) -> str:
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    lignes = [
        "## Le parcours de démonstration, rejoué",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        "",
        f"**{conformes}/{len(releves)} tours conformes, "
        f"{sum(r.appels_llm for r in releves)} appels LLM.**",
        "",
        "| tour | message | nœuds | appels | verdict | ce qui a décidé |",
        "|---|---|---|---|---|---|",
    ]
    for r in releves:
        message = " ".join(r.tour.message.split())
        lignes.append(
            f"| `{r.tour.cle}` | {message} | {' → '.join(r.noeuds)} | {r.appels_llm} "
            f"| **{r.verdict}** | {r.pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--seulement", nargs="*", default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")

    orchestrateur = Orchestrator(
        settings=reglages,
        model=ModeleCompteur(build_model(reglages)),
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    suffixe = uuid.uuid4().hex[:8]
    liees: dict[str, str] = {}
    releves: list[Releve] = []
    tours = [t for t in PARCOURS if not args.seulement or t.cle in args.seulement]
    for numero, tour in enumerate(tours, start=1):
        print(f"[{numero}/{len(tours)}] {tour.cle} — « {tour.message} »")
        releve = poser(orchestrateur, tour, f"parcours-{suffixe}-{tour.fil}", liees)
        releves.append(releve)
        print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
        print(f"    réponse : {' '.join(releve.reponse.split())[:220]}\n")

    texte = rapport(releves, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                [{**r.__dict__, "tour": r.tour.cle} for r in releves],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
