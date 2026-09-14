"""Garde-fou lecture seule + modèles d'ontologie et de résultats."""

import datetime as dt
import decimal
from contextlib import closing

import pytest

from data_analyst_agent.agents.retrieval.sql import (
    ColumnInfo,
    ForeignKeyInfo,
    PostgresAdapter,
    QueryError,
    QueryResult,
    SchemaInfo,
    TableInfo,
    assert_read_only,
    build_result,
    mask_literals,
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


# --- ce qui vit dans un littéral, un identifiant cité ou un commentaire ---------
#
# Le garde-fou lisait le texte brut : « SELECT ';' AS x » était refusé comme
# deux instructions, et toute question portant sur une valeur contenant un
# point-virgule était bloquée — le modèle rebouclait jusqu'à épuiser sa limite
# d'allers-retours sans jamais savoir pourquoi (audit §5.1). Même mécanique pour
# un mot-clé d'écriture qui n'est qu'une donnée.


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM t WHERE nom = 'a;b'",  # le cas de l'audit
        "SELECT ';' AS point_virgule",
        "SELECT * FROM t WHERE nom = 'l''été; ok'",  # apostrophe doublée
        'SELECT "colonne;bizarre" FROM t',  # identifiant cité
        'SELECT "identifiant ""double"" ; ok" FROM t',  # guillemet double doublé
        "SELECT 'guillemet \"double\" dedans ; ok' AS x",
        "SELECT * FROM t WHERE action = 'DELETE'",  # mot-clé, mais donnée
        'SELECT * FROM "drop"',  # table nommée comme un mot-clé
        "SELECT 1 -- un point-virgule ; en commentaire",
        "SELECT 1 /* ; DROP TABLE t */",
        "-- commentaire d'entête\nSELECT 1",  # le SELECT reste le premier mot
    ],
)
def test_point_virgule_et_mots_cles_dans_une_donnee_sont_acceptes(query):
    assert_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 'a'; DROP TABLE t",
        "SELECT 'a;b'; DROP TABLE t",  # littéral ET vraie seconde instruction
        'SELECT "c" ; DROP TABLE t',
        "SELECT 1 -- commentaire\n; DROP TABLE t",  # le ; est APRÈS le commentaire
        "SELECT 1 /* commentaire */; DROP TABLE t",
        "SELECT E'a\\'; DROP TABLE t --'",  # l'antislash n'échappe rien : refusé
    ],
)
def test_un_point_virgule_hors_litteral_reste_interdit(query):
    """Le correctif ne doit pas ouvrir de brèche : c'est le sens qui compte."""
    with pytest.raises(QueryError, match="une seule instruction"):
        assert_read_only(query)


@pytest.mark.parametrize("query", ["-- rien qu'un commentaire", "/* rien */", "  ;  "])
def test_une_requete_sans_sql_executable_est_vide(query):
    with pytest.raises(QueryError, match="requête vide"):
        assert_read_only(query)


def test_la_requete_rendue_est_l_originale_intacte():
    """Le masque sert à décider ; c'est la requête d'origine qui part au moteur."""
    query = "SELECT * FROM t WHERE nom = 'a;b';"

    assert assert_read_only(query) == "SELECT * FROM t WHERE nom = 'a;b'"


def test_le_masque_preserve_longueur_et_lignes():
    """Un masque de même longueur garde les positions exploitables (messages, offsets)."""
    query = "SELECT 'a;b' -- x\nFROM t"

    masque = mask_literals(query)

    # le littéral (5 caractères) et le commentaire (4) blanchis, le reste intact
    assert masque == "SELECT " + " " * 5 + " " + " " * 4 + "\nFROM t"
    assert len(masque) == len(query)
    assert masque.count("\n") == query.count("\n")


def test_un_litteral_non_referme_masque_tout_ce_qui_suit():
    """Choix conservateur : la requête est alors invalide, le moteur la rejette."""
    query = "SELECT 'a; DROP TABLE t"

    assert mask_literals(query) == "SELECT " + " " * len("'a; DROP TABLE t")


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


def test_une_ligne_unique_est_rendue_verticalement():
    """Un agrégat rend UNE ligne : la donner en tableau force un alignement raté.

    Mesuré sur ``passengers`` : ``0 | 177 | 0 | 0 | 0 | 2`` sous six en-têtes,
    et le modèle nommait ``name`` — mesuré à 0 — parmi les colonnes à trous.
    """
    result = QueryResult(
        columns=["name_missing", "age_missing", "embarked_missing"], rows=[[0, 177, 2]]
    )

    rendu = result.to_markdown()

    assert rendu == "name_missing : 0\nage_missing : 177\nembarked_missing : 2"
    assert "|" not in rendu


def test_une_ligne_a_une_seule_colonne_garde_le_tableau():
    """Rien à aligner sur une seule colonne : le rendu qui marche ne bouge pas."""
    assert "| 342 |" in QueryResult(columns=["count"], rows=[[342]]).to_markdown()


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


# --- fermeture de l'adaptateur Postgres ---------------------------------------


class EngineEspion:
    """Le minimum d'un Engine SQLAlchemy pour ce test : savoir s'il a été rendu."""

    def __init__(self) -> None:
        self.disposes = 0

    def dispose(self) -> None:
        self.disposes += 1


def test_close_rend_le_pool_de_connexions():
    """Sans `dispose()`, chaque nœud exécuté laisse un pool ouvert (audit §2.3)."""
    engine = EngineEspion()
    adapter = PostgresAdapter(engine)

    adapter.close()

    assert engine.disposes == 1


def test_close_est_appele_par_contextlib_closing():
    """La forme employée par l'orchestrateur, y compris quand le corps lève."""
    engine = EngineEspion()

    with pytest.raises(ZeroDivisionError), closing(PostgresAdapter(engine)):
        raise ZeroDivisionError

    assert engine.disposes == 1
