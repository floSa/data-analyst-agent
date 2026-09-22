"""La mémoire de conversation, mesurée : six fils, et ce que l'agent en fait.

Un tableau produit dans un fil est enregistré comme **source éphémère** — nom,
colonnes, nombre de lignes, la question qui l'a produit, la source d'origine,
et un drapeau qui dit s'il est tronqué (cf.
:class:`~data_analyst_agent.orchestrator.workspace.WorkspaceArtifact`). Le
magasin existe, il est écrit, il est relu. La question que ce runner pose est
la suivante : **est-ce que l'agent S'EN SERT, et sait-il dire ce qu'il a ?**

Ce runner ne juge rien. Il n'a pas d'oracle, pas de verdict, pas de score. Il
RELÈVE, comme `scripts/releve_des_parcours.py` dont il reprend la mécanique de
fil : pour chaque tour, les nœuds traversés avec leur détail, les appels
d'outil réellement émis par le modèle AVEC leurs arguments, le nombre d'appels
LLM, les artefacts produits, la réponse — et, en plus, **ce que la mémoire de
conversation a injecté dans le prompt, nœud par nœud, en caractères**.

**Le fil est un VRAI fil.** « fais-moi un graphique de leur âge » n'a de sens
qu'après « extrais-moi les passagères qui ont survécu ». Ce que l'API reporte
d'un tour au suivant est donc reporté ici, à l'identique (cf. la route
``/chat`` de `api/app.py`) : ``source_de_travail``, ``echange_precedent``,
``pending``, ``workspace_root``.

**Chaque fil commence par une AMORCE**, et ce n'est pas une facilité. Sur un
catalogue à plusieurs sources, la première question de données d'un fil neuf
n'interroge rien : ``_regle_choisir_la_source`` propose l'inventaire et attend
qu'on choisisse (``docs/surface-conversationnelle.md`` §14). Sans un tour qui
lie la source, les six fils mesureraient tous la même chose — la proposition de
sources — et aucun ne produirait de tableau. L'amorce est un vrai tour, relevé
comme les autres, mais elle ne compte pas dans les chiffres : elle n'est pas ce
qu'on mesure.

**Le mouchard d'injection** enveloppe les quatre endroits où la mémoire de
conversation entre dans un prompt, et note le nom de la fonction APPELANTE :
c'est ainsi qu'on sait dans quels nœuds l'inventaire est injecté, et dans
lesquels il ne l'est pas, sans le déduire d'une lecture du code.

    uv run python scripts/mesure_memoire_de_conversation.py
    uv run python scripts/mesure_memoire_de_conversation.py \
        --markdown docs/releve-de-la-memoire-de-conversation.md
    uv run python scripts/mesure_memoire_de_conversation.py --fils A D --tirages 1

``--depuis-json`` réécrit le rapport depuis le journal d'une campagne déjà
jouée, sans rien rejouer : corriger une tournure du rapport ne doit pas coûter
une campagne, et surtout pas une campagne DIFFÉRENTE de celle que le document
cite.

Prérequis : Postgres joignable (bases ``titanic`` et ``daa_demonstration``,
`scripts/seed_titanic_postgres.py` et `scripts/seed_catalogue_demonstration.py`),
les sources non suivies par git présentes sous ``sources/`` (``*.duckdb``,
``*.xlsx``), le bac à sable disponible, le serveur LLM en place. **Le catalogue
n'est pas celui du `.env` : chaque fil déclare le sien**, et le relevé l'imprime.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from releve_des_parcours import AppelDOutil, ModeleMouchard

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import Settings, get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator import rappel as module_rappel
from data_analyst_agent.orchestrator.context_budget import ContextLimits, estimate_tokens
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

RACINE = Path(__file__).resolve().parents[1]
CATALOGUE_PAR_DEFAUT = RACINE / "sources" / "catalogue.yaml"
CATALOGUE_DEMONSTRATION = RACINE / "sources" / "demonstration" / "catalogue.yaml"


# --- les six fils ------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """Un message du fil, et ce qu'on cherche à en apprendre.

    ``cherche`` n'est pas un oracle : rien n'est comparé à lui. C'est la raison
    pour laquelle ce message-là est dans la liste, écrite ici et pas dans le
    document, pour que les deux ne dérivent pas l'un de l'autre.

    ``amorce`` marque le tour qui LIE la source. Il est joué et relevé comme
    les autres — un fil dont on cacherait un tour ne serait plus le fil qu'on
    prétend mesurer — mais il est exclu des comptages : ce n'est pas lui qu'on
    mesure, et le compter ferait passer chaque fil pour un tour de plus.
    """

    numero: int
    texte: str
    cherche: str
    amorce: bool = False


@dataclass(frozen=True)
class Fil:
    """Un fil : sa lettre, ce qu'il cherche, son catalogue, ses messages.

    Le catalogue est porté par le FIL et non par le runner, parce que les six
    ne partagent pas le même : le fil A tient sur le catalogue par défaut (le
    Titanic, que la question de floSa nomme), les cinq autres sur celui de
    démonstration. Un runner à catalogue unique aurait mesuré cinq fils sur une
    base qui n'a pas les sources qu'ils citent, et n'aurait rien dit.
    """

    cle: str
    titre: str
    cherche: str
    catalogue: Path
    messages: tuple[Message, ...]

    @property
    def mesures(self) -> tuple[Message, ...]:
        return tuple(m for m in self.messages if not m.amorce)


FILS: tuple[Fil, ...] = (
    Fil(
        cle="A",
        titre="Repartir du tableau, pas de la base",
        cherche=(
            "le tour 2 repart-il du tableau du tour 1, ou relance-t-il un SQL complet "
            "sur la base ? Les DEUX aboutissent peut-être à une bonne figure : ce n'est "
            "pas le résultat qu'on mesure ici, c'est le CHEMIN"
        ),
        catalogue=CATALOGUE_PAR_DEFAUT,
        messages=(
            Message(
                0,
                "travaillons sur titanic",
                "lie la source, sans quoi le tour 1 n'interroge rien",
                amorce=True,
            ),
            Message(
                1,
                "extrais-moi les passagères qui ont survécu",
                "un tableau, et on verra s'il est tronqué",
            ),
            Message(
                2,
                "fais-moi un graphique de leur âge",
                "le chemin : SQL neuf sur la base, ou lecture du tableau d'avant",
            ),
        ),
    ),
    Fil(
        cle="B",
        titre="L'inventaire, su et dit",
        cherche=(
            "sait-il énumérer ses objets, les distinguer, et dire ce que chacun "
            "contient — ou invente-t-il, ou se tait-il ?"
        ),
        catalogue=CATALOGUE_DEMONSTRATION,
        messages=(
            Message(0, "travaillons sur interventions", "lie la source", amorce=True),
            Message(
                1,
                "les 10 interventions les plus longues, avec leur station et leur nature",
                "un tableau",
            ),
            Message(2, "fais-moi un graphique du nombre d'interventions par nature", "une figure"),
            Message(
                3,
                "qu'est-ce que tu as en mémoire dans cette conversation ?",
                "l'inventaire de ses propres objets",
            ),
            Message(
                4,
                "et le premier tableau, il contenait quoi ?",
                "le contenu d'un objet désigné par son rang, pas par son nom",
            ),
        ),
    ),
    Fil(
        cle="C",
        titre="Sources primaires et sources transformées",
        cherche=(
            "distingue-t-il les sources du catalogue de celles que la conversation a "
            "fabriquées ? Les nomme-t-il toutes les deux ?"
        ),
        catalogue=CATALOGUE_DEMONSTRATION,
        messages=(
            Message(0, "travaillons sur referentiel", "lie la source", amorce=True),
            Message(
                1, "donne-moi les 10 stations les plus récemment mises en service", "un tableau"
            ),
            Message(
                2,
                "quelles données as-tu à ta disposition maintenant ?",
                "le catalogue déclaré, le tableau fabriqué, ou les deux",
            ),
        ),
    ),
    Fil(
        cle="D",
        titre="La tranche",
        cherche=(
            "dit-il que le tableau est tronqué, ou donne-t-il le compte de la tranche "
            "comme s'il était le total ? Le drapeau existe dans le magasin ; la "
            "question est s'il arrive à l'utilisateur"
        ),
        catalogue=CATALOGUE_DEMONSTRATION,
        messages=(
            Message(0, "travaillons sur interventions", "lie la source", amorce=True),
            Message(
                1,
                "liste-moi toutes les interventions de maintenance",
                "900 lignes en source, 200 au plafond : le tableau est une tranche",
            ),
            Message(
                2,
                "combien de lignes dans ce tableau ?",
                "200 (la tranche), 900 (le tout), ou 200 en le DISANT",
            ),
        ),
    ),
    Fil(
        cle="E",
        titre="Témoin — ce qui ne doit PAS repartir de la mémoire",
        cherche="le tour 2 doit interroger la base, pas le tableau d'à côté",
        catalogue=CATALOGUE_DEMONSTRATION,
        messages=(
            Message(0, "travaillons sur interventions", "lie la source", amorce=True),
            Message(1, "les 10 interventions les plus longues", "un tableau sur interventions"),
            Message(
                2,
                "dans facturation, combien de factures ont été émises en 2025 ?",
                "une autre source, sans rapport : le tableau d'à côté ne doit pas servir",
            ),
        ),
    ),
    Fil(
        cle="F",
        titre="Témoin — le fil long",
        cherche="retrouve-t-il encore le tableau du premier tour, et à quel coût en contexte ?",
        catalogue=CATALOGUE_DEMONSTRATION,
        messages=(
            Message(0, "travaillons sur interventions", "lie la source", amorce=True),
            Message(1, "les 10 interventions les plus longues", "objet 1"),
            Message(2, "combien d'interventions par nature ?", "objet 2"),
            Message(3, "les 5 techniciens qui sont intervenus le plus souvent", "objet 3"),
            Message(4, "combien d'interventions par mois de signalement ?", "objet 4"),
            Message(5, "les 10 stations les plus souvent en panne", "objet 5"),
            Message(6, "la durée moyenne d'indisponibilité par nature", "objet 6"),
            Message(7, "les interventions signalées en décembre 2025", "objet 7"),
            Message(8, "combien d'interventions par technicien ?", "objet 8"),
            Message(
                9,
                "reprends le tableau du tout premier tour",
                "huit objets plus tard, le premier est-il encore atteignable ?",
            ),
        ),
    ),
)

FILS_PAR_CLE = {f.cle: f for f in FILS}


# --- le mouchard d'injection --------------------------------------------------


@dataclass
class Injection:
    """Un morceau de mémoire de conversation entré dans un prompt.

    ``appelant`` est le nom de la fonction qui a demandé le texte, lu dans la
    pile. C'est lui qui répond à « dans QUELS nœuds l'inventaire est-il
    injecté ? » sans qu'on ait à le déduire d'une lecture du code : un nœud qui
    ne demande rien n'apparaît pas, et c'est un fait mesuré.
    """

    quoi: str  # cf. TEXTES_INJECTES et les deux relevés à part
    appelant: str
    noeud: str
    octets: int
    texte: str = ""


# À quel nœud du graphe appartient chaque fonction appelante. Écrit ici plutôt
# que deviné : `_peser_le_prompt` est du nœud `plan` et ne le dit pas dans son
# nom, et une correspondance fausse ferait mentir le tableau des coûts.
NOEUD_DE_L_APPELANT = {
    "_peser_le_prompt": "plan",
    "_plan_node": "plan",
    "_appliquer_les_regles": "plan",
    "_repli_du_planificateur": "plan",
    "_rejouer_un_code": "rappel",
    # `fit_to_budget` PÈSE le catalogue avant de décider ce qu'il en garde : il
    # appelle `describe()` en boucle sans rien injecter. C'est du nœud `plan`,
    # mais ce n'est pas de l'injection — cf. `PESEE`.
    "fit_to_budget": "plan",
    "system_prompt": "rappel",
    "_rappel_node": "rappel",
    "_decor_de_donnees": "analysis",
    "_analysis_node": "analysis",
    "_retrieval_node": "retrieval",
    "_system_node": "system",
    "_fetch_predict_node": "fetch_predict",
}

# Ce qui entre RÉELLEMENT dans un prompt, et qu'on compte en caractères.
TEXTES_INJECTES = ("describe", "catalogue_du_rappel", "montage_sandbox")
# La pesée du budget : le même texte, demandé pour le mesurer et non pour
# l'envoyer. Compté à part, sinon chaque tour paierait deux à trois fois son
# catalogue dans le tableau des coûts.
PESEE = "describe_pesee"
# Les tableaux de la conversation exposés comme SOURCES interrogeables. Ce
# n'est pas un texte : c'est une liste de noms, et la compter en caractères
# ferait croire qu'elle pèse dix octets dans un prompt.
SOURCES_EPHEMERES = "sources_ephemeres"


@dataclass
class Mouchard:
    """Ce que la mémoire de conversation a injecté pendant UN tour.

    Remis à zéro entre deux tours par :meth:`ouvrir`. Les enveloppes restent
    posées pour toute la durée du processus : les reposer à chaque tour serait
    autant d'occasions d'en oublier une.
    """

    injections: list[Injection] = field(default_factory=list)
    # Ce que le nœud d'analyse a monté sous /data/ : nom de fichier -> origine.
    # `source` = table matérialisée depuis la base, `memoire` = tableau du fil.
    montages: dict[str, str] = field(default_factory=dict)
    actif: bool = False

    def ouvrir(self) -> None:
        self.injections = []
        self.montages = {}
        self.actif = True

    def noter(self, quoi: str, texte: str | None, profondeur: int = 2) -> None:
        if not self.actif or not texte:
            return
        appelant = sys._getframe(profondeur).f_code.co_name
        self.injections.append(
            Injection(
                quoi=quoi,
                appelant=appelant,
                noeud=NOEUD_DE_L_APPELANT.get(appelant, appelant),
                octets=len(texte),
                texte=texte,
            )
        )


MOUCHARD = Mouchard()


def poser_les_enveloppes() -> None:
    """Enveloppe les quatre points d'entrée de la mémoire dans les prompts.

    Aucun n'est modifié : chacun est appelé, son résultat est noté, puis rendu
    tel quel. C'est la seule façon de mesurer ce qui entre RÉELLEMENT dans un
    prompt — le reconstituer à côté donnerait un chiffre voisin, qu'aucune
    exécution n'aurait produit, et qui divergerait au premier changement.

    Les quatre, et pourquoi ceux-là :

    - ``ConversationWorkspace.describe`` — le catalogue des objets pour le
      PLANIFICATEUR (`_peser_le_prompt`) ;
    - ``rappel.catalogue_pour_le_prompt`` — le catalogue pour l'agent de
      RAPPEL, une ligne par artefact ;
    - ``Orchestrator._mount_workspace`` — ce qu'on dit au code d'ANALYSE des
      tableaux montés sous ``/data/`` ;
    - ``Orchestrator._effective_catalog`` — les tableaux vus comme des SOURCES
      interrogeables, et par qui.

    ``_decor_de_donnees`` est enveloppé en plus, mais pour autre chose : il ne
    dit pas ce qui entre dans un prompt, il dit ce que le tour a RELU dans la
    base pour le monter en CSV. C'est le coût qu'un tour paie quand il ne
    repart pas du tableau.
    """
    describe_origine = ConversationWorkspace.describe

    def describe(self: ConversationWorkspace) -> str | None:
        texte = describe_origine(self)
        # Le même texte sert deux fois : `fit_to_budget` le PÈSE pour décider
        # ce qu'il garde, `_peser_le_prompt` l'INJECTE. Les confondre ferait
        # payer deux à trois fois son catalogue à chaque tour.
        depuis = sys._getframe(1).f_code.co_name
        MOUCHARD.noter(PESEE if depuis == "fit_to_budget" else "describe", texte)
        return texte

    ConversationWorkspace.describe = describe  # type: ignore[method-assign]

    catalogue_origine = module_rappel.catalogue_pour_le_prompt

    def catalogue_pour_le_prompt(workspace: ConversationWorkspace) -> str:
        texte = catalogue_origine(workspace)
        MOUCHARD.noter("catalogue_du_rappel", texte)
        return texte

    module_rappel.catalogue_pour_le_prompt = catalogue_pour_le_prompt

    mount_origine = Orchestrator._mount_workspace

    def _mount_workspace(state, data_files, data_context, objets):
        avant = len(data_context)
        contexte, tronques = mount_origine(state, data_files, data_context, objets)
        MOUCHARD.noter("montage_sandbox", contexte[avant:].strip())
        return contexte, tronques

    Orchestrator._mount_workspace = staticmethod(_mount_workspace)  # type: ignore[method-assign]

    effectif_origine = Orchestrator._effective_catalog

    def _effective_catalog(self: Orchestrator, state):
        catalogue = effectif_origine(self, state)
        ajoutes = len(catalogue.sources) - len(self.catalog.sources)
        if ajoutes > 0:
            noms = [s.name for s in catalogue.sources[len(self.catalog.sources) :]]
            MOUCHARD.noter(SOURCES_EPHEMERES, ", ".join(noms))
        return catalogue

    Orchestrator._effective_catalog = _effective_catalog  # type: ignore[method-assign]

    decor_origine = Orchestrator._decor_de_donnees

    def _decor_de_donnees(self: Orchestrator, state, source, objets):
        @contextlib.contextmanager
        def enveloppe():
            with decor_origine(self, state, source, objets) as (fichiers, contexte, avis):
                if MOUCHARD.actif:
                    memoire = set()
                    workspace = state.get("workspace")
                    if workspace is not None:
                        memoire = {a.file for a in workspace.injected}
                    for nom in fichiers.values():
                        MOUCHARD.montages[nom] = "memoire" if nom in memoire else "source"
                yield fichiers, contexte, avis

        return enveloppe()

    Orchestrator._decor_de_donnees = _decor_de_donnees  # type: ignore[method-assign]


# --- le relevé d'un tour ------------------------------------------------------


@dataclass
class Noeud:
    """Un nœud traversé : son nom, ce qu'il a dit de lui-même, sa durée, son prompt.

    ``prompt_tokens`` est l'estimation locale décomptée AVANT l'appel,
    ``server_prompt_tokens`` ce que le serveur dit avoir évalué (cf.
    ``TraceStep``). Seul le nœud ``plan`` les renseigne aujourd'hui ; c'est
    aussi le seul dont le prompt porte le catalogue des objets, et c'est donc
    là qu'on peut dire quelle PART du prompt la mémoire occupe — un chiffre en
    caractères ne le dit pas tout seul.
    """

    nom: str
    detail: str
    duree_ms: int
    prompt_tokens: int | None = None
    server_prompt_tokens: int | None = None


@dataclass
class Artefact:
    """Un objet du magasin, tel que le manifeste le décrit après le tour."""

    nom: str
    kind: str
    lignes: int
    colonnes: list[str]
    tronque: bool
    question: str


@dataclass
class Releve:
    """Ce qu'un tour a fait, tel qu'on le relève. Aucun verdict."""

    fil: str
    tirage: int
    numero: int
    message: str
    cherche: str
    amorce: bool
    noeuds: list[Noeud] = field(default_factory=list)
    outils: list[AppelDOutil] = field(default_factory=list)
    appels_llm: int = 0
    artefacts_rendus: list[str] = field(default_factory=list)
    reponse: str = ""
    erreur: str | None = None
    plan: str = ""
    plan_source: str = ""
    plan_capacite: str = ""
    sql: str = ""
    source_liee: str = ""
    duree_ms: int = 0
    # la mémoire de conversation
    injections: list[Injection] = field(default_factory=list)
    montages: dict[str, str] = field(default_factory=dict)
    magasin: list[Artefact] = field(default_factory=list)
    code_produit: str = ""

    # -- ce qui se déduit du relevé, et rien de plus --------------------------

    @property
    def noms_des_noeuds(self) -> list[str]:
        return [n.nom for n in self.noeuds]

    @property
    def objets_avant(self) -> list[str]:
        """Les objets que le magasin portait AVANT ce tour.

        Le manifeste est lu APRÈS ; ce que ce tour a produit porte sa propre
        question, et c'est ce qui permet de l'en retirer sans relire le disque
        deux fois.
        """
        return [a.nom for a in self.magasin if a.question != self.message]

    @property
    def repart_dun_objet(self) -> bool:
        """Ce tour s'appuie-t-il sur un objet déjà produit ?

        Trois signatures, et il suffit d'une. Le plan DÉSIGNE un objet du
        magasin comme source ; l'agent de rappel a lu ou rejoué un objet ; le
        code généré ouvre un CSV de la mémoire monté sous ``/data/``. Les trois
        se lisent dans le relevé, aucune ne demande d'interpréter une phrase.
        """
        avant = set(self.objets_avant)
        if self.plan_source in avant:
            return True
        if any(o.outil in ("lire_un_artefact", "rejouer_un_code") for o in self.outils):
            return True
        return any(
            f"/data/{nom}" in self.code_produit
            for nom, origine in self.montages.items()
            if origine == "memoire"
        )

    @property
    def relance_un_sql(self) -> bool:
        """Ce tour a-t-il fait écrire du SQL neuf sur une source ?"""
        return bool(self.sql)

    @property
    def rematerialise_la_base(self) -> bool:
        """Ce tour a-t-il relu la base entière pour la monter en CSV ?

        C'est le coût invisible du chemin « repartir de la base » : le nœud
        d'analyse matérialise CHAQUE table de la source, même quand le code
        qu'il va produire ne lira qu'un tableau de la mémoire.
        """
        return any(origine == "source" for origine in self.montages.values())

    def injecte(self, quoi: str) -> int:
        return sum(i.octets for i in self.injections if i.quoi == quoi)

    @property
    def octets_injectes(self) -> int:
        """Ce que la mémoire de conversation a réellement mis dans les prompts.

        La pesée du budget et la liste des sources éphémères en sont exclues :
        la première mesure un texte qu'elle n'envoie pas, la seconde n'est pas
        un texte (cf. ``PESEE`` et ``SOURCES_EPHEMERES``).
        """
        return sum(i.octets for i in self.injections if i.quoi in TEXTES_INJECTES)


def poser(
    orchestrateur: Orchestrator,
    modele: ModeleMouchard,
    fil: Fil,
    message: Message,
    tirage: int,
    *,
    conversation: str,
    racine: Path,
    source_de_travail: str,
    echange_precedent: tuple[str, str] | None,
    pending,
) -> tuple[Releve, str, tuple[str, str], object]:
    """Un tour, posé comme l'API le pose, et ce qu'il faut reporter au suivant."""
    avant_appels = modele.appels
    avant_outils = len(modele.outils)
    MOUCHARD.ouvrir()
    depart = time.monotonic()
    reponse = orchestrateur.ask(
        message.texte,
        conversation_id=conversation,
        workspace_root=racine,
        # Toujours une CHAÎNE, jamais None : c'est ce que l'API passe, et le
        # court-circuit de choix de source en dépend.
        source_de_travail=source_de_travail,
        echange_precedent=echange_precedent,
        pending=pending,
    )
    duree = int((time.monotonic() - depart) * 1000)
    MOUCHARD.actif = False

    magasin = _magasin(racine, conversation)
    releve = Releve(
        fil=fil.cle,
        tirage=tirage,
        numero=message.numero,
        message=message.texte,
        cherche=message.cherche,
        amorce=message.amorce,
        noeuds=[
            Noeud(s.node, s.detail, s.duration_ms, s.prompt_tokens, s.server_prompt_tokens)
            for s in reponse.trace
        ],
        outils=list(modele.outils[avant_outils:]),
        appels_llm=modele.appels - avant_appels,
        artefacts_rendus=[a.mime for a in reponse.artifacts],
        reponse=reponse.answer,
        erreur=reponse.error,
        plan=(
            f"{reponse.plan.capability} · source={reponse.plan.source or '—'}"
            if reponse.plan is not None
            else ""
        ),
        plan_source=(reponse.plan.source or "") if reponse.plan is not None else "",
        plan_capacite=reponse.plan.capability if reponse.plan is not None else "",
        sql=_sql_du_tour(reponse.trace),
        source_liee=reponse.source_de_travail or "",
        duree_ms=duree,
        injections=list(MOUCHARD.injections),
        montages=dict(MOUCHARD.montages),
        magasin=magasin,
        code_produit=_code_du_tour(racine, conversation, magasin, message.texte),
    )
    return (
        releve,
        reponse.source_de_travail or "",
        (message.texte, reponse.answer),
        reponse.pending,
    )


def _sql_du_tour(trace) -> str:
    """Le SQL du nœud de récupération, tel que sa trace le porte.

    Le détail du nœud ``retrieval`` EST la requête (cf. ``_retrieval_node``) —
    sauf quand aucune n'a abouti, auquel cas il dit combien ont été tentées.
    On ne garde que le premier cas : « 3 requête(s), aucune n'a abouti » n'est
    pas du SQL, et le compter comme tel ferait passer un échec pour un chemin.
    """
    for etape in trace:
        if etape.node == "retrieval" and etape.detail and "aucune n'a abouti" not in etape.detail:
            return etape.detail
    return ""


def _magasin(racine: Path, conversation: str) -> list[Artefact]:
    """Le manifeste de la conversation après ce tour, tel qu'il est sur le disque.

    Relu sur le disque et non demandé à l'objet ``ConversationWorkspace`` : il
    est construit puis abandonné à chaque ``ask()``, et ce qui compte est ce
    qu'un tour ULTÉRIEUR retrouvera.
    """
    chemin = Path(racine) / conversation / ConversationWorkspace.MANIFEST
    if not chemin.exists():
        return []
    data = json.loads(chemin.read_text(encoding="utf-8"))
    return [
        Artefact(
            nom=a["name"],
            kind=a.get("kind", "table"),
            lignes=a.get("row_count", 0),
            colonnes=a.get("columns", []),
            tronque=a.get("tronque", False),
            question=a.get("question", ""),
        )
        for a in data.get("artifacts", [])
    ]


def _code_du_tour(racine: Path, conversation: str, magasin: list[Artefact], question: str) -> str:
    """Le code Python que CE tour a produit, lu dans le dossier de la conversation.

    C'est lui qui dit d'où le tour a pris ses données : un
    ``pd.read_csv('/data/resultat_1.csv')`` est une reprise du tableau,
    ``/data/passengers.csv`` est un retour à la base. Le plan, lui, désigne la
    SOURCE — et il désigne la base dans les deux cas.
    """
    noms = [a for a in magasin if a.kind in ("code", "figure") and a.question == question]
    if not noms:
        return ""
    chemin = Path(racine) / conversation / f"{noms[-1].nom}.py"
    return chemin.read_text(encoding="utf-8") if chemin.exists() else ""


# --- le rapport ---------------------------------------------------------------


def _plat(texte: str, limite: int = 0) -> str:
    plat = " ".join(texte.split())
    return plat[:limite] + " […]" if limite and len(plat) > limite else plat


def _bloc(texte: str, limite: int = 1400) -> str:
    """Un texte de réponse, replié en citation Markdown et borné."""
    plat = texte.strip() or "(vide)"
    if len(plat) > limite:
        plat = plat[:limite] + " […]"
    return "\n".join(f"> {ligne}" if ligne else ">" for ligne in plat.splitlines())


def _tours_mesures(releves: list[Releve]) -> list[Releve]:
    return [r for r in releves if not r.amorce]


def _chiffres_des_chemins(releves: list[Releve]) -> list[str]:
    """Combien de tours repartent d'un objet, combien relancent un SQL."""
    mesures = _tours_mesures(releves)
    repart = [r for r in mesures if r.repart_dun_objet]
    sql = [r for r in mesures if r.relance_un_sql]
    les_deux = [r for r in mesures if r.repart_dun_objet and r.relance_un_sql]
    rien = [r for r in mesures if not r.repart_dun_objet and not r.relance_un_sql]
    remat = [r for r in mesures if r.rematerialise_la_base]
    lignes = [
        "| chemin | tours | part |",
        "|---|---|---|",
        _ligne_de_part("repart d'un objet déjà produit", len(repart), len(mesures)),
        _ligne_de_part("relance un SQL sur une source", len(sql), len(mesures)),
        _ligne_de_part("les deux dans le même tour", len(les_deux), len(mesures)),
        _ligne_de_part("ni l'un ni l'autre", len(rien), len(mesures)),
        _ligne_de_part("rematérialise la base en CSV", len(remat), len(mesures)),
        f"| **total mesuré** | **{len(mesures)}** | (hors amorces) |",
    ]
    return lignes


