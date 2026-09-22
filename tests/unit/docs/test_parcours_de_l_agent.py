"""`docs/parcours-de-l-agent.md` ne doit pas pouvoir pourrir.

Une documentation qui décrit des nœuds et des outils devient fausse au premier
renommage, et rien ne rougit : le document reste bien formé, ses diagrammes se
rendent toujours, et il décrit un produit qui n'existe plus. C'est le mode de
panne qu'on ferme ici, dans l'esprit des empreintes déjà en place sur le prompt
système et sur les fiches d'outils.

**Les trois vérifications vont dans les DEUX sens, et c'est ce qui compte.**
« Tout nom cité existe » ne suffit pas : renommer `rappel` en `memoire` laisse
le document citer `rappel`, et un test qui ne regarde que les noms du graphe ne
voit rien tant qu'il ne lit pas aussi ce que le document a oublié. Les deux
directions ensemble font échouer le test sur un renommage, quel que soit le
sens dans lequel il a été fait.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from data_analyst_agent.agents.retrieval.catalog import Catalog
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.systeme import build_systeme_agent

RACINE = Path(__file__).resolve().parents[3]
DOCUMENT = RACINE / "docs" / "parcours-de-l-agent.md"

# Les deux tableaux de référence du document, repérés par leur en-tête. Le
# document cite les mêmes noms ailleurs — dans les traces, dans le tableau des
# coûts — mais c'est ici qu'il les DÉCLARE, et c'est cette déclaration-là qu'on
# tient pour la liste.
EN_TETE_DES_NOEUDS = "| nœud | ce qu'il fait |"
EN_TETE_DES_OUTILS = "| outil | ce qu'il rend |"

# Les nœuds techniques de langgraph : ils n'ont rien à faire dans un document
# écrit pour quelqu'un qui découvre le projet.
NOEUDS_TECHNIQUES = {"__start__", "__end__"}


def _texte() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def _premiere_colonne(texte: str, en_tete: str) -> set[str]:
    """Les noms entre accents graves de la première colonne d'un tableau."""
    lignes = texte.splitlines()
    depart = lignes.index(en_tete)
    noms: set[str] = set()
    for ligne in lignes[depart + 2 :]:
        if not ligne.startswith("|"):
            break
        cellule = ligne.split("|")[1].strip()
        if trouve := re.fullmatch(r"`([a-z_]+)`", cellule):
            noms.add(trouve.group(1))
    return noms


def _noeuds_du_graphe() -> set[str]:
    orchestrateur = Orchestrator(settings=Settings(_env_file=None), catalog=Catalog(sources=[]))
    return set(orchestrateur.graph.get_graph().nodes) - NOEUDS_TECHNIQUES


def _outils_de_l_agent_systeme() -> set[str]:
    (toolset,) = build_systeme_agent().toolsets
    return set(toolset.tools)


def test_le_document_existe():
    assert DOCUMENT.exists(), f"{DOCUMENT} : le document que ce test protège a disparu"


def test_tout_noeud_cite_existe_dans_le_graphe():
    """Un nœud renommé dans le graphe ne doit pas rester dans le document."""
    cites = _premiere_colonne(_texte(), EN_TETE_DES_NOEUDS)

    assert cites, "le tableau des nœuds est vide ou son en-tête a changé"
    assert cites <= _noeuds_du_graphe()


def test_tout_noeud_du_graphe_est_cite():
    """Et l'autre sens : un nœud ajouté au graphe doit entrer dans le document.

    Sans lui, un renommage se contenterait de faire disparaître un nom du
    document sans rien casser — le document décrirait alors un graphe amputé.
    """
    assert _noeuds_du_graphe() <= _premiere_colonne(_texte(), EN_TETE_DES_NOEUDS)


def test_tout_outil_cite_existe_dans_l_agent_systeme():
    cites = _premiere_colonne(_texte(), EN_TETE_DES_OUTILS)

    assert cites, "le tableau des outils est vide ou son en-tête a changé"
    assert cites <= _outils_de_l_agent_systeme()


def test_tout_outil_de_l_agent_systeme_est_cite():
    assert _outils_de_l_agent_systeme() <= _premiere_colonne(_texte(), EN_TETE_DES_OUTILS)


