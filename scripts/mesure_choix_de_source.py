"""Mesure le parcours de CHOIX DE SOURCE d'une conversation, de bout en bout.

La batterie de ``mesure_surface_conversationnelle.py`` pose chaque question
dans une conversation NEUVE : c'est un parti pris assumé — une question méta ne
doit pas devoir son succès au contexte laissé par la précédente. Elle ne peut
donc rien dire d'un mécanisme qui est, par nature, multi-tours. D'où ce second
runner.

Ce qui est mesuré, dans UNE conversation et dans l'ordre :

1. l'agent **propose** ses sources (première question, aucune source liée) ;
2. l'utilisateur **en valide une** (« titanic ») ;
3. une **vraie question** sur la source choisie, sans la nommer — c'est là que
   se voit le fait que la source est portée par la conversation et non
   redevinée ;
4. l'utilisateur **nomme l'autre source** : la bascule, et le fait qu'elle soit
   annoncée ;
5. un tour de plus, pour vérifier que la nouvelle source a bien remplacé
   l'ancienne.

Le coût est compté, pas estimé : le modèle est enveloppé dans le compteur
d'allers-retours de l'autre runner (``ModeleCompteur``). Le tour de validation
doit apparaître à **zéro appel** : reconnaître le nom d'une source du catalogue
dans un message est du code, et le faire trancher par un modèle serait payer un
aller-retour pour comparer deux chaînes de caractères.

Le fil vit dans un dossier temporaire, jeté à la sortie : ce runner ne touche
pas au ``DAA_WORKSPACE_DIR`` de l'installation.

    uv run python scripts/mesure_choix_de_source.py
    uv run python scripts/mesure_choix_de_source.py --markdown /tmp/tableau.md

Prérequis, les mêmes que l'autre runner : le serveur LLM répond, et les sources
du catalogue sont joignables (Postgres seedé pour ``titanic``).
"""

from __future__ import annotations

import argparse
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Le compteur d'allers-retours LLM et le formateur de cellule sont ceux de
# l'autre runner : la mesure du coût doit être LA MÊME des deux côtés, sinon
# les deux tableaux ne se comparent plus. (`scripts` est sur le chemin
# d'import — cf. le `pythonpath` de pytest et le dossier du script lui-même.)
from mesure_surface_conversationnelle import ModeleCompteur, une_ligne

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.graph import Orchestrator

FIL = "mesure-choix-de-source"


@dataclass
class Tour:
    """Un tour du parcours, et ce qu'on en a observé."""

    numero: int
    attendu: str  # ce que ce tour est censé produire, en clair
    message: str
    reponse: str
    source_liee: str
    capacite: str | None
    appels_llm: int
    duree_ms: int


def mener(
    orchestrateur: Orchestrator,
    compteur: ModeleCompteur,
    magasin: ConversationStore,
    numero: int,
    attendu: str,
    message: str,
) -> Tour:
    """Un tour, mené comme l'API le mène — magasin compris.

    Le passage par le ``ConversationStore`` n'est pas un détail de confort :
    c'est lui qui persiste la source liée et la relit au tour suivant. Un
    runner qui repasserait l'objet de mémoire mesurerait un mécanisme qui n'est
    pas celui de l'application.
    """
    fil = magasin.load(FIL) or magasin.create(FIL)
    depart = time.monotonic()
    avant = compteur.appels
    reponse = orchestrateur.ask(
        message,
        pending=fil.pending,
        conversation_id=fil.id,
        workspace_root=magasin.base_dir,
        source_de_travail=fil.source_de_travail,
    )
    duree = int((time.monotonic() - depart) * 1000)
    magasin.record_turn(
        fil.id,
        question=message,
        answer=reponse.answer,
        artifacts=reponse.artifacts,
        error=reponse.error,
        pending=reponse.pending,
        source_de_travail=reponse.source_de_travail,
    )
    apres = magasin.load(FIL)
    return Tour(
        numero=numero,
        attendu=attendu,
        message=message,
        reponse=reponse.answer,
        source_liee=apres.source_de_travail,
        capacite=reponse.plan.capability if reponse.plan else None,
        appels_llm=compteur.appels - avant,
        duree_ms=duree,
    )


def parcours(noms: list[str]) -> list[tuple[str, str]]:
    """Les cinq tours, dans l'ordre : (ce qui est attendu, le message).

    Les deux premiers noms du catalogue sont utilisés tels quels : le parcours
    doit se rejouer sur un autre catalogue sans être réécrit.
    """
    premiere, seconde = noms[0], noms[1]
    return [
        ("la proposition des sources", "bonjour, je voudrais regarder des données"),
        (f"la validation de « {premiere} »", premiere),
        ("une vraie question, source NON nommée", "combien de lignes en tout ?"),
        (f"la bascule vers « {seconde} », annoncée", f"et dans {seconde}, combien de lignes ?"),
        ("la nouvelle source tient, sans être nommée", "et combien de colonnes ?"),
    ]


def tableau_markdown(tours: list[Tour]) -> str:
    lignes = [
        "| Tour | Attendu | Message | Réponse obtenue | Source liée après | Capacité "
        "| Appels LLM |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in tours:
        lignes.append(
            f"| {t.numero} | {t.attendu} | {une_ligne(t.message, 80)} "
            f"| {une_ligne(t.reponse, 260)} | `{t.source_liee or '(aucune)'}` "
            f"| {t.capacite or '—'} | {t.appels_llm} |"
        )
    total = sum(t.appels_llm for t in tours)
    lignes += [
        "",
        f"Coût mesuré : **{total} appels LLM** pour {len(tours)} tours "
        f"(moyenne {total / len(tours):.2f} par tour).",
    ]
    return "\n".join(lignes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="écrit le tableau et le coût ici")
    args = parser.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    registre = Registry.load(reglages.models_registry_path)
    noms = [s.name for s in catalogue.sources]
    if len(noms) < 2:
        raise SystemExit(
            "ce parcours demande AU MOINS DEUX sources déclarées : c'est ce qui fait "
            f"qu'un choix existe. Catalogue courant : {', '.join(noms) or '(vide)'}."
        )
    print(f"Sources : {', '.join(noms)}")
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})\n")

    compteur = ModeleCompteur(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages, model=compteur, catalog=catalogue, registry=registre
    )
    with tempfile.TemporaryDirectory(prefix="daa-mesure-source-") as racine:
        magasin = ConversationStore(Path(racine), "mesure")
        tours = []
        for numero, (attendu, message) in enumerate(parcours(noms), start=1):
            print(f"[{numero}/5] {attendu}\n    > {message}", flush=True)
            tour = mener(orchestrateur, compteur, magasin, numero, attendu, message)
            tours.append(tour)
            print(f"    « {une_ligne(tour.reponse, 220)} »")
            print(
                f"    → source liée : {tour.source_liee or '(aucune)'}"
                f", {tour.appels_llm} appel(s) LLM, {tour.duree_ms} ms\n",
                flush=True,
            )

    texte = tableau_markdown(tours)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"\nTableau écrit dans {args.markdown}")


if __name__ == "__main__":
    main()
