"""Persistance des conversations : transcription, reprise, duplication.

Chaque conversation est un dossier sous ``workspace_dir`` — le même que celui
où :mod:`data_analyst_agent.orchestrator.workspace` écrit déjà les tableaux
intermédiaires (CSV + manifeste) et le contexte du dernier tour. Ce module y
ajoute ``transcript.json`` : le fil des messages, le titre, les horodatages et
la prédiction en attente.

Conséquence de ce choix : **dupliquer une conversation est une copie de
dossier**. La copie repart donc avec la mémoire de l'originale (ses tableaux
mémorisés restent interrogeables, « prédis ces lignes » fonctionne toujours),
là où recopier les seuls messages donnerait un fil qui parle d'objets disparus.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from data_analyst_agent.orchestrator.graph import PendingInference
from data_analyst_agent.orchestrator.workspace import safe_dir_name
from data_analyst_agent.sandbox.client import MimeOutput

TITLE_MAX_CHARS = 60


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _title_from(message: str) -> str:
    """Titre lisible tiré du premier message (le fil n'a pas d'autre nom)."""
    titre = " ".join(message.split())
    if len(titre) > TITLE_MAX_CHARS:
        titre = titre[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return titre or "Nouvelle conversation"


class Message(BaseModel):
    """Un tour affiché : ce que l'utilisateur a écrit, ou ce que l'agent a répondu."""

    role: str  # "user" | "agent"
    content: str
    artifacts: list[MimeOutput] = Field(default_factory=list)
    error: str | None = None
    at: str = Field(default_factory=_now)


class Conversation(BaseModel):
    """Fil complet, tel que persisté dans ``transcript.json``."""

    id: str
    title: str = "Nouvelle conversation"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    messages: list[Message] = Field(default_factory=list)
    # multi-tours : prédiction en attente de features, persistée avec le fil pour
    # qu'une reprise après rechargement retrouve la question posée par l'agent.
    pending: PendingInference | None = None


class ConversationSummary(BaseModel):
    """Ce qu'il faut pour peupler la barre latérale, sans charger les messages."""

    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ConversationStore:
    """Magasin des conversations : un dossier par fil, sous ``base_dir``."""

    TRANSCRIPT = "transcript.json"

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    # -- chemins --------------------------------------------------------------

    def dir_of(self, conversation_id: str) -> Path:
        return self.base_dir / safe_dir_name(conversation_id)

    def _transcript_path(self, conversation_id: str) -> Path:
        return self.dir_of(conversation_id) / self.TRANSCRIPT

    # -- lecture --------------------------------------------------------------

    def load(self, conversation_id: str) -> Conversation | None:
        path = self._transcript_path(conversation_id)
        if not path.exists():
            return None
        try:
            return Conversation.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            # transcription corrompue (écriture interrompue, format ancien) : on
            # préfère un fil vide à une page de chat inutilisable.
            return None

    def list(self) -> list[ConversationSummary]:
        """Les fils du plus récemment utilisé au plus ancien."""
        if not self.base_dir.exists():
            return []
        resumes = []
        for dossier in self.base_dir.iterdir():
            if not (dossier / self.TRANSCRIPT).exists():
                continue
            conversation = self.load(dossier.name)
            if conversation is None:
                continue
            resumes.append(
                ConversationSummary(
                    id=conversation.id,
                    title=conversation.title,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                    message_count=len(conversation.messages),
                )
            )
        return sorted(resumes, key=lambda c: c.updated_at, reverse=True)

    # -- écriture -------------------------------------------------------------

    def _save(self, conversation: Conversation) -> Conversation:
        dossier = self.dir_of(conversation.id)
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / self.TRANSCRIPT).write_text(
            conversation.model_dump_json(indent=2), encoding="utf-8"
        )
        return conversation

    def create(self, conversation_id: str | None = None) -> Conversation:
        """Ouvre un fil ; un id fourni par le client est honoré tel quel.

        Un client peut mener une conversation sous un id qu'il a choisi (c'est le
        cas de ``scripts/live_scenarios.py``) : lui en attribuer un autre
        casserait le chaînage de ses tours suivants.
        """
        return self._save(Conversation(id=conversation_id or uuid.uuid4().hex))

    def record_turn(
        self,
        conversation_id: str,
        question: str,
        answer: str,
        artifacts: list[MimeOutput] | None = None,
        error: str | None = None,
        pending: PendingInference | None = None,
    ) -> Conversation:
        """Ajoute le tour (question + réponse) au fil et met à jour son état."""
        conversation = self.load(conversation_id) or Conversation(id=conversation_id)
        if not conversation.messages:
            conversation.title = _title_from(question)
        conversation.messages.append(Message(role="user", content=question))
        conversation.messages.append(
            Message(role="agent", content=answer, artifacts=artifacts or [], error=error)
        )
        conversation.pending = pending
        conversation.updated_at = _now()
        return self._save(conversation)

    def delete(self, conversation_id: str) -> bool:
        """Supprime le fil ET sa mémoire (tableaux intermédiaires compris)."""
        dossier = self.dir_of(conversation_id)
        if not (dossier / self.TRANSCRIPT).exists():
            return False
        shutil.rmtree(dossier, ignore_errors=True)
        return True

    def duplicate(self, conversation_id: str) -> Conversation | None:
        """Copie le fil et sa mémoire sous un nouvel id ; renvoie la copie."""
        source = self.load(conversation_id)
        if source is None:
            return None
        copie = source.model_copy(deep=True)
        copie.id = uuid.uuid4().hex
        copie.title = f"{source.title} (copie)"
        copie.created_at = copie.updated_at = _now()
        # copie du dossier entier : les CSV mémorisés et le contexte du dernier
        # tour suivent, donc la copie est reprenable comme l'originale.
        shutil.copytree(self.dir_of(conversation_id), self.dir_of(copie.id), dirs_exist_ok=True)
        return self._save(copie)
