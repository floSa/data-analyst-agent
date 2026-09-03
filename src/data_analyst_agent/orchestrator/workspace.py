"""Mémoire de conversation : persiste les tableaux intermédiaires en CSV.

Chaque conversation possède un dossier ; les tableaux produits (résultats de
requête, lots de prédiction) y sont écrits en CSV et décrits dans un manifeste
JSON. Aux tours suivants, ces tableaux sont réexposés :

- comme **sources éphémères** interrogeables en SQL (DuckDB) et réutilisables
  pour une prédiction (« prédis ces lignes ») ;
- **montés dans la sandbox** pour que le code d'analyse généré puisse les relire
  (``pd.read_csv('/data/resultat_1.csv')``) ;
- **décrits au planificateur** pour qu'il sache y faire référence.

Le nom d'un objet (``resultat_1``, ``resultat_2``…) est aussi le nom de la
source éphémère et de la table DuckDB correspondante (via le nom de fichier).

Cette réexposition est **plafonnée** : ``artifacts`` est ce que porte le disque,
``injected`` ce qui entre réellement dans le contexte du tour (cf.
:mod:`data_analyst_agent.orchestrator.context_budget`). Les trois usages
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

import pandas as pd
from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.catalog import FileSource
from data_analyst_agent.orchestrator.context_budget import ContextLimits, ContextTrim

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


class WorkspaceArtifact(BaseModel):
    """Métadonnées d'un tableau intermédiaire persisté."""

    name: str  # nom d'usage = nom de source éphémère = nom de table DuckDB
    file: str  # nom du fichier CSV, relatif au dossier de la conversation
    columns: list[str]
    row_count: int
    question: str  # la question qui l'a produit (aide le planificateur)


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
    """Dossier de travail d'une conversation (objets intermédiaires en CSV)."""

    MANIFEST = "manifest.json"
    CONTEXT = "context.json"

    def __init__(
        self, base_dir: Path, conversation_id: str, *, limits: ContextLimits | None = None
    ) -> None:
        self.dir = Path(base_dir) / safe_dir_name(conversation_id)
        self.limits = limits or ContextLimits()
        self.artifacts: list[WorkspaceArtifact] = self._load()
        self.context: ConversationContext = self._load_context()
        # `artifacts` est le disque, `injected` est le contexte : la fenêtre
        # sépare les deux, et `trim` dit ce qu'elle a écarté.
        self.injected: list[WorkspaceArtifact] = []
        self.trim: ContextTrim = ContextTrim()
        self._apply_limits()

    # -- plafond du contexte --------------------------------------------------

    def _apply_limits(self) -> None:
        """Recalcule ``injected`` et ``trim`` à partir de ``artifacts``.

        Appelé à l'ouverture ET après chaque ``save_table`` : un tableau produit
        au tour courant doit entrer dans la fenêtre (c'est le plus récent, donc
        le plus susceptible d'être désigné par « ces lignes »), et l'éviction
        qu'il provoque doit être visible tout de suite.
        """
        fenetre = self.limits.artifact_window
        total = len(self.artifacts)
        if fenetre <= 0 or total <= fenetre:
            self.injected = list(self.artifacts)
            self.trim = ContextTrim(total=total, kept=total)
            return
        self.injected = self.artifacts[-fenetre:]
        self.trim = ContextTrim(
            total=total,
            kept=len(self.injected),
            cause=f"fenêtre DAA_CONTEXT_ARTIFACT_WINDOW={fenetre}",
        )

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

    def _claim_artifact_name(self, taken: set[str]) -> tuple[str, Path]:
        """Réserve le premier ``resultat_N`` libre, en créant son CSV vide.

        Le nom était calculé par ``f"resultat_{len(self.artifacts) + 1}"`` sur
        l'état lu au DÉBUT du tour : deux tours partis du même état écrivaient
        tous les deux ``resultat_1.csv``, le second écrasant le premier. La
        réservation se fait donc maintenant sur le disque, au moment d'écrire :
        ``O_CREAT | O_EXCL`` échoue si le fichier existe déjà, et cet échec est
        indivisible — c'est le noyau qui arbitre, pas un compteur lu à distance.

        La convention de nom ne change pas (``resultat_1``, ``resultat_2``…) :
        les workspaces déjà sur disque restent lisibles, et le nom réservé reste
        celui de la source éphémère et de la table DuckDB.
        """
        numero = 1
        while True:
            name = f"resultat_{numero}"
            path = self.dir / f"{name}.csv"
            if name not in taken:
                try:
                    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                except FileExistsError:
                    pass  # CSV présent sans entrée au manifeste : nom déjà pris
                else:
                    return name, path
            numero += 1

    def save_table(self, columns: list[str], rows: list[list], question: str) -> WorkspaceArtifact:
        """Écrit un tableau en CSV, l'ajoute au manifeste et le renvoie."""
        make_private_dir(self.dir)
        table = pd.DataFrame(rows, columns=columns)
        with conversation_lock(self.dir):
            # le manifeste est relu ici, et pas au début du tour : un tour
            # concurrent a pu en ajouter une entrée entre-temps, et l'écraser
            # ferait disparaître son tableau du manifeste alors que le CSV existe.
            persistes = self._load()
            name, path = self._claim_artifact_name({a.name for a in persistes})
            with atomic_write_to(path) as tmp:
                table.to_csv(tmp, index=False)
            artifact = WorkspaceArtifact(
                name=name,
                file=path.name,
                columns=list(columns),
                row_count=len(rows),
                question=question,
            )
            self.artifacts = [*persistes, artifact]
            self._save_manifest()
            self._apply_limits()
        return artifact

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
        avis = self.trim.planner_notice()
        if avis:
            blocs.append(avis)
        return "\n\n".join(blocs) or None
