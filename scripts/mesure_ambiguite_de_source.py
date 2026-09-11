"""Mesure ce que fait l'agent quand une question peut porter sur DEUX sources.

Le défaut que ce runner existe pour montrer tient en une phrase : *l'ordre de
déclaration du YAML décidait de la réponse*. Deux sources qui partagent une
colonne, la même question, les mêmes octets de données — et un comportement qui
bascule entièrement selon laquelle est écrite en premier dans le catalogue.
Cf. ``tests/catalogues/ambiguite/README.md``, qui porte les catalogues et les
oracles.

Ce qui est mesuré, pour CHAQUE ordre de déclaration et sur N essais :

- **quelle source a répondu**, lue dans le CHIFFRE de la réponse. Les deux
  oracles sont francs (35,24 % contre 51,00 %) : la valeur dit sans ambiguïté
  quelle source a été interrogée, là où le nom cité dans une phrase ne prouve
  rien ;
- **ou bien que l'agent a proposé** son inventaire et attendu ;
- **la source liée au fil** après le tour.

Chaque essai est une conversation NEUVE, menée comme l'API la mène — magasin de
conversations compris, puisque c'est lui qui persiste la source de travail. Le
fil vit dans un dossier temporaire, jeté à la sortie.

    uv run python scripts/mesure_ambiguite_de_source.py
    uv run python scripts/mesure_ambiguite_de_source.py --essais 5 --markdown /tmp/t.md

Le catalogue de production n'est PAS utilisé : les deux catalogues d'ambiguïté
sont passés explicitement (``--catalogues``), parce que la mesure n'a de sens
que sur des octets figés. Prérequis : le serveur LLM répond, et les sources des
catalogues mesurés sont joignables.
"""

from __future__ import annotations

import argparse
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Le compteur d'allers-retours LLM et le formateur de cellule sont ceux des
# autres runners : une mesure de coût qui ne se compare pas aux leurs ne sert
# à rien. (`scripts` est sur le chemin d'import — cf. le `pythonpath` de pytest
# et le dossier du script lui-même.)
from mesure_surface_conversationnelle import ModeleCompteur, une_ligne

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.conversations import ConversationStore
from data_analyst_agent.orchestrator.graph import Orchestrator

RACINE = Path(__file__).resolve().parent.parent
CATALOGUES = (
    RACINE / "tests/catalogues/ambiguite/titanic-en-premier.yaml",
    RACINE / "tests/catalogues/ambiguite/employes-en-premier.yaml",
)

QUESTION = "Quel est le pourcentage de femmes ?"

# Les oracles, recopiés du README des catalogues : 314/891 pour titanic,
# 153/300 pour employes. Deux valeurs franchement distinctes — c'est la
# propriété qui rend la mesure lisible.
ORACLES = {"titanic": 35.24, "employes": 51.00}

# Ce qui, dans la réponse, dit que l'agent a PROPOSÉ au lieu de répondre. C'est
# la dernière ligne de ``introspection.proposer_les_sources`` : la reconnaître
# par sa question finale, et non par la présence des noms, évite de compter
# comme proposition une réponse qui se contente de citer ses sources.
MARQUEUR_DE_PROPOSITION = "sur laquelle veux-tu travailler"

# Un pourcentage écrit à la française ou à l'anglaise, décimales comprises.
NOMBRE = re.compile(r"\d+[.,]\d+|\d+")
# Tolérance sur la valeur lue : le modèle arrondit (35,2 ; 35 ; 35,24).
TOLERANCE = 0.6


@dataclass
class Essai:
    """Un essai : une conversation neuve, une question, ce qu'on en a observé."""

    ordre: str
    numero: int
    verdict: str  # « titanic », « employes », « proposition », « autre », « erreur »
    reponse: str
    source_liee: str
    capacite: str | None
    appels_llm: int
    duree_ms: int


def source_repondue(reponse: str) -> str | None:
    """La source que le CHIFFRE de la réponse désigne, ou ``None``.

    On lit la valeur, pas le nom : un modèle qui écrit « d'après titanic » sans
    avoir interrogé quoi que ce soit ne prouve rien, alors qu'un 35,24 ne peut
    venir que d'un ``count`` sur 891 lignes.
    """
    valeurs = [float(n.replace(",", ".")) for n in NOMBRE.findall(reponse)]
    for nom, oracle in ORACLES.items():
        if any(abs(v - oracle) <= TOLERANCE for v in valeurs):
            return nom
    return None


def classer(reponse: str, erreur: str | None) -> str:
    """Le verdict de l'essai. L'ordre des marches est celui de leur certitude."""
    if erreur:
        return "erreur"
    if MARQUEUR_DE_PROPOSITION in " ".join(reponse.lower().split()):
        return "proposition"
    return source_repondue(reponse) or "autre"


