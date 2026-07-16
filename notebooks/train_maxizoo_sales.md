---
jupyter:
  jupytext:
    text_representation:
      extension: .md
      format_name: markdown
  kernelspec:
    display_name: Python 3
    language: python
    name: python3
---

# Entraînement — modèle Maxizoo (prévision de la quantité vendue)

<!-- #region -->
Modèle du registre (`models/registry.yaml`) : prédire la quantité vendue d'un
SKU, dans un magasin, un jour donné. Les features et leurs bornes font foi dans
`data_analyst_agent.agents.inference.schemas.maxizoo_sales` — ce notebook doit
entraîner sur exactement ces colonnes.

Prérequis : la base doit exister. Elle n'est pas versionnée (180 Mo) :

```bash
python scripts/load_maxizoo_duckdb.py --export ../base_demo
```

Sortie : `models/maxizoo_sales.joblib` (pipeline scikit-learn complet,
prétraitement inclus), rechargé tel quel par le registry au moment du predict.
<!-- #endregion -->

## Chargement des données

<!-- #region -->
Les features sortent d'une seule requête sur la base : la table de faits
`sales_daily`, jointe au magasin, au produit, à la campagne active et à la météo
du jour.

Deux décisions de modélisation, toutes deux dictées par le dictionnaire, sont
prises **dans cette requête** :

**`WHERE is_rupture = 0` (piège n°4).** Quand le stock est épuisé, la vente est
*censurée* : la quantité observée n'est pas la demande, c'est ce qu'il restait à
vendre. Entraîner dessus apprendrait au modèle à reproduire nos ruptures — il
prédirait une demande basse là où on a simplement manqué de stock. On écarte donc
ces ~1,1 % de lignes. C'est peu, mais c'est le seul sous-ensemble dont on sait
que la cible est fausse.

**`quantity = 0` est gardé (piège n°2).** ~46 % des lignes sont des jours sans
vente pour ce SKU dans ce magasin. Ce ne sont pas des trous : ce sont des zéros
réels, et c'est précisément ce qu'un modèle de demande doit savoir prédire. Les
retirer gonflerait la prévision sur tout le catalogue de niche.
<!-- #endregion -->
```python
from pathlib import Path

import duckdb
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
BASE = REPO / "sources" / "maxizoo.duckdb"

REQUETE = """
SELECT s.quantity,
       st.store_type,
       p.commodity_group,
       p.brand_type,
       p.base_price::DOUBLE                        AS base_price,
       (EXTRACT(ISODOW FROM s.date) - 1)::INT      AS day_of_week,  -- 0 = lundi
       EXTRACT(MONTH FROM s.date)::INT             AS month,
       COALESCE(pc.discount_rate, 0)::DOUBLE       AS discount_rate,
       COALESCE(pc.promo_type, 'aucune')           AS promo_type,
       w.temp_anomaly::DOUBLE                      AS temp_anomaly
FROM sales_daily s
JOIN stores   st ON st.store_id = s.store_id
JOIN products p  ON p.sku_id    = s.sku_id
-- LEFT : la plupart des jours n'ont pas de promo
LEFT JOIN promo_calendar pc ON pc.promo_id = s.promo_id
JOIN weather  w  ON w.store_id  = s.store_id AND w.date = s.date
WHERE s.is_rupture = 0
ORDER BY s.date, s.store_id, s.sku_id  -- reproductibilité : cf. ci-dessous
"""

df = duckdb.connect(str(BASE), read_only=True).execute(REQUETE).df()
print(df.shape)
df.head()
```

<!-- #region -->
**L'`ORDER BY` n'est pas cosmétique.** Sans lui, DuckDB scanne en parallèle et
rend les lignes dans un ordre qui varie d'une exécution à l'autre. Ce sont alors
d'autres lignes qui tombent dans le test set de `train_test_split`, et un autre
point d'arrêt pour l'early stopping du modèle : `random_state=42` ne suffit pas
à rendre le notebook reproductible. Constaté en vrai — R² qui dérivait de 0,491
à 0,497 et artefact de 851 Ko à 1074 Ko d'un run à l'autre, à code identique.
Trier sur la clé primaire fige l'entrée, et le reste suit.
<!-- #endregion -->