def _ligne_de_part(libelle: str, combien: int, total: int) -> str:
    part = f"{100 * combien / total:.0f} %" if total else "—"
    return f"| {libelle} | {combien} | {part} |"


# Les nœuds du graphe, pour pouvoir dire lesquels n'ont RIEN demandé. La liste
# est celle de `Orchestrator._build_graph` ; un nœud ajouté au graphe et oublié
# ici se verrait dans le relevé sous son nom de fonction appelante.
NOEUDS_DU_GRAPHE = (
    "plan",
    "retrieval",
    "analysis",
    "inference",
    "fetch_predict",
    "system",
    "rappel",
    "synthesize",
)


def _chiffres_de_l_injection(releves: list[Releve]) -> list[str]:
    """Ce que l'inventaire coûte en contexte, et dans quels nœuds il entre.

    Trois relevés et non un, parce que ce ne sont pas trois fois la même
    chose : le TEXTE qui entre dans un prompt se compte en caractères ; la
    PESÉE du budget demande le même texte sans l'envoyer ; l'exposition des
    tableaux comme sources interrogeables n'est pas un texte du tout.
    """
    par_noeud: dict[tuple[str, str], list[int]] = {}
    for r in releves:
        for i in r.injections:
            par_noeud.setdefault((i.noeud, i.quoi), []).append(i.octets)
    lignes = [
        "Ce qui entre RÉELLEMENT dans un prompt, en caractères :",
        "",
        "| nœud | ce qui est injecté | tours concernés | médiane | max |",
        "|---|---|---|---|---|",
    ]
    for (noeud, quoi), tailles in sorted(par_noeud.items()):
        if quoi in TEXTES_INJECTES:
            lignes.append(
                f"| `{noeud}` | `{quoi}` | {len(tailles)} | "
                f"{int(statistics.median(tailles))} car. | {max(tailles)} car. |"
            )
    vus = {noeud for (noeud, quoi) in par_noeud if quoi in TEXTES_INJECTES}
    jamais = [n for n in NOEUDS_DU_GRAPHE if n not in vus]
    traverses = {n.nom for r in releves for n in r.noeuds}
    lignes += [
        "",
        "Nœuds du graphe où **aucun texte** de la mémoire de conversation n'est entré "
        "pendant toute la campagne : "
        + ", ".join(f"`{n}`" + ("" if n in traverses else " (jamais traversé)") for n in jamais)
        + ". Cela ne veut pas dire qu'ils l'ignorent : certains reçoivent les tableaux "
        "du fil comme des sources interrogeables, sans qu'aucune ligne de catalogue "
        "n'entre dans leur prompt (tableau plus bas).",
        "",
    ]
    lignes += _part_du_prompt(releves)
    pesees = [t for (n, q), ts in par_noeud.items() if q == PESEE for t in ts]
    if pesees:
        lignes += [
            f"La **pesée du budget** (`fit_to_budget`, nœud `plan`) a redemandé ce même "
            f"catalogue {len(pesees)} fois pour le mesurer sans l'envoyer — médiane "
            f"{int(statistics.median(pesees))} car., max {max(pesees)} car. Elle n'est "
            "pas comptée ci-dessus.",
            "",
        ]
    ephemeres: dict[str, int] = {}
    for r in releves:
        for i in r.injections:
            if i.quoi == SOURCES_EPHEMERES:
                ephemeres[i.noeud] = ephemeres.get(i.noeud, 0) + 1
    if ephemeres:
        lignes += [
            "Les tableaux du fil exposés comme **sources interrogeables** "
            "(`_effective_catalog`), par nœud demandeur :",
            "",
            "| nœud | tours où au moins une source éphémère est exposée |",
            "|---|---|",
            *(f"| `{noeud}` | {combien} |" for noeud, combien in sorted(ephemeres.items())),
        ]
    return lignes


