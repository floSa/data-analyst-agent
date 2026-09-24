"""Les deux propriétés du SQL, sur une base DuckDB qui porte les vrais rapports.

Sonde RÉELLE et non doublure : ce qui décide du verdict est un comptage dans la
base — combien de lignes, combien de valeurs distinctes —, et une doublure qui
répondrait « unique » ou « pas unique » mesurerait le test au lieu de mesurer la
propriété.

Les tables reprennent la forme des Cycles du Ponant, aux volumes près :
8 produits, des ordres de fabrication et des lignes de commande qui se répètent
tous deux par produit. C'est exactement la configuration qui multiplie.
"""

from __future__ import annotations

import duckdb
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.analysis.consigne import FiltreMonte
from data_analyst_agent.agents.retrieval.agent import run_retrieval
from data_analyst_agent.agents.retrieval.catalog import FiltreDesSommes
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.agents.retrieval.verification import (
    lire_les_portees,
    somme_multipliee,
    somme_sql_sans_son_filtre,
    sonde_de_l_adaptateur,
)
from data_analyst_agent.config import Settings

VENTES = FiltreDesSommes(
    colonne="commandes.statut",
    exclure="ANN",
    sommes=[
        "lignes_commande.quantite",
        "lignes_commande.montant_ligne_eur",
        "commandes.montant_total_eur",
    ],
)
CROISE = [FiltreMonte("ventes", "ventes_", VENTES)]


@pytest.fixture
def base():
    """Un croisement `ventes` + `production` en miniature, tables préfixées."""
    connection = duckdb.connect(":memory:")
    connection.execute("""
        CREATE TABLE ventes_produits AS SELECT * FROM (VALUES
            (1, 'VEL-01'), (2, 'VEL-04')) AS t(produit_id, code_produit);
        CREATE TABLE ventes_clients AS SELECT * FROM (VALUES
            (1, 'magasin'), (2, 'en ligne')) AS t(client_id, canal);
        CREATE TABLE ventes_commandes AS SELECT * FROM (VALUES
            (10, 1, 'LIV', 100.0), (11, 2, 'ANN', 50.0), (12, 1, 'EXP', 30.0))
            AS t(commande_id, client_id, statut, montant_total_eur);
        CREATE TABLE ventes_lignes_commande AS SELECT * FROM (VALUES
            (10, 1, 5, 40.0), (11, 1, 7, 50.0), (12, 2, 3, 30.0))
            AS t(commande_id, produit_id, quantite, montant_ligne_eur);
        CREATE TABLE production_ordres_fabrication AS SELECT * FROM (VALUES
            (100, 'VEL-01', 20), (101, 'VEL-01', 30), (102, 'VEL-04', 40))
            AS t(ordre_id, code_produit, quantite_produite);
    """)
    tables = [
        "ventes_produits",
        "ventes_clients",
        "ventes_commandes",
        "ventes_lignes_commande",
        "production_ordres_fabrication",
    ]
    adapter = DuckDBAdapter(connection, tables)
    yield adapter
    adapter.close()


@pytest.fixture
def lire_la_base(base):
    """``(sql) -> (multiplication, filtre_manquant)``, mesuré dans la base."""
    schema = base.schema()
    connues = {t.name.lower(): t.name for t in schema.tables}
    sonde = sonde_de_l_adaptateur(base, schema)

    def lire(sql: str, filtres=CROISE):
        return (
            somme_multipliee(sql, connues, sonde),
            somme_sql_sans_son_filtre(sql, connues, filtres),
        )

    return lire


# --- ① la somme multipliée par une jointure ----------------------------------

MULTIPLIE = """
SELECT SUM(T1.quantite_produite) AS fabrique, SUM(T3.quantite) AS vendu
FROM production_ordres_fabrication AS T1
INNER JOIN ventes_produits AS T2 ON T1.code_produit = T2.code_produit
INNER JOIN ventes_lignes_commande AS T3 ON T2.produit_id = T3.produit_id
WHERE T2.code_produit = 'VEL-01'
"""


