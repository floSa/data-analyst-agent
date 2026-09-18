"""Six fils où une prédiction en attente croise le reste de la conversation.

Ce runner joue des **FILS**, pas des messages isolés, et c'est sa raison d'être :
les défauts qu'il mesure n'existent qu'à partir du deuxième tour. Une prédiction
restée en attente de features change ce que le tour SUIVANT peut faire — et
c'est ce changement-là qu'on relève.

Ce qu'il reporte d'un tour au suivant est exactement ce que la route ``/chat``
reporte (cf. `api/app.py`) : ``source_de_travail``, ``echange_precedent``,
``pending``, ``workspace_root``. Le report est celui de
`scripts/releve_des_parcours.py`, et il en vient : ``poser`` et le mouchard
d'appels d'outil sont importés de là plutôt que réécrits, pour que les deux
runners ne puissent pas diverger sur la façon de tenir un fil.

**Six fils, dont deux témoins.** Les quatre premiers portent les défauts ; les
deux derniers portent ce qu'on ne doit PAS casser en les réparant — un « oui »
qui complète une prédiction, et un tableau rappelé sur un fil sans prédiction
en attente. Un correctif qui verdit a, b, c, d et rougit e ou f n'est pas un
correctif : c'est un déplacement.

Chaque tour porte son attendu, et l'attendu est une PROPRIÉTÉ de la trace ou de
la réponse — jamais un texte à comparer. Le moteur ne rend pas deux fois la même
phrase ; il rend deux fois le même parcours, ou il ne le rend pas.

    uv run python scripts/mesure_fils_de_prediction.py
    uv run python scripts/mesure_fils_de_prediction.py --tirages 3 --json avant.json
    uv run python scripts/mesure_fils_de_prediction.py --fils a c

Prérequis : ``DAA_CATALOG_PATH=sources/metier/catalogue.yaml``, le catalogue
semé, Postgres joignable, le serveur LLM en place. Séquentiel par construction :
ce moteur ne rend pas la même chose sous charge concurrente.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from releve_des_parcours import Message, ModeleMouchard, Releve, poser

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

# --- ce qu'on sait lire dans un tour -----------------------------------------


def _noeud(releve: Releve, nom: str) -> str:
    """Le détail du nœud ``nom``, ou ``""`` s'il n'a pas été traversé."""
    for n in releve.noeuds:
        if n.nom == nom:
            return n.detail
    return ""


def _sest_retire_pour_la_prediction(releve: Releve, nom: str) -> bool:
    """Ce nœud s'est-il retiré en invoquant la prédiction en attente ?

    C'est la signature EXACTE du court-circuit : le détail qu'il écrit dans la
    trace. On ne la devine pas depuis l'absence du nœud — ``system`` et
    ``rappel`` sont toujours traversés, ils laissent seulement une ligne qui
    dit qu'ils passent la main.
    """
    return "prédiction en attente" in _noeud(releve, nom)


def _outils(releve: Releve) -> list[str]:
    return [a.outil for a in releve.outils]


def _un_rappel_a_servi(releve: Releve) -> bool:
    """Le nœud de rappel a retrouvé l'artefact ET en a fait quelque chose.

    Trois signatures, et la troisième est venue avec le calcul sur un tableau.
    Les deux premières sont le NOM de l'outil, écrit dans la trace quand le nœud
    sert lui-même — une lecture, un refus, une formulation. La troisième est le
    REJEU : quand le code tourne, le tour est rendu comme une analyse, et le
    détail du nœud devient « rejeu de resultat_1 — 1 essai(s), statut ok ». Le
    nom de l'outil n'y est plus.

    Lire les trois n'est pas desserrer l'oracle, c'est le recentrer sur ce qu'il
    mesure : « le rappel reste ARMÉ et sert l'artefact ». Un rejeu en est une
    preuve plus forte qu'un appel d'outil — il dit que l'artefact a été
    retrouvé, ET qu'on s'en est servi jusqu'à l'exécution. Ne chercher que le
    nom de l'outil mesurait la façon dont le nœud écrit sa trace, pas ce qu'il
    a fait ; `a` et `f` sont tombés à 0/3 sur cette signature-là, sur des tours
    qui aboutissaient.
    """
    detail = _noeud(releve, "rappel")
    appele = "lire_un_artefact" in detail or "rejouer_un_code" in detail
    return bool(detail) and (appele or detail.startswith("rejeu de"))


# La contradiction du défaut 3, telle qu'elle se lit dans la réponse SERVIE :
# une phrase qui nie un artefact et l'énumère comme disponible dans la même
# haleine. On la cherche sur le nom NU, parenthèse de commentaire retirée.
_NIE = re.compile(r"Aucun artefact ne s'appelle « ([^»]+) »")
_DISPONIBLES = re.compile(r"Artefacts (?:disponibles|encore disponibles) : ([^.]+)")


def contradiction_du_rappel(reponse: str) -> str:
    """La phrase qui nie un artefact qu'elle énumère — ``""`` si elle n'y est pas.

    Le nom nié est comparé à la liste des disponibles APRÈS avoir retiré ce que
    l'appelant y aurait ajouté entre parenthèses : « resultat_1 (ce n'est pas du
    code) » et « resultat_1 » sont le même artefact, et c'est précisément parce
    que le refus ne le voyait pas qu'il se contredisait.
    """
    nie = _NIE.search(reponse)
    dispo = _DISPONIBLES.search(reponse)
    if nie is None or dispo is None:
        return ""
    nom = nie.group(1).split(" (")[0].strip()
    disponibles = {d.strip() for d in dispo.group(1).split(",")}
    return nom if nom in disponibles else ""


def _statut_inference(releve: Releve) -> str:
    detail = _noeud(releve, "inference") or _noeud(releve, "fetch_predict")
    trouve = re.search(r"statut (\w+)", detail)
    return trouve.group(1) if trouve else ""


def _un_tableau_est_rendu(releve: Releve) -> bool:
    """Un tableau a-t-il été RENDU à l'utilisateur ?

    C'est l'artefact `application/json` que la page affiche, et c'est la seule
    preuve qui ne dépende pas de la formulation. Le détail du nœud `retrieval`
    ne fait pas l'affaire : quand la requête aboutit, il porte le SQL et rien
    d'autre — il ne dit « 0 requête(s), aucune n'a abouti » que dans le cas
    inverse. Mesurer sur cette phrase-là, c'est mesurer une absence.
    """
    return any(a.mime == "application/json" for a in releve.artefacts)


# --- un tour, son attendu ----------------------------------------------------

Verdict = Callable[[Releve, list["TourJoue"]], str]
"""Rend ``""`` si le tour tient sa promesse, sinon ce qui cloche, en une ligne.

