"""Ce qu'on LIT dans une source pour la décrire : tables, lignes, période.

Le catalogue déclare un nom, un type et une description écrite à la main. C'est
ce qu'il faut pour router, et c'est trop peu pour **choisir** : « employes » et
« titanic » se ressemblent sur trois lignes de YAML, et rien n'y dit lequel des
deux couvre l'année qu'on cherche ni lequel pèse trois cents lignes. D'où ce
module : trois faits par source, tous **lus dans la source elle-même**.

- le **nombre de tables**, du schéma ;
- le **nombre de lignes**, d'un ``count(*)`` par table ;
- la **période couverte**, d'un ``min``/``max`` sur la colonne de date que la
  source DÉSIGNE (``date_reference``), à défaut la première du schéma — et le
  CONSTAT qu'il n'y en a pas quand il n'y en a pas, avec sa raison.

**Une absence se constate.** Ne rien dire de la période d'une source qui n'en a
pas semblait honnête et coûtait une question, mesurée trois tirages sur trois le
2026-09-22 : « sur quelle période portent les données de la source titanic ? »
recevait la fiche entière SAUF ce point-là, et le modèle comblait le silence en
répondant « une période non spécifiée dans sa description ». Il ne se trompait
pas sur les faits — il n'en avait aucun sur ce sujet, et une phrase vague est ce
qu'on écrit quand on n'a rien lu. Le fait manquant était pourtant connu du
relevé : le schéma ne porte aucune colonne de date, et c'est LA réponse.

**Jamais racontés.** C'est le défaut corrigé par ``acfd8f5`` — « décris le
dataset iris » répondu de mémoire, avec une jolie prose et zéro requête — et il
reviendrait par cette porte si la volumétrie était devinée du nom du fichier.
Une source qui ne répond pas ne rend donc aucun chiffre : elle rend la raison
pour laquelle elle n'en rend pas.

**Le relevé coûte une ouverture de connexion et quelques agrégats par source.**
Il est donc gardé en mémoire (``RelevesDuCatalogue``), et les textes qui s'en
servent disent à l'utilisateur qu'il date — un chiffre relevé plus tôt n'est pas
un chiffre faux, mais il ne doit pas passer pour frais.

**Gardé, et pas figé.** Trois bornes encadrent le relevé, toutes réglables
(``ReglagesDuReleve``, alimenté par le `.env`) :

- une **péremption** du relevé réussi, pour qu'une source qui grossit finisse
  par se redire ;
- une **reprise** bien plus courte pour une source injoignable, parce qu'une
  panne se répare en minutes et que le contraire fait mentir la réponse pendant
  toute la session — défaut observé le 2026-09-14, Postgres arrêté au démarrage
  puis relancé, et « volumétrie non relevée » encore affiché plusieurs minutes
  après son retour ;
- un **délai maximal** par source, qui dégrade la ligne en injoignable au lieu
  d'attendre. Sans lui, une source qui ne répond pas — pas une source qui
  refuse : une source **muette**, dont le TCP part et ne revient jamais — bloque
  l'inventaire entier sans plafond (mesuré : toujours bloqué au bout de 75 s).

Et une quatrième borne, sur le travail plutôt que sur le temps : quand compter
coûte réellement et que le moteur sait donner un ordre de grandeur sans lire les
lignes, une **grande** table est estimée plutôt que comptée. Ce qui est estimé
est dit tel quel. Seul Postgres est concerné, et c'est mesuré : DuckDB répond
``count(*)`` depuis ses métadonnées, l'y estimer décorerait d'un « ~ » un chiffre
exact et gratuit (cf. ``ESTIMATIONS``).
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.catalog import Catalog, Source, open_source
from data_analyst_agent.agents.retrieval.sql import DatabaseAdapter, SchemaInfo
from data_analyst_agent.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Les types SQL qui portent une date. Comparés en majuscules et par
# « commence par » : Postgres rend ``TIMESTAMP WITHOUT TIME ZONE``, DuckDB
# ``TIMESTAMP_NS``, et aucun des deux n'est un nom de type à comparer
# littéralement.
TYPES_TEMPORELS = ("DATE", "TIMESTAMP", "DATETIME")


class Periode(BaseModel):
    """La période couverte par une colonne de date, telle que lue."""

    table: str
    colonne: str
    debut: str
    fin: str


class FaitsDeSource(BaseModel):
    """Ce qu'une source dit d'elle-même quand on la lit, ou pourquoi elle s'est tue.

    ``echec`` non vide veut dire « rien n'a pu être lu » : les autres champs
    valent alors leur défaut et ne doivent pas être affichés comme des faits.
    Le distinguer d'une source réellement vide compte — « 0 ligne » est une
    information, « je n'ai pas pu ouvrir la source » en est une autre.

    ``avertissement`` est l'autre moitié : tout a été lu, et quelque chose de
    DÉCLARÉ ne tient pas. Les faits restent bons ; c'est le catalogue qui est à
    corriger.
    """

    nom: str
    lignes_par_table: dict[str, int] = Field(default_factory=dict)
    # Les tables dont le nombre de lignes est une ESTIMATION du moteur et non un
    # comptage. Gardé à part plutôt que fondu dans le chiffre : un ordre de
    # grandeur affiché comme un compte exact est un petit mensonge, et c'est
    # exactement le travers que ce module existe pour empêcher.
    estimees: list[str] = Field(default_factory=list)
    periode: Periode | None = None
    # Ce qui a été relevé de travers sans empêcher de relever — aujourd'hui, une
    # ``date_reference`` que le schéma ne porte pas. Séparé de ``echec``, qui dit
    # que rien n'a pu être lu : ici tout a été lu, et une DÉCLARATION est fausse.
    # Dit plutôt que taire : sans lui, une désignation mal orthographiée
    # retomberait sur la première colonne de date exactement comme avant la
    # correction, et ne se verrait nulle part.
    avertissement: str = ""
    # Pourquoi il n'y a PAS de période, quand il n'y en a pas — la source ne
    # porte aucune colonne de date, ou celle qu'elle porte est vide. Une absence
    # se CONSTATE : c'est la règle de tout ce module, et la période était le seul
    # des trois faits à ne pas la suivre. Elle se taisait, et le modèle comblait
    # (cf. l'en-tête). Vide = il y a une période, ou rien n'a été lu.
    sans_periode: str = ""
    echec: str = ""

    @property
    def lu(self) -> bool:
        return not self.echec

    @property
    def tables(self) -> int:
        return len(self.lignes_par_table)

    @property
    def lignes(self) -> int:
        return sum(self.lignes_par_table.values())

    def en_clair(self) -> str:
        """Une ligne de français : le volume, et la période — ou son absence.

        Vide quand rien n'a été lu **et** qu'il n'y a rien à dire ; l'échec,
        lui, se dit — silencieusement, il se confondrait avec une source vide.

        L'absence de période se dit au même titre que la période, et pour la
        même raison qu'un échec se dit : un silence n'est pas un fait, c'est un
        trou, et un trou dans une fiche se remplit par une phrase vague. Elle est
        écrite sans décoration, comme la période elle-même — ce qui suit le
        deux-points ne nomme rien qu'une réponse devrait recopier.

        Un chiffre estimé porte un ``~``, et le total aussi dès qu'une seule
        table l'est : la précision affichée doit être celle qu'on a, pas celle
        qu'on aimerait avoir.
        """
        if not self.lu:
            return f"volumétrie non relevée : {self.echec}"
        if not self.lignes_par_table:
            return "aucune table"
        detail = ", ".join(
            f"{t} : {'~' if t in self.estimees else ''}{n}"
            for t, n in self.lignes_par_table.items()
        )
        approche = "~" if self.estimees else ""
        volume = f"{self.tables} table(s), {approche}{self.lignes} ligne(s) ({detail})"
        if self.periode is not None:
            volume += (
                f" — période couverte : du {self.periode.debut} au {self.periode.fin} "
                f"(colonne {self.periode.colonne} de {self.periode.table})"
            )
        elif self.sans_periode:
            volume += f" — aucune période couverte : {self.sans_periode}"
        return f"{volume} [{self.avertissement}]" if self.avertissement else volume


@dataclass(frozen=True)
class ReglagesDuReleve:
    """Les quatre bornes du relevé. Défauts justifiés dans ``config.Settings``.

    Les durées sont en secondes ; ``0`` désactive la borne, et le dit ici plutôt
    que sur chaque site d'appel :

    - ``delai`` à 0 : aucun délai maximal, le relevé attend ce qu'il faut ;
    - ``peremption``/``reprise`` à 0 : aucune mise en cache, le relevé est refait
      à chaque demande ;
    - ``seuil_approximation`` à 0 : jamais d'estimation, tout est compté.
    """

    delai: float = 10.0
    peremption: float = 900.0
    reprise: float = 30.0
    seuil_approximation: int = 100_000

    @classmethod
    def from_settings(cls, settings: Settings) -> ReglagesDuReleve:
        return cls(
            delai=settings.releve_delai,
            peremption=settings.releve_peremption,
            reprise=settings.releve_reprise,
            seuil_approximation=settings.releve_seuil_approximation,
        )


def _reglages(reglages: ReglagesDuReleve | None) -> ReglagesDuReleve:
    """Ceux qu'on donne, ou ceux du `.env`. Un seul endroit qui tranche."""
    return reglages if reglages is not None else ReglagesDuReleve.from_settings(get_settings())


