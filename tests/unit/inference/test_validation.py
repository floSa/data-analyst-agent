"""Validation des features : exhaustif sur les cas manquant/bornes/type/inconnu."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from data_analyst_agent.agents.inference.schemas import (
    SCHEMAS,
    CaliforniaHousingFeatures,
    IrisFeatures,
    TitanicFeatures,
    describe_features,
    field_choices,
    get_schema,
)
from data_analyst_agent.agents.inference.validation import (
    coerce_values,
    format_reask,
    validate_features,
)

TITANIC_OK = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}


def test_payload_valide():
    outcome = validate_features(TitanicFeatures, TITANIC_OK)
    assert outcome.valid
    assert outcome.features == TITANIC_OK
    assert outcome.issues == []


def test_coercion_douce_des_types():
    # un LLM extrait souvent des chaînes : "28" doit passer pour un float
    outcome = validate_features(TitanicFeatures, {**TITANIC_OK, "age": "28"})
    assert outcome.valid
    assert outcome.features["age"] == 28.0


def test_champs_manquants_listes():
    outcome = validate_features(TitanicFeatures, {"sex": "female", "pclass": 1})
    assert not outcome.valid
    assert set(outcome.missing_fields) == {"age", "sibsp", "parch", "fare", "embarked"}
    assert all(i.problem == "manquant" for i in outcome.issues)
    # la description du champ est reprise dans le message
    age_issue = next(i for i in outcome.issues if i.field == "age")
    assert "Âge" in age_issue.message


def test_hors_bornes():
    outcome = validate_features(TitanicFeatures, {**TITANIC_OK, "age": 250})
    assert not outcome.valid
    assert outcome.issues[0].problem == "hors_bornes"
    assert outcome.issues[0].field == "age"


def test_valeur_non_autorisee():
    outcome = validate_features(TitanicFeatures, {**TITANIC_OK, "sex": "autre"})
    assert not outcome.valid
    assert outcome.issues[0].problem == "valeur_non_autorisee"


def test_type_invalide():
    outcome = validate_features(TitanicFeatures, {**TITANIC_OK, "age": "vingt-huit"})
    assert not outcome.valid
    assert outcome.issues[0].problem == "type_invalide"


def test_champ_inconnu_refuse():
    outcome = validate_features(TitanicFeatures, {**TITANIC_OK, "cabine": "C85"})
    assert not outcome.valid
    assert outcome.issues[0].problem == "champ_inconnu"
    assert outcome.issues[0].field == "cabine"


def test_plusieurs_problemes_en_une_passe():
    outcome = validate_features(TitanicFeatures, {"sex": "x", "age": -3})
    problems = {i.field: i.problem for i in outcome.issues}
    assert problems["sex"] == "valeur_non_autorisee"
    assert problems["age"] == "hors_bornes"
    assert problems["pclass"] == "manquant"


def test_format_reask():
    outcome = validate_features(TitanicFeatures, {"sex": "female"})
    question = format_reask("titanic", outcome.issues)
    assert "titanic" in question
    assert "pclass" in question
    assert question.strip().endswith("?")


def test_schemas_iris_et_california():
    assert validate_features(
        IrisFeatures,
        {"sepal_length": 5.1, "sepal_width": 3.5, "petal_length": 1.4, "petal_width": 0.2},
    ).valid
    assert not validate_features(CaliforniaHousingFeatures, {"latitude": 50}).valid


def test_get_schema():
    assert get_schema("titanic") is TitanicFeatures
    assert set(SCHEMAS) == {"titanic", "iris", "california_housing"}
    import pytest

    with pytest.raises(KeyError, match="connus"):
        get_schema("boston")


# -- réalignement des noms de features (« MedInc » vs « med_inc ») ----------------


def test_features_en_camel_case_sont_realignees():
    """« MedInc » est le nom canonique sklearn du dataset : il doit être accepté."""
    payload = {
        "MedInc": 8.3,
        "HouseAge": 41,
        "AveRooms": 6.9,
        "AveBedrms": 1.02,
        "Population": 322,
        "AveOccup": 2.5,
        "Latitude": 37.88,
        "Longitude": -122.23,
    }
    outcome = validate_features(get_schema("california_housing"), payload)

    assert outcome.valid, outcome.issues
    assert outcome.features["med_inc"] == 8.3
    assert outcome.features["house_age"] == 41


def test_realignement_tolere_casse_et_underscores():
    outcome = validate_features(
        get_schema("iris"),
        {"Sepal_Length": 5.1, "sepalwidth": 3.5, "PETAL_LENGTH": 1.4, "petal width": 0.2},
    )
    assert outcome.valid, outcome.issues
    assert outcome.features["sepal_length"] == 5.1
    assert outcome.features["petal_width"] == 0.2


def test_nom_exact_prime_sur_un_alias():
    outcome = validate_features(
        get_schema("california_housing"),
        {
            "med_inc": 8.3,
            "MedInc": 1.0,  # alias concurrent : le nom exact doit gagner
            "house_age": 41,
            "ave_rooms": 6.9,
            "ave_bedrms": 1.02,
            "population": 322,
            "ave_occup": 2.5,
            "latitude": 37.88,
            "longitude": -122.23,
        },
    )
    assert outcome.valid, outcome.issues
    assert outcome.features["med_inc"] == 8.3


def test_champ_vraiment_inconnu_reste_signale():
    """Le réalignement ne doit pas transformer le garde-fou en passoire."""
    outcome = validate_features(get_schema("iris"), {"couleur_petale": "bleu"})

    assert not outcome.valid
    assert any(i.problem == "champ_inconnu" and i.field == "couleur_petale" for i in outcome.issues)


# -- arguments d'outil rendus en chaînes (vLLM vs Ollama) ------------------------

# Ce que le planificateur a réellement rendu, servi par vLLM, pour « Prédis la
# survie d'une passagère de 1re classe de 28 ans, tarif 80 livres, embarquée à
# Southampton, sans frère, sœur, parent ni enfant à bord. » — l'extraction est
# juste, TOUTES les valeurs sont des chaînes. Le même modèle servi par Ollama
# rend `pclass=1`, `age=28`, `fare=80`, `sibsp=0`, `parch=0`.
TITANIC_VLLM = {
    "sex": "female",
    "pclass": "1",
    "age": "28",
    "sibsp": "0",
    "parch": "0",
    "fare": "80",
    "embarked": "S",
}


def test_prediction_complete_en_chaines_aboutit():
    """Le moteur ne décide pas si une prédiction aboutit.

    Refusée sous vLLM et acceptée sous Ollama, pour la même question et le même
    modèle : c'est `Literal[1, 2, 3]` qui compare des valeurs, et pour qui '1'
    n'est pas 1. Pydantic rattrapait déjà les `int`/`float` en mode souple.
    """
    outcome = validate_features(TitanicFeatures, TITANIC_VLLM)

    assert outcome.valid, outcome.issues
    assert outcome.features == TITANIC_OK


def test_les_trois_schemas_encaissent_un_payload_tout_en_chaines():
    """L'étendue mesurée : vLLM rend en chaînes TOUS les champs numériques."""
    iris = validate_features(
        IrisFeatures,
        {"sepal_length": "5.1", "sepal_width": "3.5", "petal_length": "1.4", "petal_width": "0.2"},
    )
    assert iris.valid, iris.issues
    assert iris.features == {
        "sepal_length": 5.1,
        "sepal_width": 3.5,
        "petal_length": 1.4,
        "petal_width": 0.2,
    }

    california = validate_features(
        CaliforniaHousingFeatures,
        {
            "med_inc": "8.3",
            "house_age": "41",
            "ave_rooms": "6.98",
            "ave_bedrms": "1.02",
            "population": "322",
            "ave_occup": "2.56",
            "latitude": "37.88",
            "longitude": "-122.23",
        },
    )
    assert california.valid, california.issues
    assert california.features["longitude"] == -122.23
    assert california.features["population"] == 322.0


