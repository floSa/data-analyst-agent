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

# Entraînement — modèle California Housing (régression du prix médian)

<!-- #region -->
Modèle jouet n°3 du registre : prédire le prix médian des logements d'un îlot
californien (recensement 1990), en centaines de milliers de dollars. Le
dataset est vendorisé dans `sources/california_housing.csv.gz` (Boston a été
retiré de scikit-learn, California est son remplaçant officiel).

Sortie : `models/california_housing.joblib`.
<!-- #endregion -->

## Chargement et renommage

<!-- #region -->
Colonnes renommées en snake_case pour coller au schéma
`CaliforniaHousingFeatures` — même contrat entraînement/predict que pour les
deux autres modèles.

<!-- #endregion -->
```python
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import cross_val_score

REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()

RENAME = {
    "MedInc": "med_inc",
    "HouseAge": "house_age",
    "AveRooms": "ave_rooms",
    "AveBedrms": "ave_bedrms",
    "Population": "population",
    "AveOccup": "ave_occup",
    "Latitude": "latitude",
    "Longitude": "longitude",
}

df = pd.read_csv(REPO / "sources" / "california_housing.csv.gz")
X = df.drop(columns=["MedHouseVal"]).rename(columns=RENAME)
y = df["MedHouseVal"]
print(X.shape, list(X.columns))
```

<!-- #region -->
20 640 îlots, 8 features numériques, cible `MedHouseVal` (× 100 000 $).

<!-- #endregion -->
## Modèle et validation croisée

<!-- #region -->
`HistGradientBoostingRegressor` : bon rapport précision/taille d'artefact
(quelques centaines de Ko là où une forêt aléatoire en pèserait des dizaines
de Mo). Le R² en CV 5 plis non mélangés est pénalisé par l'ordre géographique
du fichier — c'est un choix assumé pour un modèle jouet.

<!-- #endregion -->
```python
model = HistGradientBoostingRegressor(random_state=42)
scores = cross_val_score(model, X, y, cv=5, scoring="r2")
print(f"R2 (CV 5 plis) : {scores.mean():.3f} +/- {scores.std():.3f}")
```

<!-- #region -->
Attendu : ~0,70 ± 0,03.

<!-- #endregion -->
## Entraînement final, artefact et contrôle

<!-- #region -->
Fit complet, dump joblib, contrôle sur la première ligne historique du
dataset (~4,5).

<!-- #endregion -->
```python
model.fit(X, y)
artefact = REPO / "models" / "california_housing.joblib"
joblib.dump(model, artefact)
print(f"artefact : {artefact.name} ({artefact.stat().st_size / 1024:.0f} Ko)")

exemple = pd.DataFrame(
    [
        {
            "med_inc": 8.3252,
            "house_age": 41.0,
            "ave_rooms": 6.98,
            "ave_bedrms": 1.02,
            "population": 322.0,
            "ave_occup": 2.55,
            "latitude": 37.88,
            "longitude": -122.23,
        }
    ]
)
valeur = float(model.predict(exemple)[0])
print(f"prédiction îlot de référence : {valeur:.3f} (x 100 000 $)")
assert 3.0 < valeur < 6.0, "la première ligne du dataset vaut ~4.5"
```

<!-- #region -->
Prédiction ~4,14 pour une valeur réelle de 4,526 : ordre de grandeur correct,
suffisant pour le rôle de modèle jouet.

<!-- #endregion -->