def _part_du_prompt(releves: list[Releve]) -> list[str]:
    """Quelle PART du prompt du planificateur la mémoire de conversation occupe.

    Un chiffre en caractères ne dit pas s'il est gros : 1 500 caractères sont
    beaucoup dans un prompt de 4 000 et rien dans un prompt de 40 000. Le nœud
    ``plan`` est le seul qui mesure son propre prompt (``TraceStep``), et c'est
    aussi le seul dont le prompt porte le catalogue des objets : la part s'y
    calcule sans rien estimer.

    Le rapport se prend en TOKENS contre TOKENS. Les caractères du catalogue
    passent donc par la même estimation que le prompt qui les contient
    (``estimate_tokens``), sans quoi on diviserait des caractères par des
    tokens — un rapport trois fois trop grand, et faux du bon côté pour se
    faire croire.
    """
    parts: list[float] = []
    pire: tuple[float, str] = (0.0, "")
    for r in releves:
        plan = next((n for n in r.noeuds if n.nom == "plan"), None)
        injecte = r.injecte("describe")
        if plan is None or plan.prompt_tokens in (None, 0) or not injecte:
            continue
        part = 100 * estimate_tokens("x" * injecte) / plan.prompt_tokens
        parts.append(part)
        if part > pire[0]:
            pire = (part, f"{r.fil}.{r.numero} · tirage {r.tirage}")
    if not parts:
        return []
    return [
        f"Rapporté au prompt qui la porte : le catalogue des objets pèse "
        f"**{statistics.median(parts):.0f} % du prompt du planificateur** en médiane "
        f"sur les {len(parts)} tours où il est injecté, et jusqu'à "
        f"**{pire[0]:.0f} %** (tour {pire[1]}). Les deux termes sont pris en tokens "
        "estimés, comme le budget qui les arbitre.",
        "",
    ]


