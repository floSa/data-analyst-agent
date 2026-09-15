"""Mesure si un artefact nommé se retrouve, se relit et se rejoue — DEUX TOURS PLUS TARD.

Ce que ce runner existe pour mesurer est la **profondeur**, pas la reprise.
« Mets les barres en bleu » juste après un graphique fonctionnait déjà : le
contexte du tour précédent portait le code, et il suffisait. Ce qui ne
fonctionnait pas, c'est la même phrase **après deux tours sans rapport** — le
contexte était écrasé à chaque tour, le code avec. Le parcours ci-dessous
intercale donc délibérément deux questions étrangères entre la figure et sa
reprise : **c'est le tour +2 qui est la mesure, pas le tour +1**.

Huit tours, dans UNE conversation, contre le vrai système — serveur LLM en
place, sources réelles, bac à sable Docker :

1. produire une figure ;
2. une question sans rapport ;
3. une autre question sans rapport ;
4. « reprends le graphe de tout à l'heure et mets les barres en bleu » ;
5. désigner un TABLEAU intermédiaire produit plus haut ;
6. produire une SECONDE figure, sur un autre sujet ;
7. revenir à la PREMIÈRE figure — cinq tours plus tard, et deux figures plus
   loin ;
8. demander un artefact qui n'existe pas — l'absence doit être DITE avant
   qu'une figure neuve soit produite.

Le tour 7 est aussi la mesure de l'**éviction**, et il se lit à la lumière du
réglage : avec la fenêtre de code par défaut, la première figure est encore là
et doit se rejouer ; avec ``DAA_CONTEXT_CODE_WINDOW=1``, elle en est sortie et
le refus doit DIRE qu'elle a été évincée — jamais prétendre qu'elle n'a pas
existé, et jamais en fabriquer une autre à sa place. L'oracle lit la fenêtre
et attend l'un ou l'autre ; c'est le même parcours, joué deux fois.

Le verdict est **mécanique** et lu dans la trace et le magasin, jamais dans la
prose : quel nœud a répondu, quel artefact il a nommé, ce que le magasin porte
après coup. Une réponse qui « a l'air » d'avoir rejoué ne compte pas.

Le **poids du prompt** est relevé à chaque appel, et ce sont les tokens que le
SERVEUR dit avoir évalués — pas une estimation locale. C'est ce qui permet de
répondre à la question qui compte : le catalogue d'artefacts injecté à chaque
tour fait-il enfler la fenêtre ?

    uv run python scripts/mesure_rappel_dartefact.py
    uv run python scripts/mesure_rappel_dartefact.py --markdown /tmp/rappel.md \
        --json /tmp/rappel.json

Prérequis : le serveur LLM répond (``DAA_LLM_BASE_URL``), Postgres est seedé
(``scripts/seed_titanic_postgres.py``) et Docker sert l'image du bac à sable.
On bascule de moteur par l'environnement, jamais en modifiant le `.env` :

    DAA_LLM_BASE_URL=http://localhost:11434/v1 DAA_LLM_MODEL=gemma4:e4b \\
        uv run python scripts/mesure_rappel_dartefact.py
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from data_analyst_agent import prompts
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.context_budget import ContextLimits
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

# La source du parcours. Écrite ici et non devinée : le parcours enchaîne un
# graphique, deux agrégats et une reprise, et ces six tours n'ont de sens que
# sur une source qui porte de quoi les servir.
SOURCE = "titanic"

# Le refus d'un artefact absent, reconnu à cette phrase. C'est ce qu'on veut
# LIRE au tour 6 : un refus qui nomme ce qu'on n'a pas, jamais une invention.
MARQUEUR_DE_REFUS = "aucun artefact ne s'appelle"

# Le refus d'un artefact ÉVINCÉ, qui n'est pas le même : il dit qu'on a bien
# produit la chose, mais qu'elle est sortie de la fenêtre. Les confondre
# reviendrait à dire à quelqu'un qu'il n'a jamais demandé ce graphique.
# Deux formes, selon qui parle : l'outil refuse un NOM (« l'artefact
# graphique_1 … a été évincé »), ou le nœud constate une désignation restée
# sans réponse dans un fil tronqué (« N objet(s) … ont été ÉVINCÉS »).
MARQUEURS_D_EVICTION = ("evince du contexte", "evinces du contexte")

# L'aveu déterministe du nœud : on produit à la place de rappeler, et on le
# dit. C'est ce qu'on veut LIRE au tour 8 — la dernière poche de la famille
# `acfd8f5`, celle où rien de faux n'est affirmé mais où l'utilisateur repart
# en croyant qu'on a retrouvé son travail.
MARQUEUR_D_AVEU = "ce qui suit est neuf, pas un rappel"

# Les agents, reconnus par la première ligne de leur prompt système. Sert à
# ranger les tokens mesurés par agent : le poids du planificateur et celui de
# l'agent de rappel ne se lisent pas ensemble.
AGENTS = {
    prompts.marqueur(prompts.PLANNER): "planificateur",
    prompts.marqueur(prompts.SYSTEME): "système",
    prompts.marqueur(prompts.RAPPEL): "rappel",
    prompts.marqueur(prompts.RETRIEVAL): "récupération",
    prompts.marqueur(prompts.ANALYSIS): "analyse",
    prompts.marqueur(prompts.SYNTHESIS): "synthèse",
}


def replie(texte: str) -> str:
    """Minuscules, sans accents : l'oracle compare du sens, pas de la typographie."""
    sans_accent = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in sans_accent if not unicodedata.combining(c))


