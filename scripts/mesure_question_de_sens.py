"""Une question de SENS reçoit-elle un sens, ou des valeurs ?

Le défaut, mesuré après C34 sur le catalogue de démonstration, la source
`telemetrie` liée au tour précédent :

    « c'est quoi puissance_kw exactement ? »
        -> « 5 lignes retournées — voir le tableau ci-dessous »

Ce n'est pas une réponse mal formulée : c'est une réponse d'un AUTRE GENRE.
L'utilisateur demande ce qu'une colonne veut dire, et reçoit des valeurs. Le
dictionnaire de la source porte précisément la réponse — que `-1` n'est pas une
puissance — et elle n'est pas servie.

**C'est un défaut de ROUTAGE, et il est total.** Sondé sur huit formulations,
l'agent système répond `AUTRE` 8 fois sur 8 : il ne s'estime concerné par
aucune. Le tour repart au planificateur, qui le classe `query`. Ce qui arrive
ensuite ne tient qu'au hasard de ce que l'agent SQL décide d'en faire — écrire
une requête (et rendre un tableau), ou répondre depuis le dictionnaire qu'il a
dans son prompt (et tomber juste). `S4` du runner de provenance était dans le
second cas, et c'est ce qui le faisait passer pour sain.

Ce runner mesure la PROPRIÉTÉ, pas la tournure : une question à laquelle on
répond en LISANT ce que l'installation déclare doit recevoir ce qui est écrit
là, quelle que soit la phrase. D'où deux volets, et le second est indispensable.

- **volet sens** : six formulations NEUVES — aucune n'a servi ailleurs dans le
  dépôt — sur QUATRE sources qui déclarent un dictionnaire. Aucune ne nomme sa
  source : elle est liée au fil, comme dans la mesure d'origine. L'oracle est
  le FAIT que porte le dictionnaire, jamais un mot précis : chaque fait est
  une disjonction de tournures, et ce qu'elle laisse passer est écrit en toutes
  lettres dans `FAITS_ADMIS`.
- **volet témoin** : les six mêmes FORMES sur `titanic` et `iris`, qui ne
  déclarent AUCUN dictionnaire. Une réponse juste dit ce que le schéma porte ;
  une réponse qui explique la colonne DE MÉMOIRE est un échec, parce qu'elle
  donne l'autorité de la base à un savoir général. C'est le seul volet qui
  distingue « le modèle a lu » de « le modèle se souvient ».

    uv run python scripts/mesure_question_de_sens.py --tirages 3
    uv run python scripts/mesure_question_de_sens.py --volets sens

Prérequis : les deux catalogues semés, Postgres joignable, le serveur LLM.
``DAA_CATALOG_PATH`` n'est PAS lu — les deux catalogues sont portés en dur,
pour la même raison que dans `mesure_provenance_du_sens.py` : une mesure de
provenance n'a de sens que sur des octets connus.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_provenance_du_sens import FAITS_DE_LA_SENTINELLE
from mesure_surface_conversationnelle import ModeleCompteur, porte_le_fait

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator

RACINE = Path(__file__).resolve().parent.parent
CATALOGUE_AVEC = RACINE / "sources/demonstration/catalogue.yaml"
CATALOGUE_SANS = RACINE / "sources/catalogue.yaml"

VOLETS = ("sens", "temoin")

# Ce qui compte comme une attribution — les mêmes deux mots que
# `mesure_provenance_du_sens`, et pour la même raison : « dictionnaire » est le
# nom que le produit donne à cet artefact partout, du catalogue au prompt.
MOTS_DE_PROVENANCE = ("dictionnaire", "dictionary")

# Ce qu'une réponse sur une source SANS dictionnaire ne peut pas savoir. Aucune
# de ces notions n'existe dans `titanic` ni dans `iris` : les y lire, c'est les
# avoir prises ailleurs que dans l'installation.
MARQUES_D_INVENTION = (
    "sentinelle",
    "non renseigné",
    "non renseigne",
    "valeur manquante codée",
    "valeur manquante codee",
    "par convention",
)


@dataclass(frozen=True)
class Question:
    """Une question de sens, et ce qui décide de son verdict.

    ``faits`` est une suite de FAITS, et chaque fait une suite de tournures
    dont UNE SEULE suffit. C'est la forme qu'impose la leçon de la dette E :
    un oracle qui exige un mot mesure notre vocabulaire, pas la justesse de la
    réponse. Ce que la disjonction laisse encore passer est dit dans
    ``FAITS_ADMIS``, parce qu'un oracle desserré sans être montré est un
    chiffre qu'on s'offre.
    """

    cle: str
    volet: str
    catalogue: Path
    source: str
    message: str
    forme: str
    faits: tuple[tuple[str, ...], ...] = ()
    # Témoin : ce que le SCHÉMA porte réellement. La réponse doit s'y appuyer —
    # une prose juste mais sans aucune trace de ce que l'installation déclare
    # est un souvenir, pas une lecture.
    ancrages: tuple[str, ...] = ()
    attendu: str = ""


# Ce que chaque disjonction accepte, et donc ce qu'elle laisse passer. Écrit
# ici et non en commentaire : c'est la contrepartie du desserrage.
FAITS_ADMIS = """\
Chaque fait est tenu par une DISJONCTION de tournures : une seule suffit. Aucun
oracle de ce runner n'exige un mot du vocabulaire du produit. Ce qu'ils
laissent passer, dit franchement : une réponse qui emploie la bonne tournure
sur la mauvaise colonne passerait, et aucune de ces listes ne vérifie la
syntaxe du filtre — seulement que le fait est ÉNONCÉ.

