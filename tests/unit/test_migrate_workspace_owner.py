"""Reprise de l'existant : les conversations d'avant passent sous un propriétaire.

Ce que ces tests tiennent, dans l'ordre où ça compte pour qui va lancer la
commande sur des données réelles : elle ne fait rien tant qu'on ne le lui
demande pas, elle ne fait rien deux fois, et elle refuse plutôt que de faire à
moitié.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from migrate_workspace_owner import MigrationError, construire_le_plan, main

from data_analyst_agent.orchestrator.conversations import Conversation, ConversationStore
from data_analyst_agent.orchestrator.workspace import LOCKS_DIR

PROPRIETAIRE = "flosa"
TRANSCRIPT = ConversationStore.TRANSCRIPT


def _owner_de(dossier: Path) -> str:
    return json.loads((dossier / TRANSCRIPT).read_text(encoding="utf-8"))["owner"]


def _ecrire_fil(dossier: Path, conversation_id: str, owner: str = "") -> None:
    charge = Conversation(
        id=conversation_id, owner=owner, title=f"titre de {conversation_id}"
    ).model_dump()
    charge["messages"] = [{"role": "user", "content": f"question de {conversation_id}"}]
    (dossier / TRANSCRIPT).write_text(json.dumps(charge), encoding="utf-8")


def _poser(
    workspace: Path, conversation_id: str, owner: str = "", *, memoire: bool = False
) -> Path:
    """Crée une conversation dans ``workspace`` et rend son dossier."""
    dossier = workspace / conversation_id
    dossier.mkdir(parents=True, exist_ok=True)
    _ecrire_fil(dossier, conversation_id, owner)
    if memoire:
        (dossier / "resultat_1.csv").write_text("a\n1\n", encoding="utf-8")
        (dossier / "manifest.json").write_text('{"artifacts": []}', encoding="utf-8")
    return dossier


def _lancer(workspace: Path, *drapeaux: str, owner: str = "floSa") -> int:
    return main(["--workspace", str(workspace), "--owner", owner, *drapeaux])


# -- les cas où il n'y a rien à faire ----------------------------------------


def test_dossier_inexistant(tmp_path: Path, capsys) -> None:
    assert _lancer(tmp_path / "jamais-cree") == 0
    assert "rien à faire" in capsys.readouterr().out


def test_dossier_vide(tmp_path: Path, capsys) -> None:
    (tmp_path / "workspaces").mkdir()

    assert _lancer(tmp_path / "workspaces", "--appliquer") == 0
    assert "rien à faire" in capsys.readouterr().out


# -- mode à blanc -------------------------------------------------------------


def test_le_mode_a_blanc_est_le_defaut_et_n_ecrit_rien(tmp_path: Path, capsys) -> None:
    _poser(tmp_path, "conv-1")
    avant = (tmp_path / "conv-1" / TRANSCRIPT).read_bytes()

    assert _lancer(tmp_path) == 0

    sortie = capsys.readouterr().out
    assert "MODE À BLANC" in sortie
    assert "conv-1" in sortie
    assert (tmp_path / "conv-1" / TRANSCRIPT).read_bytes() == avant
    assert not (tmp_path / PROPRIETAIRE).exists()


# -- migration nominale -------------------------------------------------------


def test_les_fils_passent_sous_le_proprietaire_avec_leur_memoire(tmp_path: Path) -> None:
    _poser(tmp_path, "conv-1", memoire=True)
    _poser(tmp_path, "demo-ca-1784705380", memoire=True)

    assert _lancer(tmp_path, "--appliquer") == 0

    for identifiant in ["conv-1", "demo-ca-1784705380"]:
        range_ = tmp_path / PROPRIETAIRE / identifiant
        assert range_.is_dir()
        assert not (tmp_path / identifiant).exists()
        assert (range_ / "resultat_1.csv").read_text(encoding="utf-8") == "a\n1\n"
        assert (
            json.loads((range_ / TRANSCRIPT).read_text(encoding="utf-8"))["owner"] == PROPRIETAIRE
        )


def test_les_messages_ne_sont_pas_touches(tmp_path: Path) -> None:
    """Seul l'`owner` change : la migration ne réécrit pas le fil."""
    _poser(tmp_path, "conv-1")
    avant = json.loads((tmp_path / "conv-1" / TRANSCRIPT).read_text(encoding="utf-8"))

    _lancer(tmp_path, "--appliquer")

    apres = json.loads(
        (tmp_path / PROPRIETAIRE / "conv-1" / TRANSCRIPT).read_text(encoding="utf-8")
    )
    assert apres.pop("owner") == PROPRIETAIRE
    avant.pop("owner")
    assert apres == avant


def test_l_application_retrouve_les_fils_migres(tmp_path: Path) -> None:
    """La preuve qui compte : le magasin du compte les liste après coup."""
    _poser(tmp_path, "conv-1")
    _poser(tmp_path, "conv-2")

    _lancer(tmp_path, "--appliquer")

    assert sorted(c.id for c in ConversationStore(tmp_path, PROPRIETAIRE).list()) == [
        "conv-1",
        "conv-2",
    ]
    assert ConversationStore(tmp_path, "quelquun-dautre").list() == []


def test_le_proprietaire_est_normalise_comme_un_compte(tmp_path: Path, capsys) -> None:
    """`--owner floSa` doit viser le dossier où l'application ira chercher."""
    _poser(tmp_path, "conv-1")

    _lancer(tmp_path, "--appliquer", owner="  FloSa ")

    assert (tmp_path / "flosa" / "conv-1").is_dir()
    assert "propriétaire : flosa" in capsys.readouterr().out


