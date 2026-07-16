"""Batterie de scénarios LIVE (bout en bout) contre l'API en marche.

Contrairement à la suite pytest (LLM scripté, déterministe), ce runner
interroge le VRAI système — LLM local (Ollama), Postgres, sandbox Docker — sur
cinq conversations de cinq tours « en cascade » (chaque tour dépend des
précédents : mémoire, anaphores, figures, slot-filling…), telles qu'un analyste
les mène : décrire un dataset, creuser la réponse précédente, demander une
figure, puis prédire — en demandant d'abord quels attributs et sous quel format.

Le LLM étant non déterministe, on vérifie des INVARIANTS, à deux niveaux :

- DUR (fait échouer la suite, code de sortie != 0) : HTTP 200, aucune exception
  brute renvoyée à l'utilisateur (KeyError/Traceback…), et une erreur attendue
  seulement là où on l'attend.
- DUR aussi : les CHIFFRES rendus, comparés à une vérité terrain calculée hors
  de l'agent (`expect_data`) — sans quoi une réponse bien tournée sur des
  chiffres faux passe pour un succès.
- SOUPLE (rapporté, n'échoue pas) : capacité routée, nœuds de la trace,
  présence d'un tableau/figure, quelques mots-clés dans la réponse.

Prérequis : l'API tourne (uvicorn), Ollama a le modèle, Postgres 'titanic' est
seedé (scripts/seed_titanic_postgres.py), l'image sandbox est construite.

    uv run python scripts/live_scenarios.py            # tout
    uv run python scripts/live_scenarios.py --base-url http://localhost:8000
    uv run python scripts/live_scenarios.py --only iris-description titanic-tarifs
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
    # -- invariant de DONNÉES (dur) : valeurs de référence, calculées à la main
    # depuis la base/le CSV, qui doivent apparaître dans la réponse ou le tableau.
    # Une phrase bien tournée sur des chiffres faux est le pire échec possible :
    # le reste des invariants ne le voit pas.
    expect_data: list[float | str] = field(default_factory=list)


@dataclass
class Scenario:
    key: str
    title: str
    turns: list[Turn]


# --- définition des scénarios en cascade --------------------------------------

SCENARIOS: list[Scenario] = [
    # Cinq conversations de cinq tours, telles qu'un analyste les mène vraiment :
    # on découvre un dataset, on creuse sur ce qui vient d'être répondu, on
    # demande une figure, puis on prédit — en commençant par demander à l'agent
    # CE QU'IL LUI FAUT et dans quel format.
    #
    # Les `expect_data` sont la VÉRITÉ TERRAIN, calculée hors de l'agent (SQL
    # direct sur la base, pandas sur le CSV, predict sur le modèle). Sans elles,
    # une réponse bien tournée sur des chiffres faux passe pour un succès.
    Scenario(
        "iris-description",
        "Iris : description du dataset → min/max → moyenne par espèce → 2 figures",
        [
            Turn(
                "peux-tu me faire une description du dataset iris ?",
                expect_data=[150],
                answer_regex=[r"iris"],
            ),
            Turn(
                "quelles sont les valeurs minimum et maximum de sepal_length ?",
                expect_data=[4.3, 7.9],
            ),
            Turn(
                "et la longueur moyenne des sépales par espèce ?",
                artifact="table",
                expect_data=[5.006, 5.936, 6.588, "setosa"],
            ),
            Turn(
                "fais-moi un histogramme des sepal_length",
                capability="analyze",
                nodes=["plan", "analysis", "synthesize"],
                artifact="image",
            ),
            Turn(
                "et un nuage de points sepal_length vs petal_length coloré par espèce",
                capability="analyze",
                artifact="image",
            ),
        ],
    ),
    Scenario(
        "titanic-exploration",
        "Titanic : description → bornes d'âge → survie par classe → figure → croisement",
        [
            Turn(
                "décris-moi la base titanic : quelles tables et quelles colonnes ?",
                expect_data=["passengers", "classes"],
            ),
            Turn(
                "combien de passagers en tout, et quel est l'âge minimum et maximum ?",
                expect_data=[891, 0.42, 80],
            ),
            Turn(
                "quel est le taux de survie par LIBELLÉ de classe "
                "(colonne label de la table classes) ?",
                capability="query",
                artifact="table",
                expect_data=[(62.96, 0.6296), (47.28, 0.4728), (24.24, 0.2424), "1re classe"],
            ),
            Turn(
                "fais un diagramme en barres de ces taux",
                capability="analyze",
                artifact="image",
            ),
            Turn(
                "et combien de femmes et d'hommes parmi les survivants ?",
                capability="query",
                expect_data=[233, 109],
            ),
        ],
    ),
    Scenario(
        "titanic-tarifs",
        "Titanic : tarif moyen par classe → maximum → figure → ports → figure",
        [
            Turn(
                "sur titanic, quel est le tarif moyen par classe (libellé) ?",
                capability="query",
                artifact="table",
                expect_data=[84.1547, 20.6622, 13.6756],
            ),
            Turn(
                "et quel est le tarif maximum payé ?",
                capability="query",
                expect_data=[512.3292],
            ),
            Turn(
                "montre-moi la distribution des tarifs en histogramme",
                capability="analyze",
                artifact="image",
            ),
            Turn(
                "quelle est la répartition des ports d'embarquement ?",
                capability="query",
                artifact="table",
                expect_data=[644, 168, 77],
            ),
            Turn(
                "fais-en un diagramme en barres",
                capability="analyze",
                artifact="image",
            ),
        ],
    ),
    Scenario(
        "iris-prediction",
        "Iris : quels attributs ? → prédiction → ajustement → tableau mémorisé → lot",
        [
            Turn(
                "je voudrais faire une prédiction sur iris : de quels attributs as-tu "
                "besoin, et sous quel format ?",
                expect_data=["sepal_length", "petal_width"],
            ),
            Turn(
                "ok : sepal_length=5.1, sepal_width=3.5, petal_length=1.4, petal_width=0.2",
                capability="predict",
                nodes=["plan", "inference", "synthesize"],
                expect_data=["setosa"],
            ),
            Turn(
                "et si petal_length passait à 5.0 et petal_width à 1.8 ?",
                capability="predict",
                expect_data=["virginica"],
            ),
            Turn(
                "donne-moi les 5 premières fleurs d'iris",
                capability="query",
                artifact="table",
                expect_data=[5.1, 3.5, 1.4, 0.2],
            ),
            Turn(
                "prédis l'espèce de ces lignes",
                capability="fetch_then_predict",
                nodes=["plan", "fetch_predict", "synthesize"],
                artifact="table",
                expect_data=["setosa", 5],
            ),
        ],
    ),
    Scenario(
        "titanic-prediction",
        "Titanic : modèle ? → attributs et format → relance → prédiction → ajustement",
        [
            Turn(
                "peux-tu me faire une prédiction ?",
                clarify=True,
                answer_regex=[r"iris", r"titanic"],
            ),
            Turn(
                "titanic : de quels attributs as-tu besoin, et sous quel format ?",
                expect_data=["embarked", "Southampton"],
            ),
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
                expect_data=["a survécu", 93.4],
            ),
            Turn(
                "et si elle était en 3e classe ?",
                capability="predict",
                expect_data=["a survécu", 64.6],
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


def _numbers_in(text: str) -> list[tuple[float, int]]:
    """Les nombres d'un texte, avec leur nombre de décimales AFFICHÉES.

    La précision affichée est ce qui permet de juger : « 63 » est un arrondi
    acceptable de 62.96, « 62.9 » non, et « 887 » n'est pas 891. Virgule
    décimale et séparateurs de milliers (espaces) admis.
    """
    trouves: list[tuple[float, int]] = []
    for brut in re.findall(r"-?\d[\d\u00a0\u202f ]*(?:[.,]\d+)?", text):
        nettoye = re.sub(r"[\u00a0\u202f ]", "", brut).replace(",", ".")
        try:
            valeur = float(nettoye)
        except ValueError:
            continue
        trouves.append((valeur, len(nettoye.partition(".")[2])))
    return trouves


def _haystack(data: dict[str, Any]) -> tuple[str, list[tuple[float, int]], list[float]]:
    """Où chercher une valeur attendue : le texte, les nombres de la RÉPONSE, et
    séparément ceux des TABLEAUX — les deux ne se jugent pas pareil (cf.
    ``check_data``).
    """
    reponse = data.get("answer") or ""
    texte = reponse
    nombres_table: list[float] = []
    for artefact in data.get("artifacts") or []:
        if artefact.get("mime") == "application/json":
            try:
                table = json.loads(artefact["data"])
            except (ValueError, KeyError):
                continue
            texte += " " + json.dumps(table, ensure_ascii=False)
            nombres_table.extend(
                valeur
                for ligne in table.get("rows") or []
                for valeur in ligne
                if isinstance(valeur, (int, float)) and not isinstance(valeur, bool)
            )
    return texte, _numbers_in(reponse), nombres_table


def _decimales_de(valeur: float) -> int:
    """Décimales significatives d'une valeur de référence (62.96 → 2, 891 → 0)."""
    texte = repr(float(valeur))
    if "e" in texte or "E" in texte:  # notation scientifique : on ne tronque pas
        return 12
    return len(texte.partition(".")[2].rstrip("0"))


