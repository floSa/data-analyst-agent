"""Tests de l'oracle de la batterie méta (scripts/mesure_surface_conversationnelle.py).

Un oracle qui déclare FAUSSE une réponse juste ne mesure pas le système : il
mesure sa propre littéralité, et il coûte un aller-retour de diagnostic à chaque
rejeu. C'est arrivé au premier passage sur la base Maxizoo — trois verdicts « à
côté » sur des réponses exactes, parce que l'oracle n'admettait que la forme ISO
d'une date et que le nom d'une colonne de date. D'où ces tests, qui gardent la
tolérance dans les deux sens : ce que l'oracle doit ACCEPTER, et ce qu'il ne doit
pas se mettre à accepter au passage.
"""

import pytest
from mesure_surface_conversationnelle import QuestionMeta, formes_de_date, replie


def test_une_date_est_admise_en_iso_et_en_francais():
    """« le 30 juin 2026 » est la bonne façon de dire 2026-06-30 à un humain."""
    assert formes_de_date("2026-06-30") == ("2026-06-30", "30 juin 2026")


def test_le_premier_du_mois_s_ecrit_aussi_1er():
    formes = formes_de_date("2021-07-01")
    assert formes == ("2021-07-01", "1 juillet 2021", "1er juillet 2021")


def test_une_date_absente_ne_produit_aucune_forme():
    """Une source sans colonne temporelle : l'oracle n'a rien à proposer, et il
    ne doit pas proposer une chaîne vide — qui serait contenue dans tout."""
    assert formes_de_date("") == ()


@pytest.mark.parametrize(
    "reponse",
    [
        "Les données couvrent la période du 1er juillet 2021 au 30 juin 2026.",
        "Du **1er juillet 2021** au **30 juin 2026**.",
        "La colonne `date` de sales_daily porte l'historique.",
    ],
)
def test_les_trois_facons_d_etre_fonde_sur_la_periode_passent(reponse: str):
    """Nommer la colonne, ou donner les bornes : les deux sont des réponses."""
    question = QuestionMeta(
        "periode",
        "période",
        "Sur quelle période portent les données ?",
        attendus_parmi=("date", *formes_de_date("2021-07-01"), *formes_de_date("2026-06-30")),
    )

    assert question.satisfait(reponse)[0]


def test_une_periode_inventee_ne_passe_pas():
    """La tolérance porte sur l'ÉCRITURE d'une date, jamais sur sa valeur."""
    question = QuestionMeta(
        "periode",
        "période",
        "Sur quelle période portent les données ?",
        attendus_parmi=(*formes_de_date("2021-07-01"), *formes_de_date("2026-06-30")),
    )

    ok, manquants = question.satisfait("Les données vont de 2019 à 2023.")
    assert not ok
    assert manquants


def test_l_oracle_compare_du_sens_et_non_de_la_typographie():
    """Le modèle échappe le blanc souligné pour l'affichage : `store\\_id` est
    le nom `store_id`, et une comparaison littérale ne le reconnaîtrait plus."""
    question = QuestionMeta("c", "colonnes", "?", attendus_tous=("store_id", "promo_id"))

    assert question.satisfait("Les colonnes sont `store\\_id` et **promo\\_id**.")[0]


def test_une_colonne_omise_est_dite():
    """Une liste incomplète n'est pas une réponse à « quelles colonnes ? »."""
    question = QuestionMeta("c", "colonnes", "?", attendus_tous=("store_id", "promo_id"))

    ok, manquants = question.satisfait("Il y a `store_id`.")
    assert not ok
    assert manquants == ["promo_id"]


def test_le_repliement_ignore_accents_et_decoration_mais_garde_le_texte():
    """Il compare du sens, pas de la typographie — et rien de plus.

    Il ne touche PAS à la ponctuation : ce qu'il replie, ce sont les trois
    écarts qui ont déjà fait compter faux une réponse juste — la casse, les
    accents que le modèle met un tour sur deux, et la décoration Markdown.
    """
    assert replie("La **Prédiction** de `store\\_id`") == "la prediction de store_id"