<!-- #region -->
~1,35 M de lignes (sur 1,36 M : les ruptures sont parties). Le
`LEFT JOIN promo_calendar` est essentiel — un `JOIN` aurait gardé les seuls
jours en campagne, soit une poignée de lignes, et le modèle aurait appris que
tout le monde est en promo tout le temps.
<!-- #endregion -->

## Features et cible

<!-- #region -->
Les 9 features du schéma `MaxizooSalesFeatures`, la cible `quantity`.

**Pourquoi pas `sku_id` ni `store_id` ?** On les a essayés : R² 0,487 → 0,493.
Trois millièmes, contre deux inconvénients. D'abord, un modèle qui apprend par
cœur « SKU001 se vend beaucoup » ne sait rien dire d'un SKU jamais vu — or le
dictionnaire (piège n°3) documente 4 SKU lancés en cours d'historique, et
l'enseigne en lancera d'autres. Ensuite, il faudrait réentraîner à chaque
référencement. Les attributs (`commodity_group`, `brand_type`, `base_price`)
portent presque toute l'information de l'identifiant, et se généralisent.
<!-- #endregion -->
```python
CATEGORICAL = ["store_type", "commodity_group", "brand_type", "promo_type"]
NUMERIC = ["base_price", "day_of_week", "month", "discount_rate", "temp_anomaly"]
FEATURES = CATEGORICAL + NUMERIC

X = df[FEATURES]
y = df["quantity"]
print("quantité moyenne :", round(float(y.mean()), 3))
print("part de jours sans vente :", round(float((y == 0).mean()), 3))
```

<!-- #region -->
Moyenne ~2,17 unités, et ~46 % de zéros : la cible est très asymétrique, ce qui
est la forme normale d'une demande au grain SKU × magasin × jour.
<!-- #endregion -->

## Pipeline de prétraitement + modèle

<!-- #region -->
One-hot sur le catégoriel (`handle_unknown="ignore"` : un univers produit
nouveau ne fera pas planter le predict), numérique passé tel quel — les arbres
n'ont que faire d'une standardisation. `HistGradientBoostingRegressor` encaisse
le million de lignes en quelques secondes et capture les interactions
(une remise n'a pas le même effet selon l'univers).

Évaluation sur un holdout 80/20 plutôt qu'en validation croisée : à 1,35 M de
lignes, 5 plis coûtent 5 fois le temps pour resserrer un intervalle déjà étroit.
<!-- #endregion -->
```python
model = Pipeline(
    [
        (
            "preprocess",
            ColumnTransformer(
                [("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL)],
                remainder="passthrough",
            ),
        ),
        ("regressor", HistGradientBoostingRegressor(max_iter=300, random_state=42)),
    ]
)

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
model.fit(X_train, y_train)
pred = model.predict(X_test)

baseline = mean_absolute_error(y_test, [y_train.mean()] * len(y_test))
print(f"MAE      : {mean_absolute_error(y_test, pred):.3f} unités")
print(f"MAE base : {baseline:.3f} unités (prédire toujours la moyenne)")
print(f"R2       : {r2_score(y_test, pred):.3f}")
```

<!-- #region -->
Attendu : MAE ~1,56 contre ~2,44 pour la moyenne (−36 %), R² ~0,49.

Un R² de 0,49 n'est pas un aveu de faiblesse : la demande quotidienne d'un SKU
dans un magasin est un tirage bruité, et cette moitié de variance non expliquée
est en grande partie irréductible. Ce qui compte est que le modèle batte
nettement le niveau de base — et surtout qu'il ait appris les bons signaux,
ce que le contrôle ci-dessous vérifie.
<!-- #endregion -->

## Entraînement final et artefact

<!-- #region -->
Fit sur tout le jeu, puis sérialisation joblib de la pipeline complète
(l'artefact embarque le prétraitement : le predict ne reçoit que des features
brutes validées par le schéma).
<!-- #endregion -->
```python
model.fit(X, y)
artefact = REPO / "models" / "maxizoo_sales.joblib"
joblib.dump(model, artefact)
print(f"artefact : {artefact.name} ({artefact.stat().st_size / 1024:.0f} Ko)")
```

