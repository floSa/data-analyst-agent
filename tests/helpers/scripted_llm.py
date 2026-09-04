"""LLM scripté multi-agents : route les réponses selon le prompt système.

L'orchestrateur mutualise UN modèle entre plusieurs agents (planificateur,
récupération, analyse, synthèse). Pour des tests déterministes, chaque agent
est identifié par un marqueur de son prompt système et reçoit sa propre file
de réponses.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent import prompts
from data_analyst_agent.orchestrator.plan import Plan

# Marqueurs des prompts système, DÉRIVÉS des prompts eux-mêmes.
#
# Ils étaient recopiés à la main ici — « planificateur », « expert SQL »,
# « data analyst Python », « réponse finale » — ce qui faisait de la
# formulation des prompts un contrat de test invisible : reformuler une phrase
# d'accroche faisait tomber toute la suite sur « aucun script pour le prompt
# système », sans que rien ne dise pourquoi (audit §5.3). Le fichier de prompt
# est désormais la seule source ; `prompts.marqueur` en tire sa première ligne.
PLANNER = prompts.marqueur(prompts.PLANNER)
RETRIEVAL = prompts.marqueur(prompts.RETRIEVAL)
ANALYSIS = prompts.marqueur(prompts.ANALYSIS)
SYNTHESIS = prompts.marqueur(prompts.SYNTHESIS)


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content)])


def tool_call(tool_name: str, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name, args)])


def plan_response(plan: Plan) -> ModelResponse:
    """Réponse structurée du planificateur (tool de sortie de PydanticAI)."""
    return tool_call("final_result", plan.model_dump())


class ScriptedLLM:
    """File de réponses par agent (marqueur du prompt système).

    ``prompts_for(marker)`` rejoue ce que chaque agent a reçu (utile pour
    vérifier le contenu des prompts construits par l'orchestrateur).
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[ModelResponse]] = {}
        # (marqueur, prompt système, dernier contenu utilisateur)
        self.captured: list[tuple[str, str, str]] = []

    def script(self, marker: str, responses: list[ModelResponse]) -> ScriptedLLM:
        self._queues.setdefault(marker, []).extend(responses)
        return self

    def prompts_for(self, marker: str) -> list[str]:
        return [user for m, _system, user in self.captured if m == marker]

    def systems_for(self, marker: str) -> list[str]:
        return [system for m, system, _user in self.captured if m == marker]

    def model(self) -> FunctionModel:
        def responder(messages, info):
            system = ""
            last_user = ""
            for message in messages:
                if isinstance(message, ModelRequest):
                    for part in message.parts:
                        if isinstance(part, SystemPromptPart):
                            system = part.content
                        elif hasattr(part, "content") and isinstance(part.content, str):
                            last_user = part.content
            for marker, queue in self._queues.items():
                if marker in system:
                    if not queue:
                        raise AssertionError(f"script épuisé pour l'agent {marker!r}")
                    self.captured.append((marker, system, last_user))
                    return queue.pop(0)
            raise AssertionError(f"aucun script pour le prompt système : {system[:120]!r}")

        return FunctionModel(responder)
