"""API FastAPI : POST /chat -> réponse en langage naturel + objets affichables.

L'orchestrateur est construit paresseusement au premier appel (le serveur
démarre sans Ollama) et reste injectable pour les tests. Les pages sont des
gabarits servis depuis ``api/templates/`` — aucun asset externe, compatible
on-prem.

Les conversations sont persistées sur disque (cf. ``orchestrator/conversations``)
et listées dans la barre latérale : on peut en ouvrir une ancienne et reprendre
où on en était, la dupliquer ou la supprimer.

**Chacun ne voit que ses fils.** Le magasin est ouvert POUR l'utilisateur de la
session, sa racine est celle de cet utilisateur, et aucune route ne dispose d'un
magasin qui verrait plus loin. Le fil d'un autre compte répond donc **404 et non
403** — un 403 confirmerait son existence, et cette fuite est gratuite à éviter.
Cela vaut aussi pour le ``conversation_id`` que ``POST /chat`` accepte du client :
il ne désigne jamais que le dossier de l'appelant.

**La surface est réduite au strict nécessaire** : la documentation interactive
(``/docs``, ``/redoc``, ``/openapi.json``) est éteinte par défaut, le corps des
requêtes et la longueur d'une question sont bornés, et ``POST /chat`` — qui
déclenche jusqu'à onze appels LLM et un conteneur — est plafonné en débit.

**Toutes les routes exigent une session**, sauf ``/health`` — une sonde de
disponibilité n'a pas de session, et lui refuser l'accès ferait passer le
service pour tombé. Une requête sans session reçoit 401 sur l'API et la page de
connexion en navigation.

Lancement : uv run uvicorn data_analyst_agent.api.app:app
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import data_analyst_agent
from data_analyst_agent.api import pages
from data_analyst_agent.auth.accounts import AccountStore, normalize_login
from data_analyst_agent.auth.current_user import CurrentUser, current_user
from data_analyst_agent.auth.rate_limit import RateLimiter
from data_analyst_agent.auth.sessions import TOKEN_BYTES, Session, SessionStore
from data_analyst_agent.auth.throttle import LoginThrottle
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.orchestrator.conversations import (
    Conversation,
    ConversationStore,
    ConversationSummary,
)
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator

# Les seules routes atteignables sans session. `/health` parce qu'une sonde n'en
# a pas ; `/login` parce qu'il faut bien une porte pour en obtenir une.
ROUTES_OUVERTES = frozenset({"/health", "/login"})

# Les méthodes qui modifient l'état sont celles que le CSRF vise : le cookie de
# session part tout seul sur une requête déclenchée par un autre site, ce que
# les en-têtes d'authentification ne faisaient pas. `SameSite=Lax` ne suffit
# pas — il ne couvre pas les sous-requêtes d'un site tiers et dépend du
# navigateur.
METHODES_MUTANTES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
EN_TETE_CSRF = "X-CSRF-Token"

# Message unique, quel que soit le refus : distinguer « ce compte n'existe pas »
# de « ce mot de passe est faux » donne un oracle d'énumération des comptes.
ECHEC_CONNEXION = "Identifiants invalides."
ECHEC_VERROUILLE = "Trop de tentatives. Réessayez dans quelques minutes."
ECHEC_FORMULAIRE = "Formulaire expiré. Recommencez."
CORPS_TROP_GROS = "corps de requête trop volumineux"
MESSAGE_TROP_LONG = "message trop long"
TROP_DE_REQUETES = "trop de questions en peu de temps : réessayez dans un instant"


# L'utilisateur de la session, tel que posé par le middleware. Toute route qui
# touche à des conversations le déclare : c'est ce qui lui donne SON magasin.
Utilisateur = Annotated[CurrentUser, Depends(current_user)]


class ChatRequest(BaseModel):
    message: str
    source: str | None = None  # force une source du catalogue (sinon le planificateur choisit)
    conversation_id: str | None = None  # multi-tours : renvoyer l'id reçu dans la réponse
    # Pas de champ `owner`, et il ne faut pas en ajouter : le propriétaire vient
    # de la session, jamais du corps de la requête. Un champ inconnu envoyé par
    # un client est ignoré par pydantic, et le magasin réécrit de toute façon
    # l'`owner` de ce qu'il persiste.


def create_app(
    orchestrator_factory: Callable[[], Orchestrator] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    reglages: Settings = settings or get_settings()
    # Éteindre la documentation interactive, c'est éteindre TROIS routes : sans
    # `openapi_url=None`, /openapi.json continue de publier le schéma complet
    # alors même que /docs a disparu. (`/docs/oauth2-redirect`, lui, ne vit que
    # tant que `docs_url` existe.)
    documentee = reglages.api_docs_enabled
    app = FastAPI(
        title="data-analyst-agent",
        version=data_analyst_agent.__version__,
        description="Agent conversationnel sur données, on-premise.",
        docs_url="/docs" if documentee else None,
        redoc_url="/redoc" if documentee else None,
        openapi_url="/openapi.json" if documentee else None,
    )
    app.state.orchestrator = None
    app.state.orchestrator_factory = orchestrator_factory or Orchestrator
    app.state.settings = reglages
    app.state.accounts = AccountStore(reglages.auth_accounts_path)
    app.state.sessions = SessionStore(
        reglages.auth_state_dir,
        reglages.session_idle_timeout,
        reglages.session_absolute_timeout,
    )
    app.state.throttle = LoginThrottle(
        reglages.auth_state_dir,
        reglages.login_max_failures,
        reglages.login_lockout_seconds,
    )
    # Quota de questions, par COMPTE : le LLM mutualisé sert une requête à la
    # fois, une rafale d'un seul utilisateur met tous les autres en file.
    app.state.chat_limiter = RateLimiter(
        reglages.auth_state_dir,
        reglages.chat_rate_limit_requests,
        reglages.chat_rate_limit_window,
    )

    def get_orchestrator() -> Orchestrator:
        if app.state.orchestrator is None:
            app.state.orchestrator = app.state.orchestrator_factory()
        return app.state.orchestrator

    def store(utilisateur: CurrentUser) -> ConversationStore:
        """Le magasin DE cet utilisateur — il n'en existe pas d'autre sorte.

        Fabriqué par requête, et non gardé dans ``app.state`` : un magasin
        partagé serait forcément le magasin de tout le monde, et c'est
        exactement ce que l'audit §2.3 a mesuré. L'objet ne porte qu'un chemin
        et un login, le fabriquer ne coûte rien. Les conversations, elles,
        vivent sur disque : elles survivent au rechargement de la page comme au
        redémarrage du serveur.
        """
        return ConversationStore(reglages.workspace_dir, utilisateur.login)

    # -- cookies --------------------------------------------------------------

    def poser_cookie_csrf(reponse: Response, jeton: str) -> None:
        """Le jeton anti-CSRF, LISIBLE par la page — c'est tout l'intérêt.

        Il n'est pas `HttpOnly` parce que le script doit le relire pour le
        renvoyer en en-tête : un site tiers peut faire partir le cookie de
        session avec une requête, il ne peut pas LIRE ce cookie-ci (même
        origine) donc pas fabriquer l'en-tête qui va avec.
        """
        reponse.set_cookie(
            reglages.csrf_cookie_name,
            jeton,
            httponly=False,
            samesite="lax",
            path="/",
            secure=reglages.session_cookie_secure,
            max_age=int(reglages.session_absolute_timeout),
        )

    def poser_cookies_de_session(reponse: Response, jeton: str, session: Session) -> None:
        reponse.set_cookie(
            reglages.session_cookie_name,
            jeton,
            httponly=True,  # hors de portée d'un script, donc d'une injection
            samesite="lax",
            path="/",
            secure=reglages.session_cookie_secure,
            max_age=int(reglages.session_absolute_timeout),
        )
        poser_cookie_csrf(reponse, session.csrf_token)

    # -- garde d'accès --------------------------------------------------------

    def csrf_valide(request: Request, session: Session) -> bool:
        fourni = request.headers.get(EN_TETE_CSRF, "")
        return bool(fourni) and secrets.compare_digest(fourni, session.csrf_token)

    def refuser(request: Request) -> Response:
        """401 pour l'API, page de connexion pour la navigation.

        Le critère est la requête elle-même : une navigation de navigateur
        demande du HTML en GET. Un `fetch` de la page, lui, reçoit 401 et se
        redirige de son côté — le rediriger ici lui ferait recevoir du HTML là
        où il attend du JSON.
        """
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=302)
        return JSONResponse({"detail": "authentification requise"}, status_code=401)

    @app.middleware("http")
    async def exiger_une_session(request: Request, call_next):
        if request.url.path in ROUTES_OUVERTES:
            return await call_next(request)
        session = app.state.sessions.resolve(request.cookies.get(reglages.session_cookie_name))
        if session is None:
            return refuser(request)
        if request.method in METHODES_MUTANTES and not csrf_valide(request, session):
            return JSONResponse({"detail": "jeton anti-CSRF absent ou invalide"}, status_code=403)
        # Ce que lira `Depends(current_user)` : la garde est ici, en un seul
        # endroit, et une route ajoutée demain est protégée sans rien déclarer.
        request.state.user = CurrentUser(login=session.login)
        return await call_next(request)

    @app.middleware("http")
    async def borner_le_corps(request: Request, call_next):
        """Refuse un corps annoncé trop gros, AVANT de le lire et avant la session.

        Enregistré après la garde de session, donc exécuté avant elle : Starlette
        empile le dernier middleware enregistré à l'extérieur. C'est voulu — on
        ne veut ni lire ni parser un corps démesuré, y compris sur `/login`, qui
        est ouvert.

        Le contrôle porte sur `Content-Length`. Une requête en `chunked` n'en
        annonce pas : elle passe ici, et se fait border plus loin par les limites
        de champ (`chat_message_max_chars`). Un serveur en frontal reste la bonne
        place pour un plafond dur.
        """
        annonce = request.headers.get("content-length", "")
        if annonce.isdigit() and int(annonce) > reglages.api_max_body_bytes:
            return JSONResponse({"detail": CORPS_TROP_GROS}, status_code=413)
        return await call_next(request)

    # -- connexion / déconnexion ----------------------------------------------

    def page_de_connexion(message: str = "", code: int = 200) -> Response:
        """Rend la page de connexion avec un jeton anti-CSRF frais.

        Le formulaire de connexion n'a pas encore de session à quoi lier son
        jeton : c'est un double envoi (cookie + champ caché), qu'un site tiers
        ne peut pas reproduire puisqu'il ne lit pas le cookie.
        """
        jeton = secrets.token_urlsafe(TOKEN_BYTES)
        reponse = HTMLResponse(
            pages.render(pages.LOGIN, csrf=jeton, erreur=message), status_code=code
        )
        poser_cookie_csrf(reponse, jeton)
        # Ni cache navigateur ni cache mandataire sur une page qui porte un jeton.
        reponse.headers["Cache-Control"] = "no-store"
        return reponse

    @app.get("/login", response_class=HTMLResponse)
    def afficher_connexion(request: Request) -> Response:
        deja = app.state.sessions.resolve(request.cookies.get(reglages.session_cookie_name))
        return RedirectResponse("/", status_code=302) if deja else page_de_connexion()

    @app.post("/login")
    def connexion(
        request: Request,
        login: Annotated[str, Form()],
        motdepasse: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
    ) -> Response:
        adresse = request.client.host if request.client else "inconnue"
        # Forme canonique dès la porte : sans ça, l'anti-force brute compterait
        # `alice`, `Alice` et ` alice ` sur trois compteurs distincts, et le
        # verrouillage se contournerait en changeant la casse.
        login = normalize_login(login)
        cookie = request.cookies.get(reglages.csrf_cookie_name, "")
        if not cookie or not secrets.compare_digest(csrf, cookie):
            return page_de_connexion(ECHEC_FORMULAIRE, 403)
        if app.state.throttle.locked(login, adresse):
            return page_de_connexion(ECHEC_VERROUILLE, 429)
        compte = app.state.accounts.verify(login, motdepasse)
        if compte is None:
            app.state.throttle.record_failure(login, adresse)
            return page_de_connexion(ECHEC_CONNEXION, 401)
        app.state.throttle.reset(login, adresse)
        # Régénération de l'identifiant de session : la session éventuellement en
        # cours est révoquée et une NOUVELLE est ouverte. Sans ça, un identifiant
        # posé d'avance par un attaquant (fixation de session) resterait le sien
        # une fois la victime authentifiée.
        app.state.sessions.revoke(request.cookies.get(reglages.session_cookie_name))
        jeton, session = app.state.sessions.create(compte.login)
        reponse = RedirectResponse("/", status_code=303)  # 303 : le navigateur repasse en GET
        poser_cookies_de_session(reponse, jeton, session)
        return reponse

    @app.post("/logout")
    def deconnexion(request: Request) -> Response:
        """Ferme la session côté SERVEUR, pas seulement dans le navigateur.

        Effacer le cookie ne suffirait pas : le jeton resterait valable pour qui
        l'aurait recopié.
        """
        app.state.sessions.revoke(request.cookies.get(reglages.session_cookie_name))
        reponse = Response(status_code=204)
        reponse.delete_cookie(reglages.session_cookie_name, path="/")
        reponse.delete_cookie(reglages.csrf_cookie_name, path="/")
        return reponse

    @app.get("/me", response_model=CurrentUser)
    def qui_suis_je(utilisateur: Utilisateur) -> CurrentUser:
        """L'utilisateur de la session en cours, pour un client d'API."""
        return utilisateur

    # -- routes applicatives --------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": data_analyst_agent.__version__}

    @app.post("/chat", response_model=ChatAnswer)
    def chat(request: ChatRequest, utilisateur: Utilisateur) -> ChatAnswer:
        """Un tour de conversation, dans le dossier de l'utilisateur de la session.

        L'``conversation_id`` du client est honoré — ``scripts/live_scenarios.py``
        mène ses tours sous un id qu'il a choisi — mais il est résolu SOUS la
        racine de l'appelant. Reprendre l'id du fil d'un autre compte n'y écrit
        donc rien : c'est un fil neuf, vide, chez soi. La propriété est vérifiée
        avant la moindre écriture parce qu'elle est vérifiée par le chemin
        lui-même.
        """
        if len(request.message) > reglages.chat_message_max_chars:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{MESSAGE_TROP_LONG} : {len(request.message)} caractères pour un "
                    f"maximum de {reglages.chat_message_max_chars}"
                ),
            )
        attente = app.state.chat_limiter.hit(utilisateur.login)
        if attente is not None:
            # `Retry-After` en secondes : le client sait quand revenir, au lieu
            # de réessayer en boucle et d'aggraver ce qu'on cherche à contenir.
            raise HTTPException(
                status_code=429,
                detail=TROP_DE_REQUETES,
                headers={"Retry-After": str(max(1, int(attente) + 1))},
            )
        magasin = store(utilisateur)
        existante = (
            magasin.load(request.conversation_id) if request.conversation_id is not None else None
        )
        conversation = existante or magasin.create(request.conversation_id)
        answer = get_orchestrator().ask(
            request.message,
            source=request.source,
            pending=conversation.pending,
            conversation_id=conversation.id,
            # la MÊME racine que celle où le magasin écrit la transcription :
            # les tableaux intermédiaires du fil doivent atterrir à côté d'elle.
            workspace_root=magasin.base_dir,
        )
        magasin.record_turn(
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
    def lister_conversations(utilisateur: Utilisateur) -> list[ConversationSummary]:
        return store(utilisateur).list()

    @app.get("/conversations/{conversation_id}", response_model=Conversation)
    def ouvrir_conversation(conversation_id: str, utilisateur: Utilisateur) -> Conversation:
        conversation = store(utilisateur).load(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation inconnue")
        return conversation

    @app.post("/conversations/{conversation_id}/duplicate", response_model=Conversation)
    def dupliquer_conversation(conversation_id: str, utilisateur: Utilisateur) -> Conversation:
        copie = store(utilisateur).duplicate(conversation_id)
        if copie is None:
            raise HTTPException(status_code=404, detail="conversation inconnue")
        return copie

    @app.delete("/conversations/{conversation_id}", status_code=204)
    def supprimer_conversation(conversation_id: str, utilisateur: Utilisateur) -> None:
        if not store(utilisateur).delete(conversation_id):
            raise HTTPException(status_code=404, detail="conversation inconnue")

    @app.get("/", response_class=HTMLResponse)
    def index(utilisateur: Utilisateur) -> str:
        return pages.render(
            pages.CHAT, login=utilisateur.login, cookie_csrf=reglages.csrf_cookie_name
        )

    return app


app = create_app()
