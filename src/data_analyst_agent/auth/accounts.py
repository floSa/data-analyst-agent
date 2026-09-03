"""Magasin de comptes : un fichier YAML de logins et d'empreintes argon2id.

Le fichier ne contient **jamais** de mot de passe, seulement une empreinte
argon2id — un hachage *lent et à mémoire dure*, contrairement à un sha256 qu'un
GPU essaie par milliards par seconde. Il est écrit en 0600 et n'est pas
versionné (cf. ``.gitignore`` et ``users.example.yaml``).

Il n'y a **pas d'inscription ouverte ni de compte par défaut** : le seul moyen
de créer un compte est ``scripts/manage_users.py``. Un compte livré avec le code
finit en production avec son mot de passe d'usine.

Le login est **normalisé une fois pour toutes ici** (cf. ``normalize_login``) :
c'est la clé d'un compte, et c'est aussi le nom du dossier où vivent ses
conversations. Deux comptes qui ne diffèrent que par la casse ou des espaces de
bord seraient deux comptes pour l'utilisateur et un seul dossier pour le
service — donc une fuite entre eux.
"""

from __future__ import annotations

import contextlib
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel, Field, ValidationError, field_validator

from data_analyst_agent.orchestrator.workspace import (
    make_private_dir,
    resource_lock,
    write_text_atomic,
)

# Le fichier porte des empreintes de mots de passe : seul le compte du service a
# à le lire. Appliqué au temporaire, avant publication (cf. write_text_atomic).
FILE_MODE = 0o600

# Un mot de passe court est cassable même derrière argon2id : la longueur est la
# seule défense qui reste une fois l'empreinte volée.
PASSWORD_MIN_CHARS = 12

# Mot de passe qu'aucun compte ne porte : son empreinte est vérifiée quand le
# login est inconnu ou désactivé. Sans ça, une réponse instantanée dit « ce
# compte n'existe pas » et l'énumération redevient possible malgré le message
# d'erreur unique.
LEURRE = "aucun-compte-ne-porte-ce-mot-de-passe"

# Un login devient un composant de chemin : on le borne, comme le ferait le
# système de fichiers, mais avec un message lisible plutôt qu'un ENAMETOOLONG.
LOGIN_MAX_CHARS = 64


def normalize_login(login: str) -> str:
    """Forme canonique d'un login : NFKC, sans espaces de bord, sans casse.

    Trois replis, et un seul motif : **un compte, un login, un dossier**.

    - ``NFKC`` parce que deux chaînes unicode d'apparence identique peuvent être
      des octets différents (``é`` composé ou décomposé, ``ﬁ`` ligaturé) ;
    - ``strip`` parce qu'un espace de bord est invisible dans un formulaire ;
    - ``casefold`` — et non ``lower`` — parce qu'il replie ce que ``lower``
      laisse passer (``ß`` → ``ss``, ``İ``), et parce qu'un utilisateur qui tape
      ``FloSa`` un jour et ``flosa`` le lendemain attend son compte, pas un
      refus.

    Le nom de dossier, lui, est INJECTIF (``safe_dir_name``) : il ne replie rien
    et ne peut donc pas rattraper une équivalence oubliée ici. C'est voulu — le
    repli est une décision de compte, prise une fois, pas une propriété d'un
    chemin.
    """
    return unicodedata.normalize("NFKC", login).strip().casefold()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AccountError(ValueError):
    """Refus d'une opération d'administration (login pris, inconnu, trop court…)."""


class Account(BaseModel):
    """Un compte, tel que persisté dans le magasin."""

    login: str
    password_hash: str
    active: bool = True
    created_at: str = Field(default_factory=_now)
    password_updated_at: str = Field(default_factory=_now)

    @field_validator("login")
    @classmethod
    def _canonique(cls, login: str) -> str:
        """Un compte lu du disque est normalisé comme un compte créé aujourd'hui.

        Le magasin peut dater d'avant cette règle : la normaliser à la lecture
        évite une migration du fichier de comptes, et fait que la comparaison de
        ``get`` porte des deux côtés sur la même forme.
        """
        return normalize_login(login)


