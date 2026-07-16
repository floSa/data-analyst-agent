"""Validation des features : exhaustif sur les cas manquant/bornes/type/inconnu."""

import pytest

from data_analyst_agent.agents.inference.schemas import (
    SCHEMAS,
    MaxizooSalesFeatures,
    describe_features,
    field_choices,
    get_schema,
)
from data_analyst_agent.agents.inference.validation import format_reask, validate_features

VENTES_OK = {
    "store_type": "grand",
    "commodity_group": "Chien",
    "brand_type": "nationale",
    "base_price": 49.90,
    "day_of_week": 5,
    "month": 11,
    "discount_rate": 0.30,
    "promo_type": "produits",
    "temp_anomaly": 0.0,
}


def test_payload_valide():
    outcome = validate_features(MaxizooSalesFeatures, VENTES_OK)
    assert outcome.valid
    assert outcome.features == VENTES_OK
    assert outcome.issues == []


def test_coercion_douce_des_types():
    # un LLM extrait souvent des chaînes : "49.9" doit passer pour un float
    outcome = validate_features(MaxizooSalesFeatures, {**VENTES_OK, "base_price": "49.9"})
    assert outcome.valid
    assert outcome.features["base_price"] == 49.9


def test_champs_manquants_listes():
    outcome = validate_features(
        MaxizooSalesFeatures, {"store_type": "grand", "commodity_group": "Chien"}
    )
    assert not outcome.valid
    assert set(outcome.missing_fields) == {
        "brand_type",
        "base_price",
        "day_of_week",
        "month",
        "discount_rate",
        "promo_type",
        "temp_anomaly",
    }
    assert all(i.problem == "manquant" for i in outcome.issues)
    # la description du champ est reprise dans le message
    jour = next(i for i in outcome.issues if i.field == "day_of_week")
    assert "0 = lundi" in jour.message


def test_hors_bornes():
    outcome = validate_features(MaxizooSalesFeatures, {**VENTES_OK, "day_of_week": 7})
    assert not outcome.valid
    assert outcome.issues[0].problem == "hors_bornes"
    assert outcome.issues[0].field == "day_of_week"


def test_remise_superieure_a_cent_pour_cent_refusee():
    outcome = validate_features(MaxizooSalesFeatures, {**VENTES_OK, "discount_rate": 1.5})
    assert not outcome.valid
    assert outcome.issues[0].problem == "hors_bornes"


def test_valeur_non_autorisee():
    outcome = validate_features(MaxizooSalesFeatures, {**VENTES_OK, "commodity_group": "Dragon"})
    assert not outcome.valid
    assert outcome.issues[0].problem == "valeur_non_autorisee"


def test_type_invalide():
    outcome = validate_features(MaxizooSalesFeatures, {**VENTES_OK, "base_price": "quarante"})
    assert not outcome.valid
    assert outcome.issues[0].problem == "type_invalide"


def test_champ_inconnu_refuse():
    """`sku_id` est justement ce que le modèle ne prend PAS : il doit être refusé."""
    outcome = validate_features(MaxizooSalesFeatures, {**VENTES_OK, "sku_id": "SKU001"})
    assert not outcome.valid
    assert outcome.issues[0].problem == "champ_inconnu"
    assert outcome.issues[0].field == "sku_id"


def test_plusieurs_problemes_en_une_passe():
    outcome = validate_features(MaxizooSalesFeatures, {"store_type": "gigantesque", "month": 13})
    problems = {i.field: i.problem for i in outcome.issues}
    assert problems["store_type"] == "valeur_non_autorisee"
    assert problems["month"] == "hors_bornes"
    assert problems["commodity_group"] == "manquant"


def test_format_reask():
    outcome = validate_features(MaxizooSalesFeatures, {"store_type": "grand"})
    question = format_reask("maxizoo_sales", outcome.issues)
    assert "maxizoo_sales" in question
    assert "commodity_group" in question
    assert question.strip().endswith("?")


