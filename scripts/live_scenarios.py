"""Batterie de scénarios LIVE (bout en bout) contre l'API en marche.

Contrairement à la suite pytest (LLM scripté, déterministe), ce runner
interroge le VRAI système — LLM local (Ollama), Postgres, sandbox Docker — sur
des conversations multi-tours « en cascade » (chaque tour dépend des
précédents : mémoire, changement de source, slot-filling…).

Le LLM étant non déterministe, on vérifie des INVARIANTS, à deux niveaux :

- DUR (fait échouer la suite, code de sortie != 0) : HTTP 200, aucune exception
  brute renvoyée à l'utilisateur (KeyError/Traceback…), et une erreur attendue
  seulement là où on l'attend.
- SOUPLE (rapporté, n'échoue pas) : capacité routée, nœuds de la trace,
  présence d'un tableau/figure, quelques mots-clés dans la réponse.

Prérequis : l'API tourne (uvicorn), Ollama a le modèle, Postgres 'titanic' est
seedé (scripts/seed_titanic_postgres.py), l'image sandbox est construite.

    uv run python scripts/live_scenarios.py            # tout
    uv run python scripts/live_scenarios.py --base-url http://localhost:8000
    uv run python scripts/live_scenarios.py --only iris titanic-join
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

# --- marqueurs d'une exception brute qui aurait fui jusqu'à l'utilisateur ------
LEAK_MARKERS = ("Traceback (most recent call", "KeyError", "ValueError:", '  File "')


@dataclass
class Turn:
    msg: str
    expect_error: bool = False  # une erreur « métier » propre est-elle attendue ?
    capability: str | None = None  # capacité attendue (souple)
    nodes: list[str] = field(default_factory=list)  # nœuds attendus dans la trace (souple)
    answer_regex: list[str] = field(
        default_factory=list
    )  # motifs attendus (souple, insensible casse)
    artifact: str | None = None  # "table" | "image" | None (souple)
    clarify: bool = False  # la réponse doit être une question (souple)


@dataclass
class Scenario:
    key: str
    title: str
    turns: list[Turn]


# --- définition des scénarios en cascade --------------------------------------

SCENARIOS: list[Scenario] = [
    Scenario(
        "iris",
        "Iris : exploration → mémoire → prédiction → analyse",
        [
            Turn(
                "donne-moi les 3 dernières lignes du dataset iris",
                capability="query",
                nodes=["plan", "retrieval", "synthesize"],
                artifact="table",
                answer_regex=[r"lignes?"],
            ),
            Turn(
                "peux-tu prédire ces 3 lignes avec le modèle auquel tu as accès ?",
                capability="fetch_then_predict",
                nodes=["plan", "fetch_predict", "synthesize"],
                artifact="table",
                answer_regex=[r"[Pp]r[ée]diction", r"iris"],
            ),
            Turn(
                "fais un histogramme des sepal_length du tableau resultat_1",
                capability="analyze",
                nodes=["plan", "analysis", "synthesize"],
                artifact="image",
            ),
        ],
    ),
    Scenario(
        "titanic-join",
        "Titanic : jointure forcée → agrégat enchaîné",
        [
            Turn(
                "sur la base titanic, donne le taux de survie par LIBELLÉ de classe "
                "(colonne label de la table classes), pas par identifiant",
                capability="query",
                nodes=["plan", "retrieval", "synthesize"],
                artifact="table",
            ),
            Turn(
                "et combien de passagers y a-t-il par classe ?",
                capability="query",
                artifact="table",
            ),
        ],
    ),
    Scenario(
        "clarification-source",
        "Clarification : question sans source, puis levée d'ambiguïté",
        [
            Turn(
                "combien de lignes y a-t-il au total ?",
                clarify=True,
                answer_regex=[r"titanic", r"iris"],
            ),
            Turn(
                "dans iris, combien de lignes ?",
                capability="query",
                artifact="table",
            ),
        ],
    ),
    Scenario(
        "slot-filling",
        "Prédiction hypothétique titanic : relance puis complément (multi-tours)",
        [
            Turn(
                "prédis la survie d'une femme de 1re classe",
                capability="predict",
                clarify=True,
                answer_regex=[r"age|âge"],
            ),
            Turn(
                "elle a 28 ans, seule à bord, billet à 80 livres, embarquée à Southampton",
                capability="predict",
                nodes=["plan", "inference", "synthesize"],
                answer_regex=[r"surv"],
            ),
        ],
    ),
    Scenario(
        "clarification-modele",
        "Clarification : prédiction sans modèle désigné (ex-KeyError)",
        [
            Turn(
                "peux-tu me faire une prédiction ?",
                clarify=True,
                answer_regex=[r"iris", r"titanic"],
            ),
        ],
    ),
    Scenario(
        "erreur-propre",
        "Erreur métier propre : individu inexistant (pas de crash)",
        [
            Turn(
                "sur titanic, prédis la survie du passager numéro 999999",
                expect_error=True,
                answer_regex=[r"Je n'ai pas pu répondre|aucune ligne"],
            ),
        ],
    ),
    Scenario(
        "california",
        "Régression California : prédiction chiffrée",
        [
            Turn(
                "prédis le prix médian pour MedInc=8.3, HouseAge=41, AveRooms=6.9, "
                "AveBedrms=1.02, Population=322, AveOccup=2.5, Latitude=37.88, Longitude=-122.23",
                capability="predict",
                nodes=["plan", "inference", "synthesize"],
                answer_regex=[r"[Pp]r[ée]diction"],
            ),
        ],
    ),
]


# --- exécution ----------------------------------------------------------------


def post_chat(base_url: str, message: str, conversation_id: str, timeout: float) -> dict[str, Any]:
    body = json.dumps({"message": message, "conversation_id": conversation_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


C_OK, C_WARN, C_BAD, C_DIM, C_RST = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def check_turn(turn: Turn, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Retourne (échecs DURS, avertissements SOUPLES)."""
    hard: list[str] = []
    soft: list[str] = []
    answer = data.get("answer") or ""
    error = data.get("error")
    plan = data.get("plan") or {}
    trace_nodes = [s.get("node") for s in data.get("trace") or []]
    artifacts = data.get("artifacts") or []

    # -- DUR : aucune exception brute n'a fui
    for marker in LEAK_MARKERS:
        if marker in answer:
            hard.append(f"exception brute dans la réponse ({marker!r})")
    # -- DUR : erreur seulement là où on l'attend
    if error and not turn.expect_error:
        hard.append(f"erreur inattendue : {error!r}")
    if turn.expect_error and not error and "Je n'ai pas pu répondre" not in answer:
        soft.append("erreur attendue mais réponse « normale »")

    # -- SOUPLE
    if turn.capability and plan.get("capability") != turn.capability:
        soft.append(f"capacité {plan.get('capability')!r} (attendu {turn.capability!r})")
    for node in turn.nodes:
        if node not in trace_nodes:
            soft.append(f"nœud '{node}' absent de la trace {trace_nodes}")
    for pattern in turn.answer_regex:
        if not re.search(pattern, answer, re.IGNORECASE):
            soft.append(f"motif /{pattern}/ absent de la réponse")
    if turn.artifact == "table" and not any(a.get("mime") == "application/json" for a in artifacts):
        soft.append("tableau attendu, absent")
    if turn.artifact == "image" and not any(a.get("mime") == "image/png" for a in artifacts):
        soft.append("figure attendue, absente")
    if turn.clarify and not answer.strip().endswith("?"):
        soft.append("clarification attendue (réponse ne finit pas par « ? »)")

    return hard, soft


