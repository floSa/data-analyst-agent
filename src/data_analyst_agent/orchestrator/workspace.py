"""Mémoire de conversation : persiste les tableaux intermédiaires en CSV.

Chaque conversation possède un dossier ; les tableaux produits (résultats de
requête, lots de prédiction) y sont écrits en CSV et décrits dans un manifeste
JSON. Aux tours suivants, ces tableaux sont réexposés :

- comme **sources éphémères** interrogeables en SQL (DuckDB) et réutilisables
  pour une prédiction (« prédis ces lignes ») ;
- **montés dans la sandbox** pour que le code d'analyse généré puisse les relire
  (``pd.read_csv('/data/resultat_1.csv')``) ;
- **décrits au planificateur** pour qu'il sache y faire référence.

Le nom d'un objet (``resultat_1``, ``resultat_2``…) est aussi le nom de la
source éphémère et de la table DuckDB correspondante (via le nom de fichier).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from data_analyst_agent.agents.retrieval.catalog import FileSource


class WorkspaceArtifact(BaseModel):
    """Métadonnées d'un tableau intermédiaire persisté."""

    name: str  # nom d'usage = nom de source éphémère = nom de table DuckDB
    file: str  # nom du fichier CSV, relatif au dossier de la conversation
    columns: list[str]
    row_count: int
    question: str  # la question qui l'a produit (aide le planificateur)


def _safe(name: str) -> str:
    """Nom de dossier sûr à partir d'un conversation_id arbitraire."""
    return re.sub(r"[^0-9A-Za-z_-]+", "_", name).strip("_") or "conversation"


class ConversationWorkspace:
    """Dossier de travail d'une conversation (objets intermédiaires en CSV)."""

    MANIFEST = "manifest.json"

    def __init__(self, base_dir: Path, conversation_id: str) -> None:
        self.dir = Path(base_dir) / _safe(conversation_id)
        self.artifacts: list[WorkspaceArtifact] = self._load()

    # -- persistance ----------------------------------------------------------

    def _manifest_path(self) -> Path:
        return self.dir / self.MANIFEST

    def _load(self) -> list[WorkspaceArtifact]:
        path = self._manifest_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [WorkspaceArtifact.model_validate(a) for a in data.get("artifacts", [])]

    def _save_manifest(self) -> None:
        payload = {"artifacts": [a.model_dump() for a in self.artifacts]}
        self._manifest_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_table(self, columns: list[str], rows: list[list], question: str) -> WorkspaceArtifact:
        """Écrit un tableau en CSV, l'ajoute au manifeste et le renvoie."""
        self.dir.mkdir(parents=True, exist_ok=True)
        name = f"resultat_{len(self.artifacts) + 1}"
        file = f"{name}.csv"
        pd.DataFrame(rows, columns=columns).to_csv(self.dir / file, index=False)
        artifact = WorkspaceArtifact(
            name=name, file=file, columns=list(columns), row_count=len(rows), question=question
        )
        self.artifacts.append(artifact)
        self._save_manifest()
        return artifact

    # -- réexposition ---------------------------------------------------------

    def path_of(self, artifact: WorkspaceArtifact) -> Path:
        return self.dir / artifact.file

    def as_sources(self) -> list[FileSource]:
        """Les objets mémorisés vus comme des sources fichier interrogeables."""
        return [
            FileSource(
                name=a.name,
                description=f"Tableau intermédiaire ({a.row_count} lignes) issu de : {a.question}",
                path=self.path_of(a),
            )
            for a in self.artifacts
        ]

    def sandbox_files(self) -> dict[Path, str]:
        """Mapping chemin hôte -> nom sous /data/ pour monter dans la sandbox."""
        return {self.path_of(a): a.file for a in self.artifacts}

    def describe(self) -> str | None:
        """Description des objets intermédiaires pour le prompt du planificateur."""
        if not self.artifacts:
            return None
        lines = [
            f"- {a.name} ({a.row_count} lignes ; colonnes : {', '.join(a.columns)})"
            f" — produit par : « {a.question} »"
            for a in self.artifacts
        ]
        latest = self.artifacts[-1].name
        return (
            "Objets intermédiaires déjà produits dans CETTE conversation "
            "(interrogeables comme des sources par leur nom, ou réutilisables tels "
            "quels pour une prédiction) :\n"
            + "\n".join(lines)
            + f"\nLe plus récent est '{latest}'. Une référence comme « ces lignes », "
            "« le tableau précédent » ou « ce résultat » désigne en général ce dernier : "
            "choisis-le comme `source`."
        )
