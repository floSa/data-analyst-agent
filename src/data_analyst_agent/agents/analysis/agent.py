"""Capacité ② — Analyse : le LLM génère du code stats/viz, la sandbox l'exécute.

Boucle self-debug : si l'exécution échoue, l'erreur est renvoyée au modèle qui
corrige son code, jusqu'à ``analysis_max_attempts`` essais (CADRAGE §7-②).

Le prompt système porte aussi le DICTIONNAIRE de la source quand elle en déclare
un (cf. `agents/dictionnaire`). C'est le même mécanisme que pour l'agent SQL, et
c'est ici qu'il manquait le plus : le bac à sable ne rend ni erreur ni trace
quand le code moyenne une valeur sentinelle, il rend un nombre — ou une courbe,
que personne ne relit. Mesuré sur `telemetrie` avant de le poser : 0 fois sur 5
le code écartait `puissance_kw = -1`, et la figure sortait deux kilowatts trop
bas sans que rien ne le signale.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from data_analyst_agent import prompts
from data_analyst_agent.agents.dictionnaire import (
    EN_TETE_CODE,
    DictionnaireInjecte,
    bloc_de_prompt,
    preparer,
)
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.sandbox.client import SandboxResult, SandboxSession

CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


class SandboxLike(Protocol):
    """Le strict nécessaire d'une session sandbox (facilite les doublures de test)."""

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult: ...


class AnalysisResult(BaseModel):
    """Issue d'une analyse : dernier code tenté et son exécution."""

    code: str
    execution: SandboxResult
    attempts: int = Field(ge=1)
    # Ce qui a été coupé du dictionnaire faute de budget ("" = rien). Remonte
    # jusqu'à l'utilisateur par la trace : un code écrit sans la section qui
    # porte la règle rend un chiffre faux, et il le rend sans bruit.
    dictionary_notice: str = ""

    @property
    def succeeded(self) -> bool:
        return self.execution.status == "ok"


def extract_code(text: str) -> str:
    """Extrait le premier bloc ``` du texte ; à défaut, le texte brut."""
    match = CODE_FENCE_RE.search(text)
    return (match.group(1) if match else text).strip()


def build_analysis_agent(model: Model, dictionnaire: DictionnaireInjecte | None = None) -> Agent:
    return Agent(model, system_prompt=composer_le_prompt(dictionnaire))


def composer_le_prompt(dictionnaire: DictionnaireInjecte | None) -> str:
    """Le prompt système de l'agent d'analyse : les consignes, puis le dictionnaire.

    Dans le prompt SYSTÈME et non dans le message du tour, contrairement au
    schéma et à la liste des fichiers montés : ce prompt-là est le seul morceau
    que la boucle de correction renvoie INTACT à chaque essai. Le message, lui,
    est remplacé dès le deuxième tour par la trace d'erreur — un dictionnaire
    qu'on y aurait mis aurait disparu exactement quand le modèle réécrit son
    code, c'est-à-dire au moment où il peut encore corriger son filtre.

    Vide quand la source ne déclare rien : le prompt est alors, au caractère
    près, celui d'avant.
    """
    base = prompts.gabarit(prompts.ANALYSIS)
    bloc = bloc_de_prompt(dictionnaire, EN_TETE_CODE) if dictionnaire is not None else ""
    return f"{base}\n\n{bloc}" if bloc else base


def _initial_prompt(
    question: str, data_context: str, mounted: list[str], previous_code: str | None = None
) -> str:
    parts = []
    if mounted:
        files = "\n".join(f"- /data/{name}" for name in mounted)
        parts.append(f"Fichiers de données disponibles :\n{files}")
    if data_context:
        parts.append(f"Contexte sur les données :\n{data_context}")
    if previous_code:
        parts.append(
            "Un graphique/analyse a déjà été produit au tour précédent par ce code. Si "
            "la question est un AJUSTEMENT (couleurs, type de graphique, titre…), PARS de "
            "ce code et modifie-le ; sinon écris un nouveau code.\n"
            f"```python\n{previous_code}\n```"
        )
    parts.append(f"Question : {question}")
    return "\n\n".join(parts)


def run_analysis(
    question: str,
    *,
    data_files: dict[Path, str] | None = None,
    data_context: str = "",
    previous_code: str | None = None,
    model: Model | None = None,
    settings: Settings | None = None,
    sandbox: SandboxLike | None = None,
    dictionary: str | None = None,
) -> AnalysisResult:
    """Génère puis exécute du code d'analyse, avec self-debug sur erreur.

    Une sandbox fournie n'est pas fermée par cette fonction ; sinon une session
    éphémère est créée avec ``data_files`` montés en lecture seule.

    ``dictionary`` est le Markdown déclaré par la source (``dictionary_text()``).
    ``None`` = la source n'en déclare pas. Il est taillé au budget ICI et non
    chez l'appelant, comme dans ``run_retrieval`` : le plafond est un réglage du
    dictionnaire, pas de l'appel, et un appelant qui l'oublierait enverrait un
    prompt sans plafond sans s'en apercevoir.
    """
    settings = settings or get_settings()
    model = model or build_model(settings)
    dictionnaire = preparer(dictionary, settings.dictionary_max_chars)
    agent = build_analysis_agent(model, dictionnaire)

    own_session: SandboxSession | None = None
    if sandbox is None:
        own_session = SandboxSession(settings=settings, mounts=data_files)
        own_session.start()
        sandbox = own_session
    try:
        prompt = _initial_prompt(
            question, data_context, list((data_files or {}).values()), previous_code
        )
        message_history = None
        code = ""
        execution = SandboxResult(status="error", error="aucun essai effectué")
        for attempt in range(1, settings.analysis_max_attempts + 1):
            run = agent.run_sync(prompt, message_history=message_history)
            code = extract_code(run.output)
            execution = sandbox.execute(code)
            if execution.status == "ok":
                return AnalysisResult(
                    code=code,
                    execution=execution,
                    attempts=attempt,
                    dictionary_notice=dictionnaire.avis,
                )
            message_history = run.all_messages()
            prompt = (
                f"L'exécution a échoué (statut : {execution.status}).\n"
                f"Erreur :\n{execution.error}\n\n"
                "Corrige le problème et renvoie le code COMPLET corrigé."
            )
        return AnalysisResult(
            code=code,
            execution=execution,
            attempts=settings.analysis_max_attempts,
            dictionary_notice=dictionnaire.avis,
        )
    finally:
        if own_session is not None:
            own_session.close()