## Contrôle de cohérence : le modèle a-t-il appris l'effet promo ?

<!-- #region -->
Le contrôle que ce jeu de données rend possible : on connaît l'effet **injecté**
dans les données. Le modèle, lui, n'a jamais vu la formule — il n'a vu que des
ventes. Retrouve-t-il le mécanisme ?

On mesure l'uplift **sur la population**, pas sur un produit de référence : on
prend 20 000 lignes réelles, on les prédit deux fois — hors campagne, puis en
campagne `produits` — et on compare les moyennes. Un ratio entre deux
prédictions ponctuelles paraît plus lisible, mais il est inutilisable : sur ce
modèle il oscille entre ×1,26 et ×1,84 selon le sous-échantillon d'entraînement,
là où l'estimation par population tient au millième. Il aurait suffi de tomber
sur le bon point pour « démontrer » n'importe quel chiffre voulu.
<!-- #endregion -->
```python
echantillon = duckdb.connect(str(BASE), read_only=True).execute("""
    SELECT st.store_type, p.commodity_group, p.brand_type, p.base_price::DOUBLE AS base_price,
           (EXTRACT(ISODOW FROM s.date) - 1)::INT AS day_of_week,
           EXTRACT(MONTH FROM s.date)::INT        AS month,
           w.temp_anomaly::DOUBLE                 AS temp_anomaly
    FROM sales_daily s
    JOIN stores   st ON st.store_id = s.store_id
    JOIN products p  ON p.sku_id    = s.sku_id
    JOIN weather  w  ON w.store_id  = s.store_id AND w.date = s.date
    WHERE s.is_rupture = 0
    USING SAMPLE 20000 ROWS (reservoir, 42)
""").df()

hors_campagne = model.predict(echantillon.assign(discount_rate=0.0, promo_type="aucune")).mean()
for remise in (0.15, 0.20, 0.30):
    en_campagne = model.predict(
        echantillon.assign(discount_rate=remise, promo_type="produits")
    ).mean()
    print(f"remise {remise:.0%} -> uplift x{en_campagne / hors_campagne:.3f}")

uplift_30 = model.predict(
    echantillon.assign(discount_rate=0.30, promo_type="produits")
).mean() / hors_campagne
assert 1.35 < uplift_30 < 1.85, "le modèle n'a pas appris l'effet des campagnes produits"
```

<!-- #region -->
**Ce qu'il a appris : la campagne.** Uplift ~×1,61 à 30 %, quand une ligne hors
campagne sert de base à ×1,0. C'est le bon ordre de grandeur, et il tombe dans
la fourchette de ce que l'uplift **mesuré** vaut sur les données brutes (×1,34 à
×1,95 selon les campagnes — cf. Q10 des questions de référence).

**Ce qu'il n'a pas appris : la profondeur de la remise.** L'uplift prédit est
quasi plat (×1,53 à 15 %, ×1,53 à 20 %, ×1,61 à 30 %) alors que la formule injectée
`1 + 2,2 × remise` promet ×1,33 → ×1,66. Le modèle a compris « il y a une
campagne », pas « la remise est profonde ».

Ce n'est pas un défaut du modèle : c'est ce que les données permettent. Mesuré
directement dans `sales_daily`, l'uplift empirique par palier de remise n'est
lui-même **pas monotone** — ×1,43 à 15 %, ×1,51 à 20 %, ×1,34 à 25 %, ×1,95 à
30 %. Avec seulement 25 à 100 couples campagne × SKU par palier, et une
saisonnalité confondue avec la remise (les 30 % sont les Black Friday, donc
novembre, dont le mois porte déjà l'effet), la profondeur n'est pas identifiable.
Un modèle qui prétendrait la retrouver aurait surtout appris à surajuster.

**Conséquence d'usage :** ce modèle répond bien à « combien vendra-t-on pendant
une campagne produits ? », et mal à « faut-il remiser à 20 ou à 30 % ? ». Cette
seconde question demanderait un plan d'expérience, pas ce jeu d'observations.
<!-- #endregion -->
