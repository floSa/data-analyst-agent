"""Croiser DEUX sources déclarées dans une même requête SQL.

`Plan.source` porte UN nom, et une question qui en croise deux n'avait donc
aucun chemin : elle ressortait en demande de précision. L'information ne
manquait pourtant pas — le planificateur écrit `source='ventes, production'`,
les deux noms empaquetés dans le champ qui en attend un, et le code les jetait
(`_match_source_name` rend `None` dès qu'il en trouve deux).

Ce module rend le périmètre interrogeable : les tables des sources désignées
sont matérialisées dans UNE connexion DuckDB, préfixées par le nom de leur
source, et l'agent SQL existant les voit comme un schéma unique. Il garde ses
trois outils, sa correction d'erreur et son dictionnaire : rien n'est ajouté au
prompt, et l'empreinte des sept prompts ne bouge pas.

LE PÉRIMÈTRE EST CE QUE LE TOUR DÉSIGNE, JAMAIS LE CATALOGUE. On monte les
sources que le plan nomme, et elles seules. Exposer tout le catalogue serait le
défaut que C51 a mesuré dans le bac à sable — deux fichiers montés quand un seul
est visé, le mauvais choisi 10 fois sur 10, un chiffre faux et plausible, donc
invisible. Une question qui ne vise qu'une source ne passe pas par ici.

Le préfixe est ce qui rend le croisement LISIBLE : `ventes_produits` et
`production_ordres_fabrication` portent dans leur nom la source dont elles
sortent. Deux sources qui déclarent toutes deux une table `produits` ne se
recouvrent pas, et la réponse peut dire d'où vient chaque colonne.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from data_analyst_agent.agents.retrieval.catalog import Source, open_source
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import (
    ForeignKeyInfo,
    QueryResult,
    SchemaInfo,
    TableInfo,
)


def prefixer(schema: SchemaInfo, prefixe: str) -> list[TableInfo]:
    """Les tables d'une source, renommées ``{prefixe}_{table}`` — CLÉS COMPRISES.

    Les clés étrangères sont renommées elles aussi, et c'est tout l'objet de
    cette fonction. Une FK qui pointe encore vers `produits` alors que la table
    s'appelle désormais `ventes_produits` désigne une table qui n'existe pas :
    le modèle la lit, ne la trouve pas, et devine la jointure.

    **Le défaut qu'on répare ici est mesuré.** La première version matérialisait
    par ``CREATE TABLE ... AS SELECT``, qui ne recopie AUCUNE contrainte : le
    croisement arrivait au modèle sans une seule clé étrangère, y compris celles
    INTERNES à chaque source, qu'il avait gratuitement avant. Les trois
    croisements qui aboutissaient rendaient alors trois jointures inventées —
    un produit cartésien (« 20 557 vendues, 180 669 produites » au lieu de 1 828
    et 4 413), une jointure sur `produit_id`, clé interne à `ventes` (« aucun
    produit trouvé à la fois dans les ventes et dans la production »), et un
    « quantité produite : 0 pour chaque produit ». Aucune n'a levé d'erreur.

    C'est la propriété que `duckdb_excel` revendique pour une base DuckDB — « un
    schéma en étoile dont on tait les FK oblige le modèle à deviner les
    jointures » — et que le croisement lui retirait en silence.
    """
    connues = {t.name for t in schema.tables}
    tables = []
    for table in schema.tables:
        cles = [
            fk.model_copy(update={"ref_table": f"{prefixe}_{fk.ref_table}"})
            if fk.ref_table in connues
            else fk
            for fk in table.foreign_keys
        ]
        tables.append(
            table.model_copy(update={"name": f"{prefixe}_{table.name}", "foreign_keys": cles})
        )
    return tables


def _une_cle_naturelle(connection, table: str, colonne: str) -> bool:
    """La colonne IDENTIFIE-T-ELLE une ligne de cette table ? — sans NULL, sans doublon.

    Le côté RÉFÉRENCÉ d'une clé étrangère doit être unique : c'est ce qui fait la
    différence entre une jointure et une multiplication. Vérifié dans les données
    matérialisées, pas déduit d'un nom.
    """
    ligne = connection.execute(
        f'SELECT count(*), count("{colonne}"), count(DISTINCT "{colonne}") FROM "{table}"'
    ).fetchone()
    lignes, renseignees, distinctes = ligne
    return lignes > 0 and renseignees == lignes == distinctes


def _valeurs_incluses(connection, table: str, colonne: str, vers: str, vers_colonne: str) -> bool:
    """TOUTES les valeurs de la colonne se retrouvent-elles en face ?

    L'inclusion est ce qui sépare une vraie clé d'une homonymie. `libelle` existe
    dans `ventes_produits` (unique, douze libellés de produits) comme dans
    `production_ateliers` — et « Assemblage final » n'est pas un produit : la
    coïncidence de nom ne survit pas à l'inclusion.

    Une colonne entièrement vide n'inclut rien de significatif et ne fonde
    aucune clé : on l'écarte plutôt que de la déclarer sur le vide.
    """
    gauche = f'SELECT DISTINCT "{colonne}" AS v FROM "{table}" WHERE "{colonne}" IS NOT NULL'
    droite = f'SELECT DISTINCT "{vers_colonne}" AS v FROM "{vers}"'
    manquantes, presentes = connection.execute(
        f"SELECT count(*) FILTER (WHERE d.v IS NULL), count(*) "
        f"FROM ({gauche}) s LEFT JOIN ({droite}) d ON d.v = s.v"
    ).fetchone()
    return presentes > 0 and manquantes == 0


def relier_les_sources(
    connection, decrites: list[TableInfo], sources: list[str]
) -> list[TableInfo]:
    """Les clés qui traversent le périmètre, DÉCLARÉES — et prouvées dans les données.

    **Ce que le croisement taisait, et ce que ça coûtait.** ``prefixer`` a rendu
    au modèle les clés INTERNES de chaque source. Restait la seule qui compte
    dans un croisement : celle qui relie les deux. Aucun schéma ne la porte —
    deux sources séparées ne déclarent pas de contrainte l'une vers l'autre — et
    le modèle la devine. Mesuré le 2026-09-22, catalogue métier : « compare la
    production et les ventes du VEL-04 » rend « 27 626 unités produites, 2 751
    vendues » là où les oracles disent 727 et 125. Un produit cartésien, qui ne
    lève aucune erreur et rend une phrase parfaitement lisible.

    C'est la propriété que ``duckdb_excel`` revendique — « un schéma en étoile
    dont on tait les FK oblige le modèle à deviner les jointures » — et le
    croisement la taisait là où elle manquait le plus.

    **Trois conditions, et les trois se lisent dans les DONNÉES.** Rien ici ne
    repose sur un nom de colonne, sur une convention ou sur une liste écrite à
    la main :

    1. la colonne porte le MÊME nom des deux côtés — c'est ce qui la rend
       candidate, et rien de plus ;
    2. elle est une clé naturelle d'UN côté, et d'un seul — sans NULL, sans
       doublon. Deux côtés uniques, ou aucun, et l'on ne sait pas qui référence
       qui : on se tait plutôt que de choisir ;
    3. toutes les valeurs de l'autre côté se retrouvent en face. C'est ce qui
       sépare une clé d'une homonymie.

    **Jamais à l'intérieur d'une source.** Ses clés sont déjà déclarées, et en
    ajouter une inventée là où le schéma s'est tu serait contredire la source.

    Rend les tables avec les clés ajoutées, dans l'ordre reçu.
    """
    # Où chaque nom de colonne apparaît, avec la source qui la porte. Le préfixe
    # EST le nom de la source : c'est `ouvrir_le_croisement` qui l'a posé.
    porteuses: dict[str, list[tuple[str, str]]] = {}
    for table in decrites:
        source = next((s for s in sources if table.name.startswith(f"{s}_")), "")
        for colonne in table.columns:
            porteuses.setdefault(colonne.name, []).append((source, table.name))

    ajouts: dict[str, list[ForeignKeyInfo]] = {}
    for colonne, lieux in porteuses.items():
        if len({source for source, _ in lieux}) < 2:
            continue  # la colonne ne traverse pas le périmètre
        uniques = [
            (source, table)
            for source, table in lieux
            if _une_cle_naturelle(connection, table, colonne)
        ]
        if len(uniques) != 1:
            continue  # personne ne peut être référencé, ou plusieurs le peuvent
        (source_cible, cible) = uniques[0]
        for source, table in lieux:
            if source == source_cible or table == cible:
                continue
            if _valeurs_incluses(connection, table, colonne, cible, colonne):
                ajouts.setdefault(table, []).append(
                    ForeignKeyInfo(column=colonne, ref_table=cible, ref_column=colonne)
                )
    if not ajouts:
        return decrites
    return [
        t.model_copy(update={"foreign_keys": [*t.foreign_keys, *ajouts[t.name]]})
        if t.name in ajouts
        else t
        for t in decrites
    ]


class AdaptateurCroise:
    """Le SQL s'exécute sur les copies ; le SCHÉMA est celui des sources d'origine.

    Deux choses séparées, parce qu'elles le sont : les données viennent de tables
    matérialisées sans contraintes, et les clés viennent des sources, qui les
    déclarent. Les recréer pour de vrai dans DuckDB obligerait à ordonner les
    créations selon les dépendances et n'apporterait rien — on ne fait ici que
    des ``SELECT``, et une clé étrangère ne sert qu'à être LUE par le modèle.
    """

    dialect = "duckdb"

    def __init__(self, adapter: DuckDBAdapter, tables: list[TableInfo]) -> None:
        self._adapter = adapter
        self._tables = tables

    def schema(self) -> SchemaInfo:
        return SchemaInfo(dialect=self.dialect, tables=self._tables)

    def run(self, query: str, max_rows: int = 200) -> QueryResult:
        return self._adapter.run(query, max_rows=max_rows)

    def close(self) -> None:
        self._adapter.close()


@dataclass
class Croisement:
    """Un adaptateur SQL sur plusieurs sources, et ce qu'il a fallu couper.

    ``tronquees`` est la liste des tables matérialisées AMPUTÉES. Elle n'est pas
    décorative : une jointure sur une table coupée rend un résultat
    parfaitement lisible où rien ne dit qu'il manque des lignes, et c'est le
    seul chemin d'ici qui produit un chiffre FAUX au lieu d'une erreur. Même
    raison, même mot que ``_avis_de_troncature`` pour l'analyse.
    """

    adapter: AdaptateurCroise
    noms: list[str]
    tronquees: list[str] = field(default_factory=list)

    def close(self) -> None:
        self.adapter.close()


def ouvrir_le_croisement(sources: list[Source], *, max_rows: int) -> Croisement:
    """Matérialise les tables des ``sources`` dans une connexion DuckDB unique.

    Chaque table est lue par l'adaptateur natif de SA source — Postgres reste
    Postgres, une base DuckDB reste ouverte en lecture seule, un classeur Excel
    passe par pandas — puis recopiée sous ``{source}_{table}``. C'est la
    matérialisation que le nœud d'analyse pratique déjà (``_decor_de_donnees``),
    à ceci près qu'elle vise une connexion SQL plutôt qu'un dossier de CSV.

    Les sources sont refermées au fur et à mesure : le croisement tourne ensuite
    sur les copies, il n'a plus rien à leur demander, et garder cinq connexions
    ouvertes le temps d'une requête remplirait le ``max_connections`` du serveur
    (même raison que le ``closing`` du nœud de récupération).

    Le verrou d'accès externe est posé par ``DuckDBAdapter.__init__``, donc
    APRÈS le chargement : la connexion est peuplée tant qu'elle a le droit de
    lire le disque, et ne l'a plus quand le SQL du modèle l'atteint.
    """
    if len(sources) < 2:
        raise ValueError("un croisement porte sur au moins deux sources")
    connection = duckdb.connect(":memory:")
    tables: list[str] = []
    decrites: list[TableInfo] = []
    tronquees: list[str] = []
    for source in sources:
        with closing(open_source(source)) as adapter:
            schema = adapter.schema()
            decrites.extend(prefixer(schema, source.name))
            for table in schema.tables:
                resultat = adapter.run(f"SELECT * FROM {table.name}", max_rows=max_rows)
                cible = f"{source.name}_{table.name}"
                cadre = pd.DataFrame(resultat.rows, columns=resultat.columns)
                # `CREATE TABLE ... AS SELECT` et non `register` : le DataFrame
                # est relâché à la sortie de la boucle, et une vue enregistrée
                # dessus rendrait une connexion qui lit un objet mort.
                connection.register("_a_charger", cadre)
                connection.execute(f'CREATE TABLE "{cible}" AS SELECT * FROM _a_charger')
                connection.unregister("_a_charger")
                tables.append(cible)
                if resultat.truncated:
                    tronquees.append(cible)
    # APRÈS le chargement et AVANT le verrou d'accès externe : relier demande de
    # LIRE les copies, et `DuckDBAdapter.__init__` ferme la connexion au disque.
    decrites = relier_les_sources(connection, decrites, [s.name for s in sources])
    return Croisement(
        adapter=AdaptateurCroise(DuckDBAdapter(connection, tables), decrites),
        noms=[s.name for s in sources],
        tronquees=tronquees,
    )


def dictionnaire_du_croisement(sources: list[Source]) -> str | None:
    """Les dictionnaires des sources du périmètre, chacun sous le nom de sa source.

    ``None`` quand aucune n'en déclare — le prompt est alors, au caractère près,
    celui d'avant.

    Le titre n'est pas décoratif : il porte le PRÉFIXE des tables. Le
    dictionnaire de `ventes` parle de `produits` et de `commandes` ; dans un
    croisement ces tables s'appellent `ventes_produits` et `ventes_commandes`,
    et un dictionnaire qui nomme des tables inexistantes se fait ignorer au
    moment précis où il porte le piège. C'est un fait du périmètre monté, pas
    une consigne de comportement : les prompts et les fiches d'outils ne
    bougent pas.

    Les règles des DEUX dictionnaires doivent s'appliquer. Le piège des
    commandes annulées (`statut <> 'ANN'`) vaut dans un croisement comme
    ailleurs : un chiffre d'affaires croisé qui les compte est faux, et il est
    faux en silence.
    """
    morceaux = []
    for source in sources:
        texte = source.dictionary_text()
        if not texte:
            continue
        morceaux.append(
            f"# Source `{source.name}` — ses tables sont préfixées `{source.name}_` "
            f"dans ce périmètre\n\n{texte}"
        )
    return "\n\n---\n\n".join(morceaux) if morceaux else None
