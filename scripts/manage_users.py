"""Administration des comptes : créer, lister, désactiver, réinitialiser.

C'est le SEUL moyen de créer un compte. Il n'y a pas d'inscription ouverte, et
pas de compte par défaut : un compte livré avec le code finit en production avec
son mot de passe d'usine.

    uv run python scripts/manage_users.py create alice
    uv run python scripts/manage_users.py list
    uv run python scripts/manage_users.py disable alice
    uv run python scripts/manage_users.py enable alice
    uv run python scripts/manage_users.py reset-password alice

Le mot de passe est demandé au terminal, jamais passé en argument : la ligne de
commande d'un process est lisible par tout compte local (``ps``) et reste dans
l'historique du shell. Pour un usage scripté (provisionnement), ``--stdin`` le
lit sur l'entrée standard.

Le magasin visé est celui de la configuration (``DAA_AUTH_ACCOUNTS_PATH``,
défaut ``var/users.yaml``) ; ``--accounts`` permet d'en viser un autre.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from data_analyst_agent.auth.accounts import (
    PASSWORD_MIN_CHARS,
    AccountError,
    AccountStore,
    normalize_login,
)
from data_analyst_agent.auth.sessions import SessionStore
from data_analyst_agent.config import Settings, get_settings


def lire_mot_de_passe(depuis_stdin: bool) -> str:
    """Demande le mot de passe deux fois, ou le lit sur l'entrée standard."""
    if depuis_stdin:
        return sys.stdin.readline().rstrip("\n")
    premier = getpass.getpass("Mot de passe : ")
    if premier != getpass.getpass("Confirmation : "):
        raise AccountError("les deux saisies diffèrent")
    return premier


def _fermer_les_sessions(settings: Settings, login: str) -> int:
    """Coupe les sessions déjà ouvertes du compte.

    Désactiver un compte ou changer son mot de passe sans ça donne
    l'illusion d'avoir fermé la porte : l'onglet resté ouvert continue de
    répondre jusqu'à l'expiration de sa session.
    """
    sessions = SessionStore(
        settings.auth_state_dir,
        settings.session_idle_timeout,
        settings.session_absolute_timeout,
    )
    return sessions.revoke_login(login)


def commande_create(store: AccountStore, settings: Settings, args) -> str:
    compte = store.create(args.login, lire_mot_de_passe(args.stdin))
    return f"compte créé : {compte.login}"


def commande_list(store: AccountStore, settings: Settings, args) -> str:
    comptes = store.list()
    if not comptes:
        return "aucun compte — `manage_users.py create <login>` pour en ouvrir un"
    lignes = [f"{'LOGIN':<24} {'ÉTAT':<10} MOT DE PASSE CHANGÉ LE"]
    for compte in comptes:
        etat = "actif" if compte.active else "désactivé"
        lignes.append(f"{compte.login:<24} {etat:<10} {compte.password_updated_at}")
    return "\n".join(lignes)


def commande_disable(store: AccountStore, settings: Settings, args) -> str:
    store.set_active(args.login, False)
    fermees = _fermer_les_sessions(settings, args.login)
    return f"compte désactivé : {args.login} ({fermees} session(s) fermée(s))"


def commande_enable(store: AccountStore, settings: Settings, args) -> str:
    store.set_active(args.login, True)
    return f"compte réactivé : {args.login}"


def commande_reset_password(store: AccountStore, settings: Settings, args) -> str:
    store.set_password(args.login, lire_mot_de_passe(args.stdin))
    fermees = _fermer_les_sessions(settings, args.login)
    return f"mot de passe réinitialisé : {args.login} ({fermees} session(s) fermée(s))"


COMMANDES = {
    "create": (commande_create, "ouvrir un compte", True),
    "list": (commande_list, "lister les comptes", False),
    "disable": (commande_disable, "fermer un compte sans le supprimer", False),
    "enable": (commande_enable, "rouvrir un compte désactivé", False),
    "reset-password": (commande_reset_password, "changer le mot de passe d'un compte", True),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Le mot de passe fait au moins {PASSWORD_MIN_CHARS} caractères.",
    )
    parser.add_argument(
        "--accounts",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="magasin de comptes (défaut : DAA_AUTH_ACCOUNTS_PATH)",
    )
    sous = parser.add_subparsers(dest="commande", required=True)
    for nom, (_, aide, avec_mot_de_passe) in COMMANDES.items():
        sous_parser = sous.add_parser(nom, help=aide)
        if nom != "list":
            sous_parser.add_argument("login")
        if avec_mot_de_passe:
            sous_parser.add_argument(
                "--stdin",
                action="store_true",
                help="lire le mot de passe sur l'entrée standard (provisionnement)",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "login", None) is not None:
        # Le magasin normaliserait de son côté, mais pas `revoke_login` : les
        # sessions sont indexées sur la forme canonique du compte, et
        # `disable alice` doit fermer les onglets de `Alice`.
        args.login = normalize_login(args.login)
    settings = get_settings()
    store = AccountStore(args.accounts or settings.auth_accounts_path)
    action = COMMANDES[args.commande][0]
    try:
        print(action(store, settings, args))
    except AccountError as refus:
        # Un refus d'administration est un message, pas une trace : l'opérateur
        # n'a rien à déboguer, il a une consigne à lire.
        print(f"refusé : {refus}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
