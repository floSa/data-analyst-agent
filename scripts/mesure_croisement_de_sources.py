"""Une question qui croise DEUX sources déclarées reçoit-elle une réponse fondée ?

`Plan.source` porte UN nom. Une question qui en croise deux n'avait donc aucun
chemin : le pilote l'a mesuré le 2026-09-22 sur `e126e56`, deux formulations,
même issue — aucun outil appelé, et « Sur quelle source veux-tu travailler :
ventes, production, stocks, iris, titanic ? » servi faute de mieux. Le catalogue
métier avait pourtant été monté POUR ces questions-là : `code_produit` vit dans
`ventes`, dans `production` et dans `stocks`.

**Le périmètre est ce que le tour désigne, jamais le catalogue.** On ne mesure
pas ici la capacité de croiser n'importe quelle paire : on mesure qu'une
conversation travaille sur un périmètre cohérent — les sources que la question
nomme — et que rien d'autre ne lui est exposé. Monter plus que le tour ne
demande est le défaut que C51 a chiffré : deux fichiers montés quand un seul est
visé, le mauvais choisi 10 fois sur 10, un chiffre faux et PLAUSIBLE.

**Les chiffres attendus se vérifient de tête sur le catalogue** — 180 commandes,
140 ordres de fabrication, 8 vélos fabriqués, 12 produits au catalogue — et tous
sortent des oracles du semis, relus dans les données écrites.

**TROIS TÉMOINS, et ils comptent autant que le reste.** Une question sur une
seule source doit continuer de se jouer sur elle seule ; une question de sens
doit continuer d'être une question de sens ; et « ventes ou production ? », qui
nomme deux sources sans rien demander dessus, doit continuer de FAIRE CHOISIR.
Sans eux, on mesurerait qu'on sait croiser, pas qu'on n'a rien cassé pour y
arriver.

    uv run python scripts/mesure_croisement_de_sources.py --tirages 3
    uv run python scripts/mesure_croisement_de_sources.py --seulement produites-vs-vendues

Prérequis : ``DAA_CATALOG_PATH=sources/metier/catalogue.yaml``, le catalogue
semé (``scripts/seed_catalogue_metier.py``), Postgres joignable, le serveur LLM
en place.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur, nombres, porte_le_fait

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator


@dataclass(frozen=True)
class Question:
    """Une question du banc, et ce qui décide de son verdict."""

    cle: str
    message: str
    # La source LIÉE au fil quand la question est posée. "" = fil vierge, qui
    # est l'état du relevé du pilote : personne n'a encore rien choisi.
    fil: str = ""
    # Les chiffres que la réponse (ou le tableau) doit porter.
    nombres: tuple[float, ...] = ()
    # Le chiffre qu'elle ne doit PAS porter : celui qu'on obtient en tombant
    # dans le piège du dictionnaire. C'est lui qui distingue « a répondu juste »
    # de « a donné les deux chiffres et laissé choisir ».
    interdits: tuple[float, ...] = ()
    # Des FAITS, chacun une disjonction de tournures dont une seule suffit. Un
    # oracle qui exige UN mot refuse des réponses justes ; les deux erreurs ont
    # été payées sur ce dépôt, et la disjonction est ce qui les sépare.
    faits: tuple[tuple[str, ...], ...] = ()
    # Le tour doit avoir REGARDÉ les données — `retrieval` ou `analysis` dans la
    # trace. C'est le critère de « réponse fondée », et il a fallu le corriger :
    # la première rédaction exigeait un artefact `application/json`, que seul
    # le chemin SQL produit. Le chemin d'ANALYSE rend une figure et du texte, et
    # trois croisements qui avaient parfaitement abouti — dictionnaires
    # appliqués, chiffres justes — étaient comptés en échec pour n'avoir pas
    # rendu un tableau que leur chemin ne rend jamais. Un oracle qui exige la
    # FORME de la réponse mesure le chemin pris, pas la réponse.
    fonde: bool = True
    # Le tour doit au contraire s'arrêter et FAIRE CHOISIR : aucun tableau,
    # et les sources proposées. C'est le témoin qu'on refuse de croiser quand
    # rien n'est demandé sur les données.
    faire_choisir: bool = False
    attendu: str = ""
    montre: str = ""


# Les oracles, relus dans les trois sources le 2026-09-22 :
#   production, par vélo : VEL-01 689, VEL-02 484, VEL-03 470, VEL-04 727,
#                          VEL-05 492, VEL-06 534, VEL-07 461, VEL-08 556
#                          (4 413 unités, 140 ordres, 8 vélos)
#   vendu (statut <> 'ANN') : VEL-01 123, VEL-04 125 — BRUT : 141 et 131
#   CA par produit (ANN exclu) : VEL-07 323 700 € — BRUT : 341 130 €
#   inventaire : VEL-04 225
QUESTIONS: tuple[Question, ...] = (
    # ---- les quatre croisements de DEUX sources ---------------------------
    Question(
        cle="produites-vs-vendues",
        message="compare les quantités produites et les quantités vendues par produit",
        nombres=(689, 727, 123, 125),
        # Les quantités vendues sont une somme d'UNITÉS EXPÉDIÉES : le
        # dictionnaire de `ventes` pose `statut <> 'ANN'` pour celles-là. 141 et
        # 131 sont les mêmes deux produits sans le filtre.
        interdits=(141, 131),
        attendu="VEL-01 689 fabriqués / 123 vendus, VEL-04 727 / 125",
        montre="LE RELEVÉ DU PILOTE — la première des deux phrases sans réponse",
    ),
    Question(
        cle="vend-plus-quon-produit",
        message="est-ce qu'on vend plus que ce qu'on produit ?",
        # 4 413 unités fabriquées : 140 ordres sur 8 vélos, vérifiable de tête.
        #
        # **Le verdict NE SUFFIT PAS, et c'est mesuré.** La première rédaction
        # n'exigeait que la conclusion (« on produit plus ») : elle a compté
        # 3/3 une réponse qui annonçait « 20 557 vendues, 180 669 produites »
        # — une jointure qui duplique, deux chiffres faux d'un facteur 40, et
        # un verdict juste PAR ACCIDENT. Un oracle qui n'exige rien bénit des
        # réponses fausses. Le verdict reste demandé ; le chiffre le fonde.
        nombres=(4413,),
        faits=(
            (
                "on produit plus",
                "produit davantage",
                "production dépasse",
                "production est supérieure",
                "plus qu'on n'en vend",
                "plus que ce qu'on vend",
                "fabrique plus",
                "produites sont supérieures",
            ),
        ),
        interdits=(1180,),
        attendu="non : 4 413 unités fabriquées pour 1 078 vélos vendus",
        montre="LE RELEVÉ DU PILOTE — la seconde phrase, qui demande un verdict",
    ),
    Question(
        cle="ca-produit-vs-fabrique",
        message="compare le chiffre d'affaires par produit avec les quantités fabriquées",
        # 461 : ce que l'atelier a fabriqué de VEL-07. Le CA du produit n'est
        # PAS exigé nommément — la réponse peut le rendre par produit (323 700 €
        # pour VEL-07) ou en total (1 496 743 €), et les deux sont justes.
        # L'oracle porte donc sur ce qui décide : le croisement a-t-il eu lieu,
        # et le piège a-t-il été évité.
        nombres=(461,),
        # PIÈGE DE DICTIONNAIRE : un chiffre d'affaires est une somme d'argent,
        # donc `statut <> 'ANN'`. 341 130 est le CA du VEL-07 annulées comprises,
        # 1 636 093 le CA total sans le filtre.
        interdits=(341_130, 1_636_093),
        faits=(("annulée", "annulées", "annulation", "exclu les annul", "statut ANN"),),
        attendu="VEL-07 : 323 700 € pour 461 fabriqués, annulées exclues et DIT",
        montre="LE PIÈGE DU DICTIONNAIRE DANS UN CROISEMENT — et la réponse le dit",
    ),
    Question(
        cle="vel04-production-ventes",
        message="compare la production et les ventes du VEL-04",
        nombres=(727, 125),
        interdits=(131,),
        attendu="VEL-04 : 727 fabriqués, 125 vendus",
        montre="un croisement RESSERRÉ sur un seul produit",
    ),
    # ---- le croisement de TROIS sources (secondaire) ----------------------
    Question(
        cle="fabrique-vendu-stock",
        message=(
            "pour chaque vélo, compare ce qu'on a fabriqué, ce qu'on a vendu "
            "et ce qu'il reste en stock"
        ),
        nombres=(727, 125, 225),
        interdits=(131,),
        attendu="VEL-04 : 727 fabriqués, 125 vendus, 225 en stock",
        montre="TROIS sources d'un coup — rien n'est conçu pour ce cas",
    ),
    # ---- le périmètre qui S'ENRICHIT depuis un fil déjà lié ---------------
    Question(
        cle="produites-vs-vendues-fil-lie",
        message="compare les quantités produites et les quantités vendues par produit",
        fil="ventes",
        nombres=(689, 727, 123, 125),
        interdits=(141, 131),
        attendu="le même tableau, sur un fil déjà lié à `ventes`",
        montre="le périmètre S'AJOUTE à la source du fil au lieu de la remplacer",
    ),
    # ---- LES TROIS TÉMOINS -----------------------------------------------
    Question(
        cle="temoin-une-seule-source",
        message="Quel chiffre d'affaires avons-nous réalisé en 2025 ?",
        fil="ventes",
        nombres=(1_496_743,),
        interdits=(1_636_093,),
        attendu="1 496 743,00 € — sur `ventes` seule, rien d'autre monté",
        montre="TÉMOIN — une question mono-source se joue sur sa source seule",
    ),
    Question(
        cle="temoin-question-de-sens",
        message="Que signifie le statut ANN d'une commande ?",
        fil="ventes",
        fonde=False,
        faits=(("annulée", "annulées", "annulation", "annulé"),),
        attendu="une commande annulée avant expédition",
        montre="TÉMOIN — une question de sens reste une question de sens",
    ),
    Question(
        cle="temoin-faire-choisir",
        message="ventes ou production ?",
        fonde=False,
        faire_choisir=True,
        faits=(("ventes",), ("production",)),
        attendu="la question du choix, sans rien croiser",
        montre="TÉMOIN — deux sources nommées sans rien demander dessus",
    ),
)


@dataclass
class Releve:
    question: Question
    tirage: int
    reponse: str
    tableau: str
    noeuds: list[str]
    valeurs: list[float] = field(default_factory=list)
    verdict: str = ""
    pourquoi: str = ""
    appels_llm: int = 0
    duree_ms: int = 0


def juger(
    question: Question,
    reponse: ChatAnswer,
    valeurs: list[float],
    texte: str,
    tableau: str,
    noeuds: list[str],
) -> tuple[str, str]:
    """Le verdict, sur ce que l'utilisateur VOIT — la phrase ET le tableau.

    Le prompt de l'agent SQL lui interdit de recopier les lignes dans sa phrase :
    ne lire que la phrase ferait passer pour muet un tour qui a rendu ses
    chiffres.
    """
    if reponse.error:
        return "échec", f"erreur : {reponse.error}"
    if question.faire_choisir:
        # Ce témoin se juge d'abord sur ce qu'il n'a PAS fait : aucune donnée
        # regardée. Un tour qui croise pour répondre à « ventes ou production ? »
        # a rouvert le fouillis qu'on ferme, et il l'a rouvert en silence.
        touches = {"retrieval", "analysis"} & set(noeuds)
        if touches:
            return "échec", f"a interrogé une source ({', '.join(sorted(touches))})"
        if tableau.strip():
            return "échec", "a produit un tableau au lieu de faire choisir"
    if question.fonde and not ({"retrieval", "analysis"} & set(noeuds)):
        return "échec", f"aucune donnée regardée ({' → '.join(noeuds)})"
    # Tolérance de 0,5 : elle absorbe l'arrondi d'affichage sans jamais
    # confondre deux oracles — les deux chiffres de chaque piège sont séparés
    # par des ordres de grandeur, pas par une décimale.
    manquants = [c for c in question.nombres if not any(abs(v - c) <= 0.5 for v in valeurs)]
    if manquants:
        return "échec", f"chiffre(s) absent(s) : {', '.join(f'{c:g}' for c in manquants)}"
    presents = [c for c in question.interdits if any(abs(v - c) <= 0.5 for v in valeurs)]
    if presents:
        return "échec", f"chiffre du piège présent : {', '.join(f'{c:g}' for c in presents)}"
    absents = [f[0] for f in question.faits if not porte_le_fait(texte, f)]
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
        source_de_travail=question.fil,
    )
    duree = int((time.monotonic() - depart) * 1000)
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    noeuds = [s.node for s in reponse.trace]
    verdict, pourquoi = juger(question, reponse, valeurs, texte, tableau, noeuds)
    return Releve(
        question=question,
        tirage=tirage,
        reponse=reponse.answer,
        tableau=tableau,
        noeuds=noeuds,
        valeurs=valeurs,
        verdict=verdict,
        pourquoi=pourquoi,
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )


def rapport(releves: list[Releve], reglages) -> str:
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    cles = list(dict.fromkeys(r.question.cle for r in releves))
    tirages = len(releves) // max(len(cles), 1)
    lignes = [
        "## Une question qui croise deux sources, mesurée",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        f"Catalogue : `{reglages.catalog_path}`",
        "",
        f"**{conformes}/{len(releves)} tours conformes** "
        f"({len(cles)} questions, {tirages} tirages chacune), "
        f"{sum(r.appels_llm for r in releves)} appels LLM.",
        "",
        "| question | fil | message | ce qu'elle montre | attendu | score | ce qui a décidé |",
        "|---|---|---|---|---|---|---|",
    ]
    for cle in cles:
        lot = [r for r in releves if r.question.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "conforme")
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        q = lot[0].question
        lignes.append(
            f"| `{cle}` | `{q.fil or '(vierge)'}` | {' '.join(q.message.split())} | {q.montre} "
            f"| {q.attendu} | **{bons}/{len(lot)}** | {' ; '.join(echecs) if echecs else '—'} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description="Le croisement de deux sources déclarées.")
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--seulement", nargs="*", default=None)
    parseur.add_argument("--tirages", type=int, default=1)
    args = parseur.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}")
    print(f"Sources : {', '.join(s.name for s in catalogue.sources)}\n")

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
            # Un fil NEUF par question ET par tirage : ces questions sont
            # indépendantes, et les enchaîner mesurerait la mémoire de
            # conversation au lieu du croisement.
            releve = poser(
                orchestrateur, question, f"croisement-{suffixe}-{tirage}-{question.cle}", tirage
            )
            releves.append(releve)
            print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
            print(f"    nœuds : {' → '.join(releve.noeuds)}")
            print(f"    réponse : {' '.join(releve.reponse.split())[:240]}\n", flush=True)

    texte = rapport(releves, reglages)
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
