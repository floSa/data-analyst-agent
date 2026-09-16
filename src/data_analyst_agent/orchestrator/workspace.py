"""Mémoire de conversation : le magasin des artefacts qu'elle a produits.

Chaque conversation possède un dossier ; ce qu'elle produit y est écrit et
décrit dans un manifeste JSON. **Un seul magasin, trois natures** (``kind``) :

- ``table`` — un résultat de requête ou un lot de prédiction, en CSV ;
- ``figure`` — le **code Python** d'une analyse qui a rendu une image ;
- ``code`` — le code Python d'une analyse qui n'en a pas rendu.

Chacune porte un NOM (``resultat_2``, ``graphique_1``…), une description d'une
ligne et la question qui l'a produite. C'est par ce nom qu'on la désigne au
tour suivant, et le nom est stable : c'est tout l'intérêt d'en donner un.

Les tableaux sont, en plus, réexposés :

- comme **sources éphémères** interrogeables en SQL (DuckDB) et réutilisables
  pour une prédiction (« prédis ces lignes ») ;
- **montés dans la sandbox** pour que le code d'analyse généré puisse les relire
  (``pd.read_csv('/data/resultat_1.csv')``) ;
- **décrits au planificateur** pour qu'il sache y faire référence.

Le nom d'un tableau (``resultat_1``, ``resultat_2``…) est aussi le nom de la
source éphémère et de la table DuckDB correspondante (via le nom de fichier).

Le code, lui, n'est ni monté ni interrogeable : il est **rappelable**. Ce qui
entre dans le prompt n'est que son CATALOGUE — une ligne par artefact, jamais
son contenu (``catalogue_du_code``). Le contenu s'ouvre à la demande, par les
outils que le modèle appelle (cf. :mod:`data_analyst_agent.orchestrator.rappel`).
C'est ce qui fait tenir la fenêtre de contexte quand une conversation dure.

Cette réexposition est **plafonnée** : ``artifacts`` est ce que porte le disque,
``retenus`` ce qui entre réellement dans le contexte du tour (cf.
:mod:`data_analyst_agent.orchestrator.context_budget`), dont ``injected`` est la
part des tableaux et ``codes_injectes`` celle du code. Les trois usages
ci-dessus lisent ``injected``, et le même ``injected`` : décrire au
planificateur un tableau qui n'est pas monté dans la sandbox — ou l'inverse —
produirait des erreurs incompréhensibles.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import string
import threading
import uuid
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.catalog import FileSource
from data_analyst_agent.orchestrator.context_budget import (
    ContextLimits,
    ContextTrim,
    estimate_tokens,
)

# Un dossier de conversation contient les questions de l'utilisateur et les
# données qu'il a fait remonter : seul le compte du service a à les lire.
DIR_MODE = 0o700

# Les verrous sont des fichiers vides rangés à part des conversations : un
# `delete` emporte le dossier du fil, il ne doit pas emporter le verrou qui
# sérialise ce delete avec les écritures concurrentes.
LOCKS_DIR = ".locks"


def make_private_dir(path: Path) -> None:
    """Crée ``path`` (et ses parents manquants) en 0o700.

    ``Path.mkdir(mode=…, parents=True)`` n'applique le mode qu'au dernier
    segment : les parents créés au passage héritent de l'umask — mesuré 0o775,
    donc lisibles par tout compte local. On les crée donc un par un. Les
    dossiers déjà présents ne sont pas touchés : un volume monté avec ses
    propres droits reste tel quel.
    """
    for dossier in reversed(path.parents):
        dossier.mkdir(mode=DIR_MODE, exist_ok=True)
    path.mkdir(mode=DIR_MODE, exist_ok=True)


# -- écritures atomiques ------------------------------------------------------


@contextlib.contextmanager
def atomic_write_to(path: Path) -> Iterator[Path]:
    """Cède un chemin temporaire, renommé sur ``path`` à la sortie du bloc.

    ``os.replace`` est atomique sur un même système de fichiers : un lecteur voit
    l'ancien contenu OU le nouveau, jamais un fichier à moitié écrit. Sur une
    exception, le temporaire est retiré et ``path`` garde son contenu précédent.

    Le nom du temporaire porte le pid et un uuid : deux écritures simultanées ne
    doivent pas se marcher dessus dans le temporaire non plus. Il commence par un
    point pour rester invisible d'un listage de la mémoire de conversation.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        yield tmp
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    """Écrit ``text`` dans ``path`` sans jamais exposer d'état intermédiaire.

    ``Path.write_text`` tronque le fichier PUIS écrit : un process interrompu
    entre les deux — ou un lecteur qui passe pendant — trouve un JSON coupé au
    milieu, donc une conversation illisible. Le ``fsync`` avant renommage évite
    en plus qu'un crash machine ne laisse un fichier renommé mais vide.

    ``mode`` est appliqué au TEMPORAIRE, avant le renommage : le fichier publié
    n'a jamais, même brièvement, les droits de l'umask. C'est ce que réclame le
    magasin de comptes (empreintes de mots de passe, 0600).
    """
    with atomic_write_to(path) as tmp, open(tmp, "w", encoding="utf-8") as fichier:
        fichier.write(text)
        fichier.flush()
        if mode is not None:
            os.fchmod(fichier.fileno(), mode)
        os.fsync(fichier.fileno())


