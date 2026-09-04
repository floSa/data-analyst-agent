"""Tests du CLI d'administration des comptes (scripts/manage_users.py).

C'est le seul moyen de créer un compte : ce qu'il refuse, personne d'autre ne
le rattrapera.
"""

import io
import secrets
from pathlib import Path

import pytest
import yaml
from manage_users import main

from data_analyst_agent.auth.sessions import SessionStore


@pytest.fixture
def magasin(tmp_path: Path, monkeypatch) -> Path:
    """Comptes ET état d'authentification dans le tmp_path : le CLI ferme les
    sessions ouvertes, il ne doit pas toucher au var/ du poste."""
    monkeypatch.setenv("DAA_AUTH_STATE_DIR", str(tmp_path / "auth"))
    return tmp_path / "users.yaml"


@pytest.fixture
def mot_de_passe() -> str:
    """Tiré au hasard à chaque exécution : aucun mot de passe de test n'existe
    dans le dépôt, donc aucun ne peut se retrouver sur une instance en service."""
    return secrets.token_urlsafe(16)


def saisie(monkeypatch, *reponses: str) -> None:
    """Simule les saisies de `getpass` (mot de passe puis confirmation)."""
    restantes = list(reponses)
    monkeypatch.setattr("manage_users.getpass.getpass", lambda _: restantes.pop(0))


def comptes_de(magasin: Path) -> list[dict]:
    return yaml.safe_load(magasin.read_text(encoding="utf-8"))["users"]


def test_create_ouvre_un_compte(magasin: Path, mot_de_passe: str, monkeypatch, capsys):
    saisie(monkeypatch, mot_de_passe, mot_de_passe)

    assert main(["--accounts", str(magasin), "create", "alice"]) == 0

    assert "compte créé : alice" in capsys.readouterr().out
    (compte,) = comptes_de(magasin)
    assert compte["login"] == "alice"
    assert compte["active"] is True
    assert compte["password_hash"].startswith("$argon2id$")


def test_le_mot_de_passe_ne_passe_pas_par_la_ligne_de_commande(magasin: Path, mot_de_passe: str):
    """`ps` est lisible par tout compte local, et le shell garde son historique :
    il n'existe aucune option pour poser le mot de passe en argument."""
    with pytest.raises(SystemExit):  # argparse refuse l'argument inconnu
        main(["--accounts", str(magasin), "create", "alice", "--password", mot_de_passe])


def test_create_lit_le_mot_de_passe_sur_lentree_standard(
    magasin: Path, mot_de_passe: str, monkeypatch, capsys
):
    """Provisionnement scripté : `--stdin`, sans terminal ni confirmation."""
    monkeypatch.setattr("sys.stdin", io.StringIO(mot_de_passe + "\n"))

    assert main(["--accounts", str(magasin), "create", "alice", "--stdin"]) == 0

    assert comptes_de(magasin)[0]["login"] == "alice"


def test_create_refuse_une_confirmation_differente(
    magasin: Path, mot_de_passe: str, monkeypatch, capsys
):
    saisie(monkeypatch, mot_de_passe, mot_de_passe + "-typo")

    assert main(["--accounts", str(magasin), "create", "alice"]) == 1

    assert "diffèrent" in capsys.readouterr().err
    assert not magasin.exists()


def test_create_refuse_un_mot_de_passe_trop_court(magasin: Path, monkeypatch, capsys):
    saisie(monkeypatch, "court", "court")

    assert main(["--accounts", str(magasin), "create", "alice"]) == 1

    assert "trop court" in capsys.readouterr().err


def test_create_refuse_un_login_deja_pris(magasin: Path, mot_de_passe: str, monkeypatch, capsys):
    saisie(monkeypatch, mot_de_passe, mot_de_passe)
    main(["--accounts", str(magasin), "create", "alice"])
    saisie(monkeypatch, mot_de_passe, mot_de_passe)

    assert main(["--accounts", str(magasin), "create", "alice"]) == 1

    assert "déjà pris" in capsys.readouterr().err


def test_list_sans_compte(magasin: Path, capsys):
    assert main(["--accounts", str(magasin), "list"]) == 0

    assert "aucun compte" in capsys.readouterr().out


