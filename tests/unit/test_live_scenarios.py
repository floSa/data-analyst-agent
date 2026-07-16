"""Tests du vérificateur de données de la batterie live (scripts/live_scenarios.py).

La batterie live vérifie le VRAI système ; son garde-fou le plus important est
``check_data``, qui compare les chiffres rendus à une vérité terrain calculée
hors de l'agent. Un vérificateur trop tolérant est pire que pas de vérificateur :
il transforme une réponse fausse en succès vert. D'où ces tests, qui vérifient
surtout ce qu'il doit REJETER.
"""

import json

import pytest
from live_scenarios import Turn, check_data


def reponse(answer: str, table: dict | None = None) -> dict:
    artifacts = []
    if table is not None:
        artifacts.append({"mime": "application/json", "data": json.dumps(table)})
    return {"answer": answer, "artifacts": artifacts}


def test_valeur_juste_acceptee():
    assert check_data(Turn("q", expect_data=[60]), reponse("Il y a 60 SKU.")) == []


@pytest.mark.parametrize("faux", [59, 61, 600])
def test_valeur_fausse_rejetee(faux: int):
    """Le cas qui compte : un comptage faux ne doit jamais passer pour un arrondi."""
    problemes = check_data(Turn("q", expect_data=[60]), reponse(f"Il y a {faux} SKU."))
    assert problemes, f"{faux} accepté à la place de 60"


def test_format_francais_accepte():
    assert check_data(Turn("q", expect_data=[62.96]), reponse("Taux : 62,96 %")) == []


def test_separateur_de_milliers_accepte():
    assert check_data(Turn("q", expect_data=[1234]), reponse("total de 1 234 lignes")) == []


def test_arrondi_a_la_precision_affichee_accepte():
    """« environ 63 % » est un arrondi juste de 62.96 : on juge la valeur, pas la forme."""
    assert check_data(Turn("q", expect_data=[62.96]), reponse("Taux : environ 63 %")) == []


def test_troncature_rejetee():
    """62.9 n'est pas un arrondi de 62.96 — c'est une valeur différente."""
    assert check_data(Turn("q", expect_data=[62.96]), reponse("Taux : 62.9 %"))


def test_valeur_cherchee_aussi_dans_le_tableau():
    """Le chiffre vit souvent dans l'artefact, pas dans la phrase de synthèse."""
    table = {"columns": ["sex", "n"], "rows": [["female", 314], ["male", 577]]}
    assert check_data(Turn("q", expect_data=[314, 577]), reponse("voir tableau", table)) == []


def test_chaine_attendue_absente_rejetee():
    assert check_data(Turn("q", expect_data=["1re classe"]), reponse("réparti par classe"))


def test_chaine_attendue_presente_acceptee():
    assert check_data(Turn("q", expect_data=["1re classe"]), reponse("La 1re classe...")) == []


def test_sans_attente_ne_verifie_rien():
    assert check_data(Turn("q"), reponse("n'importe quoi")) == []


def test_tableau_illisible_ne_fait_pas_tomber_le_verificateur():
    data = {
        "answer": "60 SKU",
        "artifacts": [{"mime": "application/json", "data": "{cassé"}],
    }
    assert check_data(Turn("q", expect_data=[60]), data) == []


def test_valeur_pleine_precision_acceptee_face_a_une_reference_arrondie():
    """Le piège inverse : le tableau rend 62.96296296296296, la référence notée à
    la main est 62.96. C'est la MÊME valeur — la rejeter accuse à tort l'agent."""
    table = {"columns": ["label", "rate"], "rows": [["1re classe", 62.96296296296296]]}
    assert check_data(Turn("q", expect_data=[62.96]), reponse("voir tableau", table)) == []


def test_valeur_pleine_precision_mais_fausse_reste_rejetee():
    """La tolérance d'arrondi ne doit pas devenir un passe-droit."""
    table = {"columns": ["label", "rate"], "rows": [["1re classe", 59.11111111]]}
    assert check_data(Turn("q", expect_data=[62.96]), reponse("voir tableau", table))


def test_alternatives_taux_fraction_ou_pourcentage():
    """« taux de survie » est aussi juste en 0.742 qu'en 74.2 : les deux passent."""
    turn = Turn("q", expect_data=[(74.2, 0.742)])
    assert check_data(turn, reponse("taux : 0.7420382165605095")) == []
    assert check_data(turn, reponse("taux : 74,2 %")) == []
    assert check_data(turn, reponse("taux : 0.31"))  # ni l'un ni l'autre


def test_pas_de_faux_positif_par_coincidence_dans_un_grand_tableau():
    """Le piège qui m'a eu : un tableau de 94 passagères porte ~1200 nombres. Si un
    passenger_id vaut 97, il ne doit PAS valider un taux attendu de 96.81 sous
    prétexte que 97 est un arrondi plausible. Un tableau n'arrondit pas."""
    table = {
        "columns": ["passenger_id", "age", "fare"],
        "rows": [[97, 38.0, 71.2833], [98, 35.0, 53.1]],
    }
    problemes = check_data(
        Turn("q", expect_data=[(96.81, 0.9681)]), reponse("94 lignes retournées", table)
    )
    assert problemes, "97 accepté comme 96.81 : le vérificateur ment"


def test_valeur_juste_dans_un_tableau_reste_acceptee():
    """La règle stricte ne doit pas rejeter une vraie valeur pleine précision."""
    table = {"columns": ["label", "rate"], "rows": [["1re classe", 96.8085106382979]]}
    assert check_data(Turn("q", expect_data=[96.81]), reponse("voir tableau", table)) == []


def test_arrondi_du_llm_accepte_dans_la_phrase():
    """Dans la RÉPONSE rédigée, « environ 63 % » reste un rendu juste de 62.96."""
    assert check_data(Turn("q", expect_data=[62.96]), reponse("environ 63 %")) == []
    assert check_data(Turn("q", expect_data=[5.8433]), reponse("environ 5,84 unités")) == []


def test_arrondi_trop_grossier_ne_prouve_rien():
    """Le « 1 » de « 1re classe » ne doit pas valider un taux attendu de 0.9681 :
    un arrondi à ±0.5 sur une valeur de 0.97 ne discrimine plus rien."""
    answer = "Pour les femmes de 1re classe, précisez ce que vous voulez obtenir."
    assert check_data(Turn("q", expect_data=[(96.81, 0.9681)]), reponse(answer))


def test_fraction_juste_reste_acceptee():
    """La garde anti-arrondi-grossier ne doit pas rejeter une vraie fraction."""
    turn = Turn("q", expect_data=[(96.81, 0.9681)])
    assert check_data(turn, reponse("taux : 0.9681")) == []
    assert check_data(turn, reponse("taux : 96,81 %")) == []