def test_valeur_hors_bornes_en_chaine_reste_refusee():
    """La coercition n'est pas un relâchement : '4' devient 4, et 4 est refusé."""
    outcome = validate_features(TitanicFeatures, {**TITANIC_VLLM, "pclass": "4"})

    assert not outcome.valid
    issue = next(i for i in outcome.issues if i.field == "pclass")
    assert issue.problem == "valeur_non_autorisee"
    assert "Input should be 1, 2 or 3" in issue.message


def test_valeur_non_convertible_reste_refusee_en_citant_ce_qui_a_ete_ecrit():
    """Illisible => laissée telle quelle : le schéma refuse, et cite la chaîne.

    C'est le cas réel de `fetch_then_predict`, où la source rend le libellé
    humain « 3e classe » : il doit rester une erreur de validation lisible, pas
    devenir un silence.
    """
    for valeur in ("abc", "3e classe", ""):
        outcome = validate_features(TitanicFeatures, {**TITANIC_VLLM, "pclass": valeur})

        assert not outcome.valid, valeur
        issue = next(i for i in outcome.issues if i.field == "pclass")
        assert repr(valeur) in issue.message

    outcome = validate_features(TitanicFeatures, {**TITANIC_VLLM, "age": "douze"})
    assert not outcome.valid
    assert "'douze'" in next(i for i in outcome.issues if i.field == "age").message


