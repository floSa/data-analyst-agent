"""Catalogue déclaratif des sources de données (YAML) — CADRAGE §7-①.

Une source est soit une base Postgres (DSN, variables d'environnement
autorisées), soit un fichier Excel/CSV. Le routeur choisit une source par son
nom ; ``open_source`` fournit l'adaptateur SQL correspondant.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.agents.retrieval.sql import DatabaseAdapter, PostgresAdapter


class PostgresSource(BaseModel):
    """Base Postgres. Le DSN peut contenir des ``${VARIABLES}`` d'environnement."""

    type: Literal["postgres"] = "postgres"
    name: str
    description: str = ""
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


class FileSource(BaseModel):
    """Fichier de données requêtable en SQL (CSV ou Excel, via DuckDB)."""

    type: Literal["file"] = "file"
    name: str
    description: str = ""
    path: Path


Source = Annotated[PostgresSource | FileSource, Field(discriminator="type")]


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
        if isinstance(source, FileSource) and not source.path.is_absolute():
            source.path = (path.parent / source.path).resolve()
    return catalog


def open_source(source: Source) -> DatabaseAdapter:
    """Ouvre l'adaptateur SQL adapté à la source (Postgres ou fichier DuckDB)."""
    if isinstance(source, PostgresSource):
        return PostgresAdapter.from_dsn(source.resolved_dsn())
    return DuckDBAdapter.from_file(source.path)
