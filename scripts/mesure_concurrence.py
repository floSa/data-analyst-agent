"""Banc de concurrence : N utilisateurs DISTINCTS qui travaillent en même temps.

Le produit est multi-utilisateurs depuis les chantiers C4 et C5 — comptes
argon2id, sessions côté serveur, cloisonnement par dossier — et vLLM a été
choisi POUR le parallélisme : il sert plusieurs requêtes de front. Rien de tout
ça n'avait été mesuré sous charge : les vingt-huit chantiers ont mesuré
séquentiellement.

Ce que ce banc n'est pas
------------------------
Ce n'est pas un tir de N requêtes sur un même compte. Chaque utilisateur a son
compte, sa session, son fil et **ses données** : ce qu'on éprouve, c'est le
cloisonnement autant que le débit. Un banc mono-compte ne dirait rien de la
propriété la plus chère du produit.

Le banc **n'alloue rien sur la carte** : il envoie des requêtes HTTP à
l'application, qui parle elle-même au moteur déjà en service. Il ne relance pas
le serveur vLLM et ne touche à aucun de ses réglages.

Le terrain
----------
Chaque utilisateur ``uK`` reçoit :

- un compte ``banc_uK`` dont le mot de passe est tiré au hasard, dans un magasin
  de comptes JETABLE (``--terrain``), jamais celui du service ;
- une source CSV ``mesures_uK`` déclarée dans un catalogue jetable, dont toutes
  les lignes portent un **jeton unique** (``JETON-uK-<aléa>``) ;
- un serveur applicatif dont les dossiers d'état sont sous ``--terrain``.

Le jeton est le canari. Il ne peut apparaître dans une réponse que si le fichier
de cet utilisateur-là a été lu : le voir chez un autre, c'est une fuite, et le
protocole n'a pas besoin d'interpréter quoi que ce soit pour le dire. Le nom de
la source, lui, est un signal FAIBLE — le catalogue est commun à tous, l'agent
peut le citer légitimement en dressant son inventaire.

Les épreuves (``--epreuve``, répétable)
--------------------------------------
``socle``         le socle HTTP seul : ce que coûte la session, sans moteur ni sandbox.
``postgres``      K requêtes simultanées sur la base : le cinquième goulot candidat.
``charge``        latences p50/p95, débit et erreurs pour N croissant, par capacité.
``cloisonnement`` audit du canari, des fils et des dossiers après la charge.
``meme-fil``      deux tours SIMULTANÉS sur un même fil : le fil les garde-t-il tous ?
``moteurs``       K requêtes simultanées AU MOTEUR : le parallélisme qu'il sert
                  vraiment, et de quoi le comparer à un second serveur (``--moteur-b``).

Usage
-----
    uv run python scripts/mesure_concurrence.py \\
        --llm-base-url http://localhost:8100/v1 \\
        --llm-model google/gemma-4-E4B-it-qat-w4a16-ct

    uv run python scripts/mesure_concurrence.py --epreuve moteurs \\
        --moteur-b http://autre-hote:8100/v1 --modele-b <modèle>

Le terrain est supprimé en sortie — comptes de test compris — sauf
``--garder-le-terrain``. ``--json`` écrit les mesures brutes, requête par
requête, pour qu'un tableau du rapport puisse être refait sans relancer le banc.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_analyst_agent.auth.accounts import AccountStore
from data_analyst_agent.config import get_settings

EPREUVES = ("socle", "postgres", "charge", "cloisonnement", "meme-fil", "moteurs")
CAPACITES = ("query", "analyze", "predict")

# Le jeton porté par chaque ligne du CSV d'un utilisateur. Assez distinctif pour
# qu'une recherche littérale ne rende jamais de faux positif, et assez court
# pour tenir dans une réponse sans se faire tronquer.
PREFIXE_JETON = "JETON"

C_OK, C_WARN, C_BAD, C_DIM, C_RST = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


# -- le terrain ----------------------------------------------------------------


@dataclass
class Utilisateur:
    """Un utilisateur du banc : son compte, sa source, son jeton, son fil."""

    index: int
    login: str
    mot_de_passe: str
    jeton: str
    source: str
    # Un fil NEUF par phase, et c'est essentiel : un fil unique réutilisé de
    # palier en palier grandirait à mesure que la campagne avance — tableaux
    # mémorisés, contexte réinjecté, prompt plus long. Les derniers paliers
    # seraient alors plus lents pour deux raisons mêlées, N et l'ancienneté du
    # fil, et la mesure n'attribuerait plus rien. Mesuré au premier jet : treize
    # tableaux intermédiaires dans le fil d'un utilisateur, et un contexte
    # tronqué, avant même d'arriver au palier 16.
    fils: list[str] = field(default_factory=list)

    def nouveau_fil(self) -> str:
        """Ouvre un identifiant de fil neuf et le retient pour l'audit."""
        fil = uuid.uuid4().hex
        self.fils.append(fil)
        return fil


def _csv_dun_utilisateur(jeton: str, lignes: int = 200) -> str:
    """Un CSV plausible, dont CHAQUE ligne porte le jeton de son propriétaire.

    Trois colonnes suffisent : le jeton (le canari), une catégorie (de quoi
    grouper, donc de quoi produire un vrai tableau) et une mesure numérique (de
    quoi tracer un histogramme dans le bac à sable). Les valeurs sont tirées
    d'une graine dérivée du jeton : deux utilisateurs n'ont pas les mêmes
    chiffres, et le banc reste rejouable.
    """
    graine = int(jeton[-8:], 36) if jeton[-8:].isalnum() else 1
    sortie = ["jeton,categorie,mesure"]
    valeur = graine % 997 + 1
    for i in range(lignes):
        valeur = (valeur * 1103515245 + 12345) % 2147483648
        categorie = "abc"[i % 3]
        sortie.append(f"{jeton},{categorie},{valeur % 10000 / 100:.2f}")
    return "\n".join(sortie) + "\n"


def preparer_le_terrain(terrain: Path, nb_utilisateurs: int) -> list[Utilisateur]:
    """Écrit comptes, sources et catalogue JETABLES, et rend les utilisateurs.

    Le magasin de comptes visé n'est jamais celui du service : le banc crée des
    comptes, et des comptes créés sont des comptes à supprimer. Les ranger dans
    un terrain jetable fait de la suppression un ``rmtree``, donc une chose qui
    ne peut pas être oubliée.

    Les mots de passe sont tirés de :mod:`secrets` et ne sont écrits nulle part :
    ils ne vivent que dans le process du banc, le temps de la mesure.
    """
    sources_dir = terrain / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    magasin = AccountStore(terrain / "users.yaml")

    utilisateurs: list[Utilisateur] = []
    declarations: list[str] = []
    for index in range(nb_utilisateurs):
        jeton = f"{PREFIXE_JETON}-u{index}-{secrets.token_hex(4)}"
        source = f"mesures_u{index}"
        login = f"banc_u{index}"
        mot_de_passe = secrets.token_urlsafe(16)
        magasin.create(login, mot_de_passe)
        (sources_dir / f"{source}.csv").write_text(_csv_dun_utilisateur(jeton), encoding="utf-8")
        utilisateurs.append(Utilisateur(index, login, mot_de_passe, jeton, source))
        declarations.append(
            f"  - type: file\n"
            f"    name: {source}\n"
            f"    description: >-\n"
            f"      Relevés de l'utilisateur u{index} — un jeton d'appartenance\n"
            f"      (colonne jeton), une categorie (a, b, c) et une mesure numérique.\n"
            f"    path: {source}.csv\n"
        )
    # Le catalogue ne porte PAS les jetons : s'ils étaient dans les descriptions,
    # le planificateur les aurait dans son prompt et le canari ne prouverait plus
    # rien. Un jeton ne s'obtient qu'en LISANT le fichier de son propriétaire.
    (sources_dir / "catalogue.yaml").write_text(
        "# Catalogue JETABLE du banc de concurrence — une source par utilisateur.\n"
        "sources:\n" + "".join(declarations),
        encoding="utf-8",
    )
    return utilisateurs


