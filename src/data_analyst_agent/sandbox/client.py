"""Client de la sandbox : pilote un conteneur Docker durci via un protocole stdio.

Le conteneur exécute ``image/bridge.py`` (kernel Jupyter) ; chaque échange est
une ligne JSON (voir la docstring de bridge.py). Le durcissement est appliqué
ici, au ``docker run`` : réseau coupé, mémoire/CPU/PIDs bornés, capabilities
retirées, rootfs en lecture seule, tmpfs pour /tmp.

Le nombre de conteneurs vivants **en même temps** est plafonné (``SandboxPlaces``) :
borner la mémoire d'un conteneur ne sert à rien si rien ne borne leur nombre.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from data_analyst_agent.config import Settings, get_settings

_IMAGE_DIR = Path(__file__).parent / "image"


class SandboxError(RuntimeError):
    """Erreur d'infrastructure de la sandbox (démarrage, crash, protocole)."""


class MimeOutput(BaseModel):
    """Une sortie riche du kernel (ex. ``image/png`` encodée en base64)."""

    mime: str
    data: str


class SandboxResult(BaseModel):
    """Contrat de retour d'une exécution (docs/CADRAGE.md §6)."""

    status: Literal["ok", "error", "timeout"]
    stdout: str = ""
    stderr: str = ""
    results: list[MimeOutput] = Field(default_factory=list)
    error: str | None = None

    @property
    def images_png(self) -> list[str]:
        """Les sorties image/png (base64), dans l'ordre de production."""
        return [r.data for r in self.results if r.mime == "image/png"]


def docker_run_command(settings: Settings, mounts: dict[Path, str] | None = None) -> list[str]:
    """Construit la commande ``docker run`` durcie pour lancer le bridge."""
    command = [
        *settings.sandbox_docker_cmd,
        "run",
        "--rm",
        "--interactive",
        "--network=none",
        f"--memory={settings.sandbox_mem_limit}",
        f"--memory-swap={settings.sandbox_mem_limit}",
        f"--cpus={settings.sandbox_cpus}",
        f"--pids-limit={settings.sandbox_pids_limit}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs=/tmp:rw,size=256m",
        "--env=HOME=/tmp",
        "--env=MPLCONFIGDIR=/tmp/mpl",
        "--env=JUPYTER_RUNTIME_DIR=/tmp/jupyter",
        "--env=IPYTHONDIR=/tmp/ipython",
    ]
    for host_path, name in (mounts or {}).items():
        command.append(f"--volume={host_path}:/data/{name}:ro")
    command.append(settings.sandbox_image)
    return command


def ensure_image(settings: Settings | None = None) -> str:
    """Construit l'image sandbox si absente ; renvoie son tag."""
    settings = settings or get_settings()
    probe = subprocess.run(
        [*settings.sandbox_docker_cmd, "image", "inspect", settings.sandbox_image],
        capture_output=True,
    )
    if probe.returncode != 0:
        subprocess.run(
            [*settings.sandbox_docker_cmd, "build", "-t", settings.sandbox_image, str(_IMAGE_DIR)],
            check=True,
        )
    return settings.sandbox_image


class SandboxPlaces:
    """Plafond du nombre de conteneurs sandbox vivants simultanément.

    Chaque analyse ouvre sa propre session, donc son propre conteneur, qui
    réserve ``sandbox_mem_limit``. Dix analyses simultanées réservaient dix
    gigaoctets sans que rien ne s'y oppose (audit §2.3) : les quotas *par*
    conteneur ne disent rien de leur *nombre*.

    Le sémaphore transforme le dépassement en attente, puis — au bout de
    ``sandbox_queue_timeout`` — en refus explicite. Un refus est un résultat ;
    une machine qui part en mémoire virtuelle n'en est pas un.

    Portée process : c'est la mémoire de la machine qu'on protège, pas celle
    d'une requête. Les workers uvicorn supplémentaires ont chacun le leur — le
    plafond réel est donc ``sandbox_max_sessions x nombre de workers``, à ne pas
    perdre de vue au moment de dimensionner.
    """

    def __init__(self, limite: int) -> None:
        self.limite = limite
        self._semaphore = threading.BoundedSemaphore(limite) if limite > 0 else None

    def acquerir(self, timeout: float) -> None:
        """Réserve une place, ou lève ``SandboxError`` si l'attente a expiré."""
        if self._semaphore is None:
            return
        if not self._semaphore.acquire(timeout=timeout):
            raise SandboxError(
                f"toutes les sandboxes sont occupées ({self.limite} en parallèle) : "
                f"pas de place libérée en {timeout:g} s"
            )

    def liberer(self) -> None:
        if self._semaphore is not None:
            self._semaphore.release()


_places_partagees: SandboxPlaces | None = None
_verrou_des_places = threading.Lock()