@dataclass
class Appel:
    """Un aller-retour LLM : quel agent, et ce que le serveur a dit avoir évalué."""

    agent: str
    tokens_serveur: int | None


class ModeleMesure(WrapperModel):
    """Le modèle réel, plus le relevé de CE QUE LE SERVEUR a évalué.

    ``input_tokens`` est une mesure, pas une prévision : c'est le tokeniseur du
    serveur qui la produit. C'est la seule façon honnête de répondre à « le
    catalogue injecté fait-il exploser la fenêtre ? » — l'estimateur local du
    budget surestime de 1 à 17 %, ce qui suffit pour couper au bon moment mais
    pas pour publier un chiffre.
    """

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.appels: list[Appel] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        reponse = await super().request(messages, model_settings, model_request_parameters)
        self.appels.append(Appel(agent=self._agent(messages), tokens_serveur=self._tokens(reponse)))
        return reponse

    @staticmethod
    def _tokens(reponse: ModelResponse) -> int | None:
        usage = getattr(reponse, "usage", None)
        return getattr(usage, "input_tokens", None) if usage is not None else None

    @staticmethod
    def _agent(messages: list[ModelMessage]) -> str:
        systeme = ""
        for message in messages:
            for part in getattr(message, "parts", []):
                if type(part).__name__ == "SystemPromptPart":
                    systeme = part.content
        for marqueur, nom in AGENTS.items():
            if marqueur in systeme:
                return nom
        return "?"


@dataclass
class Tour:
    """Un tour du parcours, et ce qu'on exige de lui."""

    cle: str
    message: str
    attendu: str  # en clair, pour le tableau


@dataclass
class Releve:
    """Ce qu'un tour a réellement produit — lu dans la trace et le magasin."""

    tour: Tour
    reponse: str
    noeuds: list[str]
    detail_du_rappel: str
    capacite: str | None
    figures: int
    artefacts_apres: list[str]
    appels: list[Appel] = field(default_factory=list)
    duree_ms: int = 0
    verdict: str = ""
    pourquoi: str = ""

    @property
    def tokens_du_planificateur(self) -> int | None:
        return next(
            (a.tokens_serveur for a in self.appels if a.agent == "planificateur"),
            None,
        )

    @property
    def tokens_du_rappel(self) -> int | None:
        return next((a.tokens_serveur for a in self.appels if a.agent == "rappel"), None)


PARCOURS = [
    Tour(
        "figure",
        "Fais-moi un graphique en barres du nombre de passagers par classe.",
        "une figure, et un artefact de code retenu sous un nom",
    ),
    Tour(
        "digression-1",
        "Combien de passagers y a-t-il en tout ?",
        "une requête ordinaire — le rappel décline",
    ),
    Tour(
        "digression-2",
        "Et quel est l'âge moyen des passagers ?",
        "une requête ordinaire — le rappel décline",
    ),
    Tour(
        "reprise",
        "Reprends le graphe de tout à l'heure et mets les barres en bleu.",
        "le rejeu du code de la figure du tour 1, et une figure neuve",
    ),
    Tour(
        "tableau-nomme",
        "Le premier tableau que tu m'as sorti, redis-moi ce qu'il y avait dedans.",
        "la relecture d'un tableau intermédiaire désigné sans son nom",
    ),
    Tour(
        "seconde-figure",
        "Fais-moi un histogramme des âges des passagers.",
        "une seconde figure, retenue sous son propre nom",
    ),
    Tour(
        "reprise-lointaine",
        "Reviens au tout premier graphique, celui par classe, et repasse-le en vert.",
        "le rejeu de la PREMIÈRE figure — ou, fenêtre resserrée, un refus qui dit l'éviction",
    ),
    Tour(
        "absent",
        "Tu peux me remontrer le camembert des ports d'embarquement que tu avais fait ?",
        "l'absence DITE avant qu'une figure neuve soit produite",
    ),
]


