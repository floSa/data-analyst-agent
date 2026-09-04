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
from sqlalchemy import String as SQLString
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
    # Valeurs possibles, pour les colonnes texte à faible cardinalité seulement.
    # Un modèle ne peut pas filtrer sur des valeurs qu'il n'a jamais vues : sans
    # cette liste il devine, et il devine dans SA langue — observé en vrai, un
    # « WHERE label LIKE '%First%' » sur des libellés « 1re classe », qui ne
    # ramène rien. Les montrer coûte une requête DISTINCT et supprime le doute.
    values: list[str] | None = None


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)

    def to_ddl(self) -> str:
        """Description compacte, façon DDL, pour le prompt du LLM.

        Les colonnes à faible cardinalité exposent leurs valeurs en commentaire :
        c'est ce qui évite au modèle d'inventer un littéral plausible mais absent.
        """
        declarations = []
        commentaires = []
        for col in self.columns:
            null = "" if col.nullable else " NOT NULL"
            pk = " PRIMARY KEY" if [col.name] == self.primary_key else ""
            declarations.append(f"  {col.name} {col.type}{null}{pk}")
            commentaires.append(
                f"  -- valeurs : {', '.join(repr(v) for v in col.values)}" if col.values else ""
            )
        for fk in self.foreign_keys:
            declarations.append(
                f"  FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column})"
            )
            commentaires.append("")
        corps = [
            decl + ("," if i < len(declarations) - 1 else "") + commentaire
            for i, (decl, commentaire) in enumerate(zip(declarations, commentaires, strict=True))
        ]
        return "\n".join([f"TABLE {self.name} (", *corps, ")"])


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


def mask_literals(query: str) -> str:
    """Blanchit ce qui n'est PAS du SQL exécutable, en gardant les positions.

    Le garde-fou raisonnait sur le texte brut : il refusait
    ``SELECT * FROM t WHERE nom = 'a;b'`` — un point-virgule, oui, mais dans un
    littéral, donc une seule instruction (audit §5.1). Toute question portant
    sur une valeur qui en contient était bloquée, et le modèle rebouclait
    jusqu'à épuiser ``retrieval_request_limit`` sans jamais comprendre pourquoi.
    Même mécanique pour un mot-clé d'écriture qui n'est qu'une donnée
    (``WHERE action = 'DELETE'``).

    Trois formes de contenu non exécutable sont neutralisées : le littéral
    (``'…'``), l'identifiant cité (``"…"``) et le commentaire (``--`` jusqu'à la
    fin de ligne, ``/* … */``). Une marque de citation s'échappe en se
    **doublant** (``''``, ``""``), comme le veut la norme.

    Les caractères masqués deviennent des espaces (les sauts de ligne restent
    des sauts de ligne) : la chaîne rendue a la même longueur et la même
    découpe en lignes que l'originale — elle sert à DÉCIDER, jamais à exécuter.

    Deux choix conservateurs, qui font pencher les cas douteux du côté du refus
    plutôt que du côté du passage :

    - l'antislash n'échappe rien. Sous ``standard_conforming_strings`` (défaut
      de Postgres depuis 9.1) et sous DuckDB, c'est le comportement du moteur ;
      dans le cas contraire (chaîne ``E'…'``), le littéral est refermé trop
      tôt et ce qui suit est examiné comme du SQL — donc refusé s'il porte un
      point-virgule ;
    - le littéral et le commentaire non refermés courent jusqu'à la fin, ce qui
      masque tout ce qui suit. La requête est alors syntaxiquement invalide :
      le moteur la rejette sans rien exécuter.

    Le ``$$…$$`` de Postgres n'est pas reconnu : un point-virgule qui s'y niche
    reste refusé à tort, comme avant ce correctif.
    """
    masque: list[str] = []
    position = 0
    fin_de_chaine = len(query)
    while position < fin_de_chaine:
        caractere = query[position]
        if caractere in ("'", '"'):
            debut = position
            position += 1
            while position < fin_de_chaine:
                if query[position] == caractere:
                    # marque doublée : elle s'échappe elle-même, on continue
                    if query[position + 1 : position + 2] == caractere:
                        position += 2
                        continue
                    position += 1
                    break
                position += 1
            masque.append(_blanchir(query[debut:position]))
            continue
        if query.startswith("--", position):
            saut = query.find("\n", position)
            fin = fin_de_chaine if saut == -1 else saut
            masque.append(_blanchir(query[position:fin]))
            position = fin
            continue
        if query.startswith("/*", position):
            ferme = query.find("*/", position + 2)
            fin = fin_de_chaine if ferme == -1 else ferme + 2
            masque.append(_blanchir(query[position:fin]))
            position = fin
            continue
        masque.append(caractere)
        position += 1
    return "".join(masque)


