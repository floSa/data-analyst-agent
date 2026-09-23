"""Le récit de la boucle interne, retiré de ce qui est servi — et ce qu'on ne retire pas.

Le défaut est relevé : une réponse JUSTE, chiffres compris, qui s'ouvre par
« Je m'excuse pour la confusion. J'ai déjà exécuté les deux requêtes nécessaires
dans mes étapes précédentes. » Ces tests gardent les deux faces du remède — ce
qui part, et surtout ce qui reste.
"""

import pytest

from data_analyst_agent.orchestrator.recit import sans_le_recit_des_etapes

PARASITE = (
    "Je m'excuse pour la confusion. J'ai déjà exécuté les deux requêtes "
    "nécessaires dans mes étapes précédentes. On vend 1828 unités et on "
    "produit 4413 unités. On produit plus que ce qu'on vend."
)


def test_le_recit_de_tete_part_et_les_chiffres_restent():
    """La phrase relevée le 2026-09-23, et ce qu'il doit en rester."""
    rendu = sans_le_recit_des_etapes(PARASITE)

    assert (
        rendu == "On vend 1828 unités et on produit 4413 unités. On produit plus que ce qu'on vend."
    )
    assert "excuse" not in rendu
    assert "étapes précédentes" not in rendu


def test_une_phrase_qui_porte_un_chiffre_reste_meme_si_elle_raconte():
    """Un chiffre est un fait, et on ne coupe jamais un fait.

    C'est le garde-fou qui décide entre « retirer une forme » et « recomposer
    la réponse ». Une phrase qui raconte sa requête ET porte le chiffre porte la
    réponse : la couper rendrait un texte plus propre et moins vrai.
    """
    texte = "J'ai exécuté une requête qui rend 1828 unités vendues. C'est le total."

    assert sans_le_recit_des_etapes(texte) == texte


@pytest.mark.parametrize(
    "texte",
    [
        "Le chiffre d'affaires 2025 est de 1 496 743 euros.",
        "Aucune ligne ne correspond : la requête n'a rien retourné.",
        "Les deux requêtes nécessaires ont été exécutées.",
        "La table produits compte douze lignes.",
    ],
)
def test_une_reponse_qui_ne_raconte_rien_ressort_au_caractere_pres(texte: str):
    """L'écrasante majorité des tours : rien à couper, rien de coupé.

    Le troisième cas est celui qui distingue le marqueur : « les deux requêtes
    ont été exécutées » décrit le TRAVAIL, pas le narrateur. Sans première
    personne, on ne coupe pas — une description du travail est légitime, et un
    filtre qui l'emporterait mangerait des réponses.
    """
    assert sans_le_recit_des_etapes(texte) == texte


def test_un_texte_entierement_recit_est_rendu_intact():
    """Mieux vaut une réponse maladroite qu'une réponse vide.

    Si tout est du récit, c'est que le tour n'a rien d'autre à dire : le rendre
    vide remplacerait un défaut de forme par une perte sèche.
    """
    texte = "Je m'excuse pour la confusion. J'ai déjà exécuté les requêtes nécessaires."

    assert sans_le_recit_des_etapes(texte) == texte


def test_on_ne_coupe_qu_en_tete():
    """La coupe s'arrête à la première phrase qui n'est pas du récit.

    Couper au milieu reviendrait à recomposer le texte du modèle : une phrase de
    récit qui suit un fait le commente, et le lien entre les deux nous échappe.
    """
    texte = "Le total est de 4413 unités. Je l'ai obtenu par une requête précédente."

    assert sans_le_recit_des_etapes(texte) == texte
