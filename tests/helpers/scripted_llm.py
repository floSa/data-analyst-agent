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
from data_analyst_agent.orchestrator import introspection
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
SYSTEME = prompts.marqueur(prompts.SYSTEME)
RAPPEL = prompts.marqueur(prompts.RAPPEL)
REPARATION = prompts.marqueur(prompts.REPARATION)


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

    **L'agent système décline par défaut**, et c'est ce qui garde les tests des
    autres capacités lisibles. Il est en tête du graphe : il reçoit désormais
    CHAQUE question, y compris celles sur les données. Exiger de chaque test
    qu'il script un « non merci » aurait ajouté une ligne de bruit à cent
    trente tests dont le sujet n'est pas là — et aurait dit le contraire de ce
    qu'on veut vérifier, à savoir que ce nœud est transparent pour une question
    sur les données. Un test qui s'intéresse à ce chemin script ``SYSTEME``
    explicitement, et reprend alors la main entière (script épuisé = échec).

    ``prompts_for(SYSTEME)`` compte les refus comme les autres passages : le
    coût du nœud reste observable.

    **L'agent de RAPPEL décline lui aussi par défaut**, et pour la même raison.
    Il n'est sollicité que dans un fil qui a déjà produit un artefact — mais
    une bonne partie des tests en fabriquent un pour poser leur décor (un
    tableau mémorisé au tour précédent), sans que le rappel soit leur sujet.
    Un test qui s'intéresse à ce chemin script ``RAPPEL`` explicitement.

    **Le tour de RÉPARATION décline aussi**, et ce défaut-là dit quelque chose.
    Il n'a lieu que sur un tour dont la ceinture a écarté la formulation, et son
    refus vaut « la seconde formulation n'est pas fondée non plus » : le repli
    part, donc un test de ceinture écrit avant ce chemin-ci vérifie toujours
    exactement ce qu'il vérifiait. Un test qui s'intéresse à la seconde chance
    script ``REPARATION`` explicitement — et ``prompts_for(REPARATION)`` compte
    les refus, donc le coût du tour reste observable.
    """

    # Une réponse texte sans aucun appel d'outil : le signal « cette question
    # n'est pas pour moi » (cf. `orchestrator/systeme.py` — c'est l'absence
    # d'appel d'outil qui route, pas ce mot).
    REFUS_DU_SYSTEME = introspection.SENTINELLE_HORS_SUJET

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
                    # Un agent donne son prompt de DEUX façons, et le routage
                    # doit reconnaître les deux : en `system_prompt` (une part
                    # du message) ou en `instructions` (un champ du message,
                    # réémis à chaque requête). L'agent système est passé aux
                    # secondes le jour où il a reçu le tour d'avant en
                    # historique — `pydantic-ai` n'émet un `system_prompt` que
                    # sur un historique VIDE. Sans cette ligne, ce n'est pas un
                    # test qui tombe, c'est chaque test qui traverse ce nœud,
                    # sur « aucun script pour le prompt système : '' ».
                    system = message.instructions or system
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
            for marqueur in (SYSTEME, RAPPEL, REPARATION):
                if marqueur in system:
                    self.captured.append((marqueur, system, last_user))
                    return text(self.REFUS_DU_SYSTEME)
            raise AssertionError(f"aucun script pour le prompt système : {system[:120]!r}")

        return FunctionModel(responder)
