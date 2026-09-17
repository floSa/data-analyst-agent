"""Le relevé des parcours : huit messages dans un vrai fil, tracés nœud par nœud.

Ce runner ne juge rien. Il n'a pas d'oracle, pas de verdict, pas de score. Il
RELÈVE : pour chacun des huit messages, les nœuds du graphe traversés avec leur
détail, les appels d'outil réellement émis par le modèle avec leurs arguments,
le nombre d'allers-retours avec le moteur, les artefacts produits, et la
réponse. C'est la matière de `docs/parcours-de-l-agent.md`, et c'est la raison
d'être du fichier : une documentation qui dessine des parcours de mémoire
décrit ce qu'on croit que le produit fait, pas ce qu'il fait.

**Le fil est un VRAI fil, et c'est le piège de ce runner.** Les huit messages
s'enchaînent — « et plus de détails sur ventes ? » n'a de sens qu'après le
message d'avant, « fais-moi un graphique de ça » n'a de sens qu'après le
tableau. Ce que l'API reporte d'un tour au suivant est donc reporté ici, à
l'identique (cf. la route ``/chat`` de `api/app.py`) :

- ``source_de_travail`` : celle que la réponse précédente a rendue, jamais
  ``None`` mais la chaîne vide au premier tour, comme ce que l'API relit du
  disque ;
- ``echange_precedent`` : le couple (message précédent, réponse précédente),
  que l'agent système reçoit comme historique ;
- ``pending`` : la prédiction en attente que la réponse précédente a rendue ;
- ``workspace_root`` : une racine par exécution, pour que les tableaux
  intermédiaires du fil atterrissent au même endroit que sa transcription.

Sans ce report, les tours 5 à 8 repartent en demande de précision et le relevé
ne mesure plus rien.

    uv run python scripts/releve_des_parcours.py
    uv run python scripts/releve_des_parcours.py --markdown docs/releve-des-parcours.md
    uv run python scripts/releve_des_parcours.py --seulement 5 6

Prérequis : ``DAA_CATALOG_PATH=sources/metier/catalogue.yaml``, le catalogue
semé (``scripts/seed_catalogue_metier.py``), Postgres joignable, le serveur LLM
en place.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

# --- les huit messages -------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """Un message du fil, et ce qu'on attend d'en apprendre.

    ``montre`` n'est pas un oracle : rien n'est comparé à lui. C'est la raison
    pour laquelle ce message-là est dans la liste, écrite ici et pas dans le
    document, pour que les deux ne dérivent pas l'un de l'autre.
    """

    numero: int
    texte: str
    montre: str


MESSAGES: tuple[Message, ...] = (
    Message(
        numero=1,
        texte="quelles sont les sources à ta disposition ?",
        montre="une question sur le système : aucune base ouverte, aucun SQL écrit",
    ),
    Message(
        numero=2,
        texte="peux-tu me préciser ventes et production ?",
        montre="deux sources nommées, servies dans la MÊME réponse",
    ),
    Message(
        numero=3,
        texte="et plus de détails sur ventes ?",
        montre="une seule source nommée : elle se LIE au fil",
    ),
    Message(
        numero=4,
        texte="parle-moi un peu de stocks et de titanic, en deux mots",
        montre="la ceinture écarte la première formulation, on redemande",
    ),
    Message(
        numero=5,
        texte="quel est le chiffre d'affaires par revendeur, les 5 premiers ?",
        montre="la première vraie question de données : plan, SQL, synthèse",
    ),
    Message(
        numero=6,
        texte="fais-moi un graphique de ça",
        montre="le rappel du tableau précédent, puis du Python en conteneur",
    ),
    Message(
        numero=7,
        texte=(
            "prédis la survie d'une passagère de 1re classe de 28 ans, "
            "sans famille à bord, billet à 80 livres, embarquée à Cherbourg"
        ),
        montre="un modèle de prédiction, qui n'est pas le moteur de langage",
    ),
    Message(
        numero=8,
        texte="reprends le tableau précédent et donne-moi les pourcentages",
        montre="le rappel d'un artefact déjà produit",
    ),
)


# --- le mouchard -------------------------------------------------------------


@dataclass
class AppelDOutil:
    """Un appel d'outil tel que le modèle l'a émis : son nom et ses arguments.

    Les arguments comptent autant que le nom. « `schema_d_une_source` a été
    appelé deux fois » ne dit pas si le tour a servi deux sources ou deux fois
    la même ; `cible='ventes'` puis `cible='production'` le dit.
    """

    outil: str
    arguments: str


class ModeleMouchard(ModeleCompteur):
    """Le compteur d'allers-retours, plus la liste des appels d'outil émis.

    ``ModeleCompteur`` compte ce que coûte un tour ; il ne dit pas ce que le
    modèle a DEMANDÉ. Les deux se relèvent au même endroit — la réponse du
    serveur — et les séparer ferait deux enveloppes autour du même modèle.

    Les appels sont lus dans les ``ToolCallPart`` de la réponse, c'est-à-dire
    ce que le modèle a réellement émis. Ni le prompt ni la trace ne le disent :
    la trace porte les noms retenus par l'agent système, et un outil que le
    modèle appelle deux fois n'y apparaît qu'une.
    """

    def __init__(self, wrapped) -> None:
        super().__init__(wrapped)
        self.outils: list[AppelDOutil] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        reponse = await super().request(messages, model_settings, model_request_parameters)
        for part in reponse.parts:
            if isinstance(part, ToolCallPart):
                self.outils.append(AppelDOutil(part.tool_name, _arguments(part)))
        return reponse


def _arguments(part: ToolCallPart) -> str:
    """Les arguments d'un appel, en une ligne lisible.

    ``args`` est tantôt une chaîne JSON, tantôt un dictionnaire, selon le
    serveur et l'outil. Les deux sont ramenés à la même écriture pour que le
    relevé d'un tour se compare à celui d'un autre.
    """
    brut = part.args
    if isinstance(brut, str):
        try:
            brut = json.loads(brut)
        except json.JSONDecodeError:
            return " ".join(brut.split())
    if not isinstance(brut, dict):
        return " ".join(str(brut).split())
    if not brut:
        return "—"
    return ", ".join(f"{cle}={' '.join(str(valeur).split())!r}" for cle, valeur in brut.items())


# --- le relevé d'un tour -----------------------------------------------------


@dataclass
class Noeud:
    """Un nœud traversé : son nom, ce qu'il a dit de lui-même, sa durée."""

    nom: str
    detail: str
    duree_ms: int