def test_deux_tables_de_faits_par_une_dimension_multiplient(lire_la_base, base):
    """Le défaut mesuré : chaque ordre apparié à chaque ligne de commande."""
    # La requête « marche » : elle rend un chiffre, et il est faux.
    assert base.run(MULTIPLIE).rows == [[100, 24]]  # 50 x 2 lignes, 12 x 2 ordres
    multiplication, _ = lire_la_base(MULTIPLIE)
    assert multiplication is not None
    assert multiplication.table_sommee == "production_ordres_fabrication"
    assert multiplication.multiplicatrices == ("ventes_lignes_commande",)
    # Le fait rendu au modèle porte la MESURE, pas une opinion sur sa requête.
    assert "3 ligne(s) renseignée(s) pour 2 valeur(s) distincte(s)" in (
        multiplication.pour_le_modele()
    )
    assert "sous-requête par table" in multiplication.pour_le_modele()


CARTESIEN = """
SELECT SUM(T1.quantite_produite) AS fabrique, SUM(T3.quantite) AS vendu
FROM production_ordres_fabrication AS T1
INNER JOIN ventes_produits AS T2 ON T1.code_produit = T2.code_produit
INNER JOIN ventes_lignes_commande AS T3
  ON T2.code_produit = (SELECT code_produit FROM ventes_produits WHERE produit_id = T3.produit_id)
"""


def test_une_table_qu_aucune_egalite_ne_relie_est_signalee(lire_la_base):
    """La seconde forme relevée : la jointure passe par une sous-requête corrélée."""
    multiplication, _ = lire_la_base(CARTESIEN)
    assert multiplication is not None
    assert "produit cartésien" in multiplication.pour_le_modele()


def test_l_avertissement_dit_le_chiffre_surevalue(lire_la_base):
    multiplication, _ = lire_la_base(MULTIPLIE)
    assert "surévalué" in multiplication.pour_l_utilisateur()


JUSTE = """
SELECT f.code_produit, f.q AS fabrique, v.q AS vendu
FROM (SELECT code_produit, SUM(quantite_produite) AS q
      FROM production_ordres_fabrication GROUP BY code_produit) f
JOIN (SELECT p.code_produit, SUM(lc.quantite) AS q
      FROM ventes_lignes_commande lc
      JOIN ventes_produits p ON p.produit_id = lc.produit_id
      JOIN ventes_commandes c ON c.commande_id = lc.commande_id
      WHERE c.statut <> 'ANN' GROUP BY p.code_produit) v ON v.code_produit = f.code_produit
ORDER BY f.code_produit
"""


def test_la_forme_juste_ne_declenche_rien(lire_la_base, base):
    """Une agrégation par table, puis un rapprochement : c'est la réparation attendue."""
    assert base.run(JUSTE).rows == [["VEL-01", 50, 5], ["VEL-04", 40, 3]]
    assert lire_la_base(JUSTE) == (None, None)


# --- LES TROIS TÉMOINS -------------------------------------------------------

CA_PAR_CANAL = """
SELECT cl.canal, SUM(lc.montant_ligne_eur) AS ca
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
JOIN ventes_clients cl ON cl.client_id = c.client_id
WHERE c.statut <> 'ANN'
GROUP BY cl.canal
"""


def test_temoin_une_jointure_de_dimension_ne_declenche_rien(lire_la_base):
    """TÉMOIN — `commandes` puis `clients` : deux clés uniques, aucune duplication.

    C'est la question du CA par canal. Un garde-fou qui la signalerait
    détruirait une bonne réponse, et le ferait sur la moitié des requêtes du
    produit : passer par une table de dimension est la forme NORMALE d'un
    croisement juste.
    """
    assert lire_la_base(CA_PAR_CANAL) == (None, None)


COMPTAGE = """
SELECT count(*) AS n
FROM ventes_commandes c
JOIN ventes_lignes_commande lc ON lc.commande_id = c.commande_id
"""