Une disjonction a été ÉLARGIE en cours de campagne, et il faut le dire : celle
du fait « -1 sort des agrégats » ne reconnaissait pas « ne doit pas être
incluse dans tout calcul de puissance », qui l'énonce pourtant exactement. Un
oracle de faits se corrige quand il rejette une réponse juste — sinon il
redevient ce qu'on lui reprochait, une liste de nos tournures."""

# --- volet 1 : six formulations neuves, quatre sources -----------------------
#
# Aucune ne figure ailleurs dans le dépôt — ni dans le parcours, ni dans le
# runner de provenance, ni dans celui d'ouverture. Aucune ne nomme sa source :
# c'est le cas mesuré, et c'est le cas difficile. Les six formes sont
# volontairement dissemblables — elliptique, à deux questions, en « pourquoi »,
# en comparaison, en soupçon — parce qu'une famille se mesure par son étendue.
SENS: tuple[Question, ...] = (
    Question(
        cle="N1",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="telemetrie",
        message="puissance_kw à -1, je dois le comprendre comment ?",
        forme="elliptique, sur une valeur",
        # Les MÊMES deux faits que `S4` du runner de provenance et que
        # `sens-puissance` du parcours, importés et non recopiés : c'est la
        # même colonne, le même dictionnaire et la même question posée de trois
        # façons. Trois copies d'une disjonction, ce seraient trois oracles qui
        # divergent en silence, et qui sont pourtant censés juger la même chose.
        faits=FAITS_DE_LA_SENTINELLE,
        attendu="-1 n'est pas une puissance, et sort des moyennes",
    ),
    Question(
        cle="N2",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="interventions",
        message="duree_indispo_min est en minutes ou en heures ? et les valeurs négatives ?",
        forme="deux questions en une",
        faits=(
            ("minute",),
            (
                "non renseigné",
                "non renseigne",
                "pas renseigné",
                "pas renseigne",
                "absence",
                "manquant",
                "inconnu",
                "pas zéro",
                "pas zero",
                "sentinelle",
                "écarter",
                "ecarter",
            ),
        ),
        attendu="des minutes, et -1 n'est pas une durée",
    ),
    Question(
        cle="N3",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="interventions",
        message="pourquoi station_libelle et pas un code station ?",
        forme="en « pourquoi », sur un choix de modélisation",
        faits=(
            ("libellé", "libelle", "quartier", "affichage"),
            (
                "referentiel",
                "référentiel",
                "correspondance",
                "aucune jointure directe",
                "pas de jointure directe",
                "jointure",
            ),
        ),
        attendu="un libellé d'affichage, et le passage par referentiel",
    ),
    Question(
        cle="N4",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="referentiel",
        message="le statut RET, il recouvre quoi au juste ?",
        forme="sur un code, sans nommer la colonne",
        faits=(("retir", "démont", "demont", "hors service", "plus en service"),),
        attendu="RET = station retirée du service",
    ),
    Question(
        cle="N5",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="facturation",
        message="montant_ttc_eur et montant_ht_eur, quelle différence ?",
        forme="comparaison de deux colonnes",
        faits=(
            ("taxe", "tva", "20"),
            (
                "deux fois",
                "doublon",
                "double",
                "ne pas sommer",
                "pas les sommer",
                "dérivé",
                "derive",
                "dériv",
            ),
        ),
        attendu="TTC dérivé du HT, et sommer les deux compte deux fois",
    ),
    Question(
        cle="N6",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="facturation",
        message="prix_kwh_eur à 0 sur ABO, c'est une donnée manquante ?",
        forme="soupçon, à démentir",
        faits=(
            ("forfait", "illimité", "illimite", "abonnement", "au mois", "mensuel"),
            (
                "vraie valeur",
                "valeur réelle",
                "valeur reelle",
                "pas une donnée manquante",
                "pas une donnee manquante",
                "n'est pas manquante",
                "pas manquante",
                "pas un trou",
                "bien 0",
                "réellement 0",
            ),
        ),
        attendu="0 est une vraie valeur — le forfait ne se facture pas au kWh",
    ),
)

