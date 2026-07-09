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
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
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