# -- le serveur ----------------------------------------------------------------


class Serveur:
    """Une instance d'uvicorn à nous, dont tout l'état vit sous le terrain.

    Viser le serveur du poste aurait deux défauts : ses comptes et ses
    conversations seraient mêlés à ceux du banc, et on mesurerait une
    configuration qu'on ne connaît pas. On lance donc le nôtre, avec les
    réglages PAR DÉFAUT du produit — c'est eux qu'on veut mesurer — et seuls les
    chemins d'état et l'URL du moteur sont surchargés.

    Le cookie de session est dépublié de son ``Secure`` pour ce serveur-ci :
    ``http.cookiejar`` refuse de renvoyer un cookie sûr sur du http, et le banc
    parle à localhost en clair. C'est une concession du BANC, pas du produit :
    le défaut du code reste ``true``.
    """

    def __init__(
        self,
        terrain: Path,
        port: int,
        llm_base_url: str,
        llm_model: str,
        surcharges: dict[str, str] | None = None,
    ) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.journal = terrain / f"uvicorn-{port}.log"
        self.env = (
            os.environ
            | {
                "DAA_WORKSPACE_DIR": str(terrain / "workspaces"),
                "DAA_AUTH_ACCOUNTS_PATH": str(terrain / "users.yaml"),
                "DAA_AUTH_STATE_DIR": str(terrain / "auth"),
                "DAA_CATALOG_PATH": str(terrain / "sources" / "catalogue.yaml"),
                "DAA_SESSION_COOKIE_SECURE": "false",
                "DAA_LLM_BASE_URL": llm_base_url,
                "DAA_LLM_MODEL": llm_model,
            }
            | (surcharges or {})
        )
        self.process: subprocess.Popen | None = None

    def demarrer(self, attente: float = 90.0) -> None:
        with self.journal.open("wb") as sortie:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "data_analyst_agent.api.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                    "--log-level",
                    "warning",
                ],
                env=self.env,
                stdout=sortie,
                stderr=subprocess.STDOUT,
            )
        echeance = time.monotonic() + attente
        while time.monotonic() < echeance:
            if self.process.poll() is not None:
                raise RuntimeError(f"uvicorn s'est arrêté — voir {self.journal}")
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as reponse:
                    if reponse.status == 200:
                        return
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.3)
        raise RuntimeError(f"uvicorn n'a pas répondu en {attente:g} s — voir {self.journal}")

    def arreter(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None


# -- le client -----------------------------------------------------------------


@dataclass
class Reponse:
    """Ce qu'une requête a rendu, et ce qu'elle a coûté."""

    utilisateur: str
    capacite: str
    debut: float
    latence: float
    statut: int
    corps: dict[str, Any] = field(default_factory=dict)
    nature: str = "ok"  # 'ok', ou la nature de l'erreur (cf. `nature_de_lerreur`)

    @property
    def reussie(self) -> bool:
        return self.nature == "ok"


class Client:
    """Un navigateur pour un utilisateur : son cookie de session, son jeton CSRF.

    Un client par utilisateur, et pas un client partagé : c'est la session qui
    porte l'identité, et un client partagé ferait de N utilisateurs un seul.
    """

    # Le banc renonce au bout de ce délai. Il doit être PLUS LONG que le pire
    # cas du serveur — `llm_timeout` (120 s) multiplié par les réessais du SDK,
    # et cela pour chacun des appels d'un tour — sans quoi on compterait comme
    # une panne du produit un abandon qu'on a décidé soi-même.
    DELAI_PAR_DEFAUT = 600.0

    def __init__(self, base_url: str, timeout: float = DELAI_PAR_DEFAUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def _cookie(self, nom: str) -> str:
        return next((c.value for c in self.cookies if c.name == nom), "")

    def connecter(self, login: str, mot_de_passe: str, cookie_csrf: str = "daa_csrf") -> None:
        """Suit le parcours du formulaire : la page pose le jeton, le POST le renvoie."""
        self.cookie_csrf = cookie_csrf
        self.opener.open(f"{self.base_url}/login", timeout=self.timeout).read()
        formulaire = urllib.parse.urlencode(
            {"login": login, "motdepasse": mot_de_passe, "csrf": self._cookie(cookie_csrf)}
        ).encode("utf-8")
        requete = urllib.request.Request(
            f"{self.base_url}/login",
            data=formulaire,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with self.opener.open(requete, timeout=self.timeout) as reponse:
            reponse.read()
        if not self._cookie("daa_session"):
            raise RuntimeError(f"connexion refusée pour {login}")

    def _requete(self, chemin: str, corps: dict | None = None, methode: str = "GET"):
        donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
        entetes = {"X-CSRF-Token": self._cookie(self.cookie_csrf)}
        if donnees is not None:
            entetes["Content-Type"] = "application/json"
        requete = urllib.request.Request(
            f"{self.base_url}{chemin}", data=donnees, headers=entetes, method=methode
        )
        return self.opener.open(requete, timeout=self.timeout)

    def get(self, chemin: str) -> tuple[int, Any]:
        try:
            with self._requete(chemin) as reponse:
                return reponse.status, json.loads(reponse.read().decode("utf-8"))
        except urllib.error.HTTPError as erreur:
            return erreur.code, _corps_derreur(erreur)

    def chat(self, message: str, conversation_id: str) -> tuple[int, dict]:
        corps = {"message": message, "conversation_id": conversation_id}
        try:
            with self._requete("/chat", corps, "POST") as reponse:
                return reponse.status, json.loads(reponse.read().decode("utf-8"))
        except urllib.error.HTTPError as erreur:
            return erreur.code, _corps_derreur(erreur)
        except TimeoutError as erreur:
            # Le BANC a renoncé, pas le serveur. À distinguer : la requête peut
            # très bien être encore en cours côté application, et compter ça
            # comme une erreur du produit serait lui prêter une panne qu'on a
            # provoquée en fixant un délai.
            return 0, {"detail": f"abandon du banc après {self.timeout:g} s : {erreur}"}
        except (urllib.error.URLError, OSError) as erreur:
            cause = getattr(erreur, "reason", erreur)
            if isinstance(cause, TimeoutError):
                return 0, {"detail": f"abandon du banc après {self.timeout:g} s : {cause}"}
            return 0, {"detail": f"connexion coupée : {type(erreur).__name__}: {erreur}"}


def _corps_derreur(erreur: urllib.error.HTTPError) -> dict:
    try:
        return json.loads(erreur.read().decode("utf-8"))
    except (ValueError, OSError):
        return {"detail": str(erreur)}


# -- la nature des erreurs -----------------------------------------------------

# Chaque nature est reconnue à un marqueur qu'on ne peut pas confondre avec un
# autre. L'ordre compte : le premier qui correspond nomme l'erreur.
NATURES = (
    ("plafond de sandbox", ("toutes les sandboxes sont occupées",)),
    # Le moteur répond, mais la CONNEXION échoue. Nature à part, et pas « erreur
    # métier » : sous charge, c'est le client HTTP partagé entre plusieurs
    # boucles d'événements qui reprend une connexion ouverte par une autre
    # (« is bound to a different event loop »), et le SDK rend « Connection
    # error ». Rien à voir avec un moteur tombé — celui-ci répond parfaitement
    # aux autres requêtes au même instant.
    ("connexion au moteur", ("Connection error", "APIConnectionError", "different event loop")),
    ("démarrage de sandbox", ("SandboxError", "sandbox")),
    ("limite de requêtes", ("trop de questions en peu de temps",)),
    ("refus d'authentification", ("authentification requise", "jeton anti-CSRF")),
    ("refus de contexte", ("refus du serveur", "contexte")),
    ("délai du moteur", ("Timeout", "timed out", "ReadTimeout")),
)


MARQUEUR_ABANDON = "abandon du banc"


def nature_de_lerreur(statut: int, corps: dict) -> str:
    """Nomme l'échec, ou rend ``ok``.

    On regarde la RÉPONSE entière — détail HTTP, erreur métier et détails de la
    trace — parce que le message technique est masqué à l'utilisateur mais reste
    dans la trace (cf. ``graph._guarded``) : c'est là, et seulement là, qu'on lit
    « toutes les sandboxes sont occupées ».
    """
    if statut == 0:
        detail = str(corps.get("detail") or "")
        return "abandon du banc" if MARQUEUR_ABANDON in detail else "connexion coupée"
    if statut >= 500:
        return "erreur serveur"
    texte = " ".join(
        [
            str(corps.get("detail") or ""),
            str(corps.get("error") or ""),
            *[str(etape.get("detail") or "") for etape in corps.get("trace") or []],
        ]
    )
    for nom, marqueurs in NATURES:
        if any(marqueur.lower() in texte.lower() for marqueur in marqueurs):
            return nom
    if statut == 429:
        return "limite de requêtes"
    if statut == 401:
        return "refus d'authentification"
    if statut != 200:
        return f"HTTP {statut}"
    if corps.get("error"):
        return "erreur métier"
    return "ok"


# -- la sonde du moteur --------------------------------------------------------


class SondeVllm:
    """Échantillonne ``/metrics`` pendant une phase : qui attend, et combien.

    C'est l'instrument qui DÉSIGNE le goulot au lieu de le supposer.
    ``num_requests_running`` dit combien de requêtes le moteur traite réellement
    en même temps : si l'application envoie huit questions et que le moteur n'en
    voit jamais plus de deux, le goulot n'est pas le moteur. S'il en voit huit et
    que ``num_requests_waiting`` reste à zéro, il n'est pas saturé non plus.

    Muette si l'URL ne sert pas de métriques : le banc doit tourner aussi
    contre un serveur qui n'en expose pas.
    """

    MOTIF = re.compile(
        r"^vllm:(num_requests_running|num_requests_waiting)\{[^}]*\}\s+([\d.]+)", re.M
    )

    def __init__(self, metrics_url: str, periode: float = 0.5) -> None:
        self.url = metrics_url
        self.periode = periode
        self.running: list[float] = []
        self.waiting: list[float] = []
        self._stop = threading.Event()
        self._fil: threading.Thread | None = None

    def disponible(self) -> bool:
        return self._lire() is not None

    def _lire(self) -> tuple[float, float] | None:
        try:
            with urllib.request.urlopen(self.url, timeout=2) as reponse:
                texte = reponse.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
        valeurs = {nom: float(v) for nom, v in self.MOTIF.findall(texte)}
        if "num_requests_running" not in valeurs:
            return None
        return valeurs["num_requests_running"], valeurs.get("num_requests_waiting", 0.0)

    def _boucle(self) -> None:
        while not self._stop.is_set():
            mesure = self._lire()
            if mesure is not None:
                self.running.append(mesure[0])
                self.waiting.append(mesure[1])
            self._stop.wait(self.periode)

    def __enter__(self) -> SondeVllm:
        self.running.clear()
        self.waiting.clear()
        self._stop.clear()
        self._fil = threading.Thread(target=self._boucle, daemon=True)
        self._fil.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._fil is not None:
            self._fil.join(timeout=5)

    def temoin(self) -> float:
        """Ce que le moteur traite AVANT qu'on lui envoie quoi que ce soit.

        Le moteur est mutualisé : un autre projet peut très bien être en train de
        s'en servir. Une mesure faite sur un moteur déjà occupé n'est pas fausse,
        elle est autre chose — et il faut pouvoir le dire. Relevé juste avant la
        phase, c'est le seul chiffre qui distingue les deux.
        """
        mesure = self._lire()
        return mesure[0] if mesure is not None else 0.0

    def resume(self) -> dict[str, float]:
        if not self.running:
            return {}
        return {
            "running_max": max(self.running),
            "running_moyen": statistics.fmean(self.running),
            "waiting_max": max(self.waiting),
            "echantillons": len(self.running),
        }


# -- les questions -------------------------------------------------------------


def questions(utilisateur: Utilisateur, capacite: str, tours: int) -> list[str]:
    """Les questions d'un utilisateur pour une capacité, dans l'ordre.

    ``query`` et ``analyze`` visent la source DE CET UTILISATEUR : c'est ce qui
    fait du jeton un canari. ``predict`` n'a pas de source — elle part des
    features données dans la question — et sert de témoin : c'est le chemin qui
    ne passe ni par une base ni par le bac à sable, donc le moteur seul.
    """
    source = utilisateur.source
    repertoires = {
        "query": [
            f"quelles sont les valeurs distinctes de la colonne jeton dans la source {source} ?",
            f"dans la source {source}, quelle est la moyenne de mesure par categorie ?",
        ],
        "analyze": [
            f"trace un histogramme de la colonne mesure de la source {source}",
            f"fais un diagramme en barres de la moyenne de mesure par categorie dans {source}",
        ],
        "predict": [
            "quelle espèce d'iris pour sepal_length=5.1, sepal_width=3.5, "
            "petal_length=1.4, petal_width=0.2 ?",
            "et pour sepal_length=6.7, sepal_width=3.1, petal_length=5.6, petal_width=2.4 ?",
        ],
    }
    repertoire = repertoires[capacite]
    return [repertoire[i % len(repertoire)] for i in range(tours)]


# -- la charge -----------------------------------------------------------------


def _percentile(valeurs: list[float], rang: float) -> float:
    """Percentile par rang le plus proche, sans interpolation.

    Sur des échantillons de huit à seize points, interpoler inventerait une
    précision qu'on n'a pas : on rend une valeur RÉELLEMENT observée.
    """
    if not valeurs:
        return float("nan")
    ordonnees = sorted(valeurs)
    index = max(0, min(len(ordonnees) - 1, int(rang / 100 * len(ordonnees) + 0.5) - 1))
    return ordonnees[index]


@dataclass
class Phase:
    """Une phase : N utilisateurs, une capacité, et ce qu'elle a mesuré."""

    n: int
    capacite: str
    duree: float
    reponses: list[Reponse]
    moteur: dict[str, float] = field(default_factory=dict)
    # Requêtes déjà en vol dans le moteur mutualisé au moment de partir : au-delà
    # de zéro, la phase a été mesurée en présence d'une charge étrangère.
    temoin: float = 0.0

    @property
    def latences(self) -> list[float]:
        return [r.latence for r in self.reponses if r.reussie]

    @property
    def p50(self) -> float:
        return _percentile(self.latences, 50)

    @property
    def p95(self) -> float:
        return _percentile(self.latences, 95)

    @property
    def debit(self) -> float:
        """Requêtes ABOUTIES par minute, sur la durée réelle de la phase."""
        return 60 * len(self.latences) / self.duree if self.duree > 0 else 0.0

    @property
    def noeuds(self) -> dict[str, float]:
        """Le p50 de chaque nœud du graphe, en secondes.

        C'est ce qui DÉSIGNE le goulot au lieu de le supposer : la réponse porte
        sa propre trace, un pas par nœud avec sa durée (cf. ``graph.TraceStep``).
        Si, à N croissant, seule la durée d'``analysis`` grandit, c'est le bac à
        sable ; si tous les nœuds qui appellent le modèle grandissent ensemble,
        c'est le moteur ; si aucun ne grandit alors que la latence de bout en
        bout grandit, le temps se passe AVANT le graphe — file d'attente HTTP ou
        pool de threads.

        Les nœuds d'une même réponse sont sommés avant d'être médianés : un tour
        peut repasser deux fois par le même nœud (boucle de correction), et ce
        qui compte est ce que ce nœud a coûté À CE TOUR.
        """
        par_noeud: dict[str, list[float]] = {}
        for reponse in self.reponses:
            if not reponse.reussie:
                continue
            tour: dict[str, float] = {}
            for etape in reponse.corps.get("trace") or []:
                nom = str(etape.get("node") or "?")
                tour[nom] = tour.get(nom, 0.0) + float(etape.get("duration_ms") or 0) / 1000
            for nom, duree in tour.items():
                par_noeud.setdefault(nom, []).append(duree)
        return {nom: _percentile(durees, 50) for nom, durees in sorted(par_noeud.items())}

    @property
    def erreurs(self) -> dict[str, int]:
        compte: dict[str, int] = {}
        for reponse in self.reponses:
            if not reponse.reussie:
                compte[reponse.nature] = compte.get(reponse.nature, 0) + 1
        return compte


def _travail_dun_utilisateur(
    client: Client,
    utilisateur: Utilisateur,
    fil: str,
    capacite: str,
    tours: int,
    depart: threading.Barrier,
) -> list[Reponse]:
    """Les tours d'UN utilisateur, tous partis au même instant que les autres.

    La barrière est ce qui fait la concurrence : sans elle, N fils lancés à la
    suite s'étalent sur le temps de démarrage et la charge n'est jamais N.
    """
    resultats: list[Reponse] = []
    depart.wait()
    for message in questions(utilisateur, capacite, tours):
        debut = time.monotonic()
        statut, corps = client.chat(message, fil)
        latence = time.monotonic() - debut
        resultats.append(
            Reponse(
                utilisateur=utilisateur.login,
                capacite=capacite,
                debut=debut,
                latence=latence,
                statut=statut,
                corps=corps,
                nature=nature_de_lerreur(statut, corps),
            )
        )
    return resultats


def chauffer_lapplication(client: Client, utilisateur: Utilisateur) -> float:
    """Un tour JETÉ, avant de mesurer quoi que ce soit.

    Le premier ``POST /chat`` d'un serveur paie ce qu'aucun des suivants ne
    paiera : l'orchestrateur est construit paresseusement, le catalogue est lu,
    le relevé des sources est fait, et le moteur charge ce qu'il a à charger.
    Mesuré sans chauffe, le palier N=1 portait tout ça : 88,0 s contre 55,1 s
    au palier N=4 — un premier palier plus lent que le second, ce qui n'a aucun
    sens et ferait conclure à un parallélisme qui n'existe pas.

    Le tour est mené par le premier utilisateur, dans un fil à lui qui compte
    comme les autres pour l'audit de cloisonnement — il n'y a pas de raison de
    l'en dispenser.
    """
    debut = time.monotonic()
    client.chat(questions(utilisateur, "query", 1)[0], utilisateur.nouveau_fil())
    return time.monotonic() - debut


def mener_une_phase(
    clients: dict[str, Client],
    utilisateurs: list[Utilisateur],
    n: int,
    capacite: str,
    tours: int,
    sonde: SondeVllm | None,
) -> Phase:
    """N utilisateurs, une capacité, tous en même temps."""
    participants = utilisateurs[:n]
    # Les fils sont ouverts ICI, dans le fil d'exécution principal : une liste
    # qu'on remplirait depuis N threads n'aurait pas d'ordre, et l'audit a
    # besoin de savoir à qui appartient quoi.
    fils = {u.login: u.nouveau_fil() for u in participants}
    depart = threading.Barrier(n)
    temoin = sonde.temoin() if sonde is not None else 0.0
    debut = time.monotonic()
    contexte = sonde if sonde is not None else _SansSonde()
    with contexte, concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futurs = [
            pool.submit(
                _travail_dun_utilisateur,
                clients[u.login],
                u,
                fils[u.login],
                capacite,
                tours,
                depart,
            )
            for u in participants
        ]
        reponses = [r for futur in futurs for r in futur.result()]
    duree = time.monotonic() - debut
    return Phase(n, capacite, duree, reponses, sonde.resume() if sonde else {}, temoin)


class _SansSonde:
    """Un contexte qui ne fait rien, pour le moteur qui ne publie pas de métriques."""

    def __enter__(self) -> _SansSonde:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


# -- le cloisonnement ----------------------------------------------------------


@dataclass
class Constat:
    """Une vérification de cloisonnement : ce qu'on a cherché, ce qu'on a trouvé."""

    intitule: str
    tenu: bool
    detail: str = ""


def _texte_complet(corps: dict) -> str:
    """Tout ce qu'une réponse rend : phrase, artefacts, trace, plan.

    Chercher le canari dans la seule ``answer`` laisserait passer une fuite par
    un tableau ou par une ligne de trace, qui sont pourtant affichés à
    l'utilisateur.
    """
    return json.dumps(corps, ensure_ascii=False)


def auditer_le_cloisonnement(
    clients: dict[str, Client],
    utilisateurs: list[Utilisateur],
    phases: list[Phase],
    terrain: Path,
) -> list[Constat]:
    """Le cloisonnement a-t-il tenu pendant la charge ?

    Cinq questions. La PREMIÈRE ne porte pas sur le cloisonnement mais sur le
    protocole : le canari est-il seulement actif ? Un jeton qui n'apparaît jamais
    nulle part ne peut pas apparaître au mauvais endroit, et l'absence de fuite
    ne prouverait alors rien du tout. Les quatre autres viennent ensuite, dans
    l'ordre de ce qu'elles coûteraient si la réponse était non : une donnée
    d'autrui dans ma réponse, un fil d'autrui dans ma liste, un fil d'autrui
    ouvrable par son identifiant, et une transcription rangée sous la mauvaise
    racine.
    """
    constats: list[Constat] = []
    par_login = {u.login: u for u in utilisateurs}
    jetons = {u.jeton: u.login for u in utilisateurs}

    # 0. le canari est-il actif ? Chacun doit AVOIR VU son propre jeton.
    aveugles = sorted(
        {
            u.login
            for u in utilisateurs
            if not any(
                r.utilisateur == u.login and u.jeton in _texte_complet(r.corps)
                for phase in phases
                for r in phase.reponses
            )
        }
    )
    porteurs = sorted({u.login for u in utilisateurs}) if phases else []
    constats.append(
        Constat(
            "le canari est actif : chacun a bien reçu SON propre jeton",
            bool(phases) and not aveugles,
            f"{len(aveugles)} utilisateur(s) n'ont jamais vu leur jeton : {aveugles[:3]}"
            if aveugles
            else f"{len(porteurs)} utilisateurs, chacun a lu sa source au moins une fois",
        )
    )

    # 1. le canari : le jeton d'un autre, où que ce soit dans MA réponse.
    fuites: list[str] = []
    for phase in phases:
        for reponse in phase.reponses:
            texte = _texte_complet(reponse.corps)
            mien = par_login[reponse.utilisateur].jeton
            for jeton, proprietaire in jetons.items():
                if jeton != mien and jeton in texte:
                    fuites.append(
                        f"{reponse.utilisateur} (N={phase.n}, {phase.capacite}) "
                        f"a reçu le jeton de {proprietaire}"
                    )
    constats.append(
        Constat(
            "aucune réponse ne porte le jeton d'un autre utilisateur",
            not fuites,
            f"{len(fuites)} fuite(s) : {fuites[:3]}" if fuites else _volume_examine(phases),
        )
    )

    # 2. ma liste de fils ne contient que les miens.
    intrus: list[str] = []
    for utilisateur in utilisateurs:
        statut, fils = clients[utilisateur.login].get("/conversations")
        if statut != 200:
            intrus.append(f"{utilisateur.login} : HTTP {statut}")
            continue
        miens = set(utilisateur.fils)
        for fil in fils:
            if fil["id"] not in miens:
                intrus.append(f"{utilisateur.login} voit le fil {fil['id']}")
    constats.append(
        Constat(
            "chacun ne liste que ses propres fils",
            not intrus,
            f"{len(intrus)} intrus : {intrus[:3]}"
            if intrus
            else f"{len(utilisateurs)} listes, "
            f"{sum(len(u.fils) for u in utilisateurs)} fils au total",
        )
    )

    # 3. le fil d'un AUTRE, ouvert par son identifiant : 404, jamais 403.
    #    Les identifiants sont distincts par utilisateur, donc réellement
    #    inconnus de celui qui les demande.
    croisements: list[str] = []
    tentatives = 0
    for utilisateur in utilisateurs:
        for autre in utilisateurs:
            if autre is utilisateur or not autre.fils:
                continue
            # Le DERNIER fil de l'autre : celui du palier le plus chargé, donc
            # celui qu'une confusion sous charge aurait le plus de chances
            # d'avoir mal rangé.
            for chemin in (
                f"/conversations/{autre.fils[-1]}",
                f"/conversations/{autre.fils[-1]}/artefacts",
            ):
                tentatives += 1
                statut, _ = clients[utilisateur.login].get(chemin)
                if statut != 404:
                    croisements.append(f"{utilisateur.login} → {chemin} : HTTP {statut}")
    constats.append(
        Constat(
            "le fil d'un autre répond 404 (jamais 403, jamais 200)",
            not croisements,
            f"{len(croisements)} accès : {croisements[:3]}"
            if croisements
            else f"{tentatives} tentatives croisées, toutes en 404",
        )
    )

    # 4. sur le DISQUE : chaque transcription est sous la racine de son
    #    propriétaire, et ne porte le jeton de personne d'autre.
    desordre: list[str] = []
    racine = terrain / "workspaces"
    for transcript in racine.rglob("transcript.json"):
        try:
            contenu = json.loads(transcript.read_text(encoding="utf-8"))
        except (ValueError, OSError) as echec:
            desordre.append(f"{transcript} illisible : {echec}")
            continue
        # Le dossier porte le nom ASSAINI du propriétaire (`safe_dir_name`) ; le
        # champ `owner`, lui, porte le login tel quel. C'est lui qui fait foi,
        # et c'est lui qu'un fichier déposé sous la mauvaise racine trahirait.
        proprietaire = contenu.get("owner", "")
        if proprietaire not in par_login:
            desordre.append(f"{transcript} : owner {proprietaire!r} inconnu")
            continue
        if proprietaire not in transcript.parts[len(racine.parts)]:
            desordre.append(f"{transcript} : rangée hors de la racine de {proprietaire}")
            continue
        brut = transcript.read_text(encoding="utf-8")
        for jeton, autre in jetons.items():
            if autre != proprietaire and jeton in brut:
                desordre.append(f"{transcript} (à {proprietaire}) porte le jeton de {autre}")
    constats.append(
        Constat(
            "sur le disque, aucune transcription ne porte le jeton d'un autre",
            not desordre,
            f"{len(desordre)} anomalie(s) : {desordre[:3]}"
            if desordre
            else f"{len(list(racine.rglob('transcript.json')))} transcriptions relues",
        )
    )
    return constats


def _volume_examine(phases: list[Phase]) -> str:
    total = sum(len(p.reponses) for p in phases)
    octets = sum(len(_texte_complet(r.corps)) for p in phases for r in p.reponses)
    return f"{total} réponses relues intégralement ({octets // 1024} Kio)"


# -- l'épreuve du socle --------------------------------------------------------


def epreuve_postgres(dsn: str, paliers: list[int], tirs: int) -> list[dict[str, Any]]:
    """K requêtes SIMULTANÉES sur la base, et rien d'autre.

    Postgres est l'un des cinq goulots candidats, et le seul que le reste du banc
    n'éprouve pas : les sources du terrain sont des fichiers, lus par DuckDB
    dans le process. On l'éprouve donc à part, et directement — l'adaptateur du
    produit, sa requête la plus courante (compter des lignes), K connexions en
    même temps.

    Ce qu'on cherche n'est pas une valeur absolue mais un ORDRE DE GRANDEUR :
    si la base sert des centaines de requêtes par seconde là où l'application
    en sert quelques-unes par seconde, elle est hors de cause, et le dire
    demande un chiffre plutôt qu'un raisonnement.
    """
    from data_analyst_agent.agents.retrieval.sql import PostgresAdapter

    resultats: list[dict[str, Any]] = []
    for k in paliers:
        # Un adaptateur PAR CLIENT, comme l'application en ouvre un par source
        # et par tour : mesurer un pool partagé mesurerait le pool, pas la base.
        adaptateurs = [PostgresAdapter.from_dsn(dsn) for _ in range(k)]
        depart = threading.Barrier(k)
        debut = time.monotonic()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=k) as pool:
                futurs = [
                    pool.submit(_rafale_sql, depart, adaptateur, tirs) for adaptateur in adaptateurs
                ]
                latences = [valeur for futur in futurs for valeur in futur.result()]
        finally:
            for adaptateur in adaptateurs:
                adaptateur.close()
        mur = time.monotonic() - debut
        resultats.append(
            {
                "k": k,
                "requetes": len(latences),
                "mur": mur,
                "debit": len(latences) / mur,
                "p50_ms": 1000 * _percentile(latences, 50),
                "p95_ms": 1000 * _percentile(latences, 95),
            }
        )
        print(
            f"  postgres K={k:<3} {len(latences):>5} requêtes en {mur:6.2f} s  "
            f"débit {len(latences) / mur:8.1f} req/s  "
            f"p50 {1000 * _percentile(latences, 50):6.1f} ms  "
            f"p95 {1000 * _percentile(latences, 95):6.1f} ms"
        )
    return resultats


def _rafale_sql(depart: threading.Barrier, adaptateur, tirs: int) -> list[float]:
    """Une rafale de requêtes sur la base, partie au signal commun."""
    latences: list[float] = []
    depart.wait()
    for _ in range(tirs):
        debut = time.monotonic()
        adaptateur.run("SELECT count(*) FROM passengers")
        latences.append(time.monotonic() - debut)
    return latences


def _rafale(depart: threading.Barrier, client: Client, chemin: str, tirs: int) -> list[float]:
    """Une rafale de ``tirs`` requêtes sur ``chemin``, partie au signal commun."""
    latences: list[float] = []
    depart.wait()
    for _ in range(tirs):
        debut = time.monotonic()
        client.get(chemin)
        latences.append(time.monotonic() - debut)
    return latences


def epreuve_socle(clients: dict[str, Client], k: int, tirs: int) -> list[dict[str, Any]]:
    """Le socle HTTP seul, sans moteur ni bac à sable : que coûte la SESSION ?

    Deux routes qui ne calculent rien, et tout ce qui les sépare est le passage
    par le magasin de sessions :

    - ``/health`` est la seule route ouverte : ni session, ni CSRF, ni disque ;
    - ``/me`` passe par ``SessionStore.resolve``, qui prend un verrou sur le
      fichier de sessions, le relit, met à jour ``last_seen_at`` et le RÉÉCRIT en
      entier — à chaque requête authentifiée, de tous les utilisateurs.

    Un verrou unique traversé par toutes les requêtes du service est un candidat
    sérieux au titre de goulot, et c'est le genre de chose qu'on ne voit jamais
    en mesurant séquentiellement. Le comparer à ``/health`` au même K, c'est
    isoler son coût de tout le reste.
    """
    logins = list(clients)[:k]
    resultats: list[dict[str, Any]] = []
    for route in ("/health", "/me"):
        depart = threading.Barrier(k)
        debut = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=k) as pool:
            futurs = [pool.submit(_rafale, depart, clients[login], route, tirs) for login in logins]
            latences = [valeur for futur in futurs for valeur in futur.result()]
        mur = time.monotonic() - debut
        resultats.append(
            {
                "route": route,
                "k": k,
                "requetes": len(latences),
                "mur": mur,
                "debit": len(latences) / mur,
                "p50_ms": 1000 * _percentile(latences, 50),
                "p95_ms": 1000 * _percentile(latences, 95),
            }
        )
        print(
            f"  {route:<9} K={k:<3} {len(latences):>5} requêtes en {mur:6.2f} s  "
            f"débit {len(latences) / mur:8.1f} req/s  "
            f"p50 {1000 * _percentile(latences, 50):6.1f} ms  "
            f"p95 {1000 * _percentile(latences, 95):6.1f} ms"
        )
    return resultats


# -- l'épreuve du même fil -----------------------------------------------------


def epreuve_meme_fil(client: Client, utilisateur: Utilisateur) -> Constat:
    """Deux tours SIMULTANÉS sur un même fil : le fil les garde-t-il tous les deux ?

    C'est le verrou de conversation qu'on éprouve ici, et rien d'autre : le
    magasin fait un lecture-modification-écriture de la transcription, et deux
    tours partis du même état en perdaient un. Un seul utilisateur suffit —
    c'est le MÊME fil qui est visé, pas deux comptes.
    """
    fil = uuid.uuid4().hex
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futurs = [
            pool.submit(
                client.chat,
                f"quelles sont les valeurs distinctes de la colonne jeton dans "
                f"la source {utilisateur.source} ? (tour {i})",
                fil,
            )
            for i in range(2)
        ]
        [futur.result() for futur in futurs]
    statut, contenu = client.get(f"/conversations/{fil}")
    messages = contenu.get("messages", []) if statut == 200 else []
    return Constat(
        "deux tours simultanés sur un même fil : les deux sont persistés",
        len(messages) == 4,
        f"{len(messages)} messages persistés (4 attendus : 2 questions + 2 réponses)",
    )


# -- l'épreuve des moteurs -----------------------------------------------------


def _appel_moteur(base_url: str, modele: str, prompt: str, tokens: int, cle: str) -> float:
    """Un appel de complétion, et ce qu'il a coûté en secondes."""
    corps = json.dumps(
        {
            "model": modele,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": tokens,
            "temperature": 0.0,
            "stream": False,
        }
    ).encode("utf-8")
    entetes = {"Content-Type": "application/json"}
    if cle:
        entetes["Authorization"] = f"Bearer {cle}"
    requete = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=corps, headers=entetes
    )
    debut = time.monotonic()
    with urllib.request.urlopen(requete, timeout=600) as reponse:
        reponse.read()
    return time.monotonic() - debut


