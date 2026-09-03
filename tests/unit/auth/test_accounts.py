"""Tests du magasin de comptes : hachage, droits du fichier, non-énumération."""

import os
from pathlib import Path

import pytest
import yaml
from argon2 import PasswordHasher

from data_analyst_agent.auth.accounts import (
    PASSWORD_MIN_CHARS,
    AccountError,
    AccountStore,
)

# argon2 par défaut, c'est 64 Mio et 3 passes par vérification : correct en
# service, insupportable dans une suite de tests. Les paramètres sont encodés
# dans l'empreinte, la nature du hachage (argon2id) ne change pas.
HACHEUR_RAPIDE = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

MOT_DE_PASSE = "correct-cheval-batterie-agrafe"


@pytest.fixture
def store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "comptes" / "users.yaml", hasher=HACHEUR_RAPIDE)


def test_magasin_absent_na_aucun_compte(store: AccountStore):
    """Installation neuve : personne n'entre tant qu'aucun compte n'est créé."""
    assert store.list() == []
    assert store.verify("alice", MOT_DE_PASSE) is None


def test_creer_puis_verifier(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)

    compte = store.verify("alice", MOT_DE_PASSE)

    assert compte is not None
    assert compte.login == "alice"
    assert compte.active


def test_le_mot_de_passe_nest_jamais_ecrit_en_clair(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)

    contenu = store.path.read_text(encoding="utf-8")

    assert MOT_DE_PASSE not in contenu
    empreinte = yaml.safe_load(contenu)["users"][0]["password_hash"]
    # argon2id, pas argon2i/d et surtout pas un hachage rapide : l'empreinte le dit.
    assert empreinte.startswith("$argon2id$")


def test_fichier_en_0600(store: AccountStore):
    """Le fichier porte des empreintes de mots de passe : lui seul les lit."""
    store.create("alice", MOT_DE_PASSE)

    assert oct(os.stat(store.path).st_mode & 0o777) == "0o600"


def test_deux_empreintes_du_meme_mot_de_passe_different(store: AccountStore):
    """Le sel est par compte : deux comptes au même mot de passe ne se trahissent pas."""
    a = store.create("alice", MOT_DE_PASSE)
    b = store.create("bob", MOT_DE_PASSE)

    assert a.password_hash != b.password_hash


def test_mauvais_mot_de_passe_refuse(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)

    assert store.verify("alice", MOT_DE_PASSE + "-faux") is None


def test_compte_desactive_refuse(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)

    store.set_active("alice", False)

    assert store.verify("alice", MOT_DE_PASSE) is None
    assert store.get("alice").active is False


def test_compte_reactive_reprend(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)
    store.set_active("alice", False)

    store.set_active("alice", True)

    assert store.verify("alice", MOT_DE_PASSE) is not None


def test_login_inconnu_et_login_desactive_rendent_le_meme_resultat(store: AccountStore):
    """Non-énumération : ``verify`` ne distingue aucun des trois refus."""
    store.create("alice", MOT_DE_PASSE)
    store.set_active("alice", False)

    assert store.verify("alice", MOT_DE_PASSE) is None  # désactivé
    assert store.verify("jamais-vu", MOT_DE_PASSE) is None  # inconnu
    store.set_active("alice", True)
    assert store.verify("alice", "autre-chose-de-long") is None  # mauvais mot de passe


class HacheurEspion:
    """Délègue à un vrai PasswordHasher (à __slots__, donc non monkeypatchable)
    en notant les vérifications faites."""

    def __init__(self, reel: PasswordHasher) -> None:
        self.reel = reel
        self.verifications: list[str] = []

    def hash(self, mot_de_passe: str) -> str:
        return self.reel.hash(mot_de_passe)

    def verify(self, empreinte: str, mot_de_passe: str) -> bool:
        self.verifications.append(empreinte)
        return self.reel.verify(empreinte, mot_de_passe)

    def check_needs_rehash(self, empreinte: str) -> bool:
        return self.reel.check_needs_rehash(empreinte)


