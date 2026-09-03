"""Concurrence sur la persistance d'une conversation.

Fichier neuf, volontairement séparé de ``test_conversations.py`` et de
``test_workspace.py`` : la branche ``Maxizoo`` réécrit ces deux fichiers
(cf. ``docs/AUDIT-2026-09.md`` §8.3), un test ajouté dedans serait à réécrire.

Aucune attente arbitraire ici : les points de synchronisation sont des barrières
et des sondes de verrou non bloquantes. Les rares ``join(timeout)`` sont des
BORNES SUPÉRIEURES, jamais des délais d'attente — une implémentation correcte
rend la main en quelques millisecondes, et seule une implémentation cassée
consomme la borne.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from data_analyst_agent.orchestrator import workspace as module_workspace
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.workspace import (
    ConversationWorkspace,
    atomic_write_to,
    conversation_lock,
    make_private_dir,
    write_text_atomic,
)

# Borne supérieure d'attente d'un thread qui ne doit PAS être bloqué. Généreuse
# à dessein : une machine lente reste largement dedans, seule une sérialisation
# indue ou un interblocage la dépasse.
BORNE = 30.0


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path / "workspaces")


def _en_parallele(taches: list, attendus: int = 0) -> list:
    """Lance chaque tâche dans son thread, toutes relâchées au même instant."""
    depart = threading.Barrier(len(taches), timeout=BORNE)

    def courir(tache):
        depart.wait()
        return tache()

    with ThreadPoolExecutor(max_workers=len(taches)) as pool:
        return [f.result(timeout=BORNE) for f in [pool.submit(courir, t) for t in taches]]


# -- le transcript ------------------------------------------------------------


def test_deux_tours_concurrents_gardent_les_quatre_messages(store: ConversationStore):
    """Le défaut mesuré à l'audit (§2.3) : `load` puis `append` puis `_save` sans
    verrou, deux tours simultanés laissaient 2 messages au lieu de 4."""
    conversation = store.create()

    _en_parallele(
        [
            lambda: store.record_turn(conversation.id, question="q1", answer="a1"),
            lambda: store.record_turn(conversation.id, question="q2", answer="a2"),
        ]
    )

    contenus = sorted(m.content for m in store.load(conversation.id).messages)
    assert contenus == ["a1", "a2", "q1", "q2"]


def test_huit_tours_concurrents_sur_le_meme_fil_ne_perdent_rien(store: ConversationStore):
    """Même invariant à 8 threads de 5 tours chacun : 80 messages, pas 79."""
    conversation = store.create()
    tours = [
        (lambda t=t, n=n: store.record_turn(conversation.id, f"q{t}-{n}", f"a{t}-{n}"))
        for t in range(8)
        for n in range(5)
    ]

    # 8 threads qui enchaînent chacun 5 tours : le contenu total est connu
    def serie(debut: int):
        for n in range(5):
            tours[debut * 5 + n]()

    _en_parallele([lambda d=d: serie(d) for d in range(8)])

    messages = store.load(conversation.id).messages
    assert len(messages) == 80
    attendus = {f"{prefixe}{t}-{n}" for t in range(8) for n in range(5) for prefixe in "qa"}
    assert {m.content for m in messages} == attendus


def test_un_tour_sur_un_autre_fil_nattend_pas(store: ConversationStore):
    """Le verrou est par conversation, pas global : deux utilisateurs sur deux
    fils différents ne doivent pas se sérialiser."""
    occupee = store.create("fil-occupe")
    libre = store.create("fil-libre")

    with conversation_lock(store.dir_of(occupee.id)) as tenu:
        assert tenu is True
        tour = threading.Thread(
            target=store.record_turn, args=(libre.id,), kwargs={"question": "q", "answer": "a"}
        )
        tour.start()
        tour.join(BORNE)

        # un verrou global ferait attendre ce thread jusqu'à la sortie du bloc
        assert not tour.is_alive()
        assert len(store.load(libre.id).messages) == 2


def test_le_verrou_est_exclusif_sur_la_meme_conversation(store: ConversationStore):
    """Sonde non bloquante : constate l'exclusion sans attendre."""
    dossier = store.dir_of("c1")
    autre = store.dir_of("c2")
    vu: dict[str, bool] = {}

    def sonder():
        with conversation_lock(dossier, blocking=False) as meme:
            vu["meme_conversation"] = meme
        with conversation_lock(autre, blocking=False) as differente:
            vu["autre_conversation"] = differente

    with conversation_lock(dossier):
        sonde = threading.Thread(target=sonder)
        sonde.start()
        sonde.join(BORNE)

    assert vu == {"meme_conversation": False, "autre_conversation": True}