Il reçoit AUSSI les tours déjà joués du fil, et il le faut : « la relance est la
même qu'au tour d'avant » est le défaut rapporté, et il ne se lit pas dans un
tour isolé. Un oracle de fil se formule sur le fil."""


def sur_le_tour(verdict: Callable[[Releve], str]) -> Verdict:
    """Un attendu qui ne regarde que le tour courant, à la signature des autres."""

    def verifier(releve: Releve, precedents: list[TourJoue]) -> str:
        return verdict(releve)

    return verifier


@dataclass(frozen=True)
class Tour:
    texte: str
    attendu: str
    verifier: Verdict


@dataclass(frozen=True)
class Fil:
    cle: str
    titre: str
    tours: tuple[Tour, ...]
    temoin: bool = False


# --- les attendus ------------------------------------------------------------


def un_tableau_est_produit(releve: Releve) -> str:
    if not releve.plan.startswith("query"):
        return f"plan `{releve.plan or '—'}` au lieu de query"
    if not _un_tableau_est_rendu(releve):
        return "aucun tableau rendu"
    return ""


def une_prediction_reste_en_attente(releve: Releve) -> str:
    if not releve.pending:
        return f"aucune prédiction en attente (plan `{releve.plan or '—'}`)"
    return ""


def la_prediction_survit(releve: Releve) -> str:
    """Le tour ne s'est pas prononcé sur la prédiction : elle attend toujours.

    C'est l'autre moitié de la confiscation. Borner le court-circuit rendait le
    tableau atteignable, et la prédiction se perdait en silence à sa place :
    « une femme » ne complétait plus rien, puisque plus rien n'attendait.
    """
    if not releve.pending:
        return "la prédiction en attente a été perdue par ce tour"
    return ""


def le_tableau_est_retrouve(releve: Releve) -> str:
    """Le cœur du défaut 3 : le rappel reste ARMÉ et sert l'artefact.

    Trois choses, et il les faut toutes : le nœud ne s'est pas retiré en
    invoquant la prédiction, un outil de rappel a servi, et la réponse ne se
    contredit pas sur le nom de ce qu'elle sert.
    """
    if _sest_retire_pour_la_prediction(releve, "rappel"):
        return "le nœud `rappel` s'est retiré : prédiction en attente"
    if not _un_rappel_a_servi(releve):
        return f"aucun outil de rappel n'a servi (nœud rappel : « {_noeud(releve, 'rappel')} »)"
    nom = contradiction_du_rappel(releve.reponse)
    if nom:
        return f"la réponse nie « {nom} » et l'énumère comme disponible"
    return ""


def la_prediction_aboutit(releve: Releve) -> str:
    statut = _statut_inference(releve)
    if statut != "ok":
        return f"statut `{statut or '—'}` au lieu de ok (plan `{releve.plan or '—'}`)"
    if releve.pending:
        return f"une prédiction reste en attente : {releve.pending}"
    return ""


# Les deux compteurs d'accompagnants, tels que la RELANCE les nomme quand elle
# les réclame. C'est la description du schéma, mot pour mot : la réponse servie
# à l'utilisateur est construite avec.
_COMPTEURS = ("sibsp", "parch")


def les_compteurs_ne_sont_pas_reclames(releve: Releve) -> str:
    """« Sans famille à bord » : ces deux-là ne se redemandent plus.

    C'est LA propriété que la réparation livre, et elle se distingue de « la
    prédiction aboutit » — qui, elle, dépend aussi de ce que le planificateur
    extrait du reste de la phrase, et qui n'est pas stable d'un tirage à
    l'autre (cf. `d-bis` et le compte-rendu). Mesurer les deux séparément est ce
    qui permet de dire laquelle des deux a bougé.
    """
    manquants = [c for c in _COMPTEURS if f"- {c} " in releve.reponse]
    if manquants:
        return f"la relance réclame encore : {', '.join(manquants)}"
    return ""


def la_relance_ne_se_repete_pas(releve: Releve, precedents: list[TourJoue]) -> str:
    """L'attendu d'un tour de complément : la relance ne redemande pas la même chose.

    C'est la formulation exacte du défaut rapporté — « la MÊME phrase, au
    caractère près » — et c'est ce qu'on peut vérifier sans dépendre de ce que
    le planificateur extrait du RESTE de la phrase, qui n'est pas stable d'un
    tirage à l'autre : ou la prédiction aboutit, ou ce qu'elle réclame a changé.
    """
    if _statut_inference(releve) == "ok":
        return ""
    if not precedents:
        return ""
    avant = " ".join(precedents[-1].reponse.split())
    if " ".join(releve.reponse.split()) == avant:
        return "la relance est identique au caractère près : le tour n'a rien retenu"
    return ""


def la_question_est_traitee_normalement(releve: Releve) -> str:
    """Un message qui ne parle pas de la prédiction est traité comme s'il n'y
    en avait aucune — et le fil n'est pas rendu à une relance."""
    if not releve.plan.startswith("query"):
        return f"plan `{releve.plan or '—'}` au lieu de query"
    if not _un_tableau_est_rendu(releve):
        return "aucun tableau rendu"
    return ""


