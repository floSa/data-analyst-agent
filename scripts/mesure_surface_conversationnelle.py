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
from collections.abc import Callable
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
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.graph import Orchestrator

# Le repli du planificateur, reconnu à cette phrase. Elle est le SYMPTÔME que
# ce runner existe pour compter : « je n'ai pas bien compris » alors que la
# réponse est dans le catalogue.
MARQUEUR_DE_REPLI = "je n'ai pas bien compris"

# Les types SQL qui portent une date. Sert à savoir si « sur quelle période
# portent les données ? » a une réponse chiffrée dans la source, ou si la
# bonne réponse est « il n'y a aucune colonne de date ».
TYPES_TEMPORELS = ("DATE", "TIME", "TIMESTAMP", "DATETIME")

# --- ce qu'une réponse sur la PÉRIODE doit porter ----------------------------
#
# Deux formes, et aucune liste de tournures. Une liste de tournures a déjà été
# écrite ici, et c'est elle qu'on retire : elle disait l'absence de date en dix
# phrases entières, donc en dix sous-chaînes, donc elle refusait la onzième
# façon de le dire — le défaut réparé en C51, dans l'autre sens. Ce qui est
# exigé est un FAIT ; ce qu'on reconnaît est sa forme.

# Un MILLÉSIME, et c'est toute la définition d'une date ici. « 1912 »,
# « 1912-04-10 », « 10/04/1912 » et « avril 1912 » le portent tous, et une
# période qui n'en porte aucun ne dit pas QUAND : c'est une borne sans année.
# La garde de chaque côté tient les décimales à l'écart — ni « 0.42 » ni
# « 80.0 » ne portent d'année, et c'est exactement la réponse qu'un oracle a
# bénie (cf. ``VeriteTerrain.exigence_de_periode``).
MILLESIME = re.compile(r"(?<!\d)(?:1\d{3}|2[01]\d{2})(?!\d)")

# Ce qui, dans une phrase, NIE l'existence — en mots entiers. Ce sont des mots
# outils, courts et invariables, et les comparer en préfixe ferait reconnaître
# « pas » dans « passagers » : le contre-exemple est dans la source.
MOTS_D_ABSENCE = ("pas", "ni", "sans", "rien", "non")

# Les deux qui se conjuguent, donc pris par leur radical : « aucun », « aucune »,
# « aucunes », « dépourvu », « dépourvue ».
RADICAUX_D_ABSENCE = ("aucun", "depourvu")

# Ce qui désigne le TEMPS, par le radical et non par le mot : `date` dit
# « dates », « datée » et « datent » sans qu'on les énumère.
#
# `date` et non `dat` : le radical plus court reconnaît « dataset », qui est un
# mot de ce dépôt, et « je n'ai pas de dataset » compterait alors pour un constat
# d'absence de date.
#
# Déclaré ICI et non importé d'`introspection`, qui en tient un du même genre
# pour savoir quand un message demande QUAND. Ce n'est pas un oubli : un oracle
# qui partagerait son vocabulaire avec le code qu'il juge ne pourrait plus
# prendre ce code en défaut sur ce vocabulaire — les deux se tromperaient
# ensemble, et la campagne rendrait vert. Les deux listes servent d'ailleurs
# deux questions différentes : celle-ci lit une RÉPONSE, l'autre lit une
# QUESTION.
RADICAUX_DU_TEMPS = ("date", "period", "temporel", "chronolog", "horodat", "annee", "millesim")

# Ce qu'on retire d'un mot avant de le comparer. La ponctuation colle au mot
# qu'elle suit — « aucune colonne de date, elle ne couvre… » — et « date, » n'est
# pas « date ».
PONCTUATION = ",;:.!?()[]«»\"'"

