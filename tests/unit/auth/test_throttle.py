"""Tests de l'anti-force brute : verrouillage par compte ET par adresse."""

import os
from pathlib import Path

import pytest

from data_analyst_agent.auth.throttle import LoginThrottle

SEUIL = 3
DUREE = 300.0


class Horloge:
    def __init__(self, depart: float = 1000.0) -> None:
        self.maintenant = depart

    def __call__(self) -> float:
        return self.maintenant

    def avance(self, secondes: float) -> None:
        self.maintenant += secondes


@pytest.fixture
def horloge() -> Horloge:
    return Horloge()


@pytest.fixture
def throttle(tmp_path: Path, horloge: Horloge) -> LoginThrottle:
    return LoginThrottle(tmp_path / "auth", SEUIL, DUREE, clock=horloge)


def test_rien_nest_verrouille_au_depart(throttle: LoginThrottle):
    assert throttle.locked("alice", "10.0.0.1") is False


def test_verrouillage_au_seuil(throttle: LoginThrottle):
    for _ in range(SEUIL - 1):
        throttle.record_failure("alice", "10.0.0.1")
    assert throttle.locked("alice", "10.0.0.1") is False  # encore sous le seuil

    throttle.record_failure("alice", "10.0.0.1")

    assert throttle.locked("alice", "10.0.0.1") is True


def test_le_verrou_expire(throttle: LoginThrottle, horloge: Horloge):
    """Une échéance, pas un blocage définitif : verrouiller un compte est aussi
    une arme contre son propriétaire."""
    for _ in range(SEUIL):
        throttle.record_failure("alice", "10.0.0.1")

    horloge.avance(DUREE)

    assert throttle.locked("alice", "10.0.0.1") is False


def test_verrouillage_par_compte_depuis_plusieurs_adresses(throttle: LoginThrottle):
    """Un dictionnaire sur un login connu, réparti sur mille machines, doit
    quand même fermer le compte."""
    for i in range(SEUIL):
        throttle.record_failure("alice", f"10.0.0.{i}")

    assert throttle.locked("alice", "10.0.0.200") is True
    assert throttle.locked("bob", "10.0.0.200") is False  # un autre compte passe


def test_verrouillage_par_adresse_sur_plusieurs_comptes(throttle: LoginThrottle):
    """Un balayage de logins depuis une seule machine ne verrouille aucun compte :
    sans compteur par adresse, il passerait sous le radar."""
    for i in range(SEUIL):
        throttle.record_failure(f"victime-{i}", "10.0.0.1")

    assert throttle.locked("encore-un-autre", "10.0.0.1") is True
    assert throttle.locked("encore-un-autre", "10.0.0.2") is False  # une autre adresse passe


def test_connexion_reussie_efface_les_compteurs(throttle: LoginThrottle):
    for _ in range(SEUIL - 1):
        throttle.record_failure("alice", "10.0.0.1")

    throttle.reset("alice", "10.0.0.1")

    for _ in range(SEUIL - 1):
        throttle.record_failure("alice", "10.0.0.1")
    assert throttle.locked("alice", "10.0.0.1") is False  # le compteur est reparti de zéro


def test_echecs_espaces_ne_verrouillent_pas(throttle: LoginThrottle, horloge: Horloge):
    """Trois fautes de frappe étalées sur un mois ne sont pas une attaque."""
    for _ in range(SEUIL * 2):
        throttle.record_failure("alice", "10.0.0.1")
        horloge.avance(DUREE)

    assert throttle.locked("alice", "10.0.0.1") is False


def test_etat_survit_a_un_redemarrage(tmp_path: Path, horloge: Horloge):
    """Un compteur en mémoire se remet à zéro au redémarrage — et n'existe pas
    pour les autres workers uvicorn."""
    premier = LoginThrottle(tmp_path / "auth", SEUIL, DUREE, clock=horloge)
    for _ in range(SEUIL):
        premier.record_failure("alice", "10.0.0.1")

    redemarre = LoginThrottle(tmp_path / "auth", SEUIL, DUREE, clock=horloge)

    assert redemarre.locked("alice", "10.0.0.1") is True


def test_fichier_en_0600(throttle: LoginThrottle):
    throttle.record_failure("alice", "10.0.0.1")

    assert oct(os.stat(throttle.path).st_mode & 0o777) == "0o600"


def test_compteurs_eteints_sont_purges(throttle: LoginThrottle, horloge: Horloge):
    """Le fichier ne doit pas garder une ligne par login jamais réessayé."""
    throttle.record_failure("alice", "10.0.0.1")

    horloge.avance(DUREE + 1)
    throttle.record_failure("bob", "10.0.0.2")

    import json

    assert sorted(json.loads(throttle.path.read_text(encoding="utf-8"))) == [
        "adresse:10.0.0.2",
        "login:bob",
    ]


def test_etat_illisible_ne_verrouille_personne(throttle: LoginThrottle):
    throttle.record_failure("alice", "10.0.0.1")
    throttle.path.write_text("{ pas du json", encoding="utf-8")

    assert throttle.locked("alice", "10.0.0.1") is False


def test_reset_sur_des_compteurs_absents_ne_fait_rien(throttle: LoginThrottle):
    throttle.reset("jamais-vu", "10.0.0.9")

    assert throttle.locked("jamais-vu", "10.0.0.9") is False
