"""« Sans famille à bord » : la phrase le dit, les deux compteurs le valent.

Le défaut mesuré : le planificateur rend `parch=0` et omet `sibsp`, la
prédiction ressort `invalid` sur « sibsp : valeur manquante », et le tour
suivant n'a plus la phrase sous la main — le fil est fermé. La réparation ne
touche ni au prompt ni à la fusion de l'acquis : elle réunit deux déclarations,
et c'est ce que ce fichier tient.
"""

import pytest
from pydantic import BaseModel, Field

from data_analyst_agent.agents.inference.accompagnants import (
    absence_daccompagnants,
    champs_daccompagnants,
)
from data_analyst_agent.agents.inference.schemas import SCHEMAS, TitanicFeatures
from data_analyst_agent.agents.inference.schemas.marques import ACCOMPAGNANTS


def test_titanic_declare_ses_deux_compteurs_daccompagnants():
    """La déclaration est dans le SCHÉMA, parce que c'est lui qui sait."""
    assert champs_daccompagnants(TitanicFeatures) == ("sibsp", "parch")


@pytest.mark.parametrize("dataset", ["iris", "california_housing"])
def test_un_schema_qui_ne_declare_rien_n_est_jamais_touche(dataset: str):
    """Pas de repli, pas d'heuristique sur les noms : sans marque, rien.

    Un pétale ne compte pas des personnes. Si la règle devinait, « sans famille
    à bord » mettrait des zéros dans une mesure de fleur.
    """
    assert champs_daccompagnants(SCHEMAS[dataset]) == ()


def test_la_marque_ne_change_ni_la_validation_ni_ce_que_le_modele_lit():
    """La marque voyage dans `json_schema_extra`, et n'en sort pas.

    C'est la condition pour que cette réparation ne coûte rien à la surface
    conversationnelle : le planificateur lit `describe_features`, qui ne rend
    que la description et les valeurs autorisées.
    """
    from data_analyst_agent.agents.inference.schemas import describe_features

    assert ACCOMPAGNANTS not in describe_features(TitanicFeatures)
    valide = TitanicFeatures(
        sex="female", pclass=1, age=28, sibsp=0, parch=0, fare=80, embarked="S"
    )
    assert valide.sibsp == 0


@pytest.mark.parametrize(
    "message",
    [
        "prédis la survie d'une passagère de 1re classe, sans famille à bord",
        "elle voyageait seule",
        "une passagère seule à bord",
        "sans accompagnant",
        "sans aucune famille",
        "non accompagnée",
        "sans personne à bord",
    ],
)
def test_la_construction_est_lue(message: str):
    assert absence_daccompagnants(message)


@pytest.mark.parametrize(
    "message",
    [
        # une valeur pour UN champ : au modèle de l'extraire, pas à la règle de
        # décider pour les deux.
        "sans enfant",
        "sans conjoint",
        "sans parent ni enfant à bord",
        # ce qui ne parle de personne du tout
        "une femme",
        "oui",
        "dans la source ventes, combien de commandes par canal de vente ?",
        "reprends le tableau précédent et donne-moi les pourcentages",
        "combien de familles dans la base ?",
    ],
)
def test_la_construction_n_est_pas_lue_ailleurs(message: str):
    assert not absence_daccompagnants(message)


def test_la_marque_se_lit_sur_n_importe_quel_schema_qui_la_pose():
    """La déclaration est un contrat, pas un cas particulier de titanic."""

    class Compagnie(BaseModel):
        seuls: int = Field(json_schema_extra={ACCOMPAGNANTS: True})
        autre: int = Field()

    assert champs_daccompagnants(Compagnie) == ("seuls",)


# --- retirer la clause pour la reposer -------------------------------------------


@pytest.mark.parametrize(
    ("message", "attendu"),
    [
        (
            "prédis la survie d'une passagère de 28 ans, tarif 80 livres, sans famille à bord",
            "prédis la survie d'une passagère de 28 ans, tarif 80 livres",
        ),
        (
            "prédis la survie d'une passagère, sans famille à bord, billet à 80 livres",
            "prédis la survie d'une passagère, billet à 80 livres",
        ),
    ],
)
def test_la_clause_se_retire_quand_elle_est_un_segment(message: str, attendu: str):
    from data_analyst_agent.agents.inference.accompagnants import sans_la_clause_dabsence

    assert sans_la_clause_dabsence(message) == attendu


@pytest.mark.parametrize(
    "message",
    [
        # la clause est le FILTRE d'une requête : la retirer changerait la question
        "combien de passagers sans famille à bord ?",
        # enchâssée dans une proposition : la retirer réécrirait la phrase
        "une femme de 28 ans qui voyageait seule",
        # elle n'y est pas
        "prédis la survie de cette dame de 28 ans",
    ],
)
def test_rien_n_est_retire_quand_la_clause_n_est_pas_isolable(message: str):
    """On ne réécrit pas la phrase de quelqu'un pour la lui reposer."""
    from data_analyst_agent.agents.inference.accompagnants import sans_la_clause_dabsence

    assert sans_la_clause_dabsence(message) == message