def _correspond_dans_la_reponse(produit: float, decimales: int, attendu: float) -> bool:
    """Le nombre ÉCRIT dans la réponse est-il un arrondi juste de l'attendu ?

    Tolérance : un demi-ulp à la précision du moins précis des deux — le LLM
    rédige « environ 63 % » pour 62.96, ou « 5,84 » pour 5.8433.

    Sauf si cet arrondi est si grossier qu'il ne discrimine plus rien : « 1 »
    est formellement un arrondi de 0.9681, mais avec ±0.5 — la moitié de la
    valeur — il ne prouve rien, et le « 1 » de « 1re classe » suffisait alors à
    valider un taux jamais calculé. Au-delà de 5 % de la valeur attendue, on
    exige donc la précision de la référence.

    Une tolérance RELATIVE partout serait l'erreur inverse : 0.5 % de 891 vaut
    ±4.5 et accepterait « 887 », une erreur de comptage prise pour un arrondi.
    """
    ulp = 0.5 * 10 ** (-min(decimales, _decimales_de(attendu)))
    if ulp > 0.05 * abs(attendu):
        ulp = 0.5 * 10 ** (-_decimales_de(attendu))
    return abs(produit - attendu) <= ulp + 1e-9


def _correspond_dans_un_tableau(produit: float, attendu: float) -> bool:
    """Un tableau porte des valeurs BRUTES : on compare à la précision de l'attendu.

    Indispensable contre les coïncidences : un tableau de 94 passagères contient
    ~1200 nombres, et la règle indulgente de la réponse (±0.5 pour un attendu à
    0 décimale) y ferait passer n'importe quel ``passenger_id`` valant 97 pour
    un taux de 96.81. Un tableau n'arrondit pas — 62.96296296 vaut toujours
    62.96 à sa précision, mais 97 ne vaut pas 96.81.
    """
    ulp = 0.5 * 10 ** (-_decimales_de(attendu))
    return abs(produit - attendu) <= ulp + 1e-9