# -- verrou par conversation --------------------------------------------------


class _ResourceLock:
    """Exclusion mutuelle sur une ressource, entre threads ET entre process.

    Deux étages, parce qu'aucun des deux ne suffit :

    - ``threading.RLock`` sérialise les threads de travail d'un même process
      (l'API en a 40 par défaut), mais ne voit rien des autres process ;
    - ``flock`` sérialise les process (``uvicorn --workers N``), mais est attaché
      à l'*open file description* : un second ``open`` du même fichier dans le
      même process bloquerait sur lui-même. D'où le comptage de réentrance —
      ``record_turn`` appelle ``_save``, qui prend le même verrou.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.RLock()
        self._depth = 0
        self._fd = -1

    def acquire(self, *, blocking: bool = True) -> bool:
        if not self._local.acquire(blocking=blocking):
            return False
        if self._depth > 0:  # déjà tenu par CE thread : réentrance
            self._depth += 1
            return True
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        except FileNotFoundError:  # premier verrou de ce workspace
            make_private_dir(self.path.parent)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as echec:
            os.close(fd)
            self._local.release()
            # `flock` non bloquant refusé : le verrou est tenu par un AUTRE
            # process, ce n'est pas une erreur. Tout le reste remonte — mais
            # seulement après avoir rendu le descripteur et le verrou de thread,
            # sinon le process se bloquerait sur lui-même à l'appel suivant.
            if blocking or not isinstance(echec, OSError):
                raise
            return False
        self._fd = fd
        self._depth = 1
        return True

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = -1
        self._local.release()


# Un verrou par ressource, pas un verrou global : deux utilisateurs sur deux
# fils différents ne doivent pas s'attendre. Références faibles pour que le
# dictionnaire ne grossisse pas d'une entrée par conversation jamais rouverte —
# un thread qui tient (ou attend) un verrou en garde une référence forte.
_locks: weakref.WeakValueDictionary[str, _ResourceLock] = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def _lock_of(resource: Path) -> _ResourceLock:
    dossier = Path(resource)
    path = dossier.parent / LOCKS_DIR / f"{dossier.name}.lock"
    cle = str(path.absolute())
    with _locks_guard:
        verrou = _locks.get(cle)
        if verrou is None:
            verrou = _ResourceLock(path)
            _locks[cle] = verrou
        return verrou


@contextlib.contextmanager
def resource_lock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Sérialise les écritures d'UNE ressource, entre threads ET entre process.

    La ressource est désignée par son chemin : un dossier (celui d'une
    conversation) ou un fichier (le magasin de comptes, celui des sessions). Le
    verrou lui-même est un fichier vide rangé dans ``.locks/`` À CÔTÉ de la
    ressource — jamais dedans : un ``rmtree`` du dossier verrouillé ne doit pas
    emporter le verrou qui le sérialise.

    Cède ``True`` si le verrou est tenu. ``blocking=False`` cède ``False`` sans
    attendre quand il est déjà pris ailleurs : c'est ce qui permet de *constater*
    l'exclusion dans un test, sans attente arbitraire.
    """
    verrou = _lock_of(path)
    if not verrou.acquire(blocking=blocking):
        yield False
        return
    try:
        yield True
    finally:
        verrou.release()


def conversation_lock(conversation_dir: Path, *, blocking: bool = True):
    """Verrou d'une conversation : ``resource_lock`` sur son DOSSIER.

    Le dossier — et non l'identifiant — est la clé : ``ConversationStore`` et
    ``ConversationWorkspace`` écrivent dans le même dossier par conversation et
    doivent donc prendre le même verrou.
    """
    return resource_lock(conversation_dir, blocking=blocking)