# --- volet 2 : le témoin, six mêmes formes sur deux sources sans dictionnaire -
#
# `titanic` et `iris` sont le pire cas possible pour cette mesure, et c'est
# pour cela qu'ils sont là : ce sont deux jeux dont le sens des colonnes est de
# notoriété publique, donc dans la mémoire du modèle. Une réponse qui les
# explique sans rien lire est indiscernable d'une réponse juste — sauf par son
# ancrage. D'où l'exigence : citer ce que le schéma porte vraiment.
TEMOINS: tuple[Question, ...] = (
    Question(
        cle="W1",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="titanic",
        message="embarked à Q, je dois le comprendre comment ?",
        forme="elliptique, sur une valeur",
        ancrages=("embarked",),
        attendu="pas de dictionnaire cité, pas de convention inventée",
    ),
    Question(
        cle="W2",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="titanic",
        message="fare est en livres ou en dollars ? et les valeurs à zéro ?",
        forme="deux questions en une",
        ancrages=("fare",),
        attendu="pas de dictionnaire cité, pas d'unité inventée",
    ),
    Question(
        cle="W3",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="titanic",
        message="pourquoi class_id et pas directement la classe ?",
        forme="en « pourquoi », sur un choix de modélisation",
        ancrages=("class_id",),
        attendu="pas de dictionnaire cité — la clé étrangère est dans le schéma",
    ),
    Question(
        cle="W4",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="iris",
        message="la valeur setosa, elle recouvre quoi au juste ?",
        forme="sur un code, sans nommer la colonne",
        ancrages=("species", "setosa"),
        attendu="pas de dictionnaire cité",
    ),
    Question(
        cle="W5",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="titanic",
        message="sibsp et parch, quelle différence ?",
        forme="comparaison de deux colonnes",
        ancrages=("sibsp", "parch"),
        attendu="pas de dictionnaire cité — et c'est ici que la mémoire tente",
    ),
    Question(
        cle="W6",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="iris",
        message="petal_width à 0, c'est une donnée manquante ?",
        forme="soupçon, à démentir",
        ancrages=("petal_width",),
        attendu="pas de dictionnaire cité, pas de sentinelle inventée",
    ),
)

QUESTIONS: tuple[Question, ...] = (*SENS, *TEMOINS)


@dataclass
class Releve:
    question: Question
    tirage: int
    reponse: str
    noeuds: list[str]
    attribue: bool = False
    tableau: bool = False
    faits_manquants: list[str] = field(default_factory=list)
    verdict: str = ""
    pourquoi: str = ""
    appels_llm: int = 0
    duree_ms: int = 0


def attribue(texte: str) -> bool:
    plat = texte.lower()
    return any(mot in plat for mot in MOTS_DE_PROVENANCE)


def _porte(texte: str, tournures: tuple[str, ...]) -> bool:
    plat = texte.lower()
    return porte_le_fait(plat, tournures)