def _chiffres_du_drapeau(releves: list[Releve]) -> list[str]:
    """Le drapeau ``tronque`` : écrit dans le magasin ? lu ? dit à l'utilisateur ?

    Trois étapes distinctes, et c'est le point : un drapeau écrit qui n'est
    jamais lu ne sert à rien, et un drapeau lu qui n'arrive pas à
    l'utilisateur ne sert à rien non plus. On les compte séparément pour que
    la chaîne se voie là où elle se coupe.
    """
    # ÉCRIT : les tableaux distincts du magasin qui portent `tronque: true`,
    # comptés par conversation — un même tableau relu à dix tours n'est pas dix
    # drapeaux.
    ecrits: set[tuple[str, int, str]] = set()
    tableaux: set[tuple[str, int, str]] = set()
    for r in releves:
        for a in r.magasin:
            if a.kind != "table":
                continue
            tableaux.add((r.fil, r.tirage, a.nom))
            if a.tronque:
                ecrits.add((r.fil, r.tirage, a.nom))
    # LU : un prompt de ce tour porte la mention `TRONQUÉ`.
    lus = [
        r
        for r in releves
        if any("TRONQUÉ" in i.texte for i in r.injections if i.quoi == "montage_sandbox")
    ]
    # DIT : la réponse servie à l'utilisateur porte le mot, sous l'une ou
    # l'autre de ses formes. Replié en minuscules ET sans accent : « tronqué »
    # et « TRONQUE » sont le même fait.
    dits = [r for r in releves if _porte_le_mot(r.reponse, "tronqu")]
    # Et parmi eux, ceux qui n'ont produit AUCUN tableau ce tour-ci : c'est là
    # que le drapeau du magasin sert, puisque la phrase de la synthèse est loin.
    reportes = [r for r in dits if not r.sql]
    return [
        "| étape du drapeau | compte | où on le lit |",
        "|---|---|---|",
        f"| **écrit** — tableaux du magasin portant `tronque: true` | "
        f"{len(ecrits)} / {len(tableaux)} tableaux produits | `manifest.json` |",
        f"| **lu** — tours dont un prompt porte la mention `TRONQUÉ` | {len(lus)} | "
        "montage `/data/` du nœud `analysis` |",
        f"| **dit** — tours dont la réponse servie porte le mot | {len(dits)} | "
        "texte rendu à l'utilisateur |",
        f"| dont **dit sans requête neuve** — la tranche est celle d'un tour passé | "
        f"{len(reportes)} | texte rendu à l'utilisateur |",
    ]


