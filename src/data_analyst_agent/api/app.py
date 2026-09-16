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

import logging
import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import data_analyst_agent
from data_analyst_agent.api import pages
from data_analyst_agent.api.forwarded import (
    adresse_client,
    reseaux_de_confiance,
    schema_client,
)
from data_analyst_agent.auth.accounts import AccountStore, normalize_login
from data_analyst_agent.auth.current_user import CurrentUser, current_user
from data_analyst_agent.auth.rate_limit import RateLimiter
from data_analyst_agent.auth.sessions import TOKEN_BYTES, Session, SessionStore
from data_analyst_agent.auth.throttle import LoginThrottle
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.orchestrator.context_budget import ContextLimits
from data_analyst_agent.orchestrator.conversations import (
    Conversation,
    ConversationStore,
    ConversationSummary,
)
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator, SourceDuCatalogue
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

logger = logging.getLogger("data_analyst_agent.api")

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
SOURCE_INCONNUE = "source inconnue"
# Un artefact introuvable, ÉVINCÉ ou appartenant à quelqu'un d'autre : le
# même 404 et le même mot, parce qu'un message différent par cas dirait à un
# inconnu lequel des trois il vient de toucher. Le détail — existe mais
# évincé — se dit en conversation, dans un fil dont on est le propriétaire.
ARTEFACT_INCONNU = "artefact inconnu"
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


class ArtefactDuFil(BaseModel):
    """Un artefact du fil, tel que la page a besoin de le lister.

    Le CONTENU n'y est pas : lister n'est pas ouvrir, et une liste qui
    porterait le code de chaque figure pèserait ce qu'on cherche justement à ne
    pas transporter. Il s'obtient par la route de lecture, un artefact à la fois.
    """

    name: str
    kind: str
    description: str
    question: str
    retenu: bool  # dans le contexte du prochain tour, ou évincé par les plafonds


class ContenuDArtefact(BaseModel):
    """Le contenu d'UN artefact : le code Python, ou la tête du tableau."""

    name: str
    kind: str
    description: str
    question: str
    content: str


class SourceDeTravailRequest(BaseModel):
    """Le choix fait dans l'indicateur de la page, sans passer par une question.

    C'est un changement porté par le FIL, pas par une question : la route
    l'écrit dans la transcription, et ``POST /chat`` continue de le relire de
    là comme il l'a toujours fait. La source ne transite jamais par le corps
    d'une question — ``ChatRequest`` n'a pas de champ pour ça, et n'en aura pas.

    Une chaîne vide délie le fil : l'agent reproposera son inventaire à la
    prochaine question qui demande une source.
    """

    source: str = ""


class SourceDeTravailResponse(BaseModel):
    """Ce que la page réaffiche après le changement : l'état, et ce qu'on en dit."""

    conversation_id: str
    source_de_travail: str
    message: str


def _annoncer_lexposition(reglages: Settings, reseaux: tuple) -> None:
    """Dit, au démarrage, ce que l'application croit de son exposition.

    Deux réglages se répondent, et l'un des quatre croisements est une panne
    silencieuse. Le cookie `Secure` suppose du HTTPS jusqu'au navigateur ; dans
    cette architecture l'application ne fait jamais elle-même le TLS, donc
    `Secure` **implique** un mandataire devant. Déclaré `Secure` sans aucun
    mandataire de confiance, on tient les deux bouts d'une contradiction :
    quelqu'un termine TLS, et l'application ne le sait pas. Toutes les requêtes
    lui viennent alors d'une seule adresse — celle du mandataire — et
    l'anti-force brute, qui verrouille par adresse, verrouille tout le monde au
    cinquième mot de passe raté de n'importe qui.

    Le niveau n'est pas décoratif : `WARNING` et au-delà remontent au journal du
    conteneur, `INFO` non — aucun gestionnaire n'est posé sur les loggers de
    l'application, et seul le `lastResort` de la bibliothèque standard écrit.
    Un avertissement en `info` est un avertissement que personne ne lit.
    """
    logger.info(
        "mandataires de confiance : %s",
        ", ".join(str(reseau) for reseau in reseaux) or "aucun",
    )
    if not reglages.session_cookie_secure:
        logger.warning(
            "DAA_SESSION_COOKIE_SECURE=false : le cookie de session part en clair. "
            "Réglage de développement — en service, une terminaison TLS et `true`."
        )
    elif not reseaux:
        logger.warning(
            "cookie de session `Secure` mais AUCUN mandataire de confiance "
            "(DAA_TRUSTED_PROXIES vide) : quelque chose termine TLS devant "
            "l'application, et elle l'ignore. Toutes les requêtes lui viennent donc "
            "de la même adresse, et l'anti-force brute verrouillera tout le monde au "
            "cinquième mot de passe raté de n'importe qui. À renseigner avec le "
            "réseau du mandataire, et rien de plus large."
        )