def _tir_simultane(
    depart: threading.Barrier, base_url: str, modele: str, prompt: str, tokens: int, cle: str
) -> float:
    """Attend les autres, puis tire. La barrière est ce qui fait la simultanéité."""
    depart.wait()
    return _appel_moteur(base_url, modele, prompt, tokens, cle)


def _prompt_distinct(prompt: str, index: int) -> str:
    """Le même travail, mais un prompt qui ne partage AUCUN préfixe avec les autres.

    Tirer K fois le même prompt ne mesure pas le parallélisme : le serveur
    garde le cache d'un préfixe déjà vu, et les K-1 requêtes suivantes sautent
    l'évaluation du prompt. Mesuré : à K=8, 48,0 s avec des prompts identiques
    là où l'on attendait huit fois une requête — le cache expliquait l'écart,
    pas le parallélisme.

    Le discriminant est mis EN TÊTE, pas en queue : un préfixe commun suivi d'un
    suffixe différent reste un préfixe commun.
    """
    return f"Cas n° {index}, dossier {index * 7 + 3}. {prompt}"


def epreuve_moteurs(
    moteurs: list[tuple[str, str, str, str]], paliers: list[int], tokens: int
) -> list[dict[str, Any]]:
    """Ce que chaque moteur fait de K requêtes SIMULTANÉES, identiques.

    Au niveau du moteur, et non de l'application : c'est le parallélisme qu'on
    compare, et l'application y ajoute son propre bruit — bac à sable, base,
    nombre d'allers-retours variable d'une question à l'autre. K requêtes de même
    longueur, parties ensemble, et le temps qu'il faut pour que toutes soient
    rendues : si le moteur sert une requête à la fois, ce temps est K fois celui
    d'une seule. C'est exactement l'argument qui a motivé le choix du serveur.
    """
    prompt = (
        "Explique en un paragraphe ce qu'est une jointure entre deux tables "
        "relationnelles, et donne un exemple."
    )
    resultats: list[dict[str, Any]] = []
    for nom, base_url, modele, cle in moteurs:
        # Un tir de CHAUFFE, jeté. Le premier appel d'un moteur paie ce que les
        # suivants ne paient plus — chargement des poids en mémoire vive du GPU,
        # premier passage d'un cache. Le compter dans le palier K=1 ferait
        # attribuer au parallélisme ce qui vient du démarrage : mesuré, 17,1 s
        # au premier appel contre ~5 s ensuite, soit un rapport de trois sorti
        # de nulle part.
        chauffe = _appel_moteur(base_url, modele, _prompt_distinct(prompt, -1), tokens, cle)
        print(f"  {nom:<8} chauffe (jetée) : {chauffe:.1f} s")
        for k in paliers:
            depart = threading.Barrier(k)
            debut = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=k) as pool:
                futurs = [
                    pool.submit(
                        _tir_simultane,
                        depart,
                        base_url,
                        modele,
                        _prompt_distinct(prompt, index),
                        tokens,
                        cle,
                    )
                    for index in range(k)
                ]
                latences = [futur.result() for futur in futurs]
            mur = time.monotonic() - debut
            resultats.append(
                {
                    "moteur": nom,
                    "k": k,
                    "mur": mur,
                    "latence_p50": _percentile(latences, 50),
                    "latence_max": max(latences),
                    "debit": k / mur,
                }
            )
            print(
                f"  {nom:<8} K={k:<2} mur {mur:6.1f} s  p50 {_percentile(latences, 50):6.1f} s  "
                f"débit {k / mur:5.2f} req/s"
            )
    return resultats


