"""Mesure qu'un agent répond juste sur TROIS TYPES DE SOURCE, sans les confondre.

`mesure_choix_de_source.py` mesure le mécanisme du choix — proposer, lier,
tenir, basculer — sur un catalogue dont les sources sont de deux types. Il ne
dit rien de ce qui nous occupe ici : un catalogue qui déclare **les trois types
en même temps** (`postgres`, `file`, `duckdb`), ouverts par trois chemins
d'adaptateur différents, et un agent qui doit rendre à chacune ses propres
chiffres.

La confusion, si elle existait, se lirait immédiatement : les trois
volumétries sont franchement distinctes (837 / 111 / 40 052), et une réponse
qui donnerait le chiffre d'une autre source est une réponse fausse, pas une
réponse imprécise. C'est pour ça que l'oracle porte sur des NOMBRES.

Le parcours tient dans UNE conversation, et il alterne les trois sources :

1. l'inventaire, qui doit annoncer les trois sources avec leurs trois types ;
2. la liaison de la source Postgres, puis sa volumétrie sans la nommer ;
3. la bascule vers la base DuckDB, et sa volumétrie à elle ;
4. une **vraie question SQL** sur la base DuckDB, qui n'a de réponse que par la
   jointure déclarée en clé étrangère — c'est la propriété qui justifie le type
   `duckdb` plutôt qu'un `file` de plus ;
5. la bascule vers le CSV, et sa volumétrie à lui.

Le coût est compté avec le même compteur que les deux autres runners
(`ModeleCompteur`), pour que les trois tableaux se comparent.

Prérequis : le serveur LLM répond, et le catalogue est semé —

    uv run python scripts/seed_catalogue_trois_types.py
    DAA_CATALOG_PATH=tests/catalogues/trois-types/catalogue.yaml \
    uv run python scripts/mesure_trois_types_de_source.py
"""

from __future__ import annotations

import argparse
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# Le compteur d'allers-retours LLM, le repli d'oracle et le formateur de cellule
# sont ceux des autres runners : la mesure doit être LA MÊME des trois côtés.
from mesure_surface_conversationnelle import ModeleCompteur, replie, une_ligne

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.graph import Orchestrator

FIL = "mesure-trois-types"


def mener(orchestrateur: Orchestrator, magasin: ConversationStore, message: str) -> tuple[str, str]:
    """Un tour, mené comme l'API le mène — magasin compris. Rend (ce qui s'affiche, source liée).

    Le passage par le ``ConversationStore`` n'est pas un détail de confort :
    c'est lui qui persiste la source liée et la relit au tour suivant. Un
    runner qui repasserait l'objet de mémoire mesurerait un mécanisme qui n'est
    pas celui de l'application.

    Ce qui s'affiche, c'est la réponse **et les tableaux servis avec** : à
    « combien de lignes en tout ? » sur une source à deux tables, l'agent
    répond « 2 lignes retournées — voir le tableau ci-dessous », et le chiffre
    est dans le tableau. Un oracle qui ne lirait que la prose compterait faux
    une réponse que l'utilisateur, lui, lit correctement. Les tableaux sont des
    artefacts JSON (`_json_table`) : leur texte brut suffit à y chercher un
    chiffre, il n'y a pas à reconstruire l'affichage du front.
    """
    fil = magasin.load(FIL) or magasin.create(FIL)
    reponse = orchestrateur.ask(
        message,
        pending=fil.pending,
        conversation_id=fil.id,
        workspace_root=magasin.base_dir,
        source_de_travail=fil.source_de_travail,
    )
    magasin.record_turn(
        fil.id,
        question=message,
        answer=reponse.answer,
        artifacts=reponse.artifacts,
        error=reponse.error,
        pending=reponse.pending,
        source_de_travail=reponse.source_de_travail,
    )
    affiche = "\n".join([reponse.answer, *(a.data for a in reponse.artifacts)])
    return affiche, magasin.load(FIL).source_de_travail


@dataclass(frozen=True)
class Etape:
    """Un tour du parcours, et l'oracle qui décide de son verdict.

    ``attendus_tous`` doivent TOUS apparaître dans la réponse ; ``attendus_parmi``
    au moins un — le cas où plusieurs formulations d'un même chiffre sont justes
    (837 lignes en tout, ou 777 commandes).

    ``interdits`` est ce qui rend cette mesure différente d'une mesure de
    justesse ordinaire : les chiffres des AUTRES sources. Une réponse qui les
    porte a confondu les sources, même si elle porte aussi le bon.
    """

    attendu: str
    message: str
    source_attendue: str | None
    attendus_tous: tuple[str, ...] = ()
    attendus_parmi: tuple[str, ...] = ()
    interdits: tuple[str, ...] = ()


# Les oracles, relevés le 2026-09-14 — cf. tests/catalogues/trois-types/README.md.
VOLUMES = {
    "commandes": ("837", "777"),
    "capteurs": ("111",),
    "entrepot": ("40052", "40 052", "40000", "40 000"),
}


def _ailleurs(source: str) -> tuple[str, ...]:
    """Les volumétries des AUTRES sources : ce qu'une réponse juste ne dit pas."""
    return tuple(v for nom, volumes in VOLUMES.items() if nom != source for v in volumes)


