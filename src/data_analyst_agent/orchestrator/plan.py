"""Planificateur : question utilisateur -> Plan structuré (pattern Plan-and-Execute).

Le Plan est un objet Pydantic produit par le LLM (sortie structurée). La règle
de routage elle-même est du code (graph.py) — le prompt ne fait que classer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from data_analyst_agent import prompts

Capability = Literal["query", "analyze", "predict", "fetch_then_predict"]


def planner_template() -> str:
    """Le gabarit du prompt du planificateur, marqueurs de substitution compris.

    Exposé parce que l'orchestrateur le PÈSE avant de le composer : un budget de
    tokens se décompte sur le prompt réel (cf. ``Orchestrator._peser_le_prompt``).
    """
    return prompts.gabarit(prompts.PLANNER)


class Plan(BaseModel):
    """Décision de routage + paramètres extraits de la question."""

    capability: Capability
    source: str | None = None
    dataset: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    data_question: str | None = None  # fetch_then_predict : quoi récupérer
    reason: str = ""


def planner_system_prompt(
    sources_description: str,
    datasets_description: str,
    pending_context: str | None = None,
    history_context: str | None = None,
) -> str:
    """Compose le prompt système du planificateur.

    ``pending_context`` (multi-tours) : décrit une prédiction en attente de
    features — le message courant est probablement un complément d'information.
    ``history_context`` : décrit le tour précédent (question + action) pour
    qu'un ajustement (« mets des couleurs plus vives ») soit rattaché à lui.

    Composé à part de l'agent parce que l'orchestrateur doit pouvoir le **peser
    avant de l'envoyer** : un budget de tokens se décompte sur le prompt réel,
    pas sur une estimation de ce qu'il contiendra.
    """
    system_prompt = prompts.render(
        prompts.PLANNER, sources=sources_description, datasets=datasets_description
    )
    for extra in (history_context, pending_context):
        if extra:
            system_prompt = f"{system_prompt}\n{extra}"
    return system_prompt


def planner_agent(system_prompt: str) -> Agent[None, Plan]:
    """Agent planificateur à sortie structurée Plan, pour un prompt déjà composé."""
    return Agent(output_type=Plan, system_prompt=system_prompt)