def _replie(texte: str) -> str:
    """Minuscules et accents retirés : « écarter » doit contenir « ecart ».

    Un oracle qui compare sans replier les accents se ment. Il n'y a pas
    d'oracle ici, mais les mêmes comparaisons — « ce tour dit-il *tronqué* ? »
    — et elles se mentiraient pareil.
    """
    return unicodedata.normalize("NFKD", texte.lower()).encode("ascii", "ignore").decode()


def _porte_le_mot(texte: str, mot: str) -> bool:
    return _replie(mot) in _replie(texte)


def _mot_pour_mot(releves: list[Releve], cle: str, numero: int) -> list[str]:
    """Les réponses d'un tour donné, sur les trois tirages, sans rien couper."""
    lignes = []
    for r in releves:
        if r.fil == cle and r.numero == numero:
            lignes += [f"**Tirage {r.tirage}** — nœuds `{' → '.join(r.noms_des_noeuds)}` :", ""]
            lignes += [_bloc(r.reponse, limite=2000), ""]
    return lignes


def rapport(releves: list[Releve], reglages: Settings, limites: ContextLimits) -> str:
    lignes = [
        "# Le relevé de la mémoire de conversation",
        "",
        "Ce document est ÉCRIT PAR `scripts/mesure_memoire_de_conversation.py`. Il n'est",
        "pas rédigé à la main, et toute correction qu'on y apporterait serait perdue à la",
        "prochaine exécution. Il ne juge rien : il relève ce qui EST. C'est la matière",
        "de `docs/memoire-de-conversation.md`, qui, lui, se lit.",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        "",
        "Catalogues : `sources/catalogue.yaml` (fil A), "
        "`sources/demonstration/catalogue.yaml` (fils B à F)",
        "",
        f"Plafonds : `DAA_RETRIEVAL_MAX_ROWS={reglages.retrieval_max_rows}`, "
        f"`DAA_CONTEXT_ARTIFACT_WINDOW={limites.artifact_window}`, "
        f"`DAA_CONTEXT_CODE_WINDOW={limites.code_window}`, "
        f"`DAA_CONTEXT_TOKEN_BUDGET={limites.token_budget}`",
        "",
        "    uv run python scripts/mesure_memoire_de_conversation.py \\",
        "        --markdown docs/releve-de-la-memoire-de-conversation.md",
        "",
        "## Ce que la campagne établit, en chiffres",
        "",
        "### Les chemins : repartir d'un objet, ou refaire",
        "",
        *_chiffres_des_chemins(releves),
        "",
        "### Ce que l'inventaire coûte en contexte, et où il entre",
        "",
        *_chiffres_de_l_injection(releves),
        "",
        "### Le drapeau `tronque`",
        "",
        *_chiffres_du_drapeau(releves),
        "",
        "## Les six fils, tour par tour",
        "",
    ]
    for fil in FILS:
        propres = [r for r in releves if r.fil == fil.cle]
        if not propres:
            continue
        lignes += _section_du_fil(fil, propres)
    lignes += ["## Ce que l'agent SAIT dire de ses objets, mot pour mot", ""]
    for cle, numero, titre in (
        ("B", 3, "B.3 — « qu'est-ce que tu as en mémoire dans cette conversation ? »"),
        ("B", 4, "B.4 — « et le premier tableau, il contenait quoi ? »"),
        ("C", 2, "C.2 — « quelles données as-tu à ta disposition maintenant ? »"),
        ("D", 2, "D.2 — « combien de lignes dans ce tableau ? »"),
    ):
        bloc = _mot_pour_mot(releves, cle, numero)
        if bloc:
            lignes += [f"### {titre}", "", *bloc]
    return "\n".join(lignes)