def _blanchir(fragment: str) -> str:
    """Le fragment, tous caractères remplacés par des espaces sauf les sauts de ligne."""
    return "".join("\n" if c == "\n" else " " for c in fragment)


def assert_read_only(query: str) -> str:
    """Refuse tout ce qui n'est pas une unique requête SELECT/WITH de lecture.

    Les vérifications portent sur la requête **masquée** (cf.
    ``mask_literals``) : ce qui vit dans un littéral, un identifiant cité ou un
    commentaire est une donnée, pas une instruction. La requête rendue, elle,
    est l'originale intacte — c'est elle qui part au moteur.
    """
    stripped = query.strip().rstrip(";").strip()
    if not stripped:
        raise QueryError("requête vide")
    masque = mask_literals(stripped)
    if not masque.strip():
        raise QueryError("requête vide")  # rien qu'un commentaire
    if ";" in masque:
        raise QueryError("une seule instruction SQL à la fois")
    first_word = masque.split(None, 1)[0].lower()
    if first_word not in ("select", "with"):
        raise QueryError("seules les requêtes SELECT (ou WITH ... SELECT) sont autorisées")
    match = _WRITE_KEYWORDS_RE.search(masque)
    if match:
        raise QueryError(f"mot-clé interdit en lecture seule : {match.group(0).upper()}")
    return stripped


# --- adaptateurs ---------------------------------------------------------------


class DatabaseAdapter(Protocol):
    """Contrat commun Postgres / DuckDB : ontologie, exécution lecture seule, fermeture.

    ``close()`` fait partie du contrat, et pas d'un raffinement optionnel : un
    adaptateur détient une ressource système — un pool de connexions Postgres,
    une base DuckDB en mémoire — que le ramasse-miettes ne rend pas à temps.
    ``open_source()`` est appelée à chaque exécution de nœud ; sans fermeture,
    chaque question laisse derrière elle un pool de plus, jusqu'à remplir le
    ``max_connections`` du serveur (audit §2.3). S'utilise sous
    ``contextlib.closing``.
    """

    dialect: str

    def schema(self) -> SchemaInfo: ...

    def run(self, query: str, max_rows: int = 200) -> QueryResult: ...

    def close(self) -> None: ...


class PostgresAdapter:
    dialect = "postgresql"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Au-delà, la colonne est un identifiant ou du texte libre (un nom de
    # passager…) : la lister n'aide pas le modèle et alourdit le prompt.
    MAX_DISTINCT_VALUES = 15

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresAdapter:
        return cls(create_engine(dsn))

    def _distinct_values(self, table: str, column: dict) -> list[str] | None:
        """Valeurs d'une colonne texte à faible cardinalité (sinon ``None``).

        Sert à montrer au modèle les littéraux réellement présents ('1re classe',
        'S'…) au lieu de le laisser les deviner.
        """
        if not isinstance(column["type"], SQLString):
            return None
        requete = text(
            f'SELECT DISTINCT "{column["name"]}" FROM "{table}" '
            f'WHERE "{column["name"]}" IS NOT NULL LIMIT :limite'
        )
        try:
            with self.engine.connect() as connection:
                lignes = connection.execute(
                    requete, {"limite": self.MAX_DISTINCT_VALUES + 1}
                ).fetchall()
        except SQLAlchemyError:
            return None  # introspection best-effort : jamais bloquante
        if len(lignes) > self.MAX_DISTINCT_VALUES:
            return None
        return sorted(str(ligne[0]) for ligne in lignes)

    def schema(self) -> SchemaInfo:
        inspector = inspect(self.engine)
        tables = []
        for name in sorted(inspector.get_table_names()):
            columns = [
                ColumnInfo(
                    name=col["name"],
                    type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                    values=self._distinct_values(name, col),
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

    def close(self) -> None:
        """Rend au serveur les connexions du pool, tout de suite.

        Un ``Engine`` abandonné garde ses connexions ouvertes jusqu'à ce que le
        ramasse-miettes veuille bien le voir. ``dispose()`` est idempotent : une
        double fermeture ne lève pas.
        """
        self.engine.dispose()
