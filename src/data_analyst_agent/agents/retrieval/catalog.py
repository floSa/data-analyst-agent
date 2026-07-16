"""Catalogue déclaratif des sources de données (YAML) — CADRAGE §7-①.

Une source est une base Postgres (DSN, variables d'environnement autorisées),
une base DuckDB, ou un fichier Excel/CSV. Le routeur choisit une source par son
nom ; ``open_source`` fournit l'adaptateur SQL correspondant.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.duckdb_source import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import DatabaseAdapter, PostgresAdapter


class SourceBase(BaseModel):
    """Ce que toute source déclare : un nom, une description, un dictionnaire.

    ``dictionary`` désigne un fichier Markdown décrivant la base — sens des
    colonnes, valeurs admises, et surtout les pièges de modélisation. Le schéma
    seul ne dit pas qu'une ligne à ``quantity = 0`` est un vrai jour sans vente,
    ni que le e-commerce est une ligne de ``stores`` : ça ne s'infère d'aucun
    DDL, et c'est pourtant ce qui sépare un SQL juste d'un SQL plausible.
    """

    name: str
    description: str = ""
    dictionary: Path | None = None

    def dictionary_text(self) -> str | None:
        """Contenu du dictionnaire, ou ``None`` si la source n'en déclare pas."""
        if self.dictionary is None:
            return None
        return self.dictionary.read_text(encoding="utf-8")


class PostgresSource(SourceBase):
    """Base Postgres. Le DSN peut contenir des ``${VARIABLES}`` d'environnement."""

    type: Literal["postgres"] = "postgres"
    dsn: str  # postgresql+pg8000://user:mdp@hote:5432/base

    def resolved_dsn(self) -> str:
        dsn = os.path.expandvars(self.dsn)
        # expandvars laisse les ${VAR} inconnues telles quelles : sans ce garde-fou,
        # le littéral file jusqu'au driver et ressort en « invalid literal for
        # int() ... '${DAA_PG_PORT}' », qui ne dit pas quoi corriger.
        manquantes = re.findall(r"\$\{?(\w+)\}?", dsn)
        if manquantes:
            raise ValueError(
                f"source {self.name!r} : variable(s) d'environnement non définie(s) : "
                f"{', '.join(sorted(set(manquantes)))} — renseignez-les dans le .env "
                "(modèle : .env.example)."
            )
        return dsn


class FileSource(SourceBase):
    """Fichier de données requêtable en SQL (CSV ou Excel, via DuckDB)."""

    type: Literal["file"] = "file"
    path: Path


class DuckDBSource(SourceBase):
    """Base DuckDB (fichier ``.duckdb``), ouverte en lecture seule.

    Contrairement à ``FileSource``, qui expose un unique fichier sans relations,
    une base porte ses tables ET ses clés : le schéma en étoile arrive au modèle
    avec ses jointures déjà déclarées.
    """

    type: Literal["duckdb"] = "duckdb"
    path: Path


Source = Annotated[PostgresSource | FileSource | DuckDBSource, Field(discriminator="type")]


class Catalog(BaseModel):
    sources: list[Source] = Field(default_factory=list)

    def get(self, name: str) -> Source:
        for source in self.sources:
            if source.name == name:
                return source
        known = ", ".join(s.name for s in self.sources) or "(catalogue vide)"
        raise KeyError(f"source inconnue : {name!r} — sources connues : {known}")

    def describe(self) -> str:
        """Liste lisible des sources, pour le prompt du routeur."""
        lines = [
            f"- {s.name} ({s.type}) : {s.description or 'sans description'}" for s in self.sources
        ]
        return "\n".join(lines) or "(catalogue vide)"


def load_catalog(path: Path) -> Catalog:
    """Charge un catalogue YAML ; les chemins de fichiers relatifs le sont au YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    catalog = Catalog.model_validate(data)
    for source in catalog.sources:
        if isinstance(source, (FileSource, DuckDBSource)) and not source.path.is_absolute():
            source.path = (path.parent / source.path).resolve()
        if source.dictionary is not None and not source.dictionary.is_absolute():
            source.dictionary = (path.parent / source.dictionary).resolve()
    return catalog


def open_source(source: Source) -> DatabaseAdapter:
    """Ouvre l'adaptateur SQL adapté à la source (Postgres, base DuckDB, fichier)."""
    if isinstance(source, PostgresSource):
        return PostgresAdapter.from_dsn(source.resolved_dsn())
    if isinstance(source, DuckDBSource):
        return DuckDBAdapter.from_database(source.path)
    return DuckDBAdapter.from_file(source.path)
