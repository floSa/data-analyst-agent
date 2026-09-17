"""Les douze questions de démonstration du catalogue métier, mesurées.

Le catalogue de `sources/metier/` est écrit pour être MONTRÉ : volumes qui
tiennent dans la tête, codes qu'on cite de mémoire, et trois pièges de
modélisation assumés. Rien de tout cela ne vaut si les douze questions qu'on
pose devant un prospect ne passent pas. Ce runner est ce qui autorise à les
écrire dans `docs/sources-metier.md` : une démonstration qu'on rejoue à la main
d'un chantier à l'autre ne se compare qu'à ce dont on se souvient.

**Les douze questions couvrent, par construction :** au moins une par source
(cinq sources), une jointure sur trois tables, deux graphiques, trois questions
qui tombent dans un piège si le dictionnaire n'est pas lu, et une prédiction.

**Chaque tour est jugé sur ce que l'utilisateur VOIT** — la phrase de réponse ET
le tableau d'artefacts. Le prompt de l'agent SQL lui interdit de recopier les
lignes dans sa phrase : ne lire que la phrase ferait passer pour muet un tour
qui a rendu ses chiffres.

**Les trois questions à piège portent un `interdit`**, et c'est le coeur du
banc. Il ne suffit pas que la bonne réponse soit là : il faut que la MAUVAISE
n'y soit pas. Sans l'interdit, une réponse qui donne les deux chiffres — « le
chiffre d'affaires brut est 1 636 093 €, dont 1 496 743 € facturés » — passerait
pour juste alors qu'elle laisse l'utilisateur choisir.

    uv run python scripts/mesure_questions_metier.py
    uv run python scripts/mesure_questions_metier.py --tirages 3
    uv run python scripts/mesure_questions_metier.py --seulement ca-2025 arrets-duree

Prérequis : ``DAA_CATALOG_PATH=sources/metier/catalogue.yaml``, le catalogue
semé (``scripts/seed_catalogue_metier.py``), Postgres joignable, le serveur LLM
en place.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur, nombres

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator


@dataclass(frozen=True)
class Question:
    """Une question de démonstration, et ce qui décide de son verdict."""

    cle: str
    source: str
    message: str
    # Les chiffres que la réponse (ou le tableau) doit porter.
    nombres: tuple[float, ...] = ()
    # Le chiffre qu'elle ne doit PAS porter : celui qu'on obtient en tombant
    # dans le piège. C'est lui qui distingue « a répondu juste » de « a donné
    # les deux chiffres et laissé choisir ».
    interdits: tuple[float, ...] = ()
    # Des FAITS, chacun une disjonction de tournures dont une seule suffit.
    # Desserré exprès par rapport à une sous-chaîne exacte : un oracle n'a pas à
    # départager deux façons d'écrire la même chose.
    faits: tuple[tuple[str, ...], ...] = ()
    # Le tour doit produire une figure — un artefact `image/png`.
    graphique: bool = False
    attendu: str = ""
    # Ce que le tour démontre, pour la colonne « pourquoi cette question » du
    # document. Écrit ici et pas dans le document : les deux dérivent sinon.
    montre: str = ""


# Toutes les valeurs ci-dessous sortent des ORACLES du semis, relus dans les
# données écrites (cf. la fin de `scripts/seed_catalogue_metier.py`). Aucune
# n'est recopiée d'un paramètre de tirage.
QUESTIONS: tuple[Question, ...] = (
    # ---- ventes -----------------------------------------------------------
    Question(
        cle="commandes-2025",
        source="ventes",
        message="Combien de commandes avons-nous reçues en 2025 ?",
        nombres=(180,),
        # 164 est le compte SANS les annulées : le filtre posé alors que rien
        # ne le demande. Le dictionnaire dit que le comptage ne se filtre pas.
        interdits=(164,),
        attendu="180 commandes",
        montre="le comptage qui ne se filtre PAS — l'autre moitié du piège nº 1",
    ),
    Question(
        cle="ca-2025",
        source="ventes",
        message="Quel chiffre d'affaires avons-nous réalisé en 2025 ?",
        nombres=(1_496_743,),
        interdits=(1_636_093,),
        attendu="1 496 743,00 €",
        montre="PIÈGE Nº 1 — une somme d'argent exclut les commandes annulées",
    ),
    Question(
        cle="produit-le-plus-vendu",
        source="ventes",
        message=(
            "Quel produit s'est le plus vendu en nombre d'unités en 2025 ? "
            "Donne son code et le nombre d'unités."
        ),
        nombres=(264,),
        faits=(("ACC-03",),),
        attendu="ACC-03, 264 unités",
        montre="JOINTURE SUR TROIS TABLES — lignes_commande, produits, commandes",
    ),
    Question(
        cle="meilleur-client",
        source="ventes",
        message="Quel est notre meilleur client en chiffre d'affaires en 2025 ?",
        nombres=(170_149,),
        faits=(("Bordeaux", "Vélocité"),),
        attendu="Vélocité Bordeaux, 170 149,00 €",
        montre="un classement en euros, donc filtré comme une somme d'argent",
    ),
    Question(
        cle="ca-par-canal",
        source="ventes",
        message="Fais-moi un graphique du chiffre d'affaires 2025 par canal de vente.",
        graphique=True,
        # Les trois chiffres sont exigés, et pas seulement la figure. Au premier
        # tirage, ce tour rendait une figure et les chiffres 2 630 871 /
        # 1 017 293 / 949 647 — la jointure vers `lignes_commande` duplique
        # `montant_total_eur` autant de fois que la commande a de lignes. Un
        # oracle qui n'exigeait que la figure comptait ce tour comme juste :
        # il mesurait qu'un graphique EXISTE, pas qu'il dise vrai. Le
        # dictionnaire porte désormais la règle (piège nº 2), et l'oracle les
        # chiffres.
        nombres=(862_229, 331_499, 303_015),
        interdits=(2_630_871, 1_017_293, 949_647),
        attendu="une figure : magasin 862 229 €, en ligne 331 499 €, grossiste 303 015 €",
        montre="GRAPHIQUE sur une source Postgres",
    ),
    # ---- production -------------------------------------------------------
    Question(
        cle="arrets-duree",
        source="production",
        message="Quelle est la durée moyenne d'un arrêt machine ?",
        nombres=(202.10,),
        interdits=(175.99,),
        attendu="202,10 minutes",
        montre="PIÈGE Nº 2 — la sentinelle -1 sort de la moyenne, le 0 y reste",
    ),
    Question(
        cle="machine-la-plus-arretee",
        source="production",
        message="Quelle machine a connu le plus d'arrêts en 2025 ?",
        nombres=(16,),
        faits=(("M-009",),),
        attendu="M-009, 16 arrêts",
        montre="un classement sur une jointure DuckDB déclarée",
    ),
    Question(
        cle="produits-fabriques",
        source="production",
        message="Combien de produits différents fabriquons-nous ?",
        nombres=(8,),
        interdits=(12,),
        attendu="8 produits",
        montre="LE RECOUPEMENT — 8 ici, 12 dans `ventes`, et les deux sont justes",
    ),
    # ---- stocks -----------------------------------------------------------
    Question(
        cle="unites-sorties",
        source="stocks",
        message="Combien d'unités sont sorties des entrepôts en 2025 ?",
        nombres=(2279,),
        interdits=(4293,),
        attendu="2 279 unités",
        montre="PIÈGE Nº 3 — la somme brute d'une colonne signée est la variation nette",
    ),
    Question(
        cle="mouvements-par-entrepot",
        source="stocks",
        message="Fais-moi un graphique du nombre de mouvements par entrepôt.",
        graphique=True,
        nombres=(170, 168, 142),
        attendu="une figure : E-LIL 170, E-LYO 168, E-NAN 142",
        montre="GRAPHIQUE sur un classeur Excel à trois feuilles",
    ),
    # ---- iris / titanic ---------------------------------------------------
    Question(
        cle="iris-especes",
        source="iris",
        message="Combien de fleurs y a-t-il par espèce ?",
        nombres=(50,),
        faits=(("setosa",), ("versicolor",), ("virginica",)),
        attendu="50 / 50 / 50",
        montre="une source de référence, servie comme FICHIER",
    ),
    Question(
        cle="titanic-prediction",
        source="titanic",
        # « Voyageant seul » ne dit pas `parch`, et l'agent l'a demandé plutôt
        # que de le deviner — ce qui est le comportement qu'on veut. La question
        # est donc corrigée, pas le produit : elle nomme les sept features que
        # le modèle attend. Une question de démonstration qui laisse une feature
        # implicite mesure la devinette, pas la prédiction.
        message=(
            "Un homme de 30 ans en 3e classe, sans frère, soeur ni conjoint à bord "
            "(sibsp 0) et sans parent ni enfant (parch 0), billet à 8 livres, "
            "embarqué à Southampton : aurait-il survécu ?"
        ),
        faits=(("n'a pas survécu", "pas survécu", "non survivant", "ne survit pas"),),
        attendu="n'a pas survécu",
        montre="PRÉDICTION — le modèle du registre, alimenté par `features`",
    ),
)


@dataclass
class Releve:
    question: Question
    tirage: int
    reponse: str
    tableau: str
    noeuds: list[str]
    figures: int
    valeurs: list[float]
    verdict: str
    pourquoi: str
    appels_llm: int
    duree_ms: int


def juger(
    question: Question, reponse: ChatAnswer, valeurs: list[float], texte: str, figures: int
) -> tuple[str, str]:
    """Le verdict : l'erreur d'abord, puis les chiffres, l'interdit, la figure, les faits.

    L'ordre n'est pas indifférent. L'INTERDIT est vérifié juste après les
    chiffres attendus, parce qu'un tour qui donne les deux chiffres est le cas
    qu'on veut voir échouer : il a « la bonne réponse dedans » et ne répond pas.
    """
    if reponse.error:
        return "échec", f"erreur : {reponse.error}"
    # Tolérance de 0,5 : elle absorbe l'arrondi d'affichage (« 202,1 » pour
    # 202,10) sans jamais confondre deux oracles — les deux chiffres de chaque
    # piège sont séparés par des ordres de grandeur, pas par une décimale.
    manquants = [c for c in question.nombres if not any(abs(v - c) <= 0.5 for v in valeurs)]
    if manquants:
        return "échec", f"chiffre(s) absent(s) : {', '.join(f'{c:g}' for c in manquants)}"
    presents = [c for c in question.interdits if any(abs(v - c) <= 0.5 for v in valeurs)]
    if presents:
        return "échec", f"chiffre du piège présent : {', '.join(f'{c:g}' for c in presents)}"
    if question.graphique and figures == 0:
        return "échec", "aucune figure produite"
    plat = texte.lower()
    absents = [f[0] for f in question.faits if not any(t.lower() in plat for t in f)]
    if absents:
        return "échec", f"fait(s) absent(s) : {', '.join(absents)}"
    return "conforme", question.attendu


def poser(orchestrateur: Orchestrator, question: Question, fil: str, tirage: int) -> Releve:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        question.message,
        conversation_id=fil,
        # Toujours une CHAÎNE, jamais None : c'est ce que l'API passe, et le
        # court-circuit de choix de source en dépend.
        source_de_travail=question.source,
    )
    duree = int((time.monotonic() - depart) * 1000)
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    figures = len([a for a in reponse.artifacts if a.mime == "image/png"])
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    verdict, pourquoi = juger(question, reponse, valeurs, texte, figures)
    return Releve(
        question=question,
        tirage=tirage,
        reponse=reponse.answer,
        tableau=tableau,
        noeuds=[s.node for s in reponse.trace],
        figures=figures,
        valeurs=valeurs,
        verdict=verdict,
        pourquoi=pourquoi,
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )


def rapport(releves: list[Releve], reglages, sans_dictionnaire: bool = False) -> str:
    """Le tableau, agrégé par question — une ligne par question, tous tirages confondus."""
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    cles = list(dict.fromkeys(r.question.cle for r in releves))
    tirages = len(releves) // max(len(cles), 1)
    lignes = [
        "## Les douze questions de démonstration, mesurées",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        f"Catalogue : `{reglages.catalog_path}`",
        f"Dictionnaires : {'RETIRÉS (témoin)' if sans_dictionnaire else 'en service'}",
        "",
        f"**{conformes}/{len(releves)} tours conformes** "
        f"({len(cles)} questions, {tirages} tirages chacune), "
        f"{sum(r.appels_llm for r in releves)} appels LLM.",
        "",
        "| question | source | message | ce qu'elle montre | attendu | score | ce qui a décidé |",
        "|---|---|---|---|---|---|---|",
    ]
    for cle in cles:
        lot = [r for r in releves if r.question.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "conforme")
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        pourquoi = " ; ".join(echecs) if echecs else "—"
        q = lot[0].question
        lignes.append(
            f"| `{cle}` | `{q.source}` | {' '.join(q.message.split())} | {q.montre} "
            f"| {q.attendu} | **{bons}/{len(lot)}** | {pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description="Les douze questions du catalogue métier.")
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--seulement", nargs="*", default=None)
    parseur.add_argument("--tirages", type=int, default=1)
    parseur.add_argument(
        "--sans-dictionnaire",
        action="store_true",
        help=(
            "retire les dictionnaires des sources, EN MÉMOIRE, le temps de la mesure. "
            "C'est le témoin des trois pièges : sans lui, on affirme qu'un dictionnaire "
            "sert sans jamais montrer ce qui se passe quand il manque."
        ),
    )
    args = parseur.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    if args.sans_dictionnaire:
        # En mémoire, sur les objets du catalogue : le dépôt n'est ni lu ni
        # écrit pour cette colonne, et une mesure interrompue ne laisse rien
        # derrière elle. Même procédé que
        # `mesure_dictionnaire_redige_pour_un_humain.py`, pour la même raison.
        for source in catalogue.sources:
            source.dictionary = None
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}")
    print(f"Dictionnaires : {'RETIRÉS (témoin)' if args.sans_dictionnaire else 'en service'}\n")

    orchestrateur = Orchestrator(
        settings=reglages,
        model=ModeleCompteur(build_model(reglages)),
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    suffixe = uuid.uuid4().hex[:8]
    questions = [q for q in QUESTIONS if not args.seulement or q.cle in args.seulement]
    total = len(questions) * args.tirages
    releves: list[Releve] = []
    numero = 0
    for tirage in range(1, args.tirages + 1):
        for question in questions:
            numero += 1
            print(f"[{numero}/{total}] {question.cle}·{tirage} — « {question.message} »")
            # Un fil NEUF par question ET par tirage : ces douze questions sont
            # indépendantes, et les enchaîner dans un même fil mesurerait la
            # mémoire de conversation au lieu de la question.
            releve = poser(
                orchestrateur, question, f"metier-{suffixe}-{tirage}-{question.cle}", tirage
            )
            releves.append(releve)
            print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
            print(f"    réponse : {' '.join(releve.reponse.split())[:240]}\n", flush=True)

    texte = rapport(releves, reglages, args.sans_dictionnaire)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **{k: v for k, v in r.__dict__.items() if k != "question"},
                        "question": r.question.cle,
                        "message": r.question.message,
                    }
                    for r in releves
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