PARCOURS = (
    Etape(
        attendu="l'inventaire annonce les trois sources et leurs trois types",
        message="bonjour, quelles sources de données as-tu ?",
        source_attendue=None,
        attendus_tous=("commandes", "capteurs", "entrepot"),
    ),
    Etape(
        attendu="la liaison de la source postgres",
        message="commandes",
        source_attendue="commandes",
    ),
    Etape(
        attendu="la volumétrie de la source postgres, sans la nommer",
        message="combien de lignes en tout ?",
        source_attendue="commandes",
        attendus_parmi=VOLUMES["commandes"],
        interdits=_ailleurs("commandes"),
    ),
    Etape(
        attendu="la bascule vers la base duckdb, et SA volumétrie",
        message="et dans entrepot, combien de lignes en tout ?",
        source_attendue="entrepot",
        attendus_parmi=VOLUMES["entrepot"],
        interdits=_ailleurs("entrepot"),
    ),
    Etape(
        attendu="une jointure que seules les clés étrangères déclarées donnent",
        message="dans quelle ville le montant total des ventes est-il le plus élevé ?",
        source_attendue="entrepot",
        attendus_parmi=("nantes", "lyon", "lille", "brest", "dijon", "nimes"),
    ),
    Etape(
        attendu="la bascule vers le fichier CSV, et SA volumétrie",
        message="et dans capteurs, combien de lignes en tout ?",
        source_attendue="capteurs",
        attendus_parmi=VOLUMES["capteurs"],
        interdits=_ailleurs("capteurs"),
    ),
)


@dataclass
class Resultat:
    etape: Etape
    reponse: str
    source_liee: str
    appels_llm: int
    duree_ms: int
    defauts: list[str] = field(default_factory=list)

    @property
    def juste(self) -> bool:
        return not self.defauts


def juger(etape: Etape, reponse: str, source_liee: str) -> list[str]:
    """Ce qui cloche dans ce tour — liste vide s'il est juste."""
    plat = replie(reponse)
    defauts = []
    if etape.source_attendue is not None and source_liee != etape.source_attendue:
        defauts.append(
            f"source liée « {source_liee or 'aucune'} » au lieu de « {etape.source_attendue} »"
        )
    manquants = [a for a in etape.attendus_tous if replie(a) not in plat]
    if manquants:
        defauts.append("absent(s) : " + ", ".join(manquants))
    if etape.attendus_parmi and not any(replie(a) in plat for a in etape.attendus_parmi):
        defauts.append("aucun attendu parmi : " + ", ".join(etape.attendus_parmi))
    confondus = [i for i in etape.interdits if replie(i) in plat]
    if confondus:
        defauts.append("chiffre d'une AUTRE source : " + ", ".join(confondus))
    return defauts


def tableau_markdown(resultats: list[Resultat]) -> str:
    lignes = [
        "| Tour | Attendu | Message | Réponse obtenue | Source liée | Appels LLM | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for numero, r in enumerate(resultats, start=1):
        verdict = "**juste**" if r.juste else "**FAUX** — " + " ; ".join(r.defauts)
        lignes.append(
            f"| {numero} | {r.etape.attendu} | {une_ligne(r.etape.message, 70)} "
            f"| {une_ligne(r.reponse, 240)} | `{r.source_liee or '(aucune)'}` "
            f"| {r.appels_llm} | {verdict} |"
        )
    justes = sum(1 for r in resultats if r.juste)
    total_appels = sum(r.appels_llm for r in resultats)
    lignes += [
        "",
        f"**{justes}/{len(resultats)} tours justes**, {total_appels} appels LLM "
        f"(moyenne {total_appels / len(resultats):.2f} par tour).",
    ]
    return "\n".join(lignes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="écrit le tableau et le verdict ici")
    args = parser.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    types = {s.name: s.type for s in catalogue.sources}
    if set(types.values()) != {"postgres", "file", "duckdb"}:
        raise SystemExit(
            "ce parcours veut un catalogue portant LES TROIS types à la fois ; "
            f"celui-ci porte {sorted(set(types.values()))}. "
            "Essayez DAA_CATALOG_PATH=tests/catalogues/trois-types/catalogue.yaml."
        )
    print("Sources : " + ", ".join(f"{n} ({t})" for n, t in types.items()))
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})\n")

    compteur = ModeleCompteur(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages,
        model=compteur,
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    resultats = []
    with tempfile.TemporaryDirectory(prefix="daa-mesure-trois-types-") as racine:
        magasin = ConversationStore(Path(racine), "mesure")
        for numero, etape in enumerate(PARCOURS, start=1):
            print(f"[{numero}/{len(PARCOURS)}] {etape.attendu}\n    > {etape.message}", flush=True)
            depart = time.monotonic()
            avant = compteur.appels
            affiche, source_liee = mener(orchestrateur, magasin, etape.message)
            resultat = Resultat(
                etape=etape,
                reponse=affiche,
                source_liee=source_liee,
                appels_llm=compteur.appels - avant,
                duree_ms=int((time.monotonic() - depart) * 1000),
                defauts=juger(etape, affiche, source_liee),
            )
            resultats.append(resultat)
            print(f"    « {une_ligne(affiche, 220)} »")
            verdict = "juste" if resultat.juste else "FAUX — " + " ; ".join(resultat.defauts)
            print(
                f"    → source liée : {source_liee or '(aucune)'}, "
                f"{resultat.appels_llm} appel(s) LLM, {resultat.duree_ms} ms — {verdict}\n",
                flush=True,
            )

    texte = tableau_markdown(resultats)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"\nTableau écrit dans {args.markdown}")
    raise SystemExit(0 if all(r.juste for r in resultats) else 1)


if __name__ == "__main__":
    main()
