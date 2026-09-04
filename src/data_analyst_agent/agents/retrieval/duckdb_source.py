"""Requêter en SQL via DuckDB : fichiers Excel/CSV, ou base DuckDB (décision §9-②).

Excel est lu par pandas/openpyxl (une feuille = une table) puis enregistré
dans DuckDB — aucune extension DuckDB à télécharger, compatible on-prem.
Les CSV passent par ``read_csv_auto`` (natif, sans réseau).

Une base ``.duckdb`` s'ouvre en lecture seule et expose ses tables telles
qu'elles ont été déclarées : clés primaires et **étrangères** comprises. C'est
ce que le mono-fichier ne peut pas donner — un schéma en étoile dont on tait les
FK oblige le modèle à deviner les jointures.

DuckDB tourne dans le process de l'API, pas dans la sandbox : toute connexion
est donc verrouillée dès sa remise à l'adaptateur (``lock_external_access``),
sinon le SQL généré par le modèle lit n'importe quel fichier de l'hôte. Le
verrou est posé dans ``__init__``, seul point de passage commun à ``from_file``
et à ``from_database`` — une base ``.duckdb`` ouverte en lecture seule reste
une connexion capable de ``read_csv_auto`` sur l'hôte tant qu'on ne l'a pas
fermée.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

from data_analyst_agent.agents.retrieval.sql import (
    ColumnInfo,
    ForeignKeyInfo,
    QueryError,
    QueryResult,
    SchemaInfo,
    TableInfo,
    assert_read_only,
    build_result,
)


def sanitize_table_name(name: str) -> str:
    # ASCII strict : les identifiants restent utilisables sans guillemets en SQL
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    return cleaned or "table_sans_nom"


def lock_external_access(connection: duckdb.DuckDBPyConnection) -> None:
    """Coupe tout accès disque/réseau de la connexion, définitivement.

    Sans ce verrou, ``assert_read_only`` laisse passer
    ``SELECT * FROM read_csv_auto('/etc/passwd')`` : la requête *est* un
    ``SELECT``, et son résultat remonte à l'utilisateur comme un tableau
    ordinaire. Même chose avec ``read_text`` ou ``glob``.

    À poser **après** la matérialisation des données (``CREATE TABLE ... AS``,
    ``register``, ou ``connect(..., read_only=True)``) : le chargement légitime,
    lui, a besoin de l'accès disque. DuckDB refuse de rouvrir le réglage ensuite.
    """
    connection.execute("SET enable_external_access=false")


class DuckDBAdapter:
    dialect = "duckdb"

    def __init__(self, connection: duckdb.DuckDBPyConnection, table_names: list[str]) -> None:
        self.connection = connection
        self._table_names = table_names
        # garde une référence aux DataFrames enregistrés (sinon ramassés par le GC)
        self._frames: dict[str, pd.DataFrame] = {}
        # Point de passage unique de toutes les fabriques : le verrou vaut donc
        # pour n'importe quel chemin d'ouverture, présent ou à venir.
        lock_external_access(connection)

    @classmethod
    def from_file(cls, path: Path) -> DuckDBAdapter:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"fichier de données introuvable : {path}")
        connection = duckdb.connect(":memory:")
        suffix = path.suffix.lower()
        if suffix == ".csv":
            table = sanitize_table_name(path.stem)
            escaped = str(path).replace("'", "''")
            # CREATE TABLE et non CREATE VIEW : une vue relirait le fichier à
            # chaque requête, ce que le verrou d'accès externe interdit ensuite.
            connection.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto('{escaped}')")
            return cls(connection, [table])
        if suffix in (".xlsx", ".xlsm"):
            sheets = pd.read_excel(path, sheet_name=None)  # toutes les feuilles
            # Tout enregistrer avant de construire l'adaptateur, qui verrouille.
            frames: dict[str, pd.DataFrame] = {}
            for sheet_name, frame in sheets.items():
                table = sanitize_table_name(str(sheet_name))
                frames[table] = frame
                connection.register(table, frame)
            if not frames:
                raise ValueError(f"aucune feuille lisible dans {path.name}")
            adapter = cls(connection, list(frames))
            adapter._frames = frames
            return adapter
        raise ValueError(f"format non géré : {path.suffix} (attendu .csv, .xlsx, .xlsm)")

    @classmethod
    def from_database(cls, path: Path) -> DuckDBAdapter:
        """Ouvre une base DuckDB existante, en lecture seule.

        ``read_only`` n'est pas qu'une ceinture de plus par-dessus
        ``assert_read_only`` : il laisse plusieurs process ouvrir la même base.
        Sans lui, DuckDB pose un verrou exclusif et le second démarrage échoue —
        l'API et un notebook ne pourraient pas cohabiter.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"base DuckDB introuvable : {path} — construisez-la avec "
                "`python scripts/load_maxizoo_duckdb.py --export ../base_demo`."
            )
        connection = duckdb.connect(str(path), read_only=True)
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        if not tables:
            raise ValueError(f"aucune table dans {path.name}")
        return cls(connection, tables)

    # Cf. PostgresAdapter : au-delà, la colonne est du texte libre.
    MAX_DISTINCT_VALUES = 15

    def _keys(self, table: str) -> tuple[list[str], list[ForeignKeyInfo]]:
        """Clés primaire et étrangères déclarées (vides si la table n'en a pas).

        Un CSV ou une feuille Excel n'a aucune contrainte : la table ressort
        alors sans clés, ce qui est la vérité et non un défaut d'introspection.
        """
        try:
            lignes = self.connection.execute(
                "SELECT constraint_type, constraint_column_names, referenced_table, "
                "referenced_column_names FROM duckdb_constraints() "
                "WHERE table_name = ? AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')",
                [table],
            ).fetchall()
        except duckdb.Error:
            return [], []  # introspection best-effort : jamais bloquante
        primary_key: list[str] = []
        foreign_keys: list[ForeignKeyInfo] = []
        for type_contrainte, colonnes, table_ref, colonnes_ref in lignes:
            if type_contrainte == "PRIMARY KEY":
                primary_key = list(colonnes)
            elif table_ref and colonnes_ref:
                foreign_keys.append(
                    ForeignKeyInfo(
                        column=colonnes[0], ref_table=table_ref, ref_column=colonnes_ref[0]
                    )
                )
        return primary_key, foreign_keys

    def _distinct_values(self, table: str, column: str, type_sql: str) -> list[str] | None:
        """Valeurs d'une colonne texte à faible cardinalité (sinon ``None``).

        Montre au modèle les littéraux réellement présents ('setosa'…) plutôt que
        de le laisser les deviner.
        """
        if "VARCHAR" not in type_sql.upper():
            return None
        try:
            lignes = self.connection.execute(
                f'SELECT DISTINCT "{column}" FROM {table} WHERE "{column}" IS NOT NULL LIMIT ?',
                [self.MAX_DISTINCT_VALUES + 1],
            ).fetchall()
        except duckdb.Error:
            return None  # introspection best-effort : jamais bloquante
        if len(lignes) > self.MAX_DISTINCT_VALUES:
            return None
        return sorted(str(ligne[0]) for ligne in lignes)

    def schema(self) -> SchemaInfo:
        tables = []
        for name in self._table_names:
            described = self.connection.execute(f"DESCRIBE {name}").fetchall()
            columns = [
                ColumnInfo(
                    name=row[0],
                    type=row[1],
                    nullable=(row[2] != "NO"),
                    values=self._distinct_values(name, row[0], row[1]),
                )
                for row in described
            ]
            primary_key, foreign_keys = self._keys(name)
            tables.append(
                TableInfo(
                    name=name,
                    columns=columns,
                    primary_key=primary_key,
                    foreign_keys=foreign_keys,
                )
            )
        return SchemaInfo(dialect=self.dialect, tables=tables)

    def close(self) -> None:
        """Ferme la base en mémoire et lâche les DataFrames enregistrés.

        Une base DuckDB en mémoire pèse le poids des données chargées — un
        classeur Excel entier, feuille par feuille. Tant que la connexion vit,
        cette mémoire est retenue. ``close()`` est idempotent côté DuckDB.
        """
        self.connection.close()
        self._frames.clear()

    def run(self, query: str, max_rows: int = 200) -> QueryResult:
        safe_query = assert_read_only(query)
        try:
            cursor = self.connection.execute(safe_query)
            columns = [d[0] for d in cursor.description]
            raw_rows = cursor.fetchmany(max_rows + 1)
        except duckdb.Error as exc:
            raise QueryError(str(exc)) from exc
        return build_result(columns, [list(r) for r in raw_rows], max_rows)