def _dernier_echange(conversation: Conversation) -> tuple[str, str] | None:
    """La dernière question de l'utilisateur et la réponse qui l'a suivie.

    ``None`` quand le fil commence : il n'y a alors rien à rappeler. On ne rend
    que le DERNIER tour — c'est lui que « oui », « et dedans ? » ou « tu ne m'as
    pas répondu » désignent, et le prompt de l'agent système est déjà le plus
    chargé du socle.
    """
    question = ""
    reponse = ""
    for message in reversed(conversation.messages):
        if not reponse and message.role == "agent":
            reponse = message.content
        elif reponse and message.role == "user":
            question = message.content
            break
    return (question, reponse) if question or reponse else None


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
    # Compilés UNE fois : la résolution d'adresse a lieu à chaque connexion, et
    # relire des CIDR à chaque requête serait payer un réglage qui ne bouge pas.
    app.state.trusted_proxies = reseaux_de_confiance(reglages.trusted_proxies)
    # L'avertissement d'exposition n'est dit qu'une fois (cf. plus bas).
    app.state.exposition_signalee = False
    _annoncer_lexposition(reglages, app.state.trusted_proxies)

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

    def avertir_si_cookie_secure_en_clair(request: Request) -> None:
        """La seule panne de cette exposition qui ne laisse AUCUNE trace ailleurs.

        Cookie `Secure` servi en clair : le navigateur reçoit le jeton
        anti-CSRF, le jette sans rien dire, et renvoie un formulaire sans jeton.
        L'application répond « Formulaire expiré. Recommencez. » — à quelqu'un
        qui vient justement de le remplir pour la première fois. Aucune trace
        côté serveur, aucune erreur côté navigateur, et un diagnostic qui prend
        la journée. Cette ligne-là le rend immédiat.

        Posée sur la PAGE de connexion et non sur la connexion réussie : dans la
        configuration cassée, il n'y a jamais de connexion réussie — c'est
        précisément le symptôme.

        Le schéma est celui vu de l'APPELANT, pas celui de l'application :
        derrière le mandataire elle est toujours en clair, et son propre schéma
        ne dirait rien. D'où `X-Forwarded-Proto`, cru aux mêmes conditions que
        l'adresse.

        Une fois par process : c'est un défaut de configuration, pas un
        événement. Le répéter à chaque ouverture de la page noierait le journal
        des requêtes sous l'avertissement.
        """
        if app.state.exposition_signalee or not reglages.session_cookie_secure:
            return
        if schema_client(request, app.state.trusted_proxies) == "https":
            return
        app.state.exposition_signalee = True
        logger.warning(
            "page de connexion servie en clair à %s alors que le cookie de session est "
            "`Secure` : le navigateur le jettera, et le formulaire reviendra avec "
            "« %s ». Soit la terminaison TLS manque, soit elle ne pose pas "
            "X-Forwarded-Proto, soit son réseau n'est pas dans DAA_TRUSTED_PROXIES.",
            adresse_client(request, app.state.trusted_proxies),
            ECHEC_FORMULAIRE,
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
        avertir_si_cookie_secure_en_clair(request)
        deja = app.state.sessions.resolve(request.cookies.get(reglages.session_cookie_name))
        return RedirectResponse("/", status_code=302) if deja else page_de_connexion()

    @app.post("/login")
    def connexion(
        request: Request,
        login: Annotated[str, Form()],
        motdepasse: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
    ) -> Response:
        adresse = adresse_client(request, app.state.trusted_proxies)
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
            # la source validée par l'utilisateur pour ce fil, relue du disque à
            # chaque tour comme la prédiction en attente — le client ne l'envoie
            # pas, et `ChatRequest` ne porte pas de champ pour ça.
            source_de_travail=conversation.source_de_travail,
            # Le tour d'avant, pour que « oui » ou « et dedans ? » aient un sens
            # à trouver. Il est relu du disque comme la source et la prédiction
            # en attente : le client ne l'envoie pas.
            echange_precedent=_dernier_echange(conversation),
        )
        magasin.record_turn(
            conversation.id,
            question=request.message,
            answer=answer.answer,
            artifacts=answer.artifacts,
            error=answer.error,
            pending=answer.pending,
            source_de_travail=answer.source_de_travail,
        )
        answer.conversation_id = conversation.id
        return answer

    @app.get("/sources", response_model=list[SourceDuCatalogue])
    def lister_sources() -> list[SourceDuCatalogue]:
        """Le catalogue déclaré, augmenté de ce qu'on LIT dans chaque source.

        Ce que l'indicateur de la page affiche, et ce sur quoi on choisit dans
        son menu. Les faits — tables, lignes, période — sont ceux du relevé mis
        en cache, donc exactement ceux de l'inventaire proposé en conversation :
        deux inventaires qui divergeraient seraient pires qu'un seul.

        Aucune donnée de conversation ici, donc rien à cloisonner : le
        catalogue est le même pour tout le monde, et la session est exigée par
        le middleware comme sur toute autre route.
        """
        return get_orchestrator().inventaire_des_sources()

    @app.get("/conversations", response_model=list[ConversationSummary])
    def lister_conversations(utilisateur: Utilisateur) -> list[ConversationSummary]:
        return store(utilisateur).list()

    @app.post("/conversations", response_model=Conversation, status_code=201)
    def ouvrir_un_fil(utilisateur: Utilisateur) -> Conversation:
        """Ouvre un fil vide, sans message.

        Existe pour une raison précise : choisir une source dans le menu AVANT
        d'avoir écrit quoi que ce soit. Le choix doit atterrir dans un fil,
        puisque c'est le fil qui porte la source — il faut donc qu'un fil
        existe. Il sera titré par son premier message, comme les autres.
        """
        return store(utilisateur).create()

    @app.put("/conversations/{conversation_id}/source", response_model=SourceDeTravailResponse)
    def choisir_la_source(
        conversation_id: str, requete: SourceDeTravailRequest, utilisateur: Utilisateur
    ) -> SourceDeTravailResponse:
        """Change la source de travail du fil, sans avoir à la taper.

        Seule une source DÉCLARÉE est acceptable : un tableau intermédiaire de
        conversation est interrogeable, ce n'est pas une source de données, et
        le lier remplacerait la source de travail par un résultat de requête.

        Le changement est inscrit dans la transcription comme un message de
        l'agent : relire un fil dont les réponses changent de données sans que
        rien ne le dise serait exactement ce que la bascule annoncée évite.
        """
        orchestrateur = get_orchestrator()
        if requete.source and not orchestrateur.source_declaree(requete.source):
            raise HTTPException(status_code=404, detail=SOURCE_INCONNUE)
        magasin = store(utilisateur)
        fil = magasin.load(conversation_id)
        if fil is None:
            raise HTTPException(status_code=404, detail="conversation inconnue")
        annonce = (
            orchestrateur.accuser_la_source(requete.source, fil.source_de_travail)
            if requete.source
            else "Plus aucune source de travail : je te proposerai mon inventaire "
            "à la prochaine question qui en demande une."
        )
        magasin.lier_la_source(conversation_id, requete.source, annonce)
        return SourceDeTravailResponse(
            conversation_id=conversation_id,
            source_de_travail=requete.source,
            message=annonce,
        )

    def _espace_du_fil(utilisateur: CurrentUser, conversation_id: str) -> ConversationWorkspace:
        """Le magasin d'artefacts d'un fil DONT ON EST LE PROPRIÉTAIRE.

        Le cloisonnement est un chemin, pas un filtre : le workspace est ouvert
        sous ``magasin.base_dir``, c'est-à-dire sous la racine de l'appelant. Le
        fil d'un autre compte n'est donc pas « refusé », il n'existe pas de là
        où on regarde — et le 404 qu'on rend est le MÊME que celui d'un
        identifiant inventé. C'est délibéré : un 403 confirmerait l'existence du
        fil, et cette fuite-là est gratuite à éviter (cf. l'en-tête de
        ``orchestrator/conversations``).

        La transcription est chargée d'abord, et pas seulement par prudence :
        sans elle, un identifiant quelconque rendrait un workspace vide plutôt
        qu'un 404, et « ce fil n'existe pas » deviendrait indiscernable de « ce
        fil n'a rien produit ».
        """
        magasin = store(utilisateur)
        if magasin.load(conversation_id) is None:
            raise HTTPException(status_code=404, detail="conversation inconnue")
        return ConversationWorkspace(
            magasin.base_dir,
            conversation_id,
            limits=ContextLimits.from_settings(reglages),
        )

    @app.get("/conversations/{conversation_id}/artefacts", response_model=list[ArtefactDuFil])
    def lister_les_artefacts(conversation_id: str, utilisateur: Utilisateur) -> list[ArtefactDuFil]:
        """Le catalogue des artefacts d'un fil : tableaux, analyses, figures.

        Tout ce que porte le DISQUE, et non le seul contexte du tour : un
        artefact évincé appartient encore à la conversation, et le fil l'affiche
        intégralement. ``retenu`` dit lequel des deux il est, pour que la page
        puisse expliquer pourquoi l'agent ne sait plus le rejouer.
        """
        espace = _espace_du_fil(utilisateur, conversation_id)
        retenus = {a.name for a in espace.catalogue()}
        return [
            ArtefactDuFil(
                name=a.name,
                kind=a.kind,
                description=a.description,
                question=a.question,
                retenu=a.name in retenus,
            )
            for a in espace.artifacts
        ]

    @app.get("/conversations/{conversation_id}/artefacts/{name}", response_model=ContenuDArtefact)
    def lire_un_artefact(
        conversation_id: str, name: str, utilisateur: Utilisateur
    ) -> ContenuDArtefact:
        """Le contenu d'un artefact : son code Python, ou la tête de son tableau.

        C'est ce qui permet de RÉCUPÉRER le code d'une figure sans passer par la
        conversation — le relire, le copier, le porter ailleurs.

        Lu sur le disque, donc y compris s'il est évincé du contexte : l'éviction
        borne ce que l'agent réinjecte dans un prompt, elle n'efface rien.
        """
        espace = _espace_du_fil(utilisateur, conversation_id)
        artefact = espace.sur_le_disque(name)
        if artefact is None:
            raise HTTPException(status_code=404, detail=ARTEFACT_INCONNU)
        return ContenuDArtefact(
            name=artefact.name,
            kind=artefact.kind,
            description=artefact.description,
            question=artefact.question,
            content=espace.lire(artefact),
        )

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
