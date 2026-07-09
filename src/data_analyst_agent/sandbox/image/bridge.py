"""Pont stdio <-> kernel Jupyter, exécuté DANS le conteneur sandbox.

Protocole ligne à ligne (une requête JSON par ligne sur stdin, une réponse
JSON par ligne sur stdout) :

    -> {"op": "execute", "id": "...", "code": "...", "timeout": 30}
    -> {"op": "ping", "id": "..."}
    <- {"id": "...", "status": "ok|error|timeout", "stdout": "...",
        "stderr": "...", "results": [{"mime": "...", "data": "..."}],
        "error": null | "..."}

Le stdout du bridge est réservé au protocole ; tout diagnostic part sur stderr.
Ce script ne dépend que de jupyter_client, présent dans l'image — il n'importe
rien du package hôte et n'est pas couvert par la mesure de couverture (il est
exercé par les tests d'intégration, à travers le conteneur).
"""

import json
import queue
import re
import sys
import time

from jupyter_client.manager import start_new_kernel

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Grâce laissée au kernel pour réagir à une interruption avant abandon.
INTERRUPT_GRACE_S = 5.0

SETUP_CODE = """
%matplotlib inline
import matplotlib
matplotlib.rcParams["figure.figsize"] = (8, 5)
"""


def log(message: str) -> None:
    print(f"bridge: {message}", file=sys.stderr, flush=True)


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def execute_request(km, kc, code: str, timeout: float) -> dict:
    """Exécute `code` dans le kernel et collecte les sorties iopub jusqu'à idle."""
    msg_id = kc.execute(code)
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    results: list[dict] = []
    error = None
    status = "ok"
    deadline = time.monotonic() + timeout
    grace_deadline = None

    while True:
        now = time.monotonic()
        if grace_deadline is None and now > deadline:
            km.interrupt_kernel()
            status = "timeout"
            error = f"exécution interrompue après {timeout:g} s"
            grace_deadline = now + INTERRUPT_GRACE_S
        if grace_deadline is not None and now > grace_deadline:
            # Le kernel n'a pas répondu à l'interruption ; l'hôte tuera le
            # conteneur à l'expiration de sa propre marge (kill_grace).
            break
        try:
            msg = kc.get_iopub_msg(timeout=0.2)
        except queue.Empty:
            continue
        if msg.get("parent_header", {}).get("msg_id") != msg_id:
            continue
        msg_type = msg["msg_type"]
        content = msg["content"]
        if msg_type == "stream":
            target = stdout_parts if content["name"] == "stdout" else stderr_parts
            target.append(content["text"])
        elif msg_type in ("execute_result", "display_data"):
            data = content.get("data", {})
            for mime, value in data.items():
                if mime == "text/plain" and len(data) > 1:
                    continue  # une représentation riche existe, on la préfère
                results.append(
                    {"mime": mime, "data": value if isinstance(value, str) else json.dumps(value)}
                )
        elif msg_type == "error":
            traceback = strip_ansi("\n".join(content.get("traceback", [])))
            if error is None:
                error = traceback or f"{content.get('ename')}: {content.get('evalue')}"
            if status == "ok":
                status = "error"
        elif msg_type == "status" and content.get("execution_state") == "idle":
            break

    return {
        "status": status,
        "stdout": "".join(stdout_parts),
        "stderr": "".join(stderr_parts),
        "results": results,
        "error": error,
    }


def main() -> None:
    log("démarrage du kernel...")
    km, kc = start_new_kernel(startup_timeout=60)
    execute_request(km, kc, SETUP_CODE, timeout=60)
    log("kernel prêt")
    send({"op": "ready"})
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                send(
                    {
                        "id": None,
                        "status": "error",
                        "stdout": "",
                        "stderr": "",
                        "results": [],
                        "error": "requête JSON invalide",
                    }
                )
                continue
            request_id = request.get("id")
            op = request.get("op")
            if op == "ping":
                send(
                    {
                        "id": request_id,
                        "status": "ok",
                        "stdout": "",
                        "stderr": "",
                        "results": [],
                        "error": None,
                    }
                )
            elif op == "execute":
                timeout = float(request.get("timeout") or 30.0)
                response = execute_request(km, kc, request.get("code", ""), timeout)
                response["id"] = request_id
                send(response)
            else:
                send(
                    {
                        "id": request_id,
                        "status": "error",
                        "stdout": "",
                        "stderr": "",
                        "results": [],
                        "error": f"opération inconnue : {op!r}",
                    }
                )
    finally:
        log("arrêt du kernel")
        kc.stop_channels()
        km.shutdown_kernel(now=True)


if __name__ == "__main__":
    main()