def juger(question: Question, reponse: ChatAnswer, texte: str) -> tuple[str, str, list[str]]:
    """Le fait d'abord ; l'attribution ensuite, et jamais l'une sans l'autre."""
    if reponse.error:
        return "échec", f"erreur : {reponse.error}", []
    if question.volet == "temoin":
        if attribue(texte):
            return "échec", "dictionnaire CITÉ — cette source n'en déclare aucun", []
        inventees = [m for m in MARQUES_D_INVENTION if m in texte.lower()]
        if inventees:
            return "échec", f"convention inventée : {', '.join(inventees)}", []
        absents = [a for a in question.ancrages if a.lower() not in texte.lower()]
        if absents:
            return "échec", f"rien du schéma : {', '.join(absents)} absent", []
        return "conforme", question.attendu, []
    manquants = [f[0] for f in question.faits if not _porte(texte, f)]
    if manquants:
        return "échec", f"fait absent : {', '.join(manquants)}", manquants
    if not attribue(texte):
        return "échec", "provenance non dite — le dictionnaire n'est pas nommé", []
    return "conforme", question.attendu, []


def poser(orchestrateur: Orchestrator, question: Question, tirage: int) -> Releve:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        question.message,
        conversation_id=f"sens-{uuid.uuid4().hex[:8]}",
        source_de_travail=question.source,
    )
    duree = int((time.monotonic() - depart) * 1000)
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    verdict, pourquoi, manquants = juger(question, reponse, texte)
    return Releve(
        question=question,
        tirage=tirage,
        reponse=" ".join(reponse.answer.split()),
        noeuds=[s.node for s in reponse.trace],
        attribue=attribue(texte),
        tableau=bool(tableau),
        faits_manquants=manquants,
        verdict=verdict,
        pourquoi=pourquoi,
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )


def rapport(releves: list[Releve], reglages, titre: str) -> str:
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    lignes = [
        f"## {titre}",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        "",
        f"**{conformes}/{len(releves)} tours conformes, "
        f"{sum(r.appels_llm for r in releves)} appels LLM.**",
        "",
        FAITS_ADMIS,
        "",
    ]
    volets = [v for v in VOLETS if any(r.question.volet == v for r in releves)]
    if len(volets) > 1:
        lignes += ["Par volet :", ""]
        for volet in volets:
            lot = [r for r in releves if r.question.volet == volet]
            bons = sum(1 for r in lot if r.verdict == "conforme")
            lignes.append(
                f"- **{volet}** : {bons}/{len(lot)}, {sum(r.appels_llm for r in lot)} appels LLM"
            )
        lignes.append("")
    lignes += [
        "| clé | source | forme | message | nœuds | tableau | provenance | score | "
        "ce qui a décidé |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cle in dict.fromkeys(r.question.cle for r in releves):
        lot = [r for r in releves if r.question.cle == cle]
        q = lot[0].question
        bons = sum(1 for r in lot if r.verdict == "conforme")
        noeuds = " / ".join(dict.fromkeys(" → ".join(r.noeuds) for r in lot))
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        pourquoi = " ; ".join(echecs) if echecs else lot[0].pourquoi
        lignes.append(
            f"| `{cle}` | `{q.source}` | {q.forme} | {' '.join(q.message.split())} "
            f"| {noeuds} | {sum(1 for r in lot if r.tableau)}/{len(lot)} "
            f"| {sum(1 for r in lot if r.attribue)}/{len(lot)} "
            f"| **{bons}/{len(lot)}** | {pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--titre", default="Six formulations neuves de question de sens")
    parseur.add_argument("--volets", nargs="*", default=list(VOLETS))
    parseur.add_argument("--seulement", nargs="*", default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    choisies = [
        q
        for q in QUESTIONS
        if q.volet in args.volets and (not args.seulement or q.cle in args.seulement)
    ]
    modele = ModeleCompteur(build_model(reglages))
    registre = Registry.load(reglages.models_registry_path)
    orchestrateurs = {
        chemin: Orchestrator(
            settings=reglages,
            model=modele,
            catalog=load_catalog(chemin),
            registry=registre,
        )
        for chemin in dict.fromkeys(q.catalogue for q in choisies)
    }

    releves: list[Releve] = []
    total = len(choisies) * args.tirages
    numero = 0
    for tirage in range(1, args.tirages + 1):
        for question in choisies:
            numero += 1
            print(f"[{numero}/{total}] {question.cle}·{tirage} — « {question.message} »")
            releve = poser(orchestrateurs[question.catalogue], question, tirage)
            releves.append(releve)
            print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
            print(f"    {releve.reponse[:220]}\n", flush=True)

    texte = rapport(releves, reglages, args.titre)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **{k: v for k, v in r.__dict__.items() if k != "question"},
                        "cle": r.question.cle,
                        "volet": r.question.volet,
                        "source": r.question.source,
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
