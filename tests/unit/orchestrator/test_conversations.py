"""Tests du magasin de conversations (transcription, reprise, duplication)."""

import pytest

from data_analyst_agent.orchestrator.conversations import ConversationStore, _title_from
from data_analyst_agent.orchestrator.graph import PendingInference
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import MimeOutput


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path)


def test_conversation_neuve_puis_relue(store: ConversationStore):
    conversation = store.create()
    store.record_turn(conversation.id, question="Combien de femmes ?", answer="Il y en a 3.")

    relue = store.load(conversation.id)
    assert [(m.role, m.content) for m in relue.messages] == [
        ("user", "Combien de femmes ?"),
        ("agent", "Il y en a 3."),
    ]


def test_titre_vient_du_premier_message_seulement(store: ConversationStore):
    conversation = store.create()
    store.record_turn(conversation.id, question="Combien de femmes ?", answer="3.")
    store.record_turn(conversation.id, question="Et les hommes ?", answer="5.")

    assert store.load(conversation.id).title == "Combien de femmes ?"


def test_titre_long_est_tronque():
    titre = _title_from("Compare le taux de survie des femmes et des hommes par classe de cabine")
    assert len(titre) <= 60
    assert titre.endswith("…")


def test_artefacts_et_erreur_persistes(store: ConversationStore):
    """Une conversation rouverte doit réafficher ses figures, pas juste son texte."""
    conversation = store.create()
    store.record_turn(
        conversation.id,
        question="Trace un histogramme",
        answer="Voici.",
        artifacts=[MimeOutput(mime="image/png", data="cGl4ZWxz")],
        error="avertissement",
    )

    reponse = store.load(conversation.id).messages[-1]
    assert reponse.artifacts == [MimeOutput(mime="image/png", data="cGl4ZWxz")]
    assert reponse.error == "avertissement"


def test_pending_survit_a_la_reprise(store: ConversationStore):
    """Reprendre un fil, c'est aussi retrouver la question que l'agent avait posée."""
    conversation = store.create()
    store.record_turn(
        conversation.id,
        question="Prédis pour une femme",
        answer="Quel âge ?",
        pending=PendingInference(dataset="titanic", features={"sex": "female"}),
    )

    relue = store.load(conversation.id)
    assert relue.pending.dataset == "titanic"
    assert relue.pending.features == {"sex": "female"}


def test_liste_du_plus_recent_au_plus_ancien(store: ConversationStore):
    ancienne = store.create()
    store.record_turn(ancienne.id, question="Ancienne", answer="ok")
    recente = store.create()
    store.record_turn(recente.id, question="Récente", answer="ok")
    # updated_at est à la seconde : on force l'ordre pour tester le tri, pas l'horloge
    fil = store.load(ancienne.id)
    fil.updated_at = "2020-01-01T00:00:00+00:00"
    store._save(fil)

    assert [c.id for c in store.list()] == [recente.id, ancienne.id]


def test_liste_vide_sans_dossier(tmp_path):
    assert ConversationStore(tmp_path / "jamais-cree").list() == []


def test_liste_ignore_un_dossier_sans_transcription(store: ConversationStore, tmp_path):
    """Un workspace laissé par une ancienne version n'est pas une conversation."""
    (tmp_path / "orphelin").mkdir()
    (tmp_path / "orphelin" / "manifest.json").write_text("{}", encoding="utf-8")

    assert store.list() == []


def test_suppression_efface_le_fil_et_sa_memoire(store: ConversationStore, tmp_path):
    conversation = store.create()
    store.record_turn(conversation.id, question="Liste les femmes", answer="ok")
    workspace = ConversationWorkspace(tmp_path, conversation.id)
    workspace.save_table(["nom"], [["Alice"]], question="Liste les femmes")

    assert store.delete(conversation.id) is True
    assert store.load(conversation.id) is None
    assert not store.dir_of(conversation.id).exists()  # les CSV partent aussi


def test_suppression_dun_fil_inconnu(store: ConversationStore):
    assert store.delete("jamais-vu") is False


def test_duplication_copie_les_messages_sous_un_nouvel_id(store: ConversationStore):
    original = store.create()
    store.record_turn(original.id, question="Combien de femmes ?", answer="3.")

    copie = store.duplicate(original.id)

    assert copie.id != original.id
    assert copie.title == "Combien de femmes ? (copie)"
    assert [(m.role, m.content) for m in copie.messages] == [
        ("user", "Combien de femmes ?"),
        ("agent", "3."),
    ]
    assert store.load(original.id) is not None  # l'original reste intact


def test_duplication_emporte_la_memoire_reutilisable(store: ConversationStore, tmp_path):
    """La copie doit pouvoir enchaîner sur « prédis ces lignes » comme l'originale."""
    original = store.create()
    store.record_turn(original.id, question="Liste les femmes", answer="ok")
    ConversationWorkspace(tmp_path, original.id).save_table(
        ["nom"], [["Alice"]], question="Liste les femmes"
    )

    copie = store.duplicate(original.id)

    memoire_copie = ConversationWorkspace(tmp_path, copie.id)
    assert [a.name for a in memoire_copie.artifacts] == ["resultat_1"]
    assert memoire_copie.path_of(memoire_copie.artifacts[0]).exists()


def test_duplication_est_independante_de_loriginal(store: ConversationStore):
    original = store.create()
    store.record_turn(original.id, question="Combien de femmes ?", answer="3.")
    copie = store.duplicate(original.id)

    store.record_turn(copie.id, question="Et les hommes ?", answer="5.")

    assert len(store.load(copie.id).messages) == 4
    assert len(store.load(original.id).messages) == 2  # l'original n'a pas bougé


def test_duplication_dun_fil_inconnu(store: ConversationStore):
    assert store.duplicate("jamais-vu") is None


def test_transcription_corrompue_ne_casse_pas_la_liste(store: ConversationStore):
    conversation = store.create()
    store.record_turn(conversation.id, question="Bonjour", answer="ok")
    (store.dir_of(conversation.id) / ConversationStore.TRANSCRIPT).write_text(
        "{ pas du json", encoding="utf-8"
    )

    assert store.load(conversation.id) is None
    assert store.list() == []


def test_id_arbitraire_retrouve_malgre_le_nom_de_dossier(store: ConversationStore):
    """L'id réel est relu du fichier, pas déduit du nom de dossier assaini."""
    store.record_turn("ma conv #1", question="Bonjour", answer="ok")

    assert [c.id for c in store.list()] == ["ma conv #1"]
    assert store.load("ma conv #1") is not None