def test_hors_campagne_est_une_valeur_valide():
    """Le cas majoritaire — pas de promo ce jour-là — doit se dire, pas s'omettre."""
    outcome = validate_features(
        MaxizooSalesFeatures, {**VENTES_OK, "promo_type": "aucune", "discount_rate": 0.0}
    )
    assert outcome.valid


def test_get_schema():
    assert get_schema("maxizoo_sales") is MaxizooSalesFeatures
    assert set(SCHEMAS) == {"maxizoo_sales"}

    with pytest.raises(KeyError, match="connus"):
        get_schema("titanic")


# -- réalignement des noms de features (« StoreType » vs « store_type ») ----------


def test_features_en_camel_case_sont_realignees():
    """Un LLM recopie souvent le nom d'usage : « StoreType » doit être accepté."""
    payload = {
        "StoreType": "grand",
        "CommodityGroup": "Chien",
        "BrandType": "nationale",
        "BasePrice": 49.90,
        "DayOfWeek": 5,
        "Month": 11,
        "DiscountRate": 0.30,
        "PromoType": "produits",
        "TempAnomaly": 0.0,
    }
    outcome = validate_features(get_schema("maxizoo_sales"), payload)

    assert outcome.valid, outcome.issues
    assert outcome.features["store_type"] == "grand"
    assert outcome.features["base_price"] == 49.90


def test_realignement_tolere_casse_et_underscores():
    outcome = validate_features(
        get_schema("maxizoo_sales"),
        {**{k: v for k, v in VENTES_OK.items() if k != "base_price"}, "Base Price": 49.90},
    )
    assert outcome.valid, outcome.issues
    assert outcome.features["base_price"] == 49.90


def test_nom_exact_prime_sur_un_alias():
    outcome = validate_features(
        get_schema("maxizoo_sales"),
        {**VENTES_OK, "BasePrice": 1.0},  # alias concurrent : le nom exact doit gagner
    )
    assert outcome.valid, outcome.issues
    assert outcome.features["base_price"] == 49.90


def test_champ_vraiment_inconnu_reste_signale():
    """Le réalignement ne doit pas transformer le garde-fou en passoire."""
    outcome = validate_features(get_schema("maxizoo_sales"), {"couleur_emballage": "bleu"})

    assert not outcome.valid
    assert any(
        i.problem == "champ_inconnu" and i.field == "couleur_emballage" for i in outcome.issues
    )


# -- description des features pour le planificateur ------------------------------


def test_describe_features_donne_sens_et_valeurs_autorisees():
    """Le planificateur doit pouvoir traduire « en Black Friday » en
    promo_type='produits' : sans le sens ni les valeurs du champ, il redemande
    une information déjà donnée."""
    texte = describe_features(get_schema("maxizoo_sales"))

    assert "Univers produit" in texte
    assert "valeurs autorisées : 'grand', 'moyen', 'petit', 'online'" in texte
    assert "'aucune'" in texte  # le hors-campagne est montré comme une option
    assert "0 = lundi" in texte


def test_describe_features_un_champ_libre_ne_liste_pas_de_valeurs():
    """`temp_anomaly` est un flottant borné : il n'a pas de liste de valeurs."""
    ligne = next(
        ligne
        for ligne in describe_features(get_schema("maxizoo_sales")).splitlines()
        if ligne.strip().startswith("* temp_anomaly")
    )
    assert "valeurs autorisées" not in ligne
    assert "Écart de température" in ligne


def test_field_choices_ne_rend_que_les_literal():
    assert field_choices(get_schema("maxizoo_sales"), "brand_type") == [
        "nationale",
        "exclusive",
        "distributeur",
    ]
    assert field_choices(get_schema("maxizoo_sales"), "base_price") is None
    assert field_choices(get_schema("maxizoo_sales"), "inexistant") is None