def test_temoin_un_comptage_ne_recoit_ni_l_un_ni_l_autre(lire_la_base):
    """TÉMOIN — 180 commandes, pas 164 ; 463 lignes.

    Les deux propriétés exigent un ``SUM(`` : un comptage n'en porte aucun, et
    n'est donc jamais vu — même joint à une table qui se répète.
    """
    assert lire_la_base(COMPTAGE) == (None, None)


CA_FILTRE = """
SELECT SUM(lc.montant_ligne_eur) AS ca
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
WHERE c.statut <> 'ANN'
"""
CA_SANS_FILTRE = """
SELECT SUM(lc.montant_ligne_eur) AS ca
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
"""


def test_temoin_une_somme_en_euros_garde_son_filtre(lire_la_base):
    """TÉMOIN — le CA 2025 filtré passe sans un mot ; le même sans filtre est repris."""
    assert lire_la_base(CA_FILTRE) == (None, None)
    multiplication, manquant = lire_la_base(CA_SANS_FILTRE)
    assert multiplication is None
    assert manquant is not None
    assert manquant.table_du_filtre == "ventes_commandes"
    assert "`statut <> 'ANN'`" in manquant.pour_le_modele()
    assert "montant_ligne_eur" in manquant.pour_le_modele()


# --- ② le filtre déclaré, sur le SQL -----------------------------------------


def test_le_filtre_manquant_est_releve_sur_la_requete_qui_multiplie(lire_la_base):
    """Les deux fautes sont empilées dans la requête relevée : les deux se disent."""
    multiplication, manquant = lire_la_base(MULTIPLIE)
    assert multiplication is not None
    assert manquant is not None
    assert "`quantite` (sommée dans `ventes_lignes_commande`)" in manquant.pour_le_modele()


def test_sans_filtre_declare_la_propriete_ne_dit_jamais_rien(lire_la_base):
    assert lire_la_base(CA_SANS_FILTRE, filtres=[])[1] is None


def test_une_source_seule_garde_ses_noms_de_tables():
    """Hors croisement, le préfixe est vide : `commandes`, pas `ventes_commandes`."""
    seule = FiltreMonte("ventes", "", VENTES)
    assert seule.table("commandes") == "commandes"
    assert seule.fichier("commandes") == "commandes.csv"


# --- là où le module se tait --------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(quantite) FROM une_table_inconnue",
        "SELECT SUM(x.quantite) FROM (SELECT * FROM ventes_lignes_commande) x",
        # Non qualifiée alors que la portée porte DEUX tables : de laquelle
        # sort-elle ? L'expression qui l'entoure, elle, ne fait plus douter
        # (C64) — c'est la colonne devinée qui fait douter.
        "SELECT SUM(quantite * 2) FROM ventes_lignes_commande lc "
        "JOIN ventes_commandes c ON c.commande_id = lc.commande_id",
        "SELECT SUM(1) FROM ventes_lignes_commande",
        "SELECT * FROM ventes_commandes",
        "",
    ],
)
def test_le_doute_se_tait(lire_la_base, sql):
    """Une table inconnue, une sous-requête, une colonne devinée : rien n'est affirmé."""
    assert lire_la_base(sql) == (None, None)


# --- chaque somme est jugée dans SA portée ------------------------------------
#
# Avant C62, les deux propriétés ne lisaient que le niveau zéro de la requête.
# Or la relance contre la multiplication pousse le modèle vers des sous-requêtes
# agrégées — c'est la forme RÉPARÉE —, et une somme écrite là n'était plus vue
# du tout. Sur « pour le VEL-02, combien fabriqués et combien vendus ? », un
# tirage sur sept servait 147 vendus au lieu de 130 : le chiffre sans le filtre
# des annulées, sans un mot d'avertissement.

DEUX_SOUS_REQUETES = """
SELECT
  (SELECT SUM(quantite_produite) FROM production_ordres_fabrication
   WHERE code_produit = 'VEL-01') AS fabrique,
  (SELECT SUM(T3.quantite) FROM ventes_lignes_commande T3
   JOIN ventes_produits T2 ON T3.produit_id = T2.produit_id
   WHERE T2.code_produit = 'VEL-01') AS vendu
"""


