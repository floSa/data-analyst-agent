"""Une réponse sur le SENS d'une colonne dit-elle d'où elle le tient ?

Le défaut : « statut dans sessions, ça veut dire quoi ? » partait à la
récupération, qui répondait JUSTE — « 'T' : Terminée, la recharge a abouti ;
'I' : Interrompue… », contenu qui ne peut venir que du dictionnaire, qu'elle a
dans son prompt — mais sans dire « selon le dictionnaire de `exploitation` ».
Le chiffre n'est pas faux ; la provenance n'est pas dite. Et c'est elle qui
distingue une LECTURE de la source d'un savoir général sur les codes de statut :
sans elle, l'utilisateur ne peut pas savoir si on lui a lu sa base ou récité
une convention répandue.

Deux volets, et le second est indispensable :

- **volet sens** : six questions de sens sur deux sources qui DÉCLARENT un
  dictionnaire. La réponse doit porter le contenu du dictionnaire ET nommer sa
  provenance ;
- **volet témoin** : les quatre mêmes formes de question sur deux sources qui
  n'en déclarent AUCUN (`titanic`, `iris`). La réponse ne doit pas citer de
  dictionnaire — le piège d'une consigne d'attribution est de faire CITER une
  source qui n'a rien servi, et une attribution inventée est pire que pas
  d'attribution : elle donne l'autorité de la base à un savoir général.

Deux catalogues, donc, passés explicitement : celui de démonstration pour le
sens, celui de production pour les témoins. Le second n'est pas un décor — il
est la seule façon de distinguer « le modèle a lu le dictionnaire » de « le
modèle a appris à écrire le mot dictionnaire ».

    uv run python scripts/mesure_provenance_du_sens.py
    uv run python scripts/mesure_provenance_du_sens.py --tirages 3 --markdown /tmp/b.md
    uv run python scripts/mesure_provenance_du_sens.py --volets sens

Prérequis : les deux catalogues semés (`seed_catalogue_demonstration.py` et
`seed_titanic_postgres.py`), Postgres joignable, le serveur LLM en place.
``DAA_CATALOG_PATH`` n'est PAS lu : les catalogues sont passés en argument,
parce qu'une mesure de provenance n'a de sens que sur des octets connus.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator

RACINE = Path(__file__).resolve().parent.parent
CATALOGUE_AVEC = RACINE / "sources/demonstration/catalogue.yaml"
CATALOGUE_SANS = RACINE / "sources/catalogue.yaml"

# Ce qui compte comme une attribution. Trois mots et non une liste de tournures :
# on ne juge pas la phrase du modèle, on vérifie qu'un mot désignant la
# documentation de la source y figure. Le « dictionnaire » est le nom que le
# produit lui donne partout — dans le catalogue (clé `dictionary`), dans le
# prompt injecté (`EN_TETE_SQL`) et dans la documentation.
MOTS_DE_PROVENANCE = ("dictionnaire", "dictionary")


@dataclass(frozen=True)
class Question:
    """Une question de sens, et ce qui décide de son verdict.

    ``provenance`` dit ce qu'on attend de l'attribution : ``exigee`` quand la
    source déclare un dictionnaire, ``interdite`` quand elle n'en déclare aucun.
    Le second cas est le témoin, et il n'est pas symétrique du premier : une
    attribution inventée n'est pas une maladresse, c'est une fausse garantie.
    """

    cle: str
    volet: str
    catalogue: Path
    source: str
    message: str
    provenance: str  # "exigee" | "interdite"
    fragments: tuple[str, ...] = ()
    # Les FAITS attendus, chacun une disjonction dont une seule tournure suffit.
    # `fragments` est une conjonction de sous-chaînes exactes : dès qu'un fait
    # peut s'énoncer de deux façons, il mesure notre vocabulaire. `S4` exigeait
    # « sentinelle » — le mot du dictionnaire et du produit, pas celui d'une
    # réponse juste : « -1 ne correspond à aucune mesure et doit être exclu des
    # moyennes » dit exactement le bon fait et échouait (dette E).
    #
    # `fragments` reste calculé à côté (``verdict_strict``) : c'est la seule
    # façon de dire combien de points viennent du desserrage.
    faits: tuple[tuple[str, ...], ...] = ()
    attendu: str = ""


VOLETS = ("sens", "temoin")

# --- volet 1 : six questions de sens, deux sources à dictionnaire ------------
#
# Trois par source, court et long mélangés. Les fragments attendus sont du
# CONTENU de dictionnaire : rien dans le schéma ne dit que `-1` est une
# sentinelle, ni que `nb_points` est un nombre PRÉVU, ni que `prix_kwh_eur = 0`
# sur `ABO` n'est pas une valeur manquante. Une réponse qui les porte a lu le
# dictionnaire, qu'elle le dise ou non — c'est ce qui rend l'exigence
# d'attribution mesurable séparément du reste.
SENS: tuple[Question, ...] = (
    Question(
        cle="S1",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="exploitation",
        message="statut dans sessions, ça veut dire quoi ?",
        provenance="exigee",
        fragments=("'T'", "'I'", "'E'"),
        attendu="les trois codes, attribués au dictionnaire",
    ),
    Question(
        cle="S2",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="exploitation",
        message=(
            "Pourrais-tu m'expliquer à quoi correspond exactement la colonne nb_points "
            "de la table stations ?"
        ),
        provenance="exigee",
        fragments=("prévu",),
        attendu="un nombre prévu, pas un compte de bornes — attribué",
    ),
    Question(
        cle="S3",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="exploitation",
        message="c'est quoi le champ prix_kwh_eur ?",
        provenance="exigee",
        fragments=("abo",),
        attendu="le forfait à 0,00 qui n'est pas un trou — attribué",
    ),
    Question(
        cle="S4",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="telemetrie",
        message="puissance_kw, ça signifie quoi ?",
        provenance="exigee",
        fragments=("sentinelle",),
        # Deux faits, et aucun mot obligatoire : `-1` n'est pas une puissance,
        # et il sort des agrégats. C'est ce qu'un utilisateur doit emporter du
        # tour ; la façon de le dire ne le regarde pas.
        faits=(
            (
                "pas une puissance",
                "n'est pas une puissance",
                "aucune mesure",
                "absence de mesure",
                "rien remonté",
                "rien remonte",
                "pas de mesure",
                "non mesuré",
                "non mesure",
                "sentinelle",
                "compteur muet",
            ),
            (
                "écarter",
                "ecarter",
                "exclure",
                "exclu",
                "ne pas inclure",
                "fausse",
                "filtrer",
                ">= 0",
                ">=0",
                "hors moyenne",
                "pas être inclus",
                "pas etre inclus",
                "pas inclus",
                "pas prise en compte",
                "pas prises en compte",
                "à ignorer",
                "a ignorer",
                "fausserait",
            ),
        ),
        attendu="-1 n'est pas une puissance et sort des moyennes, attribué",
    ),
    Question(
        cle="S5",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="telemetrie",
        message=(
            "J'aimerais comprendre ce que représente exactement la colonne code_station "
            "dans la table bornes_suivies."
        ),
        provenance="exigee",
        fragments=("st-",),
        attendu="le code fonctionnel ST-nnn, attribué",
    ),
    Question(
        cle="S6",
        volet="sens",
        catalogue=CATALOGUE_AVEC,
        source="telemetrie",
        message="horodatage c'est quoi ?",
        provenance="exigee",
        fragments=("horaire",),
        attendu="le pas horaire, attribué au dictionnaire",
    ),
)

# --- volet 2 : le témoin, sur deux sources SANS dictionnaire -----------------
#
# Les mêmes formes de question, sur `titanic` et `iris`, qui n'en déclarent
# aucun. Le bloc de dictionnaire est alors ABSENT du prompt — donc toute
# attribution y est une invention, et le témoin la voit.
TEMOINS: tuple[Question, ...] = (
    Question(
        cle="T1",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="titanic",
        message="sex dans passengers, ça veut dire quoi ?",
        provenance="interdite",
        attendu="pas de dictionnaire cité — il n'y en a pas",
    ),
    Question(
        cle="T2",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="titanic",
        message=(
            "Pourrais-tu m'expliquer à quoi correspond exactement la colonne class_id "
            "de la table passengers ?"
        ),
        provenance="interdite",
        attendu="pas de dictionnaire cité — il n'y en a pas",
    ),
    Question(
        cle="T3",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="iris",
        message="c'est quoi le champ petal_length ?",
        provenance="interdite",
        attendu="pas de dictionnaire cité — il n'y en a pas",
    ),
    Question(
        cle="T4",
        volet="temoin",
        catalogue=CATALOGUE_SANS,
        source="iris",
        message="J'aimerais comprendre ce que représente exactement la colonne species.",
        provenance="interdite",
        attendu="pas de dictionnaire cité — il n'y en a pas",
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
    valeurs: list[float] = field(default_factory=list)
    verdict: str = ""
    pourquoi: str = ""
    # Le verdict de l'oracle d'AVANT desserrage. Hors de tout total.
    verdict_strict: str = ""
    appels_llm: int = 0
    duree_ms: int = 0


def attribue(texte: str) -> bool:
    """La réponse nomme-t-elle la documentation de la source ?"""
    plat = texte.lower()
    return any(mot in plat for mot in MOTS_DE_PROVENANCE)


def juger(question: Question, reponse: ChatAnswer, texte: str) -> tuple[str, str]:
    """Le contenu d'abord, l'attribution ensuite — et jamais l'une sans l'autre.

    L'ordre compte pour la lisibilité du relevé : une réponse qui n'a pas le
    contenu du dictionnaire n'a rien à attribuer, et dire « attribution
    manquante » masquerait qu'elle n'a rien lu du tout.
    """
    if reponse.error:
        return "échec", f"erreur : {reponse.error}"
    if question.faits:
        plat = texte.lower()
        manquants = [f[0] for f in question.faits if not any(t.lower() in plat for t in f)]
        if manquants:
            return "échec", f"fait absent : {', '.join(manquants)}"
    else:
        absents = [f for f in question.fragments if f.lower() not in texte.lower()]
        if absents:
            return "échec", f"contenu absent : {', '.join(absents)}"
    cite = attribue(texte)
    if question.provenance == "exigee" and not cite:
        return "échec", "provenance non dite — le dictionnaire n'est pas nommé"
    if question.provenance == "interdite" and cite:
        return "échec", "dictionnaire CITÉ alors que la source n'en déclare aucun"
    return "conforme", question.attendu


def juger_strict(question: Question, reponse: ChatAnswer, texte: str) -> str:
    """Le verdict de l'oracle d'AVANT, conservé pour mesurer le desserrage.

    Il n'entre dans aucun total. Sans lui, un oracle desserré et un produit
    réparé rendent le même chiffre, et ce chiffre ne prouve plus rien.
    """
    if reponse.error:
        return "échec"
    if [f for f in question.fragments if f.lower() not in texte.lower()]:
        return "échec"
    cite = attribue(texte)
    if question.provenance == "exigee" and not cite:
        return "échec"
    if question.provenance == "interdite" and cite:
        return "échec"
    return "conforme"


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
    verdict, pourquoi = juger(question, reponse, texte)
    return Releve(
        question=question,
        tirage=tirage,
        reponse=" ".join(reponse.answer.split()),
        noeuds=[s.node for s in reponse.trace],
        attribue=attribue(texte),
        verdict=verdict,
        pourquoi=pourquoi,
        verdict_strict=juger_strict(question, reponse, texte),
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
    ]
    desserres = [r for r in releves if r.question.faits]
    if desserres:
        stricts = sum(1 for r in desserres if r.verdict_strict == "conforme")
        larges = sum(1 for r in desserres if r.verdict == "conforme")
        lignes += [
            f"Sur les {len(desserres)} tours dont l'oracle a été desserré "
            f"(`{'`, `'.join(dict.fromkeys(r.question.cle for r in desserres))}`) : "
            f"**{stricts}/{len(desserres)}** avec l'oracle d'avant, "
            f"**{larges}/{len(desserres)}** avec celui d'après. "
            f"L'écart est ce que le desserrage donne, et rien d'autre.",
            "",
        ]
    volets = [v for v in VOLETS if any(r.question.volet == v for r in releves)]
    if len(volets) > 1:
        lignes += ["Par volet :", ""]
        for volet in volets:
            lot = [r for r in releves if r.question.volet == volet]
            bons = sum(1 for r in lot if r.verdict == "conforme")
            appels = sum(r.appels_llm for r in lot)
            lignes.append(f"- **{volet}** : {bons}/{len(lot)}, {appels} appels LLM")
        lignes.append("")
    lignes += [
        "| clé | source | message | nœuds | appels | provenance dite | score | strict | "
        "ce qui a décidé |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cle in dict.fromkeys(r.question.cle for r in releves):
        lot = [r for r in releves if r.question.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "conforme")
        noeuds = " / ".join(dict.fromkeys(" → ".join(r.noeuds) for r in lot))
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        pourquoi = " ; ".join(echecs) if echecs else lot[0].pourquoi
        dits = sum(1 for r in lot if r.attribue)
        strict = (
            f"{sum(1 for r in lot if r.verdict_strict == 'conforme')}/{len(lot)}"
            if lot[0].question.faits
            else "—"
        )
        lignes.append(
            f"| `{cle}` | `{lot[0].question.source}` | {' '.join(lot[0].question.message.split())} "
            f"| {noeuds} | {sum(r.appels_llm for r in lot)} | {dits}/{len(lot)} "
            f"| **{bons}/{len(lot)}** | {strict} | {pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--titre", default="La provenance du sens d'une colonne")
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
    # UN orchestrateur par catalogue, et le modèle est partagé : le compteur
    # d'allers-retours doit totaliser les deux volets, sinon le coût du témoin
    # disparaît du tableau.
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
            print(f"    {releve.reponse[:200]}\n", flush=True)

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
