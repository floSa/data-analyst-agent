"""API FastAPI : POST /chat -> réponse en langage naturel + objets affichables.

L'orchestrateur est construit paresseusement au premier appel (le serveur
démarre sans Ollama) et reste injectable pour les tests. Les pages sont des
gabarits servis depuis ``api/templates/`` — aucun asset externe, compatible
on-prem.

Les conversations sont persistées sur disque (cf. ``orchestrator/conversations``)
et listées dans la barre latérale : on peut en ouvrir une ancienne et reprendre
où on en était, la dupliquer ou la supprimer.

Lancement : uv run uvicorn data_analyst_agent.api.app:app
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import data_analyst_agent
from data_analyst_agent.api import pages
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.orchestrator.conversations import (
    Conversation,
    ConversationStore,
    ConversationSummary,
)
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator


class ChatRequest(BaseModel):
    message: str
    source: str | None = None  # force une source du catalogue (sinon le planificateur choisit)
    conversation_id: str | None = None  # multi-tours : renvoyer l'id reçu dans la réponse


def create_app(
    orchestrator_factory: Callable[[], Orchestrator] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    app = FastAPI(
        title="data-analyst-agent",
        version=data_analyst_agent.__version__,
        description="Agent conversationnel sur données, on-premise.",
    )
    app.state.orchestrator = None
    app.state.orchestrator_factory = orchestrator_factory or Orchestrator
    app.state.settings = settings or get_settings()
    # les conversations vivent sur disque : elles survivent au rechargement de la
    # page comme au redémarrage du serveur.
    app.state.store = ConversationStore(app.state.settings.workspace_dir)

    def get_orchestrator() -> Orchestrator:
        if app.state.orchestrator is None:
            app.state.orchestrator = app.state.orchestrator_factory()
        return app.state.orchestrator

    def store() -> ConversationStore:
        return app.state.store

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": data_analyst_agent.__version__}

    @app.post("/chat", response_model=ChatAnswer)
    def chat(request: ChatRequest) -> ChatAnswer:
        existante = (
            store().load(request.conversation_id) if request.conversation_id is not None else None
        )
        conversation = existante or store().create(request.conversation_id)
        answer = get_orchestrator().ask(
            request.message,
            source=request.source,
            pending=conversation.pending,
            conversation_id=conversation.id,
        )
        store().record_turn(
            conversation.id,
            question=request.message,
            answer=answer.answer,
            artifacts=answer.artifacts,
            error=answer.error,
            pending=answer.pending,
        )
        answer.conversation_id = conversation.id
        return answer

    @app.get("/conversations", response_model=list[ConversationSummary])
    def lister_conversations() -> list[ConversationSummary]:
        return store().list()

    @app.get("/conversations/{conversation_id}", response_model=Conversation)
    def ouvrir_conversation(conversation_id: str) -> Conversation:
        conversation = store().load(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation inconnue")
        return conversation

    @app.post("/conversations/{conversation_id}/duplicate", response_model=Conversation)
    def dupliquer_conversation(conversation_id: str) -> Conversation:
        copie = store().duplicate(conversation_id)
        if copie is None:
            raise HTTPException(status_code=404, detail="conversation inconnue")
        return copie

    @app.delete("/conversations/{conversation_id}", status_code=204)
    def supprimer_conversation(conversation_id: str) -> None:
        if not store().delete(conversation_id):
            raise HTTPException(status_code=404, detail="conversation inconnue")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return pages.gabarit(pages.CHAT)

    return app


app = create_app()
