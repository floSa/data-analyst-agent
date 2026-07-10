"""Ontologie et exécution SQL en lecture seule — socle text-to-SQL maison (§9-①).

Fournit les modèles d'introspection (tables, colonnes, clés étrangères), le
garde-fou lecture seule, et l'adaptateur Postgres (SQLAlchemy + pg8000, BSD —
psycopg est LGPL donc écarté, cf. règle licences).
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


class QueryError(RuntimeError):
    """Erreur SQL « métier » : renvoyée au LLM pour qu'il se corrige."""


# --- ontologie ---------------------------------------------------------------


class ForeignKeyInfo(BaseModel):
    column: str
    ref_table: str
    ref_column: str


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool = True


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)

    def to_ddl(self) -> str:
        """Description compacte, façon DDL, pour le prompt du LLM."""
        lines = [f"TABLE {self.name} ("]
        for col in self.columns:
            null = "" if col.nullable else " NOT NULL"
            pk = " PRIMARY KEY" if [col.name] == self.primary_key else ""
            lines.append(f"  {col.name} {col.type}{null}{pk},")
        for fk in self.foreign_keys:
            lines.append(f"  FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column}),")
        lines[-1] = lines[-1].rstrip(",")
        lines.append(")")
        return "\n".join(lines)


class SchemaInfo(BaseModel):
    dialect: str = "postgresql"
    tables: list[TableInfo] = Field(default_factory=list)

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def to_prompt(self) -> str:
        return "\n\n".join(t.to_ddl() for t in self.tables) or "(aucune table)"


# --- résultats ---------------------------------------------------------------


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_markdown(self, max_rows: int = 20) -> str:
        """Rendu texte compact (pour le LLM et l'affichage)."""
        if not self.columns:
            return "(résultat vide)"
        shown = self.rows[:max_rows]
        head = "| " + " | ".join(self.columns) + " |"
        sep = "| " + " | ".join("---" for _ in self.columns) + " |"
        body = ["| " + " | ".join(str(v) for v in row) + " |" for row in shown]
        suffix = []
        if len(self.rows) > max_rows or self.truncated:
            suffix.append(
                f"... ({self.row_count} lignes affichables{', tronqué' if self.truncated else ''})"
            )
        return "\n".join([head, sep, *body, *suffix])


def normalize_value(value: Any) -> Any:
    """Rend une valeur SQL sérialisable (JSON/pydantic) sans surprise."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None  # NaN/inf casseraient le JSON strict côté client
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return str(value)


def build_result(columns: list[str], raw_rows: list, max_rows: int) -> QueryResult:
    truncated = len(raw_rows) > max_rows
    rows = [[normalize_value(v) for v in row] for row in raw_rows[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)


# --- garde-fou lecture seule ---------------------------------------------------

_WRITE_KEYWORDS_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|into|vacuum|call)\b",
    re.IGNORECASE,
)


def assert_read_only(query: str) -> str:
    """Refuse tout ce qui n'est pas une unique requête SELECT/WITH de lecture."""
    stripped = query.strip().rstrip(";").strip()
    if not stripped:
        raise QueryError("requête vide")
    if ";" in stripped:
        raise QueryError("une seule instruction SQL à la fois")
    first_word = stripped.split(None, 1)[0].lower()
    if first_word not in ("select", "with"):
        raise QueryError("seules les requêtes SELECT (ou WITH ... SELECT) sont autorisées")
    match = _WRITE_KEYWORDS_RE.search(stripped)
    if match:
        raise QueryError(f"mot-clé interdit en lecture seule : {match.group(0).upper()}")
    return stripped


# --- adaptateurs ---------------------------------------------------------------


class DatabaseAdapter(Protocol):
    """Contrat commun Postgres / DuckDB : ontologie + exécution lecture seule."""

    dialect: str

    def schema(self) -> SchemaInfo: ...

    def run(self, query: str, max_rows: int = 200) -> QueryResult: ...


class PostgresAdapter:
    dialect = "postgresql"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresAdapter:
        return cls(create_engine(dsn))

    def schema(self) -> SchemaInfo:
        inspector = inspect(self.engine)
        tables = []
        for name in sorted(inspector.get_table_names()):
            columns = [
                ColumnInfo(
                    name=col["name"],
                    type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                )
                for col in inspector.get_columns(name)
            ]
            pk = inspector.get_pk_constraint(name).get("constrained_columns") or []
            fks = [
                ForeignKeyInfo(
                    column=fk["constrained_columns"][0],
                    ref_table=fk["referred_table"],
                    ref_column=fk["referred_columns"][0],
                )
                for fk in inspector.get_foreign_keys(name)
                if fk.get("constrained_columns")
            ]
            tables.append(TableInfo(name=name, columns=columns, primary_key=pk, foreign_keys=fks))
        return SchemaInfo(dialect=self.dialect, tables=tables)

    def run(self, query: str, max_rows: int = 200) -> QueryResult:
        safe_query = assert_read_only(query)
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(safe_query))
                columns = list(result.keys())
                raw_rows = result.fetchmany(max_rows + 1)
        except SQLAlchemyError as exc:
            raise QueryError(str(exc.__cause__ or exc)) from exc
        return build_result(columns, [list(r) for r in raw_rows], max_rows)