# -- les trois natures d'artefact ---------------------------------------------

# Un magasin unique porte les trois, et c'est le point : un tableau, le code
# d'une analyse et le code d'une figure sont trois choses qu'on veut DÉSIGNER
# PAR LEUR NOM au tour suivant, et rien ne gagnait à les ranger séparément.
#
# `figure` n'est pas l'image : c'est le CODE qui l'a produite. L'image part dans
# la réponse et y reste ; ce qu'on veut rappeler pour « mets les barres en
# bleu », c'est le code — le rejouer avec une modification rend une image
# neuve, là où repeindre un PNG ne rend rien.
KIND_TABLE = "table"
KIND_CODE = "code"
KIND_FIGURE = "figure"
KindArtefact = Literal["table", "code", "figure"]

# Le nom est la clé d'usage : c'est par lui que l'utilisateur et le modèle
# désignent l'artefact, et pour un tableau c'est aussi le nom de la source
# éphémère et de la table DuckDB. Un préfixe par nature, pour qu'un nom dise ce
# qu'il désigne sans qu'il faille ouvrir le manifeste.
PREFIXE = {KIND_TABLE: "resultat", KIND_CODE: "analyse", KIND_FIGURE: "graphique"}
EXTENSION = {KIND_TABLE: ".csv", KIND_CODE: ".py", KIND_FIGURE: ".py"}

# Ce qu'on rend d'un TABLEAU quand on le lit par son nom. Le fichier entier
# peut peser des milliers de lignes ; ce qu'on met dans le contexte d'un modèle
# pour qu'il sache de quoi on parle n'en demande pas tant.
LIGNES_LUES = 20


class WorkspaceArtifact(BaseModel):
    """Un objet produit dans la conversation, et qui porte un nom.

    Trois natures (``kind``), un seul magasin. ``columns`` et ``row_count`` ne
    décrivent qu'un tableau ; ``source`` ne sert qu'au code, pour remonter le
    même décor de données au moment de le rejouer.

    Les défauts ne sont pas de la commodité : un manifeste écrit AVANT ce
    mécanisme ne porte ni ``kind``, ni ``description``, ni ``source``, et il se
    relit tel quel en tableau — ce qu'il était. Rien à migrer.
    """

    name: str  # nom d'usage = nom de source éphémère = nom de table DuckDB
    file: str  # nom du fichier, relatif au dossier de la conversation
    kind: KindArtefact = KIND_TABLE
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    question: str  # la question qui l'a produit (aide le planificateur)
    description: str = ""  # une ligne, ce qui permet de le reconnaître
    source: str = ""  # la source interrogée, pour rejouer un code sur le même décor
    # Ce tableau est-il lui-même une TRANCHE ? Une requête est coupée à
    # `retrieval_max_rows`, et le CSV qu'on en garde ne porte aucune marque de
    # ce qui manque. Un tour ultérieur qui le remonte pour y compter refait le
    # même faux pas, un tour plus tard et sans que rien n'ait changé de place.
    # Un manifeste écrit avant ce champ ne le porte pas et vaut `False` : c'est
    # exact, on ne savait pas, et on ne prétend pas le contraire.
    tronque: bool = False

    @property
    def est_un_tableau(self) -> bool:
        return self.kind == KIND_TABLE

    @property
    def est_du_code(self) -> bool:
        return self.kind in (KIND_CODE, KIND_FIGURE)

    def ligne_de_catalogue(self) -> str:
        """L'artefact en UNE ligne : son nom, ce qu'il est, ce qui l'a produit.

        C'est tout ce qui entre dans le prompt. Jamais le contenu — ni les
        lignes d'un tableau, ni le code d'une figure : c'est ce qui fait tenir
        la fenêtre de contexte quand une conversation dure, et c'est le motif
        « le système de fichiers comme contexte » — on injecte l'index, on
        ouvre à la demande (cf. l'outil ``lire_un_artefact``).
        """
        return f"- {self.name} — {self.description} — produit par : « {self.question} »"


class ConversationContext(BaseModel):
    """Trace du dernier tour, pour comprendre un ajustement (« plus de couleurs »)."""

    last_question: str = ""
    last_capability: str | None = None
    last_source: str | None = None
    last_code: str | None = None  # code d'analyse produit (pour repartir dessus)
    # Features d'une prédiction RÉUSSIE, pour l'ajuster au tour suivant (« et si
    # elle était en 3e classe ? »). Le `pending` ne couvre que les prédictions
    # INCOMPLÈTES : dès qu'une prédiction aboutit il est vidé, et l'acquis
    # disparaissait avec lui — l'agent redemandait alors des features déjà
    # données, comme le sibsp d'une passagère décrite deux tours plus haut.
    last_dataset: str | None = None
    last_features: dict = Field(default_factory=dict)


