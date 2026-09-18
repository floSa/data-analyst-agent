"""Limitation de débit par appelant : fenêtre glissante d'horodatages.

Pourquoi c'est nécessaire ici et pas ailleurs : ``POST /chat`` déclenche jusqu'à
onze appels LLM et un conteneur Docker pour un corps de quelques octets. Sans
quota, c'est un amplificateur de charge gratuit (audit §6.3) — et le LLM
mutualisé a un débit fini, partagé par tous les appelants : une rafale d'un
seul compte allonge la file de tous les autres.

Fenêtre GLISSANTE et non compteur remis à zéro : un compteur par minute civile
laisse passer deux fois le quota à cheval sur la minute. On garde les
horodatages des requêtes récentes et on compte celles qui tiennent dans la
fenêtre.

L'état est persisté comme celui de ``LoginThrottle``, avec les mêmes primitives
(écriture atomique, verrou par ressource) et pour la même raison : un compteur
en mémoire d'un seul process se remet à zéro au redémarrage et n'existe pas
pour les autres workers uvicorn.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from data_analyst_agent.orchestrator.workspace import (
    make_private_dir,
    resource_lock,
    write_text_atomic,
)

FILE_MODE = 0o600


class RateLimiter:
    """Compte les requêtes récentes d'une clé et refuse au-delà du quota.

    ``clock`` est injectable, comme pour les sessions et le verrouillage : une
    fenêtre de temps se teste en avançant l'horloge, pas en dormant.
    """

    FILE = "rate_limit.json"

    def __init__(
        self,
        state_dir: Path,
        max_requests: int,
        window_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(state_dir) / self.FILE
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock

    # -- fichier --------------------------------------------------------------

    def _read(self) -> dict[str, list[float]]:
        if not self.path.exists():
            return {}
        try:
            brut = json.loads(self.path.read_text(encoding="utf-8"))
            return {cle: [float(t) for t in valeurs] for cle, valeurs in brut.items()}
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            return {}

    def _write(self, appels: dict[str, list[float]]) -> None:
        make_private_dir(self.path.parent)
        write_text_atomic(self.path, json.dumps(appels), mode=FILE_MODE)

    def _recents(self, appels: dict[str, list[float]], now: float) -> dict[str, list[float]]:
        """Ne garde que ce qui tient dans la fenêtre : le fichier ne grandit pas."""
        vivants = {
            cle: [t for t in horodatages if now - t < self.window_seconds]
            for cle, horodatages in appels.items()
        }
        return {cle: horodatages for cle, horodatages in vivants.items() if horodatages}

    # -- usage ----------------------------------------------------------------

    def hit(self, key: str) -> float | None:
        """Enregistre une requête. Renvoie ``None`` si elle passe, sinon l'attente.

        L'attente rendue est le temps qui reste avant que la plus ancienne
        requête de la fenêtre en sorte — de quoi renseigner un ``Retry-After``
        honnête plutôt qu'un délai inventé.

        Une requête refusée n'est PAS comptée : sinon un client qui insiste
        repousserait indéfiniment sa propre échéance, et le refus deviendrait un
        bannissement.
        """
        if self.max_requests <= 0:
            return None
        now = self.clock()
        with resource_lock(self.path):
            appels = self._recents(self._read(), now)
            fenetre = appels.get(key, [])
            if len(fenetre) >= self.max_requests:
                self._write(appels)
                return self.window_seconds - (now - min(fenetre))
            appels[key] = [*fenetre, now]
            self._write(appels)
        return None