def places_partagees(settings: Settings) -> SandboxPlaces:
    """Le compteur de places du process, construit au premier besoin.

    Refabriqué si le plafond configuré change — ce qui n'arrive qu'entre deux
    tests, jamais en service : les réglages d'un process sont fixés au
    démarrage. Un test qui veut son propre compteur passe le sien à la session.
    """
    global _places_partagees
    with _verrou_des_places:
        if _places_partagees is None or _places_partagees.limite != settings.sandbox_max_sessions:
            _places_partagees = SandboxPlaces(settings.sandbox_max_sessions)
        return _places_partagees


class SandboxSession:
    """Une session = un conteneur éphémère + un kernel, plusieurs ``execute()``.

    Utilisable en context manager. ``command`` court-circuite la construction
    de la commande docker (tests sans Docker, débogage local).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        mounts: dict[Path, str] | None = None,
        command: list[str] | None = None,
        places: SandboxPlaces | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._command = command or docker_run_command(self.settings, mounts)
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._places = places or places_partagees(self.settings)
        self._place_prise = False

    # -- cycle de vie ------------------------------------------------------

    def start(self) -> None:
        # La place se prend AVANT le `docker run` : réserver après coup
        # laisserait passer la pointe qu'on cherche justement à contenir. Une
        # session déjà pourvue ne reprend pas de place — sinon un `start()`
        # rejoué en retirerait une du compteur pour toujours.
        if not self._place_prise:
            self._places.acquerir(self.settings.sandbox_queue_timeout)
            self._place_prise = True
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            self._rendre_la_place()
            raise SandboxError(f"commande introuvable : {self._command[0]!r}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        ready = self._next_message(self.settings.sandbox_start_timeout)
        if ready is None or ready.get("op") != "ready":
            self.close()
            raise SandboxError(f"la sandbox n'a pas démarré : {self._diagnostics()}")

    def close(self) -> None:
        process = self._process
        self._process = None
        try:
            if process is None:
                return
            try:
                if process.stdin:
                    process.stdin.close()  # EOF -> le bridge arrête le kernel
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=10)
        finally:
            # Après l'attente, pas avant : la place n'est libre que quand le
            # conteneur a rendu sa mémoire. Et une seule fois, quel que soit le
            # nombre d'appels à `close()`.
            self._rendre_la_place()

    def _rendre_la_place(self) -> None:
        if self._place_prise:
            self._place_prise = False
            self._places.liberer()

    def __enter__(self) -> SandboxSession:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- exécution ---------------------------------------------------------

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult:
        """Exécute du code Python dans le kernel et renvoie ses sorties."""
        process = self._process
        if process is None or process.stdin is None:
            raise SandboxError("session non démarrée ou déjà fermée")
        timeout = timeout or self.settings.sandbox_exec_timeout
        request_id = uuid.uuid4().hex
        request = {"op": "execute", "id": request_id, "code": code, "timeout": timeout}
        try:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise SandboxError(f"sandbox injoignable : {self._diagnostics()}") from exc

        # Marge extérieure : le bridge interrompt le kernel au timeout ; si le
        # conteneur lui-même ne répond plus, on le tue.
        outer_deadline = timeout + self.settings.sandbox_kill_grace
        response = self._next_message(outer_deadline, expected_id=request_id)
        if response is None:
            self.close()
            return SandboxResult(
                status="timeout",
                error=f"aucune réponse après {outer_deadline:g} s — conteneur tué",
            )
        response.pop("id", None)
        return SandboxResult.model_validate(response)

    def ping(self) -> bool:
        """Vérifie que le bridge répond (protocole vivant)."""
        process = self._process
        if process is None or process.stdin is None:
            return False
        request_id = uuid.uuid4().hex
        process.stdin.write(json.dumps({"op": "ping", "id": request_id}) + "\n")
        process.stdin.flush()
        response = self._next_message(self.settings.sandbox_start_timeout, expected_id=request_id)
        return response is not None and response.get("status") == "ok"

    # -- plomberie interne -------------------------------------------------

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._messages.put(json.loads(line))
            except json.JSONDecodeError:
                self._stderr_tail.append(f"[stdout non-JSON] {line}")
        self._messages.put(None)  # EOF : le processus est mort

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip())

    def _next_message(self, timeout: float, expected_id: str | None = None) -> dict | None:
        """Attend le prochain message (ou celui d'id attendu). None = mort/timeout."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty:
                return None
            if message is None:
                return None
            if expected_id is not None and message.get("id") != expected_id:
                continue  # réponse orpheline d'une requête précédente
            return message

    def _diagnostics(self) -> str:
        tail = "\n".join(list(self._stderr_tail)[-20:])
        return f"stderr récent :\n{tail}" if tail else "(aucune sortie stderr)"