def test_une_somme_en_sous_requete_est_jugee(lire_la_base):
    """La forme du défaut : deux sommes en sous-requête, dont une sans son filtre."""
    _, manquant = lire_la_base(DEUX_SOUS_REQUETES)
    assert manquant is not None
    assert "`quantite` (sommée dans `ventes_lignes_commande`)" in manquant.pour_le_modele()


SOUS_REQUETE_FILTREE = """
SELECT
  (SELECT SUM(T3.quantite) FROM ventes_lignes_commande T3
   JOIN ventes_commandes c ON c.commande_id = T3.commande_id
   WHERE c.statut <> 'ANN') AS vendu
"""


def test_le_filtre_pose_dans_la_sous_requete_qui_somme_suffit(lire_la_base):
    """Le filtre compte là où il agit : dans la portée qui somme."""
    assert lire_la_base(SOUS_REQUETE_FILTREE) == (None, None)


WITH_NON_FILTRE = """
WITH v AS (SELECT produit_id, SUM(quantite) AS q FROM ventes_lignes_commande GROUP BY produit_id)
SELECT * FROM v
"""


def test_une_somme_dans_un_with_est_jugee(lire_la_base):
    assert lire_la_base(WITH_NON_FILTRE)[1] is not None


WITH_MULTIPLIE = """
WITH x AS (
  SELECT SUM(o.quantite_produite) AS fabrique, SUM(lc.quantite) AS vendu
  FROM production_ordres_fabrication o
  JOIN ventes_produits p ON p.code_produit = o.code_produit
  JOIN ventes_lignes_commande lc ON lc.produit_id = p.produit_id
  JOIN ventes_commandes c ON c.commande_id = lc.commande_id
  WHERE c.statut <> 'ANN')
SELECT * FROM x
"""


def test_une_somme_multipliee_ecrite_dans_un_with_est_signalee(lire_la_base):
    """Deux tables de faits jointes par le produit, à l'abri d'un ``WITH``."""
    multiplication, manquant = lire_la_base(WITH_MULTIPLIE)
    assert multiplication is not None
    assert multiplication.table_sommee == "production_ordres_fabrication"
    assert "ventes_lignes_commande" in multiplication.multiplicatrices
    assert manquant is None  # le filtre est posé là où la somme se fait


CTE_PUIS_FILTRE_EXTERIEUR = """
WITH lignes AS (
  SELECT lc.quantite, c.statut FROM ventes_lignes_commande lc
  JOIN ventes_commandes c ON c.commande_id = lc.commande_id)
SELECT SUM(quantite) AS vendu FROM lignes WHERE statut <> 'ANN'
"""


def test_un_cte_puis_un_filtre_exterieur_ne_declenche_rien(lire_la_base):
    """La requête est juste, et le module n'en juge rien : il ne connaît pas `lignes`.

    Le nom d'un ``WITH`` n'est pas une table du schéma : sa portée n'a pas de
    cardinalité à mesurer, et elle est comptée dans ``Lecture.illisibles`` —
    le module se tait, et il dit combien de fois.
    """
    assert lire_la_base(CTE_PUIS_FILTRE_EXTERIEUR) == (None, None)
    lecture = lire_les_portees(CTE_PUIS_FILTRE_EXTERIEUR, {"ventes_lignes_commande": "x"})
    assert lecture.portees == []
    assert lecture.illisibles == 1


def test_une_portee_sans_somme_n_est_pas_comptee_illisible(lire_la_base):
    """Un ``SELECT *`` sur un ``WITH`` n'a rien à faire juger : il n'est pas un manque."""
    lecture = lire_les_portees(
        WITH_NON_FILTRE, {"ventes_lignes_commande": "ventes_lignes_commande"}
    )
    assert len(lecture.portees) == 1
    assert lecture.illisibles == 0


