"""Dix façons de dire « on travaille sur cette source » — et ce qui se lie vraiment.

Le défaut : le court-circuit déterministe de ``_choix_de_source`` ne reconnaît
qu'un message RÉDUIT au nom d'une source (« telemetrie », « facturation,
vas-y »). La même intention dite en une phrase — « J'aimerais reprendre le
travail sur la source exploitation, peux-tu la charger ? » — partait à la
récupération, qui répondait « Je n'ai pas interrogé la source pour cette
question… Reformule ». L'utilisateur qui demande poliment était éconduit.

Ce runner pose la demande de DIX façons qu'un utilisateur emploierait —
familières, polies, longues, avec ou sans le mot « source » — sur les cinq
sources du catalogue de démonstration, et il mesure TROIS choses dans le même
mouvement :

- **volet ouverture** : la source est-elle liée au fil, et l'accusé de
  réception porte-t-il son volume ? Une ouverture qui répond sans lier n'a
  rien fait : le tour suivant redemandera la source ;
- **volet contre-épreuve** : la MÊME phrase d'ouverture, suivie d'une vraie
  question. Elle ne doit PAS se faire avaler par la liaison — c'est le risque
  propre à tout second chemin, et le seul moyen de savoir ce qu'il coûte est
  de le mesurer au lieu de l'espérer ;
- **volet verrou** : une fois la source liée par une phrase, le verrou de
  source de C15 tient-il encore ? La question qui ne nomme personne répond sur
  la source liée, et celle qui nomme l'autre bascule en l'annonçant.

**Les dix formulations ont été écrites APRÈS le correctif**, et jamais relues
pendant sa mise au point. C'est la seule façon de savoir si on a réparé
l'intention ou la phrase : ce produit a déjà payé deux fois un lexique de
tournures, et un correctif qu'on ajuste sur ses propres exemples rend le
chiffre qu'on lui demande.

    uv run python scripts/mesure_ouverture_de_source.py
    uv run python scripts/mesure_ouverture_de_source.py --tirages 3 --markdown /tmp/o.md
    uv run python scripts/mesure_ouverture_de_source.py --volets ouverture

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

from mesure_surface_conversationnelle import ModeleCompteur, nombres

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator


@dataclass(frozen=True)
class Tour:
    """Un tour, et ce qui décide de son verdict.

    ``liee`` est la source qui doit être liée au fil APRÈS le tour : c'est
    l'oracle propre à ce runner, et il ne se lit pas dans le texte. Une réponse
    peut nommer une source sans l'avoir retenue, et c'est exactement le faux
    succès qu'on veut voir — ``ouverture-exploitation·longue`` du parcours
    passait parce que l'agent système avait énuméré les CINQ sources, « 6
    table(s) » comprise, sans rien lier.
    """

    cle: str
    volet: str
    message: str
    fil: str = ""
    liee: str = ""
    nombres: tuple[float, ...] = ()
    fragments: tuple[str, ...] = ()
    interdits: tuple[float, ...] = ()
    # Le nœud par lequel le tour DOIT être passé. Il ne sert qu'à la
    # contre-épreuve, et il y est indispensable : l'accusé de réception d'une
    # source porte son volume table par table, donc « 1 200 factures » et
    # « 547 200 relevés » s'y trouvent déjà. Un oracle qui ne lit que les
    # chiffres déclare donc conforme un tour qui a AVALÉ la question et
    # répondu par l'accueil de la source. Mesuré : 2 tours sur 4 passaient
    # ainsi, et seul celui dont l'oracle ne figure pas dans l'accueil
    # (l'énergie totale) montrait le défaut.
    noeuds_requis: tuple[str, ...] = ()
    attendu: str = ""


VOLETS = ("ouverture", "contre-epreuve", "verrou")

# --- volet 1 : dix ouvertures, cinq sources ----------------------------------
#
# Trois emploient le mot « source » (O02, O08, O10), sept non. Deux tiennent en
# quatre mots, une en vingt-trois. Aucune ne se réduit au nom de la source :
# ce cas-là est déjà tenu par le court-circuit déterministe, il est mesuré par
# le parcours, et il n'y a rien à y réparer.
#
# Les oracles chiffrés sont le VOLUME relevé dans la source, que l'accusé de
# réception porte (``FaitsDeSource.en_clair``) : 6 tables pour `exploitation`,
# 3 pour `telemetrie` et `facturation`, et pour les deux sources à table unique
# le nombre de LIGNES — « 1 table(s) » se retrouverait par accident dans
# n'importe quelle phrase.
OUVERTURES: tuple[Tour, ...] = (
    Tour(
        cle="O01",
        volet="ouverture",
        message="on bosse sur exploitation aujourd'hui, tu peux me la sortir ?",
        liee="exploitation",
        nombres=(6.0,),
        fragments=("exploitation",),
        attendu="exploitation liée, 6 tables annoncées",
    ),
    Tour(
        cle="O02",
        volet="ouverture",
        message="Bonjour, pourriez-vous charger la source facturation s'il vous plaît ?",
        liee="facturation",
        nombres=(3.0,),
        fragments=("facturation",),
        attendu="facturation liée, 3 feuilles annoncées",
    ),
    Tour(
        cle="O03",
        volet="ouverture",
        message="telemetrie, si tu veux bien",
        liee="telemetrie",
        nombres=(3.0,),
        fragments=("telemetrie",),
        attendu="telemetrie liée, 3 tables annoncées",
    ),
    Tour(
        cle="O04",
        volet="ouverture",
        message=("Je voudrais qu'on se mette sur interventions pour la suite de notre échange."),
        liee="interventions",
        nombres=(900.0,),
        fragments=("interventions",),
        attendu="interventions liée, 900 lignes annoncées",
    ),
    Tour(
        cle="O05",
        volet="ouverture",
        message="mets-moi sur referentiel",
        liee="referentiel",
        nombres=(150.0,),
        fragments=("referentiel",),
        attendu="referentiel liée, 150 lignes annoncées",
    ),
    Tour(
        cle="O06",
        volet="ouverture",
        message="Pourrais-tu ouvrir exploitation ? Je vais avoir plusieurs questions dessus.",
        liee="exploitation",
        nombres=(6.0,),
        fragments=("exploitation",),
        attendu="exploitation liée, 6 tables annoncées",
    ),
    Tour(
        cle="O07",
        volet="ouverture",
        message="alors, on va regarder du côté de telemetrie si ça te va",
        liee="telemetrie",
        nombres=(3.0,),
        fragments=("telemetrie",),
        attendu="telemetrie liée, 3 tables annoncées",
    ),
    Tour(
        cle="O08",
        volet="ouverture",
        message=(
            "Je souhaite reprendre mon travail de la semaine dernière sur la source "
            "facturation ; peux-tu la rendre active ?"
        ),
        liee="facturation",
        nombres=(3.0,),
        fragments=("facturation",),
        attendu="facturation liée, 3 feuilles annoncées",
    ),
    Tour(
        cle="O09",
        volet="ouverture",
        message="passe-moi la main sur interventions stp",
        liee="interventions",
        nombres=(900.0,),
        fragments=("interventions",),
        attendu="interventions liée, 900 lignes annoncées",
    ),
    Tour(
        cle="O10",
        volet="ouverture",
        message=(
            "Auriez-vous l'obligeance de bien vouloir sélectionner la source exploitation "
            "comme base de travail pour cette session ?"
        ),
        liee="exploitation",
        nombres=(6.0,),
        fragments=("exploitation",),
        attendu="exploitation liée, 6 tables annoncées",
    ),
)

# --- volet 2 : la contre-épreuve ---------------------------------------------
#
# La même intention d'ouverture, SUIVIE d'une vraie question. Un second chemin
# de liaison qui avale ces quatre tours aurait réparé trois questions sur
# quarante-huit en en cassant quatre : il faut le savoir avant de le garder, et
# non après. La source doit être liée ET le chiffre rendu.
CONTRE_EPREUVES: tuple[Tour, ...] = (
    Tour(
        cle="C01",
        volet="contre-epreuve",
        message="on bosse sur exploitation, combien de sessions en tout ?",
        liee="exploitation",
        nombres=(48000.0,),
        noeuds_requis=("retrieval",),
        attendu="48 000 — la question n'est pas avalée par la liaison",
    ),
    Tour(
        cle="C02",
        volet="contre-epreuve",
        message=(
            "Pourriez-vous charger la source facturation et me dire combien de factures "
            "elle contient ?"
        ),
        liee="facturation",
        nombres=(1200.0,),
        noeuds_requis=("retrieval",),
        attendu="1 200 — la question n'est pas avalée par la liaison",
    ),
    Tour(
        cle="C03",
        volet="contre-epreuve",
        message="mets-moi sur telemetrie ; il y a combien de lignes dans releves_puissance ?",
        liee="telemetrie",
        nombres=(547200.0,),
        noeuds_requis=("retrieval",),
        attendu="547 200 — la question n'est pas avalée par la liaison",
    ),
    Tour(
        cle="C04",
        volet="contre-epreuve",
        message=(
            "Auriez-vous l'obligeance de sélectionner exploitation, puis de me donner "
            "l'énergie totale délivrée en kWh ?"
        ),
        liee="exploitation",
        nombres=(1757519.23,),
        noeuds_requis=("retrieval",),
        attendu="1 757 519,23 — la question n'est pas avalée par la liaison",
    ),
)

# --- volet 3 : le verrou, après une liaison par PHRASE -----------------------
#
# C15 et C22 protègent la source de travail contre une bascule silencieuse. Ils
# sont mesurés sur une liaison par le court-circuit déterministe ; ce volet les
# rejoue sur une liaison obtenue par une phrase, parce que c'est le chemin
# qu'on ajoute. Trois tours ENCHAÎNÉS dans le même fil.
VERROU: tuple[Tour, ...] = (
    Tour(
        cle="V1-ouvre",
        volet="verrou",
        fil="verrou",
        message="Pourrais-tu te mettre sur la source exploitation, s'il te plaît ?",
        liee="exploitation",
        nombres=(6.0,),
        fragments=("exploitation",),
        attendu="exploitation liée par une phrase",
    ),
    Tour(
        cle="V2-tient",
        volet="verrou",
        fil="verrou",
        message="quelle est l'énergie totale en kWh ?",
        liee="exploitation",
        nombres=(1757519.23,),
        interdits=(531098.10,),
        noeuds_requis=("retrieval",),
        attendu="1 757 519,23 — le verrou tient, la question ne nomme personne",
    ),
    Tour(
        cle="V3-bascule",
        volet="verrou",
        fil="verrou",
        message="et dans facturation, quelle est l'énergie totale en kWh ?",
        liee="facturation",
        nombres=(531098.10,),
        interdits=(1757519.23,),
        fragments=("facturation",),
        noeuds_requis=("retrieval",),
        attendu="531 098,10 après une bascule annoncée",
    ),
)

TOURS: tuple[Tour, ...] = (*OUVERTURES, *CONTRE_EPREUVES, *VERROU)


@dataclass
class Releve:
    tour: Tour
    tirage: int
    reponse: str
    tableau: str
    noeuds: list[str]
    source_liee: str
    valeurs: list[float] = field(default_factory=list)
    verdict: str = ""
    pourquoi: str = ""
    appels_llm: int = 0
    duree_ms: int = 0


def juger(tour: Tour, reponse: ChatAnswer, valeurs: list[float], texte: str, liee: str):
    """Le verdict d'un tour : le chiffre, les interdits, les fragments, ET la liaison.

    La liaison est vérifiée EN DERNIER et jamais omise : c'est le seul oracle
    qui distingue une ouverture réussie d'une réponse bien tournée qui n'a rien
    retenu.
    """
    if reponse.error:
        return "échec", f"erreur : {reponse.error}"
    manquants = [c for c in tour.nombres if not any(abs(v - c) <= 0.5 for v in valeurs)]
    if manquants:
        return "échec", f"chiffre(s) absent(s) : {', '.join(f'{c:g}' for c in manquants)}"
    presents = [c for c in tour.interdits if any(abs(v - c) <= 0.5 for v in valeurs)]
    if presents:
        return "échec", f"chiffre du voisin : {', '.join(f'{c:g}' for c in presents)}"
    absents = [f for f in tour.fragments if f.lower() not in texte.lower()]
    if absents:
        return "échec", f"fragment(s) absent(s) : {', '.join(absents)}"
    noeuds = [s.node for s in reponse.trace]
    sautes = [n for n in tour.noeuds_requis if n not in noeuds]
    if sautes:
        return "échec", f"question avalée — nœud(s) non atteint(s) : {', '.join(sautes)}"
    if tour.liee and liee != tour.liee:
        return "échec", f"source liée : `{liee or '(aucune)'}` au lieu de `{tour.liee}`"
    return "conforme", tour.attendu


def poser(
    orchestrateur: Orchestrator, tour: Tour, tirage: int, fil: str, liees: dict[str, str]
) -> Releve:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        tour.message,
        conversation_id=fil,
        # Toujours une CHAÎNE, jamais None — c'est ce que l'API passe, et le
        # court-circuit déterministe en dépend (cf. le piège de protocole
        # documenté dans docs/sources-de-demonstration.md).
        source_de_travail=liees.get(tour.fil or tour.cle, ""),
    )
    duree = int((time.monotonic() - depart) * 1000)
    liee = reponse.source_de_travail or ""
    liees[tour.fil or tour.cle] = liee
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    verdict, pourquoi = juger(tour, reponse, valeurs, texte, liee)
    return Releve(
        tour=tour,
        tirage=tirage,
        reponse=" ".join(reponse.answer.split()),
        tableau=tableau,
        noeuds=[s.node for s in reponse.trace],
        source_liee=liee,
        valeurs=valeurs,
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
    ]
    volets = [v for v in VOLETS if any(r.tour.volet == v for r in releves)]
    if len(volets) > 1:
        lignes += ["Par volet :", ""]
        for volet in volets:
            lot = [r for r in releves if r.tour.volet == volet]
            bons = sum(1 for r in lot if r.verdict == "conforme")
            appels = sum(r.appels_llm for r in lot)
            lignes.append(f"- **{volet}** : {bons}/{len(lot)}, {appels} appels LLM")
        lignes.append("")
    lignes += [
        "| clé | message | nœuds | appels | source liée | score | ce qui a décidé |",
        "|---|---|---|---|---|---|---|",
    ]
    for cle in dict.fromkeys(r.tour.cle for r in releves):
        lot = [r for r in releves if r.tour.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "conforme")
        noeuds = " / ".join(dict.fromkeys(" → ".join(r.noeuds) for r in lot))
        liees = " / ".join(dict.fromkeys(r.source_liee or "(aucune)" for r in lot))
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        pourquoi = " ; ".join(echecs) if echecs else lot[0].pourquoi
        lignes.append(
            f"| `{cle}` | {' '.join(lot[0].tour.message.split())} | {noeuds} "
            f"| {sum(r.appels_llm for r in lot)} | {liees} | **{bons}/{len(lot)}** | {pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--titre", default="L'ouverture polie, et ce qui se lie")
    parseur.add_argument("--volets", nargs="*", default=list(VOLETS))
    parseur.add_argument("--seulement", nargs="*", default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")
    orchestrateur = Orchestrator(
        settings=reglages,
        model=ModeleCompteur(build_model(reglages)),
        catalog=load_catalog(reglages.catalog_path),
        registry=Registry.load(reglages.models_registry_path),
    )
    tours = [
        t
        for t in TOURS
        if t.volet in args.volets and (not args.seulement or t.cle in args.seulement)
    ]
    suffixe = uuid.uuid4().hex[:8]
    releves: list[Releve] = []
    total = len(tours) * args.tirages
    numero = 0
    for tirage in range(1, args.tirages + 1):
        # Un fil NEUF par tirage : le volet verrou enchaîne trois tours, et
        # rejouer la même conversation mesurerait la mémoire du fil.
        liees: dict[str, str] = {}
        for tour in tours:
            numero += 1
            print(f"[{numero}/{total}] {tour.cle}·{tirage} — « {tour.message} »")
            fil = f"ouverture-{suffixe}-{tirage}-{tour.fil or tour.cle}"
            releve = poser(orchestrateur, tour, tirage, fil, liees)
            releves.append(releve)
            print(f"    → {releve.verdict} ({releve.pourquoi}) — {releve.duree_ms} ms")
            print(f"    liée : {releve.source_liee or '(aucune)'} | {releve.reponse[:180]}\n")

    texte = rapport(releves, reglages, args.titre)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **{k: v for k, v in r.__dict__.items() if k != "tour"},
                        "cle": r.tour.cle,
                        "volet": r.tour.volet,
                        "message": r.tour.message,
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