# Où s'arrête une phrase. C'est la portée de la négation, et rien de plus
# étroit : elle nie ce qui la suit DANS SA PHRASE. Un comptage de mots l'aurait
# fait, mais les distances mesurées vont de un — « aucune période » — à six
# — « aucune de mes sources ne contient de colonne temporelle » —, et un seuil
# posé entre les deux refuserait la seconde, qui est une réponse juste.
FIN_DE_PHRASE = re.compile(r"[.;!?\n]")

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
class Exigence:
    """Ce qu'une réponse doit PORTER, jugé par une fonction et non par des mots.

    Le troisième bord de l'oracle, à côté de ``attendus_tous`` et de
    ``interdits``, et il existe parce que les deux premiers ne savent exprimer
    qu'une chose : « cette chaîne-ci est là ». Certaines questions n'attendent
    aucune chaîne en particulier — « de quand datent tes données ? » attend une
    DATE, quelle qu'elle soit, ou le constat qu'il n'y en a pas. Énumérer les
    écritures d'une date ou les façons de dire une absence ferait une liste, et
    une liste refuse la première tournure qu'elle n'a pas prévue.

    ``manque`` est ce que le relevé imprime quand le juge dit non : ce qui
    MANQUE à la réponse, dans les mots de la question, et non le nom de la
    fonction qui l'a refusée.
    """

    manque: str
    juge: Callable[[str], bool]


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

    ``exigence`` : ce qui ne se dit pas en chaînes attendues — un FAIT que la
    réponse doit porter, jugé par une fonction (cf. ``Exigence``).

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
    exigence: Exigence | None = None
    clarification_admise: tuple[str, ...] = ()
    capacite_attendue: str | None = None

    def satisfait(self, reponse: str) -> tuple[bool, list[str]]:
        """Le verdict de l'oracle, et ce qui manquait le cas échéant."""
        plat = replie(reponse)
        manquants = [a for a in self.attendus_tous if replie(a) not in plat]
        if self.attendus_parmi and not any(replie(a) in plat for a in self.attendus_parmi):
            manquants.append("aucun de : " + ", ".join(self.attendus_parmi))
        if self.exigence is not None and not self.exigence.juge(reponse):
            manquants.append(self.exigence.manque)
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

    def cles_de_table(self, source: str = "") -> tuple[str, ...]:
        """Les tables relevées — celles d'une source, ou toutes.

        Une question peut porter sur UNE source (« la source titanic ») ou sur
        tout ce que l'agent possède (« les données que tu as ») : ce n'est pas
        le même périmètre, et l'oracle de période se lit sur le périmètre de sa
        question.
        """
        return tuple(c for c in self.temporelles if not source or c.startswith(f"{source}."))

    def exigence_de_periode(self, *cles_de_table: str) -> Exigence:
        """Ce qu'une réponse sur la PÉRIODE doit porter — décidé par la source.

        Deux exigences, jamais les deux à la fois, et c'est la vérité terrain
        qui tranche. Là où une colonne de date existe, une réponse fondée porte
        une DATE — y accepter « aucune période » bénirait une réponse fausse.
        Là où il n'y en a aucune, elle CONSTATE l'absence — et une date y serait
        inventée.

        **Ce qu'elle n'accepte plus, et pourquoi.** Les NOMS DE COLONNES de la
        table. Ils y étaient pour prouver qu'on avait regardé le schéma au lieu
        d'inventer des bornes plausibles ; ils ont béni une réponse fausse.
        « De quand datent les données que tu as ? » recevait « les données de la
        table `passengers` couvrent des âges allant de 0.42 à 80.0 ans » —
        `age` est une colonne de `passengers`, donc l'oracle était satisfait, et
        une question de DATES comptait juste avec une réponse d'ÂGES (deux
        campagnes, le 2026-09-22, 40/40 les deux fois). Nommer une colonne ne
        prouve rien sur les dates : c'est une exigence qui n'exige rien.

        Le pendant exact du défaut de C51, dans l'autre sens : un oracle qui
        exige UN mot refuse des réponses justes, un oracle qui n'exige rien en
        bénit de fausses.
        """
        if any(self.temporelles[cle] for cle in cles_de_table):
            return Exigence("une date", porte_une_date)
        return Exigence("le constat qu'il n'y a aucune date", constate_l_absence_de_date)


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
            # le périmètre de la question : cette source-là, et ses tables
            exigence=vt.exigence_de_periode(*vt.cles_de_table(src)),
        ),
        QuestionMeta(
            "periode-indirecte",
            "période",
            "De quand datent les données que tu as ?",
            # « que tu as » : toutes les sources, donc toutes les tables
            exigence=vt.exigence_de_periode(*vt.cles_de_table()),
            # Aucune clarification admise, et c'est la seconde porte par
            # laquelle une non-réponse était bénie. « Sur quelle source veux-tu
            # travailler : titanic, iris ? » a compté juste ici (relevés du
            # 2026-09-07, §3 et §7 de docs/surface-conversationnelle.md) : la
            # question ne laisse pourtant RIEN à choisir — elle
            # porte sur tout ce que l'agent a, et les deux sources se datent de
            # la même façon. Une clarification n'y porte ni date ni constat.
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


# Un nombre écrit à la française ou à l'anglaise, groupes de milliers compris.
# Écrit en alternatives EXPLICITES plutôt qu'avec une classe de séparateurs, et
# c'est une correction : `\\d[\\d\\s,.]*\\d` avalait « 2025.\\n\\n3 table(s) » en un
# seul nombre — un point de fin de phrase, deux retours à la ligne, et le compte
# de tables disparaissait du relevé. Un séparateur n'en est un que COLLÉ à des
# chiffres des deux côtés, et par groupes de trois.
#
# Les deux gardes qui encadrent l'alternative comptent autant qu'elle : sans
# elles, « ST-029 911 » rendait 29 911, un code de station recollé au compte de
# la ligne suivante. Un nombre commence après autre chose qu'un mot, un tiret ou
# un séparateur, et finit avant autre chose qu'un mot.
NOMBRE = re.compile(
    r"(?<![\w.,-])(?:"
    r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?"  # 1 757 519,23
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # 1,757,519.23
    r"|\d{1,3}(?:\.\d{3})+(?:,\d+)?"  # 1.757.519,23
    r"|\d+(?:[.,]\d+)?"  # 48000, 1757519.23, 3
    r")(?!\w)"
)


def nombres(texte: str) -> list[float]:
    """Les nombres du texte, séparateurs français comme anglais.

    Sert d'oracle à plusieurs runners : une réponse se juge sur les CHIFFRES
    qu'elle porte, et « 1 757 519,23 » comme « 1,757,519.23 » désignent la même
    valeur. Le dernier séparateur suivi d'une ou deux décimales est le décimal.

    **Et « trois » est le nombre 3.** « Elle comprend TROIS tables » porte le
    même fait que « 3 tables », et l'oracle d'ouverture de source le refusait
    pour n'avoir pas trouvé le caractère. C'est une normalisation de plus, du
    même ordre que celle des séparateurs (cf. ``nombres_dits``).
    """
    trouves: list[float] = []
    for brut in NOMBRE.findall(texte):
        nettoye = re.sub(r"[\s\u00a0\u202f]", "", brut)
        if "," in nettoye and "." in nettoye:
            nettoye = (
                nettoye.replace(",", "")
                if nettoye.rindex(".") > nettoye.rindex(",")
                else nettoye.replace(".", "").replace(",", ".")
            )
        elif re.search(r",\d{1,2}$", nettoye):
            nettoye = nettoye.replace(",", ".")
        else:
            nettoye = nettoye.replace(",", "").replace(".", "") if _groupe(nettoye) else nettoye
        try:
            trouves.append(float(nettoye))
        except ValueError:
            continue
    return trouves + nombres_dits(texte)


def _groupe(nettoye: str) -> bool:
    """Un séparateur de MILLIERS seul (« 1,757,519 » ou « 1.757.519 »)."""
    return bool(re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", nettoye))


# --- les deux oracles qui jugent un FAIT et non son orthographe ---------------
#
# Un oracle qui compare des sous-chaînes mesure une TYPOGRAPHIE. Deux l'ont
# fait, et les deux refusaient des réponses justes :
#
#   · le parcours de démonstration rejetait « ne doit pas être prise en compte
#     dans tout calcul de puissance » — la consigne dite en entier — parce
#     qu'aucune des dix-sept tournures de sa liste n'en était une sous-chaîne
#     exacte : « pas prise en compte » y est, mais le « être » de la réponse
#     tombe au milieu ;
#   · l'ouverture de source rejetait « Elle comprend TROIS tables » parce
#     qu'elle cherchait le caractère `3`.
#
# Ce qu'on NE fait pas : allonger les listes. Une liste plus longue est la même
# erreur en plus long, et c'est écrit trois fois dans ce dépôt — un lexique est
# une liste, et la famille des façons de dire une chose est ouverte. Ce qui
# change est la COMPARAISON : on replie les deux côtés et on compare des
# radicaux, exactement comme `introspection._actions` le fait pour les actions
# qu'une réponse doit porter ; et on lit un nombre écrit en lettres comme le
# nombre qu'il est.

# Ce qu'on retire d'un mot pour en garder le radical, et le seuil en deçà
# duquel on n'y touche pas. Les deux valeurs sont celles d'`introspection` —
# `_TERMINAISON` et `_RADICAL_MINIMAL` — et c'est délibéré : mesurer qu'un fait
# est dit et exiger qu'il le soit sont la même opération, faite à deux endroits.
TERMINAISON = 2
RADICAL_MINIMAL = 5


def radicaux(texte: str) -> list[tuple[str, bool]]:
    """Les mots du texte, repliés, dans l'ordre — avec le fait qu'on les a coupés.

    « prise » et « prises », « écarter » et « écartée » rendent le même jeton.
    Le booléen dit si le mot a été RACCOURCI, et il décide de la comparaison :
    un jeton coupé se compare par préfixe — « exclu » doit reconnaître
    « exclusion » — un mot court se compare entier, sans quoi « pas »
    reconnaîtrait « passager ».

    L'ordre est gardé parce qu'il sert : une tournure est portée quand ses
    radicaux se retrouvent DANS L'ORDRE, ce qui distingue « pas prise en
    compte » d'une réponse où ces trois mots-là se croisent par hasard.
    """
    jetons = []
    for mot in introspection.replie(texte).split():
        coupe = len(mot) >= RADICAL_MINIMAL
        jetons.append((mot[:-TERMINAISON] if coupe else mot, coupe))
    return jetons


def porte_le_fait(texte: str, tournures: tuple[str, ...]) -> bool:
    """Le texte DIT-il la chose, quelle que soit la façon de l'écrire ?

    ``tournures`` reste une disjonction — plusieurs façons de dire, une seule
    suffit — mais chacune est confrontée par ses RADICAUX pris dans l'ordre, et
    non comme une sous-chaîne. Une tournure qui ne porte aucun mot (« >= 0 »,
    « 3 % ») est comparée telle quelle : il n'y a pas de radical à en tirer, et
    c'est bien son écriture qu'on cherche.
    """
    portes = radicaux(texte)
    plat = texte.lower()
    return any(_tournure_portee(portes, plat, tournure) for tournure in tournures)


def _tournure_portee(portes: list[tuple[str, bool]], plat: str, tournure: str) -> bool:
    attendus = radicaux(tournure)
    if not attendus:
        return tournure.lower() in plat
    reste = iter(portes)
    return all(any(_meme_mot(porte, attendu) for porte in reste) for attendu in attendus)


def _meme_mot(porte: tuple[str, bool], attendu: tuple[str, bool]) -> bool:
    """Deux jetons désignent-ils le même mot ?

    Un jeton COUPÉ ne prétend qu'à son début : « exclu » doit reconnaître
    « exclusion », et « fausse » « fausserait ». Un mot qu'on n'a pas coupé est
    trop court pour qu'un préfixe veuille dire quoi que ce soit — « pas »
    reconnaîtrait « passager » — et se compare entier.
    """
    jeton, coupe = porte
    cible, coupee = attendu
    if coupee or coupe:
        return jeton.startswith(cible) or cible.startswith(jeton)
    return jeton == cible


def porte_une_date(texte: str) -> bool:
    """Le texte porte-t-il une DATE ?

    Un millésime, et rien d'autre à chercher : toute écriture d'une date en
    porte un — « 1912-04-10 », « 10/04/1912 », « avril 1912 », « 1912 ». C'est
    une FORME et non une liste d'écritures acceptées, du même ordre que
    ``NOMBRE`` : la famille des façons d'écrire une date est ouverte, celle des
    façons d'écrire une année ne l'est pas.

    Ce qu'il refuse est ce qu'on cherchait à refuser : « des âges allant de 0.42
    à 80.0 ans » ne porte aucune année, et ne dit donc pas QUAND.
    """
    return MILLESIME.search(introspection.replie(texte)) is not None


def _nie(mot: str) -> bool:
    """Ce mot-ci nie-t-il l'existence de ce qui suit ?"""
    return mot in MOTS_D_ABSENCE or any(mot.startswith(r) for r in RADICAUX_D_ABSENCE)


def _parle_du_temps(mot: str) -> bool:
    return any(mot.startswith(radical) for radical in RADICAUX_DU_TEMPS)


def constate_l_absence_de_date(texte: str) -> bool:
    """Le texte CONSTATE-t-il qu'il n'y a aucune date ?

    Une négation, puis dans la même phrase le temps qu'elle nie, et dans cet
    ORDRE : « aucune colonne de date », « pas de date », « sans aucune donnée
    temporelle », « aucune de mes sources ne contient de colonne temporelle ».
    Aucune de ces quatre écritures n'est listée nulle part — ce sont les deux
    vocabulaires et leur ordre qui les reconnaissent toutes, comme
    ``introspection._actions`` reconnaît « je prédis » là où le fait dit
    « prédire ».

    **L'ordre fait tout le travail, et c'est une distinction mesurée.** Le
    constat met sa négation AVANT ce qu'elle nie, et nie une EXISTENCE ; la
    réponse vague la met APRÈS, et ne nie qu'une qualité — « une période non
    spécifiée dans sa description », deux campagnes sur deux avant C52, porte
    « période » et « non » et ne dit toujours pas qu'il n'y a pas de date. Un
    oracle qui aurait cherché la seule rencontre des deux mots l'aurait bénie.
    """
    for phrase in FIN_DE_PHRASE.split(introspection.replie(texte)):
        mots = [mot.strip(PONCTUATION) for mot in phrase.split()]
        nie = next((rang for rang, mot in enumerate(mots) if _nie(mot)), None)
        if nie is not None and any(_parle_du_temps(mot) for mot in mots[nie + 1 :]):
            return True
    return False


# Les nombres écrits EN LETTRES, lus comme des nombres. Ce n'est pas une liste
# de tournures acceptées : c'est une NORMALISATION, du même ordre que le
# repliage des accents ou la reconnaissance de « 1 757 519,23 » et de
# « 1,757,519.23 » comme la même valeur. Une réponse qui écrit « trois tables »
# porte le fait « 3 tables ».
#
# `un` et `une` n'y sont pas, et c'est la seule exclusion : ce sont les articles
# indéfinis du français, et les lire comme le nombre 1 mettrait un 1 dans
# presque toutes les réponses. Aucun oracle de ces campagnes n'attend 1.
NOMBRES_EN_LETTRES = {
    "zero": 0,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
    "onze": 11,
    "douze": 12,
    "treize": 13,
    "quatorze": 14,
    "quinze": 15,
    "seize": 16,
    "vingt": 20,
    "trente": 30,
    "quarante": 40,
    "cinquante": 50,
    "soixante": 60,
    "cent": 100,
    "cents": 100,
    "mille": 1000,
}


def nombres_dits(texte: str) -> list[float]:
    """Les nombres écrits en lettres (cf. ``NOMBRES_EN_LETTRES``)."""
    return [
        float(NOMBRES_EN_LETTRES[mot])
        for mot in introspection.replie(texte).split()
        if mot in NOMBRES_EN_LETTRES
    ]


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
