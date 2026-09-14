"""Mesure ce que l'agent sait répondre aux questions SUR LUI-MÊME (questions « méta »).

Dix conversations de durcissement, et aucune mesure de la surface
conversationnelle : on savait que « De quels attributs as-tu besoin ? » tombait
dans le repli, on ne savait pas *combien* de questions de cette famille
tombaient avec elle. Ce runner répond à cette question-là, et il est fait pour
être **rejoué** — avant/après une correction, ou après un changement de modèle.

Ce qui est mesuré : une question naturelle d'un utilisateur qui découvre
l'agent (quelles sources, quelles tables, quelles colonnes, le sens d'une
colonne, quels modèles, quelles features, que sais-tu faire, quelle période,
combien de lignes), ses reformulations et ses tournures indirectes.

**Et une famille de témoins**, qui ne sont pas des questions méta : de vraies
questions sur les DONNÉES, dont on vérifie qu'elles restent routées là où
elles doivent l'être. Un routeur de questions méta se juge autant sur ce qu'il
laisse passer que sur ce qu'il attrape — « quelles colonnes de passengers
contiennent des valeurs manquantes ? » ressemble à une question de schéma et
n'en est pas une.

Contre le VRAI système, comme ``live_scenarios.py`` et à la différence de la
suite pytest : le serveur LLM en place et les sources réelles du catalogue. Un
verdict n'a de sens que si le planificateur a réellement classé la demande.

Chaque question est posée dans une conversation NEUVE (aucun
``conversation_id``) : une question méta ne doit pas devoir son succès au
contexte laissé par la précédente.

Le verdict est **mécanique**, et son oracle est tiré des sources de vérité —
les noms du catalogue, les datasets du registre, les champs des schémas de
features, les colonnes, les types et les comptes lus dans la source. Rien
n'est recopié à la main : un oracle écrit en dur mesurerait la mémoire de son
auteur, pas la couverture du système.

    uv run python scripts/mesure_surface_conversationnelle.py
    uv run python scripts/mesure_surface_conversationnelle.py --only sources-directe
    uv run python scripts/mesure_surface_conversationnelle.py --markdown /tmp/tableau.md

Prérequis : le serveur LLM répond (``DAA_LLM_BASE_URL``) et les sources du
catalogue sont joignables — Postgres seedé
(``scripts/seed_titanic_postgres.py``) pour la source ``titanic``. Une source
injoignable fait échouer la lecture de la vérité terrain, et c'est voulu :
elle changerait les verdicts sans le dire.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS
from data_analyst_agent.agents.retrieval.catalog import Catalog, load_catalog, open_source
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import Orchestrator

# Le repli du planificateur, reconnu à cette phrase. Elle est le SYMPTÔME que
# ce runner existe pour compter : « je n'ai pas bien compris » alors que la
# réponse est dans le catalogue.
MARQUEUR_DE_REPLI = "je n'ai pas bien compris"

# Les types SQL qui portent une date. Sert à savoir si « sur quelle période
# portent les données ? » a une réponse chiffrée dans la source, ou si la
# bonne réponse est « il n'y a aucune colonne de date ».
TYPES_TEMPORELS = ("DATE", "TIME", "TIMESTAMP", "DATETIME")

# Les tournures qui disent honnêtement l'absence de colonne temporelle. Ce
# n'est pas de l'indulgence : quand la source n'a pas de date, c'est LA bonne
# réponse, et exiger un nom de colonne compterait faux une réponse juste.
AVEUX_D_ABSENCE_DE_DATE = (
    "aucune colonne de date",
    "pas de colonne de date",
    "aucune colonne temporelle",
    "aucune date",
    "pas de date",
    "aucune information de date",
    "aucune information temporelle",
    "aucune donnee temporelle",
    "ne contient pas de date",
    "ne comporte aucune date",
)

VERDICTS = ("correct", "a_cote", "repli", "erreur")
LIBELLES = {
    "correct": "répondu correctement",
    "a_cote": "répondu à côté",
    "repli": "repli « je n'ai pas bien compris »",
    "erreur": "erreur",
}
FAMILLE_TEMOIN = "témoin (données)"


def replie(texte: str) -> str:
    """Minuscules, sans accents, sans décoration Markdown.

    L'oracle compare du sens, pas de la typographie. Les accents parce que le
    modèle alterne « prédiction » et « prediction » d'un tour à l'autre. La
    décoration Markdown parce qu'elle a déjà fait compter FAUX une réponse
    juste : le modèle écrit ``passenger\\_id`` — l'antislash échappe le blanc
    souligné pour l'affichage — et la comparaison littérale ne reconnaissait
    plus le nom de la colonne qu'il venait pourtant de citer correctement.
    """
    sans_accent = unicodedata.normalize("NFKD", texte.lower())
    nu = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return nu.replace("\\", "").replace("*", "").replace("`", "")


def _nomme(plat: str, nom: str) -> bool:
    """Le texte cite-t-il ce nom de colonne, entier ?

    Une sous-chaîne ne suffit pas, et le contre-exemple est dans la source :
    la colonne ``sex`` vit à l'intérieur du mot « sexe », que toute réponse
    française écrit. Un interdit cherché en sous-chaîne déclarerait fausse une
    réponse juste qui parle du sexe des passagers.
    """
    return re.search(rf"(?<![a-z0-9_]){re.escape(replie(nom))}(?![a-z0-9_])", plat) is not None


@dataclass(frozen=True)
class QuestionMeta:
    """Une question et l'oracle qui décide de son verdict.

    ``attendus_tous`` : toutes ces chaînes doivent apparaître dans la réponse
    (le cas « énumère-moi X » : une liste incomplète n'est pas une réponse).
    ``attendus_parmi`` : au moins une (le cas où plusieurs réponses sont
    justes).

    ``clarification_admise`` : les choix qu'une question renvoyée doit
    énumérer pour compter comme une réponse. Vide = une clarification ne
    compte jamais, et c'est le cas général : à « quelles sources
    possèdes-tu ? », répondre « sur quelle source veux-tu travailler :
    titanic, iris ? » énumère bel et bien la réponse, mais sous forme de
    question — l'utilisateur, lui, n'a pas été répondu. Renseignée seulement
    quand la question laisse RÉELLEMENT le choix ouvert (« de quels attributs
    as-tu besoin ? », trois modèles au registre).

    ``capacite_attendue`` : la capacité par laquelle la demande doit passer.
    Renseignée sur les témoins : c'est ce qui prouve qu'un routeur de
    questions méta ne s'est pas emparé d'une question sur les données.

    ``interdits`` : les chaînes qu'une réponse juste ne PEUT pas contenir.
    Ajouté parce que l'oracle a compté juste une réponse fausse : à « quelles
    colonnes contiennent des valeurs manquantes ? », le modèle répondait
    « `name`, `age`, `fare`, `embarked` » — ``age`` et ``embarked`` y sont,
    donc ``attendus_tous`` était satisfait, et le verdict tombait vert sur une
    réponse qui nomme deux colonnes sans le moindre trou. Une question dont
    l'oracle est une LISTE EXHAUSTIVE se juge sur les deux bords : ce qu'elle
    doit nommer, et ce qu'elle ne doit pas. Les interdits sont dérivés de la
    source comme le reste — jamais écrits à la main.
    """

    cle: str
    famille: str
    question: str
    attendus_tous: tuple[str, ...] = ()
    attendus_parmi: tuple[str, ...] = ()
    interdits: tuple[str, ...] = ()
    clarification_admise: tuple[str, ...] = ()
    capacite_attendue: str | None = None

    def satisfait(self, reponse: str) -> tuple[bool, list[str]]:
        """Le verdict de l'oracle, et ce qui manquait le cas échéant."""
        plat = replie(reponse)
        manquants = [a for a in self.attendus_tous if replie(a) not in plat]
        if self.attendus_parmi and not any(replie(a) in plat for a in self.attendus_parmi):
            manquants.append("aucun de : " + ", ".join(self.attendus_parmi))
        manquants += [f"nomme {i} à tort" for i in self.interdits if _nomme(plat, i)]
        return not manquants, manquants


@dataclass
class Resultat:
    question: QuestionMeta
    reponse: str
    capacite: str | None
    mode_de_synthese: str
    erreur: str | None
    appels_llm: int
    duree_ms: int
    verdict: str
    manquants: list[str] = field(default_factory=list)
    # La trace complète, dans le journal. Une erreur d'un nœud ne rend à
    # l'utilisateur qu'une phrase et une référence d'incident : sans la trace,
    # un échec vu une fois et non reproductible ne laisse rien à diagnostiquer,
    # et c'est arrivé.
    trace: list[str] = field(default_factory=list)


class ModeleCompteur(WrapperModel):
    """Le modèle réel, plus un compteur d'allers-retours.

    C'est la seule mesure qui prouve le coût d'une question méta. Un chrono ne
    suffit pas : il ne distingue pas un appel LLM rapide d'une réponse
    construite sans appel du tout, et c'est exactement la distinction qu'on
    cherche à établir.
    """

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.appels = 0

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.appels += 1
        return await super().request(messages, model_settings, model_request_parameters)


# --- l'oracle, lu dans les sources de vérité ---------------------------------


@dataclass(frozen=True)
class VeriteTerrain:
    """Ce que le système sait de lui-même, lu là où il le sait.

    Le catalogue et le registre pour les noms, l'ontologie de chaque source
    pour les tables, les colonnes et leurs types, et du SQL pour les comptes.
    """

    sources: tuple[str, ...]
    datasets: tuple[str, ...]
    tables: dict[str, tuple[str, ...]]  # source -> tables
    colonnes: dict[str, tuple[str, ...]]  # "source.table" -> colonnes
    temporelles: dict[str, tuple[str, ...]]  # "source.table" -> colonnes de date
    lignes: dict[str, int]  # "source.table" -> nombre de lignes
    features: dict[str, tuple[str, ...]]  # dataset -> champs du schéma
    ages_max: float  # MAX(age) de passengers, pour un témoin d'agrégat
    survivants: int  # COUNT(survived = 1), pour un témoin de comptage
    colonnes_a_trous: tuple[str, ...]  # colonnes de passengers avec des NULL

    @classmethod
    def lire(cls, catalogue: Catalog, registre: Registry) -> VeriteTerrain:
        tables: dict[str, tuple[str, ...]] = {}
        colonnes: dict[str, tuple[str, ...]] = {}
        temporelles: dict[str, tuple[str, ...]] = {}
        lignes: dict[str, int] = {}
        ages_max = 0.0
        survivants = 0
        a_trous: tuple[str, ...] = ()
        for source in catalogue.sources:
            with closing(open_source(source)) as adaptateur:
                schema = adaptateur.schema()
                tables[source.name] = tuple(t.name for t in schema.tables)
                for table in schema.tables:
                    cle = f"{source.name}.{table.name}"
                    colonnes[cle] = tuple(c.name for c in table.columns)
                    temporelles[cle] = tuple(
                        c.name
                        for c in table.columns
                        if any(t in c.type.upper() for t in TYPES_TEMPORELS)
                    )
                    lignes[cle] = int(
                        adaptateur.run(f"SELECT COUNT(*) FROM {table.name}").rows[0][0]
                    )
                    if table.name == "passengers":
                        ages_max = float(
                            adaptateur.run("SELECT MAX(age) FROM passengers").rows[0][0]
                        )
                        survivants = int(
                            adaptateur.run(
                                "SELECT COUNT(*) FROM passengers WHERE survived = 1"
                            ).rows[0][0]
                        )
                        a_trous = tuple(
                            c.name
                            for c in table.columns
                            if adaptateur.run(
                                f"SELECT COUNT(*) FROM passengers WHERE {c.name} IS NULL"
                            ).rows[0][0]
                        )
        return cls(
            sources=tuple(s.name for s in catalogue.sources),
            datasets=tuple(registre.datasets),
            tables=tables,
            colonnes=colonnes,
            temporelles=temporelles,
            lignes=lignes,
            features={d: tuple(SCHEMAS[d].model_fields) for d in registre.datasets if d in SCHEMAS},
            ages_max=ages_max,
            survivants=survivants,
            colonnes_a_trous=a_trous,
        )

    def colonnes_pleines(self, cle_table: str) -> tuple[str, ...]:
        """Les colonnes de cette table qui n'ont AUCUN trou — le complément.

        ``passenger_id`` en est exclu : il apparaît dans les noms des autres
        colonnes (« passenger_id » contient… non, mais la réponse cite souvent
        la table « passengers »), et un interdit qui se déclenche sur un mot
        de la phrase mesurerait la formulation, pas le fond.
        """
        return tuple(
            c
            for c in self.colonnes[cle_table]
            if c not in self.colonnes_a_trous and c not in ("passenger_id",)
        )

    def oracle_de_periode(self, cle_table: str) -> tuple[str, ...]:
        """Ce qu'une réponse FONDÉE sur la période peut contenir.

        Une colonne de date s'il y en a une ; sinon l'aveu que la source n'en
        a pas — plus les noms de colonnes, qui prouvent aussi qu'on a regardé
        le schéma au lieu d'inventer des bornes plausibles.
        """
        if self.temporelles[cle_table]:
            return self.temporelles[cle_table]
        return AVEUX_D_ABSENCE_DE_DATE + self.colonnes[cle_table]


def batterie(vt: VeriteTerrain) -> list[QuestionMeta]:
    """Les questions, famille par famille, oracles branchés sur la vérité terrain.

    Les tournures ne sont pas des variantes décoratives : c'est là que la
    mesure a de l'intérêt. Une question directe (« quelles sources ? ») et une
    tournure indirecte (« sur quoi peux-tu travailler ? ») demandent la même
    chose, et rien ne garantit qu'elles finissent au même endroit.

    Certaines questions nomment une source ou un dataset (``titanic``,
    ``iris``) parce que c'est ainsi qu'on les pose une fois qu'on a vu la
    liste ; les autres restent volontairement non qualifiées, comme au premier
    tour d'une conversation.
    """
    src = vt.sources[0]
    passengers = "titanic.passengers"
    return [
        # --- quelles sources ? (la question du propriétaire, et ses variantes)
        QuestionMeta(
            "sources-directe",
            "sources",
            "Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-reformulee",
            "sources",
            "Sur quoi peux-tu travailler ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-indirecte",
            "sources",
            "À quelles bases de données as-tu accès ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-imperative",
            "sources",
            "Liste-moi tes sources de données.",
            attendus_tous=vt.sources,
        ),
        # --- quelles tables ?
        QuestionMeta(
            "tables-directe",
            "tables",
            f"Quelles tables contient la source {src} ?",
            attendus_tous=vt.tables[src],
        ),
        QuestionMeta(
            "tables-indirecte",
            "tables",
            f"Comment est structurée la base {src} ?",
            attendus_tous=vt.tables[src],
        ),
        # --- quelles colonnes ?
        QuestionMeta(
            "colonnes-table",
            "colonnes",
            "Quelles colonnes y a-t-il dans la table passengers ?",
            attendus_tous=vt.colonnes[passengers],
        ),
        QuestionMeta(
            "colonnes-fichier",
            "colonnes",
            "Quels champs trouve-t-on dans la source iris ?",
            attendus_tous=vt.colonnes["iris.iris"],
        ),
        # --- que signifie une colonne ?
        QuestionMeta(
            "sens-colonne",
            "sens d'une colonne",
            "Que signifie la colonne class_id de la table passengers ?",
            # la réponse fondée renvoie à la table pointée par la clé étrangère
            attendus_tous=("classes",),
        ),
        # --- quels modèles ?
        QuestionMeta(
            "modeles-directe",
            "modèles",
            "Quels modèles de prédiction sais-tu utiliser ?",
            attendus_tous=vt.datasets,
        ),
        QuestionMeta(
            "modeles-indirecte",
            "modèles",
            "Est-ce que tu sais faire des prédictions, et sur quoi ?",
            attendus_tous=vt.datasets,
        ),
        # --- quelles features ?
        QuestionMeta(
            "features-dataset",
            "features",
            "De quels attributs as-tu besoin pour prédire la survie d'un passager du Titanic ?",
            attendus_tous=vt.features["titanic"],
        ),
        QuestionMeta(
            "features-iris",
            "features",
            "Quelles mesures faut-il te donner pour que tu prédises l'espèce d'un iris ?",
            attendus_tous=vt.features["iris"],
        ),
        QuestionMeta(
            # la question exacte de docs/axes-amelioration.md
            "features-nue",
            "features",
            "De quels attributs as-tu besoin ?",
            attendus_parmi=vt.datasets,
            clarification_admise=vt.datasets,
        ),
        QuestionMeta(
            "features-indirecte",
            "features",
            "De quoi as-tu besoin pour prédire ?",
            attendus_parmi=vt.datasets,
            clarification_admise=vt.datasets,
        ),
        # --- que sais-tu faire ?
        QuestionMeta(
            "capacites-directe",
            "capacités",
            "Que sais-tu faire ?",
            attendus_tous=("analys", "predi"),
        ),
        QuestionMeta(
            "capacites-indirecte",
            "capacités",
            "À quoi sers-tu, exactement ?",
            attendus_tous=("analys", "predi"),
        ),
        # --- quelle période ?
        QuestionMeta(
            "periode-directe",
            "période",
            f"Sur quelle période portent les données de la source {src} ?",
            attendus_parmi=vt.oracle_de_periode(passengers),
        ),
        QuestionMeta(
            "periode-indirecte",
            "période",
            "De quand datent les données que tu as ?",
            attendus_parmi=vt.oracle_de_periode(passengers),
            clarification_admise=vt.sources,
        ),
        # --- combien de lignes ?
        QuestionMeta(
            "volumetrie-table",
            "volumétrie",
            "Combien de lignes contient la table passengers ?",
            attendus_tous=(str(vt.lignes[passengers]),),
        ),
        QuestionMeta(
            "volumetrie-globale",
            "volumétrie",
            "Quelle est la taille de tes données ?",
            attendus_parmi=tuple(str(n) for n in vt.lignes.values()),
            clarification_admise=vt.sources,
        ),
        # --- LES REFORMULATIONS DU PROPRIÉTAIRE, mesurées en usage réel
        #
        # Dix façons de poser LA MÊME question (« quelles données as-tu ? »),
        # relevées par le propriétaire en se servant de l'application. Trois
        # étaient court-circuitées par le lexique, sept partaient au
        # planificateur — classées `query`, donc du SQL écrit pour répondre à
        # une question de configuration, ou le repli. Les voici nommées : c'est
        # la partie de la batterie qui juge le ROUTAGE, et non la réponse.
        #
        # Elles sont écrites TELLES QUELLES, fautes et accents manquants
        # compris (« tu as acces à quelles données »). Les corriger mesurerait
        # un utilisateur qui n'existe pas.
        QuestionMeta(
            "sources-acces-familier",
            "sources",
            "tu as acces à quelles données",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-premiere-personne",
            "sources",
            "sur quoi je peux travailler ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-perimetre",
            "sources",
            "c'est quoi ton périmètre ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-disposes",
            "sources",
            "de quoi disposes-tu ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-montre-moi",
            "sources",
            "montre-moi ce que tu as",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-tu-bosses",
            "sources",
            "tu bosses sur quoi ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-liste-bases",
            "sources",
            "liste tes bases",
            attendus_tous=vt.sources,
        ),
        # --- huit reformulations de plus, sur les autres familles
        #
        # Le constat portait sur « quelles sources ? », mais rien ne dit que
        # les autres familles tiennent mieux : leur lexique est écrit de la
        # même main, et rien ne le mesurait hors des tournures qu'il connaît.
        QuestionMeta(
            "sources-comme-donnees",
            "sources",
            "qu'est-ce que tu as comme données ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "sources-consulter",
            "sources",
            "quelles infos tu peux consulter ?",
            attendus_tous=vt.sources,
        ),
        QuestionMeta(
            "capacites-demander-quoi",
            "capacités",
            "je peux te demander quoi ?",
            attendus_tous=("analys", "predi"),
        ),
        QuestionMeta(
            "capacites-capable",
            "capacités",
            "dis-moi ce dont tu es capable",
            attendus_tous=("analys", "predi"),
        ),
        QuestionMeta(
            "colonnes-familier",
            "colonnes",
            "il y a quoi comme colonnes dans passengers ?",
            attendus_tous=vt.colonnes[passengers],
        ),
        QuestionMeta(
            "colonnes-champs-classes",
            "colonnes",
            "c'est quoi les champs de la table classes ?",
            attendus_tous=vt.colonnes["titanic.classes"],
        ),
        QuestionMeta(
            "modeles-previsions",
            "modèles",
            "quel genre de prévisions tu peux faire ?",
            attendus_tous=vt.datasets,
        ),
        QuestionMeta(
            "features-familier",
            "features",
            "il te faut quoi pour deviner l'espèce d'un iris ?",
            attendus_tous=vt.features["iris"],
        ),
        # --- TÉMOINS : de vraies questions sur les données, qui doivent le rester
        QuestionMeta(
            "temoin-comptage",
            FAMILLE_TEMOIN,
            "Combien de passagers ont survécu ?",
            attendus_tous=(str(vt.survivants),),
            capacite_attendue="query",
        ),
        QuestionMeta(
            "temoin-maximum",
            FAMILLE_TEMOIN,
            "Quel est l'âge du passager le plus âgé ?",
            attendus_tous=(f"{vt.ages_max:g}",),
            capacite_attendue="query",
        ),
        QuestionMeta(
            # le piège du routeur : la tournure d'une question de schéma
            # (« quelles colonnes ») pour une question de DONNÉES
            "temoin-colonnes-a-trous",
            FAMILLE_TEMOIN,
            "Quelles colonnes de la table passengers contiennent des valeurs manquantes ?",
            attendus_tous=vt.colonnes_a_trous,
            # l'autre bord de l'oracle : les colonnes PLEINES, qu'une réponse
            # juste ne nomme pas. Dérivées, comme les colonnes à trous.
            interdits=vt.colonnes_pleines("titanic.passengers"),
            capacite_attendue="query",
        ),
        QuestionMeta(
            "temoin-prediction",
            FAMILLE_TEMOIN,
            "Prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80 livres, "
            "embarquée à Southampton, sans frère, sœur, parent ni enfant à bord.",
            attendus_parmi=("a survecu", "n'a pas survecu"),
            capacite_attendue="predict",
        ),
    ]


# --- exécution ---------------------------------------------------------------


def classer(
    question: QuestionMeta, reponse: str, erreur: str | None, mode: str, capacite: str | None
) -> tuple[str, list[str]]:
    """Le verdict : erreur, repli, mauvaise capacité, clarification, oracle.

    L'ordre importe, et chaque marche a sa raison.

    Un repli est d'abord un repli, même s'il cite par hasard une chaîne
    attendue : c'est justement l'absurdité constatée — le message qui dit ne
    pas comprendre nomme la source qu'on lui demandait.

    Une capacité inattendue vient ensuite : sur un témoin, une réponse juste
    obtenue par le mauvais chemin n'est pas une bonne nouvelle.

    Une clarification non admise est enfin « à côté », même quand elle
    contient tous les mots attendus. Sans cette marche, l'oracle littéral
    comptait « Sur quelle source veux-tu travailler : titanic, iris ? » comme
    une réponse correcte à « quelles sources possèdes-tu ? » — la mesure
    aurait alors déclaré couverte la classe même qu'on cherchait à mesurer.
    """
    if erreur:
        return "erreur", []
    if MARQUEUR_DE_REPLI in replie(reponse):
        return "repli", []
    if question.capacite_attendue and capacite != question.capacite_attendue:
        return "a_cote", [f"routé en {capacite} au lieu de {question.capacite_attendue}"]
    if mode == "clarification":
        plat = replie(reponse)
        admise = question.clarification_admise and all(
            replie(choix) in plat for choix in question.clarification_admise
        )
        if not admise:
            return "a_cote", ["clarification renvoyée au lieu d'une réponse"]
        return "correct", []
    ok, manquants = question.satisfait(reponse)
    return ("correct" if ok else "a_cote"), manquants


def poser(orchestrateur: Orchestrator, compteur: ModeleCompteur, q: QuestionMeta) -> Resultat:
    depart = time.monotonic()
    avant = compteur.appels
    reponse = orchestrateur.ask(q.question)
    duree = int((time.monotonic() - depart) * 1000)
    synthese = next((s.detail for s in reponse.trace if s.node == "synthesize"), "")
    capacite = reponse.plan.capability if reponse.plan else None
    verdict, manquants = classer(q, reponse.answer, reponse.error, synthese, capacite)
    return Resultat(
        question=q,
        reponse=reponse.answer,
        capacite=capacite,
        mode_de_synthese=synthese,
        erreur=reponse.error,
        appels_llm=compteur.appels - avant,
        duree_ms=duree,
        verdict=verdict,
        manquants=manquants,
        trace=[f"{s.node} : {s.detail}" for s in reponse.trace],
    )


def une_ligne(texte: str, largeur: int = 320) -> str:
    """La réponse ramenée sur une ligne, pour une cellule de tableau Markdown."""
    plat = " ".join(texte.split()).replace("|", "\\|")
    return plat if len(plat) <= largeur else plat[: largeur - 1] + "…"


def tableau_markdown(resultats: list[Resultat]) -> str:
    lignes = [
        "| Clé | Famille | Question | Réponse obtenue | Capacité | Synthèse | Appels LLM "
        "| Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in resultats:
        lignes.append(
            f"| `{r.question.cle}` | {r.question.famille} | {une_ligne(r.question.question)} "
            f"| {une_ligne(r.reponse)} | {r.capacite or '—'} | {r.mode_de_synthese or '—'} "
            f"| {r.appels_llm} | **{LIBELLES[r.verdict]}** |"
        )
    return "\n".join(lignes)


def comptes(resultats: list[Resultat]) -> dict[str, int]:
    return {v: sum(1 for r in resultats if r.verdict == v) for v in VERDICTS}


def _bloc(titre: str, resultats: list[Resultat]) -> list[str]:
    """Un tableau, ses comptes et son coût LLM."""
    total = len(resultats)
    lignes = [f"### {titre} — {total} question(s)", "", tableau_markdown(resultats), ""]
    compte = comptes(resultats)
    lignes += ["| Verdict | Nombre | Part |", "|---|---|---|"]
    for verdict in VERDICTS:
        n = compte[verdict]
        lignes.append(f"| {LIBELLES[verdict]} | {n} | {n / total:.0%} |")
    lignes.append(f"| **Total** | **{total}** | |")
    appels = sum(r.appels_llm for r in resultats)
    lignes += [
        "",
        f"Coût mesuré : **{appels} appels LLM** pour {total} question(s) "
        f"(moyenne {appels / total:.2f} par question).",
        "",
    ]
    return lignes


def rapport(resultats: list[Resultat]) -> str:
    """Deux blocs : les questions méta, puis les témoins — jamais mélangés.

    Additionner les deux comptes dirait n'importe quoi : les témoins sont là
    pour ne PAS bouger, et les compter avec le reste diluerait justement ce
    qu'on mesure.
    """
    meta = [r for r in resultats if r.question.famille != FAMILLE_TEMOIN]
    temoins = [r for r in resultats if r.question.famille == FAMILLE_TEMOIN]
    lignes: list[str] = []
    if meta:
        lignes += _bloc("Questions méta", meta)
    if temoins:
        lignes += _bloc("Témoins — questions sur les données", temoins)
    return "\n".join(lignes).rstrip()


def journal(resultats: list[Resultat]) -> list[dict]:
    return [
        {
            "cle": r.question.cle,
            "famille": r.question.famille,
            "question": r.question.question,
            "reponse": r.reponse,
            "capacite": r.capacite,
            "mode_de_synthese": r.mode_de_synthese,
            "erreur": r.erreur,
            "appels_llm": r.appels_llm,
            "duree_ms": r.duree_ms,
            "verdict": r.verdict,
            "manquants": r.manquants,
            "trace": r.trace,
        }
        for r in resultats
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", metavar="CLE", help="ne poser que ces questions")
    parser.add_argument("--markdown", type=Path, help="écrit les tableaux et les comptes ici")
    parser.add_argument("--json", type=Path, help="écrit le journal complet des réponses ici")
    args = parser.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    registre = Registry.load(reglages.models_registry_path)
    print(f"Sources : {', '.join(s.name for s in catalogue.sources)}")
    print(f"Modèles : {', '.join(registre.datasets)}")
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})\n")

    questions = batterie(VeriteTerrain.lire(catalogue, registre))
    if args.only:
        demandees = set(args.only)
        questions = [q for q in questions if q.cle in demandees]
        if not questions:
            sys.exit(f"aucune question ne porte ces clés : {', '.join(sorted(demandees))}")

    compteur = ModeleCompteur(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages, model=compteur, catalog=catalogue, registry=registre
    )
    resultats: list[Resultat] = []
    for numero, question in enumerate(questions, start=1):
        print(f"[{numero}/{len(questions)}] {question.cle} — {question.question}", flush=True)
        resultat = poser(orchestrateur, compteur, question)
        resultats.append(resultat)
        print(
            f"    → {LIBELLES[resultat.verdict]} "
            f"({resultat.capacite or 'sans plan'}, {resultat.appels_llm} appel(s) LLM, "
            f"{resultat.duree_ms} ms)"
        )
        print(f"    « {une_ligne(resultat.reponse, 200)} »")
        if resultat.manquants:
            print(f"    manque : {', '.join(resultat.manquants)}")
        print(flush=True)

    texte = rapport(resultats)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
        print(f"\nTableau écrit dans {args.markdown}")
    if args.json:
        args.json.write_text(
            json.dumps(journal(resultats), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Journal écrit dans {args.json}")


if __name__ == "__main__":
    main()
