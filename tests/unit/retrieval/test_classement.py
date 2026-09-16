"""Un palmarès porte la grandeur qui l'ordonne — la vérification, sans modèle.

C'est tout l'intérêt d'une vérification STRUCTURELLE plutôt que d'une ligne de
prompt : elle est du code, elle se teste sans serveur LLM, et son verdict ne
dépend ni de la langue de la question ni de l'humeur d'un tirage.

Deux familles de cas, et la seconde compte autant que la première : ce qui DOIT
être signalé, et ce qui ne doit PAS l'être. Un faux positif ici coûte un
aller-retour de modèle sur chaque requête ordonnée du produit.
"""

import pytest

from data_analyst_agent.agents.retrieval.classement import grandeurs_non_projetees

# --- ce qui doit être signalé ------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "attendu"),
    [
        # Le défaut exact de Q4 : les stations et leur ordre, sans les comptes.
        (
            "SELECT s.code, s.nom FROM stations s JOIN bornes b ON b.station_id = s.id "
            "JOIN sessions x ON x.borne_id = b.id GROUP BY s.code, s.nom "
            "ORDER BY COUNT(*) DESC LIMIT 3",
            ["COUNT(*)"],
        ),
        # La même chose avec une somme, et sur plusieurs lignes.
        (
            "SELECT r.nom\nFROM regions r\nGROUP BY r.nom\nORDER BY SUM(s.energie_kwh) DESC",
            ["SUM(s.energie_kwh)"],
        ),
        # Deux termes de tri, un seul projeté : on ne signale que l'autre.
        (
            "SELECT code, nom FROM t GROUP BY code, nom ORDER BY COUNT(*) DESC, nom ASC",
            ["COUNT(*)"],
        ),
        # Le tri porte sur une colonne d'une CTE que le SELECT ne rend pas.
        (
            "WITH c AS (SELECT sid, COUNT(*) AS n FROM sessions GROUP BY sid) "
            "SELECT st.code, st.nom FROM c JOIN stations st ON st.id = c.sid "
            "ORDER BY c.n DESC LIMIT 3",
            ["c.n"],
        ),
        # Identifiant cité : il survit au masquage, à la différence d'un littéral.
        ('SELECT code FROM t ORDER BY "nb sessions" DESC', ['"nb sessions"']),
        # Le sens de tri et les NULLS ne font pas partie de l'expression.
        ("SELECT code FROM t ORDER BY total DESC NULLS LAST LIMIT 5", ["total"]),
    ],
)
def test_le_tri_hors_du_select_est_signale(sql, attendu):
    assert grandeurs_non_projetees(sql) == attendu


# --- ce qui ne doit PAS être signalé -----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # La grandeur est projetée, telle quelle.
        "SELECT code, nom, COUNT(*) AS n FROM t GROUP BY code, nom ORDER BY COUNT(*) DESC LIMIT 3",
        # ... ou par son alias.
        "SELECT code, nom, COUNT(*) AS n FROM t GROUP BY code, nom ORDER BY n DESC LIMIT 3",
        # ... ou par un alias posé sans AS.
        "SELECT code, COUNT(*) nb FROM t GROUP BY code ORDER BY nb DESC",
        # ... ou par son rang dans la liste : ORDER BY 3 DÉSIGNE une colonne projetée.
        "SELECT code, nom, COUNT(*) FROM t GROUP BY code, nom ORDER BY 3 DESC",
        # L'écriture diffère, la grandeur est la même.
        "SELECT code, COUNT( s.id ) AS n FROM t GROUP BY code ORDER BY count(s.id) DESC",
        # SELECT * rend tout, y compris ce sur quoi on trie.
        "SELECT * FROM sessions ORDER BY date_debut DESC LIMIT 10",
        "SELECT s.* FROM sessions s ORDER BY s.energie_kwh DESC",
        # La qualification n'est pas la colonne : `s.code` et `code` sont la même.
        "SELECT code FROM stations s ORDER BY s.code",
        'SELECT "code" FROM stations ORDER BY code',
        # Aucun classement : rien à vérifier.
        "SELECT COUNT(*) FROM sessions",
        # Le ORDER BY d'une fenêtre vit entre parenthèses, et n'est pas le tri du résultat.
        "SELECT code, ROW_NUMBER() OVER (ORDER BY total DESC) AS rang FROM t ORDER BY rang",
        # Celui d'une sous-requête non plus.
        "SELECT code FROM (SELECT code FROM t ORDER BY COUNT(*) DESC) q",
        # Ni celui d'un agrégat ordonné.
        "SELECT STRING_AGG(nom, ', ' ORDER BY nom) AS noms FROM stations",
        # Un DISTINCT en tête de liste n'est pas une expression projetée.
        "SELECT DISTINCT code, total FROM t ORDER BY total DESC",
        # Un point-virgule final ne doit pas coller au dernier terme de tri.
        "SELECT code, total FROM t ORDER BY total DESC;",
        # Une virgule dans un littéral ne coupe pas la liste du SELECT.
        "SELECT code || ', ' || nom AS libelle, total FROM t ORDER BY total DESC",
    ],
)
def test_le_sql_en_regle_ne_declenche_rien(sql):
    assert grandeurs_non_projetees(sql) == []


