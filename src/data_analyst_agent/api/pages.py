"""Gabarits HTML de l'application, servis depuis des fichiers.

La page de chat vivait en chaîne Python dans ``api/app.py``, qui faisait
412 lignes dont 290 de front. Y empiler une deuxième page aurait rendu le
fichier illisible — et la chaîne a un défaut propre : un échappement raté y
casse tout le script sans le moindre bruit côté serveur (d'où le préfixe ``r``
et le test de validité syntaxique du JS). Dans un ``.html``, le problème
n'existe pas et l'éditeur colore le fichier.

La substitution est volontairement minuscule — ``{{cle}}``, valeur échappée —
plutôt qu'un moteur de gabarits : il y a deux pages et trois valeurs à passer.
"""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"

CHAT = "chat.html"
LOGIN = "login.html"


@lru_cache
def gabarit(nom: str) -> str:
    """Contenu brut d'un gabarit, lu une fois (relire à chaque requête ne sert rien)."""
    return (TEMPLATES_DIR / nom).read_text(encoding="utf-8")


def render(nom: str, **valeurs: str) -> str:
    """Rend un gabarit en remplaçant les ``{{cle}}`` par les valeurs, ÉCHAPPÉES.

    L'échappement est systématique et non optionnel : la page de connexion
    reçoit un jeton et un message d'erreur, et une exception à cette règle est
    exactement ce qui ouvre une injection.
    """
    page = gabarit(nom)
    for cle, valeur in valeurs.items():
        page = page.replace("{{" + cle + "}}", html.escape(str(valeur), quote=True))
    return page