def test_les_bornes_valent_aussi_sur_une_chaine():
    outcome = validate_features(TitanicFeatures, {**TITANIC_VLLM, "age": "150"})

    assert not outcome.valid
    issue = next(i for i in outcome.issues if i.field == "age")
    assert issue.problem == "hors_bornes"


def test_coercition_ne_touche_ni_aux_valeurs_deja_typees_ni_aux_champs_inconnus():
    payload = {"pclass": 2, "age": 30.5, "sex": "male", "couleur_du_billet": "42"}

    assert coerce_values(TitanicFeatures, payload) == payload


def test_coercition_ignore_un_literal_de_chaines():
    """`Literal['S', 'C', 'Q']` attend une str : rien à convertir, rien à casser."""
    converti = coerce_values(TitanicFeatures, {"embarked": "S", "sex": "female"})

    assert converti == {"embarked": "S", "sex": "female"}


def test_coercition_lit_un_booleen_sans_passer_par_bool():
    """`bool('false')` vaut True — d'où une table explicite plutôt qu'un appel."""

    class AvecBooleen(BaseModel):
        actif: bool
        drapeau: Literal[True, False]

    assert coerce_values(AvecBooleen, {"actif": "false", "drapeau": "0"}) == {
        "actif": False,
        "drapeau": False,
    }
    assert coerce_values(AvecBooleen, {"actif": " VRAI "}) == {"actif": True}
    # illisible : laissé tel quel, le schéma tranchera
    assert coerce_values(AvecBooleen, {"actif": "peut-être"}) == {"actif": "peut-être"}


def test_coercition_deplie_l_optionnel_et_se_tait_sur_l_ambigu():
    class Melange(BaseModel):
        seuil: int | None = None
        panache: Literal[1, "un"] = 1
        indecis: int | str = 0

    converti = coerce_values(Melange, {"seuil": "7", "panache": "1", "indecis": "3"})

    assert converti["seuil"] == 7
    # aucun type attendu sans ambiguïté : on ne touche à rien
    assert converti["panache"] == "1"
    assert converti["indecis"] == "3"


def test_coercition_laisse_passer_un_type_qu_elle_ne_sait_pas_lire():
    """Hors booléen et nombre, la conversion s'abstient : au schéma de trancher."""

    class AvecDate(BaseModel):
        jour: date

    assert coerce_values(AvecDate, {"jour": "1912-04-15"}) == {"jour": "1912-04-15"}
    assert validate_features(AvecDate, {"jour": "1912-04-15"}).valid


# -- description des features pour le planificateur ------------------------------


def test_describe_features_donne_sens_et_valeurs_autorisees():
    """Le planificateur doit pouvoir traduire « Southampton » en 'S' : sans le sens
    ni les valeurs du champ, il redemande une information déjà donnée."""
    texte = describe_features(get_schema("titanic"))

    assert "Port d'embarquement (S=Southampton, C=Cherbourg, Q=Queenstown)" in texte
    assert "valeurs autorisées : 'S', 'C', 'Q'" in texte
    assert "valeurs autorisées : 'male', 'female'" in texte
    assert "* age — Âge en années" in texte


def test_describe_features_sans_literal_liste_pas_de_valeurs():
    texte = describe_features(get_schema("california_housing"))

    assert "* med_inc — Revenu médian (dizaines de milliers de $)" in texte
    assert "valeurs autorisées" not in texte  # que des flottants bornés


def test_field_choices_ne_rend_que_les_literal():
    assert field_choices(get_schema("titanic"), "embarked") == ["S", "C", "Q"]
    assert field_choices(get_schema("titanic"), "age") is None
    assert field_choices(get_schema("titanic"), "inexistant") is None
