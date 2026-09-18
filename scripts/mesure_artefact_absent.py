"""Mesure si l'agent DIT qu'il n'a pas produit ce qu'on lui demande de reprendre.

Le défaut, relevé en live sur les deux moteurs au chantier précédent
(`docs/surface-conversationnelle.md` §20.10) : dans une conversation qui a
produit un diagramme en barres, on demande « reprends le camembert des ports
d'embarquement que tu m'avais fait ». Ce camembert n'existe pas. L'agent de
rappel décline, le planificateur en fabrique un — correct, bons chiffres — et
**rien ne dit qu'il n'existait pas**. Rien de faux n'est affirmé, aucun artefact
n'est inventé ; mais l'utilisateur repart en croyant qu'on a retrouvé son
travail alors qu'on en a refait un autre.

Ce runner mesure les DEUX moitiés du mécanisme, parce qu'elles se paient l'une
l'autre :

- l'**aveu** quand le message DÉSIGNE un artefact passé qui n'existe pas ;
- le **silence** quand il demande une figure neuve sans rien désigner. Une
  tournure innocente prise pour une désignation est le défaut symétrique, et il
  coûterait une phrase de repentir à chaque tour.

Deux bancs, et ils ne mesurent pas la même chose :

1. **le signal, à sec** — le détecteur est déterministe (`rappel.designation_
   dun_artefact_passe`), sa précision se mesure donc sans le moindre appel au
   modèle, sur un corpus de tournures étiquetées. C'est là que se lisent les
   faux positifs ;
2. **le parcours, en vrai** — serveur LLM en place, Postgres réel, bac à sable
   Docker. Six tours dans une conversation, puis trois messages d'anaphore pure
   (« celui d'avant », « le précédent ») posés chacun dans un fil NEUF : c'est
   le seul décor où ils ne peuvent désigner que du vide, donc le seul où
   l'oracle est mécanique.

Le verdict est lu dans la trace et la réponse, jamais dans une impression : quel
nœud a répondu, si la phrase d'aveu y est, combien de figures sont sorties.

    uv run python scripts/mesure_artefact_absent.py
    uv run python scripts/mesure_artefact_absent.py --markdown /tmp/absent.md \
        --json /tmp/absent.json

Prérequis : le serveur LLM répond (``DAA_LLM_BASE_URL``), Postgres est seedé
(``scripts/seed_titanic_postgres.py``) et Docker sert l'image du bac à sable.
On vise un autre serveur par l'environnement, jamais en modifiant le `.env` :

    DAA_LLM_BASE_URL=http://localhost:8100/v1 \\
        DAA_LLM_MODEL=google/gemma-4-E4B-it-qat-w4a16-ct \\
        uv run python scripts/mesure_artefact_absent.py
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator
from data_analyst_agent.orchestrator.rappel import (
    SUITE_EST_NEUVE,
    designation_dun_artefact_passe,
)
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

SOURCE = "titanic"


def replie(texte: str) -> str:
    """Minuscules, sans accents : l'oracle compare du sens, pas de la typographie."""
    sans_accent = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in sans_accent if not unicodedata.combining(c))


# La phrase qui porte tout le mécanisme, telle qu'on la cherche dans la réponse.
MARQUEUR_D_AVEU = replie(SUITE_EST_NEUVE)


# --- banc 1 : le signal, mesuré à sec ----------------------------------------

# Les tournures qui DÉSIGNENT un artefact passé. Trois d'entre elles ne portent
# aucun mot du catalogue — « celui d'avant », « le précédent », « ce que tu
# m'avais sorti » : c'est là que le signal doit être grammatical, parce qu'un
# lexique d'objets n'y trouverait rien à quoi s'accrocher.
DESIGNATIONS = (
    "Reprends le camembert des ports d'embarquement que tu m'avais fait.",
    "Tu peux me remontrer le camembert des ports d'embarquement que tu avais fait ?",
    "Reprends le graphe de tout à l'heure et mets les barres en bleu.",
    "Reviens au tout premier graphique, celui par classe, et repasse-le en vert.",
    "Le premier tableau que tu m'as sorti, redis-moi ce qu'il y avait dedans.",
    "Le graphique de tantôt, en vert.",
    "Refais le même graphique mais en bleu.",
    "Je voudrais revoir la courbe que tu as tracée plus tôt.",
    "Le nuage de points âge/tarif que tu m'avais sorti, remets-le-moi.",
    "Remontre-moi celui d'avant.",
    "Affiche le précédent.",
    "Ce que tu m'avais sorti, tu peux me le remettre ?",
)