# --- les fils ------------------------------------------------------------

# La prédiction volontairement INCOMPLÈTE de tous ces fils : `sibsp` et `parch`
# n'y sont ni dits ni suggérés. C'est le seul manque, et il est le même partout
# pour que les fils se comparent entre eux.
INCOMPLETE = (
    "prédis la survie d'une passagère de 1re classe de 28 ans, "
    "tarif 80 livres, embarquée à Southampton"
)
# La source est NOMMÉE, et il le faut : une source que le planificateur devine
# est effacée (§14 de `docs/surface-conversationnelle.md`), le tour part en
# demande de précision et le fil ne produit aucun tableau. Mesuré ici même, 3
# tirages sur 3, avant que la question ne nomme `ventes` : le runner mesurait
# alors sa propre question, pas les défauts.
TABLEAU = "dans la source ventes, combien de commandes par canal de vente ?"
RAPPEL = "reprends le tableau précédent et donne-moi les pourcentages"

FILS: tuple[Fil, ...] = (
    Fil(
        cle="a",
        titre="une prédiction en attente confisque le tableau du fil",
        tours=(
            Tour(TABLEAU, "un tableau est produit", sur_le_tour(un_tableau_est_produit)),
            Tour(
                INCOMPLETE,
                "la prédiction reste en attente",
                sur_le_tour(une_prediction_reste_en_attente),
            ),
            Tour(
                RAPPEL,
                "le tableau est retrouvé, la prédiction attend toujours",
                sur_le_tour(lambda r: le_tableau_est_retrouve(r) or la_prediction_survit(r)),
            ),
        ),
    ),
    Fil(
        cle="b",
        titre="on donne l'attribut manquant, la prédiction aboutit",
        tours=(
            Tour(
                INCOMPLETE,
                "la prédiction reste en attente",
                sur_le_tour(une_prediction_reste_en_attente),
            ),
            Tour(
                "elle voyageait sans frère, sœur ni conjoint, et sans parent ni enfant à bord",
                "la prédiction aboutit",
                sur_le_tour(la_prediction_aboutit),
            ),
        ),
    ),
    Fil(
        cle="c",
        titre="une question sans rapport est traitée normalement",
        tours=(
            Tour(
                INCOMPLETE,
                "la prédiction reste en attente",
                sur_le_tour(une_prediction_reste_en_attente),
            ),
            Tour(
                TABLEAU,
                "la question SQL est traitée",
                sur_le_tour(la_question_est_traitee_normalement),
            ),
            # Le troisième tour mesure le PRIX de garder la prédiction en
            # attente au lieu de la solder : son contexte reste dans le prompt
            # du planificateur, et il pourrait attirer vers `predict` un message
            # qui n'a rien à y faire. Une deuxième question sans rapport le dit.
            Tour(
                "et combien de clients distincts ?",
                "la question suivante est traitée aussi",
                sur_le_tour(la_question_est_traitee_normalement),
            ),
        ),
    ),
    Fil(
        cle="d",
        titre="« sans famille à bord » : sibsp et parch ne sont plus réclamés",
        tours=(
            Tour(
                INCOMPLETE + ", sans famille à bord",
                "les deux compteurs sont lus, pas réclamés",
                sur_le_tour(les_compteurs_ne_sont_pas_reclames),
            ),
        ),
    ),
    # `d` et `d-bis` mesurent la MÊME phrase et ne disent pas la même chose, et
    # c'est délibéré. `d` mesure ce que la réparation livre : les deux
    # compteurs d'accompagnants ne se redemandent plus. `d-bis` mesure le tour
    # ENTIER — il dépend aussi de ce que le planificateur extrait du reste de la
    # phrase (`age`, `fare`, `pclass`...), qui varie d'un tirage à l'autre sur ce
    # moteur. Les confondre ferait porter à la réparation le compte d'une
    # instabilité qui ne lui appartient pas, dans un sens comme dans l'autre.
    Fil(
        cle="d-bis",
        titre="« sans famille à bord » : la prédiction aboutit du premier coup",
        tours=(
            Tour(
                INCOMPLETE + ", sans famille à bord",
                "la prédiction aboutit du premier coup",
                sur_le_tour(la_prediction_aboutit),
            ),
        ),
    ),
    Fil(
        cle="g",
        titre="la séquence rapportée : la relance ne se répète pas",
        tours=(
            Tour(
                INCOMPLETE + ", sans famille à bord",
                "les deux compteurs sont lus, pas réclamés",
                sur_le_tour(les_compteurs_ne_sont_pas_reclames),
            ),
            Tour(
                "c'est une femme",
                "la relance n'est pas la même qu'au tour d'avant",
                la_relance_ne_se_repete_pas,
            ),
        ),
    ),
    Fil(
        cle="e",
        titre="TÉMOIN — « une femme » complète toujours la prédiction",
        temoin=True,
        tours=(
            Tour(
                "prédis la survie d'un passager de 1re classe de 28 ans, tarif 80 livres, "
                "embarqué à Southampton, sans frère, sœur ni conjoint à bord, "
                "et sans parent ni enfant à bord",
                "la prédiction reste en attente",
                sur_le_tour(une_prediction_reste_en_attente),
            ),
            Tour("une femme", "la prédiction aboutit", sur_le_tour(la_prediction_aboutit)),
        ),
    ),
    Fil(
        cle="f",
        titre="TÉMOIN — le rappel d'un tableau, sans prédiction en attente",
        temoin=True,
        tours=(
            Tour(TABLEAU, "un tableau est produit", sur_le_tour(un_tableau_est_produit)),
            Tour(RAPPEL, "le tableau est retrouvé et servi", sur_le_tour(le_tableau_est_retrouve)),
        ),
    ),
)


