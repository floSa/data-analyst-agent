"""Intégration Postgres réelle (testcontainers) : ontologie, jointures, agent.

Le schéma en étoile Maxizoo (10 tables, 9 clés étrangères) seedé depuis
l'échantillon versionné ; la valeur golden est vérifiée contre l'oracle pandas.

Ce que ces tests couvrent et que DuckDB ne couvre pas : `PostgresAdapter` face à
un VRAI serveur — introspection SQLAlchemy, remontée d'erreur pg8000, garde-fou
lecture seule sur une base qui, elle, accepterait le DELETE.
"""

import shutil
import subprocess

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.retrieval.agent import run_retrieval
from data_analyst_agent.agents.retrieval.sql import PostgresAdapter, QueryError
from data_analyst_agent.config import Settings
from helpers.maxizoo import golden_ca_2025_par_magasin, seed_maxizoo_postgres


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_disponible(), reason="démon Docker indisponible"),
]

# Le CA par magasin : la question qui porte le piège n°1 du dictionnaire — le
# canal e-commerce est une LIGNE de `stores`, et il sort premier.
GOLDEN_SQL = """
SELECT st.store_name, round(sum(s.revenue), 2) AS ca
FROM sales_daily s
JOIN stores st ON st.store_id = s.store_id
GROUP BY st.store_name
ORDER BY ca DESC
"""


@pytest.fixture(scope="module")
def adapter():
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="pg8000") as container:
        engine = create_engine(container.get_connection_url())
        seed_maxizoo_postgres(engine)
        yield PostgresAdapter(engine)
        engine.dispose()


def test_ontologie_tables_et_cles_etrangeres(adapter: PostgresAdapter):
    schema = adapter.schema()
    assert "sales_daily" in schema.table_names()
    ventes = next(t for t in schema.tables if t.name == "sales_daily")
    assert ventes.primary_key == ["date", "store_id", "sku_id"]
    # La table de faits est au centre de l'étoile : sans ses FK, le modèle doit
    # deviner comment joindre magasins, produits et campagnes.
    assert {(fk.column, fk.ref_table) for fk in ventes.foreign_keys} == {
        ("store_id", "stores"),
        ("sku_id", "products"),
        ("promo_id", "promo_calendar"),
    }
    assert "FOREIGN KEY" in schema.to_prompt()


def test_valeurs_a_faible_cardinalite_exposees(adapter: PostgresAdapter):
    """`store_id` a 3 valeurs dans l'échantillon : les montrer évite au modèle de les inventer."""
    magasins = next(t for t in adapter.schema().tables if t.name == "stores")
    identifiants = next(c for c in magasins.columns if c.name == "store_id")
    assert identifiants.values == ["ONLINE", "S01", "S12"]


def test_jointure_golden_vs_oracle_pandas(adapter: PostgresAdapter):
    result = adapter.run(GOLDEN_SQL)
    assert result.columns == ["store_name", "ca"]
    obtenu = [(nom, float(ca)) for nom, ca in result.rows]
    assert obtenu == pytest.approx(golden_ca_2025_par_magasin())
    # le e-commerce est en tête : c'est un magasin comme un autre (piège n°1)
    assert obtenu[0][0] == "Canal Online"


def test_garde_fou_avant_la_base(adapter: PostgresAdapter):
    with pytest.raises(QueryError, match=r"interdit|SELECT"):
        adapter.run("DELETE FROM sales_daily")
    # la table est intacte
    assert adapter.run("SELECT count(*) FROM sales_daily").rows[0][0] == 3666


def test_erreur_sql_reelle_remontee(adapter: PostgresAdapter):
    with pytest.raises(QueryError, match="nexiste_pas"):
        adapter.run("SELECT nexiste_pas FROM sales_daily")


def test_agent_scripte_self_correction_sur_vrai_postgres(adapter: PostgresAdapter):
    model_steps = [
        [ToolCallPart("get_schema", {})],
        # colonne volontairement fausse : l'erreur pg8000 doit revenir au modèle
        [ToolCallPart("run_sql", {"query": "SELECT chiffre_affaires FROM sales_daily"})],
        [ToolCallPart("run_sql", {"query": GOLDEN_SQL.strip()})],
        [TextPart("Le canal en ligne réalise le plus gros chiffre d'affaires.")],
    ]
    remaining = [ModelResponse(parts=parts) for parts in model_steps]

    def responder(messages, info):
        return remaining.pop(0)

    outcome = run_retrieval(
        "Quel magasin réalise le plus gros chiffre d'affaires ?",
        adapter=adapter,
        model=FunctionModel(responder),
        settings=Settings(_env_file=None),
    )
    assert outcome.succeeded
    assert [q.ok for q in outcome.executed] == [False, True]
    assert outcome.result.rows[0][0] == "Canal Online"
    assert "en ligne" in outcome.summary
