"""Garde-fou lecture seule + modèles d'ontologie et de résultats."""

import datetime as dt
import decimal

import pytest

from data_analyst_agent.agents.retrieval.sql import (
    ColumnInfo,
    ForeignKeyInfo,
    QueryError,
    QueryResult,
    SchemaInfo,
    TableInfo,
    assert_read_only,
    build_result,
    normalize_value,
)

# --- assert_read_only --------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM t",
        "select count(*) from t where x = 1",
        "WITH s AS (SELECT 1 AS a) SELECT a FROM s",
        "SELECT * FROM t;",  # point-virgule final toléré
    ],
)
def test_requetes_lecture_acceptees(query):
    assert_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE pwn (x int)",
        "TRUNCATE t",
        "GRANT ALL ON t TO public",
        "SELECT * INTO pwn FROM t",
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
        "SELECT 1; DROP TABLE t",
        "",
    ],
)
def test_requetes_ecriture_refusees(query):
    with pytest.raises(QueryError):
        assert_read_only(query)


def test_nom_de_colonne_contenant_un_mot_cle_accepte():
    # "created_at" contient "create" mais n'est pas le mot-clé isolé
    assert_read_only("SELECT created_at, updated_by FROM t")


# --- normalisation et résultats ------------------------------------------------


def test_normalize_value():
    assert normalize_value(decimal.Decimal("3.14")) == pytest.approx(3.14)
    assert normalize_value(dt.date(2026, 7, 9)) == "2026-07-09"
    assert normalize_value(None) is None
    assert normalize_value("x") == "x"
    assert normalize_value(b"blob") == "b'blob'"


def test_normalize_value_nan_et_inf_deviennent_null():
    # NaN/inf casseraient le JSON strict côté client (JSON.parse)
    assert normalize_value(float("nan")) is None
    assert normalize_value(float("inf")) is None
    assert normalize_value(float("-inf")) is None


def test_build_result_tronque():
    rows = [[i] for i in range(10)]
    result = build_result(["n"], rows, max_rows=3)
    assert result.truncated is True
    assert result.row_count == 3


def test_to_markdown():
    result = QueryResult(columns=["a", "b"], rows=[[1, "x"], [2, "y"]])
    markdown = result.to_markdown()
    assert "| a | b |" in markdown
    assert "| 1 | x |" in markdown


# --- ontologie -----------------------------------------------------------------


def test_to_ddl_et_prompt():
    table = TableInfo(
        name="passengers",
        columns=[
            ColumnInfo(name="passenger_id", type="INTEGER", nullable=False),
            ColumnInfo(name="class_id", type="INTEGER", nullable=False),
        ],
        primary_key=["passenger_id"],
        foreign_keys=[
            ForeignKeyInfo(column="class_id", ref_table="classes", ref_column="class_id")
        ],
    )
    schema = SchemaInfo(tables=[table])
    prompt = schema.to_prompt()
    assert "TABLE passengers" in prompt
    assert "PRIMARY KEY" in prompt
    assert "FOREIGN KEY (class_id) REFERENCES classes(class_id)" in prompt
    assert schema.table_names() == ["passengers"]


def test_ddl_expose_les_valeurs_des_colonnes_a_faible_cardinalite():
    """Le modèle doit VOIR les littéraux : sinon il les devine dans sa langue
    (« LIKE '%First%' » sur des libellés « 1re classe » → zéro ligne)."""
    table = TableInfo(
        name="classes",
        columns=[
            ColumnInfo(name="class_id", type="INTEGER", nullable=False),
            ColumnInfo(name="label", type="TEXT", values=["1re classe", "2e classe"]),
        ],
        primary_key=["class_id"],
    )
    ddl = SchemaInfo(tables=[table]).to_prompt()

    assert "-- valeurs : '1re classe', '2e classe'" in ddl
    assert "class_id INTEGER NOT NULL PRIMARY KEY," in ddl  # virgule conservée


def test_ddl_sans_valeurs_reste_inchange():
    """Une colonne à forte cardinalité (un nom) n'encombre pas le prompt."""
    table = TableInfo(name="t", columns=[ColumnInfo(name="name", type="TEXT")])
    ddl = SchemaInfo(tables=[table]).to_prompt()

    assert "-- valeurs" not in ddl
    assert ddl == "TABLE t (\n  name TEXT\n)"


def test_ddl_derniere_colonne_sans_virgule_finale():
    """Régression : le commentaire de valeurs ne doit pas laisser une virgule pendante."""
    table = TableInfo(
        name="t",
        columns=[
            ColumnInfo(name="a", type="TEXT"),
            ColumnInfo(name="b", type="TEXT", values=["x", "y"]),
        ],
    )
    ddl = SchemaInfo(tables=[table]).to_prompt()

    assert ddl == "TABLE t (\n  a TEXT,\n  b TEXT  -- valeurs : 'x', 'y'\n)"