def test_le_verrou_est_reentrant_dans_le_meme_thread(store: ConversationStore):
    """`record_turn` appelle `_save`, qui reprend le même verrou : sans réentrance,
    `flock` — attaché à l'open file description — bloquerait sur lui-même."""
    dossier = store.dir_of("c")

    with conversation_lock(dossier) as dehors, conversation_lock(dossier) as dedans:
        assert (dehors, dedans) == (True, True)

    # et il est bien relâché à la sortie
    with conversation_lock(dossier, blocking=False) as apres:
        assert apres is True


# Tient le verrou d'un dossier, l'annonce, et le relâche à la première ligne
# reçue : donne au test un autre PROCESS, sans aucune attente arbitraire.
PORTEUR_DE_VERROU = """
import sys
from pathlib import Path

from data_analyst_agent.orchestrator.workspace import conversation_lock

with conversation_lock(Path(sys.argv[1])) as pris:
    print("PRIS" if pris else "REFUSE", flush=True)
    sys.stdin.readline()
"""


def test_le_verrou_tient_aussi_entre_process(store: ConversationStore):
    """`uvicorn --workers N` sert les requêtes depuis N process : un verrou
    seulement `threading` n'y sérialiserait rien. D'où `flock`, qui est arbitré
    par le noyau et vaut entre process."""
    dossier = store.dir_of("c")
    make_private_dir(dossier)

    porteur = subprocess.Popen(
        [sys.executable, "-c", PORTEUR_DE_VERROU, str(dossier)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert porteur.stdout.readline().strip() == "PRIS"

        with conversation_lock(dossier, blocking=False) as pendant:
            assert pendant is False  # tenu par l'autre process

        porteur.stdin.write("relache\n")
        porteur.stdin.flush()
        assert porteur.wait(timeout=BORNE) == 0

        with conversation_lock(dossier, blocking=False) as apres:
            assert apres is True
    finally:
        if porteur.poll() is None:
            porteur.kill()
        porteur.stdin.close()
        porteur.stdout.close()


def test_deux_premiers_messages_simultanes_sous_le_meme_id(store: ConversationStore):
    """`create` sur un id déjà ouvert rend le fil au lieu de le remettre à zéro :
    le « charge, sinon crée » de l'API était un vérifie-puis-agis."""
    store.record_turn("id-du-client", question="q1", answer="a1")

    rendue = store.create("id-du-client")

    assert [m.content for m in rendue.messages] == ["q1", "a1"]
    assert len(store.load("id-du-client").messages) == 2


def test_une_seule_suppression_reussit(store: ConversationStore):
    conversation = store.create()
    store.record_turn(conversation.id, question="q", answer="a")

    resultats = _en_parallele([lambda: store.delete(conversation.id) for _ in range(4)])

    assert sorted(resultats) == [False, False, False, True]


def test_duplication_pendant_un_tour_donne_une_copie_coherente(store: ConversationStore):
    """La copie doit voir un état complet : transcript et manifeste du même
    instant, jamais un transcript d'avant le tour et un manifeste d'après."""
    original = store.create()
    store.record_turn(original.id, question="q1", answer="a1")

    copie, _ = _en_parallele(
        [
            lambda: store.duplicate(original.id),
            lambda: store.record_turn(original.id, question="q2", answer="a2"),
        ]
    )

    relue = store.load(copie.id)
    assert len(relue.messages) in (2, 4)  # avant ou après le tour, pas entre
    assert len(relue.messages) % 2 == 0
    assert len(store.load(original.id).messages) == 4


# -- les artefacts ------------------------------------------------------------


def test_deux_tours_partis_du_meme_etat_produisent_deux_artefacts(tmp_path: Path):
    """Le second défaut mesuré (§2.3) : le nom venait de
    `f"resultat_{len(self.artifacts) + 1}"` sur l'état lu au DÉBUT du tour. Deux
    tours partis du même état écrivaient tous les deux `resultat_1.csv`, le
    second écrasant le premier, et le manifeste n'en gardait qu'un.

    Reproduction sans thread : deux instances construites avant toute écriture
    ont, par construction, lu le même manifeste vide — c'est exactement l'état de
    deux tours en vol.
    """
    tour_a = ConversationWorkspace(tmp_path, "conv")
    tour_b = ConversationWorkspace(tmp_path, "conv")

    premier = tour_a.save_table(["a"], [[1]], "q1")
    second = tour_b.save_table(["b"], [[2]], "q2")

    assert premier.name != second.name
    assert premier.file != second.file
    # les deux CSV sont sur le disque, avec leur propre contenu
    assert tour_a.path_of(premier).read_text(encoding="utf-8").splitlines()[0] == "a"
    assert tour_b.path_of(second).read_text(encoding="utf-8").splitlines()[0] == "b"
    # et les deux sont au manifeste, relu par le tour suivant
    relu = ConversationWorkspace(tmp_path, "conv")
    assert sorted(a.name for a in relu.artifacts) == sorted([premier.name, second.name])
    assert sorted(a.question for a in relu.artifacts) == ["q1", "q2"]


def test_dix_artefacts_concurrents_sont_tous_au_manifeste_et_sur_le_disque(tmp_path: Path):
    tours = [ConversationWorkspace(tmp_path, "conv") for _ in range(10)]

    produits = _en_parallele(
        [(lambda ws=ws, n=n: ws.save_table(["x"], [[n]], f"q{n}")) for n, ws in enumerate(tours)]
    )

    noms = [a.name for a in produits]
    assert len(set(noms)) == 10  # aucun nom en double
    relu = ConversationWorkspace(tmp_path, "conv")
    assert sorted(a.name for a in relu.artifacts) == sorted(noms)
    for artefact in relu.artifacts:
        assert relu.path_of(artefact).exists()


def test_le_nommage_reste_compatible_avec_les_workspaces_sur_disque(tmp_path: Path):
    """Un dossier écrit par la version précédente doit rester lisible ET
    extensible : le tour suivant ne doit pas réécrire `resultat_1.csv`."""
    dossier = tmp_path / "conv"
    dossier.mkdir()
    (dossier / "resultat_1.csv").write_text("a\n1\n", encoding="utf-8")
    (dossier / "resultat_2.csv").write_text("b\n2\n", encoding="utf-8")
    (dossier / "manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "name": f"resultat_{n}",
                        "file": f"resultat_{n}.csv",
                        "columns": ["a"],
                        "row_count": 1,
                        "question": f"ancienne q{n}",
                    }
                    for n in (1, 2)
                ]
            }
        ),
        encoding="utf-8",
    )

    ws = ConversationWorkspace(tmp_path, "conv")
    assert [a.name for a in ws.artifacts] == ["resultat_1", "resultat_2"]

    ajoute = ws.save_table(["c"], [[3]], "nouvelle question")

    assert ajoute.name == "resultat_3"  # la convention de nom est conservée
    assert (dossier / "resultat_1.csv").read_text(encoding="utf-8") == "a\n1\n"  # intact
    assert [a.name for a in ConversationWorkspace(tmp_path, "conv").artifacts] == [
        "resultat_1",
        "resultat_2",
        "resultat_3",
    ]