class ReleveTropLong(RuntimeError):
    """Le relevé d'une source a dépassé son délai. Dégradé, jamais propagé."""


def _sous_delai[T](travail: Callable[[], T], delai: float, nom: str) -> T:
    """``travail()``, abandonné au-delà de ``delai`` secondes. ``0`` = sans borne.

    Un fil DÉMON, et pas un ``ThreadPoolExecutor`` : ce qu'on abandonne ici,
    c'est un appel bloquant dans un pilote de base — un ``connect`` vers une
    adresse muette, qui ne rend la main qu'au délai TCP du système. L'exécuteur
    joint ses fils à la sortie de l'interpréteur (``concurrent.futures`` pose un
    ``atexit``) : le process refuserait alors de s'arrêter tant que la source
    n'a pas répondu, ce qui déplacerait le blocage sans le supprimer.

    Le fil abandonné, lui, finit sa requête en cours puis meurt : il referme
    donc sa connexion (``closing``) au lieu de la laisser au ramasse-miettes,
    et son résultat est simplement jeté.
    """
    if delai <= 0:
        return travail()
    boite: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def courir() -> None:
        try:
            boite.put((True, travail()))
        except BaseException as exc:  # repassée telle quelle au demandeur
            boite.put((False, exc))

    threading.Thread(target=courir, daemon=True, name=f"releve-{nom}").start()
    try:
        abouti, valeur = boite.get(timeout=delai)
    except queue.Empty:
        raise ReleveTropLong(f"pas de réponse en moins de {delai:g} s") from None
    if not abouti:
        raise valeur  # type: ignore[misc]
    return valeur  # type: ignore[return-value]