# -- le rapport ----------------------------------------------------------------


def tableau_des_latences(phases: list[Phase]) -> str:
    lignes = [
        "| capacité | N | requêtes | abouties | p50 (s) | p95 (s) | débit (req/min) | "
        "moteur : max simultané / max en file |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for phase in phases:
        moteur = phase.moteur
        colonne_moteur = (
            f"{moteur['running_max']:.0f} / {moteur['waiting_max']:.0f}" if moteur else "—"
        )
        lignes.append(
            f"| {phase.capacite} | {phase.n} | {len(phase.reponses)} | {len(phase.latences)} | "
            f"{phase.p50:.1f} | {phase.p95:.1f} | {phase.debit:.1f} | {colonne_moteur} |"
        )
    return "\n".join(lignes)


def tableau_des_noeuds(phases: list[Phase]) -> str:
    """Où le temps passe, nœud par nœud, à N croissant — la preuve du goulot."""
    noms = sorted({nom for phase in phases for nom in phase.noeuds})
    if not noms:
        return "Aucune trace exploitable."
    lignes = [
        "| capacité | N | bout en bout p50 (s) | " + " | ".join(noms) + " |",
        "|---" * (3 + len(noms)) + "|",
    ]
    for phase in phases:
        cellules = [f"{phase.noeuds[nom]:.1f}" if nom in phase.noeuds else "—" for nom in noms]
        lignes.append(
            f"| {phase.capacite} | {phase.n} | {phase.p50:.1f} | " + " | ".join(cellules) + " |"
        )
    return "\n".join(lignes)


def tableau_des_erreurs(phases: list[Phase]) -> str:
    natures = sorted({n for phase in phases for n in phase.erreurs})
    if not natures:
        return "Aucune erreur, à aucun palier."
    lignes = ["| capacité | N | " + " | ".join(natures) + " |", "|---" * (2 + len(natures)) + "|"]
    for phase in phases:
        if not phase.erreurs:
            continue
        cellules = [str(phase.erreurs.get(nature, 0)) for nature in natures]
        lignes.append(f"| {phase.capacite} | {phase.n} | " + " | ".join(cellules) + " |")
    return "\n".join(lignes)


def _afficher_phase(phase: Phase) -> None:
    couleur = C_OK if not phase.erreurs else C_WARN
    moteur = phase.moteur
    detail_moteur = (
        f"  moteur max {moteur['running_max']:.0f} en vol / {moteur['waiting_max']:.0f} en file"
        if moteur
        else ""
    )
    print(
        f"{couleur}  N={phase.n:<2} {phase.capacite:<8}{C_RST} "
        f"p50 {phase.p50:6.1f} s  p95 {phase.p95:6.1f} s  "
        f"débit {phase.debit:5.1f} req/min  "
        f"{len(phase.latences)}/{len(phase.reponses)} abouties{detail_moteur}"
    )
    if phase.temoin:
        print(
            f"{C_WARN}      moteur déjà occupé au départ : {phase.temoin:.0f} requête(s) "
            f"d'un autre client{C_RST}"
        )
    for nature, compte in sorted(phase.erreurs.items()):
        print(f"{C_BAD}      {compte} fois : {nature}{C_RST}")


# -- le programme --------------------------------------------------------------


def _arguments() -> argparse.Namespace:
    reglages = get_settings()
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument(
        "--epreuve", action="append", choices=EPREUVES, help="répétable ; défaut : toutes"
    )
    analyseur.add_argument(
        "--paliers", default="1,2,4,8", help="valeurs de N, séparées par des virgules"
    )
    analyseur.add_argument(
        "--capacites", default=",".join(CAPACITES), help="capacités mesurées, dans l'ordre"
    )
    analyseur.add_argument(
        "--tours", type=int, default=2, help="questions par utilisateur et par phase"
    )
    analyseur.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help=(
            "secondes d'attente entre deux phases. Sert à SÉPARER deux choses que "
            "l'enchaînement mêle : la saturation du serveur, et le quota de débit "
            "par compte (20 questions par minute glissante), qu'un utilisateur "
            "présent dans tous les paliers finit par toucher"
        ),
    )
    analyseur.add_argument(
        "--tirs-socle", type=int, default=50, help="requêtes par client, épreuve du socle"
    )
    analyseur.add_argument(
        "--delai-client",
        type=float,
        default=Client.DELAI_PAR_DEFAUT,
        help=(
            "délai au bout duquel le BANC renonce à une requête. À garder "
            "identique entre deux campagnes qu'on veut comparer : il décide de ce "
            "qui est compté en « abandon du banc »"
        ),
    )
    analyseur.add_argument("--port", type=int, default=8931, help="port du serveur du banc")
    analyseur.add_argument("--terrain", type=Path, help="dossier jetable (défaut : sous /tmp)")
    analyseur.add_argument("--garder-le-terrain", action="store_true")
    analyseur.add_argument("--llm-base-url", default=reglages.llm_base_url)
    analyseur.add_argument("--llm-model", default=reglages.llm_model)
    analyseur.add_argument(
        "--metrics-url",
        default="",
        help="métriques Prometheus du moteur ; déduit de --llm-base-url si absent",
    )
    analyseur.add_argument(
        "--dsn-postgres",
        default="",
        help=(
            "DSN de l'épreuve postgres. Déduit du catalogue du projet si absent — "
            "le banc n'a pas de source postgres à lui"
        ),
    )
    analyseur.add_argument("--moteur-b", default="", help="second moteur, pour --epreuve moteurs")
    analyseur.add_argument("--modele-b", default="", help="modèle du second moteur")
    analyseur.add_argument(
        "--paliers-moteurs", default="1,2,4,8", help="valeurs de K de l'épreuve des moteurs"
    )
    analyseur.add_argument("--tokens-moteurs", type=int, default=200)
    analyseur.add_argument("--json", type=Path, help="où écrire les mesures brutes")
    return analyseur.parse_args()


