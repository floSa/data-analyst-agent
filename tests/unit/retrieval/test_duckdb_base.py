"""Une base DuckDB est une source à part entière, et elle apporte ses CLÉS.

Le catalogue savait déclarer deux natures de source : une base Postgres et un
fichier. Entre les deux manquait la forme qu'a n'importe quel gros jeu de
données local — un fichier ``.duckdb``, qui est une BASE : plusieurs tables, des
clés primaires, et surtout des clés étrangères.

C'est cette dernière ligne qui justifie un type de source de plus plutôt qu'une
extension de ``FileSource`` : un classeur ou un CSV n'a aucune contrainte à
déclarer, donc un schéma en étoile passé par cette porte arriverait au modèle
sans ses jointures, et le modèle les devinerait. Les tests ci-dessous portent
donc sur trois propriétés : le type est reconnu, les clés remontent, et le
verrou d'accès à l'hôte tient aussi sur une base ouverte en lecture seule —
``read_only`` ne protège QUE la base, pas le disque autour.
"""

from contextlib import closing
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from data_analyst_agent.agents.retrieval.catalog import (
    DuckDBSource,
    load_catalog,
    open_source,
)
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import QueryError


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """Une base en étoile minuscule : deux tables, une PK, une FK."""
    chemin = tmp_path / "entrepot.duckdb"
    connexion = duckdb.connect(str(chemin))
    connexion.execute("CREATE TABLE magasins (magasin_id INTEGER PRIMARY KEY, ville VARCHAR)")
    connexion.execute(
        "CREATE TABLE ventes ("
        "  vente_id INTEGER PRIMARY KEY,"
        "  magasin_id INTEGER REFERENCES magasins(magasin_id),"
        "  jour DATE, montant DOUBLE)"
    )
    connexion.execute("INSERT INTO magasins VALUES (1, 'Nantes'), (2, 'Lyon')")
    connexion.execute(
        "INSERT INTO ventes VALUES (1, 1, DATE '2024-01-05', 10.0), (2, 2, DATE '2024-08-09', 32.5)"
    )
    connexion.close()
    return chemin


def test_le_catalogue_accepte_le_type_duckdb(tmp_path: Path, base: Path):
    """Trois types déclarables, et un chemin relatif résolu comme celui d'un fichier."""
    yaml = tmp_path / "catalogue.yaml"
    yaml.write_text(
        "sources:\n"
        "  - type: duckdb\n"
        "    name: entrepot\n"
        "    description: L'entrepôt local\n"
        "    path: entrepot.duckdb\n",
        encoding="utf-8",
    )

    catalogue = load_catalog(yaml)

    source = catalogue.get("entrepot")
    assert isinstance(source, DuckDBSource)
    assert source.path == base.resolve()
    assert "(duckdb)" in catalogue.describe()


def test_un_type_de_source_inconnu_reste_refuse(tmp_path: Path):
    """L'union discriminée s'élargit d'un type, elle ne s'ouvre pas à tous."""
    yaml = tmp_path / "catalogue.yaml"
    yaml.write_text("sources:\n  - type: sqlite\n    name: x\n    path: x.db\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_catalog(yaml)


def test_open_source_ouvre_une_base_duckdb(base: Path):
    with closing(open_source(DuckDBSource(name="entrepot", path=base))) as adaptateur:
        assert isinstance(adaptateur, DuckDBAdapter)
        assert adaptateur.schema().table_names() == ["magasins", "ventes"]


def test_les_cles_declarees_remontent_dans_le_schema(base: Path):
    """La raison d'être du type : un schéma en étoile arrive avec ses jointures.

    Sans ça, le DDL servi au modèle ne dit pas que ``ventes.magasin_id`` désigne
    ``magasins`` — et le modèle invente une jointure plausible.
    """
    with closing(open_source(DuckDBSource(name="entrepot", path=base))) as adaptateur:
        ventes = next(t for t in adaptateur.schema().tables if t.name == "ventes")

    assert ventes.primary_key == ["vente_id"]
    assert [(fk.column, fk.ref_table, fk.ref_column) for fk in ventes.foreign_keys] == [
        ("magasin_id", "magasins", "magasin_id")
    ]
    assert "FOREIGN KEY (magasin_id) REFERENCES magasins(magasin_id)" in ventes.to_ddl()


def test_un_fichier_sans_contrainte_ne_rend_pas_de_cles(tmp_path: Path):
    """Le pendant : un CSV n'a pas de clés, et l'introspection le dit sans se plaindre."""
    csv = tmp_path / "plat.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")

    with closing(DuckDBAdapter.from_file(csv)) as adaptateur:
        table = adaptateur.schema().tables[0]

    assert table.primary_key == []
    assert table.foreign_keys == []


def test_la_base_est_ouverte_en_lecture_seule(base: Path):
    """``read_only`` laisse un second process ouvrir la même base — l'API et un
    notebook doivent pouvoir cohabiter."""
    with closing(open_source(DuckDBSource(name="entrepot", path=base))):
        autre = duckdb.connect(str(base), read_only=True)
        assert autre.execute("SELECT count(*) FROM ventes").fetchone()[0] == 2
        autre.close()


def test_le_verrou_dacces_a_lhote_tient_aussi_sur_une_base(base: Path, tmp_path: Path):
    """``read_only`` protège la BASE, pas le disque autour.

    Une connexion en lecture seule reste capable de ``read_csv_auto`` sur
    n'importe quel fichier de l'hôte : c'est le verrou posé dans ``__init__``,
    et non le mode d'ouverture, qui l'en empêche.
    """
    secret = tmp_path / "secret.csv"
    secret.write_text("mot_de_passe\nhunter2\n", encoding="utf-8")

    with (
        closing(open_source(DuckDBSource(name="entrepot", path=base))) as adaptateur,
        pytest.raises(QueryError, match="disabled by configuration"),
    ):
        adaptateur.run(f"SELECT * FROM read_csv_auto('{secret}')")


def test_une_base_absente_dit_ou_elle_manque(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="base DuckDB introuvable"):
        open_source(DuckDBSource(name="fantome", path=tmp_path / "nulle-part.duckdb"))


def test_une_base_sans_aucune_table_est_refusee(tmp_path: Path):
    """Rien à requêter : mieux vaut le dire à l'ouverture qu'au premier SELECT."""
    chemin = tmp_path / "vide.duckdb"
    duckdb.connect(str(chemin)).close()

    with pytest.raises(ValueError, match="aucune table"):
        open_source(DuckDBSource(name="vide", path=chemin))


def test_une_introspection_des_cles_qui_echoue_ne_fait_pas_tomber_le_schema(base: Path):
    """`duckdb_constraints()` est du best-effort : une version de DuckDB qui ne
    l'expose pas, ou un refus quelconque, rend une table sans clés — pas une
    source illisible. Le DDL y perd ses jointures, il ne disparaît pas."""

    class Boudeuse:
        def execute(self, *_args, **_kwargs):
            raise duckdb.Error("pas de duckdb_constraints() ici")

    with closing(open_source(DuckDBSource(name="entrepot", path=base))) as adaptateur:
        # La vraie connexion est reposée ensuite : la doublure n'a rien à fermer,
        # et c'est bien la vraie qui doit l'être à la sortie du `closing`.
        vraie, adaptateur.connection = adaptateur.connection, Boudeuse()
        try:
            assert adaptateur._keys("ventes") == ([], [])
        finally:
            adaptateur.connection = vraie