def run(base_url: str, scenarios: list[Scenario], timeout: float) -> int:
    total_hard = total_soft = total_turns = 0
    print(f"Batterie live → {base_url}  ({len(scenarios)} scénarios)\n")
    for scenario in scenarios:
        print(f"{C_DIM}━━━{C_RST} {scenario.title}  [{scenario.key}]")
        conversation_id = uuid.uuid4().hex
        for i, turn in enumerate(scenario.turns, 1):
            total_turns += 1
            start = time.monotonic()
            try:
                data = post_chat(base_url, turn.msg, conversation_id, timeout)
            except (urllib.error.URLError, TimeoutError) as exc:
                total_hard += 1
                print(f"  {C_BAD}✗{C_RST} T{i} {turn.msg[:60]!r} — échec HTTP : {exc}")
                continue
            elapsed = time.monotonic() - start
            hard, soft = check_turn(turn, data)
            total_hard += len(hard)
            total_soft += len(soft)
            mark = (
                f"{C_BAD}✗{C_RST}" if hard else (f"{C_WARN}~{C_RST}" if soft else f"{C_OK}✓{C_RST}")
            )
            answer = (data.get("answer") or "").replace("\n", " ")
            print(f"  {mark} T{i} ({elapsed:4.1f}s) {turn.msg[:58]!r}")
            print(f"      → {answer[:110]}")
            for h in hard:
                print(f"      {C_BAD}DUR{C_RST}  {h}")
            for s in soft:
                print(f"      {C_WARN}~{C_RST}    {s}")
        print()

    verdict = f"{C_BAD}ÉCHEC{C_RST}" if total_hard else f"{C_OK}OK{C_RST}"
    print(
        f"Bilan : {verdict} — {total_turns} tours, "
        f"{total_hard} invariant(s) dur(s) violé(s), {total_soft} écart(s) souple(s)."
    )
    return 1 if total_hard else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Batterie de scénarios live contre l'API.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=180.0, help="timeout par requête (s)")
    parser.add_argument("--only", nargs="*", metavar="KEY", help="ne lancer que ces scénarios")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.only:
        scenarios = [s for s in SCENARIOS if s.key in set(args.only)]
        if not scenarios:
            keys = ", ".join(s.key for s in SCENARIOS)
            parser.error(f"aucun scénario ne correspond à {args.only} — connus : {keys}")

    sys.exit(run(args.base_url, scenarios, args.timeout))


if __name__ == "__main__":
    main()
