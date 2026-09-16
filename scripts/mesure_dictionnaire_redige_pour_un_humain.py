"""Un dictionnaire écrit pour une PERSONNE tient-il devant l'agent ?

C'est la question qui décide si le produit tient chez un client. Le dictionnaire
d'`exploitation` a dû être RÉÉCRIT pour que les quatre questions du piège nº 1
passent : le texte d'avant énonçait sa règle de comptage en tête et rangeait son
contre-cas dans un paragraphe de fin — impeccable pour un lecteur humain, et
trompeur pour le modèle, qui retient ce qui est mis en avant.

Un client n'écrira jamais son dictionnaire pour notre agent. Il l'écrira comme
celui d'avant. Ce runner mesure donc le texte d'AVANT, tel quel, contre les
quatre questions qui se disputent la colonne `sessions.statut` — deux où il ne
faut PAS filtrer, une où il faut filtrer, une classement où le filtre fausse les
comptes sans fausser les rangs.

    # le texte d'avant, avec l'en-tête en service
    git show 2160276:sources/demonstration/dictionnaires/exploitation.md > /tmp/avant.md
    uv run python scripts/mesure_dictionnaire_redige_pour_un_humain.py \\
        --dictionnaire /tmp/avant.md --essais 3

    # le même texte, avec une AUTRE formulation d'en-tête
    uv run python scripts/mesure_dictionnaire_redige_pour_un_humain.py \\
        --dictionnaire /tmp/avant.md --en-tete /tmp/en-tete-candidat.txt

Le verdict est mécanique et double : le CHIFFRE rendu, et le SQL lu dans la
trace. Les deux sont nécessaires — sur le classement, le modèle ne projette pas
toujours la colonne des comptes (cf. le sujet 3), et l'absence de chiffre ne dit
alors rien du filtre. Le SQL, lui, le dit toujours.

Rien n'est écrit sur le disque du dépôt : le dictionnaire de substitution est
posé sur l'objet du catalogue, en mémoire, le temps de la mesure.

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, Postgres
semé (``scripts/seed_catalogue_demonstration.py``), le serveur LLM joignable.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_surface_conversationnelle import nombres

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval import agent as agent_sql
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

SOURCE = "exploitation"


@dataclass(frozen=True)
class Question:
    """Une question, son oracle, et le chiffre FAUX que le filtre de trop rend.

    Le faux connu est aussi important que le juste : une réponse qui ne tombe ni
    sur l'un ni sur l'autre est un troisième cas — le modèle a fait autre chose
    — et le confondre avec l'échec attendu masquerait ce qui se passe vraiment.
    """

    cle: str
    question: str
    juste: tuple[float, ...]
    faux: tuple[float, ...]
    # Le filtre attendu dans le SQL : True = `statut` doit y être, False = il ne
    # doit pas y être.
    filtre_attendu: bool


QUESTIONS = (
    Question(
        cle="total",
        question="combien de sessions de recharge y a-t-il en tout ?",
        juste=(48000.0,),
        faux=(42281.0,),
        filtre_attendu=False,
    ),
    Question(
        cle="energie",
        question="quelle énergie totale, en kWh, a été délivrée sur l'année ?",
        juste=(1757519.23,),
        faux=(1730823.72,),
        filtre_attendu=False,
    ),
    Question(
        cle="stations",
        question=(
            "quelles sont les trois stations avec le plus de sessions ? donne leur code et leur nom"
        ),
        juste=(1015.0, 911.0, 872.0),
        faux=(907.0, 818.0, 759.0),
        filtre_attendu=False,
    ),
    Question(
        cle="abouties",
        question="combien de recharges ont réellement abouti ?",
        juste=(42281.0,),
        faux=(48000.0,),
        filtre_attendu=True,
    ),
)

# `statut` cité dans une clause qui RESTREINT. Un `SELECT ..., statut` ou un
# `GROUP BY statut` ne filtre rien : les compter comme un filtre condamnerait
# des requêtes justes.
FILTRE_STATUT = re.compile(r"\b(?:where|and|or|having)\b[^;]*?\bstatut\b", re.IGNORECASE | re.S)


def contient(valeurs: list[float], cibles: tuple[float, ...], tolerance: float = 0.5) -> bool:
    return all(any(abs(v - cible) <= tolerance for v in valeurs) for cible in cibles)


@dataclass
class Essai:
    cle: str
    tirage: int
    question: str
    reponse: str
    tableau: str
    sql: str
    filtre: bool
    verdict: str
    pourquoi: str
    valeurs: list[float] = field(default_factory=list)
    duree_ms: int = 0


def juger(question: Question, valeurs: list[float], filtre: bool) -> tuple[str, str]:
    """Le verdict, lu dans le chiffre ET dans le SQL.

    Le SQL tranche quand le chiffre se tait : « donne leur code et leur nom »
    obtient parfois une réponse sans les comptes, et une réponse muette n'est
    pas une réponse fausse.
    """
    if filtre != question.filtre_attendu:
        return "faux", ("filtre sur `statut` de trop" if filtre else "filtre sur `statut` manquant")
    if contient(valeurs, question.juste):
        return "juste", "chiffre et filtre conformes"
    if contient(valeurs, question.faux):
        return "faux", "le chiffre du filtre de trop, sans le filtre — incohérent"
    return "sans chiffre", "filtre conforme, mais l'oracle n'est pas cité"


def un_tirage(orchestrateur: Orchestrator, question: Question, tirage: int) -> Essai:
    fil = f"mesure-dico-humain-{question.cle}-{uuid.uuid4().hex[:8]}"
    depart = time.monotonic()
    reponse = orchestrateur.ask(question.question, conversation_id=fil, source_de_travail=SOURCE)
    duree = int((time.monotonic() - depart) * 1000)
    etape = next((s for s in reponse.trace if s.node == "retrieval"), None)
    sql = etape.detail if etape is not None else ""
    filtre = bool(FILTRE_STATUT.search(sql))
    # La réponse ET le tableau : le prompt de l'agent SQL lui INTERDIT de
    # recopier les lignes dans sa phrase (« le tableau est affiché séparément »).
    # Ne lire que la phrase ferait donc passer pour muet un tour qui a rendu ses
    # chiffres là où l'utilisateur les regarde.
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    valeurs = nombres(reponse.answer) + nombres(tableau)
    verdict, pourquoi = juger(question, valeurs, filtre)
    return Essai(
        cle=question.cle,
        tirage=tirage,
        question=question.question,
        reponse=reponse.answer,
        tableau=tableau,
        sql=sql,
        filtre=filtre,
        verdict=verdict,
        pourquoi=pourquoi,
        valeurs=valeurs,
        duree_ms=duree,
    )


def rapport(
    essais: list[Essai],
    reglages,
    dictionnaire: Path,
    en_tete: Path | None,
    rappel: Path | None,
) -> str:
    justes = sum(1 for e in essais if e.verdict == "juste")
    lignes = [
        "## Le dictionnaire écrit pour un humain, mis à l'épreuve",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        f"Dictionnaire : `{dictionnaire}`",
        f"En-tête : {'`' + str(en_tete) + '`' if en_tete else 'celui en service'}",
        f"Rappel après le dictionnaire : {'`' + str(rappel) + '`' if rappel else 'aucun'}",
        "",
        f"**{justes}/{len(essais)} tirages justes.**",
        "",
        "| question | tirage | filtre `statut` | valeurs lues | verdict | pourquoi |",
        "|---|---|---|---|---|---|",
    ]
    for e in essais:
        valeurs = ", ".join(f"{v:g}" for v in e.valeurs[:6]) or "—"
        lignes.append(
            f"| `{e.cle}` | {e.tirage} | {'oui' if e.filtre else 'non'} | {valeurs} "
            f"| **{e.verdict}** | {e.pourquoi} |"
        )
    lignes.append("")
    for question in QUESTIONS:
        lot = [e for e in essais if e.cle == question.cle]
        if lot:
            lignes.append(
                f"- `{question.cle}` : {sum(1 for e in lot if e.verdict == 'juste')}/{len(lot)}"
            )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--dictionnaire", type=Path, default=None)
    parseur.add_argument("--en-tete", type=Path, default=None)
    # Un texte collé APRÈS le dictionnaire, et non avant. Axe distinct de
    # l'en-tête, et il fallait le mesurer : l'hypothèse de tout ce sujet est que
    # le modèle retient ce qui est mis en avant, donc la POSITION de la consigne
    # est une variable au même titre que sa formulation.
    parseur.add_argument("--rappel", type=Path, default=None)
    parseur.add_argument("--essais", type=int, default=3)
    parseur.add_argument("--questions", nargs="*", default=None)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    source = next((s for s in catalogue.sources if s.name == SOURCE), None)
    if source is None:
        raise SystemExit(f"la source « {SOURCE} » n'est pas au catalogue : rien à mesurer")
    if args.dictionnaire is not None:
        # En mémoire, sur l'objet du catalogue : le fichier du dépôt n'est ni
        # lu ni écrit, et une mesure interrompue ne laisse rien derrière elle.
        source.dictionary = args.dictionnaire.resolve()
    if args.en_tete is not None:
        agent_sql.EN_TETE_SQL = args.en_tete.read_text(encoding="utf-8").strip()
    if args.rappel is not None:
        rappel = args.rappel.read_text(encoding="utf-8").strip()
        colle = agent_sql.bloc_de_prompt
        agent_sql.bloc_de_prompt = lambda injecte, en_tete: (
            f"{colle(injecte, en_tete)}\n\n{rappel}" if injecte.texte else ""
        )

    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Dictionnaire : {source.dictionary}")
    print(f"En-tête : {args.en_tete or 'celui en service'}\n")

    orchestrateur = Orchestrator(
        settings=reglages,
        model=build_model(reglages),
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    choisies = [q for q in QUESTIONS if not args.questions or q.cle in args.questions]
    essais: list[Essai] = []
    for question in choisies:
        for tirage in range(1, args.essais + 1):
            print(f"[{question.cle} {tirage}/{args.essais}] « {question.question} »")
            essai = un_tirage(orchestrateur, question, tirage)
            essais.append(essai)
            print(f"    → {essai.verdict} ({essai.pourquoi}) — {essai.duree_ms} ms")
            print(f"    sql : {' '.join(essai.sql.split())[:200]}")
            print(f"    réponse : {' '.join(essai.reponse.split())[:200]}\n")

    texte = rapport(essais, reglages, source.dictionary, args.en_tete, args.rappel)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps([e.__dict__ for e in essais], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
