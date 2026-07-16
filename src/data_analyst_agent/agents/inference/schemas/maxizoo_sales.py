"""Schéma de features du modèle de prévision Maxizoo — source de vérité (CADRAGE §7-③).

Le modèle prédit la quantité vendue d'un SKU, dans un magasin, un jour donné.
Il ne prend NI `store_id` NI `sku_id` : les identifiants n'apportaient presque
rien (R² 0,487 → 0,493, mesuré) et coûtaient cher — un modèle qui apprend par
cœur « SKU001 se vend beaucoup » est aveugle au SKU qu'il n'a jamais vu. Ce sont
donc les attributs qui décrivent le produit et le magasin, et un SKU lancé
demain (le cas « cold start » du dictionnaire) se prédit sans réentraînement.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CommodityGroup = Literal[
    "Chien",
    "Chat",
    "Aquariophilie",
    "Oiseau",
    "Rongeur",
    "Hygiène & Soins",
    "Reptile",
    "Accessoires & Jouets",
]

# 'aucune' n'est pas une typologie de l'enseigne : c'est le cas — majoritaire —
# du jour sans campagne. `promo_calendar.promo_type` ne le contient pas, il
# correspond au `promo_id NULL` de sales_daily.
PromoType = Literal[
    "aucune",
    "produits",
    "seuils",
    "ouverture_magasin",
    "influence",
    "mise_en_avant",
    "cadeau_seuil",
]


class MaxizooSalesFeatures(BaseModel):
    """Features attendues par le modèle de prévision des ventes Maxizoo."""

    model_config = ConfigDict(extra="forbid")

    store_type: Literal["grand", "moyen", "petit", "online"] = Field(
        description="Format du magasin ; 'online' désigne le canal e-commerce"
    )
    commodity_group: CommodityGroup = Field(description="Univers produit")
    brand_type: Literal["nationale", "exclusive", "distributeur"] = Field(
        description="Type de marque (nationale, exclusivité enseigne, marque de distributeur)"
    )
    base_price: float = Field(
        gt=0, le=500, description="Prix catalogue du SKU en euros, hors promo et hors inflation"
    )
    day_of_week: int = Field(ge=0, le=6, description="Jour de la semaine, 0 = lundi … 6 = dimanche")
    month: int = Field(ge=1, le=12, description="Mois de l'année (1 = janvier)")
    discount_rate: float = Field(
        ge=0,
        le=1,
        description="Taux de remise appliqué (0 = pas de remise ; 0.3 = -30 %). "
        "Seules les campagnes de type 'produits' remisent : 0 pour toutes les autres",
    )
    promo_type: PromoType = Field(
        description="Typologie de la campagne active ce jour-là, ou 'aucune' hors campagne"
    )
    temp_anomaly: float = Field(
        ge=-20,
        le=20,
        description="Écart de température à la normale du jour, en °C (0 = temps de saison). "
        "C'est l'écart qui porte le signal météo, pas la température absolue",
    )