# Les tournures INNOCENTES, et elles pèsent autant. Les cinq premières sont des
# questions réelles de `scripts/mesure_surface_conversationnelle.py` : celles
# qui portent « tu as », c'est-à-dire le piège du détecteur.
INNOCENTES = (
    "De quand datent les données que tu as ?",
    "qu'est-ce que tu as comme données ?",
    "À quelles bases de données as-tu accès ?",
    "montre-moi ce que tu as",
    "tu as accès à quelles données",
    "Quelles colonnes de la table passengers contiennent des valeurs manquantes ?",
    "Fais-moi un camembert des ports d'embarquement.",
    "Fais-moi un graphique en barres du nombre de passagers par classe.",
    "Combien de passagers y a-t-il en tout ?",
    "Et quel est l'âge moyen des passagers ?",
    "Peux-tu me faire un histogramme des âges ?",
    "Quel est l'âge du passager le plus âgé ?",
    "Compare les résultats de la première classe et de la deuxième.",
    "Ajoute une légende au graphique.",
    "Fais un tableau des survivants par sexe.",
    "Trace la courbe des âges.",
    "Est-ce que tu sais faire des prédictions, et sur quoi ?",
    "Quelle est la taille de tes données ?",
)


def banc_du_signal() -> tuple[list[tuple[str, str, bool]], int, int]:
    """Le détecteur passé sur le corpus étiqueté — rappel, et faux positifs.

    Aucun appel au modèle : le signal est une propriété du message, et une
    propriété se vérifie, elle ne s'échantillonne pas.
    """
    lignes = []
    trouves = 0
    faux = 0
    for message in DESIGNATIONS:
        marqueur = designation_dun_artefact_passe(message)
        trouves += bool(marqueur)
        lignes.append(("désignation", message, bool(marqueur)))
    for message in INNOCENTES:
        marqueur = designation_dun_artefact_passe(message)
        faux += bool(marqueur)
        lignes.append(("innocente", message, not marqueur))
    return lignes, trouves, faux


# --- banc 2 : le parcours, en vrai -------------------------------------------


@dataclass
class Tour:
    """Un tour du parcours, et ce qu'on exige de lui.

    ``fil_neuf`` pose le message dans une conversation VIERGE. C'est le seul
    décor où « celui d'avant » ne peut désigner que du vide, donc le seul où
    l'oracle d'une anaphore pure soit mécanique.
    """

    cle: str
    message: str
    attendu: str
    aveu_exige: bool
    fil_neuf: bool = False