# --- ce que la récupération en fait : une relance bornée, puis un avertissement


def rejoue(etapes: list[list], recus: list[str]) -> FunctionModel:
    """Rejoue des réponses modèle, en notant ce que les outils lui ont rendu."""
    restantes = [ModelResponse(parts=parts) for parts in etapes]

    def repondre(messages, info):
        # Réécrit et non accumulé : pydantic-ai repasse TOUT l'historique à
        # chaque appel, et l'accumuler compterait deux fois la même remarque.
        recus.clear()
        recus.extend(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, str)
        )
        return restantes.pop(0)

    return FunctionModel(repondre)


def test_la_relance_repart_au_modele_qui_corrige(base):
    """Le fait repart par le canal du classement, et la requête juste est servie."""
    recus: list[str] = []
    model = rejoue(
        [
            [ToolCallPart("run_sql", {"query": MULTIPLIE})],
            [ToolCallPart("run_sql", {"query": JUSTE})],
            [TextPart("VEL-01 : 50 fabriqués, 5 vendus.")],
        ],
        recus,
    )
    issue = run_retrieval(
        "compare la production et les ventes",
        adapter=base,
        model=model,
        settings=Settings(_env_file=None),
        filtres=CROISE,
    )
    relance = "\n".join(recus)
    assert "la jointure DUPLIQUE ses lignes" in relance
    assert "sans écarter `statut = 'ANN'`" in relance
    # La requête servie est la corrigée, et elle ne traîne aucun avertissement.
    assert issue.result.rows == [["VEL-01", 50, 5], ["VEL-04", 40, 3]]
    assert issue.avertissement == ""


def test_une_seule_relance_par_propriete_puis_l_avertissement(base):
    """Le modèle passe outre : la remarque n'est pas resservie, et la réponse le dit.

    Sans ce verrou, la même remarque reviendrait à chaque requête et la boucle
    mangerait ``retrieval_request_limit`` sur un texte déjà lu. Et la réponse
    n'est pas jetée pour autant : le tableau est servi, avec ce qui cloche.
    """
    recus: list[str] = []
    model = rejoue(
        [
            [ToolCallPart("run_sql", {"query": MULTIPLIE})],
            [ToolCallPart("run_sql", {"query": MULTIPLIE})],
            [TextPart("100 fabriqués, 24 vendus.")],
        ],
        recus,
    )
    issue = run_retrieval(
        "compare la production et les ventes",
        adapter=base,
        model=model,
        settings=Settings(_env_file=None),
        filtres=CROISE,
    )
    assert sum(r.count("la jointure DUPLIQUE ses lignes") for r in recus) == 1
    assert "surévalué" in issue.avertissement
    assert "exclut de toute somme" in issue.avertissement
    assert issue.result.rows == [[100, 24]]  # servi, et non jeté


# --- une somme écrite DANS une expression (C64) -------------------------------
#
# Le défaut mesuré : « pour le VEL-01, combien on en a fabriqué et combien on en
# a vendu ? » servait 141 vendus quand le juste est 123 — le chiffre sans le
# filtre des annulées —, dans un `SUM(CASE WHEN … THEN quantite ELSE 0 END)`.
# Les deux propriétés n'exigeaient plus qu'une chose pour se taire : que la
# colonne soit enrobée. Un test par forme d'enrobage.

CASE_SANS_FILTRE = """
SELECT SUM(CASE WHEN T1.code_produit = 'VEL-01' THEN T2.quantite ELSE 0 END) AS vendu
FROM ventes_produits AS T1
INNER JOIN ventes_lignes_commande AS T2 ON T1.produit_id = T2.produit_id
"""
COALESCE_SANS_FILTRE = """
SELECT SUM(COALESCE(lc.quantite, 0)) AS vendu
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
"""
ARITHMETIQUE_SANS_FILTRE = """
SELECT SUM(lc.quantite * lc.montant_ligne_eur) AS valeur
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
"""
CAST_SANS_FILTRE = """
SELECT SUM(CAST(lc.quantite AS BIGINT)) AS vendu
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
"""