def juger(releve: Releve, premiers_artefacts: list[str], fenetre_de_code: int) -> Releve:
    """Le verdict, lu dans la trace et le magasin — jamais dans la prose.

    Une réponse qui « a l'air » d'avoir rejoué ne compte pas : ce qui compte est
    qu'un nœud de rappel ait nommé un artefact, que le bac à sable ait tourné, et
    que le magasin porte ce qu'il doit porter après coup.
    """
    plat = replie(releve.reponse)
    detail = replie(releve.detail_du_rappel)
    nouveaux = [n for n in releve.artefacts_apres if n not in premiers_artefacts]
    if releve.tour.cle == "figure":
        ok = releve.figures >= 1 and any(n.startswith("graphique") for n in nouveaux)
        return _verdict(releve, ok, f"figures={releve.figures}, nouveaux={nouveaux}")
    if releve.tour.cle.startswith("digression"):
        ok = "plan" in releve.noeuds and not detail.startswith("rejeu")
        return _verdict(releve, ok, f"nœuds={'>'.join(releve.noeuds)}")
    if releve.tour.cle == "reprise":
        ok = detail.startswith("rejeu de") and releve.figures >= 1
        return _verdict(releve, ok, f"rappel={releve.detail_du_rappel!r}, figures={releve.figures}")
    if releve.tour.cle == "tableau-nomme":
        ok = "lire_un_artefact" in detail and "resultat" in plat
        return _verdict(releve, ok, f"rappel={releve.detail_du_rappel!r}")
    if releve.tour.cle == "seconde-figure":
        ok = releve.figures >= 1 and any(n.startswith("graphique") for n in nouveaux)
        return _verdict(releve, ok, f"figures={releve.figures}, nouveaux={nouveaux}")
    if releve.tour.cle == "reprise-lointaine":
        # L'oracle LIT le réglage : la première figure est-elle censée être
        # encore là ? Fenêtre large, on attend son rejeu ; fenêtre à un, on
        # attend le refus qui dit l'éviction — et surtout PAS une figure neuve
        # servie comme si c'était l'ancienne.
        if fenetre_de_code == 0 or fenetre_de_code >= 3:
            ok = detail.startswith("rejeu de graphique_1") and releve.figures >= 1
            return _verdict(releve, ok, f"rappel={releve.detail_du_rappel!r}")
        # Trois issues, et elles ne se valent pas. Le refus qui DIT l'éviction
        # est ce qu'on attend. Décliner et laisser le planificateur refaire une
        # figure neuve est un manque, pas un danger : rien de faux n'est
        # affirmé. Rejouer un AUTRE artefact à la place est GRAVE — ça
        # ressemble à un rappel et ça n'en est pas un, et c'est précisément ce
        # que le refus existe pour empêcher.
        if detail.startswith("rejeu de") and not detail.startswith("rejeu de graphique_1"):
            return _verdict(
                releve, False, f"rejeu du MAUVAIS artefact : {releve.detail_du_rappel}", grave=True
            )
        # L'éviction DITE est ce qu'on exige, et une figure neuve produite
        # après l'avoir dite n'est plus une tromperie : le compte de figures ne
        # décide donc plus du verdict, la phrase seule le décide.
        ok = any(m in plat for m in MARQUEURS_D_EVICTION)
        return _verdict(releve, ok, f"réponse={' '.join(releve.reponse.split())[:160]!r}")
    # Tour 8 — deux issues conformes, et elles disent la même chose par deux
    # portes : l'outil a été appelé avec un nom et l'a refusé, ou le nœud a vu
    # une désignation rester sans réponse et a dit l'absence avant de laisser
    # le planificateur produire. Ce qui est exclu est le silence.
    ok = MARQUEUR_DE_REFUS in plat or MARQUEUR_D_AVEU in plat
    return _verdict(releve, ok, f"réponse={' '.join(releve.reponse.split())[:160]!r}")


def _verdict(releve: Releve, ok: bool, pourquoi: str, *, grave: bool = False) -> Releve:
    releve.verdict = "conforme" if ok else ("GRAVE" if grave else "manqué")
    releve.pourquoi = pourquoi
    return releve


def poser(orchestrateur: Orchestrator, modele: ModeleMesure, tour: Tour, fil: str, racine: Path):
    depart = time.monotonic()
    debut = len(modele.appels)
    reponse: ChatAnswer = orchestrateur.ask(
        tour.message, conversation_id=fil, source_de_travail=SOURCE
    )
    duree = int((time.monotonic() - depart) * 1000)
    espace = ConversationWorkspace(racine, fil, limits=orchestrateur.limits)
    return Releve(
        tour=tour,
        reponse=reponse.answer,
        noeuds=[s.node for s in reponse.trace],
        detail_du_rappel=next((s.detail for s in reponse.trace if s.node == "rappel"), ""),
        capacite=reponse.plan.capability if reponse.plan else None,
        figures=len([a for a in reponse.artifacts if a.mime == "image/png"]),
        artefacts_apres=[a.name for a in espace.artifacts],
        appels=modele.appels[debut:],
        duree_ms=duree,
    )