def test_list_montre_letat_des_comptes(magasin: Path, mot_de_passe: str, monkeypatch, capsys):
    for login in ("alice", "bob"):
        saisie(monkeypatch, mot_de_passe, mot_de_passe)
        main(["--accounts", str(magasin), "create", login])
    main(["--accounts", str(magasin), "disable", "bob"])
    capsys.readouterr()

    main(["--accounts", str(magasin), "list"])

    sortie = capsys.readouterr().out
    lignes = {ligne.split()[0]: ligne for ligne in sortie.splitlines()[1:]}
    assert "actif" in lignes["alice"]
    assert "désactivé" in lignes["bob"]
    assert mot_de_passe not in sortie  # rien du secret ne ressort, même haché


def test_disable_puis_enable(magasin: Path, mot_de_passe: str, monkeypatch, capsys):
    saisie(monkeypatch, mot_de_passe, mot_de_passe)
    main(["--accounts", str(magasin), "create", "alice"])

    assert main(["--accounts", str(magasin), "disable", "alice"]) == 0
    assert comptes_de(magasin)[0]["active"] is False

    assert main(["--accounts", str(magasin), "enable", "alice"]) == 0
    assert comptes_de(magasin)[0]["active"] is True


def test_disable_ferme_les_sessions_ouvertes(
    magasin: Path, tmp_path: Path, mot_de_passe: str, monkeypatch, capsys
):
    """Sans ça, l'administrateur croit avoir fermé la porte alors que l'onglet
    resté ouvert continue de répondre jusqu'à l'expiration de la session."""
    saisie(monkeypatch, mot_de_passe, mot_de_passe)
    main(["--accounts", str(magasin), "create", "alice"])
    sessions = SessionStore(tmp_path / "auth", 3600, 43200)
    jeton, _ = sessions.create("alice")
    capsys.readouterr()

    main(["--accounts", str(magasin), "disable", "alice"])

    assert sessions.resolve(jeton) is None
    assert "1 session(s) fermée(s)" in capsys.readouterr().out


def test_reset_password_change_le_mot_de_passe_et_ferme_les_sessions(
    magasin: Path, tmp_path: Path, mot_de_passe: str, monkeypatch, capsys
):
    saisie(monkeypatch, mot_de_passe, mot_de_passe)
    main(["--accounts", str(magasin), "create", "alice"])
    ancienne_empreinte = comptes_de(magasin)[0]["password_hash"]
    sessions = SessionStore(tmp_path / "auth", 3600, 43200)
    jeton, _ = sessions.create("alice")

    nouveau = secrets.token_urlsafe(16)
    saisie(monkeypatch, nouveau, nouveau)
    assert main(["--accounts", str(magasin), "reset-password", "alice"]) == 0

    assert comptes_de(magasin)[0]["password_hash"] != ancienne_empreinte
    assert sessions.resolve(jeton) is None


@pytest.mark.parametrize("commande", ["disable", "enable", "reset-password"])
def test_login_inconnu_refuse(magasin: Path, commande: str, monkeypatch, capsys):
    saisie(monkeypatch, "un-mot-de-passe-assez-long", "un-mot-de-passe-assez-long")

    assert main(["--accounts", str(magasin), commande, "jamais-vu"]) == 1

    assert "inconnu" in capsys.readouterr().err


def test_magasin_par_defaut_vient_de_la_configuration(tmp_path: Path, monkeypatch, capsys):
    """Sans `--accounts`, le CLI vise le magasin que l'application lira."""
    monkeypatch.setenv("DAA_AUTH_ACCOUNTS_PATH", str(tmp_path / "ailleurs" / "users.yaml"))
    monkeypatch.setenv("DAA_AUTH_STATE_DIR", str(tmp_path / "auth"))
    mot_de_passe = secrets.token_urlsafe(16)
    saisie(monkeypatch, mot_de_passe, mot_de_passe)

    assert main(["create", "alice"]) == 0

    assert (tmp_path / "ailleurs" / "users.yaml").exists()


def test_une_commande_est_obligatoire():
    with pytest.raises(SystemExit):
        main([])
