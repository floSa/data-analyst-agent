"""Un chiffre calculé sur une TRANCHE sort-il en se disant chiffre de la source ?

``analysis_table_max_rows`` matérialise chaque table SQL en CSV, coupée à
10 000 lignes, et c'est le seul endroit du socle qui livre une donnée
incomplète sans que la donnée le dise : un ``SELECT *`` coupé rend un CSV
parfaitement lisible où rien ne signale les lignes manquantes. Le code engendré
y compte, et le compte est celui de l'échantillon.

Le défaut a été vu par la dette `H` sur une seule phrase (`N1`) et rangé sous
« le routage n'atteint pas l'agent système ». Le routage est la CAUSE de cette
phrase-là ; il n'est pas le défaut. Le défaut est une propriété du nœud
d'analyse, et il concerne **toute** question qui compte sur une source plus
grande que la tranche — quel que soit le chemin qui l'y a menée.

D'où ce runner, et ses DEUX volets — le second n'était pas prévu, il a été
ajouté parce que le premier a rendu un résultat qu'on n'attendait pas.

- **volet `comptage`** : huit questions de comptage ou de proportion, sur les
  deux seules sources du catalogue qui dépassent la tranche (`telemetrie`,
  547 200 relevés ; `exploitation`, 48 000 sessions), formulées comme un
  utilisateur les pose et sans jamais nommer leur source — elle est liée au
  fil. C'est le volet demandé, et il mesure le chemin ORDINAIRE d'un comptage.
- **volet `rejeu`** : deux conversations à deux tours. Le premier demande une
  figure, ce qui fait répondre le nœud d'analyse — donc sur la tranche ; le
  second demande de la retoucher, ce qui fait répondre le nœud de RAPPEL. Le
  verdict porte sur le SECOND tour. Ce volet existe parce que c'est là que la
  propriété se rompt, et le premier volet ne pouvait pas le voir.

    uv run python scripts/mesure_tranche_ou_source.py --tirages 3
    uv run python scripts/mesure_tranche_ou_source.py --volets rejeu
    uv run python scripts/mesure_tranche_ou_source.py --questions T1 E2

LE CRITÈRE, nommé avant la mesure
=================================

Le critère n'est **pas** « le bon chiffre ». Un chiffre d'échantillon annoncé
comme tel est une réponse honnête ; c'est le chiffre d'échantillon **muet** qui
est faux, parce qu'il se lit comme un chiffre de la source. Quatre verdicts, et
un seul échec :

``source``
    le chiffre rendu est celui de la source entière. Rien à qualifier.
``tranche qualifiée``
    le chiffre rendu est celui de la tranche, et le texte rendu à
    l'utilisateur dit que le compte porte sur autre chose que la source.
    ACCEPTABLE, et c'est écrit tel quel dans la commande de ce chantier.
``tranche muette``
    le chiffre rendu est celui de la tranche et rien ne le dit. **SEUL ÉCHEC.**
``sans chiffre``
    ni l'un ni l'autre — tour perdu, ou réponse sans aucune grandeur. Compté à
    part : ce n'est pas un succès.

Ce qui vaut QUALIFICATION est un FAIT, pas un mot — la leçon des dettes `E` et
`G`. Le fait « ce compte ne porte pas sur la source » se porte de deux façons,
et une seule suffit :

1. **la taille de la tranche est écrite à côté du chiffre** (« sur les 10 000
   relevés analysés, 290 ») — c'est une vérification NUMÉRIQUE, pas lexicale,
   et elle ne dépend d'aucune tournure ;
2. **la réponse dit l'échantillon** — une disjonction de tournures, énumérée
   dans ``TOURNURES_DE_TRANCHE`` et donc montrée.

Ce que l'oracle laisse passer, dit franchement : une réponse qui écrirait
10 000 pour une tout autre raison passerait la branche 1, et une réponse qui
dirait « échantillon » en rendant quand même le chiffre comme s'il était celui
de la source passerait la branche 2. Aucune des deux ne vérifie que
l'utilisateur a COMPRIS ; elles vérifient que l'information est SORTIE.

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, le
catalogue semé, Postgres joignable, le serveur LLM, et Docker pour le bac à
sable.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog, open_source
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

RACINE = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Question:
    """Une question de comptage, et les deux vérités contre lesquelles la juger.

    ``sql`` rend UNE valeur, et il est joué deux fois : sur la table entière et
    sur ``SELECT * ... LIMIT <plafond>``, qui est exactement ce que le nœud
    d'analyse matérialise. Les deux assiettes ne sont pas une coquetterie : sans
    elles on ne distingue pas un chiffre faux d'un chiffre d'échantillon, et
    c'est précisément la distinction que ce runner existe pour faire.
    """

    cle: str
    source: str
    table: str
    message: str
    sql: str
    # Une proportion se lit à 0,03 point près : 3,01 et 2,90 ne sont pas
    # le même nombre, et aucune des deux n'est à 0,03 de l'autre.
    pourcentage: bool = False
    volet: str = "comptage"
    # Volet `rejeu` seulement : le tour qui RETOUCHE la figure du premier. Le
    # verdict porte sur sa réponse, jamais sur celle du tour d'avant — celui-là
    # n'est là que pour qu'il y ait quelque chose à rejouer.
    retouche: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question(
        cle="T1",
        source="telemetrie",
        table="releves_puissance",
        message="combien de relevés sont à -1 sur la puissance ?",
        sql="count(*) FILTER (WHERE puissance_kw = -1)",
    ),
    Question(
        cle="T2",
        source="telemetrie",
        table="releves_puissance",
        message="les compteurs muets, ça représente quelle proportion des relevés ?",
        sql="100.0 * count(*) FILTER (WHERE puissance_kw = -1) / count(*)",
        pourcentage=True,
    ),
    Question(
        cle="T3",
        source="telemetrie",
        table="releves_puissance",
        message="au total, j'ai combien de relevés de puissance ?",
        sql="count(*)",
    ),
    Question(
        cle="T4",
        source="telemetrie",
        table="releves_puissance",
        message="combien de fois une borne a répondu avec une puissance à zéro ?",
        sql="count(*) FILTER (WHERE puissance_kw = 0)",
    ),
    Question(
        cle="E1",
        source="exploitation",
        table="sessions",
        message="en tout, ça fait combien de sessions ?",
        sql="count(*)",
    ),
    Question(
        cle="E2",
        source="exploitation",
        table="sessions",
        message="combien de sessions ont le statut T ?",
        sql="count(*) FILTER (WHERE statut = 'T')",
    ),
    Question(
        cle="E3",
        source="exploitation",
        table="sessions",
        message="quelle part des sessions a été interrompue ?",
        sql="100.0 * count(*) FILTER (WHERE statut = 'I') / count(*)",
        pourcentage=True,
    ),
    Question(
        cle="E4",
        source="exploitation",
        table="sessions",
        message="combien de sessions sont tombées en erreur ?",
        sql="count(*) FILTER (WHERE statut = 'E')",
    ),
)

QUESTIONS += (
    Question(
        cle="R1",
        volet="rejeu",
        source="telemetrie",
        table="releves_puissance",
        message="fais-moi un camembert des relevés selon qu'ils sont à -1, à 0 ou positifs",
        retouche="reprends ce graphique et mets-le en bleu",
        sql="count(*) FILTER (WHERE puissance_kw = -1)",
    ),
    Question(
        cle="R2",
        volet="rejeu",
        source="exploitation",
        table="sessions",
        message="fais-moi un graphique des sessions par statut",
        retouche="reprends ce graphique et mets-le en bleu",
        sql="count(*) FILTER (WHERE statut = 'T')",
    ),
)

VOLETS = ("comptage", "rejeu")

PAR_CLE = {q.cle: q for q in QUESTIONS}

# --- ce qui vaut qualification (branche 2 du critère) ------------------------

# La disjonction est écrite ici, en clair, parce qu'un oracle desserré sans
# être montré est un chiffre qu'on s'offre (dette E). Aucune de ces tournures
# n'est exigée : une seule suffit, et la branche NUMÉRIQUE se passe des trois.
TOURNURES_DE_TRANCHE = (
    "tronqu",  # « données tronquées », « la table est tronquée »
    "echantillon",  # « sur un échantillon »
    "extrait de",
    "premieres lignes",
    "premiers releves",
    "pas la table entiere",
    "pas la source entiere",
    "pas l integralite",
    "sous ensemble",
    "partiel",
    "limite a",
    "plafonn",
)


def _replie(texte: str) -> str:
    """Minuscules, sans accents — pour comparer des sens, pas des typographies."""
    import unicodedata

    sans = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in sans if not unicodedata.combining(c))


# Un nombre écrit à la française (16 447, 16 447, 2,90) ou à l'anglaise
# (16,447 / 2.90). Les séparateurs de milliers sont recollés AVANT la lecture :
# « 547 200 » est un nombre, pas deux.
_MILLIERS = re.compile("(?<=\\d)[\u00a0\u202f\u2009 ,](?=\\d{3}\\b)")
_NOMBRE = re.compile(r"\d+(?:\.\d+)?")


def nombres(texte: str) -> list[float]:
    """Toutes les grandeurs du texte, séparateurs de milliers recollés."""
    plat = _MILLIERS.sub("", texte).replace(",", ".")
    valeurs: list[float] = []
    for brut in _NOMBRE.findall(plat):
        try:
            valeurs.append(float(brut))
        except ValueError:
            continue
    return valeurs


@dataclass(frozen=True)
class Verites:
    """Ce que la question vaut sur chaque assiette, et le volume de chacune."""

    entiere: float
    tranche: float
    lignes_entiere: int
    lignes_tranche: int


def verites(questions: tuple[Question, ...], plafond: int) -> dict[str, Verites]:
    """Les deux assiettes, en SQL direct, hors de l'agent.

    Une connexion par source et non par question : ouvrir six fois Postgres
    pour six comptages sur la même table est un coût qu'on ne paie pas, et
    l'adaptateur du dépôt est déjà celui qui sait parler aux deux dialectes.
    """
    catalogue = load_catalog(Path(get_settings().catalog_path))
    trouvees: dict[str, Verites] = {}
    for nom in sorted({q.source for q in questions}):
        source = next((s for s in catalogue.sources if s.name == nom), None)
        if source is None:
            raise SystemExit(f"la source « {nom} » n'est pas au catalogue : rien à mesurer")
        with closing(open_source(source)) as adaptateur:
            for question in (q for q in questions if q.source == nom):
                lues: dict[str, tuple[float, int]] = {}
                for assiette, portee in (
                    ("entiere", f"SELECT * FROM {question.table}"),
                    ("tranche", f"SELECT * FROM {question.table} LIMIT {plafond}"),
                ):
                    ligne = adaptateur.run(
                        f"SELECT {question.sql} AS valeur, count(*) AS lignes FROM ({portee}) t",
                        max_rows=2,
                    ).rows[0]
                    lues[assiette] = (float(ligne[0]), int(ligne[1]))
                trouvees[question.cle] = Verites(
                    entiere=lues["entiere"][0],
                    tranche=lues["tranche"][0],
                    lignes_entiere=lues["entiere"][1],
                    lignes_tranche=lues["tranche"][1],
                )
    return trouvees


# --- le verdict d'un tirage --------------------------------------------------

SOURCE = "source"
QUALIFIEE = "tranche qualifiée"
MUETTE = "tranche muette"
SANS = "sans chiffre"


def _porte(valeurs: list[float], cible: float, pourcentage: bool) -> bool:
    marge = 0.03 if pourcentage else 0.5
    return any(abs(v - cible) <= marge for v in valeurs)


def qualifie(reponse: str, lignes_tranche: int) -> str:
    """Comment la réponse dit sa tranche — "" si elle ne la dit pas.

    Deux branches, une seule suffit, et la première ne lit aucun mot : le
    VOLUME de la tranche écrit dans la réponse est en lui-même l'aveu que le
    compte ne porte pas sur la source.
    """
    if _porte(nombres(reponse), float(lignes_tranche), pourcentage=False):
        return f"volume de la tranche cité ({lignes_tranche})"
    plat = _replie(reponse)
    dites = [t for t in TOURNURES_DE_TRANCHE if t in plat]
    return "tournure : " + ", ".join(dites) if dites else ""


@dataclass
class Tirage:
    cle: str
    tirage: int
    message: str
    reponse: str
    noeuds: list[str]
    valeurs: list[float] = field(default_factory=list)
    verdict: str = SANS
    aveu: str = ""
    avis_en_trace: str = ""
    duree_ms: int = 0
    # Volet `rejeu` : le début de la réponse du PREMIER tour, conservé parce
    # que c'est lui qui prouve que la figure rejouée portait bien un chiffre de
    # tranche, et qu'elle le disait.
    ouverture: str = ""

    @property
    def echoue(self) -> bool:
        return self.verdict == MUETTE

    @property
    def acceptable(self) -> bool:
        return self.verdict in (SOURCE, QUALIFIEE)


def juge(tirage: Tirage, verite: Verites, question: Question) -> Tirage:
    """Le verdict, dans l'ordre qui compte : la source d'abord.

    Une réponse qui porte le chiffre de la source entière est jugée ``source``
    même si elle porte aussi celui de la tranche — c'est le cas d'une réponse
    qui compare les deux, et elle est meilleure que les deux.
    """
    tirage.valeurs = nombres(tirage.reponse)
    tirage.aveu = qualifie(tirage.reponse, verite.lignes_tranche)
    if _porte(tirage.valeurs, verite.entiere, question.pourcentage):
        tirage.verdict = SOURCE
    elif _porte(tirage.valeurs, verite.tranche, question.pourcentage):
        tirage.verdict = QUALIFIEE if tirage.aveu else MUETTE
    else:
        tirage.verdict = SANS
    return tirage


def un_tirage(orchestrateur: Orchestrator, question: Question, rang: int) -> Tirage:
    """Un tirage — un tour, ou deux quand la question porte une retouche.

    Le fil est le MÊME pour les deux tours, et c'est tout le sujet : le rejeu
    n'existe que parce que le tour d'avant a laissé un artefact dans la
    conversation. Deux fils, ce serait deux premiers tours.
    """
    fil = f"mesure-tranche-{question.cle}-{uuid.uuid4().hex[:8]}"
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        question.message, conversation_id=fil, source_de_travail=question.source
    )
    ouverture = " ".join(reponse.answer.split())[:160] if question.retouche else ""
    if question.retouche:
        reponse = orchestrateur.ask(
            question.retouche, conversation_id=fil, source_de_travail=question.source
        )
    duree = int((time.monotonic() - depart) * 1000)
    avis = " ".join(s.truncation for s in reponse.trace if s.truncation)
    return Tirage(
        cle=question.cle,
        tirage=rang,
        message=question.retouche or question.message,
        reponse=reponse.answer,
        noeuds=[s.node for s in reponse.trace],
        avis_en_trace=avis,
        duree_ms=duree,
        ouverture=ouverture,
    )


def rapport(tirages: list[Tirage], toutes: dict[str, Verites], reglages) -> str:
    lignes = [
        "## Un chiffre de tranche, rendu comme un chiffre de source",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`) — "
        f"tranche à {reglages.analysis_table_max_rows} lignes.",
        "",
        "| clé | volet | source | question | vrai (source) | vrai (tranche) |",
        "|---|---|---|---|---|---|",
    ]
    for question in QUESTIONS:
        verite = toutes.get(question.cle)
        if verite is None:
            continue
        mise = "{:.2f}" if question.pourcentage else "{:.0f}"
        lignes.append(
            f"| `{question.cle}` | {question.volet} | `{question.source}` "
            f"| « {question.message} » "
            f"| **{mise.format(verite.entiere)}** | {mise.format(verite.tranche)} |"
        )
    lignes += [
        "",
        "| clé | tirage | nœuds | chiffre rendu | verdict | ce qui qualifie |",
        "|---|---|---|---|---|---|",
    ]
    for t in tirages:
        question = PAR_CLE[t.cle]
        verite = toutes[t.cle]
        mise = "{:.2f}" if question.pourcentage else "{:.0f}"
        if t.verdict == SOURCE:
            rendu = mise.format(verite.entiere)
        elif t.verdict in (QUALIFIEE, MUETTE):
            rendu = mise.format(verite.tranche)
        else:
            rendu = ", ".join(f"{v:g}" for v in t.valeurs[:6]) or "—"
        marque = {SOURCE: "✔", QUALIFIEE: "✔", MUETTE: "✘", SANS: "—"}[t.verdict]
        lignes.append(
            f"| `{t.cle}` | {t.tirage} | {' → '.join(t.noeuds)} | {rendu} "
            f"| {marque} {t.verdict} | {t.aveu or '—'} |"
        )
    lignes.append("")
    for volet in VOLETS:
        lot = [t for t in tirages if PAR_CLE[t.cle].volet == volet]
        if not lot:
            continue
        lignes.append(
            f"- volet **{volet}** : {sum(1 for t in lot if t.acceptable)}/{len(lot)} acceptables "
            f"({sum(1 for t in lot if t.verdict == SOURCE)} sur la source, "
            f"{sum(1 for t in lot if t.verdict == QUALIFIEE)} tranche qualifiée, "
            f"**{sum(1 for t in lot if t.verdict == MUETTE)} tranche muette**, "
            f"{sum(1 for t in lot if t.verdict == SANS)} sans chiffre)"
        )
    total = len(tirages)
    bons = sum(1 for t in tirages if t.acceptable)
    lignes += ["", f"**{bons}/{total} acceptables en tout.**"]
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--questions", nargs="*", choices=sorted(PAR_CLE), default=None)
    parseur.add_argument("--volets", nargs="*", choices=VOLETS, default=None)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    retenues = tuple(
        q
        for q in QUESTIONS
        if (not args.questions or q.cle in args.questions)
        and (not args.volets or q.volet in args.volets)
    )
    toutes = verites(retenues, reglages.analysis_table_max_rows)
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    for question in retenues:
        verite = toutes[question.cle]
        print(
            f"  {question.cle} [{question.source}] source {verite.entiere:.2f} "
            f"| tranche {verite.tranche:.2f} ({verite.lignes_tranche} lignes)"
        )
    print()

    catalogue = load_catalog(Path(reglages.catalog_path))
    orchestrateur = Orchestrator(
        settings=reglages,
        model=build_model(reglages),
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    tirages: list[Tirage] = []
    for question in retenues:
        for rang in range(1, args.tirages + 1):
            print(f"[{question.cle} {rang}/{args.tirages}] « {question.message} »")
            tirage = juge(un_tirage(orchestrateur, question, rang), toutes[question.cle], question)
            tirages.append(tirage)
            print(f"    → {tirage.verdict} | {tirage.aveu or 'rien ne qualifie'}")
            print(f"    {' '.join(tirage.reponse.split())[:260]}\n")

    texte = rapport(tirages, toutes, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "verites": {c: v.__dict__ for c, v in toutes.items()},
                    "tirages": [t.__dict__ for t in tirages],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
