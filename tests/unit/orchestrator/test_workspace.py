"""Mémoire de conversation : persistance CSV et réexposition des objets."""

from pathlib import Path

from data_analyst_agent.agents.retrieval.catalog import FileSource, open_source
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace


def test_save_table_ecrit_csv_et_manifeste(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "conv-1")
    artifact = ws.save_table(
        ["sepal_length", "species"], [[7.9, "virginica"], [7.7, "virginica"]], "les 2 plus grandes"
    )
    assert artifact.name == "resultat_1"
    assert artifact.row_count == 2
    csv = ws.path_of(artifact)
    assert csv.exists()
    assert csv.read_text(encoding="utf-8").splitlines()[0] == "sepal_length,species"
    # le manifeste est écrit à côté
    assert (ws.dir / ConversationWorkspace.MANIFEST).exists()


def test_persistance_relue_par_une_nouvelle_instance(tmp_path: Path):
    """Le tour suivant (nouvelle instance) retrouve les objets sur disque."""
    ConversationWorkspace(tmp_path, "conv-2").save_table(["a"], [[1], [2]], "q1")
    rechargee = ConversationWorkspace(tmp_path, "conv-2")
    assert [a.name for a in rechargee.artifacts] == ["resultat_1"]
    assert rechargee.artifacts[0].columns == ["a"]


def test_numerotation_incrementale(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "conv-3")
    ws.save_table(["a"], [[1]], "q1")
    ws.save_table(["b"], [[2]], "q2")
    assert [a.name for a in ws.artifacts] == ["resultat_1", "resultat_2"]


def test_as_sources_interrogeable_en_sql(tmp_path: Path):
    """Un objet mémorisé devient une source fichier requêtable (table DuckDB homonyme)."""
    ws = ConversationWorkspace(tmp_path, "conv-4")
    ws.save_table(["x", "y"], [[1, 2], [3, 4]], "q")
    sources = ws.as_sources()
    assert isinstance(sources[0], FileSource)
    assert sources[0].name == "resultat_1"
    adapter = open_source(sources[0])
    # le nom de table DuckDB coïncide avec le nom de la source
    assert adapter.schema().table_names() == ["resultat_1"]
    assert adapter.run("SELECT count(*) FROM resultat_1").rows[0][0] == 2


def test_describe_et_sandbox_files(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "conv-5")
    ws.save_table(["sepal_length"], [[7.9]], "3 dernières lignes iris")
    description = ws.describe()
    assert "resultat_1" in description
    assert "sepal_length" in description
    assert "ces lignes" in description  # aiguille le planificateur sur le plus récent
    files = ws.sandbox_files()
    assert list(files.values()) == ["resultat_1.csv"]


def test_describe_vide_si_aucun_objet(tmp_path: Path):
    assert ConversationWorkspace(tmp_path, "vide").describe() is None
