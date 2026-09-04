"""Un compte, un login, un dossier.

Le login devient le nom du dossier où vivent les conversations d'un compte
(``workspace_dir/<login>/``). Deux comptes qui ne diffèrent que par la casse ou
par une espace de bord seraient deux comptes pour l'utilisateur et un seul
dossier pour le service : chacun lirait les fils de l'autre.

Le repli est fait ICI, au niveau du compte, et pas dans le nom de dossier — qui
est injectif par construction (cf. ``test_safe_dir_name``) et ne rattrape donc
aucune équivalence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data_analyst_agent.auth.accounts import (
    LOGIN_MAX_CHARS,
    AccountError,
    AccountStore,
    normalize_login,
)
from data_analyst_agent.orchestrator.workspace import safe_dir_name
from helpers.auth import HACHEUR_RAPIDE

MOT_DE_PASSE = "un-mot-de-passe-assez-long"


@pytest.fixture
def store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "users.yaml", hasher=HACHEUR_RAPIDE)


# -- la règle elle-même -------------------------------------------------------


@pytest.mark.parametrize(
    ("saisi", "canonique"),
    [
        ("floSa", "flosa"),
        ("FLOSA", "flosa"),
        ("  flosa  ", "flosa"),
        ("\tflosa\n", "flosa"),
        ("Élodie", "élodie"),
        ("STRASSE", "strasse"),
        ("straße", "strasse"),  # casefold replie ce que lower laisse passer
        ("ﬁnance", "finance"),  # NFKC déligature
        ("alice", "alice"),
    ],
)
def test_forme_canonique(saisi: str, canonique: str) -> None:
    assert normalize_login(saisi) == canonique


def test_les_formes_unicode_equivalentes_donnent_le_meme_compte() -> None:
    """`é` composé et `é` décomposé sont deux suites d'octets, un seul compte."""
    compose = "café"
    decompose = "café"
    assert compose != decompose
    assert normalize_login(compose) == normalize_login(decompose)


def test_deux_graphies_du_meme_login_donnent_le_meme_dossier() -> None:
    """C'est la propriété qui compte : la normalisation PUIS l'encodage."""
    assert safe_dir_name(normalize_login("floSa")) == safe_dir_name(normalize_login("  FLOSA "))


def test_deux_logins_reellement_distincts_gardent_deux_dossiers() -> None:
    assert safe_dir_name(normalize_login("alice")) != safe_dir_name(normalize_login("alice2"))
    assert safe_dir_name(normalize_login("élodie")) != safe_dir_name(normalize_login("elodie"))


# -- effet sur le magasin de comptes -----------------------------------------


def test_le_compte_est_persiste_sous_sa_forme_canonique(store: AccountStore) -> None:
    compte = store.create("  FloSa ", MOT_DE_PASSE)

    assert compte.login == "flosa"
    charge = yaml.safe_load(store.path.read_text(encoding="utf-8"))
    assert [u["login"] for u in charge["users"]] == ["flosa"]


def test_deux_graphies_ne_font_pas_deux_comptes(store: AccountStore) -> None:
    store.create("floSa", MOT_DE_PASSE)

    with pytest.raises(AccountError, match="déjà pris"):
        store.create("FLOSA", MOT_DE_PASSE)


def test_la_connexion_accepte_une_autre_graphie(store: AccountStore) -> None:
    store.create("flosa", MOT_DE_PASSE)

    compte = store.verify("  FloSa  ", MOT_DE_PASSE)

    assert compte is not None
    assert compte.login == "flosa"


def test_l_administration_vise_le_compte_pas_sa_graphie(store: AccountStore) -> None:
    store.create("flosa", MOT_DE_PASSE)

    store.set_active("FloSa", False)

    compte = store.get("flosa")
    assert compte is not None
    assert compte.active is False


@pytest.mark.parametrize(
    "refuse",
    ["", "   ", "\t\n", "ali ce", "a\x00b", "a\x7fb", "x" * (LOGIN_MAX_CHARS + 1)],
)
def test_les_logins_impraticables_sont_refuses(store: AccountStore, refuse: str) -> None:
    with pytest.raises(AccountError):
        store.create(refuse, MOT_DE_PASSE)


def test_un_login_unicode_reste_acceptable(store: AccountStore) -> None:
    """La normalisation replie les équivalences, elle n'interdit pas l'unicode."""
    compte = store.create("Élodie", MOT_DE_PASSE)

    assert compte.login == "élodie"
    assert store.verify("ÉLODIE", MOT_DE_PASSE) is not None


# -- magasin antérieur à la règle --------------------------------------------


def test_un_magasin_ancien_est_normalise_a_la_lecture(store: AccountStore) -> None:
    """Pas de migration du fichier de comptes : la lecture replie."""
    store.create("alice", MOT_DE_PASSE)
    brut = yaml.safe_load(store.path.read_text(encoding="utf-8"))
    brut["users"][0]["login"] = "  Alice "
    store.path.write_text(yaml.safe_dump(brut, allow_unicode=True), encoding="utf-8")

    compte = store.get("alice")

    assert compte is not None
    assert compte.login == "alice"


def test_un_magasin_ancien_a_doublons_bloque_tout(store: AccountStore) -> None:
    """Échouer fermé : deux comptes sur un dossier, c'est une fuite entre eux."""
    store.create("alice", MOT_DE_PASSE)
    brut = yaml.safe_load(store.path.read_text(encoding="utf-8"))
    brut["users"].append({**brut["users"][0], "login": "ALICE"})
    store.path.write_text(yaml.safe_dump(brut, allow_unicode=True), encoding="utf-8")

    with pytest.raises(AccountError, match="même login"):
        store.get("alice")