# Comment demander à un moteur ce qu'il sait du volume d'une table SANS la lire.
# Un moteur absent d'ici est compté, pas deviné.
#
# **DuckDB n'y est pas, et c'est une décision mesurée.** Il tient bien ce chiffre
# (``duckdb_tables().estimated_size``), mais il répond aussi ``count(*)`` depuis
# ses métadonnées : sur la base de 1,66 M de lignes, les dix comptages exacts
# pèsent 2,7 ms à eux tous, et passer par l'estimation a coûté PLUS cher
# (27,6 ms contre 13,9 ms sur une table de 5 M). Estimer y reviendrait à décorer
# d'un « ~ » un chiffre qui était exact et gratuit — dégrader sans contrepartie,
# ce que le seuil existe précisément pour éviter. Postgres, lui, balaie
# réellement : 88 ms pour 5 M de lignes contre 1,5 ms de ``reltuples``.
ESTIMATIONS = {
    "postgresql": "SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass('\"{}\"')",
}


def _estimation(adaptateur: DatabaseAdapter, table: str) -> int | None:
    """Ce que le moteur sait du volume SANS lire les lignes, ou ``None``.

    Le moteur tient ce chiffre dans son catalogue et le rend pour rien ; il ne le
    garantit pas juste. D'où l'usage qu'on en fait : seulement au-dessus d'un
    seuil, et toujours annoncé comme une estimation.

    ``None`` couvre tout ce qui n'est pas un chiffre utilisable — un moteur qui
    n'est pas dans ``ESTIMATIONS``, une table absente de son catalogue, ou un
    ``reltuples`` à -1, qui est ce que Postgres répond d'une table jamais passée
    sous ``ANALYZE``.
    """
    requete = ESTIMATIONS.get(adaptateur.dialect)
    if requete is None:
        return None
    try:
        lignes = adaptateur.run(requete.format(table.replace("'", "''")), max_rows=1).rows
    except Exception:  # l'estimation est un bonus : jamais bloquante
        return None
    if not lignes or lignes[0][0] is None:
        return None
    estimation = int(lignes[0][0])
    return estimation if estimation >= 0 else None