def _section_du_fil(fil: Fil, releves: list[Releve]) -> list[str]:
    tirages = sorted({r.tirage for r in releves})
    lignes = [
        f"### Fil {fil.cle} — {fil.titre}",
        "",
        f"Catalogue : `{fil.catalogue.relative_to(RACINE)}`. "
        f"{len(tirages)} tirage(s), {len(fil.mesures)} tour(s) mesuré(s) par tirage.",
        "",
        f"Ce qu'on cherche : {fil.cherche}.",
        "",
        "| tirage | # | message | nœuds | plan | SQL | objets du magasin | mémoire injectée |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in releves:
        objets = ", ".join(
            f"{a.nom}({a.lignes}l{'·tronqué' if a.tronque else ''})" if a.kind == "table" else a.nom
            for a in r.magasin
        )
        lignes.append(
            f"| {r.tirage} | {r.numero}{' (amorce)' if r.amorce else ''} "
            f"| {_plat(r.message, 60)} | {' → '.join(r.noms_des_noeuds)} "
            f"| {r.plan or '—'} | {'oui' if r.sql else '—'} | {objets or '—'} "
            f"| {r.octets_injectes} car. |"
        )
    lignes.append("")
    for r in releves:
        lignes += _tour_en_detail(r)
    return lignes


def _tour_en_detail(r: Releve) -> list[str]:
    titre = f"#### {r.fil}.{r.numero} · tirage {r.tirage} — « {_plat(r.message)} »"
    lignes = [
        titre + (" *(amorce, hors comptage)*" if r.amorce else ""),
        "",
        f"Ce qu'on cherche : {r.cherche}.",
        "",
        f"**{r.appels_llm} appel(s) LLM**, {r.duree_ms} ms"
        + (f", plan : `{r.plan}`" if r.plan else "")
        + (f", source liée après le tour : `{r.source_liee}`" if r.source_liee else "")
        + (
            f", artefacts rendus : {', '.join(f'`{m}`' for m in r.artefacts_rendus)}"
            if r.artefacts_rendus
            else ""
        )
        + (f", **erreur : {r.erreur}**" if r.erreur else "")
        + ".",
        "",
        "| nœud | détail | ms |",
        "|---|---|---|",
    ]
    for n in r.noeuds:
        jetons = ""
        if n.prompt_tokens:
            jetons = f" *(prompt : {n.prompt_tokens} tokens estimés"
            jetons += f", {n.server_prompt_tokens} évalués)*" if n.server_prompt_tokens else ")*"
        lignes.append(f"| `{n.nom}` | {_plat(n.detail, 400) or '—'}{jetons} | {n.duree_ms} |")
    lignes += ["", "Appels d'outil émis par le modèle, dans l'ordre :", ""]
    if r.outils:
        lignes += ["| # | outil | arguments |", "|---|---|---|"]
        for rang, appel in enumerate(r.outils, start=1):
            lignes.append(f"| {rang} | `{appel.outil}` | `{_plat(appel.arguments, 200)}` |")
    else:
        lignes.append("Aucun.")
    lignes += ["", "Mémoire de conversation injectée dans ce tour :", ""]
    if r.injections:
        lignes += ["| nœud | fonction appelante | quoi | taille |", "|---|---|---|---|"]
        for i in r.injections:
            lignes.append(f"| `{i.noeud}` | `{i.appelant}` | `{i.quoi}` | {i.octets} car. |")
    else:
        lignes.append("Rien : aucun nœud de ce tour n'a demandé le catalogue des objets.")
    if r.montages:
        lignes += ["", "Monté sous `/data/` pour le code d'analyse :", ""]
        lignes += ["| fichier | origine |", "|---|---|"]
        for nom, origine in sorted(r.montages.items()):
            lignes.append(
                f"| `/data/{nom}` | "
                f"{'tableau de la conversation' if origine == 'memoire' else 'table de la base'} |"
            )
    lignes += [
        "",
        f"Chemin : repart d'un objet = **{'oui' if r.repart_dun_objet else 'non'}**, "
        f"SQL neuf = **{'oui' if r.relance_un_sql else 'non'}**, "
        f"base rematérialisée = **{'oui' if r.rematerialise_la_base else 'non'}**.",
        "",
    ]
    if r.sql:
        lignes += ["SQL émis :", "", "```sql", r.sql.strip(), "```", ""]
    if r.code_produit:
        # `text` et non `python` : ce bloc est un RELEVÉ de ce que le modèle a
        # émis, pas du code du projet. `ruff format` formate les blocs `python`
        # d'un Markdown — il réécrirait donc les guillemets et l'indentation du
        # modèle, et le relevé cesserait d'être ce qui a tourné.
        lignes += ["Code produit, tel quel :", "", "```text", r.code_produit.strip(), "```", ""]
    lignes += ["Réponse servie :", "", _bloc(r.reponse), ""]
    return lignes


# --- exécution ----------------------------------------------------------------


def orchestrateur_du_fil(
    fil: Fil, reglages: Settings, modele: ModeleMouchard, cache: dict[Path, Orchestrator]
) -> Orchestrator:
    """Un orchestrateur par CATALOGUE, gardé le temps de la campagne.

    Le piège que ce cache ferme : un runner à catalogue unique mesure cinq fils
    sur une base qui n'a pas leurs sources, ne lève aucune erreur, et rend zéro.
    Le catalogue est donc porté par le fil, et imprimé avant chaque fil.
    """
    if fil.catalogue not in cache:
        propres = reglages.model_copy(update={"catalog_path": fil.catalogue})
        cache[fil.catalogue] = Orchestrator(
            settings=propres,
            model=modele,
            catalog=load_catalog(fil.catalogue),
            registry=Registry.load(reglages.models_registry_path),
        )
    return cache[fil.catalogue]


def jouer(
    fil: Fil,
    tirage: int,
    orchestrateur: Orchestrator,
    modele: ModeleMouchard,
    racine: Path,
) -> list[Releve]:
    """Un fil, un tirage : une conversation neuve, jouée du premier au dernier tour."""
    conversation = f"memoire-{fil.cle.lower()}-{tirage}-{uuid.uuid4().hex[:8]}"
    releves: list[Releve] = []
    source_de_travail = ""
    echange_precedent: tuple[str, str] | None = None
    pending = None
    for message in fil.messages:
        print(f"  [{fil.cle}.{message.numero}/t{tirage}] « {message.texte} »", flush=True)
        releve, source_de_travail, echange_precedent, pending = poser(
            orchestrateur,
            modele,
            fil,
            message,
            tirage,
            conversation=conversation,
            racine=racine,
            source_de_travail=source_de_travail,
            echange_precedent=echange_precedent,
            pending=pending,
        )
        releves.append(releve)
        print(
            f"      {' → '.join(releve.noms_des_noeuds)} | plan={releve.plan or '—'} "
            f"| objet={'oui' if releve.repart_dun_objet else 'non'} "
            f"| sql={'oui' if releve.sql else 'non'} "
            f"| injecté={releve.octets_injectes} car. | {releve.duree_ms} ms"
        )
        print(f"      « {_plat(releve.reponse, 200)} »", flush=True)
    return releves


def journal(releves: list[Releve]) -> list[dict]:
    return [asdict(r) for r in releves]


def relire_le_journal(chemin: Path) -> list[Releve]:
    """Les relevés d'une campagne DÉJÀ jouée, relus depuis son journal JSON.

    Ce chemin ne mesure rien et n'appelle aucun modèle : il réécrit le rapport
    à partir de ce qui a été mesuré. Il existe pour une raison précise — une
    correction de forme dans le rapport ne doit pas obliger à rejouer 81 tours,
    parce qu'une campagne rejouée n'est plus la même campagne et que le
    document qui la cite deviendrait faux sur des points qu'on ne relit pas.

    Ce qu'il ne permet pas : fabriquer un relevé. Le journal est écrit par
    ``--json`` au terme d'une campagne, et rien d'autre ne l'écrit.
    """
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    releves = []
    for r in brut:
        champs = dict(r)
        champs["noeuds"] = [Noeud(**n) for n in r["noeuds"]]
        champs["outils"] = [AppelDOutil(**o) for o in r["outils"]]
        champs["injections"] = [Injection(**i) for i in r["injections"]]
        champs["magasin"] = [Artefact(**a) for a in r["magasin"]]
        releves.append(Releve(**champs))
    return releves


def main() -> None:
    parseur = argparse.ArgumentParser(description="La mémoire de conversation, mesurée.")
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--tirages", type=int, default=3)
    parseur.add_argument(
        "--fils",
        nargs="*",
        default=None,
        help="les lettres des fils à jouer (défaut : les six).",
    )
    parseur.add_argument(
        "--depuis-json",
        type=Path,
        default=None,
        help="réécrit le rapport depuis un journal déjà mesuré ; ne joue rien.",
    )
    args = parseur.parse_args()

    reglages = get_settings()
    if args.depuis_json is not None:
        releves = relire_le_journal(args.depuis_json)
        print(f"Relu : {args.depuis_json} ({len(releves)} tours, aucun rejoué)")
        texte = rapport(releves, reglages, ContextLimits.from_settings(reglages))
        if args.markdown:
            args.markdown.write_text(texte + "\n", encoding="utf-8")
            print(f"Écrit : {args.markdown}")
        else:
            print(texte)
        return
    limites = ContextLimits.from_settings(reglages)
    print(f"llm_base_url : {reglages.llm_base_url}")
    print(f"llm_model    : {reglages.llm_model}")
    print(
        f"Plafonds     : retrieval_max_rows={reglages.retrieval_max_rows}, "
        f"fenêtres={limites.artifact_window}/{limites.code_window}, "
        f"budget={limites.token_budget} tokens"
    )
    print(f"Workspaces   : {reglages.workspace_dir}\n")

    poser_les_enveloppes()
    modele = ModeleMouchard(build_model(reglages))
    cache: dict[Path, Orchestrator] = {}
    racine = Path(reglages.workspace_dir) / f"memoire-{uuid.uuid4().hex[:8]}"
    racine.mkdir(parents=True, exist_ok=True)

    choisis = [f for f in FILS if not args.fils or f.cle.upper() in {c.upper() for c in args.fils}]
    releves: list[Releve] = []
    depart = time.monotonic()
    for fil in choisis:
        orchestrateur = orchestrateur_du_fil(fil, reglages, modele, cache)
        print(f"\n=== Fil {fil.cle} — {fil.titre}")
        print(f"Catalogue : {fil.catalogue}", flush=True)
        for tirage in range(1, args.tirages + 1):
            releves += jouer(fil, tirage, orchestrateur, modele, racine)
    print(f"\n{len(releves)} tours joués en {(time.monotonic() - depart) / 60:.0f} min.")
    print(Counter(r.fil for r in releves))

    texte = rapport(releves, reglages, limites)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"Écrit : {args.markdown}")
    else:
        print(texte)
    if args.json:
        args.json.write_text(
            json.dumps(journal(releves), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Journal écrit dans {args.json}")


if __name__ == "__main__":
    main()
