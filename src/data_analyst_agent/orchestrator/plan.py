"""Planificateur : question utilisateur -> Plan structuré (pattern Plan-and-Execute).

Le Plan est un objet Pydantic produit par le LLM (sortie structurée). La règle
de routage elle-même est du code (graph.py) — le prompt ne fait que classer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_ai import Agent
from pydantic_core import CoreSchema

from data_analyst_agent import prompts

# Quatre actions SUR les données. C'est le contrat de sortie structurée du
# planificateur, et rien d'autre : ce que le LLM a le droit de choisir.
#
# Une question SUR le système (« quelles sources possèdes-tu ? ») est aussi une
# capacité de l'agent, et elle n'est volontairement PAS ici : elle est reconnue
# EN AMONT du planificateur, par un agent qui dispose d'outils rendant les faits
# du dépôt (`orchestrator/systeme.py`, nœud `system` en tête du graphe). La
# raison est mesurée, pas esthétique —
# ce Literal EST le JSON Schema de sortie, que le modèle lit même quand le
# prompt ne dit rien de la valeur ajoutée. Constaté en live sur le modèle en
# service, de
# façon reproductible : avec une cinquième valeur, « prédis la survie d'une
# passagère de 1re classe… » ressortait avec `pcass` au lieu de `pclass` —
# champ inconnu, prédiction remplacée par une relance. La valeur retirée, la
# prédiction aboutit.
#
# Élargir ce Literal n'est donc pas gratuit : c'est toucher au contrat que le
# modèle lit, et ça se paie sur les capacités voisines. Mesures dans
# docs/surface-conversationnelle.md.
Capability = Literal["query", "analyze", "predict", "fetch_then_predict"]


def planner_template() -> str:
    """Le gabarit du prompt du planificateur, marqueurs de substitution compris.

    Exposé parce que l'orchestrateur le PÈSE avant de le composer : un budget de
    tokens se décompte sur le prompt réel (cf. ``Orchestrator._peser_le_prompt``).
    """
    return prompts.gabarit(prompts.PLANNER)


class Plan(BaseModel):
    """Décision de routage + paramètres extraits de la question.

    CETTE DOCSTRING NE PART PAS AU MODÈLE (``__get_pydantic_json_schema__``), et
    ce n'est pas un détail de présentation : c'est le réglage le plus cher qu'on
    ait mesuré sur ce contrat.
    """

    capability: Capability
    source: str | None = None
    # LE PÉRIMÈTRE, quand la question en demande un — et c'est un CHAMP de plus
    # au contrat de sortie, pas une valeur de plus au `Literal` ci-dessus. La
    # distinction est celle qui décide du prix : une valeur de plus élargit ce
    # que le modèle a le droit de CHOISIR, et le coût mesuré plus haut (`pcass`
    # au lieu de `pclass`, la prédiction remplacée par une relance) est celui
    # d'un choix élargi. Un champ facultatif, lui, n'enlève rien aux autres.
    #
    # **Le prix a été mesuré AVANT d'être payé**, et il est nul. Planificateur
    # seul, catalogue métier, trois tirages, quinze messages, le contrat
    # d'aujourd'hui contre celui-ci : les huit questions sur les données gardent
    # leur capacité ET leur source au tirage près, la prédiction reste
    # `predict` avec ses sept features, et « ventes ou production ? » cesse
    # même d'empaqueter deux noms une fois sur trois. Le relevé est dans
    # docs/croisement-de-sources.md.
    #
    # **Il porte ce que `source` ne peut pas porter.** Le planificateur écrivait
    # déjà `source='ventes, production'` — deux noms empaquetés dans un champ
    # qui en attend un — et C54 a bâti le croisement sur cet empaquetage. Il
    # tient sur deux questions et lâche sur trois autres : le modèle choisit
    # alors UN nom, et rien ne dit qu'il en avait lu deux. Le champ ne remplace
    # pas l'empaquetage, il s'y AJOUTE — `_perimetre_croise` lit l'union des
    # deux, et c'est cette union, et non l'un ou l'autre, qui rend cinq
    # questions sur cinq.
    #
    # **Et il départage ce qu'aucun décompte de mots ne départageait.** « titanic
    # et iris, c'est quoi au juste ? » laisse ce champ VIDE 3 tirages sur 3 —
    # c'est une question sur ce que SONT ces sources ; « compare la production
    # et les ventes du VEL-04 » le remplit 3 sur 3 — c'est une question sur
    # leurs DONNÉES. Les deux nomment deux sources et disent plus que leurs
    # noms : aucune propriété du message ne les séparait, et celle-ci ne se lit
    # pas dans les noms cités mais dans ce que la question RÉCLAME.
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "Les sources à interroger ENSEMBLE quand la demande confronte des "
            "données de plusieurs d'entre elles. Vide quand une seule suffit."
        ),
    )
    dataset: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    data_question: str | None = None  # fetch_then_predict : quoi récupérer
    reason: str = ""

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Le JSON Schema de sortie, SANS la description tirée de la docstring.

        **Pydantic promeut silencieusement ``__doc__`` en ``description`` du
        schéma.** Une phrase écrite pour qui lit le code devient donc une phrase
        que le MODÈLE lit, sans que personne l'ait décidé — et elle ne parle pas
        au modèle, elle parle du module.

        **Ce que cette phrase-là coûtait, mesuré le 2026-09-22**, catalogue
        métier, six tirages par variante, deux contrats identiques au mot près
        sauf celle-ci :

        | description du modèle | ce que rend le plan |
        |---|---|
        | « …paramètres extraits de la question. » | `source='production'`, `sources=[]` — **6/6** |
        | (aucune) | `source=None`, `sources=['production','ventes']` — **6/6** |

        (sur « compare la production et les ventes du VEL-04 » ; la première
        ligne est la docstring de cette classe, au mot près.)

        Six sur six dans chaque sens, sur la même question, le même prompt et le
        même modèle. Le titre du schéma, lui, ne change rien : `Plan` et `PlanB`
        se comportent pareil à description égale, ce qui isole la cause.
        « paramètres extraits de la question » énonce le champ au SINGULIER, et
        le modèle le suit contre la description du champ ``sources``, qui dit
        l'inverse deux lignes plus bas.

        **On retire plutôt qu'on réécrit.** Une troisième rédaction — « une
        source, ou un périmètre qui en croise plusieurs » — a été mesurée aussi :
        elle rend deux questions sur trois et en perd une que le retrait garde.
        Réécrire, c'est chercher la phrase qui plaît au modèle du jour ; retirer,
        c'est lui rendre le champ tel qu'il est déclaré. La description du CHAMP
        reste, elle, et elle est mesurée comme payante : sans elle, trois
        questions de croisement sur huit perdent leur périmètre.

        La docstring reste écrite pour qui lit le code. C'est le seul endroit du
        dépôt où une documentation est explicitement coupée du schéma, et elle
        le dit.
        """
        schema = handler(core_schema)
        schema = handler.resolve_ref_schema(schema)
        schema.pop("description", None)
        return schema