def test_un_csv_orphelin_ne_fait_pas_reutiliser_son_nom(tmp_path: Path):
    """Un CSV présent sans entrée au manifeste (écriture interrompue avant le
    manifeste) ne doit pas être écrasé par le tour suivant."""
    dossier = tmp_path / "conv"
    dossier.mkdir()
    (dossier / "resultat_1.csv").write_text("orphelin\n", encoding="utf-8")

    artefact = ConversationWorkspace(tmp_path, "conv").save_table(["a"], [[1]], "q")

    assert artefact.name == "resultat_2"
    assert (dossier / "resultat_1.csv").read_text(encoding="utf-8") == "orphelin\n"


def test_contexte_concurrent_reste_un_json_complet(tmp_path: Path):
    """`context.json` est réécrit à chaque tour : un lecteur qui passe pendant
    l'écriture ne doit jamais tomber sur un JSON coupé."""
    tours = [ConversationWorkspace(tmp_path, "conv") for _ in range(6)]
    tours[0].record_turn("q0", "query", "titanic")
    stop = threading.Event()
    lectures: list[str] = []
    illisibles: list[Exception] = []

    def relire():
        while not stop.is_set():
            try:
                lectures.append(ConversationWorkspace(tmp_path, "conv").context.last_question)
            except (ValueError, OSError) as echec:  # JSON coupé au milieu
                illisibles.append(echec)

    lecteur = threading.Thread(target=relire, daemon=True)
    lecteur.start()
    try:
        _en_parallele(
            [
                (lambda ws=ws, n=n: ws.record_turn(f"q{n}", "query", "s"))
                for n, ws in enumerate(tours)
            ]
        )
    finally:
        stop.set()
        lecteur.join(BORNE)

    assert lectures  # le lecteur a bien travaillé pendant les écritures
    assert illisibles == []
    assert all(q.startswith("q") for q in lectures)