def _valeur_presente(
    attendue: float | str,
    texte: str,
    nombres_reponse: list[tuple[float, int]],
    nombres_table: list[float],
) -> bool:
    if isinstance(attendue, str):
        return attendue.lower() in texte.lower()
    if any(_correspond_dans_la_reponse(n, d, attendue) for n, d in nombres_reponse):
        return True
    return any(_correspond_dans_un_tableau(n, attendue) for n in nombres_table)


def check_data(turn: Turn, data: dict[str, Any]) -> list[str]:
    """Les valeurs de référence sont-elles bien celles produites ?

    Une entrée peut être un tuple d'alternatives : un « taux » est aussi juste
    rendu en fraction (0.742) qu'en pourcentage (74.2), et le LLM choisit
    librement — l'invariant porte sur la valeur, pas sur cette convention.
    """
    if not turn.expect_data:
        return []
    texte, nombres_reponse, nombres_table = _haystack(data)
    manquantes = []
    for attendue in turn.expect_data:
        variantes = attendue if isinstance(attendue, tuple) else (attendue,)
        if any(_valeur_presente(v, texte, nombres_reponse, nombres_table) for v in variantes):
            continue
        libelle = " ou ".join(repr(v) for v in variantes)
        manquantes.append(f"valeur {libelle} absente ou fausse")
    return manquantes


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

    # -- DUR : les chiffres rendus sont-ils les bons ?
    hard.extend(check_data(turn, data))

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