# -- nom de dossier -----------------------------------------------------------

# Les seuls caractères repris tels quels. `~` en est volontairement exclu : il
# sert de marque d'échappement, il doit donc s'échapper lui-même.
CARACTERES_SURS = frozenset(string.ascii_letters + string.digits + "_-")
ECHAPPEMENT = "~"

# Image réservée du nom vide. Aucun nom non vide ne la produit : dans un nom
# encodé, un `~` est TOUJOURS suivi de deux chiffres hexadécimaux.
NOM_VIDE = "~vide"

# Un composant de chemin est borné par le système de fichiers (255 octets sur
# ext4 et xfs) et l'échappement peut quadrupler la longueur d'un nom unicode.
# Au-delà, on se replie sur un préfixe lisible suivi de l'empreinte du nom
# COMPLET — le marqueur `~~` n'est pas produisible autrement, l'empreinte
# porte l'identité, l'injectivité tient.
LONGUEUR_MAX = 120
MARQUEUR_REPLI = "~~"
EMPREINTE_CHARS = 32


def safe_dir_name(name: str) -> str:
    """Nom de dossier sûr **et injectif** pour un identifiant arbitraire.

    Chaque octet UTF-8 hors ``[0-9A-Za-z_-]`` est échappé en ``~XX``. Deux
    conséquences, et c'est tout l'intérêt :

    - **aucune traversée de chemin** : le résultat ne contient ni ``/`` (encodé
      ``~2f``) ni ``.`` (encodé ``~2e``), donc ni ``..`` ni un chemin absolu ;
    - **aucune collision** : l'encodage est réversible, donc deux identifiants
      distincts donnent deux dossiers distincts.

    Le nettoyage précédent (« tout caractère hors classe devient ``_`` ») tenait
    le premier point mais pas le second : ``a/b``, ``a.b`` et ``a b`` tombaient
    tous sur ``a_b``. Tant que la clé était un identifiant de conversation,
    c'était un défaut — deux fils pouvaient partager une mémoire. Depuis qu'un
    **login** entre dans le chemin, c'en serait un de cloisonnement : deux
    comptes dont les logins ne diffèrent que par la ponctuation liraient et
    écriraient les conversations l'un de l'autre.

    Les identifiants déjà sur disque (uuid hexadécimaux, noms de démonstration
    en ``[a-z0-9-]``) ne contiennent que des caractères sûrs : ils traversent
    l'encodage inchangés, et rien n'est à migrer de ce côté.

    Partagé avec :mod:`data_analyst_agent.orchestrator.conversations` : les deux
    modules écrivent dans le MÊME dossier par conversation, il doit être calculé
    de la même façon des deux côtés.
    """
    if not name:
        return NOM_VIDE
    encode = "".join(
        caractere
        if caractere in CARACTERES_SURS
        else "".join(f"{ECHAPPEMENT}{octet:02x}" for octet in caractere.encode("utf-8"))
        for caractere in name
    )
    if len(encode) <= LONGUEUR_MAX:
        return encode
    empreinte = hashlib.sha256(name.encode("utf-8")).hexdigest()[:EMPREINTE_CHARS]
    garde = LONGUEUR_MAX - len(MARQUEUR_REPLI) - EMPREINTE_CHARS
    return f"{encode[:garde]}{MARQUEUR_REPLI}{empreinte}"


def user_dir(base_dir: Path, login: str) -> Path:
    """Racine d'un utilisateur : ``workspace_dir/<login>/``.

    Toute la mémoire d'un compte vit là-dessous — transcriptions, CSV, manifeste,
    contexte — **et ses verrous** : ``resource_lock`` range son ``.locks/`` à
    côté de la ressource, donc sous la racine de l'utilisateur. Le cloisonnement
    est ainsi structurel et non un filtre qu'une route pourrait oublier.
    """
    return Path(base_dir) / safe_dir_name(login)


