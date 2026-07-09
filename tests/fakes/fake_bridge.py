"""Faux bridge parlant le protocole sandbox — pour tester le client sans Docker.

Comportements déclenchés par des marqueurs dans le code reçu :
FAKE_CRASH (mort brutale), FAKE_SLEEP (ne répond pas à temps), FAKE_ERROR,
FAKE_TIMEOUT (timeout côté bridge), FAKE_PNG, FAKE_NOISE (ligne non-JSON),
FAKE_ORPHAN (réponse avec mauvais id d'abord). Lancé avec --silent, il
n'envoie jamais le ready (test d'échec de démarrage).
"""

import json
import sys
import time

# PNG 1x1 pixel, valide, en base64
TINY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="  # noqa: E501


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def response(request_id: str, **overrides) -> dict:
    base = {
        "id": request_id,
        "status": "ok",
        "stdout": "",
        "stderr": "",
        "results": [],
        "error": None,
    }
    base.update(overrides)
    return base


def main() -> None:
    if "--silent" in sys.argv:
        time.sleep(30)
        return
    send({"op": "ready"})
    for line in sys.stdin:
        request = json.loads(line)
        request_id = request.get("id")
        if request.get("op") == "ping":
            send(response(request_id))
            continue
        code = request.get("code", "")
        if "FAKE_CRASH" in code:
            sys.exit(1)
        if "FAKE_SLEEP" in code:
            time.sleep(30)
        if "FAKE_NOISE" in code:
            sys.stdout.write("ligne de bruit non JSON\n")
            sys.stdout.flush()
        if "FAKE_ORPHAN" in code:
            send(response("id-orphelin", stdout="orphelin\n"))
        if "FAKE_ERROR" in code:
            send(response(request_id, status="error", error="ZeroDivisionError: division by zero"))
        elif "FAKE_TIMEOUT" in code:
            send(response(request_id, status="timeout", error="exécution interrompue après 5 s"))
        elif "FAKE_PNG" in code:
            send(response(request_id, results=[{"mime": "image/png", "data": TINY_PNG}]))
        else:
            send(response(request_id, stdout="ok\n"))


if __name__ == "__main__":
    main()
