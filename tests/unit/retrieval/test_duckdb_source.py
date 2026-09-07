"""DuckDB sur fichiers : CSV natif, Excel multi-feuilles via pandas/openpyxl."""

from contextlib import closing
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from data_analyst_agent.agents.retrieval.duckdb_source import DuckDBAdapter, sanitize_table_name
from data_analyst_agent.agents.retrieval.sql import MAX_DISTINCT_VALUES, QueryError


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


def test_schema_expose_les_valeurs_dune_colonne_texte(csv_ventes: Path):
    """« region » a 2 valeurs : les montrer évite au modèle d'inventer un littéral."""
    schema = DuckDBAdapter.from_file(csv_ventes).schema()
    colonnes = {c.name: c for c in schema.tables[0].columns}

    assert colonnes["region"].values == ["nord", "sud"]
    assert colonnes["montant"].values is None  # numérique : pas de liste de valeurs
    assert "-- valeurs : 'nord', 'sud'" in schema.to_prompt()


def test_schema_ignore_une_colonne_texte_a_forte_cardinalite(tmp_path: Path):
    """Au-delà du seuil, c'est du texte libre : inutile et coûteux dans le prompt."""
    fichier = tmp_path / "gros.csv"
    lignes = "\n".join(f"nom{i},{i}" for i in range(MAX_DISTINCT_VALUES + 5))
    fichier.write_text(f"nom,valeur\n{lignes}\n", encoding="utf-8")

    schema = DuckDBAdapter.from_file(fichier).schema()
    colonnes = {c.name: c for c in schema.tables[0].columns}

    assert colonnes["nom"].values is None
    assert "-- valeurs" not in schema.to_prompt()


@pytest.mark.parametrize(
    "requete",
    [
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT content FROM read_text('/etc/hostname')",
        "SELECT * FROM glob('/home/*')",
    ],
    ids=["read_csv_auto", "read_text", "glob"],
)
def test_lecture_de_fichier_hote_refusee(csv_ventes: Path, requete: str):
    """DuckDB tourne dans le process de l'API : sans verrou, le SQL généré
    par le modèle exfiltre n'importe quel fichier lisible par le serveur.
    Ces requêtes sont des ``SELECT`` valides — le garde-fou lecture seule les
    laisse passer, seul ``enable_external_access=false`` les arrête."""
    adapter = DuckDBAdapter.from_file(csv_ventes)
    with pytest.raises(QueryError, match="file system operations are disabled"):
        adapter.run(requete)


def test_le_verrou_ne_peut_pas_etre_leve_par_le_sql_genere(csv_ventes: Path):
    adapter = DuckDBAdapter.from_file(csv_ventes)
    with pytest.raises(QueryError):
        adapter.run("SET enable_external_access=true")
    with pytest.raises(QueryError, match="file system operations are disabled"):
        adapter.run("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_le_verrou_vaut_pour_toute_connexion_pas_seulement_from_file(tmp_path: Path):
    """Le verrou est posé dans ``__init__``, point de passage de toutes les
    fabriques : une base ``.duckdb`` ouverte en lecture seule (cas d'une source
    de type base, cf. ``from_database``) est protégée sans code dédié."""
    base = tmp_path / "ventes.duckdb"
    fabrique = duckdb.connect(str(base))
    fabrique.execute("CREATE TABLE ventes AS SELECT 'nord' AS region, 100 AS montant")
    fabrique.close()

    adapter = DuckDBAdapter(duckdb.connect(str(base), read_only=True), ["ventes"])

    assert adapter.run("SELECT count(*) AS n FROM ventes").rows == [[1]]
    with pytest.raises(QueryError, match="file system operations are disabled"):
        adapter.run("SELECT * FROM read_csv_auto('/etc/passwd')")


# --- fermeture ----------------------------------------------------------------


def test_close_ferme_la_base_en_memoire(csv_ventes: Path):
    """Une base DuckDB en mémoire retient les données chargées tant qu'elle vit.

    `open_source` est appelée à chaque exécution de nœud (audit §2.3) : sans
    fermeture, chaque question laisse un classeur de plus en mémoire.
    """
    adapter = DuckDBAdapter.from_file(csv_ventes)

    adapter.close()

    with pytest.raises(QueryError, match="closed"):
        adapter.run("SELECT 1")


def test_close_lache_les_dataframes_enregistres(xlsx_multi: Path):
    """Les feuilles Excel sont retenues par l'adaptateur pour le GC : à lâcher aussi."""
    adapter = DuckDBAdapter.from_file(xlsx_multi)
    assert adapter._frames

    adapter.close()

    assert not adapter._frames


def test_close_sutilise_avec_contextlib_closing(csv_ventes: Path):
    """La forme employée par l'orchestrateur : la fermeture est garantie même si ça lève."""
    adapter = DuckDBAdapter.from_file(csv_ventes)

    with pytest.raises(QueryError), closing(adapter):
        adapter.run("DROP TABLE ventes")

    with pytest.raises(QueryError, match="closed"):
        adapter.run("SELECT 1")
