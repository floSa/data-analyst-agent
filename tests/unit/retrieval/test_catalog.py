"""Tests du catalogue de sources (YAML, union discriminée, résolution)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    DuckDBSource,
    FileSource,
    PostgresSource,
    load_catalog,
    open_source,
)
from data_analyst_agent.agents.retrieval.duckdb_source import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import PostgresAdapter
from helpers.maxizoo import build_duckdb

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


def test_catalogue_du_projet_declare_la_base_maxizoo():
    """Le catalogue livré : maxizoo (base DuckDB) et son dictionnaire, rien d'autre."""
    catalogue = Path(__file__).parents[3] / "sources" / "catalogue.yaml"
    catalog = load_catalog(catalogue)
    assert [s.name for s in catalog.sources] == ["maxizoo"]
    maxizoo = catalog.get("maxizoo")
    assert isinstance(maxizoo, DuckDBSource)
    assert maxizoo.path.name == "maxizoo.duckdb"
    # Le dictionnaire est ce qui porte les pièges de modélisation : un catalogue
    # qui ne le déclarerait plus laisserait l'agent écrire du SQL plausible et faux.
    assert maxizoo.dictionary is not None
    assert maxizoo.dictionary.name == "maxizoo_dictionnaire.md"


def test_source_inconnue():
    catalog = Catalog(sources=[])
    with pytest.raises(KeyError, match="inconnue"):
        catalog.get("nexiste-pas")


def test_type_invalide_rejete():
    with pytest.raises(ValidationError):
        Catalog.model_validate({"sources": [{"type": "mongodb", "name": "x", "dsn": "mongodb://"}]})


def test_describe_liste_les_sources():
    catalog = Catalog(
        sources=[FileSource(name="maxizoo", description="Ventes retail", path=Path("v.csv"))]
    )
    description = catalog.describe()
    assert "maxizoo" in description
    assert "Ventes retail" in description


def test_open_source_fichier_csv(tmp_path: Path):
    csv = tmp_path / "mini.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    adapter = open_source(FileSource(name="mini", path=csv))
    assert isinstance(adapter, DuckDBAdapter)
    assert adapter.schema().table_names() == ["mini"]


def test_open_source_base_duckdb(tmp_path: Path):
    """Une base DuckDB s'ouvre avec ses tables ET ses clés, pas juste ses colonnes."""
    base = build_duckdb(tmp_path / "mini.duckdb")
    adapter = open_source(DuckDBSource(name="maxizoo", path=base))
    assert isinstance(adapter, DuckDBAdapter)
    schema = adapter.schema()
    assert "sales_daily" in schema.table_names()
    ventes = next(t for t in schema.tables if t.name == "sales_daily")
    assert ventes.primary_key == ["date", "store_id", "sku_id"]
    assert {(fk.column, fk.ref_table) for fk in ventes.foreign_keys} == {
        ("store_id", "stores"),
        ("sku_id", "products"),
        ("promo_id", "promo_calendar"),
    }
    assert "FOREIGN KEY" in schema.to_prompt()


def test_base_duckdb_absente_dit_comment_la_construire(tmp_path: Path):
    """Le message doit donner la commande : la base n'est pas versionnée."""
    source = DuckDBSource(name="maxizoo", path=tmp_path / "pas-la.duckdb")
    with pytest.raises(FileNotFoundError, match="load_maxizoo_duckdb"):
        open_source(source)


def test_dictionnaire_lu_depuis_le_disque(tmp_path: Path):
    md = tmp_path / "dico.md"
    md.write_text("Le e-commerce est un magasin.", encoding="utf-8")
    source = FileSource(name="x", path=tmp_path / "x.csv", dictionary=md)
    assert source.dictionary_text() == "Le e-commerce est un magasin."


def test_sans_dictionnaire_pas_de_texte():
    assert FileSource(name="x", path=Path("x.csv")).dictionary_text() is None


def test_chemin_du_dictionnaire_resolu_par_rapport_au_yaml(tmp_path: Path):
    fichier = tmp_path / "catalogue.yaml"
    fichier.write_text(
        "sources:\n"
        "  - type: duckdb\n"
        "    name: maxizoo\n"
        "    path: maxizoo.duckdb\n"
        "    dictionary: maxizoo_dictionnaire.md\n",
        encoding="utf-8",
    )
    source = load_catalog(fichier).get("maxizoo")
    assert source.path == (tmp_path / "maxizoo.duckdb").resolve()
    assert source.dictionary == (tmp_path / "maxizoo_dictionnaire.md").resolve()


def test_open_source_postgres_est_paresseux():
    # create_engine ne se connecte pas : construire l'adaptateur ne requiert pas de serveur
    source = PostgresSource(name="pg", dsn="postgresql+pg8000://u:p@hote-inexistant:5432/db")
    adapter = open_source(source)
    assert isinstance(adapter, PostgresAdapter)
    assert adapter.dialect == "postgresql"


def test_dsn_variable_non_definie_message_explicite(monkeypatch):
    """Une ${VAR} non résolue doit nommer le coupable, pas finir en int('${...}')."""
    monkeypatch.delenv("DAA_PG_PORT", raising=False)
    source = PostgresSource(
        name="maxizoo", dsn="postgresql+pg8000://u:p@localhost:${DAA_PG_PORT}/maxizoo"
    )

    with pytest.raises(ValueError, match="DAA_PG_PORT") as exc:
        source.resolved_dsn()
    assert "maxizoo" in str(exc.value)
    assert ".env" in str(exc.value)  # dit quoi corriger


def test_dsn_resolu_depuis_lenvironnement(monkeypatch):
    monkeypatch.setenv("DAA_PG_PORT", "5432")
    source = PostgresSource(name="t", dsn="postgresql+pg8000://u:p@h:${DAA_PG_PORT}/t")

    assert source.resolved_dsn() == "postgresql+pg8000://u:p@h:5432/t"
