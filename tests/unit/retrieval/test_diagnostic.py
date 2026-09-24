"""Ce que le modèle reçoit quand sa requête échoue, sur une base DuckDB réelle.

Le défaut de C65 : la requête que la remarque de multiplication fait écrire —
deux sous-requêtes déjà agrégées — est re-sommée par-dessus, la base refuse
sans nommer ce que la portée expose, et la MÊME requête repart à l'identique
jusqu'à épuiser ``retrieval_request_limit``.

La base est celle de ``test_verification`` en plus petit : ce qui se mesure ici
n'est pas la cardinalité mais ce que le texte d'erreur porte, et il faut une
vraie base pour que l'erreur soit celle du moteur et non celle d'une doublure.
"""

from __future__ import annotations

import duckdb
import pytest

from data_analyst_agent.agents.retrieval.agent import RetrievalDeps, _retour_d_erreur
from data_analyst_agent.agents.retrieval.diagnostic import (
    colonnes_exposees,
    porte_sur_une_colonne,
    signature,
)
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import QueryError

# La requête exacte relevée le 2026-09-24, essai 7 de la passe 3 : les deux
# sous-requêtes n'exposent que `total_produit` et `total_commandes`, et c'est
# l'erreur qu'elle lève qui ouvre la boucle — elle ne nomme AUCUN candidat.
RESOMMEE = """
SELECT SUM(T1.quantite_produite) AS total_produit, SUM(T2.quantite) AS total_commandes
FROM (
    SELECT SUM(quantite_produite) AS total_produit
    FROM production_ordres_fabrication
) AS T1,
(
    SELECT SUM(quantite) AS total_commandes
    FROM ventes_lignes_commande
) AS T2
"""


@pytest.fixture
def base():
    connection = duckdb.connect(":memory:")
    connection.execute("""
        CREATE TABLE ventes_lignes_commande AS SELECT * FROM (VALUES
            (10, 1, 5), (11, 1, 7)) AS t(commande_id, produit_id, quantite);
        CREATE TABLE production_ordres_fabrication AS SELECT * FROM (VALUES
            (100, 'VEL-01', 20), (101, 'VEL-04', 30))
            AS t(ordre_id, code_produit, quantite_produite);
    """)
    adapter = DuckDBAdapter(connection, ["ventes_lignes_commande", "production_ordres_fabrication"])
    yield adapter
    adapter.close()


@pytest.fixture
def rendu(base):
    """``(sql) -> ce que run_sql rend au modèle``, l'erreur venant de la base."""
    deps = RetrievalDeps(adapter=base)

    def essayer(sql: str) -> str:
        try:
            base.run(sql)
        except QueryError as exc:
            return _retour_d_erreur(deps, sql, str(exc))
        raise AssertionError("cette requête devait échouer")

    return essayer


# --- ① la requête renvoyée à l'identique --------------------------------------


def test_la_meme_requete_renvoyee_recoit_le_fait(rendu):
    """Le premier essai ne le dit pas ; le second le dit."""
    premier = rendu(RESOMMEE)
    assert "CETTE REQUÊTE EXACTE" not in premier
    assert "CETTE REQUÊTE EXACTE" in rendu(RESOMMEE)


def test_les_blancs_ne_font_pas_une_requete_differente(rendu):
    rendu(RESOMMEE)
    reindentee = " ".join(RESOMMEE.split()) + " ;"
    assert "CETTE REQUÊTE EXACTE" in rendu(reindentee)


def test_une_requete_differente_ne_le_recoit_pas(rendu):
    rendu(RESOMMEE)
    autre = rendu("SELECT inconnue FROM production_ordres_fabrication")
    assert "CETTE REQUÊTE EXACTE" not in autre


def test_la_signature_garde_la_casse_des_litteraux():
    """`'ANN'` et `'ann'` ne filtrent pas les mêmes lignes : deux requêtes."""
    assert signature("SELECT 1 WHERE s <> 'ANN'") != signature("SELECT 1 WHERE s <> 'ann'")


# --- ② les colonnes que la portée expose --------------------------------------


def test_l_erreur_de_binder_ne_nomme_pas_ce_que_la_sous_requete_expose(base):
    """Le fait mesuré qui justifie tout ceci : la base ne le dit pas.

    Elle nomme parfois un candidat, et alors le fait ne fait que redire ce
    qu'elle a dit. Sur CETTE erreur-là — celle qui a coûté les quatre essais
    identiques — elle n'en nomme aucun.
    """
    with pytest.raises(QueryError) as leve:
        base.run(RESOMMEE)
    assert "Candidate bindings" not in str(leve.value)


def test_la_sous_requete_agregee_rend_ses_alias(rendu):
    texte = rendu(RESOMMEE)
    assert "`T1` expose `total_produit`" in texte
    assert "`T2` expose `total_commandes`" in texte


def test_une_colonne_inconnue_d_une_table_rend_les_colonnes_du_schema(rendu):
    texte = rendu("SELECT code_produit FROM ventes_lignes_commande")
    assert "`ventes_lignes_commande` expose" in texte
    assert "`quantite`" in texte


def test_une_erreur_qui_ne_parle_pas_de_colonne_ne_dit_rien(rendu):
    """Une conversion ratée n'a pas de colonne manquante à nommer."""
    texte = rendu(
        "SELECT * FROM production_ordres_fabrication AS T1 "
        "JOIN ventes_lignes_commande AS T2 ON T1.code_produit = T2.produit_id"
    )
    assert "EXPOSE RÉELLEMENT" not in texte


def test_l_erreur_de_la_base_reste_en_tete(rendu):
    """Les faits s'ajoutent à l'erreur, jamais à sa place."""
    texte = rendu(RESOMMEE)
    assert texte.startswith("ERREUR SQL : ")
    assert texte.endswith("Corrige la requête et réessaie.")


def test_un_with_rend_les_colonnes_de_sa_projection(base):
    sql = "WITH f AS (SELECT SUM(quantite_produite) AS tot FROM production_ordres_fabrication) "
    sql += "SELECT SUM(f.quantite_produite) FROM f"
    exposees = {r.alias: r.colonnes for r in colonnes_exposees(sql, base.schema())}
    assert exposees["f"] == ("tot",)


def test_une_etoile_ne_se_deplie_pas(base):
    """On se tait plutôt que d'inventer : la portée d'ici ne voit pas ces tables."""
    sql = "SELECT x FROM (SELECT * FROM production_ordres_fabrication) AS s"
    assert "s" not in {r.alias for r in colonnes_exposees(sql, base.schema())}


def test_un_sql_que_l_analyseur_refuse_ne_dit_rien(base):
    assert colonnes_exposees("SELECT SELECT FROM FROM", base.schema()) == []


@pytest.mark.parametrize(
    "erreur",
    [
        'Binder Error: Values list "T1" does not have a column named "quantite_produite"',
        'Binder Error: Referenced column "quantite_produite" not found in FROM clause!',
        'column "quantite_produite" does not exist',
    ],
)
def test_les_erreurs_de_colonne_sont_reconnues(erreur):
    assert porte_sur_une_colonne(erreur)


def test_une_erreur_de_conversion_n_en_est_pas_une():
    assert not porte_sur_une_colonne("Conversion Error: Could not convert string 'VEL-08'")
