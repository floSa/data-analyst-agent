"""API FastAPI : POST /chat -> réponse en langage naturel + objets affichables.

L'orchestrateur est construit paresseusement au premier appel (le serveur
démarre sans Ollama) et reste injectable pour les tests. La page de chat
minimale est servie inline — aucun asset externe, compatible on-prem.

Lancement : uv run uvicorn data_analyst_agent.api.app:app
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import data_analyst_agent
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator

# borne simple du magasin de conversations (V1 on-prem, 10-20 utilisateurs)
MAX_CONVERSATIONS = 1000


class ChatRequest(BaseModel):
    message: str
    source: str | None = None  # force une source du catalogue (sinon le planificateur choisit)
    conversation_id: str | None = None  # multi-tours : renvoyer l'id reçu dans la réponse


def create_app(orchestrator_factory: Callable[[], Orchestrator] | None = None) -> FastAPI:
    app = FastAPI(
        title="data-analyst-agent",
        version=data_analyst_agent.__version__,
        description="Agent conversationnel sur données, on-premise.",
    )
    app.state.orchestrator = None
    app.state.orchestrator_factory = orchestrator_factory or Orchestrator
    # multi-tours : conversation_id -> prédiction en attente de features (ou None)
    app.state.conversations = {}

    def get_orchestrator() -> Orchestrator:
        if app.state.orchestrator is None:
            app.state.orchestrator = app.state.orchestrator_factory()
        return app.state.orchestrator

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": data_analyst_agent.__version__}

    @app.post("/chat", response_model=ChatAnswer)
    def chat(request: ChatRequest) -> ChatAnswer:
        conversation_id = request.conversation_id or uuid.uuid4().hex
        pending = app.state.conversations.get(conversation_id)
        answer = get_orchestrator().ask(
            request.message,
            source=request.source,
            pending=pending,
            conversation_id=conversation_id,
        )
        if len(app.state.conversations) >= MAX_CONVERSATIONS:
            app.state.conversations.clear()  # purge grossière, suffisante en V1
        app.state.conversations[conversation_id] = answer.pending
        answer.conversation_id = conversation_id
        return answer

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return CHAT_PAGE

    return app


CHAT_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>data-analyst-agent</title>
<style>
  :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
  body { max-width: 860px; margin: 0 auto; padding: 1rem; }
  h1 { font-size: 1.2rem; }
  #journal { display: flex; flex-direction: column; gap: .75rem; margin-bottom: 1rem; }
  .message { padding: .6rem .9rem; border-radius: .6rem; white-space: pre-wrap; }
  .utilisateur { background: #2563eb22; align-self: flex-end; }
  .agent { background: #6b728022; align-self: flex-start; }
  .agent img { max-width: 100%; border-radius: .4rem; margin-top: .5rem; }
  .agent table { border-collapse: collapse; margin-top: .5rem; font-size: .85rem; }
  .agent td, .agent th { border: 1px solid #6b7280; padding: .2rem .5rem; }
  form { display: flex; gap: .5rem; }
  input[type=text] { flex: 1; padding: .6rem; border-radius: .4rem; border: 1px solid #6b7280; }
  button { padding: .6rem 1.2rem; border-radius: .4rem; border: 0;
           background: #2563eb; color: white; }
  button:disabled { opacity: .5; }
  .erreur { color: #dc2626; }
  .reflexion { display: flex; align-items: center; gap: .5rem;
               font-style: italic; opacity: .8; }
  .roue { width: 1.1em; height: 1.1em; border-radius: 50%;
          border: 2px solid #6b7280; border-top-color: transparent;
          animation: rotation .8s linear infinite; flex: none; }
  @keyframes rotation { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<h1>data-analyst-agent</h1>
<div id="journal"></div>
<form id="formulaire">
  <input type="text" id="message" autocomplete="off"
         placeholder="Pose ta question sur les données...">
  <button type="submit" id="envoyer">Envoyer</button>
</form>
<script>
const journal = document.getElementById("journal");
const formulaire = document.getElementById("formulaire");
const champ = document.getElementById("message");
const bouton = document.getElementById("envoyer");
let conversationId = null;  // multi-tours : entretenu par le serveur

function bulle(classe) {
  const div = document.createElement("div");
  div.className = "message " + classe;
  journal.appendChild(div);
  return div;
}

function rendreTable(bloc, data) {
  const table = document.createElement("table");
  const entete = table.insertRow();
  data.columns.forEach(c => {
    const th = document.createElement("th");
    th.textContent = c;
    entete.appendChild(th);
  });
  data.rows.slice(0, 20).forEach(ligne => {
    const tr = table.insertRow();
    ligne.forEach(v => { tr.insertCell().textContent = v === null ? "" : v; });
  });
  bloc.appendChild(table);
}

formulaire.addEventListener("submit", async (event) => {
  event.preventDefault();
  const texte = champ.value.trim();
  if (!texte) return;
  bulle("utilisateur").textContent = texte;
  champ.value = "";
  bouton.disabled = true;
  const attente = bulle("agent");
  attente.classList.add("reflexion");
  attente.innerHTML = '<span class="roue"></span><span>L\\'agent réfléchit…</span>';
  try {
    const reponse = await fetch("/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: texte, conversation_id: conversationId}),
    });
    const corps = await reponse.json();
    conversationId = corps.conversation_id || conversationId;
    attente.classList.remove("reflexion");
    attente.textContent = corps.answer || "(pas de réponse)";
    if (corps.error) {
      const p = document.createElement("p");
      p.className = "erreur";
      p.textContent = corps.error;
      attente.appendChild(p);
    }
    for (const artefact of corps.artifacts || []) {
      if (artefact.mime === "image/png") {
        const img = document.createElement("img");
        img.src = "data:image/png;base64," + artefact.data;
        attente.appendChild(img);
      } else if (artefact.mime === "application/json") {
        try { rendreTable(attente, JSON.parse(artefact.data)); } catch (e) {}
      }
    }
  } catch (erreur) {
    attente.classList.remove("reflexion");
    attente.textContent = "Erreur réseau : " + erreur;
    attente.classList.add("erreur");
  } finally {
    bouton.disabled = false;
    champ.focus();
  }
});
</script>
</body>
</html>
"""


app = create_app()