# -- écriture interrompue -----------------------------------------------------


def test_une_ecriture_interrompue_laisse_le_transcript_precedent_lisible(
    store: ConversationStore, monkeypatch
):
    """`write_text` tronquait le fichier PUIS écrivait : interrompu entre les
    deux, il laissait un JSON coupé, donc une conversation illisible (`load`
    renvoie None) et un fil perdu."""
    conversation = store.create()
    store.record_turn(conversation.id, question="q1", answer="a1")

    def replace_qui_echoue(*_args, **_kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr(module_workspace.os, "replace", replace_qui_echoue)
    with pytest.raises(OSError, match="disque plein"):
        store.record_turn(conversation.id, question="q2", answer="a2")
    monkeypatch.undo()

    relue = store.load(conversation.id)
    assert relue is not None  # ni tronqué, ni illisible
    assert [m.content for m in relue.messages] == ["q1", "a1"]
    # et pas de temporaire abandonné dans le dossier de la conversation
    assert list(store.dir_of(conversation.id).glob(".*.tmp")) == []


def test_un_lecteur_concurrent_ne_voit_jamais_de_transcript_illisible(store: ConversationStore):
    """Le cas réel : la page de chat recharge la liste des fils pendant qu'un
    tour s'écrit. Un `write_text` non atomique la fait tomber sur un fichier à
    moitié écrit — `load` renvoie None et le fil DISPARAÎT de la barre latérale.
    """
    conversation = store.create()
    store.record_turn(conversation.id, question="q0", answer="a0")
    stop = threading.Event()
    illisibles = []
    lus = []

    def relire():
        while not stop.is_set():
            relue = store.load(conversation.id)
            lus.append(relue)
            if relue is None:
                illisibles.append(relue)

    lecteur = threading.Thread(target=relire, daemon=True)
    lecteur.start()
    try:
        _en_parallele(
            [
                (
                    lambda n=n: [
                        store.record_turn(conversation.id, f"q{n}-{i}", "a") for i in range(8)
                    ]
                )
                for n in range(4)
            ]
        )
    finally:
        stop.set()
        lecteur.join(BORNE)

    assert len(lus) > 1  # le lecteur a bien tourné pendant les écritures
    assert illisibles == []
    assert len(store.load(conversation.id).messages) == 2 + 2 * 4 * 8


def test_atomic_write_to_nettoie_son_temporaire_sur_erreur(tmp_path: Path):
    cible = tmp_path / "fichier.json"
    cible.write_text("ancien", encoding="utf-8")

    def ecrire_puis_echouer() -> None:
        with atomic_write_to(cible) as tmp:
            tmp.write_text("nouveau", encoding="utf-8")
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        ecrire_puis_echouer()

    assert cible.read_text(encoding="utf-8") == "ancien"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_text_atomic_remplace_sans_etat_intermediaire(tmp_path: Path):
    cible = tmp_path / "fichier.json"

    write_text_atomic(cible, '{"a": 1}')
    write_text_atomic(cible, '{"a": 2}')

    assert json.loads(cible.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["fichier.json"]


def test_le_dossier_des_verrous_nest_pas_pris_pour_une_conversation(store: ConversationStore):
    """Les verrous vivent dans `.locks/` sous `base_dir` : `list()` ne doit pas
    les compter, et `delete` ne doit pas emporter le verrou du fil qu'il efface
    (c'est lui qui sérialise cette suppression)."""
    conversation = store.create()
    store.record_turn(conversation.id, question="q", answer="a")

    assert (store.base_dir / module_workspace.LOCKS_DIR).is_dir()
    assert [c.id for c in store.list()] == [conversation.id]

    assert store.delete(conversation.id) is True
    assert (store.base_dir / module_workspace.LOCKS_DIR).is_dir()
    assert store.list() == []
    assert oct(os.stat(store.base_dir / module_workspace.LOCKS_DIR).st_mode & 0o777) == "0o700"
