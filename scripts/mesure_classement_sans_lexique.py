"""Dix façons de demander le MÊME palmarès — et ce que le SQL en fait.

C31 a ajouté au prompt de l'agent SQL une règle qui énumère des tournures
(« les trois plus… », « le plus gros… », « classe par… »). Elle est annoncée
0/5 → 5/5, et elle l'est : sur LA phrase qui l'a motivée. Remesurée sur une
seconde formulation de la même demande — « les trois stations les plus
sollicitées » — elle rend 0/3. « Les plus sollicitées » ne figure pas dans la
liste.

Ce runner pose la même demande de DIX façons qu'un utilisateur emploierait, et
lit trois choses dans chaque tour :

- **le SQL**, jugé par ``agents/retrieval/classement`` : toute expression du
  ORDER BY figure-t-elle dans le SELECT ? C'est la propriété qu'on veut, dite
  sans regarder la question — donc mesurable quelle que soit la phrase ;
- **ce que l'utilisateur voit** : la réponse ET le tableau doivent porter les
  trois stations et leurs trois chiffres ;
- **ce que la PHRASE dit de la grandeur classée** : elle doit la nommer. C'est
  l'exigence ajoutée par C33, et elle vaut pour les dix.

**L'oracle a été arbitré, et voici ce qui a bougé.** Deux formulations sur dix
classaient sur l'énergie plutôt que sur le nombre de sessions, et l'oracle les
comptait fausses. Elles ne le sont pas : « qui charge le plus ? » et « où est-ce
qu'on recharge le plus ? » ne nomment aucune unité, et le verbe *charger* porte
une quantité d'énergie autant qu'un événement. Les deux lectures sont
légitimes, et les trois stations en tête sont d'ailleurs les MÊMES des deux
côtés — seul le chiffre qui les accompagne change.

Ce que le produit doit donc à l'utilisateur n'est pas une grandeur en
particulier, c'est de DIRE laquelle il a classée. D'où le double mouvement :

- les HUIT formulations qui nomment une fréquence (sollicitation,
  fréquentation, activité, « tourner », « top », « palmarès ») ou l'unité
  elle-même (« sessions », « recharges ») : leur oracle reste les 3 stations et
  les 3 comptes de sessions, et gagne l'exigence de la grandeur nommée ;
- `F03` et `F07`, dont le critère est le verbe *charger* : les 3 stations et
  les comptes **OU** les énergies, plus l'exigence de la grandeur nommée.

Desserré sur deux lignes, resserré sur les dix. Le tableau AVANT/APRÈS de la
campagne est dans `docs/sources-de-demonstration.md` : un oracle qu'on desserre
sans le montrer est un chiffre qu'on s'offre.

    uv run python scripts/mesure_classement_sans_lexique.py
    uv run python scripts/mesure_classement_sans_lexique.py --tirages 3 --markdown /tmp/c.md
    uv run python scripts/mesure_classement_sans_lexique.py --oracle-davant

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, le
catalogue semé, Postgres joignable, le serveur LLM en place.
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
from data_analyst_agent.agents.retrieval.classement import (
    grandeur_du_classement,
    grandeurs_non_projetees,
)
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

SOURCE = "exploitation"

# Les trois stations en tête de `exploitation`, et leurs chiffres selon DEUX
# grandeurs. Les trois stations sont les mêmes des deux côtés, et dans le même
# ordre : ST-097, ST-029, ST-016. Ce fait est ce qui rend l'arbitrage de
# l'oracle possible sans rien céder sur l'exactitude — quand la question ne dit
# pas quelle grandeur classer, la RÉPONSE reste la même liste de stations, et
# seul le chiffre qui l'accompagne change.
STATIONS = ("ST-097", "ST-029", "ST-016")
COMPTES = (1015.0, 911.0, 872.0)  # COUNT(sessions)
ENERGIES = (37957.56, 33821.99, 32178.53)  # SUM(energie_kwh)


@dataclass(frozen=True)
class Formulation:
    """Une façon de demander le palmarès, et ce que son oracle accepte.

    ``energie_legitime`` est un ARBITRAGE, pas une tolérance. Il ne vaut que
    pour les formulations dont le critère est le verbe **charger / recharger**,
    dont l'objet est une QUANTITÉ d'énergie autant qu'un événement : « qui
    charge le plus ? » n'a pas de réponse unique, et classer sur
    `SUM(energie_kwh)` en est une lecture légitime. Les huit autres nomment une
    notion de FRÉQUENCE — sollicitation, fréquentation, activité, « tourner »,
    « top », « palmarès » — ou l'unité elle-même (« sessions », « recharges ») :
    leur oracle reste le nombre de sessions, et rien n'y est desserré.

    ``pourquoi`` est écrit ici et non dans un document à côté : un oracle qu'on
    desserre sans dire pourquoi, sur la ligne où on le desserre, est un chiffre
    qu'on s'offre.
    """

    cle: str
    message: str
    energie_legitime: bool = False
    pourquoi: str = ""


# Dix formulations DISTINCTES, écrites comme un utilisateur parle — pas comme
# un gabarit se décline. Aucune n'annonce « classement » ; deux seulement
# emploient un superlatif que la règle de C31 énumère.
FORMULATIONS: tuple[Formulation, ...] = (
    Formulation(
        "F01",
        "quelles sont les trois stations avec le plus de sessions ? donne leur code et leur nom",
    ),
    Formulation("F02", "les trois stations les plus sollicitées : donne leur code et leur nom"),
    Formulation(
        "F03",
        "qui charge le plus ? je veux les 3 premières stations, leur code et leur nom",
        energie_legitime=True,
        pourquoi=(
            "« charger » n'a pas d'unité : son objet est de l'énergie autant qu'un "
            "événement, et la question ne nomme ni sessions ni recharges"
        ),
    ),
    Formulation("F04", "le top 3 des stations, avec leur code et leur nom"),
    Formulation("F05", "quelles stations tournent le plus ? les 3 premières, code et nom"),
    Formulation(
        "F06", "classe-moi les stations par activité et garde les 3 premières, code et nom"
    ),
    Formulation(
        "F07",
        "où est-ce qu'on recharge le plus ? donne-moi le code et le nom des 3 stations en tête",
        energie_legitime=True,
        pourquoi=(
            "« on recharge le plus » se lit en fréquence comme en volume ; aucune "
            "unité n'est nommée, et le volume est la lecture la plus directe du verbe"
        ),
    ),
    Formulation(
        "F08", "je cherche les 3 stations les plus fréquentées, avec leur code et leur nom"
    ),
    Formulation("F09", "palmarès des stations : les 3 premières, leur code et leur nom"),
    Formulation(
        "F10",
        "sur quelles stations y a-t-il eu le plus de recharges ? les 3 premières, code et nom",
    ),
)


@dataclass
class Tirage:
    cle: str
    message: str
    numero: int
    sql: str = ""
    absentes: tuple[str, ...] = ()
    reponse: str = ""
    projette: bool = False
    oracle: bool = False
    # La grandeur sur laquelle le SQL a classé, lue sur le SQL, et le fait que
    # la PHRASE la nomme. La phrase et non le texte entier : la grandeur figure
    # de toute façon en tête de colonne du tableau, et un oracle qui lirait le
    # tableau serait satisfait sans que l'utilisateur ait rien appris.
    grandeur: str = ""
    grandeur_dite: bool = False
    pourquoi: str = ""
    appels_llm: int = 0
    duree_ms: int = 0

    @property
    def conforme(self) -> bool:
        return self.projette and self.oracle and self.grandeur_dite


def poser(
    orchestrateur: Orchestrator,
    formulation: Formulation,
    numero: int,
    oracle_davant: bool = False,
) -> Tirage:
    compteur = orchestrateur.model
    avant = compteur.appels if isinstance(compteur, ModeleCompteur) else 0
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        formulation.message,
        conversation_id=f"classement-{uuid.uuid4().hex[:8]}",
        source_de_travail=SOURCE,
    )
    duree = int((time.monotonic() - depart) * 1000)
    # Le nœud `retrieval` porte la requête retenue dans son détail — c'est la
    # seule trace du SQL qui remonte jusqu'à l'appelant, et elle suffit : on
    # juge ce qui a été EXÉCUTÉ, pas ce qui a été tenté.
    sql = next((s.detail for s in reponse.trace if s.node == "retrieval"), "")
    tableau = " ".join(a.data for a in reponse.artifacts if a.mime == "application/json")
    texte = f"{reponse.answer}\n{tableau}"
    valeurs = nombres(texte)
    absentes = tuple(grandeurs_non_projetees(sql))
    classement = grandeur_du_classement(sql)
    grandeur = classement.grandeurs[0] if classement is not None else ""
    tirage = Tirage(
        cle=formulation.cle,
        message=formulation.message,
        numero=numero,
        sql=" ".join(sql.split()),
        absentes=absentes,
        reponse=" ".join(reponse.answer.split()),
        projette=not absentes,
        grandeur=grandeur,
        # L'oracle d'AVANT n'exigeait rien de la phrase : on le rejoue tel quel
        # sur demande, pour que le tableau AVANT/APRÈS compare deux mesures et
        # non deux définitions.
        grandeur_dite=True
        if oracle_davant
        else bool(grandeur) and grandeur.lower() in reponse.answer.lower(),
        appels_llm=(compteur.appels - avant) if isinstance(compteur, ModeleCompteur) else 0,
        duree_ms=duree,
    )
    if reponse.error:
        tirage.pourquoi = f"erreur : {reponse.error}"
        return tirage
    lectures = {"les comptes de sessions": COMPTES}
    if formulation.energie_legitime and not oracle_davant:
        lectures["les énergies délivrées"] = ENERGIES
    tenues = [
        nom
        for nom, attendus in lectures.items()
        if all(any(abs(v - c) <= 0.5 for v in valeurs) for c in attendus)
    ]
    absents = [s for s in STATIONS if s.lower() not in texte.lower()]
    tirage.oracle = bool(tenues) and not absents
    raisons = []
    if absentes:
        raisons.append("ORDER BY hors SELECT : " + ", ".join(absentes))
    if absents:
        raisons.append("station(s) absente(s) : " + ", ".join(absents))
    if not tenues:
        raisons.append("aucune lecture tenue : " + ", ".join(lectures))
    if not tirage.grandeur_dite:
        raisons.append(
            f"grandeur non nommée dans la phrase : `{grandeur}`"
            if grandeur
            else "aucun classement dans le SQL"
        )
    tirage.pourquoi = " ; ".join(raisons) or f"les trois stations et {tenues[0]}"
    return tirage


def rapport(tirages: list[Tirage], reglages, titre: str) -> str:
    par_cle: dict[str, list[Tirage]] = {}
    for t in tirages:
        par_cle.setdefault(t.cle, []).append(t)
    conformes = sum(1 for t in tirages if t.conforme)
    projette = sum(1 for t in tirages if t.projette)
    lignes = [
        f"## {titre}",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`), source `{SOURCE}`",
        "",
        f"**{conformes}/{len(tirages)} tirages conformes** "
        f"(SQL en règle : {projette}/{len(tirages)}), "
        f"{sum(t.appels_llm for t in tirages)} appels LLM.",
        "",
        "| clé | formulation | SQL en règle | grandeur classée | dite | oracle "
        "| score | ce qui a décidé |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cle, lot in par_cle.items():
        bons = sum(1 for t in lot if t.conforme)
        regle = sum(1 for t in lot if t.projette)
        pourquoi = "; ".join(sorted({t.pourquoi for t in lot if not t.conforme})) or lot[0].pourquoi
        grandeurs = " / ".join(dict.fromkeys(f"`{t.grandeur}`" for t in lot if t.grandeur)) or "—"
        lignes.append(
            f"| `{cle}` | {lot[0].message} | {regle}/{len(lot)} | {grandeurs} "
            f"| {sum(1 for t in lot if t.grandeur_dite)}/{len(lot)} "
            f"| {sum(1 for t in lot if t.oracle)}/{len(lot)} | **{bons}/{len(lot)}** | {pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--titre", default="Le classement, sans parier sur les mots")
    parseur.add_argument("--seulement", nargs="*", default=None)
    # Rejoue l'oracle d'AVANT C33 : le nombre de sessions pour les dix, et rien
    # d'exigé de la phrase. Sert à comparer deux MESURES et non deux
    # définitions — sans lui, le tableau avant/après mélangerait ce que le
    # produit a changé et ce que l'oracle a changé.
    parseur.add_argument("--oracle-davant", action="store_true")
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
    choisies = [f for f in FORMULATIONS if not args.seulement or f.cle in args.seulement]
    tirages: list[Tirage] = []
    for formulation in choisies:
        for numero in range(1, args.tirages + 1):
            print(f"[{formulation.cle}·{numero}] « {formulation.message} »")
            tirage = poser(orchestrateur, formulation, numero, args.oracle_davant)
            tirages.append(tirage)
            etat = "conforme" if tirage.conforme else "ÉCHEC"
            print(f"    → {etat} — {tirage.pourquoi} ({tirage.duree_ms} ms)")
            print(f"    sql : {tirage.sql[:200]}\n")

    texte = rapport(tirages, reglages, args.titre)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps([t.__dict__ for t in tirages], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
