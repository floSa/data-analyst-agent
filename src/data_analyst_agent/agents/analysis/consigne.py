"""Une somme qui exige un filtre, vérifiée sur le CODE produit — pas sur la question.

Relevé sur « compare les quantités produites et les quantités vendues par
produit » (C60, catalogue métier, trois tirages identiques). Le dictionnaire de
`ventes` arrivait ENTIER dans le prompt de l'analyse — piège nº 1 compris, la
phrase « toute somme d'unités vendues ou expédiées » comprise, aucune coupe —
et le code l'ignorait trois fois sur trois : il lisait
``ventes_lignes_commande.csv``, sommait ``quantite``, ne joignait jamais
``ventes_commandes`` où vit le statut, et commentait lui-même « aucune règle
spécifique mentionnée pour la vente de produits ». 141 et 131 au lieu de 123
et 125, sans erreur : l'exécution réussit, donc la boucle de correction ne se
déclenchait pas.

Ce n'était pas un défaut d'ACHEMINEMENT, et une phrase de prompt de plus n'y
aurait rien fait : le texte était lu, et il n'était pas appliqué. Ce module dit
la même chose sous la forme d'une propriété du code, comme
``agents/retrieval/classement`` le fait pour le SQL : **un code qui lit une
table déclarée, nomme une colonne dont la somme exige un filtre, somme, et ne
porte nulle part la valeur à écarter, a oublié le filtre.** Vraie ou fausse
quelle que soit la tournure de la question.

**Ce qu'il vérifie vient de la SOURCE, jamais d'ici.** La règle est déclarée au
catalogue (``filtre_des_sommes``) : la colonne qui filtre, la valeur à écarter,
les colonnes dont la somme l'exige. Rien n'est lu dans le Markdown du
dictionnaire : un dictionnaire de démonstration pose ``WHERE statut = 'T'``
pour COMPTER des recharges et le refuse pour SOMMER leur énergie — un
analyseur de texte aurait appris la règle à l'envers sur l'un des deux.

**Ce qu'il ne touche pas : les comptages.** La même source dit « AUCUN filtre
sur un comptage » — 180 commandes, pas 164. Un comptage ne nomme aucune
colonne de mesure : ``len``, ``size``, ``count`` sur ``commande_id``. La
propriété exige qu'une colonne DÉCLARÉE soit nommée ; un comptage n'est donc
jamais signalé, même s'il additionne ses propres effectifs.

Ce n'est pas un analyseur de pandas. Il lit l'arbre syntaxique, et seulement
les CHAÎNES du code — un commentaire qui cite la règle ne filtre rien, et n'est
pas compté comme filtre. Partout où il doute (un code qui ne se lit pas, un
nom de fichier construit par f-string), il ne dit rien : un doute coûte au pire
le chiffre d'avant ; un faux positif coûterait un essai de la boucle, et
pourrait pousser le modèle à filtrer une réponse juste.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass

from data_analyst_agent.agents.retrieval.catalog import FiltreDesSommes

_SOMME_SQL = re.compile(r"\bsum\s*\(", re.IGNORECASE)


@dataclass(frozen=True)
class FiltreMonte:
    """Une règle déclarée, et le nom sous lequel ses tables sont montées.

    ``prefixe`` vaut ``"ventes_"`` dans un croisement, où chaque table porte le
    nom de sa source (`agents/retrieval/croisement`), et ``""`` quand la source
    est seule : ``/data/lignes_commande.csv``.

    Deux noms montés et non un : le chemin d'analyse voit des CSV dans un bac à
    sable, le chemin SQL voit des tables dans une connexion
    (`agents/retrieval/verification`). C'est la même déclaration, lue sous les
    deux formes sous lesquelles elle est montée.
    """

    source: str
    prefixe: str
    filtre: FiltreDesSommes

    def table(self, table: str) -> str:
        return f"{self.prefixe}{table}"

    def fichier(self, table: str) -> str:
        return f"{self.table(table)}.csv"


def _chaines(arbre: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _somme(arbre: ast.AST, chaines: list[str]) -> bool:
    """Le code somme-t-il quelque chose — ``.sum()``, ``agg('sum')``, ``SUM(`` en SQL ?"""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Attribute) and noeud.attr == "sum":
            return True
    return any(c == "sum" or _SOMME_SQL.search(c) for c in chaines)


def _lit(chaines: list[str], fichier: str) -> bool:
    """Le code nomme-t-il ce fichier — seul, en chemin, ou dans une requête DuckDB ?

    ``/lignes_commande.csv`` et non ``lignes_commande.csv`` : dans un
    croisement, ``/data/ventes_lignes_commande.csv`` ne doit pas passer pour la
    table non préfixée, ni l'inverse.
    """
    return any(c == fichier or f"/{fichier}" in c for c in chaines)


def _nomme(chaines: list[str], colonne: str) -> bool:
    mot = re.compile(rf"\b{re.escape(colonne)}\b")
    return any(c == colonne or (_SOMME_SQL.search(c) and mot.search(c)) for c in chaines)


@dataclass(frozen=True)
class SommeNonFiltree:
    """Ce qui a été relevé dans le code : quelles sommes, et quel filtre leur manque."""

    source: str
    lues: str
    colonne: str
    exclure: str
    fichier_du_filtre: str

    def pour_le_modele(self) -> str:
        """Le fait rendu à la boucle de correction, tiré de la déclaration seule."""
        return (
            f"Le code somme {self.lues} sans écarter `{self.colonne} = '{self.exclure}'`. "
            f"Le dictionnaire de `{self.source}` impose `{self.colonne} <> '{self.exclure}'` "
            f"pour toute somme de ces colonnes. `{self.colonne}` vit dans "
            f"`{self.fichier_du_filtre}` : joins-la avant de sommer. Ne filtre aucun "
            "comptage : cette règle ne vaut que pour les sommes."
        )

    def pour_l_utilisateur(self) -> str:
        """Ce que l'utilisateur lit quand le dernier essai somme encore sans le filtre.

        La réponse est servie — la jeter serait pire, et le chiffre peut être
        juste si le filtre a été posé sans écrire la valeur (``isin(['LIV',
        'EXP'])``) — mais elle n'est pas servie en silence.
        """
        return (
            f"Avertissement sur ce calcul : le code somme {self.lues} sans écarter "
            f"`{self.colonne} = '{self.exclure}'`, que le dictionnaire de `{self.source}` "
            "exclut de toute somme de ces colonnes. Le chiffre peut être surévalué."
        )


def somme_sans_son_filtre(code: str, filtres: Sequence[FiltreMonte]) -> SommeNonFiltree | None:
    """Ce qui manque à ce code s'il somme sans le filtre déclaré, sinon ``None``."""
    if not filtres or not code.strip():
        return None
    try:
        arbre = ast.parse(code)
    except SyntaxError:
        # Un code qui ne se lit pas ne s'exécute pas non plus : c'est la trace
        # d'erreur qui parle, pas ce module.
        return None
    chaines = _chaines(arbre)
    if not _somme(arbre, chaines):
        return None
    for monte in filtres:
        f = monte.filtre
        valeur = re.compile(rf"\b{re.escape(f.exclure)}\b")
        if any(valeur.search(c) for c in chaines):
            continue
        sommees = [
            (table, colonne)
            for table, colonne in (s.split(".", 1) for s in f.sommes)
            if _lit(chaines, monte.fichier(table)) and _nomme(chaines, colonne)
        ]
        if not sommees:
            continue
        table_du_filtre, colonne_du_filtre = f.colonne.split(".", 1)
        return SommeNonFiltree(
            source=monte.source,
            lues=", ".join(f"`{c}` (lue dans `{monte.fichier(t)}`)" for t, c in sommees),
            colonne=colonne_du_filtre,
            exclure=f.exclure,
            fichier_du_filtre=monte.fichier(table_du_filtre),
        )
    return None