# --- jouer un fil ------------------------------------------------------------


@dataclass
class TourJoue:
    numero: int
    texte: str
    attendu: str
    constat: str
    noeuds: list[str]
    artefacts: list[str]
    outils: list[dict[str, str]]
    appels_llm: int
    plan: str
    pending: str
    statut_inference: str
    reponse: str

    @property
    def tenu(self) -> bool:
        return not self.constat


@dataclass
class FilJoue:
    cle: str
    titre: str
    tirage: int
    temoin: bool
    tours: list[TourJoue] = field(default_factory=list)

    @property
    def tenu(self) -> bool:
        return all(t.tenu for t in self.tours)

    @property
    def appels_llm(self) -> int:
        return sum(t.appels_llm for t in self.tours)


def jouer(
    orchestrateur: Orchestrator, mouchard: ModeleMouchard, fil: Fil, tirage: int, racine: Path
) -> FilJoue:
    """Un fil, tour à tour, en reportant ce que l'API reporte.

    Une conversation NEUVE par tirage : deux tirages qui partageraient un fil
    partageraient ses artefacts, et le rappel du second retrouverait ceux du
    premier — il verdirait sans rien prouver.
    """
    identifiant = f"fil-{fil.cle}-{tirage}-{uuid.uuid4().hex[:6]}"
    joue = FilJoue(cle=fil.cle, titre=fil.titre, tirage=tirage, temoin=fil.temoin)
    source_de_travail = ""
    echange_precedent: tuple[str, str] | None = None
    pending = None
    for numero, tour in enumerate(fil.tours, start=1):
        releve, source_de_travail, echange_precedent, pending = poser(
            orchestrateur,
            mouchard,
            Message(numero=numero, texte=tour.texte, montre=tour.attendu),
            fil=identifiant,
            racine=racine,
            source_de_travail=source_de_travail,
            echange_precedent=echange_precedent,
            pending=pending,
        )
        joue.tours.append(
            TourJoue(
                numero=numero,
                texte=tour.texte,
                attendu=tour.attendu,
                constat=tour.verifier(releve, list(joue.tours)),
                noeuds=[f"{n.nom}: {' '.join(n.detail.split())}" for n in releve.noeuds],
                artefacts=[a.mime for a in releve.artefacts],
                outils=[{"outil": a.outil, "arguments": a.arguments} for a in releve.outils],
                appels_llm=releve.appels_llm,
                plan=releve.plan,
                pending=releve.pending,
                statut_inference=_statut_inference(releve),
                reponse=releve.reponse,
            )
        )
    return joue


