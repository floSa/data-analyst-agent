"""Mesure ce que le code d'analyse fait d'une VALEUR SENTINELLE qu'il ignore.

Le défaut est celui de C30, déplacé d'un agent à l'autre : le dictionnaire de la
source allait à l'agent SQL et pas à celui qui écrit le PYTHON. Il est pire ici,
et c'est la raison de ce runner : une requête SQL fausse laisse sa trace — on la
relit dans la conversation, ligne à ligne — là où un ``df.puissance_kw.mean()``
faux ne laisse qu'un nombre, ou pire, une courbe. Personne ne relit une courbe.

L'oracle est semé, c'est le piège nº 2 du catalogue de démonstration :
``telemetrie.releves_puissance.puissance_kw`` vaut ``-1.0`` quand le compteur
n'a rien remonté. Ce n'est pas une puissance nulle — une borne à l'arrêt, elle,
remonte ``0.0``, et cette valeur-là est VRAIE et doit être gardée. Le piège a
donc deux gueules, et deux façons de se tromper :

- garder les ``-1`` tire la moyenne vers le BAS ;
- écarter les ``0`` avec eux la tire vers le HAUT.

D'où trois oracles et non deux, calculés ici en SQL direct — et sur DEUX
assiettes, parce que les deux chemins ne voient pas la même chose : l'agent SQL
interroge la table ENTIÈRE, le code d'analyse ne reçoit que la tranche
matérialisée en CSV par ``analysis_table_max_rows``. Le nœud qui a répondu,
lu dans la trace, décide de l'assiette contre laquelle le chiffre est jugé.

    uv run python scripts/mesure_dictionnaire_analyse.py --essais 5
    uv run python scripts/mesure_dictionnaire_analyse.py --figures /tmp/fig

``--figures`` écrit les PNG produits : une figure se REGARDE, et un code qui
tourne sans lever peut rendre des axes vides. Le statut ``ok`` du bac à sable
ne dit rien de ce qui est dessiné dessus.

Prérequis : ``DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml``, le
catalogue semé, le serveur LLM joignable et Docker pour le bac à sable.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

SOURCE = "telemetrie"
TABLE = "releves_puissance"
COLONNE = "puissance_kw"

QUESTIONS = {
    "moyenne": "quelle est la puissance moyenne relevée ?",
    "par_heure": "trace-moi la puissance moyenne par heure",
}


@dataclass(frozen=True)
class Oracles:
    """Les trois moyennes, sur la tranche que le bac à sable reçoit.

    ``naive`` compte les sentinelles comme des puissances ; ``juste`` les
    écarte et garde les zéros ; ``sans_zeros`` écarte les deux — c'est la
    sur-correction, et elle est fausse elle aussi.
    """

    lignes: int
    sentinelles: int
    zeros: int
    naive: float
    juste: float
    sans_zeros: float

    def nomme(self, valeur: float, tolerance: float = 0.2) -> str:
        for nom, cible in (
            ("juste", self.juste),
            ("naïve", self.naive),
            ("sans les zéros", self.sans_zeros),
        ):
            if abs(valeur - cible) <= tolerance:
                return nom
        return "autre"


def oracles(chemin: Path, plafond: int) -> dict[str, Oracles]:
    """Les trois moyennes en SQL direct, sur DEUX assiettes — et c'est nécessaire.

    L'agent SQL voit la TABLE ENTIÈRE ; le code d'analyse ne voit que ce que
    ``analysis_table_max_rows`` a matérialisé en CSV. Les deux chemins n'ont
    donc pas la même vérité, et confondre leurs oracles ferait passer pour faux
    un chiffre juste — ou l'inverse.
    """
    assiettes = {
        "entière": f"SELECT * FROM {TABLE}",
        "tranche": f"SELECT * FROM {TABLE} LIMIT {plafond}",
    }
    mesures: dict[str, Oracles] = {}
    with duckdb.connect(str(chemin), read_only=True) as connexion:
        for nom, requete in assiettes.items():
            ligne = connexion.execute(
                f"""SELECT count(*), count(*) FILTER (WHERE {COLONNE} = -1),
                           count(*) FILTER (WHERE {COLONNE} = 0),
                           avg({COLONNE}),
                           avg({COLONNE}) FILTER (WHERE {COLONNE} <> -1),
                           avg({COLONNE}) FILTER (WHERE {COLONNE} > 0)
                    FROM ({requete})"""
            ).fetchone()
            mesures[nom] = Oracles(
                lignes=ligne[0],
                sentinelles=ligne[1],
                zeros=ligne[2],
                naive=round(ligne[3], 4),
                juste=round(ligne[4], 4),
                sans_zeros=round(ligne[5], 4),
            )
    return mesures


# --- ce qu'on lit dans le code généré ----------------------------------------

# Écarter la sentinelle s'écrit de bien des façons ; ce sont celles qu'on a vues
# passer. La borne INFÉRIEURE est la seule qui compte : `!= -1`, `> -1`, `>= 0`
# gardent les zéros ; `> 0` les jette avec la sentinelle, et c'est une autre
# faute. On ne cherche donc pas « y a-t-il un filtre » mais « lequel ».
GARDE_LES_ZEROS = re.compile(
    r"""
    (?:!=|<>|\bne\b)\s*\(?\s*-\s*1(?:\.0*)?\b      # != -1
    | >\s*=?\s*-\s*1(?:\.0*)?\b                     # > -1, >= -1
    | >=\s*0(?:\.0*)?\b                             # >= 0
    | \breplace\s*\(\s*-\s*1                        # replace(-1, nan)
    | \bmask\s*\([^)]*==\s*-\s*1                    # mask(x == -1)
    | \bwhere\s*\([^)]*[!<>]=?\s*-\s*1              # where(x != -1)
    """,
    re.VERBOSE,
)
JETTE_LES_ZEROS = re.compile(r">\s*0(?:\.0*)?\b|\bne\b\s*\(?\s*0(?:\.0*)?\)?|!=\s*0(?:\.0*)?\b")

VERDICTS = {
    "écartée": "la sentinelle est écartée, les zéros gardés",
    "sur-filtrée": "la sentinelle est écartée, les zéros aussi",
    "gardée": "la sentinelle est comptée comme une puissance",
}


def lire_le_filtre(code: str) -> str:
    """Ce que le code fait de la sentinelle, lu dans le code lui-même."""
    pertinent = "\n".join(ligne for ligne in code.splitlines() if not ligne.strip().startswith("#"))
    garde = bool(GARDE_LES_ZEROS.search(pertinent))
    jette = bool(JETTE_LES_ZEROS.search(pertinent))
    if garde:
        return "écartée"
    if jette:
        return "sur-filtrée"
    return "gardée"


# Un nombre à la française ou à l'anglaise. On ne retient que la plage plausible
# d'une puissance moyenne : le reste (un compte de lignes, une heure) n'est pas
# une moyenne, et le confondre avec elle rendrait la lecture illisible.
NOMBRE = re.compile(r"\d+[.,]\d+|\b\d+\b")


def valeurs_plausibles(texte: str, bas: float = 55.0, haut: float = 85.0) -> list[float]:
    trouves = []
    for brut in NOMBRE.findall(texte):
        try:
            valeur = float(brut.replace(",", "."))
        except ValueError:
            continue
        if bas <= valeur <= haut:
            trouves.append(valeur)
    return trouves


@dataclass
class Essai:
    cle: str
    tirage: int
    question: str
    reponse: str
    code: str
    filtre: str
    figures: int
    statut: str
    noeuds: list[str]
    essais_de_code: int
    valeurs: list[float] = field(default_factory=list)
    lecture: str = ""
    duree_ms: int = 0
    fichiers_figures: list[str] = field(default_factory=list)


def un_tirage(
    orchestrateur: Orchestrator,
    cle: str,
    tirage: int,
    racine: Path,
    dossier_figures: Path | None,
) -> Essai:
    question = QUESTIONS[cle]
    fil = f"mesure-dict-analyse-{cle}-{uuid.uuid4().hex[:8]}"
    depart = time.monotonic()
    reponse = orchestrateur.ask(question, conversation_id=fil, source_de_travail=SOURCE)
    duree = int((time.monotonic() - depart) * 1000)

    espace = ConversationWorkspace(racine, fil, limits=orchestrateur.limits)
    code = ""
    for artefact in espace.artifacts:
        if artefact.est_du_code:
            code = espace.lire(artefact)
    images = [a for a in reponse.artifacts if a.mime == "image/png"]
    fichiers: list[str] = []
    if dossier_figures is not None and images:
        dossier_figures.mkdir(parents=True, exist_ok=True)
        for rang, image in enumerate(images, start=1):
            chemin = dossier_figures / f"{cle}-{tirage}-{rang}.png"
            chemin.write_bytes(_octets(image))
            fichiers.append(str(chemin))
    analyse = next((s for s in reponse.trace if s.node == "analysis"), None)
    noeuds = [s.node for s in reponse.trace]
    return Essai(
        cle=cle,
        tirage=tirage,
        question=question,
        reponse=reponse.answer,
        code=code,
        filtre=lire_le_filtre(code) if code else "aucun code",
        figures=len(images),
        statut=analyse.detail if analyse is not None else "(pas de nœud analyse)",
        noeuds=noeuds,
        essais_de_code=_essais_de_code(analyse.detail if analyse is not None else ""),
        valeurs=valeurs_plausibles(reponse.answer),
        duree_ms=duree,
        fichiers_figures=fichiers,
    )


def _octets(image) -> bytes:
    """Les octets d'une figure, quelle que soit la façon dont elle est portée."""
    import base64

    donnees = image.data
    if isinstance(donnees, bytes):
        return donnees
    return base64.b64decode(donnees)


def assiette(essai: Essai) -> str:
    """Contre quelle vérité juger le chiffre de cet essai.

    Un tour qui a fini dans ``analysis`` a compté sur la tranche montée ; tout
    autre — et ``retrieval`` au premier chef — a compté sur la table entière.
    """
    return "tranche" if "analysis" in essai.noeuds else "entière"


def _essais_de_code(detail: str) -> int:
    trouve = re.search(r"(\d+) essai", detail)
    return int(trouve.group(1)) if trouve else 0


def rapport(essais: list[Essai], refs: dict[str, Oracles], reglages) -> str:
    lignes = [
        "## Le dictionnaire jusqu'à celui qui écrit le Python",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        "",
        "| assiette | lignes | sentinelles | zéros | moyenne naïve | moyenne juste "
        "| sans les zéros |",
        "|---|---|---|---|---|---|---|",
    ]
    for nom, ref in refs.items():
        lignes.append(
            f"| {nom} | {ref.lignes} | {ref.sentinelles} | {ref.zeros} | {ref.naive:.4f} "
            f"| **{ref.juste:.4f}** | {ref.sans_zeros:.4f} |"
        )
    lignes += [
        "",
        "| question | tirage | nœuds | sentinelle | figures | valeur lue | ce qu'elle vaut "
        "| essais |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in essais:
        ref = refs[assiette(e)]
        valeurs = ", ".join(f"{v:g}" for v in e.valeurs) or "—"
        noms = ", ".join(sorted({ref.nomme(v) for v in e.valeurs})) or "—"
        lignes.append(
            f"| `{e.cle}` | {e.tirage} | {' → '.join(e.noeuds)} | {e.filtre} | {e.figures} "
            f"| {valeurs} | {noms} | {e.essais_de_code} |"
        )
    lignes.append("")
    for cle in QUESTIONS:
        lot = [e for e in essais if e.cle == cle]
        if not lot:
            continue
        avec_code = [e for e in lot if e.code]
        ecartees = sum(1 for e in avec_code if e.filtre == "écartée")
        lignes.append(
            f"- `{cle}` : {len(avec_code)}/{len(lot)} tirages ont écrit du code ; "
            f"sentinelle écartée **{ecartees}/{len(avec_code) or len(lot)}** "
            f"(sur-filtrée {sum(1 for e in avec_code if e.filtre == 'sur-filtrée')}, "
            f"gardée {sum(1 for e in avec_code if e.filtre == 'gardée')})"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--essais", type=int, default=5)
    parseur.add_argument("--questions", nargs="*", choices=sorted(QUESTIONS), default=None)
    parseur.add_argument("--figures", type=Path, default=None)
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    args = parseur.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    source = next((s for s in catalogue.sources if s.name == SOURCE), None)
    if source is None:
        raise SystemExit(f"la source « {SOURCE} » n'est pas au catalogue : rien à mesurer")
    refs = oracles(source.path, reglages.analysis_table_max_rows)
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    for nom, ref in refs.items():
        print(
            f"Oracles [{nom}] {ref.lignes} lignes ({ref.sentinelles} sentinelles, "
            f"{ref.zeros} zéros) : naïve {ref.naive:.4f} | juste {ref.juste:.4f} "
            f"| sans les zéros {ref.sans_zeros:.4f}"
        )
    print()

    orchestrateur = Orchestrator(
        settings=reglages,
        model=build_model(reglages),
        catalog=catalogue,
        registry=Registry.load(reglages.models_registry_path),
    )
    essais: list[Essai] = []
    for cle in args.questions or list(QUESTIONS):
        for tirage in range(1, args.essais + 1):
            print(f"[{cle} {tirage}/{args.essais}] « {QUESTIONS[cle]} »")
            essai = un_tirage(orchestrateur, cle, tirage, reglages.workspace_dir, args.figures)
            essais.append(essai)
            print(
                f"    → sentinelle {essai.filtre} | {essai.figures} figure(s) "
                f"| {essai.statut} | {essai.duree_ms} ms"
            )
            ref = refs[assiette(essai)]
            print(
                f"    valeurs : {essai.valeurs} → "
                f"{[ref.nomme(v) for v in essai.valeurs]} (assiette {assiette(essai)})"
            )
            print(f"    réponse : {' '.join(essai.reponse.split())[:200]}\n")

    texte = rapport(essais, refs, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "oracles": {nom: r.__dict__ for nom, r in refs.items()},
                    "essais": [e.__dict__ for e in essais],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