PARCOURS = [
    Tour(
        "figure",
        "Fais-moi un graphique en barres du nombre de passagers par classe.",
        "une figure, retenue sous un nom — le fil a désormais quelque chose",
        aveu_exige=False,
    ),
    Tour(
        "designe-absent",
        "Reprends le camembert des ports d'embarquement que tu m'avais fait.",
        "l'ABSENCE dite, puis le camembert produit quand même",
        aveu_exige=True,
    ),
    Tour(
        "neuve",
        "Fais-moi un histogramme des âges des passagers.",
        "une figure, et PAS un mot — rien n'était désigné",
        aveu_exige=False,
    ),
    Tour(
        "designe-present",
        "Reprends le graphe de tout à l'heure, celui par classe, et mets les barres en bleu.",
        "le rejeu de la figure du tour 1, et PAS un mot — le rappel a abouti",
        aveu_exige=False,
    ),
    Tour(
        "designe-absent-2",
        "Le nuage de points âge/tarif que tu m'avais sorti, remets-le-moi.",
        "l'ABSENCE dite, sur une seconde tournure",
        aveu_exige=True,
    ),
    Tour(
        "temoin-requete",
        "Combien de passagers ont survécu ?",
        "une requête ordinaire, et PAS un mot",
        aveu_exige=False,
    ),
    # Les trois anaphores pures, chacune dans un fil NEUF : rien n'existe, donc
    # rien ne peut être retrouvé, et l'aveu doit tomber sans le moindre appel à
    # l'agent de rappel — le catalogue vide suffit à trancher.
    Tour(
        "anaphore-celui-davant",
        "Remontre-moi celui d'avant.",
        "fil vierge : l'absence dite, sans appeler le modèle de rappel",
        aveu_exige=True,
        fil_neuf=True,
    ),
    Tour(
        "anaphore-le-precedent",
        "Affiche le précédent.",
        "fil vierge : l'absence dite, sans appeler le modèle de rappel",
        aveu_exige=True,
        fil_neuf=True,
    ),
    Tour(
        "anaphore-ce-que-tu-mavais-sorti",
        "Ce que tu m'avais sorti, tu peux me le remettre ?",
        "fil vierge : l'absence dite, sans appeler le modèle de rappel",
        aveu_exige=True,
        fil_neuf=True,
    ),
]


@dataclass
class Releve:
    """Ce qu'un tour a réellement produit — lu dans la trace et la réponse."""

    tour: Tour
    reponse: str
    noeuds: list[str]
    detail_du_rappel: str
    figures: int
    artefacts_apres: list[str]
    duree_ms: int = 0
    verdict: str = ""
    pourquoi: str = ""

    @property
    def aveu_present(self) -> bool:
        return MARQUEUR_D_AVEU in replie(self.reponse)


def juger(releve: Releve) -> Releve:
    """Le verdict, lu dans la réponse et la trace — jamais dans une impression.

    Un seul fait décide : la phrase d'aveu est-elle là où elle doit être, et
    absente là où elle n'a rien à faire ? Le reste — la figure produite, le
    rejeu — est relevé pour prouver qu'on n'a bloqué personne, et il est exigé
    sur les tours qui doivent produire.
    """
    if releve.tour.aveu_exige and not releve.aveu_present:
        return _verdict(releve, False, "aveu ABSENT")
    if not releve.tour.aveu_exige and releve.aveu_present:
        return _verdict(releve, False, "aveu de TROP (faux positif)")
    if releve.tour.cle == "designe-present":
        ok = releve.detail_du_rappel.startswith("rejeu de graphique_1") and releve.figures >= 1
        return _verdict(releve, ok, f"rappel={releve.detail_du_rappel!r}")
    if releve.tour.cle in ("figure", "neuve", "designe-absent"):
        ok = releve.figures >= 1
        return _verdict(releve, ok, f"figures={releve.figures}")
    if releve.tour.fil_neuf:
        # L'aveu d'un fil vierge se paie zéro appel : le nœud n'a pas de
        # catalogue à soumettre, donc rien à demander au modèle.
        ok = "rappel" in releve.noeuds and "absence dite" in releve.detail_du_rappel
        return _verdict(releve, ok, f"rappel={releve.detail_du_rappel!r}")
    return _verdict(releve, True, f"figures={releve.figures}")


def _verdict(releve: Releve, ok: bool, pourquoi: str) -> Releve:
    releve.verdict = "conforme" if ok else "manqué"
    releve.pourquoi = pourquoi
    return releve


def poser(orchestrateur: Orchestrator, tour: Tour, fil: str, racine: Path) -> Releve:
    depart = time.monotonic()
    reponse: ChatAnswer = orchestrateur.ask(
        tour.message, conversation_id=fil, source_de_travail=SOURCE
    )
    duree = int((time.monotonic() - depart) * 1000)
    espace = ConversationWorkspace(racine, fil, limits=orchestrateur.limits)
    return Releve(
        tour=tour,
        reponse=reponse.answer,
        noeuds=[s.node for s in reponse.trace],
        detail_du_rappel=next((s.detail for s in reponse.trace if s.node == "rappel"), ""),
        figures=len([a for a in reponse.artifacts if a.mime == "image/png"]),
        artefacts_apres=[a.name for a in espace.artifacts],
        duree_ms=duree,
    )


