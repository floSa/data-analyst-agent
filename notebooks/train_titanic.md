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

# Entraînement — modèle Titanic (classification de la survie)

<!-- #region -->
Modèle jouet n°1 du registre (`models/registry.yaml`) : prédire la survie d'un
passager du Titanic. Les features et leurs bornes font foi dans
`data_analyst_agent.agents.inference.schemas.titanic` — ce notebook doit
entraîner sur exactement ces colonnes.

Sortie : `models/titanic.joblib` (pipeline scikit-learn complet, prétraitement
inclus), rechargé tel quel par le registry au moment du predict.
<!-- #endregion -->

## Chargement des données

<!-- #region -->
Le CSV vendorisé dans `sources/` (891 passagers), colonnes normalisées en
minuscules pour coller au schéma Pydantic.

<!-- #endregion -->
```python
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()

df = pd.read_csv(REPO / "sources" / "titanic.csv")
df.columns = [c.lower() for c in df.columns]
print(df.shape)
df[["survived", "pclass", "sex", "age"]].head()
```

<!-- #region -->
891 lignes, 12 colonnes brutes ; on n'utilisera que les 7 features du schéma.

<!-- #endregion -->
## Features et cible

<!-- #region -->
Les 7 features du schéma `TitanicFeatures`, la cible `survived` (38 % de
survivants — les classes sont déséquilibrées mais pas pathologiquement).

<!-- #endregion -->
```python
FEATURES = ["sex", "pclass", "age", "sibsp", "parch", "fare", "embarked"]
X = df[FEATURES]
y = df["survived"]
print("taux de survie global :", round(float(y.mean()), 3))
```

<!-- #region -->
Le taux global (~0,384) sert de niveau de base : prédire « personne ne
survit » donnerait ~62 % d'accuracy.

<!-- #endregion -->
## Pipeline de prétraitement + modèle

<!-- #region -->
Imputation médiane + standardisation pour le numérique ; imputation par le
mode + one-hot pour le catégoriel (`handle_unknown="ignore"` pour la
robustesse au predict). Régression logistique : simple, calibrée, largement
suffisante pour un modèle jouet.

<!-- #endregion -->
```python
numeric = ["age", "sibsp", "parch", "fare"]
categorical = ["sex", "pclass", "embarked"]
preprocess = ColumnTransformer(
    [
        (
            "num",
            Pipeline(
                [("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]
            ),
            numeric,
        ),
        (
            "cat",
            Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="most_frequent")),
                    ("onehot", OneHotEncoder(handle_unknown="ignore")),
                ]
            ),
            categorical,
        ),
    ]
)
model = Pipeline(
    [
        ("preprocess", preprocess),
        ("classifier", LogisticRegression(max_iter=1000, random_state=42)),
    ]
)
scores = cross_val_score(model, X, y, cv=5, scoring="accuracy")
print(f"accuracy (CV 5 plis) : {scores.mean():.3f} +/- {scores.std():.3f}")
```

<!-- #region -->
Attendu : ~0,79 ± 0,02 — dans la norme pour ce dataset avec ces features.

<!-- #endregion -->
## Entraînement final et artefact

<!-- #region -->
Fit sur tout le jeu, puis sérialisation joblib de la pipeline complète
(l'artefact embarque le prétraitement : le predict ne reçoit que des features
brutes validées par le schéma).

<!-- #endregion -->
```python
model.fit(X, y)
artefact = REPO / "models" / "titanic.joblib"
joblib.dump(model, artefact)
print(f"artefact : {artefact.name} ({artefact.stat().st_size / 1024:.0f} Ko)")
```

<!-- #region -->
Artefact d'environ 5 Ko, committable sans état d'âme.

<!-- #endregion -->
## Contrôle de cohérence

<!-- #region -->
Une femme de 1ʳᵉ classe doit ressortir avec une probabilité de survie élevée
(c'est le scénario golden n°3 du CADRAGE §12).

<!-- #endregion -->
```python
exemple = pd.DataFrame(
    [
        {
            "sex": "female",
            "pclass": 1,
            "age": 28.0,
            "sibsp": 0,
            "parch": 0,
            "fare": 80.0,
            "embarked": "S",
        }
    ]
)
proba = model.predict_proba(exemple)[0][1]
print(f"proba de survie (femme, 1re classe, 28 ans) : {proba:.3f}")
assert proba > 0.8, "une femme de 1re classe doit avoir une proba de survie élevée"
```

<!-- #region -->
Proba observée ~0,93 : cohérent avec l'histoire (les femmes de 1ʳᵉ classe ont
très majoritairement survécu).

<!-- #endregion -->
