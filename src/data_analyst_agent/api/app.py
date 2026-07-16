"""API FastAPI : POST /chat -> réponse en langage naturel + objets affichables.

L'orchestrateur est construit paresseusement au premier appel (le serveur
démarre sans Ollama) et reste injectable pour les tests. La page de chat
minimale est servie inline — aucun asset externe, compatible on-prem.

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
  body { margin: 0; display: flex; height: 100vh; overflow: hidden; }
  /* barre latérale : nouvelle conversation + fils précédents */
  #barre { width: 16rem; flex: none; display: flex; flex-direction: column;
           gap: .5rem; padding: 1rem .75rem; border-right: 1px solid #6b728055;
           background: #6b728011; overflow-y: auto; }
  #nouvelle { width: 100%; }
  #fils { display: flex; flex-direction: column; gap: .15rem; }
  .fil { display: flex; align-items: center; gap: .25rem; border-radius: .4rem; }
  .fil:hover, .fil.actif { background: #2563eb22; }
  .fil-titre { flex: 1; min-width: 0; padding: .45rem .5rem; background: none;
               border: 0; color: inherit; font-size: .85rem; text-align: left;
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
               cursor: pointer; }
  .fil-action { flex: none; padding: .25rem .35rem; background: none; border: 0;
                color: inherit; opacity: 0; cursor: pointer; font-size: .8rem; }
  .fil:hover .fil-action, .fil:focus-within .fil-action { opacity: .7; }
  .fil-action:hover { opacity: 1; }
  .vide { font-size: .8rem; opacity: .6; padding: .5rem; }
  /* colonne de chat */
  #colonne { flex: 1; display: flex; flex-direction: column; min-width: 0;
             max-width: 860px; margin: 0 auto; padding: 1rem; }
  h1 { font-size: 1.2rem; }
  #journal { flex: 1; overflow-y: auto; display: flex; flex-direction: column;
             gap: .75rem; margin-bottom: 1rem; }
  .message { padding: .6rem .9rem; border-radius: .6rem; white-space: pre-wrap;
             max-width: 80%; }
  .utilisateur { background: #2563eb22; align-self: flex-end; }
  .agent { background: #6b728022; align-self: flex-start; }
  .agent img { max-width: 100%; border-radius: .4rem; margin-top: .5rem; }
  .agent table { border-collapse: collapse; margin-top: .5rem; font-size: .85rem; }
  .agent td, .agent th { border: 1px solid #6b7280; padding: .2rem .5rem; }
  form { display: flex; gap: .5rem; }
  input[type=text] { flex: 1; padding: .6rem; border-radius: .4rem; border: 1px solid #6b7280; }
  button { padding: .6rem 1.2rem; border-radius: .4rem; border: 0;
           background: #2563eb; color: white; cursor: pointer; }
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
<nav id="barre">
  <button id="nouvelle" type="button">+ Nouvelle conversation</button>
  <div id="fils"></div>
</nav>
<main id="colonne">
  <h1>data-analyst-agent</h1>
  <div id="journal"></div>
  <form id="formulaire">
    <input type="text" id="message" autocomplete="off"
           placeholder="Pose ta question sur les données...">
    <button type="submit" id="envoyer">Envoyer</button>
  </form>
</main>
<script>
const journal = document.getElementById("journal");
const formulaire = document.getElementById("formulaire");
const champ = document.getElementById("message");
const bouton = document.getElementById("envoyer");
const fils = document.getElementById("fils");
// null = conversation neuve : le serveur lui attribuera un id au 1er message.
let conversationId = null;

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

// même rendu pour un tour qui arrive et pour un tour relu d'une conversation
// rouverte : figures et tableaux réapparaissent à l'identique.
function rendreReponse(bloc, corps) {
  bloc.classList.remove("reflexion");
  bloc.textContent = corps.answer || "(pas de réponse)";
  if (corps.error) {
    const p = document.createElement("p");
    p.className = "erreur";
    p.textContent = corps.error;
    bloc.appendChild(p);
  }
  for (const artefact of corps.artifacts || []) {
    if (artefact.mime === "image/png") {
      const img = document.createElement("img");
      img.src = "data:image/png;base64," + artefact.data;
      bloc.appendChild(img);
    } else if (artefact.mime === "application/json") {
      try { rendreTable(bloc, JSON.parse(artefact.data)); } catch (e) {}
    }
  }
}

function defiler() { journal.scrollTop = journal.scrollHeight; }

async function rafraichirFils() {
  const reponse = await fetch("/conversations");
  const liste = await reponse.json();
  fils.textContent = "";
  if (!liste.length) {
    const vide = document.createElement("p");
    vide.className = "vide";
    vide.textContent = "Aucune conversation.";
    fils.appendChild(vide);
    return;
  }
  for (const resume of liste) {
    const ligne = document.createElement("div");
    ligne.className = "fil" + (resume.id === conversationId ? " actif" : "");

    const titre = document.createElement("button");
    titre.type = "button";
    titre.className = "fil-titre";
    titre.textContent = resume.title;
    titre.title = resume.title;
    titre.addEventListener("click", () => ouvrir(resume.id));

    const dupliquer = document.createElement("button");
    dupliquer.type = "button";
    dupliquer.className = "fil-action";
    dupliquer.textContent = "⧉";
    dupliquer.title = "Dupliquer";
    dupliquer.addEventListener("click", async () => {
      const reponse = await fetch("/conversations/" + resume.id + "/duplicate",
                                 {method: "POST"});
      if (reponse.ok) { ouvrir((await reponse.json()).id); }
    });

    const supprimer = document.createElement("button");
    supprimer.type = "button";
    supprimer.className = "fil-action";
    supprimer.textContent = "✕";
    supprimer.title = "Supprimer";
    supprimer.addEventListener("click", async () => {
      if (!confirm("Supprimer « " + resume.title + " » ?")) return;
      await fetch("/conversations/" + resume.id, {method: "DELETE"});
      if (resume.id === conversationId) nouvelleConversation();
      else rafraichirFils();
    });

    ligne.append(titre, dupliquer, supprimer);
    fils.appendChild(ligne);
  }
}

async function ouvrir(id) {
  const reponse = await fetch("/conversations/" + id);
  if (!reponse.ok) { rafraichirFils(); return; }
  const conversation = await reponse.json();
  conversationId = conversation.id;
  journal.textContent = "";
  for (const message of conversation.messages) {
    if (message.role === "user") bulle("utilisateur").textContent = message.content;
    else rendreReponse(bulle("agent"), message);
  }
  await rafraichirFils();
  defiler();
  champ.focus();
}

function nouvelleConversation() {
  conversationId = null;
  journal.textContent = "";
  rafraichirFils();
  champ.focus();
}

document.getElementById("nouvelle").addEventListener("click", nouvelleConversation);

formulaire.addEventListener("submit", async (event) => {
  event.preventDefault();
  const texte = champ.value.trim();
  if (!texte) return;
  bulle("utilisateur").textContent = texte;
  champ.value = "";
  bouton.disabled = true;
  const attente = bulle("agent");
  attente.classList.add("reflexion");
  attente.innerHTML = `<span class="roue"></span><span>L'agent réfléchit…</span>`;
  defiler();
  try {
    const reponse = await fetch("/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: texte, conversation_id: conversationId}),
    });
    const corps = await reponse.json();
    const nouveau = corps.conversation_id && corps.conversation_id !== conversationId;
    conversationId = corps.conversation_id || conversationId;
    rendreReponse(attente, corps);
    // 1er message : le fil vient d'être créé et titré côté serveur.
    if (nouveau) rafraichirFils();
  } catch (erreur) {
    attente.classList.remove("reflexion");
    attente.textContent = "Erreur réseau : " + erreur;
    attente.classList.add("erreur");
  } finally {
    bouton.disabled = false;
    champ.focus();
    defiler();
  }
});

rafraichirFils();
</script>
</body>
</html>
"""


app = create_app()
