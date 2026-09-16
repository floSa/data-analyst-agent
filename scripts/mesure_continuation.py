"""Mesure ce que l'agent fait d'un message qui NE SE SUFFIT PAS À LUI-MÊME.

« Oui », « et dedans ? », « tu ne m'as pas répondu » : trois messages qui ne
portent pas leur sujet. Ils le prennent au tour d'avant. Le défaut mesuré le
2026-09-16 est que l'agent POSE lui-même la question fermée — « souhaitez-vous
que je vous donne les détails sur `referentiel` et `facturation` ? » — et ne
tient pas ce qu'il vient de proposer : « oui » lui revient, et il répond « je
n'ai pas bien compris ta demande » suivi de son menu.

**Le tour d'amorce est JOUÉ**, sauf là où il ne peut pas l'être. Chaque cas
pose d'abord une vraie question à l'agent, garde la réponse qu'il a réellement
écrite, puis lui envoie le message de continuation avec ce tour-là en mémoire —
exactement comme le fait la route ``/chat`` (``_dernier_echange`` +
``echange_precedent``). Fabriquer le tour d'amorce mesurerait la réaction de
l'agent à un texte d'auteur ; ce qu'on veut mesurer est sa réaction à SA PROPRE
phrase.

L'exception est la situation (a), et la première campagne l'a imposée : jouée,
l'amorce se termine presque toujours par une question OUVERTE — « quelle source
souhaitez-vous explorer ? » — à laquelle « oui » ne répond à rien. Une question
FERMÉE ne se commande pas. Ses quatre réponses d'amorce sont donc figées, sur le
patron de celle que l'agent a réellement écrite le jour du défaut.

Trois situations de tour précédent, quatre cas chacune :

- **(a) l'agent a posé une question fermée** — « souhaitez-vous que… ? », et
  l'utilisateur accepte. C'est le défaut nommé : ce que l'agent PROPOSE, il
  doit pouvoir le tenir.
- **(b) l'agent a répondu et l'utilisateur relance** — « et dedans ? »,
  « et les colonnes ? », « et pour l'autre ? ». Le sujet est au tour d'avant.
- **(c) l'utilisateur conteste** — « tu ne m'as pas répondu », « ce n'est pas
  ce que je demandais ». Le message ne dit rien du sujet : il dit que la
  réponse d'avant a manqué sa cible.

Le verdict est **mécanique** et son oracle est tiré des sources de vérité — les
noms du catalogue, les tables lues dans le schéma réel, les champs des schémas
d'attributs. Trois issues :

- ``tenu`` : pas de repli, la réponse porte au moins un des noms que le tour
  d'avant appelait, et AUCUN de ceux qu'il excluait ;
- ``a_cote`` : pas de repli, mais aucun nom attendu — ou un déballage, c'est-à-dire
  une réponse qui ajoute ce que le tour d'avant excluait. Les deux disent la même
  chose : ce qui est rendu ne continue pas le tour ;
- ``perdu`` : le repli « je n'ai pas bien compris », ou une erreur.

Les **interdits** ne sont pas un raffinement : sans eux la mesure compte faux.
À « oui » sur une proposition portant sur deux sources, l'agent qui déballe
l'inventaire des cinq a perdu le tour — et sa réponse contient pourtant les
deux noms attendus.

Le **fil brut** des échecs est journalisé : le message tel qu'il part au modèle
dans le nœud système, et ce que le modèle renvoie. C'est ce qui distingue
« le modèle n'a pas compris » de « le modèle a compris et le socle n'a pas
entendu » — et c'est la seconde qui a été mesurée.

    uv run python scripts/mesure_continuation.py
    uv run python scripts/mesure_continuation.py --tirages 3 --markdown /tmp/avant.md
    uv run python scripts/mesure_continuation.py --only a-oui-nu

Prérequis : le serveur LLM répond (``DAA_LLM_BASE_URL``) et les sources du
catalogue sont joignables. Mesuré sur le catalogue de démonstration :

    DAA_CATALOG_PATH=sources/demonstration/catalogue.yaml \\
        uv run python scripts/mesure_continuation.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
import uuid
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS
from data_analyst_agent.agents.retrieval.catalog import Catalog, load_catalog, open_source
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator.graph import ChatAnswer, Orchestrator

# Le repli du planificateur, reconnu à cette phrase — le SYMPTÔME que cette
# mesure existe pour compter. Même marqueur que
# `scripts/mesure_surface_conversationnelle.py` : deux mesures qui compteraient
# deux replis différents ne se compareraient pas.
MARQUEUR_DE_REPLI = "je n'ai pas bien compris"

VERDICTS = ("tenu", "a_cote", "perdu")
LIBELLES = {
    "tenu": "le tour est tenu",
    "a_cote": "répondu à côté du tour d'avant (ou déballé ce qu'il excluait)",
    "perdu": "repli « je n'ai pas bien compris »",
}
SITUATIONS = {
    "a": "(a) l'agent a posé une question fermée",
    "b": "(b) l'agent a répondu, l'utilisateur relance",
    "c": "(c) l'utilisateur conteste",
}


def replie(texte: str) -> str:
    """Minuscules, sans accents, sans décoration Markdown.

    Même repli que celui de la mesure de surface, et pour les mêmes raisons :
    le modèle alterne « télémétrie » et « telemetrie » d'un tour à l'autre, et
    échappe les blancs soulignés pour l'affichage (``lignes\\_facture``).
    """
    sans_accent = unicodedata.normalize("NFKD", texte.lower())
    nu = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return nu.replace("\\", "").replace("*", "").replace("`", "")


def _cite(plat: str, nom: str) -> bool:
    """Ce nom est-il cité dans ce texte replié, comme un MOT et non un fragment ?

    Entouré de limites, sur le modèle d'``introspection._nomme_dans`` : sans
    cela, `interventions` se trouverait dans « interventions_reseau » et
    `exploitation` dans « en exploitation », et un interdit se déclencherait
    sur un mot de la phrase au lieu d'un nom du catalogue.
    """
    return bool(re.search(rf"(?<![\w-]){re.escape(replie(nom).strip())}(?![\w-])", plat))


@dataclass(frozen=True)
class Cas:
    """Un tour d'amorce, un message de continuation, et ce que la suite doit porter.

    ``attendus`` : les noms dont AU MOINS UN doit se retrouver dans la réponse
    au message de continuation. Ils viennent de la vérité terrain, jamais d'une
    liste écrite à la main. Un seul suffit : on mesure si le tour est tenu, pas
    l'exhaustivité d'une énumération — c'est la mesure de surface qui juge
    l'énumération, et elle le fait déjà.

    ``interdits`` : les noms que la réponse ne doit PAS porter, et sans eux la
    mesure compte faux. Mesuré : à « oui » sur une proposition qui ne portait
    que sur `referentiel` et `facturation`, l'agent d'avant déballait
    l'inventaire des CINQ sources — une réponse qui a perdu le tour, et qui
    contient pourtant les deux noms attendus. Un attendu trouvé dans un
    déballage ne prouve rien. Les interdits sont le complément des attendus,
    tirés de la même vérité terrain : ce que le tour d'avant EXCLUAIT.

    ``reponse_figee`` : la réponse d'amorce, quand elle ne peut pas être jouée.
    Elle ne l'est que pour la situation (a), et c'est une leçon de la première
    campagne, pas une commodité. Le tour d'amorce JOUÉ ne produit presque
    jamais de question fermée : l'agent termine par « Quelle source
    souhaitez-vous explorer ? », une question OUVERTE à laquelle « oui » n'a
    aucune réponse déterminée — et la situation (a) ne mesurait alors pas ce
    qu'elle annonce. Une question fermée ne se commande pas ; elle se fige.
    Les quatre réponses figées suivent le patron de celle que l'agent a
    réellement écrite le jour du défaut : une action nommée, puis
    « souhaitez-vous que je fasse cela ? ».
    """

    cle: str
    situation: str
    amorce: str
    continuation: str
    attendus: tuple[str, ...]
    source: str = ""
    reponse_figee: str = ""
    interdits: tuple[str, ...] = ()

    def porte(self, reponse: str) -> tuple[bool, list[str], list[str]]:
        plat = replie(reponse)
        trouves = [a for a in self.attendus if _cite(plat, a)]
        deballes = [i for i in self.interdits if _cite(plat, i)]
        return (bool(trouves) and not deballes), trouves, deballes


@dataclass
class Releve:
    cas: Cas
    tirage: int
    amorce_rendue: str
    reponse: str
    verdict: str
    trouves: list[str]
    deballes: list[str]
    noeuds: list[str]
    erreur: str | None
    appels_llm: int
    duree_ms: int
    # Le fil brut du nœud système au tour de continuation : ce qui part au
    # modèle, ce qu'il renvoie. Journalisé pour tous, imprimé pour les échecs.
    fil: list[dict] = field(default_factory=list)


class ModeleEspion(WrapperModel):
    """Compte les appels et garde le fil brut du dernier tour.

    Le compteur sert au coût ; le fil sert à la cause. Sans lui, un échec se
    raconte (« le modèle n'a pas compris ») au lieu de se montrer — et ce qui
    a été mesuré ici est précisément l'inverse de ce qu'on aurait raconté :
    le modèle COMPREND, il écrit l'appel d'outil en prose au lieu de l'émettre.
    """

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.appels = 0
        self.fil: list[dict] = []
        self.enregistre = False

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ):
        self.appels += 1
        reponse = await super().request(messages, model_settings, model_request_parameters)
        if self.enregistre:
            self.fil.append({"envoi": [_lisible(m) for m in messages], "retour": _lisible(reponse)})
        return reponse


def _lisible(message: ModelMessage) -> dict:
    """Un message du fil, réduit à ce qui se lit dans un rapport.

    Le prompt système est remplacé par sa taille : il pèse cinq mille
    caractères, il est le même à chaque tour, et ce n'est pas lui qu'on
    cherche dans un fil brut.
    """
    parts = []
    for part in message.parts:
        nom = type(part).__name__
        if nom == "SystemPromptPart":
            parts.append({"type": nom, "contenu": f"<{len(part.content)} caractères>"})
        elif nom == "ToolCallPart":
            parts.append({"type": nom, "outil": part.tool_name, "args": str(part.args)[:200]})
        elif nom == "ToolReturnPart":
            parts.append({"type": nom, "outil": part.tool_name, "contenu": str(part.content)[:200]})
        else:
            parts.append({"type": nom, "contenu": str(getattr(part, "content", part))[:800]})
    entree = {"role": type(message).__name__, "parts": parts}
    instructions = getattr(message, "instructions", None)
    if instructions:
        entree["instructions"] = f"<{len(instructions)} caractères>"
    return entree


@dataclass(frozen=True)
class VeriteTerrain:
    """Ce que l'installation déclare, lu là où elle le déclare."""

    sources: tuple[str, ...]
    tables: dict[str, tuple[str, ...]]
    colonnes: dict[str, tuple[str, ...]]
    datasets: tuple[str, ...]
    features: dict[str, tuple[str, ...]]

    @classmethod
    def lire(cls, catalogue: Catalog, registre: Registry) -> VeriteTerrain:
        tables: dict[str, tuple[str, ...]] = {}
        colonnes: dict[str, tuple[str, ...]] = {}
        for source in catalogue.sources:
            with closing(open_source(source)) as adaptateur:
                schema = adaptateur.schema()
            tables[source.name] = tuple(t.name for t in schema.tables)
            for table in schema.tables:
                colonnes[f"{source.name}.{table.name}"] = tuple(c.name for c in table.columns)
        return cls(
            sources=tuple(s.name for s in catalogue.sources),
            tables=tables,
            colonnes=colonnes,
            datasets=tuple(registre.datasets),
            features={d: tuple(SCHEMAS[d].model_fields) for d in registre.datasets if d in SCHEMAS},
        )

    def toutes_colonnes(self, source: str) -> tuple[str, ...]:
        return tuple(
            colonne
            for table in self.tables[source]
            for colonne in self.colonnes[f"{source}.{table}"]
        )


def batterie(vt: VeriteTerrain) -> list[Cas]:
    """Douze cas, quatre par situation, oracles branchés sur la vérité terrain.

    Les amorces ne sont pas décoratives : chacune est choisie pour que le tour
    de continuation ait un référent VÉRIFIABLE. « Et les colonnes ? » après
    « décris-moi `facturation` » a une réponse ; le même « et les colonnes ? »
    après « quelles sources as-tu ? » n'en a pas, et compterait faux une
    réponse qui demande laquelle — ce serait mesurer une ambiguïté qu'on a
    soi-même semée.
    """

    def hors_sources(*gardees: str) -> tuple[str, ...]:
        """Les sources que le tour d'avant EXCLUAIT — celles qu'un déballage ajoute."""
        return tuple(s for s in vt.sources if s not in gardees)

    def hors_modeles(*gardes: str) -> tuple[str, ...]:
        """Les attributs des AUTRES modèles, plus toutes les sources.

        Les autres modèles parce que « et pour `iris` ? » ne demande pas les
        attributs de `titanic` ; toutes les sources parce que le repli qu'on
        mesure est précisément un inventaire de sources servi à la place.
        """
        autres = tuple(
            champ
            for dataset, champs in vt.features.items()
            if dataset not in gardes
            for champ in champs
            if all(champ not in vt.features.get(g, ()) for g in gardes)
        )
        return autres + vt.sources

    facturation = vt.tables.get("facturation", ())
    telemetrie = vt.tables.get("telemetrie", ())
    exploitation = vt.tables.get("exploitation", ())
    referentiel = vt.toutes_colonnes("referentiel") if "referentiel" in vt.tables else ()
    return [
        # --- (a) l'agent a posé une question fermée --------------------------
        # Réponse d'amorce FIGÉE : l'agent jouant ce tour termine presque
        # toujours par une question OUVERTE (« quelle source souhaitez-vous
        # explorer ? »), et « oui » n'y répond à rien. Cf. `Cas.reponse_figee`.
        Cas(
            cle="a-oui-explicite",
            situation="a",
            amorce="Quelles sources de données as-tu ?",
            reponse_figee=(
                "J'ai accès aux sources de données suivantes : "
                + ", ".join(f"`{s}`" for s in vt.sources)
                + ".\n\nJe peux vous donner les détails sur `referentiel` et "
                "`facturation`. Souhaitez-vous que je fasse cela ?"
            ),
            continuation="Oui, je souhaite que tu fasses ça",
            attendus=("referentiel", "facturation"),
            interdits=hors_sources("referentiel", "facturation"),
        ),
        Cas(
            cle="a-oui-nu",
            situation="a",
            amorce="Quelles sources de données as-tu ?",
            reponse_figee=(
                "J'ai accès aux sources de données suivantes : "
                + ", ".join(f"`{s}`" for s in vt.sources)
                + ".\n\nJe peux vous donner les détails sur `referentiel` et "
                "`facturation`. Souhaitez-vous que je fasse cela ?"
            ),
            continuation="Oui",
            attendus=("referentiel", "facturation"),
            interdits=hors_sources("referentiel", "facturation"),
        ),
        Cas(
            cle="a-vas-y",
            situation="a",
            amorce="Quelles tables contient la source `telemetrie` ?",
            reponse_figee=(
                "La source `telemetrie` contient "
                f"{len(telemetrie)} table(s).\n\nSouhaitez-vous que je vous "
                "détaille leurs colonnes ?"
            ),
            continuation="vas-y",
            attendus=telemetrie,
            interdits=hors_sources("telemetrie"),
        ),
        Cas(
            cle="a-accord",
            situation="a",
            amorce="De quels modèles de prédiction disposes-tu ?",
            reponse_figee=(
                "Je dispose des modèles suivants : "
                + ", ".join(f"`{d}`" for d in vt.datasets)
                + ".\n\nSouhaitez-vous que je vous dise quels attributs "
                "`titanic` attend pour prédire ?"
            ),
            continuation="d'accord, fais-le",
            attendus=vt.features.get("titanic", ()),
            interdits=hors_modeles("titanic"),
        ),
        # --- (b) l'utilisateur relance ---------------------------------------
        Cas(
            cle="b-et-dedans",
            situation="b",
            amorce="Quelles tables contient la source `telemetrie` ?",
            continuation="et dedans ?",
            attendus=telemetrie,
            interdits=hors_sources("telemetrie"),
        ),
        Cas(
            cle="b-et-les-colonnes",
            situation="b",
            amorce="Décris-moi la source `facturation`.",
            continuation="et les colonnes ?",
            attendus=facturation,
            interdits=hors_sources("facturation"),
        ),
        Cas(
            cle="b-lesquelles",
            situation="b",
            amorce="Quelles tables contient la source `exploitation` ?",
            continuation="lesquelles portent des dates ?",
            attendus=exploitation,
            interdits=hors_sources("exploitation"),
        ),
        Cas(
            cle="b-et-pour-l-autre",
            situation="b",
            amorce="De quels attributs a besoin le modèle `titanic` pour prédire ?",
            continuation="et pour `iris` ?",
            attendus=vt.features.get("iris", ()),
            interdits=hors_modeles("iris"),
        ),
        # --- (c) l'utilisateur conteste --------------------------------------
        Cas(
            cle="c-pas-repondu",
            situation="c",
            amorce="Quelles sources de données as-tu ?",
            continuation="tu ne m'as pas répondu",
            attendus=vt.sources,
        ),
        Cas(
            cle="c-pas-ce-que-je-demandais",
            situation="c",
            amorce="Décris-moi la source `referentiel`.",
            continuation="ce n'est pas ce que je demandais",
            attendus=(*referentiel, "referentiel"),
            interdits=hors_sources("referentiel"),
        ),
        Cas(
            cle="c-pas-ma-question",
            situation="c",
            amorce="Quelles tables contient la source `facturation` ?",
            continuation="tu n'as pas répondu à ma question",
            attendus=facturation,
            interdits=hors_sources("facturation"),
        ),
        Cas(
            cle="c-pas-ca",
            situation="c",
            amorce="De quels modèles de prédiction disposes-tu ?",
            continuation="ce n'est pas ça que je voulais savoir",
            attendus=vt.datasets,
            interdits=vt.sources,
        ),
    ]


def juger(cas: Cas, reponse: ChatAnswer, texte: str) -> tuple[str, list[str], list[str]]:
    if reponse.error:
        return "perdu", [], []
    if MARQUEUR_DE_REPLI in replie(texte):
        return "perdu", [], []
    porte, trouves, deballes = cas.porte(texte)
    return ("tenu" if porte else "a_cote"), trouves, deballes


def poser(orchestrateur: Orchestrator, espion: ModeleEspion, cas: Cas, tirage: int) -> Releve:
    """Les deux tours : l'amorce, puis la continuation avec l'amorce en mémoire.

    Le fil est neuf à chaque tirage — un tirage ne doit rien devoir au
    précédent — et le second tour reçoit ce que la route ``/chat`` lui passe :
    le dernier échange, et la source que le premier tour a éventuellement liée.

    Sauf ``reponse_figee``, où le premier tour n'est pas joué : le fil part du
    tour figé, et rien d'autre ne change. C'est le seul moyen de mettre l'agent
    devant une question fermée qu'il ne pose pas de lui-même.
    """
    fil = f"cont-{uuid.uuid4().hex[:8]}"
    avant = espion.appels
    depart = time.monotonic()
    if cas.reponse_figee:
        rendue = cas.reponse_figee
        source_liee = cas.source
    else:
        premier = orchestrateur.ask(cas.amorce, conversation_id=fil, source_de_travail=cas.source)
        rendue = premier.answer
        source_liee = premier.source_de_travail
    espion.fil.clear()
    espion.enregistre = True
    try:
        second = orchestrateur.ask(
            cas.continuation,
            conversation_id=fil,
            source_de_travail=source_liee,
            echange_precedent=(cas.amorce, rendue),
        )
    finally:
        espion.enregistre = False
    duree = int((time.monotonic() - depart) * 1000)
    tableau = " ".join(a.data for a in second.artifacts if a.mime == "application/json")
    texte = f"{second.answer}\n{tableau}"
    verdict, trouves, deballes = juger(cas, second, texte)
    return Releve(
        cas=cas,
        tirage=tirage,
        amorce_rendue=" ".join(rendue.split()),
        reponse=" ".join(second.answer.split()),
        verdict=verdict,
        trouves=trouves,
        deballes=deballes,
        noeuds=[s.node for s in second.trace],
        erreur=second.error,
        appels_llm=espion.appels - avant,
        duree_ms=duree,
        fil=list(espion.fil),
    )


def _part_lisible(part: dict) -> str:
    """Une part du fil brut, en une ligne bornée — c'est un rapport, pas un dump."""
    return f"[{part['type']}] {json.dumps(part, ensure_ascii=False)[:600]}"


def une_ligne(texte: str, largeur: int = 200) -> str:
    plat = " ".join(texte.split())
    return plat if len(plat) <= largeur else plat[: largeur - 1] + "…"


def rapport(releves: list[Releve], reglages, titre: str) -> str:
    tenus = sum(1 for r in releves if r.verdict == "tenu")
    lignes = [
        f"## {titre}",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`), "
        f"catalogue `{reglages.catalog_path}`.",
        "",
        f"**{tenus}/{len(releves)} tours tenus, {sum(r.appels_llm for r in releves)} appels LLM.**",
        "",
        "Par situation :",
        "",
    ]
    for code, libelle in SITUATIONS.items():
        lot = [r for r in releves if r.cas.situation == code]
        if not lot:
            continue
        bons = sum(1 for r in lot if r.verdict == "tenu")
        lignes.append(f"- **{libelle}** : {bons}/{len(lot)}")
    lignes += [
        "",
        "| clé | situation | amorce | continuation | score | nœuds du 2ᵉ tour "
        "| ce qui a décidé | ce qui est rendu |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cle in dict.fromkeys(r.cas.cle for r in releves):
        lot = [r for r in releves if r.cas.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "tenu")
        noeuds = " / ".join(dict.fromkeys(" → ".join(r.noeuds) for r in lot))
        echecs = [r for r in lot if r.verdict != "tenu"]
        montre = echecs[0] if echecs else lot[0]
        if montre.verdict == "perdu":
            pourquoi = "repli"
        elif montre.deballes:
            pourquoi = "déballé : " + ", ".join(f"`{d}`" for d in montre.deballes[:3])
        elif not montre.trouves:
            pourquoi = "aucun nom attendu"
        else:
            pourquoi = "porte " + ", ".join(f"`{t}`" for t in montre.trouves[:3])
        lignes.append(
            f"| `{cle}` | {lot[0].cas.situation} | {une_ligne(lot[0].cas.amorce, 60)} "
            f"| {une_ligne(lot[0].cas.continuation, 40)} | **{bons}/{len(lot)}** | {noeuds} "
            f"| {pourquoi} | {une_ligne(montre.reponse, 110)} |"
        )
    perdus = [r for r in releves if r.verdict != "tenu"]
    if perdus:
        lignes += ["", "### Le fil brut des échecs", ""]
        for cle in dict.fromkeys(r.cas.cle for r in perdus):
            r = next(x for x in perdus if x.cas.cle == cle)
            origine = "figé" if r.cas.reponse_figee else "joué"
            lignes += [
                f"**`{cle}`** — {LIBELLES[r.verdict]}",
                "",
                "```",
                f"TOUR 1 ({origine}) — utilisateur : {r.cas.amorce}",
                f"TOUR 1 — agent : {une_ligne(r.amorce_rendue, 400)}",
                f"TOUR 2 — utilisateur : {r.cas.continuation}",
                "",
                "Ce qui part au modèle dans le nœud système, et ce qu'il renvoie :",
            ]
            for appel in r.fil[:1]:
                for message in appel["envoi"]:
                    for part in message["parts"]:
                        lignes.append(f"  ENVOI {_part_lisible(part)}")
                for part in appel["retour"]["parts"]:
                    lignes.append(f"  RETOUR {_part_lisible(part)}")
            lignes += [
                "",
                f"TOUR 2 — agent : {une_ligne(r.reponse, 400)}",
                f"nœuds : {' → '.join(r.noeuds)}",
                "```",
                "",
            ]
    return "\n".join(lignes).rstrip()


def journal(releves: list[Releve]) -> list[dict]:
    return [
        {
            "cle": r.cas.cle,
            "situation": r.cas.situation,
            "tirage": r.tirage,
            "amorce": r.cas.amorce,
            "amorce_rendue": r.amorce_rendue,
            "continuation": r.cas.continuation,
            "reponse": r.reponse,
            "verdict": r.verdict,
            "trouves": r.trouves,
            "deballes": r.deballes,
            "noeuds": r.noeuds,
            "erreur": r.erreur,
            "appels_llm": r.appels_llm,
            "duree_ms": r.duree_ms,
            "fil": r.fil,
        }
        for r in releves
    ]


def rejuger(journal_lu: list[dict], cas: dict[str, Cas]) -> list[Releve]:
    """Applique l'oracle D'AUJOURD'HUI aux réponses D'HIER.

    Un oracle se corrige en cours de chantier — celui-ci deux fois — et un
    oracle corrigé ne vaut que s'il est appliqué des DEUX côtés de la
    comparaison. Rejouer deux heures de moteur pour ça serait payer le
    changement d'un verdict au prix d'une campagne, et introduirait une
    variance qui n'a rien à voir avec ce qu'on mesure. Les réponses sont dans
    le journal ; seul le jugement est neuf.
    """
    releves = []
    for ligne in journal_lu:
        cas_du_tour = cas[ligne["cle"]]
        if ligne["erreur"] or MARQUEUR_DE_REPLI in replie(ligne["reponse"]):
            verdict, trouves, deballes = "perdu", [], []
        else:
            porte, trouves, deballes = cas_du_tour.porte(ligne["reponse"])
            verdict = "tenu" if porte else "a_cote"
        releves.append(
            Releve(
                cas=cas_du_tour,
                tirage=ligne["tirage"],
                amorce_rendue=ligne["amorce_rendue"],
                reponse=ligne["reponse"],
                verdict=verdict,
                trouves=trouves,
                deballes=deballes,
                noeuds=ligne["noeuds"],
                erreur=ligne["erreur"],
                appels_llm=ligne["appels_llm"],
                duree_ms=ligne["duree_ms"],
                fil=ligne.get("fil", []),
            )
        )
    return releves


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tirages", type=int, default=3, help="tirages par cas (défaut : 3)")
    parser.add_argument("--only", nargs="*", metavar="CLE", help="ne jouer que ces cas")
    parser.add_argument("--titre", default="Continuation d'un tour", help="titre du rapport")
    parser.add_argument("--markdown", type=Path, help="écrit le tableau ici")
    parser.add_argument("--json", type=Path, help="écrit le journal complet ici")
    parser.add_argument(
        "--rejuger",
        type=Path,
        metavar="JOURNAL",
        help="ne joue rien : applique l'oracle d'aujourd'hui aux réponses de ce journal",
    )
    args = parser.parse_args(argv)

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    registre = Registry.load(reglages.models_registry_path)
    print(f"Sources : {', '.join(s.name for s in catalogue.sources)}")
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})\n")

    cas = batterie(VeriteTerrain.lire(catalogue, registre))
    if args.only:
        demandes = set(args.only)
        cas = [c for c in cas if c.cle in demandes]
        if not cas:
            raise SystemExit(f"aucun cas ne porte ces clés : {', '.join(sorted(demandes))}")

    if args.rejuger:
        releves = rejuger(
            json.loads(args.rejuger.read_text(encoding="utf-8")), {c.cle: c for c in cas}
        )
        texte = rapport(releves, reglages, args.titre)
        print(texte)
        if args.markdown:
            args.markdown.write_text(texte + "\n", encoding="utf-8")
            print(f"\nTableau écrit dans {args.markdown}")
        return

    espion = ModeleEspion(build_model(reglages))
    orchestrateur = Orchestrator(
        settings=reglages, model=espion, catalog=catalogue, registry=registre
    )
    releves: list[Releve] = []
    total = len(cas) * args.tirages
    numero = 0
    for c in cas:
        for tirage in range(1, args.tirages + 1):
            numero += 1
            print(f"[{numero}/{total}] {c.cle} #{tirage} — « {c.continuation} »", flush=True)
            releve = poser(orchestrateur, espion, c, tirage)
            releves.append(releve)
            print(f"    amorce  → « {une_ligne(releve.amorce_rendue, 150)} »")
            print(f"    suite   → {LIBELLES[releve.verdict]} ({' → '.join(releve.noeuds)})")
            print(f"    « {une_ligne(releve.reponse, 150)} »", flush=True)

    texte = rapport(releves, reglages, args.titre)
    print("\n" + texte)
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
