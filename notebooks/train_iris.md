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

# Entraînement — modèle Iris (classification de l'espèce)

<!-- #region -->
Modèle jouet n°2 du registre : prédire l'espèce d'un iris à partir des quatre
mesures. Dataset embarqué dans scikit-learn (aucun téléchargement). Les noms
de colonnes sont renommés en snake_case pour coller au schéma
`IrisFeatures`.

Sortie : `models/iris.joblib`.
<!-- #endregion -->

## Chargement et renommage

<!-- #region -->
Les colonnes sklearn (« sepal length (cm) »…) deviennent les noms du schéma
Pydantic — c'est le contrat entre l'entraînement et le predict.

<!-- #endregion -->
```python
from pathlib import Path

import joblib
import pandas as pd
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()

RENAME = {
    "sepal length (cm)": "sepal_length",
    "sepal width (cm)": "sepal_width",
    "petal length (cm)": "petal_length",
    "petal width (cm)": "petal_width",
}

data = load_iris(as_frame=True)
X = data.data.rename(columns=RENAME)
y = data.target
print(X.shape, list(X.columns))
print("classes :", list(data.target_names))
```

<!-- #region -->
150 observations, 3 classes équilibrées (50/50/50) : setosa, versicolor,
virginica — les libellés humains sont dans `models/registry.yaml`.

<!-- #endregion -->
## Modèle et validation croisée

<!-- #region -->
Standardisation + régression logistique multinomiale : le dataset est
quasi linéairement séparable, inutile de sortir l'artillerie.

<!-- #endregion -->
```python
model = Pipeline(
    [
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(max_iter=1000, random_state=42)),
    ]
)
scores = cross_val_score(model, X, y, cv=5, scoring="accuracy")
print(f"accuracy (CV 5 plis) : {scores.mean():.3f} +/- {scores.std():.3f}")
```

<!-- #region -->
Attendu : ~0,96.

<!-- #endregion -->
## Entraînement final, artefact et contrôle

<!-- #region -->
Fit complet, dump joblib, et un contrôle sur un setosa d'école.

<!-- #endregion -->
```python
model.fit(X, y)
artefact = REPO / "models" / "iris.joblib"
joblib.dump(model, artefact)
print(f"artefact : {artefact.name} ({artefact.stat().st_size / 1024:.0f} Ko)")

setosa = pd.DataFrame(
    [{"sepal_length": 5.1, "sepal_width": 3.5, "petal_length": 1.4, "petal_width": 0.2}]
)
prediction = int(model.predict(setosa)[0])
print("prédiction pour un setosa type :", data.target_names[prediction])
assert prediction == 0
```

<!-- #region -->
Le setosa type est bien reconnu ; l'artefact pèse ~2 Ko.

<!-- #endregion -->