@dataclass
class Artefact:
    """Un artefact rendu à l'utilisateur : son type et sa taille."""

    mime: str
    octets: int


@dataclass
class Releve:
    """Ce qu'un tour a fait, tel qu'on le relève."""

    message: Message
    noeuds: list[Noeud] = field(default_factory=list)
    outils: list[AppelDOutil] = field(default_factory=list)
    appels_llm: int = 0
    artefacts: list[Artefact] = field(default_factory=list)
    longueur: int = 0
    reponse: str = ""
    erreur: str | None = None
    plan: str = ""
    source_liee: str = ""
    pending: str = ""
    duree_ms: int = 0


def poser(
    orchestrateur: Orchestrator,
    mouchard: ModeleMouchard,
    message: Message,
    *,
    fil: str,
    racine: Path,
    source_de_travail: str,
    echange_precedent: tuple[str, str] | None,
    pending,
) -> tuple[Releve, str, tuple[str, str], object]:
    """Un tour, posé comme l'API le pose, et ce qu'il faut reporter au suivant."""
    avant_appels = mouchard.appels
    avant_outils = len(mouchard.outils)
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        message.texte,
        conversation_id=fil,
        workspace_root=racine,
        # Toujours une CHAÎNE, jamais None : c'est ce que l'API passe, et le
        # court-circuit de choix de source en dépend.
        source_de_travail=source_de_travail,
        echange_precedent=echange_precedent,
        pending=pending,
    )
    duree = int((time.monotonic() - depart) * 1000)
    releve = Releve(
        message=message,
        noeuds=[Noeud(s.node, s.detail, s.duration_ms) for s in reponse.trace],
        outils=list(mouchard.outils[avant_outils:]),
        appels_llm=mouchard.appels - avant_appels,
        artefacts=[Artefact(a.mime, len(a.data)) for a in reponse.artifacts],
        longueur=len(reponse.answer),
        reponse=reponse.answer,
        erreur=reponse.error,
        plan=(
            f"{reponse.plan.capability} · source={reponse.plan.source or '—'}"
            if reponse.plan is not None
            else ""
        ),
        source_liee=reponse.source_de_travail or "",
        pending=(
            f"{reponse.pending.dataset} · {sorted(reponse.pending.features)}"
            if reponse.pending is not None
            else ""
        ),
        duree_ms=duree,
    )
    # Ce que l'API reporte au tour suivant, et rien d'autre.
    return (
        releve,
        reponse.source_de_travail or "",
        (message.texte, reponse.answer),
        reponse.pending,
    )


# --- le rapport --------------------------------------------------------------


def _bloc(texte: str, limite: int = 1200) -> str:
    """Un texte de réponse, replié en citation Markdown et borné."""
    plat = texte.strip() or "(vide)"
    if len(plat) > limite:
        plat = plat[:limite] + " […]"
    return "\n".join(f"> {ligne}" if ligne else ">" for ligne in plat.splitlines())


