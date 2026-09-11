"""Ce qu'on LIT dans une source pour la décrire : tables, lignes, période.

Le catalogue déclare un nom, un type et une description écrite à la main. C'est
ce qu'il faut pour router, et c'est trop peu pour **choisir** : « employes » et
« titanic » se ressemblent sur trois lignes de YAML, et rien n'y dit lequel des
deux couvre l'année qu'on cherche ni lequel pèse trois cents lignes. D'où ce
module : trois faits par source, tous **lus dans la source elle-même**.

- le **nombre de tables**, du schéma ;
- le **nombre de lignes**, d'un ``count(*)`` par table ;
- la **période couverte**, d'un ``min``/``max`` sur la première colonne de date
  rencontrée — et rien du tout s'il n'y en a aucune.

**Jamais racontés.** C'est le défaut corrigé par ``acfd8f5`` — « décris le
dataset iris » répondu de mémoire, avec une jolie prose et zéro requête — et il
reviendrait par cette porte si la volumétrie était devinée du nom du fichier.
Une source qui ne répond pas ne rend donc aucun chiffre : elle rend la raison
pour laquelle elle n'en rend pas.

**Le relevé coûte une ouverture de connexion et quelques agrégats par source.**
Il est donc fait UNE FOIS par source et gardé en mémoire pour la vie du
processus (``RelevesDuCatalogue``), et les textes qui s'en servent le disent à
l'utilisateur — un chiffre qui date du démarrage n'est pas un chiffre faux, mais
il ne doit pas passer pour frais.
"""

from __future__ import annotations

import logging
import re
from contextlib import closing

from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.catalog import Catalog, Source, open_source
from data_analyst_agent.agents.retrieval.sql import DatabaseAdapter, SchemaInfo

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
    """

    nom: str
    lignes_par_table: dict[str, int] = Field(default_factory=dict)
    periode: Periode | None = None
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
        """Une ligne de français : le volume, et la période s'il y en a une.

        Vide quand rien n'a été lu **et** qu'il n'y a rien à dire ; l'échec,
        lui, se dit — silencieusement, il se confondrait avec une source vide.
        """
        if not self.lu:
            return f"volumétrie non relevée : {self.echec}"
        if not self.lignes_par_table:
            return "aucune table"
        detail = ", ".join(f"{t} : {n}" for t, n in self.lignes_par_table.items())
        volume = f"{self.tables} table(s), {self.lignes} ligne(s) ({detail})"
        if self.periode is None:
            return volume
        return (
            f"{volume} — période couverte : du {self.periode.debut} au {self.periode.fin} "
            f"(colonne {self.periode.colonne} de {self.periode.table})"
        )


def _compter(adaptateur: DatabaseAdapter, table: str) -> int:
    """Le nombre de lignes d'une table. Un identifiant cité : il vient du schéma."""
    resultat = adaptateur.run(f'SELECT count(*) AS n FROM "{table}"', max_rows=1)
    return int(resultat.rows[0][0]) if resultat.rows else 0


def _colonne_de_date(schema: SchemaInfo) -> tuple[str, str] | None:
    """La première colonne de date du schéma — (table, colonne) — ou ``None``.

    La **première**, dans l'ordre du schéma, et pas une élue par un pari sur son
    nom : une source qui en porte plusieurs verra la période de l'une d'elles,
    nommée dans la réponse, ce qui vaut mieux qu'un choix silencieux entre
    ``created_at`` et ``closed_at``.
    """
    for table in schema.tables:
        for colonne in table.columns:
            if colonne.type.upper().startswith(TYPES_TEMPORELS):
                return table.name, colonne.name
    return None


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


def relever(source: Source) -> FaitsDeSource:
    """Ouvre la source, lit ses faits, referme. Ne lève jamais.

    Ne lève jamais parce qu'un inventaire est précisément ce qu'on demande quand
    on ne sait pas encore quoi demander : une source injoignable doit se dire
    injoignable **à côté des autres**, pas faire tomber la liste entière. Le
    détail technique part au journal ; ce qui remonte est une phrase.
    """
    try:
        with closing(open_source(source)) as adaptateur:
            schema = adaptateur.schema()
            lignes = {table.name: _compter(adaptateur, table.name) for table in schema.tables}
            reperee = _colonne_de_date(schema)
            periode = _periode(adaptateur, *reperee) if reperee else None
        return FaitsDeSource(nom=source.name, lignes_par_table=lignes, periode=periode)
    except Exception as exc:
        logger.warning("relevé de la source %s impossible : %s", source.name, exc)
        return FaitsDeSource(nom=source.name, echec="la source n'a pas répondu")


class RelevesDuCatalogue:
    """Les faits de chaque source, lus une fois et gardés.

    **Paresseux, et pas au démarrage** : ouvrir toutes les sources à la
    construction de l'orchestrateur ferait payer le prix à qui ne pose aucune
    question d'inventaire, et ferait dépendre le démarrage du serveur de la
    disponibilité de chaque base. Le premier inventaire le paie, les suivants
    non.

    **Jamais réévalué** ensuite : un chiffre relevé au premier inventaire reste
    celui-là jusqu'au redémarrage. C'est le compromis assumé du cache, et c'est
    pour ça que les textes qui l'affichent disent quand il a été lu.

    Une source ajoutée au catalogue effectif (un tableau intermédiaire de
    conversation) n'est pas ici : ce n'est pas une source de données, et le
    relever coûterait une lecture à chaque tour.
    """

    def __init__(self, catalogue: Catalog) -> None:
        self._catalogue = catalogue
        self._cache: dict[str, FaitsDeSource] = {}

    def de(self, nom: str) -> FaitsDeSource | None:
        """Les faits d'une source déclarée, relevés au besoin ; ``None`` si inconnue."""
        if nom not in self._cache:
            try:
                source = self._catalogue.get(nom)
            except KeyError:
                return None
            self._cache[nom] = relever(source)
        return self._cache[nom]

    def tous(self) -> dict[str, FaitsDeSource]:
        """Les faits de toutes les sources déclarées, dans l'ordre du catalogue."""
        return {s.name: self.de(s.name) for s in self._catalogue.sources}
