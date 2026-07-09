"""Validation des features : exhaustif sur les cas manquant/bornes/type/inconnu."""

from data_analyst_agent.agents.inference.schemas import (
    SCHEMAS,
    CaliforniaHousingFeatures,
    IrisFeatures,
    TitanicFeatures,
    get_schema,
)
from data_analyst_agent.agents.inference.validation import format_reask, validate_features

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