def un_essai(
    orchestrateur: Orchestrator,
    compteur: ModeleCompteur,
    magasin: ConversationStore,
    ordre: str,
    numero: int,
) -> Essai:
    """Une conversation neuve, un tour, mené comme l'API le mène.

    Le passage par le ``ConversationStore`` n'est pas du confort : c'est lui
    qui persiste la source de travail et la relit au tour suivant. Un runner
    qui repasserait l'objet de mémoire mesurerait un mécanisme qui n'est pas
    celui de l'application.
    """
    fil = magasin.create(f"{ordre}-{numero}")
    depart = time.monotonic()
    avant = compteur.appels
    reponse = orchestrateur.ask(
        QUESTION,
        conversation_id=fil.id,
        workspace_root=magasin.base_dir,
        source_de_travail=fil.source_de_travail,
    )
    duree = int((time.monotonic() - depart) * 1000)
    magasin.record_turn(
        fil.id,
        question=QUESTION,
        answer=reponse.answer,
        artifacts=reponse.artifacts,
        error=reponse.error,
        pending=reponse.pending,
        source_de_travail=reponse.source_de_travail,
    )
    return Essai(
        ordre=ordre,
        numero=numero,
        verdict=classer(reponse.answer, reponse.error),
        reponse=reponse.answer if not reponse.error else f"{reponse.answer} [{reponse.error}]",
        source_liee=magasin.load(fil.id).source_de_travail,
        capacite=reponse.plan.capability if reponse.plan else None,
        appels_llm=compteur.appels - avant,
        duree_ms=duree,
    )


def mesurer(chemin: Path, essais: int) -> list[Essai]:
    """Les essais d'un catalogue, sur un orchestrateur monté pour LUI.

    L'orchestrateur est reconstruit par catalogue : c'est l'objet qui porte le
    catalogue, et c'est précisément la variable de l'expérience.
    """
    reglages = get_settings()
    catalogue = load_catalog(chemin)
    ordre = chemin.stem
    noms = ", ".join(s.name for s in catalogue.sources)
    print(f"\n=== {ordre} — sources déclarées dans cet ordre : {noms}", flush=True)

    compteur = ModeleCompteur(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages,
        model=compteur,
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    resultats = []
    with tempfile.TemporaryDirectory(prefix="daa-mesure-ambiguite-") as racine:
        magasin = ConversationStore(Path(racine), "mesure")
        for numero in range(1, essais + 1):
            essai = un_essai(orchestrateur, compteur, magasin, ordre, numero)
            resultats.append(essai)
            print(
                f"  [{numero}/{essais}] {essai.verdict:12} "
                f"source liée : {essai.source_liee or '(aucune)':10} "
                f"« {une_ligne(essai.reponse, 130)} »",
                flush=True,
            )
    return resultats


def tableau_markdown(par_ordre: dict[str, list[Essai]]) -> str:
    """Le tableau de synthèse : un ordre par ligne, les verdicts comptés."""
    lignes = [
        f"Question posée dans une conversation neuve, à chaque essai : « {QUESTION} »",
        "",
        "| Ordre déclaré | Essais | A répondu sur `titanic` (35,24 %) "
        "| A répondu sur `employes` (51,00 %) | A proposé et attendu | Autre / erreur |",
        "|---|---|---|---|---|---|",
    ]
    for ordre, essais in par_ordre.items():
        compte = {v: sum(1 for e in essais if e.verdict == v) for v in {e.verdict for e in essais}}
        autres = compte.get("autre", 0) + compte.get("erreur", 0)
        lignes.append(
            f"| `{ordre}` | {len(essais)} | {compte.get('titanic', 0)}/{len(essais)} "
            f"| {compte.get('employes', 0)}/{len(essais)} "
            f"| {compte.get('proposition', 0)}/{len(essais)} | {autres} |"
        )
    lignes += ["", "Le détail de chaque essai :", ""]
    lignes += [
        "| Ordre | Essai | Verdict | Source liée après | Capacité | Appels LLM | Réponse |",
        "|---|---|---|---|---|---|---|",
    ]
    for essais in par_ordre.values():
        for e in essais:
            lignes.append(
                f"| `{e.ordre}` | {e.numero} | **{e.verdict}** "
                f"| `{e.source_liee or '(aucune)'}` | {e.capacite or '—'} | {e.appels_llm} "
                f"| {une_ligne(e.reponse, 200)} |"
            )
    return "\n".join(lignes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--essais", type=int, default=5, help="essais par ordre (défaut : 5)")
    parser.add_argument(
        "--catalogues", type=Path, nargs="+", default=list(CATALOGUES), help="les YAML à comparer"
    )
    parser.add_argument("--markdown", type=Path, help="écrit le tableau ici")
    args = parser.parse_args()

    reglages = get_settings()
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")

    par_ordre = {chemin.stem: mesurer(chemin, args.essais) for chemin in args.catalogues}

    texte = tableau_markdown(par_ordre)
    print("\n" + texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"\nTableau écrit dans {args.markdown}")


if __name__ == "__main__":
    main()
