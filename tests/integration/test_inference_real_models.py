"""Intégration inférence : le VRAI artefact entraîné par notebooks/.

Pas de Docker requis — mais on charge le joblib committé et on vérifie que les
prédictions sont cohérentes et déterministes.
"""

from pathlib import Path

import pytest

from data_analyst_agent.agents.inference.predict import run_inference
from data_analyst_agent.agents.inference.registry import Registry
from helpers.maxizoo import features_frame

pytestmark = pytest.mark.integration

REGISTRY_PATH = Path(__file__).parents[2] / "models" / "registry.yaml"

# Un samedi de novembre, grande surface, croquettes chien de marque nationale.
GOLDEN_VENTE = {
    "store_type": "grand",
    "commodity_group": "Chien",
    "brand_type": "nationale",
    "base_price": 49.90,
    "day_of_week": 5,
    "month": 11,
    "discount_rate": 0.0,
    "promo_type": "aucune",
    "temp_anomaly": 0.0,
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REGISTRY_PATH)


def test_prevision_de_vente_plausible(registry: Registry):
    outcome = run_inference("maxizoo_sales", GOLDEN_VENTE, registry=registry)
    assert outcome.status == "ok"
    prediction = outcome.prediction
    # Une quantité ne peut pas être négative, et la moyenne de la base est ~2,17
    # unités : une prévision à deux chiffres trahirait un modèle déréglé.
    assert 0 <= prediction.value < 10
    assert prediction.unit.startswith("unités vendues")
    assert prediction.probabilities is None  # régression : pas de classes


def test_la_promo_fait_monter_la_prevision_sur_la_population(registry: Registry):
    """L'effet promo appris par le modèle, mesuré sur les 3 666 lignes de l'échantillon.

    **Sur la population, pas en un point.** Comparer deux prédictions ponctuelles
    donne ici le signe INVERSE de l'effet moyen (2,31 en promo contre 2,41 sans,
    sur la ligne de référence) : au grain SKU x magasin x jour, la variance
    dépasse largement l'effet. Ce test-là aurait été rouge sans que rien ne soit
    cassé — et un test qui échoue sur du sain finit désactivé.

    Uplift attendu ~x1,5, cohérent avec le x1,6 mesuré sur la base complète.
    """
    modele = registry.model("maxizoo_sales")
    lignes = features_frame()

    sans = modele.predict(lignes.assign(discount_rate=0.0, promo_type="aucune")).mean()
    avec = modele.predict(lignes.assign(discount_rate=0.30, promo_type="produits")).mean()

    assert 1.3 < avec / sans < 1.8


def test_features_incompletes_redemande(registry: Registry):
    outcome = run_inference(
        "maxizoo_sales", {"store_type": "grand", "commodity_group": "Chien"}, registry=registry
    )
    assert outcome.status == "invalid"
    assert outcome.prediction is None
    assert {i.field for i in outcome.issues} == {
        "brand_type",
        "base_price",
        "day_of_week",
        "month",
        "discount_rate",
        "promo_type",
        "temp_anomaly",
    }
    assert "base_price" in outcome.reask


def test_sku_jamais_vu_se_predit_quand_meme(registry: Registry):
    """Le cold start du dictionnaire (piège n°3) : un SKU neuf n'a pas d'historique.

    Le modèle ne prenant que des ATTRIBUTS, un produit qui n'existait pas à
    l'entraînement se prédit sans réentraîner. C'est la raison d'être de ce
    choix de features — ce test en est la démonstration.
    """
    nouveau_produit = {**GOLDEN_VENTE, "base_price": 189.0, "commodity_group": "Reptile"}
    outcome = run_inference("maxizoo_sales", nouveau_produit, registry=registry)
    assert outcome.status == "ok"
    assert outcome.prediction.value >= 0


def test_determinisme(registry: Registry):
    first = run_inference("maxizoo_sales", GOLDEN_VENTE, registry=registry)
    second = run_inference("maxizoo_sales", GOLDEN_VENTE, registry=registry)
    assert first.prediction.value == second.prediction.value