def _compter(adaptateur: DatabaseAdapter, table: str, seuil: int = 0) -> tuple[int, bool]:
    """Le nombre de lignes d'une table, et s'il est estimé. Identifiant cité : il vient du schéma.

    Au-dessus de ``seuil``, l'estimation du moteur est PRÉFÉRÉE au comptage :
    c'est là que le comptage exact devient un balayage complet, et c'est là que
    la différence entre 5 000 000 et 4 999 998 n'intéresse personne — on relève
    pour choisir une source, pas pour répondre. En dessous, le comptage exact
    est de toute façon gratuit, et on ne dégrade jamais gratuitement.
    """
    if seuil > 0:
        estimation = _estimation(adaptateur, table)
        if estimation is not None and estimation >= seuil:
            return estimation, True
    resultat = adaptateur.run(f'SELECT count(*) AS n FROM "{table}"', max_rows=1)
    return (int(resultat.rows[0][0]) if resultat.rows else 0), False


@dataclass(frozen=True)
class ColonneDeDate:
    """La colonne sur laquelle lire la période, et ce que la désignation a donné.

    ``avertissement`` non vide veut dire que la source DÉSIGNAIT une colonne et
    que le schéma ne la porte pas : on retombe alors sur la première, et on le
    dit. Se taire ici rendrait la correction invisible — une période lue sur
    ``date_commande`` est indiscernable, pour qui lit la phrase, selon qu'elle
    vient d'un défaut assumé ou d'un ``date_livraision`` mal orthographié.
    """

    colonne: tuple[str, str] | None
    avertissement: str = ""


def _colonnes_de_date(schema: SchemaInfo) -> list[tuple[str, str]]:
    """Toutes les colonnes de date du schéma, dans son ordre."""
    return [
        (table.name, colonne.name)
        for table in schema.tables
        for colonne in table.columns
        if colonne.type.upper().startswith(TYPES_TEMPORELS)
    ]


def _designee(candidates: list[tuple[str, str]], designation: str) -> tuple[str, str] | None:
    """Celle des colonnes de date que ``designation`` nomme, ou ``None``.

    Deux écritures acceptées, ``table.colonne`` et ``colonne`` seule : une source
    à une table n'a aucune raison de se qualifier, et une source qui porte la
    même date dans deux tables en a toutes. Insensible à la casse, comme le
    rapprochement des features — Postgres replie ses identifiants en minuscules,
    un en-tête de CSV garde sa majuscule, et la désignation est écrite à la main.
    """
    voulue = designation.strip().lower()
    for table, colonne in candidates:
        if voulue in (colonne.lower(), f"{table.lower()}.{colonne.lower()}"):
            return table, colonne
    return None