def rapport(releves: list[Releve], reglages) -> str:
    lignes = [
        "# Le relevé des parcours",
        "",
        "Ce document est ÉCRIT PAR `scripts/releve_des_parcours.py`. Il n'est pas",
        "rédigé à la main, et toute correction qu'on y apporterait serait perdue à",
        "la prochaine exécution. Il est la trace sur laquelle",
        "`docs/parcours-de-l-agent.md` est établi.",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        f"Catalogue : `{reglages.catalog_path}`",
        "",
        "    uv run python scripts/releve_des_parcours.py --markdown docs/releve-des-parcours.md",
        "",
        "## Le fil en un coup d'œil",
        "",
        "| # | message | nœuds traversés | appels LLM | artefacts | réponse |",
        "|---|---|---|---|---|---|",
    ]
    for r in releves:
        noeuds = " → ".join(n.nom for n in r.noeuds)
        arts = ", ".join(f"`{a.mime}`" for a in r.artefacts) or "—"
        lignes.append(
            f"| {r.message.numero} | {' '.join(r.message.texte.split())} | {noeuds} "
            f"| {r.appels_llm} | {arts} | {r.longueur} car. |"
        )
    lignes += [
        "",
        f"**Total : {sum(r.appels_llm for r in releves)} appels LLM** "
        f"pour {len(releves)} tours, "
        f"{sum(r.duree_ms for r in releves) / 1000:.0f} s.",
        "",
    ]
    for r in releves:
        lignes += [
            f"## Tour {r.message.numero} — « {' '.join(r.message.texte.split())} »",
            "",
            f"Ce qu'il montre : {r.message.montre}.",
            "",
            f"**{r.appels_llm} appel(s) LLM**, {r.duree_ms} ms, réponse de {r.longueur} car."
            + (f", plan : `{r.plan}`" if r.plan else "")
            + (f", source liée après le tour : `{r.source_liee}`" if r.source_liee else "")
            + (f", prédiction en attente : `{r.pending}`" if r.pending else "")
            + (f", **erreur : {r.erreur}**" if r.erreur else "")
            + ".",
            "",
            "| nœud | détail | ms |",
            "|---|---|---|",
        ]
        for n in r.noeuds:
            lignes.append(f"| `{n.nom}` | {' '.join(n.detail.split()) or '—'} | {n.duree_ms} |")
        lignes += ["", "Appels d'outil émis par le modèle, dans l'ordre :", ""]
        if r.outils:
            lignes += ["| # | outil | arguments |", "|---|---|---|"]
            for rang, appel in enumerate(r.outils, start=1):
                lignes.append(f"| {rang} | `{appel.outil}` | `{appel.arguments}` |")
        else:
            lignes.append("Aucun.")
        lignes += ["", "Artefacts rendus :", ""]
        if r.artefacts:
            lignes += ["| type | taille |", "|---|---|"]
            for a in r.artefacts:
                lignes.append(f"| `{a.mime}` | {a.octets} car. |")
        else:
            lignes.append("Aucun.")
        lignes += ["", "Réponse servie :", "", _bloc(r.reponse), ""]
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description="Le relevé des huit parcours de l'agent.")
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument(
        "--seulement",
        nargs="*",
        type=int,
        default=None,
        help="les numéros de messages à jouer ; les autres sont sautés, et le fil avec.",
    )
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
    # Une racine PAR EXÉCUTION : deux relevés qui partageraient un dossier
    # partageraient les tableaux du fil, et le tour 8 du second rappellerait
    # ceux du premier.
    suffixe = uuid.uuid4().hex[:8]
    racine = Path(reglages.workspace_dir) / f"releve-{suffixe}"
    racine.mkdir(parents=True, exist_ok=True)
    fil = f"parcours-{suffixe}"

    messages = [m for m in MESSAGES if not args.seulement or m.numero in args.seulement]
    releves: list[Releve] = []
    source_de_travail = ""
    echange_precedent: tuple[str, str] | None = None
    pending = None
    for message in messages:
        print(f"[{message.numero}/{len(MESSAGES)}] « {message.texte} »", flush=True)
        releve, source_de_travail, echange_precedent, pending = poser(
            orchestrateur,
            mouchard,
            message,
            fil=fil,
            racine=racine,
            source_de_travail=source_de_travail,
            echange_precedent=echange_precedent,
            pending=pending,
        )
        releves.append(releve)
        print(f"    nœuds : {' → '.join(n.nom for n in releve.noeuds)}")
        print(f"    outils : {', '.join(a.outil for a in releve.outils) or 'aucun'}")
        print(
            f"    {releve.appels_llm} appels LLM, {len(releve.artefacts)} artefact(s), "
            f"{releve.longueur} car., {releve.duree_ms} ms"
        )
        print(f"    réponse : {' '.join(releve.reponse.split())[:240]}\n", flush=True)

    texte = rapport(releves, reglages)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"Écrit : {args.markdown}")
    else:
        print(texte)
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **{k: v for k, v in r.__dict__.items() if k != "message"},
                        "numero": r.message.numero,
                        "message": r.message.texte,
                        "noeuds": [n.__dict__ for n in r.noeuds],
                        "outils": [o.__dict__ for o in r.outils],
                        "artefacts": [a.__dict__ for a in r.artefacts],
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
