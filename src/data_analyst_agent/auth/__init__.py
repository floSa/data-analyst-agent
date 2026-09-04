"""Authentification : comptes locaux, sessions côté serveur, anti-force brute.

L'application doit rester autonome — pas d'OIDC, pas d'annuaire, pas de service
d'identité à déployer à côté. D'où un magasin de comptes adossé à un fichier
(:mod:`.accounts`), des sessions opaques persistées sur disque
(:mod:`.sessions`) et un verrouillage des tentatives (:mod:`.throttle`).
"""

from data_analyst_agent.auth.accounts import Account, AccountError, AccountStore
from data_analyst_agent.auth.current_user import CurrentUser, current_user
from data_analyst_agent.auth.sessions import Session, SessionStore
from data_analyst_agent.auth.throttle import LoginThrottle

__all__ = [
    "Account",
    "AccountError",
    "AccountStore",
    "CurrentUser",
    "LoginThrottle",
    "Session",
    "SessionStore",
    "current_user",
]