def _dsn_du_catalogue() -> str:
    """Le DSN de la première source postgres DÉCLARÉE par le projet.

    Le terrain du banc n'a que des fichiers : pour éprouver la base, il faut
    celle du projet. On la lit dans son catalogue plutôt que de la réécrire ici —
    un DSN recopié dans un script finit toujours par diverger de celui qui sert.
    """
    from data_analyst_agent.agents.retrieval.catalog import PostgresSource, load_catalog

    catalogue = load_catalog(get_settings().catalog_path)
    for source in catalogue.sources:
        if isinstance(source, PostgresSource):
            return source.resolved_dsn()
    raise SystemExit("aucune source postgres déclarée : passez --dsn-postgres")


def _metrics_url(base_url: str, explicite: str) -> str:
    if explicite:
        return explicite
    # http://hôte:port/v1 → http://hôte:port/metrics
    return base_url.rstrip("/").removesuffix("/v1") + "/metrics"


def main() -> int:
    args = _arguments()
    epreuves = args.epreuve or list(EPREUVES)
    paliers = [int(p) for p in args.paliers.split(",") if p.strip()]
    capacites = [c.strip() for c in args.capacites.split(",") if c.strip()]
    terrain = args.terrain or Path(f"/tmp/banc-concurrence-{uuid.uuid4().hex[:8]}")
    terrain.mkdir(parents=True, exist_ok=True)

    mesures: dict[str, Any] = {
        "moteur": {"base_url": args.llm_base_url, "modele": args.llm_model},
        "paliers": paliers,
        "capacites": capacites,
        "tours": args.tours,
        "delai_client": args.delai_client,
        "phases": [],
        "constats": [],
        "socle": [],
        "postgres": [],
        "moteurs": [],
    }
    serveur: Serveur | None = None
    code = 0
    try:
        if {"socle", "charge", "cloisonnement", "meme-fil"} & set(epreuves):
            nb = max(paliers)
            print(f"{C_DIM}terrain : {terrain}{C_RST}")
            utilisateurs = preparer_le_terrain(terrain, nb)
            print(f"{nb} utilisateurs distincts, {nb} sources, {nb} comptes jetables")

            serveur = Serveur(terrain, args.port, args.llm_base_url, args.llm_model)
            serveur.demarrer()
            print(f"serveur du banc : {serveur.base_url} (moteur {args.llm_base_url})")

            clients: dict[str, Client] = {}
            for utilisateur in utilisateurs:
                client = Client(serveur.base_url, args.delai_client)
                client.connecter(utilisateur.login, utilisateur.mot_de_passe)
                clients[utilisateur.login] = client
            print(f"{len(clients)} sessions ouvertes\n")

            sonde = SondeVllm(_metrics_url(args.llm_base_url, args.metrics_url))
            avec_sonde = sonde.disponible()
            if not avec_sonde:
                print(f"{C_DIM}(pas de métriques moteur : sonde éteinte){C_RST}")

            phases: list[Phase] = []
            if "socle" in epreuves:
                print(f"{'=' * 78}\nÉPREUVE — socle HTTP : ce que coûte la session\n{'=' * 78}")
                for k in paliers:
                    mesures["socle"].extend(epreuve_socle(clients, k, args.tirs_socle))

            if "charge" in epreuves:
                print(
                    f"{'=' * 78}\nÉPREUVE — charge : N utilisateurs distincts en même temps\n"
                    f"{'=' * 78}"
                )
                for capacite in capacites:
                    for n in paliers:
                        phase = mener_une_phase(
                            clients,
                            utilisateurs,
                            n,
                            capacite,
                            args.tours,
                            sonde if avec_sonde else None,
                        )
                        phases.append(phase)
                        _afficher_phase(phase)
                        mesures["phases"].append(_phase_en_json(phase))
                        if args.pause:
                            time.sleep(args.pause)
                print(f"\n{tableau_des_latences(phases)}\n")
                print(f"{tableau_des_noeuds(phases)}\n")
                print(tableau_des_erreurs(phases))

            if "cloisonnement" in epreuves:
                print(f"\n{'=' * 78}\nÉPREUVE — cloisonnement sous charge\n{'=' * 78}")
                constats = auditer_le_cloisonnement(clients, utilisateurs, phases, terrain)
                for constat in constats:
                    marque = f"{C_OK}TENU{C_RST}" if constat.tenu else f"{C_BAD}ROMPU{C_RST}"
                    print(f"  [{marque}] {constat.intitule}")
                    print(f"         {C_DIM}{constat.detail}{C_RST}")
                    mesures["constats"].append(vars(constat))
                code |= 0 if all(c.tenu for c in constats) else 1

            if "meme-fil" in epreuves:
                print(f"\n{'=' * 78}\nÉPREUVE — deux tours simultanés sur un même fil\n{'=' * 78}")
                constat = epreuve_meme_fil(clients[utilisateurs[0].login], utilisateurs[0])
                marque = f"{C_OK}TENU{C_RST}" if constat.tenu else f"{C_BAD}ROMPU{C_RST}"
                print(f"  [{marque}] {constat.intitule}\n         {C_DIM}{constat.detail}{C_RST}")
                mesures["constats"].append(vars(constat))
                code |= 0 if constat.tenu else 1

        if "postgres" in epreuves:
            print(
                f"\n{'=' * 78}\nÉPREUVE — Postgres : K requêtes simultanées sur la base\n{'=' * 78}"
            )
            mesures["postgres"] = epreuve_postgres(
                args.dsn_postgres or _dsn_du_catalogue(), paliers, args.tirs_socle
            )

        if "moteurs" in epreuves:
            print(
                f"\n{'=' * 78}\nÉPREUVE — moteurs : K requêtes simultanées, identiques\n{'=' * 78}"
            )
            moteurs = [("vllm", args.llm_base_url, args.llm_model, get_settings().llm_api_key)]
            if args.moteur_b:
                moteurs.append(("moteur-b", args.moteur_b, args.modele_b or args.llm_model, ""))
            mesures["moteurs"] = epreuve_moteurs(
                moteurs,
                [int(k) for k in args.paliers_moteurs.split(",") if k.strip()],
                args.tokens_moteurs,
            )
    finally:
        if serveur is not None:
            serveur.arreter()
        if args.json:
            args.json.write_text(
                json.dumps(mesures, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"\n{C_DIM}mesures brutes : {args.json}{C_RST}")
        if args.garder_le_terrain:
            print(f"{C_WARN}terrain conservé (comptes de test compris) : {terrain}{C_RST}")
        else:
            shutil.rmtree(terrain, ignore_errors=True)
            print(f"{C_DIM}terrain supprimé — comptes de test compris : {terrain}{C_RST}")
    return code


def _exemples_derreur(phase: Phase, combien: int = 2) -> list[str]:
    """Quelques détails techniques d'échec, pour pouvoir nommer la cause après coup.

    Les jetons sont retirés : un fichier de mesures n'a pas à transporter les
    canaris du banc, même dans une ligne de trace.
    """
    exemples: list[str] = []
    for reponse in phase.reponses:
        if reponse.reussie or len(exemples) >= combien:
            continue
        detail = " | ".join(
            str(etape.get("detail") or "") for etape in reponse.corps.get("trace") or []
        ) or str(reponse.corps.get("detail") or reponse.corps.get("error") or "")
        exemples.append(f"{reponse.nature} — {re.sub(r'JETON-\S+', 'JETON-…', detail)[:300]}")
    return exemples


def _phase_en_json(phase: Phase) -> dict[str, Any]:
    """Ce qu'on garde d'une phase : de quoi refaire les tableaux, pas les réponses.

    Le corps des réponses n'y est pas : il porte les jetons des utilisateurs du
    banc, et un fichier de mesures n'a pas à les transporter.
    """
    return {
        "n": phase.n,
        "capacite": phase.capacite,
        "duree": phase.duree,
        "p50": phase.p50,
        "p95": phase.p95,
        "debit_par_minute": phase.debit,
        "abouties": len(phase.latences),
        "requetes": len(phase.reponses),
        "noeuds_p50": phase.noeuds,
        "erreurs": phase.erreurs,
        "exemples_derreur": _exemples_derreur(phase),
        "moteur": phase.moteur,
        "temoin_charge_etrangere": phase.temoin,
        "latences": sorted(phase.latences),
    }


if __name__ == "__main__":
    sys.exit(main())