def test_un_proprietaire_vide_est_refuse(tmp_path: Path, capsys) -> None:
    assert _lancer(tmp_path, owner="   ") == 1
    assert "refusé" in capsys.readouterr().err


# -- idempotence --------------------------------------------------------------


def test_la_seconde_execution_ne_fait_rien(tmp_path: Path, capsys) -> None:
    _poser(tmp_path, "conv-1", memoire=True)
    _lancer(tmp_path, "--appliquer")
    capsys.readouterr()
    apres_le_premier = (tmp_path / PROPRIETAIRE / "conv-1" / TRANSCRIPT).read_bytes()

    assert _lancer(tmp_path, "--appliquer") == 0

    sortie = capsys.readouterr().out
    assert "rien à faire" in sortie
    assert (tmp_path / PROPRIETAIRE / "conv-1" / TRANSCRIPT).read_bytes() == apres_le_premier


def test_une_migration_interrompue_se_reprend(tmp_path: Path) -> None:
    """Un fil déjà rangé mais pas estampillé est rattrapé, pas ignoré.

    Il ne peut pas naître de la commande elle-même — elle estampille AVANT de
    déplacer — mais bien d'un dossier restauré ou recopié à la main.
    """
    range_sans_estampille = tmp_path / PROPRIETAIRE / "conv-range"
    range_sans_estampille.mkdir(parents=True)
    _ecrire_fil(range_sans_estampille, "conv-range")
    _poser(tmp_path, "conv-a-la-racine")

    assert _lancer(tmp_path, "--appliquer") == 0

    for identifiant in ["conv-range", "conv-a-la-racine"]:
        assert _owner_de(tmp_path / PROPRIETAIRE / identifiant) == PROPRIETAIRE


# -- refus plutôt que moitié --------------------------------------------------


def test_une_collision_de_noms_annule_tout_le_lot(tmp_path: Path, capsys) -> None:
    """Écraser serait perdre un fil ; en déplacer la moitié laisserait un état bâtard."""
    _poser(tmp_path, "conv-1")
    _poser(tmp_path, "conv-2")
    deja_range = tmp_path / PROPRIETAIRE / "conv-1"
    deja_range.mkdir(parents=True)
    _ecrire_fil(deja_range, "conv-1", owner=PROPRIETAIRE)

    assert _lancer(tmp_path, "--appliquer") == 1

    assert "collision" in capsys.readouterr().err
    assert (tmp_path / "conv-1").is_dir(), "aucun fil ne doit avoir bougé"
    assert (tmp_path / "conv-2").is_dir()
    assert not (tmp_path / PROPRIETAIRE / "conv-2").exists()


def test_un_fil_qui_porte_le_nom_du_proprietaire_est_refuse(tmp_path: Path) -> None:
    _poser(tmp_path, PROPRIETAIRE)

    with pytest.raises(MigrationError, match="CONVERSATION"):
        construire_le_plan(tmp_path, PROPRIETAIRE)


# -- ce à quoi on ne touche pas ----------------------------------------------


def test_une_transcription_illisible_est_signalee_et_laissee_en_place(
    tmp_path: Path, capsys
) -> None:
    _poser(tmp_path, "conv-1")
    abimee = tmp_path / "conv-abimee"
    abimee.mkdir()
    (abimee / TRANSCRIPT).write_text("{ pas du json", encoding="utf-8")

    assert _lancer(tmp_path, "--appliquer") == 0

    sortie = capsys.readouterr().out
    assert "illisible" in sortie
    assert (abimee / TRANSCRIPT).exists()
    assert (tmp_path / PROPRIETAIRE / "conv-1").is_dir()


def test_les_racines_des_autres_utilisateurs_ne_bougent_pas(tmp_path: Path, capsys) -> None:
    """Une migration relancée après qu'un autre compte a écrit ne le déloge pas."""
    _poser(tmp_path, "conv-1")
    autre = tmp_path / "bob"
    _poser(autre, "conv-de-bob", owner="bob")

    assert _lancer(tmp_path, "--appliquer") == 0

    assert (autre / "conv-de-bob" / TRANSCRIPT).exists()
    assert (
        json.loads((autre / "conv-de-bob" / TRANSCRIPT).read_text(encoding="utf-8"))["owner"]
        == "bob"
    )
    assert "ignorée(s)" in capsys.readouterr().out


def test_un_fichier_a_la_racine_est_ignore(tmp_path: Path) -> None:
    _poser(tmp_path, "conv-1")
    (tmp_path / "note.txt").write_text("bonjour", encoding="utf-8")

    assert _lancer(tmp_path, "--appliquer") == 0
    assert (tmp_path / "note.txt").exists()


def test_le_verrou_reste_a_la_racine_est_retire(tmp_path: Path) -> None:
    """Le verrou d'un fil déplacé ne sérialise plus rien : il vit sous la cible."""
    _poser(tmp_path, "conv-1")
    verrous = tmp_path / LOCKS_DIR
    verrous.mkdir()
    (verrous / "conv-1.lock").touch()

    assert _lancer(tmp_path, "--appliquer") == 0

    assert not verrous.exists(), "le dossier de verrous vidé doit disparaître"


def test_un_verrou_dun_autre_fil_survit(tmp_path: Path) -> None:
    _poser(tmp_path, "conv-1")
    verrous = tmp_path / LOCKS_DIR
    verrous.mkdir()
    (verrous / "conv-1.lock").touch()
    (verrous / "bob.lock").touch()

    _lancer(tmp_path, "--appliquer")

    assert (verrous / "bob.lock").exists()
    assert not (verrous / "conv-1.lock").exists()