def _colonne_de_date(schema: SchemaInfo, designation: str | None = None) -> ColonneDeDate:
    """La colonne de date DÉSIGNÉE par la source, à défaut la première du schéma.

    La désignation d'abord, parce qu'elle est la seule chose qui puisse dire
    laquelle **compte** : une source qui porte une date de commande et une date
    de livraison a deux périodes également vraies, et le schéma ne dit pas
    laquelle décrit la source. Le défaut — la première, dans l'ordre du schéma —
    reste celui d'avant : il ne parie pas sur le nom des colonnes, il assume de
    ne pas choisir, et la colonne retenue est nommée dans la réponse.

    Une désignation que le schéma ne porte pas ne fait pas tomber le relevé : le
    reste des faits est bon, et un inventaire qui disparaît pour une faute de
    frappe dans un YAML serait une punition démesurée. Elle se dit.
    """
    candidates = _colonnes_de_date(schema)
    if not designation:
        return ColonneDeDate(candidates[0] if candidates else None)
    retenue = _designee(candidates, designation)
    if retenue is not None:
        return ColonneDeDate(retenue)
    connues = ", ".join(f"{t}.{c}" for t, c in candidates) or "aucune"
    return ColonneDeDate(
        candidates[0] if candidates else None,
        f"colonne de date de référence déclarée introuvable : {designation} — "
        f"colonnes de date de la source : {connues}",
    )


# Un horodatage à minuit pile, tel que le rendent DuckDB (une colonne de dates
# d'un classeur Excel) et Postgres pour un TIMESTAMP sans heure renseignée.
_MINUIT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]00:00:00(\.0+)?$")


def _sans_heure_inutile(valeur: str) -> str:
    """« 2024-01-01T00:00:00 » -> « 2024-01-01 ».

    Cosmétique, et assumé comme tel : la période part dans une phrase que
    quelqu'un lit pour choisir une source. Une heure à minuit pile n'est pas
    une information, c'est le type de la colonne qui transparaît. Une heure
    RÉELLE, elle, est gardée — elle dit quelque chose des données.
    """
    trouve = _MINUIT.match(valeur)
    return trouve.group(1) if trouve else valeur


def _periode(adaptateur: DatabaseAdapter, table: str, colonne: str) -> Periode | None:
    requete = f'SELECT min("{colonne}") AS d, max("{colonne}") AS f FROM "{table}"'
    lignes = adaptateur.run(requete, max_rows=1).rows
    if not lignes or lignes[0][0] is None or lignes[0][1] is None:
        return None  # colonne entièrement vide : il n'y a pas de période
    return Periode(
        table=table,
        colonne=colonne,
        debut=_sans_heure_inutile(str(lignes[0][0])),
        fin=_sans_heure_inutile(str(lignes[0][1])),
    )


def _pourquoi_sans_periode(colonne: tuple[str, str] | None, periode: Periode | None) -> str:
    """La raison qu'il n'y ait pas de période, ``""`` quand il y en a une.

    Deux raisons, et les distinguer n'est pas de la précision pour la précision :
    « cette source ne date rien » et « cette source date, et la colonne est
    vide » ne mènent pas à la même suite. La première clôt la question — il n'y
    a rien à aller chercher ; la seconde désigne une donnée manquante, donc
    quelque chose à corriger en amont.

    Elle est calculée ici, au relevé, et pas au moment d'écrire la phrase :
    c'est ici qu'on a le schéma sous la main, et ``en_clair`` n'a jamais rien
    déduit — elle met en français ce qui a été lu.
    """
    if periode is not None:
        return ""
    if colonne is None:
        return "la source ne porte aucune colonne de date"
    table, nom = colonne
    return f"la colonne de date {nom} de {table} ne porte aucune valeur"


def _lire(source: Source, seuil: int) -> FaitsDeSource:
    """Le relevé lui-même : ouvrir, compter, dater, refermer. Lève si ça se passe mal."""
    with closing(open_source(source)) as adaptateur:
        schema = adaptateur.schema()
        lignes: dict[str, int] = {}
        estimees: list[str] = []
        for table in schema.tables:
            lignes[table.name], estimee = _compter(adaptateur, table.name, seuil)
            if estimee:
                estimees.append(table.name)
        reperee = _colonne_de_date(schema, source.date_reference)
        periode = _periode(adaptateur, *reperee.colonne) if reperee.colonne else None
    return FaitsDeSource(
        nom=source.name,
        lignes_par_table=lignes,
        estimees=estimees,
        periode=periode,
        sans_periode=_pourquoi_sans_periode(reperee.colonne, periode),
        avertissement=reperee.avertissement,
    )


