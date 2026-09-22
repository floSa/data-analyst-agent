"""Le parcours de démonstration, rejoué depuis un message utilisateur.

Les seize tours de `docs/sources-de-demonstration.md` — trois ouvertures qui
nomment une source, les sept questions métier, les trois tours du verrou de
source, les deux questions sur le sens d'une colonne piégeuse, et la réserve de
forme. Ce parcours était rejoué à la main d'un chantier à l'autre ; il est ici
un runner, pour la raison qui vaut pour tous les autres de ce dossier : une
campagne qu'on refait à la main ne se compare qu'à ce dont on se souvient.

**Chaque tour est jugé mécaniquement**, et sur ce que l'utilisateur voit
vraiment : la phrase de réponse ET le tableau d'artefacts. Le prompt de l'agent
SQL lui INTERDIT de recopier les lignes dans sa phrase — ne lire que la phrase
ferait passer pour muet un tour qui a rendu ses chiffres.

**Le protocole imite l'API, pas un banc d'essai.** ``source_de_travail`` vaut
toujours une chaîne, jamais ``None`` : avec ``None``, le court-circuit
déterministe de `_choix_de_source` ne se déclenche pas, un message qui ne porte
qu'un nom de source ne lie rien, et le banc fabrique un défaut que le produit
n'a pas. C'est arrivé ; c'est écrit dans le document.

**Chaque tour se pose de TROIS façons, et c'est le défaut que ce runner a
d'abord eu.** Il rejouait seize phrases que nous avions écrites nous-mêmes, une
par question, et rendait 16/16 : ce chiffre mesurait ces phrases-là, pas les
questions. Chaque tour porte donc deux paraphrases d'utilisateur en plus de la
sienne — l'une courte et familière, l'autre longue et polie — avec le MÊME
oracle. Quarante-huit questions, qu'on peut regarder en face.

    uv run python scripts/mesure_parcours_de_demonstration.py
    uv run python scripts/mesure_parcours_de_demonstration.py --tirages 3
    uv run python scripts/mesure_parcours_de_demonstration.py --formulations canonique

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, le
catalogue semé, Postgres joignable, le serveur LLM en place.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_provenance_du_sens import (
    SENTINELLE_N_EST_PAS_UNE_PUISSANCE,
    SENTINELLE_SORT_DES_AGREGATS,
)
from mesure_surface_conversationnelle import ModeleCompteur, nombres, porte_le_fait

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator


@dataclass(frozen=True)
class Tour:
    """Un tour du parcours, et ce qui décide de son verdict.

    ``fil`` groupe les tours d'une même conversation — le verrou de source n'a
    de sens que sur trois tours enchaînés. ``source`` est la source de travail
    passée à l'entrée, comme l'API la passe.
    """

    cle: str
    fil: str
    message: str
    # Les DEUX paraphrases du même tour : même intention, même oracle, autre
    # phrase. L'une courte et familière, l'autre longue et polie — les deux
    # bords de ce qu'un utilisateur écrit vraiment. Le 16/16 d'avant se lisait
    # sur la seule `message`, et ne disait donc rien de la QUESTION : seulement
    # de la phrase qu'on avait nous-mêmes écrite pour la poser.
    courte: str = ""
    longue: str = ""
    source: str = ""
    # La source qui doit être LIÉE au fil après le tour — l'oracle de la dette D.
    # Il ne se lit pas dans le texte : une réponse peut nommer une source, et la
    # décrire parfaitement, sans avoir rien retenu. C'est exactement le cas qui
    # passait inaperçu, et le même oracle existe déjà dans
    # `mesure_ouverture_de_source.py` pour la même raison.
    liee: str = ""
    # Ce que la réponse (ou le tableau) doit porter : des nombres, à la
    # tolérance près, et des fragments de texte.
    nombres: tuple[float, ...] = ()
    fragments: tuple[str, ...] = ()
    # Les FAITS que la réponse doit porter, chacun une disjonction de tournures
    # dont une seule suffit. C'est la forme desserrée de `fragments`, et elle
    # existe pour une raison précise : `fragments` est une conjonction de
    # sous-chaînes exactes, donc il mesure une TYPOGRAPHIE dès que le fait peut
    # s'écrire de deux façons. `sens-statut` exigeait `'T'` avec des apostrophes
    # droites et rejetait `` `T` `` — la même information, l'autre convention de
    # citation (dette E). Un oracle n'a pas à départager deux façons d'écrire
    # la même chose ; il a à constater que la chose est dite.
    #
    # `fragments` n'est pas retiré : il reste le verdict STRICT, calculé en
    # parallèle et rapporté à part, pour qu'un desserrage ne se confonde jamais
    # avec un progrès du produit.
    faits: tuple[tuple[str, ...], ...] = ()
    # Ce qu'elle ne doit PAS porter — la valeur du voisin, pour le verrou.
    interdits: tuple[float, ...] = ()
    attendu: str = ""


FORMULATIONS = ("canonique", "courte", "longue")


PARCOURS: tuple[Tour, ...] = (
    # --- trois ouvertures : un message qui ne porte qu'un nom de source -------
    #
    # Leur oracle a CHANGÉ, et c'est l'arbitrage de la dette D. Il exigeait le
    # volume — « 6 tables », « 3 feuilles » — c'est-à-dire le chiffre que produit
    # le gabarit de l'accusé de réception (``FaitsDeSource.en_clair``). Mesuré,
    # 3 tirages sur 3 : la formulation longue de `ouverture-facturation` répond
    # en donnant les trois feuilles ET leurs colonnes — strictement plus que
    # l'accusé — et échouait pour n'avoir pas écrit le chiffre 3. Un oracle qui
    # rejette une réponse plus riche que celle qu'il attend mesure une forme.
    #
    # Ce qui manquait vraiment était ailleurs, et personne ne le regardait : la
    # source n'était pas LIÉE, et la question suivante du même fil recevait
    # l'inventaire des cinq sources. L'oracle demande donc désormais la liaison —
    # ce que l'utilisateur perd pour de bon quand elle n'a pas lieu — et le nom
    # de la source dans la réponse, qu'il exigeait déjà.
    Tour(
        cle="ouverture-exploitation",
        fil="ouverture-exploitation",
        message="exploitation",
        courte="on bosse sur exploitation",
        longue=(
            "Bonjour, j'aimerais travailler sur la source exploitation. Peux-tu me la présenter ?"
        ),
        fragments=("exploitation",),
        liee="exploitation",
        attendu="exploitation liée, et son contenu dit",
    ),
    Tour(
        cle="ouverture-telemetrie",
        fil="ouverture-telemetrie",
        message="telemetrie",
        courte="passe sur telemetrie",
        longue="Pourrais-tu m'ouvrir la source telemetrie et me dire ce qu'elle contient ?",
        fragments=("telemetrie",),
        liee="telemetrie",
        attendu="telemetrie liée, et son contenu dit",
    ),
    Tour(
        cle="ouverture-facturation",
        fil="ouverture-facturation",
        message="facturation",
        courte="facturation, vas-y",
        longue=(
            "Je souhaiterais consulter la source facturation ; peux-tu m'indiquer ce "
            "qu'on y trouve ?"
        ),
        fragments=("facturation",),
        liee="facturation",
        attendu="facturation liée, et son contenu dit",
    ),
    # --- les sept questions métier -------------------------------------------
    Tour(
        cle="Q1",
        fil="metier-exploitation",
        message="combien de sessions de recharge y a-t-il en tout ?",
        courte="ça fait combien de recharges en tout ?",
        longue=(
            "Pourrais-tu me dire quel est le nombre total de sessions de recharge "
            "enregistrées dans cette source ?"
        ),
        source="exploitation",
        nombres=(48000.0,),
        attendu="48 000",
    ),
    Tour(
        cle="Q2",
        fil="metier-exploitation",
        message="combien de sessions ont le statut T en 2025 ?",
        courte="en 2025, combien en statut T ?",
        longue="Sur l'année 2025, combien de sessions portent-elles le statut T ?",
        source="exploitation",
        nombres=(42281.0,),
        attendu="42 281",
    ),
    Tour(
        cle="Q3",
        fil="metier-exploitation",
        message="quelle énergie totale, en kWh, a été délivrée sur l'année ?",
        courte="ça fait combien de kWh au total sur l'année ?",
        longue=(
            "Peux-tu me calculer l'énergie totale délivrée sur l'ensemble de l'année, "
            "exprimée en kWh ?"
        ),
        source="exploitation",
        nombres=(1757519.23,),
        attendu="1 757 519,23 kWh",
    ),
    Tour(
        cle="Q4",
        fil="metier-exploitation",
        message=(
            "quelles sont les trois stations avec le plus de sessions ? donne leur code et leur nom"
        ),
        courte="le top 3 des stations, code et nom",
        longue=(
            "Pourrais-tu m'indiquer quelles sont les trois stations les plus "
            "sollicitées, en précisant leur code et leur nom ?"
        ),
        source="exploitation",
        nombres=(1015.0, 911.0, 872.0),
        fragments=("ST-097", "ST-029", "ST-016"),
        attendu="les trois stations, dans l'ordre, avec les comptes",
    ),
    Tour(
        cle="Q5",
        fil="metier-exploitation",
        message="combien de sessions par région ? classe-les de la plus active à la moins active",
        courte="les sessions par région, de la plus grosse à la plus petite",
        longue=(
            "Peux-tu me donner le nombre de sessions pour chaque région, en les "
            "ordonnant de la plus active à la moins active ?"
        ),
        source="exploitation",
        nombres=(9673.0, 9500.0, 8866.0, 7664.0, 6372.0, 5925.0),
        attendu="les six régions, dans l'ordre",
    ),
    Tour(
        cle="Q6",
        fil="metier-telemetrie",
        message="combien de lignes dans la table releves_puissance ?",
        courte="releves_puissance, ça fait combien de lignes ?",
        longue=(
            "Pourrais-tu m'indiquer le nombre total d'enregistrements présents dans la "
            "table releves_puissance ?"
        ),
        source="telemetrie",
        nombres=(547200.0,),
        attendu="547 200",
    ),
    Tour(
        cle="Q7",
        fil="metier-facturation",
        message="combien de factures as-tu, et quel est le montant total hors taxes facturé ?",
        courte="combien de factures, et ça fait combien en HT ?",
        longue=(
            "Peux-tu me dire combien de factures sont enregistrées, ainsi que le "
            "montant total hors taxes facturé ?"
        ),
        source="facturation",
        nombres=(1200.0, 574408.10),
        attendu="1 200 factures, 574 408,10 € HT",
    ),
    # --- le verrou de source, sur une colonne qui existe des deux côtés -------
    # Son premier tour est une ouverture de plus, et il lui faut la sienne : le
    # verrou se mesure sur trois tours ENCHAÎNÉS, et emprunter le fil d'une
    # ouverture déjà jouée mesurerait autre chose.
    Tour(
        cle="verrou-ouverture",
        fil="verrou",
        message="exploitation",
        courte="on repart sur exploitation",
        longue="J'aimerais reprendre le travail sur la source exploitation, peux-tu la charger ?",
        fragments=("exploitation",),
        nombres=(6.0,),
        attendu="la source est liée au fil du verrou",
    ),
    Tour(
        cle="verrou-source-liee",
        fil="verrou",
        message="quelle est l'énergie totale en kWh dans cette source ?",
        courte="ça fait combien de kWh là-dedans ?",
        longue=(
            "Pourrais-tu me donner l'énergie totale, en kWh, pour la source sur "
            "laquelle nous travaillons actuellement ?"
        ),
        source="exploitation",
        nombres=(1757519.23,),
        interdits=(531098.10,),
        attendu="1 757 519,23 — celle de la source liée, et pas celle du voisin",
    ),
    Tour(
        cle="verrou-bascule",
        fil="verrou",
        message="et dans facturation, quelle est l'énergie totale en kWh ?",
        courte="et côté facturation, ça donne quoi en kWh ?",
        longue=(
            "Et si l'on regarde maintenant du côté de la source facturation, quelle y "
            "est l'énergie totale en kWh ?"
        ),
        source="exploitation",
        nombres=(531098.10,),
        interdits=(1757519.23,),
        fragments=("facturation",),
        attendu="531 098,10 après une bascule annoncée",
    ),
    # --- le sens d'une colonne piégeuse, qui ne vient que du dictionnaire -----
    Tour(
        cle="sens-statut",
        fil="sens-statut",
        message="que signifie la colonne statut de la table sessions ?",
        courte="statut dans sessions, ça veut dire quoi ?",
        longue=(
            "Pourrais-tu m'expliquer la signification de la colonne statut de la table sessions ?"
        ),
        source="exploitation",
        fragments=("dictionnaire", "'T'", "'I'", "'E'"),
        # Chaque code compte pour un fait, et vaut par sa CITATION — quelle que
        # soit la convention de guillemets — ou par son SENS écrit en clair.
        # Une réponse qui dit « le code T signifie terminée » est juste ; elle
        # échouait parce qu'elle n'écrivait pas `'T'`.
        faits=(
            ("'t'", "`t`", '"t"', "« t »", "terminé"),
            ("'i'", "`i`", '"i"', "« i »", "interrompu"),
            ("'e'", "`e`", '"e"', "« e »", "erreur"),
            ("dictionnaire", "dictionary"),
        ),
        attendu="les trois codes et leur sens, attribués",
    ),
    # Son oracle a changé, et c'est la dette `G`. Il exigeait la sous-chaîne
    # « sentinelle » — le mot du dictionnaire et du produit, pas celui d'une
    # réponse juste — et il passait 3/3, donc rien ne l'obligeait. C'est
    # précisément ce qui le rendait dangereux : il aurait passé jusqu'au jour où
    # une reformulation juste l'aurait fait tomber, et on aurait lu une
    # régression là où il n'y en avait pas. La dette `F` a appris ce que coûte
    # une régression mal imputée ; on ne laisse pas un troisième oracle la
    # préparer.
    #
    # Il mesure désormais les MÊMES deux faits que `S4`, et il les IMPORTE
    # plutôt que de les recopier : c'est la même colonne, le même dictionnaire
    # et la même question, deux runners ne doivent pas pouvoir en juger
    # différemment. Le verdict strict garde l'ancien à côté, comme partout.
    Tour(
        cle="sens-puissance",
        fil="sens-puissance",
        message="que veut dire la colonne puissance_kw dans la source telemetrie ?",
        courte="c'est quoi puissance_kw dans telemetrie ?",
        longue=(
            "Peux-tu m'expliquer ce que représente la colonne puissance_kw dans la "
            "source telemetrie ?"
        ),
        source="telemetrie",
        fragments=("dictionnaire", "sentinelle"),
        faits=(
            SENTINELLE_N_EST_PAS_UNE_PUISSANCE,
            SENTINELLE_SORT_DES_AGREGATS,
            ("dictionnaire", "dictionary"),
        ),
        attendu="-1 n'est pas une puissance et sort des moyennes, attribué",
    ),
    # --- la réserve de forme, LEVÉE : la question n'a plus à nommer sa source -
    #
    # Cet oracle attendait l'INVENTAIRE, et il avait raison de l'attendre : la
    # cascade de ciblage ne savait reconnaître qu'un nom de source ou de table,
    # jamais un nom de colonne, et « que veut dire la colonne puissance_kw ? »
    # rendait donc la liste des tables. C'était écrit dans la documentation
    # comme une réserve de FORME — « la question doit nommer sa source ».
    #
    # Ce n'en était pas une : `puissance_kw` n'existe que dans une source sur
    # cinq, donc la question n'a jamais été ambiguë. L'oracle consacrait un trou
    # de la cascade (`introspection._cible`) en exigence d'utilisateur. Il
    # demande maintenant ce que la question demande : le sens.
    Tour(
        cle="reserve-de-forme",
        fil="reserve",
        message="que veut dire la colonne puissance_kw ?",
        courte="c'est quoi puissance_kw ?",
        longue="Pourrais-tu m'expliquer ce que représente la colonne puissance_kw ?",
        fragments=("source",),
        faits=(
            (
                "pas une puissance",
                "n'est pas une puissance",
                "aucune mesure",
                "absence de mesure",
                "rien remonté",
                "pas de mesure",
                "non mesuré",
                "sentinelle",
            ),
            ("kilowatt", "kw"),
        ),
        attendu="le sens de la colonne, sans que la source soit nommée",
    ),
)


@dataclass
class Releve:
    tour: Tour
    formulation: str
    reponse: str
    tableau: str
    noeuds: list[str]
    valeurs: list[float] = field(default_factory=list)
    verdict: str = ""
    pourquoi: str = ""
    # Le verdict de l'oracle d'AVANT desserrage. Hors de tout total.
    verdict_strict: str = ""
    appels_llm: int = 0
    duree_ms: int = 0

    @property
    def message(self) -> str:
        return phrase(self.tour, self.formulation)


def phrase(tour: Tour, formulation: str) -> str:
    """La phrase de ce tour dans cette formulation."""
    return {"canonique": tour.message, "courte": tour.courte, "longue": tour.longue}[formulation]


def juger(
    tour: Tour, reponse: ChatAnswer, valeurs: list[float], texte: str, liee: str = ""
) -> tuple[str, str]:
    """Le verdict qui fait foi : les chiffres, puis les FAITS.

    Un tour qui déclare des ``faits`` est jugé sur eux et non sur ses
    ``fragments`` : la disjonction remplace la conjonction de sous-chaînes.
    ``juger_strict`` garde l'ancien verdict, pour que l'écart entre les deux
    soit un chiffre et non une affirmation.
    """
    if reponse.error:
        return "échec", f"erreur : {reponse.error}"
    manquants = [c for c in tour.nombres if not any(abs(v - c) <= 0.5 for v in valeurs)]
    if manquants:
        return "échec", f"chiffre(s) absent(s) : {', '.join(f'{c:g}' for c in manquants)}"
    presents = [c for c in tour.interdits if any(abs(v - c) <= 0.5 for v in valeurs)]
    if presents:
        return "échec", f"chiffre du voisin : {', '.join(f'{c:g}' for c in presents)}"
    if tour.faits:
        absents = [f[0] for f in tour.faits if not porte_le_fait(texte, f)]
        if absents:
            return "échec", f"fait(s) absent(s) : {', '.join(absents)}"
    else:
        absents = [f for f in tour.fragments if f.lower() not in texte.lower()]
        if absents:
            return "échec", f"fragment(s) absent(s) : {', '.join(absents)}"
    if tour.liee and liee != tour.liee:
        return "échec", f"source liée : `{liee or '(aucune)'}` au lieu de `{tour.liee}`"
    return "conforme", tour.attendu


def juger_strict(tour: Tour, reponse: ChatAnswer, valeurs: list[float], texte: str) -> str:
    """Le verdict de l'oracle d'AVANT, conservé pour mesurer le desserrage.

    Il n'entre dans aucun total ; il n'existe que pour répondre à la seule
    question qui compte quand on desserre un oracle : combien de points
    viennent du desserrage, et combien du produit. Sans lui, les deux se
    confondent et le chiffre ne prouve plus rien.
    """
    if reponse.error:
        return "échec"
    if [c for c in tour.nombres if not any(abs(v - c) <= 0.5 for v in valeurs)]:
        return "échec"
    if [c for c in tour.interdits if any(abs(v - c) <= 0.5 for v in valeurs)]:
        return "échec"
    if [f for f in tour.fragments if f.lower() not in texte.lower()]:
        return "échec"
    return "conforme"


def poser(
    orchestrateur: Orchestrator,
    tour: Tour,
    formulation: str,
    fil: str,
    liees: dict[str, str],
) -> Releve:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        phrase(tour, formulation),
        conversation_id=fil,
        # Toujours une CHAÎNE, jamais None : c'est ce que l'API passe, et le
        # court-circuit de choix de source en dépend. La source retenue par le
        # tour précédent du même fil prime sur celle déclarée par le tour.
        source_de_travail=liees.get(tour.fil, tour.source),
    )
    duree = int((time.monotonic() - depart) * 1000)
    liees[tour.fil] = reponse.source_de_travail or liees.get(tour.fil, tour.source)
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    verdict, pourquoi = juger(tour, reponse, valeurs, texte, liees[tour.fil])
    return Releve(
        tour=tour,
        formulation=formulation,
        reponse=reponse.answer,
        tableau=tableau,
        noeuds=[s.node for s in reponse.trace],
        valeurs=valeurs,
        verdict=verdict,
        pourquoi=pourquoi,
        verdict_strict=juger_strict(tour, reponse, valeurs, texte),
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )


def rapport(releves: list[Releve], reglages) -> str:
    """Le tableau, agrégé par (tour, formulation) — une ligne par question posée.

    Agrégé et non ligne à ligne : à trois tirages sur trois formulations, le
    détail fait 144 lignes que personne ne lit. Ce qu'on veut voir est ce qui
    CHANGE d'une phrase à l'autre pour une même question, et les raisons des
    échecs, dédoublonnées.
    """
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    formulations = [f for f in FORMULATIONS if any(r.formulation == f for r in releves)]
    lignes = [
        "## Le parcours de démonstration, rejoué",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        "",
        f"**{conformes}/{len(releves)} tours conformes, "
        f"{sum(r.appels_llm for r in releves)} appels LLM.**",
        "",
    ]
    desserres = [r for r in releves if r.tour.faits]
    if desserres:
        stricts = sum(1 for r in desserres if r.verdict_strict == "conforme")
        larges = sum(1 for r in desserres if r.verdict == "conforme")
        lignes += [
            f"Sur les {len(desserres)} tours dont l'oracle a été desserré "
            f"(`{'`, `'.join(dict.fromkeys(r.tour.cle for r in desserres))}`) : "
            f"**{stricts}/{len(desserres)}** avec l'oracle d'avant, "
            f"**{larges}/{len(desserres)}** avec celui d'après. "
            f"L'écart est ce que le desserrage donne, et rien d'autre.",
            "",
        ]
    if len(formulations) > 1:
        lignes += ["Par formulation :", ""]
        for formulation in formulations:
            lot = [r for r in releves if r.formulation == formulation]
            bons = sum(1 for r in lot if r.verdict == "conforme")
            lignes.append(f"- **{formulation}** : {bons}/{len(lot)}")
        lignes.append("")
    lignes += [
        "| tour | formulation | message | nœuds | appels | score | strict | ce qui a décidé |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cle in dict.fromkeys(r.tour.cle for r in releves):
        for formulation in formulations:
            lot = [r for r in releves if r.tour.cle == cle and r.formulation == formulation]
            if not lot:
                continue
            bons = sum(1 for r in lot if r.verdict == "conforme")
            noeuds = " / ".join(dict.fromkeys(" → ".join(r.noeuds) for r in lot))
            echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
            pourquoi = " ; ".join(echecs) if echecs else lot[0].pourquoi
            appels = sum(r.appels_llm for r in lot)
            strict = (
                f"{sum(1 for r in lot if r.verdict_strict == 'conforme')}/{len(lot)}"
                if lot[0].tour.faits
                else "—"
            )
            lignes.append(
                f"| `{cle}` | {formulation} | {' '.join(lot[0].message.split())} | {noeuds} "
                f"| {appels} | **{bons}/{len(lot)}** | {strict} | {pourquoi} |"
            )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--seulement", nargs="*", default=None)
    # Le défaut joue les TROIS formulations, et c'est le sujet : un parcours
    # qui ne rejoue que les phrases qu'on a écrites mesure ces phrases-là.
    parseur.add_argument("--formulations", nargs="*", default=list(FORMULATIONS))
    parseur.add_argument("--tirages", type=int, default=1)
    args = parseur.parse_args()

    formulations = [f for f in FORMULATIONS if f in args.formulations]
    if not formulations:
        parseur.error(f"formulations connues : {', '.join(FORMULATIONS)}")

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")

    orchestrateur = Orchestrator(
        settings=reglages,
        model=ModeleCompteur(build_model(reglages)),
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    suffixe = uuid.uuid4().hex[:8]
    releves: list[Releve] = []
    tours = [t for t in PARCOURS if not args.seulement or t.cle in args.seulement]
    total = len(tours) * len(formulations) * args.tirages
    numero = 0
    for tirage in range(1, args.tirages + 1):
        for formulation in formulations:
            # Un fil NEUF par formulation ET par tirage : le verrou de source se
            # mesure sur des tours enchaînés, et rejouer la même conversation
            # lui donnerait le contexte du tirage précédent. Ce serait mesurer
            # la mémoire du fil, pas la phrase.
            liees: dict[str, str] = {}
            for tour in tours:
                numero += 1
                message = phrase(tour, formulation)
                print(f"[{numero}/{total}] {tour.cle}·{formulation}·{tirage} — « {message} »")
                releve = poser(
                    orchestrateur,
                    tour,
                    formulation,
                    f"parcours-{suffixe}-{tirage}-{formulation}-{tour.fil}",
                    liees,
                )
                releves.append(releve)
                print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
                print(f"    réponse : {' '.join(releve.reponse.split())[:220]}\n", flush=True)

    texte = rapport(releves, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **{k: v for k, v in r.__dict__.items() if k != "tour"},
                        "tour": r.tour.cle,
                        "message": r.message,
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
