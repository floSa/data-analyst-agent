"""Le nom de l'ancien moteur ne revient pas dans le dépôt.

Ce dépôt n'a qu'un moteur d'inférence : vLLM, sur le port 8100, servant
``google/gemma-4-E4B-it-qat-w4a16-ct`` (docs/MOTEUR.md). Le précédent a été
retiré partout — valeurs par défaut, variable d'environnement dépréciée, code,
scripts, fichiers d'exemple et documentation.

**Pourquoi un test et pas une relecture.** Le nettoyage a porté sur 299
occurrences dans 34 fichiers. Ce qui a coûté n'était pas de les trouver, c'était
qu'elles étaient revenues : un défaut de `config.py` qui visait l'ancien port, une
variable dépréciée gardée « au cas où », et des phrases de documentation qui
expliquaient un réglage en le comparant à l'autre moteur. Une relecture ne
protège pas de la prochaine ; ce test, si.

**Il n'a aucune exception**, et c'est délibéré. Une exception voudrait dire qu'il
reste une phrase qui ne tient que par le nom de l'autre produit — et c'est
précisément ce qu'on a fini de réécrire. Un document d'archive garde son récit en
disant « l'ancien moteur » ; il n'a jamais besoin de le nommer.

Ce qui est interdit, et pourquoi :

- le **nom du produit**, sous toutes ses casses ;
- son **port**, qui le désigne aussi sûrement que son nom ;
- l'**étiquette de modèle** de son registre, qui n'a pas cours face à un serveur
  qui attend un identifiant de dépôt Hugging Face.

Le motif est assemblé morceau par morceau : écrit en clair, il ferait échouer ce
fichier sur lui-même, et l'exclure serait la première exception.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]

# Assemblé pour que la chaîne interdite n'apparaisse nulle part dans ce fichier.
_PRODUIT = "oll" + "ama"
_PORT = "114" + "34"
_ETIQUETTE = "gemma4" + ":" + "e4b"

INTERDITS: dict[str, str] = {
    _PRODUIT: "le nom de l'ancien moteur",
    _PORT: "le port de l'ancien moteur (le moteur en service écoute 8100)",
    _ETIQUETTE: "l'étiquette de modèle de l'ancien moteur",
}

MOTIF = re.compile("|".join(re.escape(mot) for mot in INTERDITS), re.IGNORECASE)

# Les chemins balayés, nommés plutôt que devinés : un dossier ajouté au dépôt et
# oublié ici ne serait pas couvert, et une liste explicite se relit.
CHEMINS_BALAYES = (
    "src",
    "tests",
    "scripts",
    "docs",
    "deploy",
    "models",
    "notebooks",
    "sources",
    "README.md",
    "pyproject.toml",
    ".env.example",
    "users.example.yaml",
)

# Ce qui n'est pas du texte relisible : un binaire ou un verrou de dépendances.
SUFFIXES_IGNORES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".joblib", ".parquet", ".xlsx", ".duckdb"}
)


def _fichiers_suivis() -> list[Path]:
    """Les fichiers SUIVIS par git sous les chemins balayés.

    Par git et non par `Path.rglob` : autrement le balayage ramasserait `.venv`,
    les caches et les artefacts de couverture — des milliers de fichiers qui ne
    sont pas le dépôt, et dont le contenu ne nous engage pas.
    """
    sortie = subprocess.run(
        ["git", "ls-files", "-z", "--", *CHEMINS_BALAYES],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    chemins = [RACINE / nom for nom in sortie.split("\0") if nom]
    return [c for c in chemins if c.suffix.lower() not in SUFFIXES_IGNORES]


def test_les_chemins_balayes_existent_tous():
    """Un chemin renommé ou supprimé viderait le balayage en silence."""
    manquants = [c for c in CHEMINS_BALAYES if not (RACINE / c).exists()]

    assert not manquants, f"chemins balayés introuvables : {manquants}"


def test_le_balayage_ramasse_bien_le_code_et_la_documentation():
    """Le garde-fou ne vaut que s'il lit quelque chose.

    Sans cette vérification, une erreur de `git ls-files` rendrait zéro fichier
    et le test principal passerait en ne lisant rien — le pire des faux verts.
    """
    fichiers = _fichiers_suivis()
    relatifs = {str(f.relative_to(RACINE)) for f in fichiers}

    assert len(fichiers) > 100
    assert "src/data_analyst_agent/config.py" in relatifs
    assert "docs/MOTEUR.md" in relatifs
    assert ".env.example" in relatifs


def test_le_motif_reconnait_ce_quil_doit_interdire():
    """Le motif lui-même est éprouvé : un test de non-présence qui ne
    reconnaîtrait rien passerait pour toujours."""
    for mot in INTERDITS:
        assert MOTIF.search(f"texte {mot} texte")
        assert MOTIF.search(f"texte {mot.upper()} texte")

    # Et il ne mord pas sur ce qui est légitime : `gemma4` est le nom de
    # l'analyseur d'appels d'outil de vLLM, et le modèle servi s'écrit ainsi.
    assert not MOTIF.search("--tool-call-parser gemma4")
    assert not MOTIF.search("google/gemma-4-E4B-it-qat-w4a16-ct")
    assert not MOTIF.search("http://localhost:8100/v1")


@pytest.mark.parametrize(("mot", "raison"), sorted(INTERDITS.items()))
def test_le_nom_de_lancien_moteur_ne_reapparait_pas(mot: str, raison: str):
    """Un paramètre par interdit : l'échec dit LEQUEL est revenu, et où."""
    motif = re.compile(re.escape(mot), re.IGNORECASE)
    fautifs: list[str] = []

    for fichier in _fichiers_suivis():
        try:
            contenu = fichier.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for numero, ligne in enumerate(contenu.splitlines(), start=1):
            if motif.search(ligne):
                fautifs.append(f"{fichier.relative_to(RACINE)}:{numero}: {ligne.strip()[:100]}")

    assert not fautifs, (
        f"{raison} est de retour dans {len(fautifs)} ligne(s). "
        "Ce dépôt n'a qu'un moteur : vLLM sur 8100, cf. docs/MOTEUR.md.\n" + "\n".join(fautifs[:20])
    )
