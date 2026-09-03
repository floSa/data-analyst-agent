"""Anti-force brute : verrouillage des tentatives après N échecs.

Deux compteurs indépendants, et il faut les deux : **par compte**, sinon un
attaquant essaie un dictionnaire sur un login connu depuis mille adresses ; **par
adresse**, sinon il balaie mille logins depuis une seule machine sans jamais
verrouiller personne. Le verrouillage d'un compte est aussi une arme contre son
propriétaire — d'où une échéance, jamais un blocage définitif.

L'état est persisté avec les mêmes primitives que le reste (écriture atomique,
verrou par ressource) : un compteur qui vit en mémoire d'un seul process se
remet à zéro au redémarrage, et n'existe pas pour les autres workers uvicorn.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from data_analyst_agent.orchestrator.workspace import (
    make_private_dir,
    resource_lock,
    write_text_atomic,
)

FILE_MODE = 0o600


class Failures(BaseModel):
    """Échecs consécutifs d'une clé (un login, ou une adresse) et leur date."""

    count: int = 0
    last_at: float = 0.0


class LoginThrottle:
    """Compte les échecs de connexion et verrouille au-delà du seuil.

    ``clock`` est injectable, comme pour les sessions : une temporisation se
    teste en avançant l'horloge, pas en dormant.
    """

    FILE = "login_failures.json"
    LOGIN = "login"
    ADDRESS = "adresse"

    def __init__(
        self,
        state_dir: Path,
        max_failures: int,
        lockout_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(state_dir) / self.FILE
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.clock = clock

    # -- fichier --------------------------------------------------------------

    def _read(self) -> dict[str, Failures]:
        if not self.path.exists():
            return {}
        try:
            brut = json.loads(self.path.read_text(encoding="utf-8"))
            return {cle: Failures.model_validate(valeur) for cle, valeur in brut.items()}
        except (json.JSONDecodeError, ValidationError, AttributeError, TypeError):
            return {}

    def _write(self, echecs: dict[str, Failures]) -> None:
        make_private_dir(self.path.parent)
        payload = {cle: valeur.model_dump() for cle, valeur in echecs.items()}
        write_text_atomic(self.path, json.dumps(payload, indent=2), mode=FILE_MODE)

    @staticmethod
    def _cles(login: str, address: str) -> list[str]:
        return [f"{LoginThrottle.LOGIN}:{login}", f"{LoginThrottle.ADDRESS}:{address}"]

    def _verrouillee(self, echecs: Failures, now: float) -> bool:
        return echecs.count >= self.max_failures and now - echecs.last_at < self.lockout_seconds

    # -- usage ----------------------------------------------------------------

    def locked(self, login: str, address: str) -> bool:
        """Le couple (compte, adresse) est-il en cours de verrouillage ?

        Un seul des deux compteurs suffit à refuser : c'est ce qui rend le
        balayage de logins depuis une adresse aussi coûteux que l'attaque d'un
        compte depuis mille.
        """
        now = self.clock()
        echecs = self._read()
        cles = self._cles(login, address)
        return any(self._verrouillee(echecs.get(cle, Failures()), now) for cle in cles)

    def record_failure(self, login: str, address: str) -> None:
        """Compte un échec sur les deux clés.

        Une série d'échecs séparés de plus de ``lockout_seconds`` ne verrouille
        pas : le compteur repart de zéro. Sinon trois fautes de frappe étalées
        sur un mois finiraient par fermer le compte.
        """
        now = self.clock()
        with resource_lock(self.path):
            echecs = self._read()
            for cle in self._cles(login, address):
                courant = echecs.get(cle, Failures())
                depuis_zero = now - courant.last_at >= self.lockout_seconds
                echecs[cle] = Failures(count=1 if depuis_zero else courant.count + 1, last_at=now)
            self._write(self._recentes(echecs, now))

    def reset(self, login: str, address: str) -> None:
        """Efface les compteurs — appelé sur une connexion réussie."""
        with resource_lock(self.path):
            echecs = self._read()
            retires = [cle for cle in self._cles(login, address) if cle in echecs]
            for cle in retires:
                del echecs[cle]
            if retires:
                self._write(echecs)

    def _recentes(self, echecs: dict[str, Failures], now: float) -> dict[str, Failures]:
        """Purge les compteurs dont le verrouillage est éteint : le fichier ne grandit pas."""
        return {
            cle: valeur
            for cle, valeur in echecs.items()
            if now - valeur.last_at < self.lockout_seconds
        }
