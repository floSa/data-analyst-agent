"""Mesure ce qu'une conversation injecte dans le contexte du modèle, tour après tour.

L'audit de septembre 2026 (§3.4) a constaté une croissance **linéaire et sans
plafond** des objets intermédiaires, sur trois axes simultanés :

1. le **prompt du planificateur**, qui décrit chaque tableau mémorisé ;
2. les **montages ``--volume``** du ``docker run`` de l'analyse ;
3. le **catalogue effectif**, où chaque tableau devient une source éphémère.

Ce script rejoue N tours de type « query » dans un dossier temporaire et relève
les trois axes aux jalons demandés. Il n'appelle aucun LLM et n'ouvre aucun
réseau : il ne fait que construire les mêmes chaînes que ``_plan_node``.

    uv run python scripts/mesure_contexte.py
    uv run python scripts/mesure_contexte.py --tours 1 10 30 100 --fenetre 0

``--fenetre`` / ``--budget`` surchargent les réglages pour comparer un
plafonnement à un autre sans toucher au ``.env``.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, describe_features
from data_analyst_agent.agents.retrieval.catalog import Catalog, load_catalog
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.orchestrator.plan import PLANNER_SYSTEM_PROMPT
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

# Un tableau réaliste : c'est ce que produit une question « query » courante.
COLONNES = ["sepal_length", "sepal_width", "species"]
LIGNES = [[7.9, 3.8, "virginica"], [7.7, 2.6, "virginica"]]


def _description_fixe(settings: Settings) -> str:
    """La part du prompt qui NE dépend pas du nombre de tours."""
    try:
        catalogue = load_catalog(settings.catalog_path)
    except Exception:  # catalogue absent : on mesure quand même les axes 2 et 3
        catalogue = Catalog(sources=[])
    try:
        registre = Registry.load(settings.models_registry_path)
        datasets = "\n".join(_ligne_dataset(registre, d) for d in registre.datasets)
    except Exception:
        datasets = "(aucun modèle)"
    return PLANNER_SYSTEM_PROMPT.format(sources=catalogue.describe(), datasets=datasets)


def _ligne_dataset(registre: Registry, dataset: str) -> str:
    """La même description que ``Orchestrator._datasets_description``, par entrée."""
    entree = registre.get(dataset)
    if dataset not in SCHEMAS:
        return f"- {dataset} ({entree.task}) : features attendues : ?"
    features = describe_features(SCHEMAS[dataset])
    return f"- {dataset} ({entree.task}) : features attendues :\n{features}"


def _tokens(texte: str) -> int:
    """Le même compteur approché que le code de production, s'il existe.

    Le script sert AVANT et APRÈS le plafonnement : sur une base qui n'a pas
    encore de compteur, on retombe sur la règle des 4 caractères par token
    utilisée par l'audit, pour que les deux colonnes restent comparables.
    """
    try:
        from data_analyst_agent.orchestrator.context_budget import estimate_tokens
    except ImportError:
        return len(texte) // 4
    return estimate_tokens(texte)


def mesurer(tours: list[int], settings: Settings, fenetre: int | None, budget: int | None) -> None:
    jalons = sorted(set(tours))
    fixe = _description_fixe(settings)
    with tempfile.TemporaryDirectory(prefix="daa-mesure-") as racine:
        for tour in range(1, max(jalons) + 1):
            memoire = _ouvrir(Path(racine), settings, fenetre, budget)
            memoire.save_table(COLONNES, LIGNES, f"les 2 plus grandes fleurs (tour {tour})")
            if tour in jalons:
                _ligne(tour, _ouvrir(Path(racine), settings, fenetre, budget), fixe)


def _ouvrir(racine: Path, settings: Settings, fenetre: int | None, budget: int | None):
    """Ouvre la mémoire comme le ferait un nouveau tour (tout est relu du disque)."""
    try:
        from data_analyst_agent.orchestrator.context_budget import ContextLimits
    except ImportError:  # base d'avant le plafonnement : aucun réglage à passer
        return ConversationWorkspace(racine, "mesure")
    limites = ContextLimits.from_settings(settings)
    if fenetre is not None:
        limites = limites.model_copy(update={"artifact_window": fenetre})
    if budget is not None:
        limites = limites.model_copy(update={"token_budget": budget})
    return ConversationWorkspace(racine, "mesure", limits=limites)


def _ligne(tour: int, memoire: ConversationWorkspace, fixe: str) -> None:
    """Relève les trois axes pour l'état courant de la mémoire."""
    if hasattr(memoire, "fit_to_budget"):
        memoire.fit_to_budget(_tokens(fixe))
    catalogue = memoire.describe() or ""
    prompt = f"{fixe}\n\n{catalogue}"
    print(
        f"tour {tour:3d}: {len(memoire.artifacts):3d} objets sur disque"
        f" | prompt planificateur = {len(prompt):6d} car. (~{_tokens(prompt):5d} tokens)"
        f" | catalogue = {len(catalogue):5d} car."
        f" | {len(memoire.sandbox_files()):3d} montages sandbox"
        f" | {len(memoire.as_sources()):3d} sources ephemeres"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tours", type=int, nargs="+", default=[1, 10, 30, 100])
    parser.add_argument("--fenetre", type=int, default=None, help="surcharge la fenêtre glissante")
    parser.add_argument("--budget", type=int, default=None, help="surcharge le budget de tokens")
    args = parser.parse_args()
    mesurer(args.tours, get_settings(), args.fenetre, args.budget)


if __name__ == "__main__":
    main()