def test_login_inconnu_verifie_quand_meme_une_empreinte(tmp_path: Path):
    """Le temps de réponse est un canal d'énumération : on hache aussi dans le vide."""
    espion = HacheurEspion(HACHEUR_RAPIDE)
    store = AccountStore(tmp_path / "users.yaml", hasher=espion)

    assert store.verify("jamais-vu", MOT_DE_PASSE) is None

    # un argon2 complet, malgré l'absence de compte à vérifier
    assert len(espion.verifications) == 1


def test_compte_desactive_verifie_aussi_une_empreinte(tmp_path: Path):
    """Même raison : un refus instantané dirait « ce compte est fermé »."""
    espion = HacheurEspion(HACHEUR_RAPIDE)
    store = AccountStore(tmp_path / "users.yaml", hasher=espion)
    store.create("alice", MOT_DE_PASSE)
    store.set_active("alice", False)
    espion.verifications.clear()

    assert store.verify("alice", MOT_DE_PASSE) is None

    assert len(espion.verifications) == 1


def test_reinitialiser_le_mot_de_passe(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)

    store.set_password("alice", "un-tout-autre-mot-de-passe")

    assert store.verify("alice", MOT_DE_PASSE) is None
    assert store.verify("alice", "un-tout-autre-mot-de-passe") is not None


def test_login_deja_pris(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)

    with pytest.raises(AccountError, match="déjà pris"):
        store.create("alice", "un-autre-mot-de-passe-long")


def test_login_vide_refuse(store: AccountStore):
    with pytest.raises(AccountError, match="login vide"):
        store.create("   ", MOT_DE_PASSE)


def test_login_inconnu_en_administration(store: AccountStore):
    with pytest.raises(AccountError, match="inconnu"):
        store.set_password("jamais-vu", MOT_DE_PASSE)
    with pytest.raises(AccountError, match="inconnu"):
        store.set_active("jamais-vu", False)


@pytest.mark.parametrize("court", ["", "court", "x" * (PASSWORD_MIN_CHARS - 1)])
def test_mot_de_passe_trop_court_refuse(store: AccountStore, court: str):
    with pytest.raises(AccountError, match="trop court"):
        store.create("alice", court)


def test_lister_trie_par_login(store: AccountStore):
    store.create("zoe", MOT_DE_PASSE)
    store.create("alice", MOT_DE_PASSE)

    assert [c.login for c in store.list()] == ["alice", "zoe"]


def test_magasin_illisible_echoue_ferme(store: AccountStore):
    """Un fichier corrompu doit interdire les connexions, pas les laisser passer
    en se faisant passer pour un magasin vide."""
    store.create("alice", MOT_DE_PASSE)
    store.path.write_text("users: [ ceci n'est pas: du yaml: valide", encoding="utf-8")

    with pytest.raises(AccountError, match="illisible"):
        store.verify("alice", MOT_DE_PASSE)


def test_magasin_de_forme_inattendue_echoue_ferme(store: AccountStore):
    store.create("alice", MOT_DE_PASSE)
    store.path.write_text("users:\n  - pas_un_compte: vrai\n", encoding="utf-8")

    with pytest.raises(AccountError, match="illisible"):
        store.list()


def test_empreinte_rehachee_quand_les_parametres_durcissent(tmp_path: Path):
    """Durcir argon2 ne doit pas invalider les comptes : l'empreinte se remplace
    à la connexion suivante, sans rien demander à l'utilisateur."""
    faible = AccountStore(tmp_path / "users.yaml", hasher=HACHEUR_RAPIDE)
    faible.create("alice", MOT_DE_PASSE)
    ancienne = faible.get("alice").password_hash

    durci = AccountStore(
        tmp_path / "users.yaml", hasher=PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    )
    assert durci.verify("alice", MOT_DE_PASSE) is not None

    assert durci.get("alice").password_hash != ancienne
    assert durci.verify("alice", MOT_DE_PASSE) is not None  # et le compte marche toujours