class ConversationWorkspace:
    """Dossier de travail d'une conversation : ses tableaux et son code."""

    MANIFEST = "manifest.json"
    CONTEXT = "context.json"

    def __init__(
        self, base_dir: Path, conversation_id: str, *, limits: ContextLimits | None = None
    ) -> None:
        self.dir = Path(base_dir) / safe_dir_name(conversation_id)
        self.limits = limits or ContextLimits()
        # Resserrement décidé par le budget de tokens du tour (None = pas encore
        # décompté). Retenu, et non recalculé : `save_table` réapplique la
        # fenêtre en cours de tour, elle ne doit pas rouvrir ce que le budget a
        # fermé — les montages de la sandbox déborderaient du budget du prompt.
        self._budget_cap: int | None = None
        self.artifacts: list[WorkspaceArtifact] = self._load()
        self.context: ConversationContext = self._load_context()
        # `artifacts` est le disque, `retenus` est le contexte : la fenêtre
        # sépare les deux, et `trim` dit ce qu'elle a écarté. `injected` et
        # `codes_injectes` sont deux vues de `retenus`, par nature — les
        # tableaux seuls sont montés dans la sandbox et interrogeables, le code
        # ne pèse qu'une ligne de catalogue.
        self.retenus: list[WorkspaceArtifact] = []
        self.injected: list[WorkspaceArtifact] = []
        self.codes_injectes: list[WorkspaceArtifact] = []
        self.trim: ContextTrim = ContextTrim()
        self._apply_limits()

    # -- plafond du contexte --------------------------------------------------

    @staticmethod
    def _fenetre(artefacts: list[WorkspaceArtifact], taille: int) -> list[WorkspaceArtifact]:
        """Les ``taille`` plus récents (0 = pas de fenêtre, on garde tout)."""
        return artefacts[-taille:] if 0 < taille < len(artefacts) else list(artefacts)

    def _apply_limits(self) -> None:
        """Recalcule ``retenus``, ses deux vues et ``trim`` à partir de ``artifacts``.

        Appelé à l'ouverture ET après chaque écriture : un objet produit au tour
        courant doit entrer dans la fenêtre (c'est le plus récent, donc le plus
        susceptible d'être désigné par « ces lignes » ou « le graphe de tout à
        l'heure »), et l'éviction qu'il provoque doit être visible tout de suite.

        **Une fenêtre par nature**, et ce n'est pas une complication gratuite :
        les deux ne coûtent pas la même chose. Un tableau retenu est monté en
        ``--volume`` dans la sandbox, ouvert comme source éphémère et décrit
        avec toutes ses colonnes — c'est ce que l'audit §3.4 a mesuré à 100
        montages et ~13 000 caractères. Un code retenu ne coûte qu'une ligne de
        catalogue. Les faire partager une fenêtre de huit ferait évincer la
        figure du tour 1 au bout de quatre tours qui produisent chacun un
        tableau, c'est-à-dire exactement le défaut qu'on corrige.

        Le **budget de tokens**, lui, est commun et ignore les natures : il
        coupe dans ce qui pèse, et les plus ANCIENS partent d'abord quelle que
        soit leur nature.
        """
        tables = [a for a in self.artifacts if a.est_un_tableau]
        codes = [a for a in self.artifacts if a.est_du_code]
        gardes = {
            id(a)
            for a in (
                *self._fenetre(tables, self.limits.artifact_window),
                *self._fenetre(codes, self.limits.code_window),
            )
        }
        cause = ""
        if len(gardes) < len(self.artifacts):
            cause = (
                f"fenêtre DAA_CONTEXT_ARTIFACT_WINDOW={self.limits.artifact_window}"
                f" / DAA_CONTEXT_CODE_WINDOW={self.limits.code_window}"
            )
        retenus = [a for a in self.artifacts if id(a) in gardes]
        if self._budget_cap is not None and self._budget_cap < len(retenus):
            retenus = retenus[-self._budget_cap :] if self._budget_cap else []
            cause = f"budget DAA_CONTEXT_TOKEN_BUDGET={self.limits.token_budget} tokens"
        self.retenus = retenus
        self.injected = [a for a in retenus if a.est_un_tableau]
        self.codes_injectes = [a for a in retenus if a.est_du_code]
        self.trim = ContextTrim(total=len(self.artifacts), kept=len(retenus), cause=cause)

    def fit_to_budget(self, overhead_tokens: int) -> ContextTrim:
        """Resserre la fenêtre pour que le prompt du tour tienne dans le budget.

        ``overhead_tokens`` est ce que pèse le RESTE du prompt : le gabarit, les
        sources déclarées, les modèles de prédiction, le contexte du tour
        précédent, la question. Rien de tout cela n'est compressible ici ; ce
        qui reste est pour le catalogue d'objets intermédiaires, et les plus
        ANCIENS partent d'abord.

        C'est ce qui distingue une dégradation délibérée d'un débordement subi :
        au retour, on sait exactement ce qui a été retiré et pourquoi, et on
        peut le dire — cf. :meth:`ContextTrim.message`.
        """
        budget = self.limits.token_budget
        if budget <= 0:
            return self.trim
        cap = len(self.retenus)
        while cap > 0 and overhead_tokens + estimate_tokens(self.describe()) > budget:
            cap -= 1
            self._budget_cap = cap
            self._apply_limits()
        if overhead_tokens + estimate_tokens(self.describe()) > budget:
            self.trim = self.trim.model_copy(
                update={
                    "over_budget": True,
                    "cause": f"budget DAA_CONTEXT_TOKEN_BUDGET={budget} tokens",
                }
            )
        return self.trim

    # -- persistance ----------------------------------------------------------

    def _manifest_path(self) -> Path:
        return self.dir / self.MANIFEST

    def _context_path(self) -> Path:
        return self.dir / self.CONTEXT

    def _load_context(self) -> ConversationContext:
        path = self._context_path()
        if not path.exists():
            return ConversationContext()
        return ConversationContext.model_validate_json(path.read_text(encoding="utf-8"))

    def record_turn(
        self,
        question: str,
        capability: str | None,
        source: str | None,
        code: str | None = None,
        dataset: str | None = None,
        features: dict | None = None,
    ) -> None:
        """Mémorise le tour courant (question + action) pour comprendre le suivant."""
        make_private_dir(self.dir)
        contexte = ConversationContext(
            last_question=question,
            last_capability=capability,
            last_source=source,
            last_code=code,
            last_dataset=dataset,
            last_features=features or {},
        )
        with conversation_lock(self.dir):
            self.context = contexte
            write_text_atomic(self._context_path(), contexte.model_dump_json(indent=2))

    def describe_context(self) -> str | None:
        """Contexte du tour précédent pour le planificateur (résolution des ajustements)."""
        c = self.context
        if not c.last_question:
            return None
        src = f" sur « {c.last_source} »" if c.last_source else ""
        return (
            f"CONTEXTE CONVERSATIONNEL : au tour précédent, l'utilisateur a demandé "
            f"« {c.last_question} » (action : {c.last_capability or '?'}{src}). Si le "
            "message courant est un AJUSTEMENT de ce tour (« mets des couleurs plus "
            "vives », « plutôt en barres », « et pour les hommes ? »), reprends la MÊME "
            f"capacité et la MÊME source{src}."
        )

    def last_code_for(self, source: str | None) -> str | None:
        """Le code d'analyse du tour précédent si c'était sur la même source."""
        c = self.context
        return c.last_code if (source is not None and c.last_source == source) else None

    def last_features_for(self, dataset: str | None) -> dict:
        """Les features de la dernière prédiction réussie, si c'est le même dataset.

        Base d'un ajustement (« et si elle était en 3e classe ? ») : sans elles,
        le tour suivant repart de zéro et redemande tout.
        """
        c = self.context
        if dataset is None or c.last_dataset != dataset:
            return {}
        return dict(c.last_features)

    def _load(self) -> list[WorkspaceArtifact]:
        path = self._manifest_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [WorkspaceArtifact.model_validate(a) for a in data.get("artifacts", [])]

    def _save_manifest(self) -> None:
        payload = {"artifacts": [a.model_dump() for a in self.artifacts]}
        write_text_atomic(self._manifest_path(), json.dumps(payload, ensure_ascii=False, indent=2))

    def _claim_artifact_name(self, taken: set[str], kind: str) -> tuple[str, Path]:
        """Réserve le premier ``<préfixe>_N`` libre de cette nature, fichier vide créé.

        Le nom était calculé par ``f"resultat_{len(self.artifacts) + 1}"`` sur
        l'état lu au DÉBUT du tour : deux tours partis du même état écrivaient
        tous les deux ``resultat_1.csv``, le second écrasant le premier. La
        réservation se fait donc maintenant sur le disque, au moment d'écrire :
        ``O_CREAT | O_EXCL`` échoue si le fichier existe déjà, et cet échec est
        indivisible — c'est le noyau qui arbitre, pas un compteur lu à distance.

        La convention de nom des tableaux ne change pas (``resultat_1``,
        ``resultat_2``…) : les workspaces déjà sur disque restent lisibles, et
        le nom réservé reste celui de la source éphémère et de la table DuckDB.
        Le code produit des ``graphique_N`` et des ``analyse_N`` — une
        **numérotation par nature**, pour qu'un nom dise ce qu'il désigne sans
        qu'il faille ouvrir le manifeste. C'est ce nom que l'utilisateur lira
        dans le catalogue et que le modèle passera aux outils de rappel.
        """
        numero = 1
        while True:
            name = f"{PREFIXE[kind]}_{numero}"
            path = self.dir / f"{name}{EXTENSION[kind]}"
            if name not in taken:
                try:
                    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                except FileExistsError:
                    pass  # fichier présent sans entrée au manifeste : nom déjà pris
                else:
                    return name, path
            numero += 1

    def _enregistrer(self, kind: str, ecrire, **champs) -> WorkspaceArtifact:
        """Réserve un nom, écrit le fichier par ``ecrire(chemin)``, publie l'entrée.

        Le chemin d'écriture est le même pour les trois natures, et il ne peut
        pas se dédoubler : c'est lui qui porte la reprise du manifeste sous
        verrou, la réservation de nom arbitrée par le noyau et la réapplication
        des fenêtres. Deux copies de cette séquence, c'était deux occasions d'en
        oublier un morceau.
        """
        make_private_dir(self.dir)
        with conversation_lock(self.dir):
            # le manifeste est relu ici, et pas au début du tour : un tour
            # concurrent a pu en ajouter une entrée entre-temps, et l'écraser
            # ferait disparaître son objet du manifeste alors que le fichier existe.
            persistes = self._load()
            name, path = self._claim_artifact_name({a.name for a in persistes}, kind)
            with atomic_write_to(path) as tmp:
                ecrire(tmp)
            artifact = WorkspaceArtifact(name=name, file=path.name, kind=kind, **champs)
            self.artifacts = [*persistes, artifact]
            self._save_manifest()
            self._apply_limits()
        return artifact

    def save_table(
        self, columns: list[str], rows: list[list], question: str, *, tronque: bool = False
    ) -> WorkspaceArtifact:
        """Écrit un tableau en CSV, l'ajoute au manifeste et le renvoie.

        ``tronque`` dit que la requête qui l'a produit a été coupée. Il est
        retenu et pas seulement affiché : c'est au tour SUIVANT qu'il sert, et
        le tour suivant n'a plus le ``QueryResult`` sous la main.
        """
        table = pd.DataFrame(rows, columns=columns)
        coupe = " ; tronqué" if tronque else ""
        return self._enregistrer(
            KIND_TABLE,
            lambda chemin: table.to_csv(chemin, index=False),
            columns=list(columns),
            row_count=len(rows),
            question=question,
            tronque=tronque,
            description=f"tableau de {len(rows)} ligne(s){coupe} ; colonnes : {', '.join(columns)}",
        )

    def save_code(
        self, code: str, question: str, *, source: str = "", figures: int = 0
    ) -> WorkspaceArtifact:
        """Écrit le code d'une analyse et l'ajoute au manifeste.

        **C'est ce que le propriétaire demande à pouvoir rappeler** : « le code
        qui génère une image doit pouvoir être rappelé pour être modifié ».
        L'image, elle, part dans la réponse et n'est pas persistée — la
        repeindre ne rendrait rien, alors que rejouer son code en rend une
        neuve.

        ``source`` est la source qui a été interrogée. Elle est retenue parce
        qu'un rejeu doit remonter le MÊME décor de données : sans elle, le code
        rappelé chercherait des CSV sous ``/data/`` que personne n'aurait
        montés.
        """
        return self._enregistrer(
            KIND_FIGURE if figures else KIND_CODE,
            lambda chemin: chemin.write_text(code, encoding="utf-8"),
            question=question,
            source=source,
            description=(
                f"code Python d'une figure ({figures} image(s))"
                if figures
                else "code Python d'analyse (sans figure)"
            ),
        )

    # -- réexposition ---------------------------------------------------------

    def path_of(self, artifact: WorkspaceArtifact) -> Path:
        return self.dir / artifact.file

    def as_sources(self) -> list[FileSource]:
        """Les objets RÉINJECTÉS vus comme des sources fichier interrogeables."""
        return [
            FileSource(
                name=a.name,
                description=f"Tableau intermédiaire ({a.row_count} lignes) issu de : {a.question}",
                path=self.path_of(a),
            )
            for a in self.injected
        ]

    def sandbox_files(self) -> dict[Path, str]:
        """Mapping chemin hôte -> nom sous /data/ des objets RÉINJECTÉS."""
        return {self.path_of(a): a.file for a in self.injected}

    def describe(self) -> str | None:
        """Description des objets RÉINJECTÉS pour le prompt du planificateur.

        L'éviction y est dite explicitement : sans cela le planificateur
        désignerait comme source un tableau qui n'est plus au catalogue effectif
        ni monté dans la sandbox, et l'utilisateur lirait « source introuvable »
        sans pouvoir comprendre pourquoi.
        """
        blocs = []
        if self.injected:
            lines = [
                f"- {a.name} ({a.row_count} lignes ; colonnes : {', '.join(a.columns)})"
                f" — produit par : « {a.question} »"
                for a in self.injected
            ]
            latest = self.injected[-1].name
            blocs.append(
                "Objets intermédiaires déjà produits dans CETTE conversation "
                "(interrogeables comme des sources par leur nom, ou réutilisables tels "
                "quels pour une prédiction) :\n"
                + "\n".join(lines)
                + f"\nLe plus récent est '{latest}'. Une référence comme « ces lignes », "
                "« ces fleurs », « le tableau précédent » ou « ce résultat » désigne en "
                "général ce dernier : choisis-le comme `source`. Pour PRÉDIRE sur un tel "
                "tableau (« prédis ces lignes »), utilise fetch_then_predict avec ce tableau "
                "comme `source`."
            )
        if self.codes_injectes:
            blocs.append(self.catalogue_du_code())
        avis = self.trim.planner_notice()
        if avis:
            blocs.append(avis)
        return "\n\n".join(blocs) or None

    # -- le catalogue, et rien que le catalogue -------------------------------

    def catalogue_du_code(self) -> str:
        """Le code retenu, UNE LIGNE par artefact — jamais son contenu.

        C'est la moitié qui fait tenir la fenêtre. Injecter le code des
        figures d'une conversation qui dure ferait exploser le prompt du
        planificateur ; injecter leur index coûte une ligne chacune, et suffit
        pour que « le graphe de tout à l'heure » désigne quelque chose. Le
        contenu s'ouvre à la demande, par ``lire_un_artefact``.
        """
        lignes = "\n".join(a.ligne_de_catalogue() for a in self.codes_injectes)
        return (
            "Code déjà produit dans CETTE conversation (rappelable par son NOM, pour "
            "être relu ou rejoué avec une modification) :\n" + lignes
        )

    def catalogue(self) -> list[WorkspaceArtifact]:
        """Tout ce qui est RETENU ce tour-ci, tableaux et code, dans l'ordre."""
        return list(self.retenus)

    # -- désigner un artefact par son nom --------------------------------------

    def sur_le_disque(self, name: str) -> WorkspaceArtifact | None:
        """L'artefact de ce nom **parmi tout ce que porte le disque**, ou ``None``.

        Distinct de :meth:`retenu` : c'est ce qui permet de dire « il a été
        évincé » au lieu de « il n'existe pas ». Les deux sont des refus, mais
        ils n'appellent pas la même réaction — l'un se règle en relançant la
        question, l'autre non.
        """
        return next((a for a in self.artifacts if a.name == name), None)

    def retenu(self, name: str) -> WorkspaceArtifact | None:
        """L'artefact de ce nom **parmi ceux du contexte de ce tour**, ou ``None``."""
        return next((a for a in self.retenus if a.name == name), None)

    def lire(self, artifact: WorkspaceArtifact) -> str:
        """Le CONTENU d'un artefact, tel qu'on le remet en contexte.

        Le code est rendu tel quel : c'est lui qu'on va modifier, le tronquer
        rendrait le rejeu impossible. Un tableau est rendu par sa tête
        (``LIGNES_LUES``) — on l'ouvre pour savoir de quoi on parle, pas pour
        recopier dix mille lignes dans une fenêtre de contexte ; le compte
        complet est dit, pour que personne ne prenne l'extrait pour le tout.
        """
        texte = self.path_of(artifact).read_text(encoding="utf-8")
        if artifact.est_du_code:
            return texte
        lignes = texte.splitlines()
        tete = "\n".join(lignes[: LIGNES_LUES + 1])
        if artifact.row_count > LIGNES_LUES:
            tete += f"\n… ({artifact.row_count} lignes au total, {LIGNES_LUES} montrées)"
        return tete
