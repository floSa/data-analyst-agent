"""DuckDB sur fichiers : CSV natif, Excel multi-feuilles via pandas/openpyxl."""

from pathlib import Path

import pandas as pd
import pytest

from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter, sanitize_table_name
from data_analyst_agent.agents.retrieval.sql import QueryError


@pytest.fixture
def csv_ventes(tmp_path: Path) -> Path:
    fichier = tmp_path / "ventes.csv"
    fichier.write_text(
        "region,montant\nnord,100\nsud,200\nnord,50\n",
        encoding="utf-8",
    )
    return fichier


@pytest.fixture
def xlsx_multi(tmp_path: Path) -> Path:
    fichier = tmp_path / "gestion.xlsx"
    with pd.ExcelWriter(fichier, engine="openpyxl") as writer:
        pd.DataFrame({"id": [1, 2], "nom": ["Alice", "Bob"]}).to_excel(
            writer, sheet_name="Employés", index=False
        )
        pd.DataFrame({"employe_id": [1, 1, 2], "montant": [10.0, 20.0, 5.5]}).to_excel(
            writer, sheet_name="Notes de frais", index=False
        )
    return fichier


def test_sanitize_table_name():
    assert sanitize_table_name("Notes de frais") == "notes_de_frais"
    assert sanitize_table_name("  Employés!  ") == "employ_s"
    assert sanitize_table_name("???") == "table_sans_nom"


def test_csv_schema_et_requete(csv_ventes: Path):
    adapter = DuckDBAdapter.from_file(csv_ventes)
    schema = adapter.schema()
    assert schema.table_names() == ["ventes"]
    colonnes = [c.name for c in schema.tables[0].columns]
    assert colonnes == ["region", "montant"]

    result = adapter.run(
        "SELECT region, sum(montant) AS total FROM ventes GROUP BY region ORDER BY region"
    )
    assert result.columns == ["region", "total"]
    assert result.rows == [["nord", 150], ["sud", 200]]


def test_xlsx_deux_feuilles_et_jointure(xlsx_multi: Path):
    adapter = DuckDBAdapter.from_file(xlsx_multi)
    assert set(adapter.schema().table_names()) == {"employ_s", "notes_de_frais"}

    result = adapter.run(
        "SELECT e.nom, sum(n.montant) AS total FROM employ_s e"
        " JOIN notes_de_frais n ON n.employe_id = e.id"
        " GROUP BY e.nom ORDER BY e.nom"
    )
    assert result.rows == [["Alice", 30.0], ["Bob", 5.5]]


def test_erreur_sql_devient_query_error(csv_ventes: Path):
    adapter = DuckDBAdapter.from_file(csv_ventes)
    with pytest.raises(QueryError, match="colonne_inconnue"):
        adapter.run("SELECT colonne_inconnue FROM ventes")


def test_ecriture_refusee_avant_execution(csv_ventes: Path):
    adapter = DuckDBAdapter.from_file(csv_ventes)
    with pytest.raises(QueryError, match=r"interdit|SELECT"):
        adapter.run("DROP VIEW ventes")


def test_troncature(csv_ventes: Path):
    adapter = DuckDBAdapter.from_file(csv_ventes)
    result = adapter.run("SELECT * FROM ventes", max_rows=2)
    assert result.truncated is True
    assert result.row_count == 2


def test_fichier_absent():
    with pytest.raises(FileNotFoundError):
        DuckDBAdapter.from_file(Path("/nexiste/pas.csv"))


def test_format_inconnu(tmp_path: Path):
    fichier = tmp_path / "donnees.parquet"
    fichier.write_bytes(b"PAR1")
    with pytest.raises(ValueError, match="format non géré"):
        DuckDBAdapter.from_file(fichier)