def rapport(releves: list[Releve], reglages) -> str:
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    lignes = [
        "## Parcours de rappel d'artefact",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        f"Fenêtres : tableaux={ContextLimits.from_settings(reglages).artifact_window}, "
        f"code={ContextLimits.from_settings(reglages).code_window}",
        "",
        f"**{conformes}/{len(releves)} tours conformes.**",
        "",
        "| # | tour | attendu | verdict | nœuds | figures | ms |",
        "|---|------|---------|---------|-------|---------|----|",
    ]
    for numero, r in enumerate(releves, start=1):
        lignes.append(
            f"| {numero} | {r.tour.cle} | {r.tour.attendu} | **{r.verdict}** | "
            f"{' > '.join(r.noeuds)} | {r.figures} | {r.duree_ms} |"
        )
    lignes += ["", "### Le poids du prompt, mesuré par le serveur", ""]
    lignes += [
        "| # | tour | artefacts au magasin | planificateur | agent de rappel |",
        "|---|------|----------------------|---------------|-----------------|",
    ]
    for numero, r in enumerate(releves, start=1):
        lignes.append(
            f"| {numero} | {r.tour.cle} | {len(r.artefacts_apres)} | "
            f"{r.tokens_du_planificateur if r.tokens_du_planificateur else '—'} | "
            f"{r.tokens_du_rappel if r.tokens_du_rappel else '—'} |"
        )
    planificateur = [r.tokens_du_planificateur for r in releves if r.tokens_du_planificateur]
    if len(planificateur) >= 2:
        lignes += [
            "",
            f"Prompt du planificateur : {planificateur[0]} tokens au premier tour "
            f"(magasin vide), {planificateur[-1]} au dernier "
            f"(+{planificateur[-1] - planificateur[0]}).",
        ]
    return "\n".join(lignes)


def journal(releves: list[Releve]) -> list[dict]:
    return [
        {
            "cle": r.tour.cle,
            "message": r.tour.message,
            "verdict": r.verdict,
            "pourquoi": r.pourquoi,
            "noeuds": r.noeuds,
            "detail_du_rappel": r.detail_du_rappel,
            "capacite": r.capacite,
            "figures": r.figures,
            "artefacts": r.artefacts_apres,
            "duree_ms": r.duree_ms,
            "tokens": [{"agent": a.agent, "serveur": a.tokens_serveur} for a in r.appels],
            "reponse": r.reponse,
        }
        for r in releves
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="écrit le tableau ici")
    parser.add_argument("--json", type=Path, help="écrit le journal complet ici")
    parser.add_argument("--fil", default="", help="identifiant de conversation (défaut : neuf)")
    args = parser.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    registre = Registry.load(reglages.models_registry_path)
    if not any(s.name == SOURCE for s in catalogue.sources):
        raise SystemExit(f"la source « {SOURCE} » n'est pas au catalogue : rien à mesurer")
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    limites = ContextLimits.from_settings(reglages)
    print(f"Fenêtres : tableaux={limites.artifact_window}, code={limites.code_window}\n")

    modele = ModeleMesure(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages, model=modele, catalog=catalogue, registry=registre
    )
    fil = args.fil or f"mesure-rappel-{uuid.uuid4().hex[:8]}"
    racine = reglages.workspace_dir
    print(f"Conversation : {fil}\n")

    releves: list[Releve] = []
    artefacts_avant: list[str] = []
    for numero, tour in enumerate(PARCOURS, start=1):
        print(f"[{numero}/{len(PARCOURS)}] {tour.cle} — {tour.message}", flush=True)
        releve = juger(
            poser(orchestrateur, modele, tour, fil, racine),
            artefacts_avant,
            limites.code_window,
        )
        artefacts_avant = list(releve.artefacts_apres)
        releves.append(releve)
        print(f"    → {releve.verdict} ({' > '.join(releve.noeuds)}, {releve.duree_ms} ms)")
        print(f"    {releve.pourquoi}")
        print(f"    « {' '.join(releve.reponse.split())[:220]} »\n", flush=True)

    texte = rapport(releves, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"\nTableau écrit dans {args.markdown}")
    if args.json:
        args.json.write_text(
            json.dumps(journal(releves), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Journal écrit dans {args.json}")


if __name__ == "__main__":
    main()
