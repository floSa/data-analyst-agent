"""Agent text-to-SQL scripté (FunctionModel + ToolCallPart) sur DuckDB en mémoire.

DuckDB est in-process : ces tests exercent le VRAI chemin tools -> SQL sans
Docker ni réseau, y compris la self-correction après une erreur SQL.
"""

from pathlib import Path

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.retrieval.agent import run_retrieval
from data_analyst_agent.agents.retrieval.duckdb_source import DuckDBAdapter
from data_analyst_agent.config import Settings


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def adapter(tmp_path: Path) -> DuckDBAdapter:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return DuckDBAdapter.from_file(csv)


def scripted_model(steps: list[list]) -> FunctionModel:
    """Rejoue une séquence de réponses modèle (parts) dans l'ordre."""
    remaining = [ModelResponse(parts=parts) for parts in steps]

    def responder(messages, info):
        return remaining.pop(0)

    return FunctionModel(responder)


def test_flux_nominal_schema_puis_sql(adapter):
    model = scripted_model(
        [
            [ToolCallPart("get_schema", {})],
            [ToolCallPart("run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE sexe = 'f'"})],
            [TextPart("Il y a 3 femmes dans la table.")],
        ]
    )
    outcome = run_retrieval(
        "Combien de femmes ?", adapter=adapter, model=model, settings=make_settings()
    )
    assert outcome.succeeded
    assert outcome.summary == "Il y a 3 femmes dans la table."
    assert outcome.result.rows == [[3]]
    assert "count(*)" in outcome.sql
    assert [q.ok for q in outcome.executed] == [True]


def test_self_correction_apres_erreur_sql(adapter):
    model = scripted_model(
        [
            [ToolCallPart("run_sql", {"query": "SELECT nexiste_pas FROM mini"})],
            [ToolCallPart("run_sql", {"query": "SELECT count(*) AS n FROM mini"})],
            [TextPart("4 lignes.")],
        ]
    )
    outcome = run_retrieval(
        "Combien de lignes ?", adapter=adapter, model=model, settings=make_settings()
    )
    assert outcome.succeeded
    assert [q.ok for q in outcome.executed] == [False, True]
    assert "nexiste_pas" in outcome.executed[0].error
    assert outcome.result.rows == [[4]]


def test_ecriture_bloquee_par_le_garde_fou(adapter):
    model = scripted_model(
        [
            [ToolCallPart("run_sql", {"query": "DROP VIEW mini"})],
            [TextPart("Je n'ai pas pu modifier la base.")],
        ]
    )
    outcome = run_retrieval(
        "Supprime la table.", adapter=adapter, model=model, settings=make_settings()
    )
    assert not outcome.succeeded
    assert outcome.executed[0].ok is False
    assert "interdit" in outcome.executed[0].error or "SELECT" in outcome.executed[0].error


def test_list_tables_disponible(adapter):
    model = scripted_model(
        [
            [ToolCallPart("list_tables", {})],
            [TextPart("La source contient la table mini.")],
        ]
    )
    outcome = run_retrieval(
        "Quelles tables ?", adapter=adapter, model=model, settings=make_settings()
    )
    assert "mini" in outcome.summary


# --- dictionnaire de la source ---------------------------------------------------


def _capture_system_prompt(adapter, **kwargs) -> str:
    """Rejoue un tour minimal et renvoie le prompt système vu par le modèle."""
    captured: list[str] = []

    def responder(messages, info):
        captured.extend(
            part.content
            for message in messages
            for part in message.parts
            if part.part_kind == "system-prompt"
        )
        return ModelResponse(parts=[TextPart("ok")])

    run_retrieval(
        "peu importe",
        adapter=adapter,
        model=FunctionModel(responder),
        settings=make_settings(),
        **kwargs,
    )
    return "\n".join(captured)


def test_dictionnaire_present_avant_la_question(adapter):
    """Les pièges doivent être connus AU MOMENT d'écrire le SQL, pas après.

    D'où le prompt système plutôt qu'une réponse de tool : un modèle qui
    apprendrait au 3e tour que le e-commerce est une ligne de `stores` a déjà
    rendu son classement des magasins.
    """
    prompt = _capture_system_prompt(
        adapter, dictionary="Piège n°1 : le e-commerce est le magasin ONLINE."
    )
    assert "Piège n°1 : le e-commerce est le magasin ONLINE." in prompt
    assert "Dictionnaire de la source" in prompt


def test_sans_dictionnaire_le_prompt_nen_parle_pas(adapter):
    """Une source sans dictionnaire ne doit pas hériter d'un en-tête vide."""
    prompt = _capture_system_prompt(adapter)
    assert "Dictionnaire de la source" not in prompt
    assert "expert SQL" in prompt


def test_contexte_conversationnel_transmis_a_lagent_sql(adapter):
    """L'anaphore « affiche ceux des autres années » ne se résout qu'avec le
    tour précédent SOUS LES YEUX de l'agent SQL — pas seulement du planificateur.

    Observé en vrai : après « quel est le CA total de 2025 ? », le tour « affiche
    ceux des autres années » recevait une phrase orpheline et l'agent répondait
    « demande trop vague » au lieu d'écrire la requête.
    """
    prompt = _capture_system_prompt(
        adapter,
        history="CONTEXTE CONVERSATIONNEL : au tour précédent, l'utilisateur a "
        "demandé « quel est le chiffre d'affaires total de 2025 ? » (action : query).",
    )
    assert "Contexte de la conversation" in prompt
    assert "chiffre d'affaires total de 2025" in prompt


def test_sans_historique_le_prompt_nen_parle_pas(adapter):
    prompt = _capture_system_prompt(adapter)
    assert "Contexte de la conversation" not in prompt


def test_boucle_infinie_coupee_par_la_limite(adapter):
    def responder_infini(messages, info):
        return ModelResponse(parts=[ToolCallPart("run_sql", {"query": "SELECT oops FROM mini"})])

    model = FunctionModel(responder_infini)
    with pytest.raises(UsageLimitExceeded):
        run_retrieval(
            "Question impossible",
            adapter=adapter,
            model=model,
            settings=make_settings(retrieval_request_limit=4),
        )
