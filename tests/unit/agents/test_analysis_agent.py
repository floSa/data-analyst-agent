"""Tests de l'agent Analyse : LLM scripté (FunctionModel), sandbox doublée."""

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.analysis.agent import extract_code, run_analysis
from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import MimeOutput, SandboxResult


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- extract_code -----------------------------------------------------------


def test_extract_code_bloc_python():
    text = "Voici :\n```python\nprint(1)\n```\nfin"
    assert extract_code(text) == "print(1)"


def test_extract_code_bloc_anonyme():
    assert extract_code("```\nx = 2\n```") == "x = 2"


def test_extract_code_sans_bloc():
    assert extract_code("print(3)\n") == "print(3)"


def test_extract_code_prend_le_premier_bloc():
    text = "```python\na = 1\n```\n...\n```python\nb = 2\n```"
    assert extract_code(text) == "a = 1"


# --- doublures --------------------------------------------------------------


class ScriptedSandbox:
    """Sandbox doublée : rejoue une liste de résultats, enregistre les codes reçus."""

    def __init__(self, outcomes: list[SandboxResult]) -> None:
        self.outcomes = list(outcomes)
        self.executed: list[str] = []
        self.closed = False

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult:
        self.executed.append(code)
        return self.outcomes.pop(0)

    def close(self) -> None:
        self.closed = True


def scripted_model(responses: list[str]) -> FunctionModel:
    """Un modèle qui rejoue des réponses dans l'ordre, quel que soit le prompt."""
    remaining = list(responses)

    def responder(messages, info):
        return ModelResponse(parts=[TextPart(remaining.pop(0))])

    return FunctionModel(responder)


# --- run_analysis -----------------------------------------------------------

OK_PNG = SandboxResult(
    status="ok",
    stdout="42\n",
    results=[MimeOutput(mime="image/png", data="aW1hZ2U=")],
)
ERREUR = SandboxResult(status="error", error="NameError: name 'dff' is not defined")


def test_succes_du_premier_coup():
    sandbox = ScriptedSandbox([OK_PNG])
    result = run_analysis(
        "Combien ?",
        model=scripted_model(["```python\nprint(42)\n```"]),
        settings=make_settings(),
        sandbox=sandbox,
    )
    assert result.succeeded
    assert result.attempts == 1
    assert result.code == "print(42)"
    assert result.execution.stdout == "42\n"


def test_self_debug_corrige_puis_reussit():
    sandbox = ScriptedSandbox([ERREUR, OK_PNG])
    result = run_analysis(
        "Combien ?",
        model=scripted_model(["```python\nprint(dff)\n```", "```python\nprint(42)\n```"]),
        settings=make_settings(),
        sandbox=sandbox,
    )
    assert result.succeeded
    assert result.attempts == 2
    assert sandbox.executed == ["print(dff)", "print(42)"]


def test_epuisement_des_essais():
    sandbox = ScriptedSandbox([ERREUR, ERREUR, ERREUR])
    result = run_analysis(
        "Combien ?",
        model=scripted_model(["```python\nboom\n```"] * 3),
        settings=make_settings(analysis_max_attempts=3),
        sandbox=sandbox,
    )
    assert not result.succeeded
    assert result.attempts == 3
    assert "NameError" in result.execution.error


def test_sandbox_fournie_jamais_fermee():
    sandbox = ScriptedSandbox([OK_PNG])
    run_analysis(
        "Q",
        model=scripted_model(["```python\nprint(1)\n```"]),
        settings=make_settings(),
        sandbox=sandbox,
    )
    assert sandbox.closed is False


def test_le_prompt_contient_fichiers_et_contexte():
    captured: list[str] = []

    def responder(messages, info):
        # premier message utilisateur de la requête
        captured.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart("```python\nprint(1)\n```")])

    from pathlib import Path

    sandbox = ScriptedSandbox([OK_PNG])
    run_analysis(
        "Quelle moyenne ?",
        data_files={Path("/tmp/titanic.csv"): "titanic.csv"},
        data_context="colonnes : age, sex, survived",
        model=FunctionModel(responder),
        settings=make_settings(),
        sandbox=sandbox,
    )
    prompt = captured[0]
    assert "/data/titanic.csv" in prompt
    assert "colonnes : age, sex, survived" in prompt
    assert "Quelle moyenne ?" in prompt