def planner_system_prompt(
    sources_description: str,
    datasets_description: str,
    pending_context: str | None = None,
    history_context: str | None = None,
    source_context: str | None = None,
) -> str:
    """Compose le prompt système du planificateur.

    ``pending_context`` (multi-tours) : décrit une prédiction en attente de
    features — le message courant est probablement un complément d'information.
    ``history_context`` : décrit le tour précédent (question + action) pour
    qu'un ajustement (« mets des couleurs plus vives ») soit rattaché à lui.
    ``source_context`` : nomme la source de travail validée par l'utilisateur,
    pour que le planificateur n'ait plus à la deviner quand elle est connue.

    Le **gabarit ne bouge pas** : ces contextes sont ajoutés à la suite, comme
    des faits de conversation. C'est la propriété qui rend le chemin des
    questions sur les données insensible à ce qu'on ajoute ici — cf. la mesure
    du coût d'une cinquième valeur de ``Capability``, plus haut.

    Composé à part de l'agent parce que l'orchestrateur doit pouvoir le **peser
    avant de l'envoyer** : un budget de tokens se décompte sur le prompt réel,
    pas sur une estimation de ce qu'il contiendra.
    """
    system_prompt = prompts.render(
        prompts.PLANNER, sources=sources_description, datasets=datasets_description
    )
    for extra in (history_context, source_context, pending_context):
        if extra:
            system_prompt = f"{system_prompt}\n{extra}"
    return system_prompt


def planner_agent(system_prompt: str) -> Agent[None, Plan]:
    """Agent planificateur à sortie structurée Plan, pour un prompt déjà composé."""
    return Agent(output_type=Plan, system_prompt=system_prompt)
