"""L'utilisateur de la requête en cours, sous forme de dépendance FastAPI.

C'est le point de raccordement de l'étape suivante (propriété des
conversations) : elle n'a pas à savoir d'où vient l'identité, seulement à
déclarer ``utilisateur: Annotated[CurrentUser, Depends(current_user)]`` et à
filtrer sur ``utilisateur.login``.

L'authentification elle-même est faite en amont, par le middleware de
``api/app.py``, qui pose ``request.state.user``. La dépendance ne fait que le
relire : elle n'ouvre donc aucun second chemin d'authentification qu'on
oublierait de durcir.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from pydantic import BaseModel


class CurrentUser(BaseModel):
    """Qui fait la requête. ``login`` est la clé stable d'un compte."""

    login: str


def current_user(request: Request) -> CurrentUser:
    utilisateur = getattr(request.state, "user", None)
    if utilisateur is None:
        # Ne devrait pas arriver : le middleware refuse avant d'en arriver là.
        # Filet, pour qu'une route ajoutée hors du chemin gardé échoue fermé.
        raise HTTPException(status_code=401, detail="authentification requise")
    return utilisateur