# --- le rapport --------------------------------------------------------------


def rapport(joues: list[FilJoue]) -> str:
    lignes = ["", "## Par fil", "", "| fil | tirages tenus | appels LLM (médiane) | titre |"]
    lignes.append("|---|---|---|---|")
    for cle in sorted({j.cle for j in joues}):
        lot = [j for j in joues if j.cle == cle]
        couts = sorted(j.appels_llm for j in lot)
        lignes.append(
            f"| {cle} | {sum(j.tenu for j in lot)}/{len(lot)} "
            f"| {couts[len(couts) // 2]} | {lot[0].titre} |"
        )
    lignes += ["", "## Ce qui n'a pas tenu", ""]
    manques = [
        f"- fil {j.cle} (tirage {j.tirage}), tour {t.numero} « {t.texte[:60]} » : {t.constat}"
        for j in joues
        for t in j.tours
        if not t.tenu
    ]
    lignes += manques or ["Rien : tous les tours de tous les tirages ont tenu."]
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description="Six fils où une prédiction croise le reste.")
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument("--fils", nargs="*", default=None, help="les clés de fils à jouer")
    parseur.add_argument("--json", type=Path, default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")

    mouchard = ModeleMouchard(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages,
        model=mouchard,
        catalog=load_catalog(reglages.catalog_path),
        registry=Registry.load(reglages.models_registry_path),
    )
    racine = Path(reglages.workspace_dir) / f"fils-{uuid.uuid4().hex[:8]}"
    racine.mkdir(parents=True, exist_ok=True)

    choisis = [f for f in FILS if not args.fils or f.cle in args.fils]
    joues: list[FilJoue] = []
    for tirage in range(1, args.tirages + 1):
        for fil in choisis:
            print(f"[tirage {tirage}] fil {fil.cle} — {fil.titre}", flush=True)
            joue = jouer(orchestrateur, mouchard, fil, tirage, racine)
            joues.append(joue)
            for tour in joue.tours:
                etat = "OK " if tour.tenu else "NON"
                print(f"    {etat} tour {tour.numero} : {tour.attendu}", flush=True)
                if not tour.tenu:
                    print(f"        {tour.constat}", flush=True)
                    print(f"        nœuds : {' | '.join(tour.noeuds)}", flush=True)
                    print(
                        f"        outils : "
                        f"{' | '.join(f'{o["outil"]}({o["arguments"]})' for o in tour.outils)}",
                        flush=True,
                    )
            print(flush=True)

    texte = rapport(joues)
    print(texte)
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **{k: v for k, v in j.__dict__.items() if k != "tours"},
                        "tenu": j.tenu,
                        "tours": [t.__dict__ | {"tenu": t.tenu} for t in j.tours],
                    }
                    for j in joues
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nÉcrit : {args.json}")


if __name__ == "__main__":
    main()
