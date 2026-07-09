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

from data_analyst_agent.orchestrator.plan import Plan

# Marqueurs stables des prompts système de chaque agent
PLANNER = "planificateur"
RETRIEVAL = "expert SQL"
ANALYSIS = "data analyst Python"
SYNTHESIS = "réponse finale"


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content)])


def tool_call(tool_name: str, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name, args)])


def plan_response(plan: Plan) -> ModelResponse:
    """Réponse structurée du planificateur (tool de sortie de PydanticAI)."""
    return tool_call("final_result", plan.model_dump())


class ScriptedLLM:
    """File de réponses par agent (marqueur du prompt système)."""

    def __init__(self) -> None:
        self._queues: dict[str, list[ModelResponse]] = {}

    def script(self, marker: str, responses: list[ModelResponse]) -> ScriptedLLM:
        self._queues.setdefault(marker, []).extend(responses)
        return self

    def model(self) -> FunctionModel:
        def responder(messages, info):
            system = ""
            for message in messages:
                if isinstance(message, ModelRequest):
                    for part in message.parts:
                        if isinstance(part, SystemPromptPart):
                            system = part.content
            for marker, queue in self._queues.items():
                if marker in system:
                    if not queue:
                        raise AssertionError(f"script épuisé pour l'agent {marker!r}")
                    return queue.pop(0)
            raise AssertionError(f"aucun script pour le prompt système : {system[:120]!r}")

        return FunctionModel(responder)