def relever(source: Source, reglages: ReglagesDuReleve | None = None) -> FaitsDeSource:
    """Ouvre la source, lit ses faits, referme. Ne lève jamais.

    Ne lève jamais parce qu'un inventaire est précisément ce qu'on demande quand
    on ne sait pas encore quoi demander : une source injoignable doit se dire
    injoignable **à côté des autres**, pas faire tomber la liste entière. Le
    détail technique part au journal ; ce qui remonte est une phrase.

    Le dépassement de délai est un échec comme un autre, et c'est voulu : pour
    qui lit l'inventaire, une source qui met trois minutes à se décrire et une
    source qui refuse la connexion sont la même chose — elles ne servent à rien
    tout de suite. Il se distingue par sa RAISON, seule chose qui aide à la
    réparer.
    """
    bornes = _reglages(reglages)
    try:
        return _sous_delai(
            lambda: _lire(source, bornes.seuil_approximation), bornes.delai, source.name
        )
    except ReleveTropLong as trop_long:
        logger.warning("relevé de la source %s abandonné : %s", source.name, trop_long)
        return FaitsDeSource(nom=source.name, echec=str(trop_long))
    except Exception as exc:
        logger.warning("relevé de la source %s impossible : %s", source.name, exc)
        return FaitsDeSource(nom=source.name, echec="la source n'a pas répondu")


class RelevesDuCatalogue:
    """Les faits de chaque source, lus au besoin et gardés le temps qu'il faut.

    **Paresseux, et pas au démarrage** : ouvrir toutes les sources à la
    construction de l'orchestrateur ferait payer le prix à qui ne pose aucune
    question d'inventaire, et ferait dépendre le démarrage du serveur de la
    disponibilité de chaque base. Le premier inventaire le paie, les suivants
    non.

    **Gardé, avec deux horloges et pas une.** Un relevé réussi vaut jusqu'à sa
    ``peremption`` ; un relevé en échec ne vaut que jusqu'à sa ``reprise``, bien
    plus courte. Les deux durées répondent à deux questions différentes — « ce
    chiffre a-t-il bougé ? » et « la panne est-elle réparée ? » — et une seule
    durée pour les deux les répond mal toutes les deux : c'est le défaut du
    2026-09-14, où « volumétrie non relevée » a survécu plusieurs minutes au
    retour de la base.

    L'horloge est injectable : un test de péremption ne doit pas coûter un quart
    d'heure d'attente. ``time.monotonic`` et pas ``time.time`` — une horloge
    qu'un réglage de date peut faire reculer ferait durer un relevé
    indéfiniment.

    Une source ajoutée au catalogue effectif (un tableau intermédiaire de
    conversation) n'est pas ici : ce n'est pas une source de données, et le
    relever coûterait une lecture à chaque tour.
    """

    def __init__(
        self,
        catalogue: Catalog,
        reglages: ReglagesDuReleve | None = None,
        horloge: Callable[[], float] = time.monotonic,
    ) -> None:
        self._catalogue = catalogue
        self._reglages = _reglages(reglages)
        self._horloge = horloge
        self._cache: dict[str, tuple[float, FaitsDeSource]] = {}

    def _encore_valable(self, nom: str) -> FaitsDeSource | None:
        """Le relevé gardé s'il est encore bon, ``None`` s'il faut le refaire."""
        garde = self._cache.get(nom)
        if garde is None:
            return None
        pose, faits = garde
        duree = self._reglages.peremption if faits.lu else self._reglages.reprise
        if duree <= 0:
            return None  # pas de cache du tout : on relit
        return faits if self._horloge() - pose < duree else None

    def de(self, nom: str) -> FaitsDeSource | None:
        """Les faits d'une source déclarée, relevés au besoin ; ``None`` si inconnue."""
        garde = self._encore_valable(nom)
        if garde is not None:
            return garde
        try:
            source = self._catalogue.get(nom)
        except KeyError:
            return None
        faits = relever(source, self._reglages)
        self._cache[nom] = (self._horloge(), faits)
        return faits

    def tous(self) -> dict[str, FaitsDeSource]:
        """Les faits de toutes les sources déclarées, dans l'ordre du catalogue."""
        return {s.name: self.de(s.name) for s in self._catalogue.sources}
