"""Planificateur : question utilisateur -> Plan structuré (pattern Plan-and-Execute).

Le Plan est un objet Pydantic produit par le LLM (sortie structurée). La règle
de routage elle-même est du code (graph.py) — le prompt ne fait que classer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

Capability = Literal["query", "analyze", "predict", "fetch_then_predict"]

PLANNER_SYSTEM_PROMPT = """\
Tu es le planificateur d'un agent conversationnel d'analyse de données.
Classe la demande de l'utilisateur dans UNE capacité :

- "query" : requête ou agrégat SQL direct sur une source (compte, pourcentage,
  moyenne, liste filtrée...).
- "analyze" : VISUALISATION demandée (bar chart, histogramme, courbe...) ou
  analyse statistique multi-étapes (test du khi-deux, ANOVA, ACP...) — du code
  sera exécuté en sandbox.
- "predict" : prédiction ML pour UN cas dont les VALEURS des features sont
  données dans le message (ex. « sexe=female, classe=1, âge=28, tarif=80... »)
  — extrais-les telles quelles dans `features` (noms exacts du schéma).
- "fetch_then_predict" : prédiction ML pour un ou des individus DÉSIGNÉS PAR
  RÉFÉRENCE À UNE SOURCE — un identifiant (« le passager 42 »), un filtre ou
  un groupe (« toutes les femmes », « les passagers de 3e classe ») : leurs
  features doivent d'abord être lues dans la source. Formule dans
  `data_question` ce qu'il faut récupérer (la ou les lignes).

Sources de données disponibles :
{sources}

Modèles de prédiction disponibles (dataset -> features attendues) :
{datasets}

Contraintes :
- Pour query/analyze/fetch_then_predict : choisis `source` parmi les sources
  listées (champ `name`).
- Pour predict/fetch_then_predict : choisis `dataset` parmi les modèles listés.
- Une prédiction qui désigne des individus STOCKÉS dans une source listée
  (« le passager 42 », « toutes les femmes DE LA BASE ») est fetch_then_predict :
  un attribut de filtre (ex. le sexe) n'est pas un jeu de features complet.
- MAIS un cas hypothétique (« une femme de 1re classe », « un passager de
  30 ans »), sans référence à une ligne existante, est predict : extrais les
  features effectivement données (même incomplètes — le système redemandera
  le reste). Idem si aucune source listée ne s'y prête.
- N'invente ni source ni dataset ni feature : n'extrais que ce que le message
  dit réellement. Les indices grammaticaux explicites comptent : « une
  passagère », « elle » -> sex=female ; « un homme » -> sex=male.
"""


class Plan(BaseModel):
    """Décision de routage + paramètres extraits de la question."""

    capability: Capability
    source: str | None = None
    dataset: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    data_question: str | None = None  # fetch_then_predict : quoi récupérer
    reason: str = ""


def build_planner(
    sources_description: str,
    datasets_description: str,
    pending_context: str | None = None,
) -> Agent[None, Plan]:
    """Agent planificateur avec sortie structurée Plan.

    ``pending_context`` (multi-tours) : décrit une prédiction en attente de
    features — le message courant est probablement un complément d'information.
    """
    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        sources=sources_description, datasets=datasets_description
    )
    if pending_context:
        system_prompt = f"{system_prompt}\n{pending_context}"
    return Agent(output_type=Plan, system_prompt=system_prompt)
