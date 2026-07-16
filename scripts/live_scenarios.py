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

Prérequis : l'API tourne (uvicorn), Ollama a le modèle, la base est construite
(scripts/load_maxizoo_duckdb.py), l'image sandbox est construite.

    uv run python scripts/live_scenarios.py            # tout
    uv run python scripts/live_scenarios.py --base-url http://localhost:8000
    uv run python scripts/live_scenarios.py --only maxizoo-decouverte maxizoo-pieges
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
    # on découvre la base, on creuse sur ce qui vient d'être répondu, on demande
    # une figure, puis on prédit — en commençant par demander à l'agent CE QU'IL
    # LUI FAUT et dans quel format.
    #
    # Les `expect_data` sont la VÉRITÉ TERRAIN. La plupart viennent des 13
    # questions de référence livrées AVEC la base (`questions_reference.md` du
    # dépôt d'export) : leurs réponses ont été obtenues en exécutant le SQL sur
    # les fichiers livrés, hors de tout agent. Le reste est calculé de la même
    # façon (SQL direct, ou predict sur le modèle). Sans elles, une réponse bien
    # tournée sur des chiffres faux passe pour un succès.
    #
    # Plusieurs tours visent un PIÈGE du dictionnaire — ce sont ceux qui
    # discriminent : un agent qui ne l'a pas lu répond quelque chose de
    # plausible, et de faux.
    Scenario(
        "maxizoo-decouverte",
        "Maxizoo : description → CA 2025 → top magasins (piège e-commerce) → figure → part online",
        [
            Turn(
                "peux-tu me décrire la base : quelles tables, et combien de lignes de ventes ?",
                expect_data=["sales_daily", "stores", 1363726],
            ),
            Turn(
                "quel est le chiffre d'affaires total de 2025 ?",  # Q1
                capability="query",
                expect_data=[10186669.50],
            ),
            Turn(
                # Q2 — LE piège n°1 : « Canal Online » doit sortir premier. Un
                # agent qui répond « Paris » a oublié que le e-commerce est une
                # ligne de `stores`.
                "quels sont les 5 magasins qui réalisent le plus de CA en 2025 ?",
                capability="query",
                artifact="table",
                expect_data=["Canal Online", 2064988.99, 1041046.77],
            ),
            Turn(
                "fais-moi un diagramme en barres de ces CA",
                capability="analyze",
                nodes=["plan", "analysis", "synthesize"],
                artifact="image",
            ),
            Turn(
                "la part du e-commerce progresse-t-elle d'une année sur l'autre ?",  # Q4
                capability="query",
                artifact="table",
                expect_data=[(13.34, 0.1334), (20.27, 0.2027)],
            ),
        ],
    ),
    Scenario(
        "maxizoo-univers",
        "Maxizoo : CA par univers → figure → top SKU → jour de la semaine → figure",
        [
            Turn(
                "comment se répartit le CA 2025 par univers produit ?",  # Q3
                capability="query",
                artifact="table",
                expect_data=["Chat", 3790391.44, (37.2, 0.372), 3620136.70],
            ),
            Turn(
                "fais-en un diagramme en barres",
                capability="analyze",
                artifact="image",
            ),
            Turn(
                "quels sont les 10 SKU les plus vendus en CA en 2025 ?",  # Q6
                capability="query",
                artifact="table",
                expect_data=["SKU001", 1438611.18, 1308100.18],
            ),
            Turn(
                # Q7 — deux signaux opposés : samedi en magasin, dimanche en
                # ligne. Un agent qui agrège les deux canaux les écrase.
                "quel jour de la semaine vend le mieux, en magasin et en ligne ?",
                capability="query",
                artifact="table",
                expect_data=[1820157, 328192],
            ),
            Turn(
                "montre-moi ça en barres groupées",
                capability="analyze",
                artifact="image",
            ),
        ],
    ),
    Scenario(
        "maxizoo-pieges",
        "Maxizoo : panier article (grains) → ruptures (censure) → cold start → cohérence",
        [
            Turn(
                # Q5 — piège de grain : il faut agréger sales_daily au grain
                # magasin x jour AVANT de joindre traffic_daily, sinon
                # nb_tickets est dupliqué 60 fois et le panier divisé par 60.
                "quel est le panier article moyen par type de magasin en 2025 ?",
                capability="query",
                artifact="table",
                expect_data=[2.76, 2.37, 2.21, 2.06],
            ),
            Turn(
                # Q8 — la censure : ce n'est PAS du CA perdu. On vérifie les
                # chiffres ; le sens, lui, se lit dans la réponse.
                "quelle est l'ampleur des ruptures de stock en 2025 ?",
                capability="query",
                expect_data=[3041, (1.08, 0.0108), 42066.45],
            ),
            Turn(
                # Q9 — absence de ligne ≠ zéro : les 4 SKU en cold start.
                "quels produits ont été lancés en cours d'historique, et quand "
                "ont-ils commencé à vendre ?",
                capability="query",
                artifact="table",
                expect_data=["SKU003", "2023-03-15", "SKU049", "2025-02-15"],
            ),
            Turn(
                "le CA horaire recompose-t-il bien le CA journalier ?",  # Q13
                capability="query",
                expect_data=[0.05],
            ),
            Turn(
                "fais-moi un histogramme des quantités vendues par jour",
                capability="analyze",
                artifact="image",
            ),
        ],
    ),
    Scenario(
        "maxizoo-meteo",
        "Maxizoo : effet météo (anomalie vs absolu) → heure de pointe → figures",
        [
            Turn(
                # Q11 — piège : raisonner sur temp_anomaly, pas temp_mean_c,
                # sinon on compare l'été à l'hiver et on mesure la saisonnalité.
                "la chaleur fait-elle baisser la fréquentation des magasins physiques en été ?",
                capability="query",
                artifact="table",
                expect_data=[51.2, 54.5],
            ),
            Turn(
                "quelle est l'heure de pointe de chaque magasin physique en 2025 ?",  # Q12
                capability="query",
                artifact="table",
                expect_data=[11, 18, "Paris"],
            ),
            Turn(
                "fais-moi un diagramme en barres de ces heures de pointe",
                capability="analyze",
                artifact="image",
            ),
            Turn(
                "combien de magasins physiques y a-t-il, et combien de SKU au catalogue ?",
                capability="query",
                expect_data=[12, 60],
            ),
            Turn(
                "sur quelle période s'étend l'historique des ventes ?",
                capability="query",
                expect_data=["2021-07-01", "2026-06-30"],
            ),
        ],
    ),
    Scenario(
        "maxizoo-prediction",
        "Maxizoo : quel modèle ? → attributs et format → relance → prédiction → ajustement",
        [
            Turn(
                "je voudrais faire une prévision de ventes : de quels attributs "
                "as-tu besoin, et sous quel format ?",
                expect_data=["store_type", "commodity_group", "discount_rate"],
            ),
            Turn(
                "prédis les ventes de croquettes chien en grand magasin",
                capability="predict",
                clarify=True,
                answer_regex=[r"base_price|prix"],
            ),
            Turn(
                # La valeur de référence est la sortie du VRAI modèle sur ces
                # features (calculée hors agent), pas une estimation à vue. Elle
                # est stable : le notebook d'entraînement est reproductible
                # (l'ORDER BY de sa requête fige le découpage train/test), donc
                # un réentraînement rend le même artefact et le même chiffre.
                "marque nationale à 49.90, un samedi (day_of_week=5) de novembre "
                "(month=11), sans promo, température de saison",
                capability="predict",
                nodes=["plan", "inference", "synthesize"],
                expect_data=[3.1374],
            ),
            Turn(
                "et si le produit était en promo à -30 % (campagne produits) ?",
                capability="predict",
                expect_data=[4.6079],
            ),
            Turn(
                "donne-moi 5 SKU avec leur univers, type de marque et prix catalogue",
                capability="query",
                artifact="table",
                expect_data=["SKU001"],
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