# --- ce qu'on lui donne de travers -------------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", "pas du sql du tout", "SELECT", "ORDER BY x"])
def test_un_sql_illisible_ne_leve_pas_et_ne_signale_rien(sql):
    """Dans le doute, on conclut « projetée ».

    Ce module vit dans la boucle d'un outil appelé à chaque requête : une
    exception y transformerait un résultat correct en tour mort, et un faux
    positif y coûterait un aller-retour de modèle. Les deux sont plus chers que
    le défaut qu'on répare.
    """
    assert grandeurs_non_projetees(sql) == []


# --- ce qui vit dans un littéral ou un commentaire est une donnée -------------
#
# Chacun de ces cas PIÈGE la lecture naïve : sans masquage, le `ORDER BY` caché
# dans le commentaire ou dans la chaîne serait pris pour celui de la requête,
# et on signalerait une grandeur qui n'existe pas.


@pytest.mark.parametrize(
    "sql",
    [
        # Un commentaire de fin de ligne, qui porte un faux ORDER BY.
        "SELECT code, total FROM t ORDER BY total DESC -- puis ORDER BY ruse",
        # Le même, suivi d'autre chose : le masque s'arrête au saut de ligne.
        "SELECT code, total FROM t -- ORDER BY ruse\nORDER BY total DESC",
        # Un commentaire encadré, au milieu de la liste du SELECT.
        "SELECT code, /* ORDER BY ruse */ total FROM t ORDER BY total",
        # Un commentaire encadré jamais refermé : il court jusqu'à la fin.
        "SELECT code, total FROM t ORDER BY total /* ORDER BY ruse",
        # Une apostrophe DOUBLÉE : elle s'échappe elle-même, le littéral continue.
        "SELECT code, total FROM t WHERE nom = 'L''Isle ORDER BY ruse' ORDER BY total",
        # Un littéral jamais refermé : il court jusqu'à la fin, et le moteur
        # rejettera la requête — ce n'est pas à ce module de le faire.
        "SELECT code, total FROM t ORDER BY total, 'ORDER BY ruse",
    ],
)
def test_un_order_by_cache_dans_un_commentaire_ou_une_chaine_est_ignore(sql):
    assert grandeurs_non_projetees(sql) == []


def test_un_select_posterieur_au_order_by_ne_decrit_pas_les_colonnes_rendues():
    """`LIMIT (SELECT 1)` porte un SELECT APRÈS le ORDER BY : ce n'est pas le nôtre.

    On cherche le dernier SELECT qui PRÉCÈDE le classement — celui qui dit ce
    que la requête rend.
    """
    assert (
        grandeurs_non_projetees("SELECT code, total FROM t ORDER BY total LIMIT (SELECT 1)") == []
    )
    assert grandeurs_non_projetees("SELECT code FROM t ORDER BY total LIMIT (SELECT 1)") == [
        "total"
    ]
