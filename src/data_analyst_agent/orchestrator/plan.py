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
  données dans le message (ex. « grand magasin, univers chien, marque nationale
  à 49,90 €, un samedi de novembre... ») — extrais-les telles quelles dans
  `features` (noms exacts du schéma).
- "fetch_then_predict" : prédiction ML pour un ou des individus DÉSIGNÉS PAR
  RÉFÉRENCE À UNE SOURCE — un identifiant (« le SKU001 »), un filtre ou un
  groupe (« tous les produits de l'univers chat », « les SKU du catalogue ») :
  leurs features doivent d'abord être lues dans la source. Formule dans
  `data_question` ce qu'il faut récupérer (la ou les lignes).

Sources de données disponibles :
{sources}

Modèles de prédiction disponibles (dataset -> features attendues) :
{datasets}

Contraintes :
- Pour query/analyze/fetch_then_predict : choisis `source` parmi les sources
  listées (champ `name`). Si la demande NE DÉSIGNE aucune source (ni par son
  nom, ni par le sujet des données) et que plusieurs sources existent, laisse
  `source` VIDE — ne devine pas : le système demandera à l'utilisateur de
  préciser.
- Pour predict/fetch_then_predict : choisis `dataset` parmi les modèles listés.
- Une prédiction qui désigne des individus STOCKÉS dans une source listée
  (« le SKU001 », « tous les produits DU CATALOGUE ») est fetch_then_predict :
  un attribut de filtre (ex. l'univers produit) n'est pas un jeu de features
  complet.
- MAIS un cas hypothétique (« un produit d'entrée de gamme en promo », « un
  samedi de novembre »), sans référence à une ligne existante, est predict :
  extrais les features effectivement données (même incomplètes — le système
  redemandera le reste). Idem si aucune source listée ne s'y prête.
- N'invente ni source ni dataset ni feature : n'extrais que ce que le message
  dit réellement, et RIEN qui ne soit un champ du schéma listé ci-dessus.
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
    history_context: str | None = None,
) -> Agent[None, Plan]:
    """Agent planificateur avec sortie structurée Plan.

    ``pending_context`` (multi-tours) : décrit une prédiction en attente de
    features — le message courant est probablement un complément d'information.
    ``history_context`` : décrit le tour précédent (question + action) pour
    qu'un ajustement (« mets des couleurs plus vives ») soit rattaché à lui.
    """
    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        sources=sources_description, datasets=datasets_description
    )
    for extra in (history_context, pending_context):
        if extra:
            system_prompt = f"{system_prompt}\n{extra}"
    return Agent(output_type=Plan, system_prompt=system_prompt)