class AccountStore:
    """Comptes locaux d'un fichier YAML, hachés en argon2id.

    ``hasher`` est injectable : les tests l'affaiblissent pour ne pas payer
    64 Mio et trois passes de mémoire dure à chaque cas.
    """

    def __init__(self, path: Path, hasher: PasswordHasher | None = None) -> None:
        self.path = Path(path)
        self.hasher = hasher or PasswordHasher()
        # Empreinte du leurre, calculée au premier besoin : c'est un argon2
        # complet, on ne le paie pas au démarrage de l'application.
        self._empreinte_leurre: str | None = None

    # -- lecture / écriture du fichier ---------------------------------------

    def _read(self) -> list[Account]:
        if not self.path.exists():
            return []  # installation neuve : aucun compte, donc personne n'entre
        try:
            charge = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            comptes = [Account.model_validate(brut) for brut in charge.get("users", [])]
        except (yaml.YAMLError, ValidationError, AttributeError, TypeError) as echec:
            # On échoue FERMÉ : un magasin illisible interdit toute connexion. Le
            # rendre vide en silence donnerait le même effet en laissant croire
            # que le fichier a été lu.
            raise AccountError(f"magasin de comptes illisible : {self.path}") from echec
        self._refuser_les_doublons(comptes)
        return comptes

    @staticmethod
    def _refuser_les_doublons(comptes: list[Account]) -> None:
        """Échoue FERMÉ si deux comptes partagent la même forme canonique.

        Le cas ne peut plus naître de ``create``, mais il peut dormir dans un
        magasin antérieur à la normalisation (``Alice`` et ``alice``). Le
        laisser passer ferait de ces deux comptes un seul dossier de
        conversations : chacun lirait les fils de l'autre. On préfère bloquer
        toute connexion et nommer le doublon à l'opérateur.
        """
        vus: set[str] = set()
        for compte in comptes:
            if compte.login in vus:
                raise AccountError(
                    f"deux comptes se replient sur le même login : {compte.login} "
                    "— renommez-en un avant de redémarrer"
                )
            vus.add(compte.login)

    def _write(self, comptes: list[Account]) -> None:
        make_private_dir(self.path.parent)
        payload = {"users": [compte.model_dump() for compte in comptes]}
        write_text_atomic(
            self.path,
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            mode=FILE_MODE,
        )

    # -- consultation ---------------------------------------------------------

    def list(self) -> list[Account]:
        return sorted(self._read(), key=lambda compte: compte.login)

    def get(self, login: str) -> Account | None:
        """Le compte, quelle que soit la façon dont l'appelant a écrit son login."""
        cherche = normalize_login(login)
        return next((compte for compte in self._read() if compte.login == cherche), None)

    # -- administration -------------------------------------------------------

    def create(self, login: str, password: str) -> Account:
        """Ouvre un compte. Le login est unique ; le mot de passe n'est jamais gardé."""
        login = self._check_login(login)
        self._check_password(password)
        with resource_lock(self.path):
            comptes = self._read()
            if any(compte.login == login for compte in comptes):
                raise AccountError(f"login déjà pris : {login}")
            compte = Account(login=login, password_hash=self.hasher.hash(password))
            comptes.append(compte)
            self._write(comptes)
            return compte

    def set_password(self, login: str, password: str) -> Account:
        """Réinitialise le mot de passe d'un compte."""
        self._check_password(password)
        return self._update(
            login,
            lambda compte: compte.model_copy(
                update={
                    "password_hash": self.hasher.hash(password),
                    "password_updated_at": _now(),
                }
            ),
        )

    def set_active(self, login: str, active: bool) -> Account:
        """Active ou désactive un compte, sans le supprimer ni perdre son historique."""
        return self._update(login, lambda compte: compte.model_copy(update={"active": active}))

    def _update(self, login: str, transformation) -> Account:
        cherche = normalize_login(login)
        with resource_lock(self.path):
            comptes = self._read()
            for index, compte in enumerate(comptes):
                if compte.login == cherche:
                    comptes[index] = transformation(compte)
                    self._write(comptes)
                    return comptes[index]
            raise AccountError(f"login inconnu : {login}")

    @staticmethod
    def _check_login(login: str) -> str:
        """Rend la forme canonique du login, ou refuse d'ouvrir le compte.

        Ce qui est refusé l'est parce qu'un login est aussi un nom de dossier et
        un identifiant qu'un opérateur relit : un espace au milieu ou un
        caractère de contrôle se voit mal dans une liste de comptes et se
        recopie mal dans une commande.
        """
        canonique = normalize_login(login)
        if not canonique:
            raise AccountError("login vide")
        if len(canonique) > LOGIN_MAX_CHARS:
            raise AccountError(f"login trop long (maximum {LOGIN_MAX_CHARS} caractères)")
        if any(caractere.isspace() for caractere in canonique):
            raise AccountError("login contenant une espace")
        if any(caractere.isprintable() is False for caractere in canonique):
            raise AccountError("login contenant un caractère non imprimable")
        return canonique

    def _check_password(self, password: str) -> None:
        if len(password) < PASSWORD_MIN_CHARS:
            raise AccountError(f"mot de passe trop court (minimum {PASSWORD_MIN_CHARS} caractères)")

    # -- authentification -----------------------------------------------------

    def verify(self, login: str, password: str) -> Account | None:
        """Rend le compte si le couple est bon ET le compte actif, sinon ``None``.

        Un seul résultat négatif pour tous les cas — login inconnu, compte
        désactivé, mot de passe faux : l'appelant ne peut donc pas rédiger un
        message qui distingue « ce compte n'existe pas » de « ce mot de passe est
        faux », et l'énumération des comptes n'a plus de canal.

        Le temps de réponse est le même canal : quand il n'y a rien à vérifier,
        on vérifie quand même une empreinte leurre. La comparaison elle-même est
        à temps constant — c'est ``PasswordHasher.verify``, qui compare des
        empreintes, jamais des mots de passe.
        """
        compte = self.get(login)
        if compte is None or not compte.active:
            self._verifier_leurre(password)
            return None
        try:
            self.hasher.verify(compte.password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return None
        if self.hasher.check_needs_rehash(compte.password_hash):
            # Les paramètres argon2 ont durci depuis : on en profite pour
            # remplacer l'empreinte, sans rien demander à l'utilisateur.
            compte = self._update(
                login,
                lambda existant: existant.model_copy(
                    update={"password_hash": self.hasher.hash(password)}
                ),
            )
        return compte

    def _verifier_leurre(self, password: str) -> None:
        if self._empreinte_leurre is None:
            self._empreinte_leurre = self.hasher.hash(LEURRE)
        with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
            self.hasher.verify(self._empreinte_leurre, password)