def rapport(releves: list[Releve], signal, reglages) -> str:
    lignes_signal, trouves, faux = signal
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    lignes = [
        "## L'artefact désigné qui n'existe pas",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        "",
        "### Le signal, mesuré à sec",
        "",
        f"**{trouves}/{len(DESIGNATIONS)} désignations reconnues, "
        f"{faux}/{len(INNOCENTES)} faux positifs.**",
        "",
        "| étiquette | tournure | verdict |",
        "|---|---|---|",
    ]
    for etiquette, message, ok in lignes_signal:
        lignes.append(f"| {etiquette} | {message} | {'✅' if ok else '❌'} |")
    lignes += [
        "",
        "### Le parcours, en vrai",
        "",
        f"**{conformes}/{len(releves)} tours conformes.**",
        "",
        "| # | tour | attendu | aveu | verdict | nœuds | figures | ms |",
        "|---|------|---------|------|---------|-------|---------|----|",
    ]
    for numero, r in enumerate(releves, start=1):
        attendu = "exigé" if r.tour.aveu_exige else "interdit"
        lignes.append(
            f"| {numero} | {r.tour.cle} | {r.tour.attendu} | {attendu} → "
            f"{'présent' if r.aveu_present else 'absent'} | **{r.verdict}** | "
            f"{' > '.join(r.noeuds)} | {r.figures} | {r.duree_ms} |"
        )
    return "\n".join(lignes)


def journal(releves: list[Releve], signal) -> dict:
    lignes_signal, trouves, faux = signal
    return {
        "signal": {
            "reconnues": trouves,
            "designations": len(DESIGNATIONS),
            "faux_positifs": faux,
            "innocentes": len(INNOCENTES),
            "detail": [
                {"etiquette": e, "tournure": m, "conforme": ok} for e, m, ok in lignes_signal
            ],
        },
        "parcours": [
            {
                "cle": r.tour.cle,
                "message": r.tour.message,
                "verdict": r.verdict,
                "pourquoi": r.pourquoi,
                "aveu_exige": r.tour.aveu_exige,
                "aveu_present": r.aveu_present,
                "noeuds": r.noeuds,
                "detail_du_rappel": r.detail_du_rappel,
                "figures": r.figures,
                "artefacts": r.artefacts_apres,
                "duree_ms": r.duree_ms,
                "reponse": r.reponse,
            }
            for r in releves
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="écrit le tableau ici")
    parser.add_argument("--json", type=Path, help="écrit le journal complet ici")
    parser.add_argument("--fil", default="", help="identifiant de conversation (défaut : neuf)")
    args = parser.parse_args()

    signal = banc_du_signal()
    _, trouves, faux = signal
    print(
        f"Signal : {trouves}/{len(DESIGNATIONS)} désignations reconnues, "
        f"{faux}/{len(INNOCENTES)} faux positifs.\n"
    )

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    registre = Registry.load(reglages.models_registry_path)
    if not any(s.name == SOURCE for s in catalogue.sources):
        raise SystemExit(f"la source « {SOURCE} » n'est pas au catalogue : rien à mesurer")
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")

    orchestrateur = Orchestrator(
        settings=reglages, model=build_model(reglages), catalog=catalogue, registry=registre
    )
    fil = args.fil or f"mesure-absent-{uuid.uuid4().hex[:8]}"
    racine = reglages.workspace_dir
    print(f"Conversation : {fil}\n")

    releves: list[Releve] = []
    for numero, tour in enumerate(PARCOURS, start=1):
        cible = f"{fil}-{tour.cle}" if tour.fil_neuf else fil
        print(f"[{numero}/{len(PARCOURS)}] {tour.cle} — « {tour.message} »")
        releve = juger(poser(orchestrateur, tour, cible, racine))
        releves.append(releve)
        print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
        print(f"    rappel : {releve.detail_du_rappel or '—'}")
        print(f"    réponse : {' '.join(releve.reponse.split())[:220]}\n")

    texte = rapport(releves, signal, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(journal(releves, signal), ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