# Les liens du document, tels qu'un lecteur les suit : `[titre](chemin)`, relatifs
# au dossier `docs/`. Une ancre (`#…`) est retirée avant de chercher le fichier.
LIEN = re.compile(r"\]\((?!https?:)([^)#]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize("cible", sorted(set(LIEN.findall(_texte()))))
def test_tout_fichier_cite_existe(cible: str):
    """Un fichier déplacé ou renommé doit faire rougir ce test, pas laisser un lien mort."""
    assert (DOCUMENT.parent / cible).resolve().exists(), f"lien mort : {cible}"


# Les scripts et modules cités hors lien, entre accents graves : le document dit
# quelle commande rejoue le relevé, et cette commande doit désigner un fichier
# qui existe.
CHEMIN_CITE = re.compile(r"`((?:scripts|src|tests|docs|sources)/[\w./-]+)`")


@pytest.mark.parametrize("cible", sorted(set(CHEMIN_CITE.findall(_texte()))))
def test_tout_chemin_cite_existe(cible: str):
    assert (RACINE / cible).exists(), f"chemin cité mais absent du dépôt : {cible}"


# --- la prose est-elle restée derrière son relevé ? --------------------------

RELEVE = RACINE / "docs" / "releve-des-parcours.md"

# La ligne par laquelle le document déclare DE QUEL relevé il est tiré.
EMPREINTE_DECLAREE = re.compile(r"^ {4}RELEVÉ : ([0-9a-f]{64})$", re.MULTILINE)

# Une ligne du tableau « Le coût, en appels LLM » du document : le numéro du
# tour, les nœuds traversés entre accents graves, et le nombre d'appels.
LIGNE_DE_COUT = re.compile(
    r"^\| (\d) \| .*? \| ((?:`[a-z_]+`(?: → )?)+) \| (\d+) \|$", re.MULTILINE
)

# La même chose côté relevé, dans son tableau « Le fil en un coup d'œil » : les
# nœuds y sont écrits sans accents graves.
LIGNE_DU_RELEVE = re.compile(r"^\| (\d) \| .*? \| ([a-z_ →]+) \| (\d+) \| .*$", re.MULTILINE)


def _empreinte(donnees: bytes) -> str:
    return hashlib.sha256(donnees).hexdigest()


def _parcours(regex: re.Pattern[str], texte: str) -> dict[int, tuple[str, int]]:
    return {
        int(numero): (noeuds.replace("`", "").strip(), int(appels))
        for numero, noeuds, appels in regex.findall(texte)
    }


def test_le_document_declare_le_releve_dont_il_est_tire():
    """Sans cette déclaration, la vérification suivante n'aurait rien à quoi se tenir."""
    assert EMPREINTE_DECLAREE.search(_texte()), (
        "le document ne déclare plus l'empreinte du relevé dont il est tiré "
        "(ligne « RELEVÉ : <sha256> »)"
    )


def test_la_prose_n_est_pas_restee_derriere_son_releve():
    """Le relevé a-t-il été régénéré sans que cette prose soit reprise ?

    C'est la seule chose qu'une machine sache vérifier d'une prose : non
    qu'elle soit vraie, mais qu'elle ait été relue depuis la dernière mesure.
    Et c'est exactement ce qui a manqué — les vérifications de noms de ce
    fichier sont restées vertes pendant que le tour 8 décrivait le contraire de
    ce que le code fait.

    L'empreinte porte sur le CONTENU du relevé, jamais sur sa date de fichier :
    un `git clone` réécrit les dates de tout le dépôt d'un coup, et un test
    fondé sur elles rougirait au hasard d'une case cochée par le système de
    fichiers.

    Réparer ce test se fait en une commande, et c'est le but :

        uv run python scripts/releve_des_parcours.py --markdown docs/releve-des-parcours.md

    puis on relit la prose, et on met la ligne « RELEVÉ : … » à jour.
    """
    declaree = EMPREINTE_DECLAREE.search(_texte())
    assert declaree is not None

    assert declaree.group(1) == _empreinte(RELEVE.read_bytes()), (
        "docs/releve-des-parcours.md a changé depuis que docs/parcours-de-l-agent.md "
        "a été écrit : la prose se réclame d'un relevé qui n'est plus celui du dépôt. "
        "Relis le tour concerné, puis mets la ligne « RELEVÉ : … » à jour."
    )


def test_les_parcours_du_document_sont_ceux_du_releve():
    """Les seuls énoncés de cette prose qu'une machine sache confronter à une mesure.

    Le tableau des coûts du document et celui du relevé portent les mêmes
    faits — nœuds traversés, appels LLM — écrits deux fois. Les comparer
    n'atteste pas la prose alentour, mais il attrape ce que l'empreinte seule
    laisserait passer : une prose reprise à la main sans qu'on ait relu les
    chiffres. Ils auraient rougi — le document annonçait 7 appels au tour 6 et
    4 au tour 8, là où le relevé en comptait 5 et 6.
    """
    du_document = _parcours(LIGNE_DE_COUT, _texte())
    du_releve = _parcours(LIGNE_DU_RELEVE, RELEVE.read_text(encoding="utf-8"))

    assert du_document, "le tableau « Le coût, en appels LLM » est vide ou a changé de forme"
    assert set(du_document) == set(du_releve)
    assert du_document == du_releve
