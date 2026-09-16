"""Dix façons de demander le MÊME palmarès — et ce que le SQL en fait.

C31 a ajouté au prompt de l'agent SQL une règle qui énumère des tournures
(« les trois plus… », « le plus gros… », « classe par… »). Elle est annoncée
0/5 → 5/5, et elle l'est : sur LA phrase qui l'a motivée. Remesurée sur une
seconde formulation de la même demande — « les trois stations les plus
sollicitées » — elle rend 0/3. « Les plus sollicitées » ne figure pas dans la
liste.

Ce runner pose la même demande de DIX façons qu'un utilisateur emploierait, et
lit deux choses dans chaque tour :

- **le SQL**, jugé par ``agents/retrieval/classement`` : toute expression du
  ORDER BY figure-t-elle dans le SELECT ? C'est la propriété qu'on veut, dite
  sans regarder la question — donc mesurable quelle que soit la phrase ;
- **ce que l'utilisateur voit** : la réponse ET le tableau doivent porter les
  trois stations et leurs trois comptes. Un SQL en règle qui se trompe de
  grandeur (l'énergie au lieu des sessions) reste un tour faux, et se
  distingue ici d'un palmarès sans ses chiffres.

    uv run python scripts/mesure_classement_sans_lexique.py
    uv run python scripts/mesure_classement_sans_lexique.py --tirages 3 --markdown /tmp/c.md

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, le
catalogue semé, Postgres joignable, le serveur LLM en place.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur, nombres

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.agents.retrieval.classement import grandeurs_non_projetees
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

SOURCE = "exploitation"

# Les trois stations en tête de `exploitation` par nombre de sessions, et leurs
# comptes. Même oracle que le tour Q4 du parcours de démonstration : c'est la
# MÊME question, et dix phrases pour la poser.
COMPTES = (1015.0, 911.0, 872.0)
STATIONS = ("ST-097", "ST-029", "ST-016")

# Dix formulations DISTINCTES, écrites comme un utilisateur parle — pas comme
# un gabarit se décline. Aucune n'annonce « classement » ; deux seulement
# emploient un superlatif que la règle de C31 énumère.
FORMULATIONS: tuple[tuple[str, str], ...] = (
    (
        "F01",
        "quelles sont les trois stations avec le plus de sessions ? donne leur code et leur nom",
    ),
    ("F02", "les trois stations les plus sollicitées : donne leur code et leur nom"),
    ("F03", "qui charge le plus ? je veux les 3 premières stations, leur code et leur nom"),
    ("F04", "le top 3 des stations, avec leur code et leur nom"),
    ("F05", "quelles stations tournent le plus ? les 3 premières, code et nom"),
    ("F06", "classe-moi les stations par activité et garde les 3 premières, code et nom"),
    (
        "F07",
        "où est-ce qu'on recharge le plus ? donne-moi le code et le nom des 3 stations en tête",
    ),
    ("F08", "je cherche les 3 stations les plus fréquentées, avec leur code et leur nom"),
    ("F09", "palmarès des stations : les 3 premières, leur code et leur nom"),
    ("F10", "sur quelles stations y a-t-il eu le plus de recharges ? les 3 premières, code et nom"),
)


@dataclass
class Tirage:
    cle: str
    message: str
    numero: int
    sql: str = ""
    absentes: tuple[str, ...] = ()
    reponse: str = ""
    projette: bool = False
    oracle: bool = False
    pourquoi: str = ""
    appels_llm: int = 0
    duree_ms: int = 0

    @property
    def conforme(self) -> bool:
        return self.projette and self.oracle


def poser(orchestrateur: Orchestrator, cle: str, message: str, numero: int) -> Tirage:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        message,
        conversation_id=f"classement-{uuid.uuid4().hex[:8]}",
        source_de_travail=SOURCE,
    )
    duree = int((time.monotonic() - depart) * 1000)
    # Le nœud `retrieval` porte la requête retenue dans son détail — c'est la
    # seule trace du SQL qui remonte jusqu'à l'appelant, et elle suffit : on
    # juge ce qui a été EXÉCUTÉ, pas ce qui a été tenté.
    sql = next((s.detail for s in reponse.trace if s.node == "retrieval"), "")
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    absentes = tuple(grandeurs_non_projetees(sql))
    tirage = Tirage(
        cle=cle,
        message=message,
        numero=numero,
        sql=" ".join(sql.split()),
        absentes=absentes,
        reponse=" ".join(reponse.answer.split()),
        projette=not absentes,
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )
    if reponse.error:
        tirage.pourquoi = f"erreur : {reponse.error}"
        return tirage
    manquants = [c for c in COMPTES if not any(abs(v - c) <= 0.5 for v in valeurs)]
    absents = [s for s in STATIONS if s.lower() not in texte.lower()]
    tirage.oracle = not manquants and not absents
    raisons = []
    if absentes:
        raisons.append("ORDER BY hors SELECT : " + ", ".join(absentes))
    if absents:
        raisons.append("station(s) absente(s) : " + ", ".join(absents))
    if manquants:
        raisons.append("compte(s) absent(s) : " + ", ".join(f"{c:g}" for c in manquants))
    tirage.pourquoi = " ; ".join(raisons) or "les trois stations et leurs trois comptes"
    return tirage


def rapport(tirages: list[Tirage], reglages, titre: str) -> str:
    par_cle: dict[str, list[Tirage]] = {}
    for t in tirages:
        par_cle.setdefault(t.cle, []).append(t)
    conformes = sum(1 for t in tirages if t.conforme)
    projette = sum(1 for t in tirages if t.projette)
    lignes = [
        f"## {titre}",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`), source `{SOURCE}`",
        "",
        f"**{conformes}/{len(tirages)} tirages conformes** "
        f"(SQL en règle : {projette}/{len(tirages)}), "
        f"{sum(t.appels_llm for t in tirages)} appels LLM.",
        "",
        "| clé | formulation | SQL en règle | oracle | score | ce qui a décidé |",
        "|---|---|---|---|---|---|",
    ]
    for cle, lot in par_cle.items():
        bons = sum(1 for t in lot if t.conforme)
        regle = sum(1 for t in lot if t.projette)
        pourquoi = "; ".join(sorted({t.pourquoi for t in lot if not t.conforme})) or lot[0].pourquoi
        lignes.append(
            f"| `{cle}` | {lot[0].message} | {regle}/{len(lot)} | "
            f"{sum(1 for t in lot if t.oracle)}/{len(lot)} | **{bons}/{len(lot)}** | {pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--titre", default="Le classement, sans parier sur les mots")
    parseur.add_argument("--seulement", nargs="*", default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")
    orchestrateur = Orchestrator(
        settings=reglages,
        model=ModeleCompteur(build_model(reglages)),
        catalog=load_catalog(reglages.catalog_path),
        registry=Registry.load(reglages.models_registry_path),
    )
    choisies = [f for f in FORMULATIONS if not args.seulement or f[0] in args.seulement]
    tirages: list[Tirage] = []
    for cle, message in choisies:
        for numero in range(1, args.tirages + 1):
            print(f"[{cle}·{numero}] « {message} »")
            tirage = poser(orchestrateur, cle, message, numero)
            tirages.append(tirage)
            etat = "conforme" if tirage.conforme else "ÉCHEC"
            print(f"    → {etat} — {tirage.pourquoi} ({tirage.duree_ms} ms)")
            print(f"    sql : {tirage.sql[:200]}\n")

    texte = rapport(tirages, reglages, args.titre)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps([t.__dict__ for t in tirages], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
