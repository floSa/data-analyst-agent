"""Intégration Postgres réelle (testcontainers) : ontologie, jointures, agent.

Base Titanic multi-tables (passengers + classes, clé étrangère) seedée depuis
le CSV vendorisé ; la valeur golden est vérifiée contre l'oracle pandas.
"""

import shutil
import subprocess

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.retrieval.agent import run_retrieval
from data_analyst_agent.agents.retrieval.sql import PostgresAdapter, QueryError
from data_analyst_agent.config import Settings
from helpers.titanic import golden_survival_rate_female_first_class, seed_titanic_postgres


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_disponible(), reason="démon Docker indisponible"),
]

GOLDEN_SQL = """
SELECT round(100.0 * sum(p.survived) / count(*), 2) AS taux
FROM passengers p
JOIN classes c ON c.class_id = p.class_id
WHERE p.sex = 'female' AND c.level = 1
"""


@pytest.fixture(scope="module")
def adapter():
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="pg8000") as container:
        engine = create_engine(container.get_connection_url())
        seed_titanic_postgres(engine)
        yield PostgresAdapter(engine)
        engine.dispose()


def test_ontologie_tables_et_cle_etrangere(adapter: PostgresAdapter):
    schema = adapter.schema()
    assert schema.table_names() == ["classes", "passengers"]
    passengers = next(t for t in schema.tables if t.name == "passengers")
    assert passengers.primary_key == ["passenger_id"]
    fks = passengers.foreign_keys
    assert len(fks) == 1
    assert fks[0].ref_table == "classes"
    assert "FOREIGN KEY" in schema.to_prompt()


def test_jointure_golden_vs_oracle_pandas(adapter: PostgresAdapter):
    result = adapter.run(GOLDEN_SQL)
    assert result.columns == ["taux"]
    taux_sql = result.rows[0][0]
    assert taux_sql == pytest.approx(golden_survival_rate_female_first_class())
    assert taux_sql == pytest.approx(96.81)


def test_garde_fou_avant_la_base(adapter: PostgresAdapter):
    with pytest.raises(QueryError, match=r"interdit|SELECT"):
        adapter.run("DELETE FROM passengers")
    # la table est intacte
    assert adapter.run("SELECT count(*) FROM passengers").rows[0][0] == 891


def test_erreur_sql_reelle_remontee(adapter: PostgresAdapter):
    with pytest.raises(QueryError, match="nexiste_pas"):
        adapter.run("SELECT nexiste_pas FROM passengers")


def test_agent_scripte_self_correction_sur_vrai_postgres(adapter: PostgresAdapter):
    model_steps = [
        [ToolCallPart("get_schema", {})],
        # colonne volontairement fausse : l'erreur pg8000 doit revenir au modèle
        [ToolCallPart("run_sql", {"query": "SELECT taux FROM passengers WHERE sex = 'female'"})],
        [ToolCallPart("run_sql", {"query": GOLDEN_SQL.strip()})],
        [TextPart("96,81 % des femmes de 1re classe ont survécu.")],
    ]
    remaining = [ModelResponse(parts=parts) for parts in model_steps]

    def responder(messages, info):
        return remaining.pop(0)

    outcome = run_retrieval(
        "Quel pourcentage de femmes de 1re classe a survécu ?",
        adapter=adapter,
        model=FunctionModel(responder),
        settings=Settings(_env_file=None),
    )
    assert outcome.succeeded
    assert [q.ok for q in outcome.executed] == [False, True]
    assert outcome.result.rows[0][0] == pytest.approx(96.81)
    assert "96,81" in outcome.summary