@pytest.mark.parametrize(
    "sql",
    [CASE_SANS_FILTRE, COALESCE_SANS_FILTRE, ARITHMETIQUE_SANS_FILTRE, CAST_SANS_FILTRE],
    ids=["case", "coalesce", "arithmetique", "cast"],
)
def test_la_colonne_sommee_est_vue_sous_son_expression(lire_la_base, sql):
    """CASE, COALESCE, `quantite * prix`, cast : c'est toujours `quantite` qu'on somme."""
    _, manquant = lire_la_base(sql)
    assert manquant is not None
    assert "`quantite` (sommée dans `ventes_lignes_commande`)" in manquant.pour_le_modele()


MULTIPLIE_DANS_UNE_EXPRESSION = """
SELECT SUM(CASE WHEN T2.code_produit = 'VEL-01' THEN T1.quantite_produite ELSE 0 END) AS fabrique
FROM production_ordres_fabrication AS T1
INNER JOIN ventes_produits AS T2 ON T1.code_produit = T2.code_produit
INNER JOIN ventes_lignes_commande AS T3 ON T2.produit_id = T3.produit_id
"""


def test_la_multiplication_se_lit_aussi_sous_l_expression(lire_la_base):
    """La propriété ① se pesait sur le même argument nu : elle se pèse sur la colonne."""
    multiplication, _ = lire_la_base(MULTIPLIE_DANS_UNE_EXPRESSION)
    assert multiplication is not None
    assert multiplication.table_sommee == "production_ordres_fabrication"
    assert multiplication.multiplicatrices == ("ventes_lignes_commande",)


# --- et ce qui, sous une expression, reste MUET -------------------------------

CASE_AVEC_LE_FILTRE = """
SELECT SUM(CASE WHEN T1.code_produit = 'VEL-01' THEN T2.quantite ELSE 0 END) AS vendu
FROM ventes_produits AS T1
INNER JOIN ventes_lignes_commande AS T2 ON T1.produit_id = T2.produit_id
JOIN ventes_commandes c ON c.commande_id = T2.commande_id
WHERE c.statut <> 'ANN'
"""
LE_FILTRE_POSE_DANS_LE_CASE = """
SELECT SUM(CASE WHEN c.statut <> 'ANN' THEN lc.quantite END) AS vendu
FROM ventes_lignes_commande lc
JOIN ventes_commandes c ON c.commande_id = lc.commande_id
"""
COMPTAGE_DANS_UN_CASE = """
SELECT COUNT(CASE WHEN T1.code_produit = 'VEL-01' THEN T2.quantite END) AS n
FROM ventes_produits AS T1
INNER JOIN ventes_lignes_commande AS T2 ON T1.produit_id = T2.produit_id
"""


@pytest.mark.parametrize(
    "sql",
    [CASE_AVEC_LE_FILTRE, LE_FILTRE_POSE_DANS_LE_CASE, COMPTAGE_DANS_UN_CASE],
    ids=["filtre-pose", "filtre-dans-le-case", "comptage"],
)
def test_sous_une_expression_le_juste_reste_muet(lire_la_base, sql):
    """Le filtre posé — y compris DANS le CASE — est un filtre ; un comptage n'est rien.

    `SUM(CASE WHEN statut <> 'ANN' THEN quantite END)` somme `quantite` et
    filtre sur `statut` : la colonne d'une condition n'est pas une colonne
    sommée, sans quoi la multiplication se jugerait depuis la table du filtre
    et une requête juste se verrait reprise.
    """
    assert lire_la_base(sql) == (None, None)


def test_le_chiffre_du_case_est_bien_celui_du_defaut(base):
    """Les deux chiffres du relevé, dans la base miniature : 12 sans le filtre, 5 avec."""
    assert base.run(CASE_SANS_FILTRE).rows == [[12]]
    assert base.run(CASE_AVEC_LE_FILTRE).rows == [[5]]
