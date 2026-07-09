"""Capacité ② — Analyse : le LLM génère du code stats/viz, la sandbox l'exécute.

Boucle self-debug : si l'exécution échoue, l'erreur est renvoyée au modèle qui
corrige son code, jusqu'à ``analysis_max_attempts`` essais (CADRAGE §7-②).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.sandbox.client import SandboxResult, SandboxSession

SYSTEM_PROMPT = """\
Tu es un data analyst Python. Tu écris du code exécuté dans un kernel Jupyter,
au sein d'une sandbox SANS accès réseau.

Bibliothèques disponibles : pandas, numpy, scipy, statsmodels, prince,
scikit-learn, matplotlib, plotly, duckdb, openpyxl. Rien d'autre n'est
installable.

Les fichiers de données sont montés en LECTURE SEULE sous /data/.

Règles impératives :
1. Réponds UNIQUEMENT par un bloc de code Python (```python ... ```), aucune
   explication hors du bloc.
2. Termine par des print(...) explicites des valeurs demandées (arrondis
   raisonnables).
3. Pour une figure : matplotlib, puis plt.show().
4. N'écris jamais sur le disque en dehors de /tmp ; ne tente aucun accès
   réseau ; n'installe rien.
"""

CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


class SandboxLike(Protocol):
    """Le strict nécessaire d'une session sandbox (facilite les doublures de test)."""

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult: ...


class AnalysisResult(BaseModel):
    """Issue d'une analyse : dernier code tenté et son exécution."""

    code: str
    execution: SandboxResult
    attempts: int = Field(ge=1)

    @property
    def succeeded(self) -> bool:
        return self.execution.status == "ok"


def extract_code(text: str) -> str:
    """Extrait le premier bloc ``` du texte ; à défaut, le texte brut."""
    match = CODE_FENCE_RE.search(text)
    return (match.group(1) if match else text).strip()


def build_analysis_agent(model: Model) -> Agent:
    return Agent(model, system_prompt=SYSTEM_PROMPT)


def _initial_prompt(question: str, data_context: str, mounted: list[str]) -> str:
    parts = []
    if mounted:
        files = "\n".join(f"- /data/{name}" for name in mounted)
        parts.append(f"Fichiers de données disponibles :\n{files}")
    if data_context:
        parts.append(f"Contexte sur les données :\n{data_context}")
    parts.append(f"Question : {question}")
    return "\n\n".join(parts)


def run_analysis(
    question: str,
    *,
    data_files: dict[Path, str] | None = None,
    data_context: str = "",
    model: Model | None = None,
    settings: Settings | None = None,
    sandbox: SandboxLike | None = None,
) -> AnalysisResult:
    """Génère puis exécute du code d'analyse, avec self-debug sur erreur.

    Une sandbox fournie n'est pas fermée par cette fonction ; sinon une session
    éphémère est créée avec ``data_files`` montés en lecture seule.
    """
    settings = settings or get_settings()
    model = model or build_model(settings)
    agent = build_analysis_agent(model)

    own_session: SandboxSession | None = None
    if sandbox is None:
        own_session = SandboxSession(settings=settings, mounts=data_files)
        own_session.start()
        sandbox = own_session
    try:
        prompt = _initial_prompt(question, data_context, list((data_files or {}).values()))
        message_history = None
        code = ""
        execution = SandboxResult(status="error", error="aucun essai effectué")
        for attempt in range(1, settings.analysis_max_attempts + 1):
            run = agent.run_sync(prompt, message_history=message_history)
            code = extract_code(run.output)
            execution = sandbox.execute(code)
            if execution.status == "ok":
                return AnalysisResult(code=code, execution=execution, attempts=attempt)
            message_history = run.all_messages()
            prompt = (
                f"L'exécution a échoué (statut : {execution.status}).\n"
                f"Erreur :\n{execution.error}\n\n"
                "Corrige le problème et renvoie le code COMPLET corrigé."
            )
        return AnalysisResult(
            code=code, execution=execution, attempts=settings.analysis_max_attempts
        )
    finally:
        if own_session is not None:
            own_session.close()
