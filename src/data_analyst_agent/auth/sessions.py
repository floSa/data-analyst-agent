"""Sessions côté serveur : identifiant opaque, état sur disque, expiration.

Le navigateur ne reçoit qu'un jeton **opaque** tiré de :mod:`secrets` — aucune
donnée, aucune signature, rien à décoder ni à forger. Tout l'état vit ici, ce
qui est la raison d'être du choix : une déconnexion **révoque vraiment** la
session, là où un jeton signé reste valable jusqu'à son échéance quoi qu'on
fasse.

Le fichier ne garde pas le jeton mais son empreinte SHA-256. Un sha256 serait
un mauvais choix pour un mot de passe (rapide, donc attaquable par force brute)
mais c'est le bon ici : le jeton fait 256 bits d'aléa, il n'y a pas de
dictionnaire à essayer. Le fichier volé ne rend donc aucune session active.

Écriture atomique et verrou par ressource : le magasin de sessions a exactement
le problème de concurrence corrigé sur les conversations — lecture, modification,
écriture — et deux connexions simultanées en perdraient une.
"""

from __future__ import annotations

import hashlib
import json
import secrets
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

# 32 octets d'urandom : hors de portée d'une devinette, et le format urlsafe
# passe tel quel dans un cookie.
TOKEN_BYTES = 32


def _empreinte(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Session(BaseModel):
    """Une session ouverte, telle que persistée (jamais le jeton lui-même)."""

    login: str
    # Jeton anti-CSRF lié à la session : le cookie seul ne prouve plus rien sur
    # l'origine de la requête (cf. api/app.py).
    csrf_token: str
    created_at: float
    last_seen_at: float


class SessionStore:
    """Sessions d'un fichier JSON, indexées par l'empreinte de leur jeton.

    Deux échéances, toutes deux configurables : l'**inactivité** ferme un poste
    laissé ouvert, la **durée absolue** borne une session qu'un onglet
    maintiendrait indéfiniment vivante.

    ``clock`` est injectable : c'est ce qui permet de tester une expiration sans
    faire dormir la suite de tests.
    """

    FILE = "sessions.json"

    def __init__(
        self,
        state_dir: Path,
        idle_timeout: float,
        absolute_timeout: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(state_dir) / self.FILE
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout
        self.clock = clock

    # -- fichier --------------------------------------------------------------

    def _read(self) -> dict[str, Session]:
        if not self.path.exists():
            return {}
        try:
            brut = json.loads(self.path.read_text(encoding="utf-8"))
            return {cle: Session.model_validate(valeur) for cle, valeur in brut.items()}
        except (json.JSONDecodeError, ValidationError, AttributeError, TypeError):
            # Magasin illisible : on repart à vide. Contrairement aux comptes,
            # échouer fermé ici ne protège rien — le pire effet est de
            # redemander une connexion, jamais d'en accorder une.
            return {}

    def _write(self, sessions: dict[str, Session]) -> None:
        make_private_dir(self.path.parent)
        payload = {cle: session.model_dump() for cle, session in sessions.items()}
        write_text_atomic(self.path, json.dumps(payload, indent=2), mode=FILE_MODE)

    # -- expiration -----------------------------------------------------------

    def expired(self, session: Session, now: float) -> bool:
        return (
            now - session.created_at >= self.absolute_timeout
            or now - session.last_seen_at >= self.idle_timeout
        )

    def _vivantes(self, sessions: dict[str, Session], now: float) -> dict[str, Session]:
        return {cle: s for cle, s in sessions.items() if not self.expired(s, now)}

    # -- cycle de vie ---------------------------------------------------------

    def create(self, login: str) -> tuple[str, Session]:
        """Ouvre une session et rend ``(jeton, session)``.

        Le jeton n'est rendu qu'ici : il n'est écrit nulle part et ne peut donc
        pas être relu du disque. Les sessions expirées sont purgées au passage —
        le fichier ne grandit pas indéfiniment.
        """
        now = self.clock()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        session = Session(
            login=login,
            csrf_token=secrets.token_urlsafe(TOKEN_BYTES),
            created_at=now,
            last_seen_at=now,
        )
        with resource_lock(self.path):
            sessions = self._vivantes(self._read(), now)
            sessions[_empreinte(token)] = session
            self._write(sessions)
        return token, session

    def resolve(self, token: str | None) -> Session | None:
        """Rend la session du jeton si elle est encore valable, sinon ``None``.

        L'accès met à jour ``last_seen_at`` : c'est ce qui fait courir le délai
        d'inactivité depuis la dernière requête et non depuis la connexion. Le
        prix en est une réécriture du fichier par requête authentifiée —
        acceptable pour une instance on-premise, et le fichier tient les
        sessions ouvertes, pas l'historique.
        """
        if not token:
            return None
        cle = _empreinte(token)
        now = self.clock()
        with resource_lock(self.path):
            sessions = self._read()
            session = sessions.get(cle)
            if session is None:
                return None
            if self.expired(session, now):
                del sessions[cle]
                self._write(sessions)
                return None
            session.last_seen_at = now
            self._write(self._vivantes(sessions, now))
            return session

    def revoke(self, token: str | None) -> bool:
        """Ferme la session du jeton. C'est ce que fait la déconnexion."""
        if not token:
            return False
        cle = _empreinte(token)
        with resource_lock(self.path):
            sessions = self._read()
            if cle not in sessions:
                return False
            del sessions[cle]
            self._write(sessions)
            return True

    def revoke_login(self, login: str) -> int:
        """Ferme TOUTES les sessions d'un compte ; rend leur nombre.

        Désactiver un compte ou changer son mot de passe doit couper les
        sessions déjà ouvertes : sans ça, l'administrateur croit avoir fermé la
        porte alors que l'onglet resté ouvert continue de répondre.
        """
        with resource_lock(self.path):
            sessions = self._read()
            fermees = [cle for cle, s in sessions.items() if s.login == login]
            for cle in fermees:
                del sessions[cle]
            if fermees:
                self._write(sessions)
            return len(fermees)
