"""Tests du catalogue de sources (YAML, union discriminée, résolution)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    FileSource,
    PostgresSource,
    load_catalog,
    open_source,
)
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import PostgresAdapter

YAML_EXEMPLE = """
sources:
  - type: file
    name: ventes
    description: Ventes mensuelles
    path: donnees/ventes.csv
  - type: postgres
    name: erp
    dsn: postgresql+pg8000://user:${MDP_ERP}@srv:5432/erp
"""


def test_chargement_yaml(tmp_path: Path):
    fichier = tmp_path / "catalogue.yaml"
    fichier.write_text(YAML_EXEMPLE, encoding="utf-8")
    catalog = load_catalog(fichier)
    assert [s.name for s in catalog.sources] == ["ventes", "erp"]
    ventes = catalog.get("ventes")
    assert isinstance(ventes, FileSource)
    # chemin relatif résolu par rapport au YAML
    assert ventes.path == (tmp_path / "donnees" / "ventes.csv").resolve()


def test_expansion_variables_environnement(monkeypatch):
    monkeypatch.setenv("MDP_ERP", "secret123")
    source = PostgresSource(name="erp", dsn="postgresql+pg8000://u:${MDP_ERP}@h/erp")
    assert source.resolved_dsn() == "postgresql+pg8000://u:secret123@h/erp"


def test_catalogue_du_projet_a_exactement_deux_sources():
    """Le catalogue livré : titanic (base 2 tables) + iris (fichier), rien d'autre."""
    catalogue = Path(__file__).parents[3] / "sources" / "catalogue.yaml"
    catalog = load_catalog(catalogue)
    assert [s.name for s in catalog.sources] == ["titanic", "iris"]
    assert isinstance(catalog.get("titanic"), PostgresSource)
    assert isinstance(catalog.get("iris"), FileSource)


def test_source_inconnue():
    catalog = Catalog(sources=[])
    with pytest.raises(KeyError, match="inconnue"):
        catalog.get("nexiste-pas")


def test_type_invalide_rejete():
    with pytest.raises(ValidationError):
        Catalog.model_validate({"sources": [{"type": "mongodb", "name": "x", "dsn": "mongodb://"}]})


def test_describe_liste_les_sources():
    catalog = Catalog(
        sources=[FileSource(name="titanic", description="Passagers", path=Path("t.csv"))]
    )
    description = catalog.describe()
    assert "titanic" in description
    assert "Passagers" in description


def test_open_source_fichier_csv(tmp_path: Path):
    csv = tmp_path / "mini.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    adapter = open_source(FileSource(name="mini", path=csv))
    assert isinstance(adapter, DuckDBAdapter)
    assert adapter.schema().table_names() == ["mini"]


def test_open_source_postgres_est_paresseux():
    # create_engine ne se connecte pas : construire l'adaptateur ne requiert pas de serveur
    source = PostgresSource(name="pg", dsn="postgresql+pg8000://u:p@hote-inexistant:5432/db")
    adapter = open_source(source)
    assert isinstance(adapter, PostgresAdapter)
    assert adapter.dialect == "postgresql"
