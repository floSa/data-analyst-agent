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
from pydantic import BaseModel, Field

from data_analyst_agent.agents.retrieval.catalog import FileSource


class WorkspaceArtifact(BaseModel):
    """Métadonnées d'un tableau intermédiaire persisté."""

    name: str  # nom d'usage = nom de source éphémère = nom de table DuckDB
    file: str  # nom du fichier CSV, relatif au dossier de la conversation
    columns: list[str]
    row_count: int
    question: str  # la question qui l'a produit (aide le planificateur)


class ConversationContext(BaseModel):
    """Trace du dernier tour, pour comprendre un ajustement (« plus de couleurs »)."""

    last_question: str = ""
    last_capability: str | None = None
    last_source: str | None = None
    last_code: str | None = None  # code d'analyse produit (pour repartir dessus)
    # Features d'une prédiction RÉUSSIE, pour l'ajuster au tour suivant (« et si
    # elle était en 3e classe ? »). Le `pending` ne couvre que les prédictions
    # INCOMPLÈTES : dès qu'une prédiction aboutit il est vidé, et l'acquis
    # disparaissait avec lui — l'agent redemandait alors des features déjà
    # données, comme le sibsp d'une passagère décrite deux tours plus haut.
    last_dataset: str | None = None
    last_features: dict = Field(default_factory=dict)


def safe_dir_name(name: str) -> str:
    """Nom de dossier sûr à partir d'un conversation_id arbitraire.

    Partagé avec :mod:`data_analyst_agent.orchestrator.conversations` : les deux
    modules écrivent dans le MÊME dossier par conversation, il doit être calculé
    de la même façon des deux côtés.
    """
    return re.sub(r"[^0-9A-Za-z_-]+", "_", name).strip("_") or "conversation"


class ConversationWorkspace:
    """Dossier de travail d'une conversation (objets intermédiaires en CSV)."""

    MANIFEST = "manifest.json"
    CONTEXT = "context.json"

    def __init__(self, base_dir: Path, conversation_id: str) -> None:
        self.dir = Path(base_dir) / safe_dir_name(conversation_id)
        self.artifacts: list[WorkspaceArtifact] = self._load()
        self.context: ConversationContext = self._load_context()

    # -- persistance ----------------------------------------------------------

    def _manifest_path(self) -> Path:
        return self.dir / self.MANIFEST

    def _context_path(self) -> Path:
        return self.dir / self.CONTEXT

    def _load_context(self) -> ConversationContext:
        path = self._context_path()
        if not path.exists():
            return ConversationContext()
        return ConversationContext.model_validate_json(path.read_text(encoding="utf-8"))

    def record_turn(
        self,
        question: str,
        capability: str | None,
        source: str | None,
        code: str | None = None,
        dataset: str | None = None,
        features: dict | None = None,
    ) -> None:
        """Mémorise le tour courant (question + action) pour comprendre le suivant."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.context = ConversationContext(
            last_question=question,
            last_capability=capability,
            last_source=source,
            last_code=code,
            last_dataset=dataset,
            last_features=features or {},
        )
        self._context_path().write_text(self.context.model_dump_json(indent=2), encoding="utf-8")

    def describe_context(self) -> str | None:
        """Contexte du tour précédent pour le planificateur (résolution des ajustements)."""
        c = self.context
        if not c.last_question:
            return None
        src = f" sur « {c.last_source} »" if c.last_source else ""
        return (
            f"CONTEXTE CONVERSATIONNEL : au tour précédent, l'utilisateur a demandé "
            f"« {c.last_question} » (action : {c.last_capability or '?'}{src}). Si le "
            "message courant est un AJUSTEMENT de ce tour (« mets des couleurs plus "
            "vives », « plutôt en barres », « et pour les hommes ? »), reprends la MÊME "
            f"capacité et la MÊME source{src}."
        )

    def last_code_for(self, source: str | None) -> str | None:
        """Le code d'analyse du tour précédent si c'était sur la même source."""
        c = self.context
        return c.last_code if (source is not None and c.last_source == source) else None

    def last_features_for(self, dataset: str | None) -> dict:
        """Les features de la dernière prédiction réussie, si c'est le même dataset.

        Base d'un ajustement (« et si elle était en 3e classe ? ») : sans elles,
        le tour suivant repart de zéro et redemande tout.
        """
        c = self.context
        if dataset is None or c.last_dataset != dataset:
            return {}
        return dict(c.last_features)

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
            "« ces fleurs », « le tableau précédent » ou « ce résultat » désigne en "
            "général ce dernier : choisis-le comme `source`. Pour PRÉDIRE sur un tel "
            "tableau (« prédis ces lignes »), utilise fetch_then_predict avec ce tableau "
            "comme `source`."
        )
