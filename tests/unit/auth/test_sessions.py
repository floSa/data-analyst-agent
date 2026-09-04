"""Tests des sessions côté serveur : opacité, expiration, révocation, concurrence."""

import json
import os
import threading
from pathlib import Path

import pytest

from data_analyst_agent.auth.sessions import SessionStore

IDLE = 60.0
ABSOLU = 600.0


class Horloge:
    """Horloge pilotée : une expiration se teste en avançant le temps, pas en dormant."""

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
def store(tmp_path: Path, horloge: Horloge) -> SessionStore:
    return SessionStore(tmp_path / "auth", IDLE, ABSOLU, clock=horloge)


def test_session_ouverte_est_resolue(store: SessionStore):
    jeton, session = store.create("alice")

    retrouvee = store.resolve(jeton)

    assert retrouvee is not None
    assert retrouvee.login == "alice"
    assert session.csrf_token


def test_jeton_opaque_et_imprevisible(store: SessionStore):
    """Aucune donnée dans le jeton : il ne se décode pas et ne se forge pas."""
    premier, _ = store.create("alice")
    second, _ = store.create("alice")

    assert premier != second
    assert "alice" not in premier
    assert len(premier) >= 40  # 32 octets d'aléa, encodés urlsafe


def test_le_jeton_nest_pas_ecrit_sur_disque(store: SessionStore):
    """Le fichier ne garde que l'empreinte : volé, il ne rend aucune session."""
    jeton, _ = store.create("alice")

    contenu = store.path.read_text(encoding="utf-8")

    assert jeton not in contenu
    assert list(json.loads(contenu)) != [jeton]


def test_fichier_de_sessions_en_0600(store: SessionStore):
    store.create("alice")

    assert oct(os.stat(store.path).st_mode & 0o777) == "0o600"


def test_jeton_inconnu_ou_absent(store: SessionStore):
    assert store.resolve("jeton-invente") is None
    assert store.resolve(None) is None
    assert store.resolve("") is None


def test_expiration_par_inactivite(store: SessionStore, horloge: Horloge):
    jeton, _ = store.create("alice")

    horloge.avance(IDLE + 1)

    assert store.resolve(jeton) is None


def test_activite_repousse_lexpiration_par_inactivite(store: SessionStore, horloge: Horloge):
    """Le délai court depuis la dernière requête, pas depuis la connexion."""
    jeton, _ = store.create("alice")

    for _ in range(5):
        horloge.avance(IDLE - 1)
        assert store.resolve(jeton) is not None


def test_expiration_absolue_malgre_lactivite(store: SessionStore, horloge: Horloge):
    """Un onglet qui rafraîchit sans fin ne doit pas garder une session éternelle."""
    jeton, _ = store.create("alice")

    for _ in range(int(ABSOLU // (IDLE - 1)) + 2):
        horloge.avance(IDLE - 1)
        if store.resolve(jeton) is None:
            break
    else:
        pytest.fail("la session a survécu à sa durée absolue")

    assert horloge.maintenant >= 1000.0 + ABSOLU


def test_deconnexion_revoque_vraiment(store: SessionStore):
    """La raison d'être des sessions côté serveur : la révocation prend effet."""
    jeton, _ = store.create("alice")

    assert store.revoke(jeton) is True

    assert store.resolve(jeton) is None
    assert store.revoke(jeton) is False  # deux fois n'est pas une erreur
    assert store.revoke(None) is False


def test_revoquer_toutes_les_sessions_dun_compte(store: SessionStore):
    a1, _ = store.create("alice")
    a2, _ = store.create("alice")
    b1, _ = store.create("bob")

    assert store.revoke_login("alice") == 2

    assert store.resolve(a1) is None
    assert store.resolve(a2) is None
    assert store.resolve(b1) is not None
    assert store.revoke_login("personne") == 0


def test_sessions_survivent_a_un_redemarrage(tmp_path: Path, horloge: Horloge):
    """L'état est sur disque : redémarrer le serveur ne déconnecte pas tout le monde."""
    premier = SessionStore(tmp_path / "auth", IDLE, ABSOLU, clock=horloge)
    jeton, _ = premier.create("alice")

    redemarre = SessionStore(tmp_path / "auth", IDLE, ABSOLU, clock=horloge)

    assert redemarre.resolve(jeton).login == "alice"


def test_sessions_expirees_sont_purgees(store: SessionStore, horloge: Horloge):
    """Le fichier ne doit pas garder une ligne par session jamais refermée."""
    store.create("alice")
    horloge.avance(ABSOLU + 1)

    store.create("bob")

    assert len(json.loads(store.path.read_text(encoding="utf-8"))) == 1


def test_magasin_illisible_redemande_une_connexion(store: SessionStore):
    """Échouer ici n'accorde jamais de session : au pire on se reconnecte."""
    jeton, _ = store.create("alice")
    store.path.write_text("{ ceci n'est pas du json", encoding="utf-8")

    assert store.resolve(jeton) is None


def test_connexions_simultanees_ne_sen_perdent_aucune(tmp_path: Path):
    """Le magasin a le lecture-modification-écriture des conversations : sans le
    verrou, deux connexions simultanées n'en laissaient qu'une sur disque."""
    store = SessionStore(tmp_path / "auth", IDLE, ABSOLU)
    jetons: list[str] = []
    depart = threading.Barrier(8)

    def ouvrir(index: int) -> None:
        depart.wait()
        jeton, _ = store.create(f"utilisateur-{index}")
        jetons.append(jeton)

    fils = [threading.Thread(target=ouvrir, args=(i,)) for i in range(8)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    assert len(jetons) == 8
    assert all(store.resolve(jeton) is not None for jeton in jetons)
