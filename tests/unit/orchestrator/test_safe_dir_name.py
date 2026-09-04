"""Le nom de dossier doit être sûr ET injectif.

Sûr : rien de ce qu'un client envoie ne doit sortir du dossier de travail.
Injectif : deux identifiants distincts ne doivent jamais désigner le même
dossier. Le second point n'était pas tenu (audit §6.3) et devient une faille de
cloisonnement dès qu'un **login** entre dans le chemin — deux comptes dont les
logins ne diffèrent que par la ponctuation partageraient leurs conversations.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from data_analyst_agent.orchestrator.workspace import (
    CARACTERES_SURS,
    LONGUEUR_MAX,
    safe_dir_name,
    user_dir,
)

# Ce qu'un client peut écrire dans un `conversation_id` ou un login pour
# essayer de sortir du dossier de travail.
HOSTILES = [
    "..",
    "../..",
    "../../etc/passwd",
    "/",
    "/etc/passwd",
    ".",
    "./.",
    "",
    "....//....//etc",
    "conv/../../autre",
    "\\..\\..",
    "~",
    "~2f",  # imite une séquence d'échappement
    "a\x00b",
    "\n",
    " ",
    "...",
    "%2e%2e%2f",
]


@pytest.mark.parametrize("hostile", HOSTILES)
def test_aucun_nom_hostile_ne_sort_du_dossier(tmp_path: Path, hostile: str) -> None:
    """Le chemin obtenu reste sous la base, quoi qu'on lui donne."""
    chemin = (tmp_path / safe_dir_name(hostile)).resolve()
    assert chemin.parent == tmp_path.resolve()
    assert chemin != tmp_path.resolve()


@pytest.mark.parametrize("hostile", HOSTILES)
def test_un_nom_hostile_donne_un_seul_segment_utilisable(hostile: str) -> None:
    """Ni séparateur, ni `.`/`..`, ni nom vide : un segment de chemin ordinaire."""
    nom = safe_dir_name(hostile)
    assert nom
    assert "/" not in nom
    assert "\\" not in nom
    assert nom not in {".", ".."}
    assert "\x00" not in nom


# Le cas de l'audit §6.3 : trois identifiants distincts, un seul dossier.
COLLISIONS_HISTORIQUES = ["a/b", "a.b", "a b", "a_b", "a-b", "a__b", "a!b", "a@b"]


def test_les_collisions_de_ponctuation_sont_levees() -> None:
    """`a/b`, `a.b` et `a b` tombaient tous sur `a_b`. Plus maintenant."""
    dossiers = [safe_dir_name(nom) for nom in COLLISIONS_HISTORIQUES]
    assert len(set(dossiers)) == len(COLLISIONS_HISTORIQUES), dict(
        zip(COLLISIONS_HISTORIQUES, dossiers, strict=True)
    )


@pytest.mark.parametrize(
    ("gauche", "droite"),
    [
        ("alice", "alice."),
        ("alice", "alice "),
        ("bob/../alice", "bob_.._alice"),
        ("é", "e"),
        ("élodie", "elodie"),
        ("Élodie", "élodie"),  # la casse n'est PAS repliée ici (cf. accounts)
        ("北京", "beijing"),
        ("ali~ce", "ali~7ece"),
        ("", "~vide"),  # l'image du nom vide n'est produisible par rien d'autre
        ("", "~"),
        ("~", "~7e"),
    ],
)
def test_deux_noms_distincts_donnent_deux_dossiers_distincts(gauche: str, droite: str) -> None:
    assert safe_dir_name(gauche) != safe_dir_name(droite)


def test_un_login_unicode_est_accepte_et_reste_distinct() -> None:
    """Un login unicode ne doit ni échouer ni se replier sur un voisin."""
    logins = ["élodie", "elodie", "ELODIE", "北京", "ελένη", "Ωμέγα", "أحمد", "🐈"]
    dossiers = [safe_dir_name(login) for login in logins]
    assert len(set(dossiers)) == len(logins)
    for dossier in dossiers:
        assert set(dossier) <= CARACTERES_SURS | {"~"}


def test_les_formes_unicode_equivalentes_restent_distinctes_ici() -> None:
    """`safe_dir_name` encode des octets, il ne normalise pas.

    C'est délibéré : la normalisation unicode est une décision de **compte**
    (cf. `auth.accounts.normalize_login`), prise une fois à la création, et pas
    une propriété d'un nom de dossier. Ici, deux chaînes différentes donnent
    deux dossiers différents — un point c'est tout.
    """
    compose = unicodedata.normalize("NFC", "é")
    decompose = unicodedata.normalize("NFD", "é")
    assert compose != decompose
    assert safe_dir_name(compose) != safe_dir_name(decompose)


def test_les_identifiants_deja_sur_disque_traversent_inchanges() -> None:
    """Les uuid hexadécimaux et les noms de démonstration ne bougent pas.

    C'est ce qui permet de changer l'encodage sans rien migrer côté noms : les
    11 conversations en service s'appellent toutes `[0-9a-f]{32}` ou
    `demo-<sujet>-<horodatage>`.
    """
    for identifiant in [
        "fdc90dfc56c347eeb92bd8a41b31309a",
        "demo-ca-1784705380",
        "demo-jours-1784705735",
        "conv_1",
    ]:
        assert safe_dir_name(identifiant) == identifiant


def test_un_nom_tres_long_reste_un_nom_de_dossier_valide() -> None:
    """255 octets est la limite d'ext4 ; l'échappement peut quadrupler la taille."""
    long = "é" * 500
    nom = safe_dir_name(long)
    assert len(nom.encode("utf-8")) <= 255
    assert len(nom) <= LONGUEUR_MAX


def test_deux_noms_longs_de_meme_prefixe_restent_distincts() -> None:
    """Le repli garde l'empreinte du nom COMPLET, pas seulement son préfixe."""
    prefixe = "é" * 500
    assert safe_dir_name(prefixe + "a") != safe_dir_name(prefixe + "b")


def test_un_nom_replie_ne_peut_pas_imiter_un_nom_court() -> None:
    """Le marqueur de repli `~~` n'est pas produisible par l'encodage normal."""
    court = safe_dir_name("é" * 10)
    long = safe_dir_name("é" * 500)
    assert "~~" not in court
    assert "~~" in long


def test_user_dir_range_l_utilisateur_sous_la_base(tmp_path: Path) -> None:
    assert user_dir(tmp_path, "alice") == tmp_path / "alice"
    assert user_dir(tmp_path, "../root").resolve().parent == tmp_path.resolve()
