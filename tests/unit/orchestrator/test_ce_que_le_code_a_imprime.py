"""Le chemin d'ANALYSE rend enfin les chiffres qu'il a calculés.

Relevé du 2026-09-23, catalogue métier, six croisements passés par l'analyse :
le code s'exécute au premier essai, joint les deux sources sur `code_produit`,
imprime son tableau — et l'utilisateur reçoit une phrase sans un seul des
chiffres. Le chemin SQL, lui, sert ses lignes en artefact ; celui-ci n'avait pas
d'équivalent.
"""

from data_analyst_agent.orchestrator.graph import Orchestrator

IMPRIME = Orchestrator._avec_ce_que_le_code_a_imprime


def test_ce_que_le_code_a_imprime_suit_la_phrase():
    """Les deux, dans cet ordre : la lecture d'abord, la preuve ensuite."""
    rendu = IMPRIME("Les ventes dépassent la production.", "VEL-01 689 123\nVEL-04 727 125\n")

    assert rendu.startswith("Les ventes dépassent la production.")
    assert "689" in rendu
    assert "727" in rendu


def test_un_code_muet_ne_rajoute_rien():
    """Une analyse qui ne trace qu'une figure garde la réponse d'avant, au caractère près."""
    assert IMPRIME("Voir la figure ci-jointe.", "   \n  ") == "Voir la figure ci-jointe."


def test_on_ne_resert_pas_ce_que_la_phrase_porte_deja():
    """Même règle que le pied du dictionnaire : pas deux fois le même paragraphe."""
    phrase = "Le total est de 4413 unités."

    assert IMPRIME(phrase, "Le total est de 4413 unités.") == phrase


def test_une_sortie_trop_longue_est_coupee_et_le_dit():
    """Une boucle bavarde ne remplit pas la fenêtre de chat — et la coupe s'annonce.

    Se taire sur la coupe serait le défaut qu'on répare partout ailleurs :
    un extrait servi comme un tout.
    """
    rendu = IMPRIME("Résumé.", "x" * (Orchestrator._IMPRIME_MAX_CARACTERES + 500))

    assert "sortie coupée" in rendu
    assert len(rendu) < Orchestrator._IMPRIME_MAX_CARACTERES + 300
