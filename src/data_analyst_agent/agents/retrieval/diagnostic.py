"""Ce qu'on rend au modèle quand sa requête ÉCHOUE : deux faits de plus.

Le défaut, mesuré le 2026-09-24 sur le catalogue métier, trois passes sur
trois : « au total, combien d'unités sont sorties de l'atelier et combien sont
parties en commande ? » consomme les dix allers-retours de
``retrieval_request_limit`` et sert 180 669 / 20 557 — les chiffres d'une
jointure qui multiplie — là où les oracles disent 4 413 et 1 828.

Le déroulé est identique aux trois passes. Une requête aboutit, reçoit la
remarque de multiplication, et le modèle écrit la forme RÉPARÉE : deux
sous-requêtes déjà agrégées, rapprochées. Il y re-somme le résultat déjà
agrégé — ``SUM(T1.quantite_produite)`` sur une sous-requête qui n'expose que
``total_produit`` —, la base refuse, et **il renvoie la MÊME requête, à
l'identique, jusqu'à épuisement du budget** : quatre essais byte pour byte
identiques sur la passe relevée.

Deux faits manquaient, et ni l'un ni l'autre ne regarde la question.

**① Une requête déjà échouée, renvoyée telle quelle, le reçoit.** Le modèle
relisait son erreur inchangée et n'avait rien qui distingue « corrige » de « tu
viens d'essayer exactement ceci ». Le fait est une propriété de la
conversation : cette chaîne-là a déjà été exécutée, et elle a échoué.

**② Une erreur de colonne introuvable reçoit ce que la portée expose.** La
base ne le dit pas toujours : ``Values list "T1" does not have a column named
"quantite_produite"`` ne nomme AUCUN candidat, et c'est précisément l'erreur
qui ouvre la boucle. Le schéma ne le dit pas non plus — une sous-requête
agrégée n'est dans aucun schéma. On lit donc l'arbre de la requête et l'on
rend, relation par relation, les colonnes qu'elle expose réellement. C'est le
diagnostic que C59 a posé pour le Python, appliqué au SQL.

**Partout où il doute, ce module se tait**, comme ``verification`` : un SQL que
l'analyseur refuse, une projection en ``*`` dont on ne sait pas déplier les
colonnes, une table absente du schéma — il ne rend rien. Un fait faux coûterait
plus cher que le silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from data_analyst_agent.agents.retrieval.sql import SchemaInfo
from data_analyst_agent.agents.retrieval.verification import (
    sans_guillemets,
    sources_de_la_portee,
)

# Ce qu'on ajoute quand la requête est renvoyée à l'identique après un échec.
# Un fait, et rien de plus : ni consigne, ni rappel de ce qu'il faut faire —
# l'erreur, juste au-dessus, le dit déjà.
DEJA_ECHOUEE = (
    "CETTE REQUÊTE EXACTE a déjà été exécutée dans ce tour, et elle a échoué avec "
    "cette même erreur. La renvoyer telle quelle échouera encore."
)

# Les erreurs qui parlent d'une colonne que la portée n'a pas. DuckDB et
# Postgres les formulent chacun à leur façon ; on les reconnaît sur ce qu'elles
# ont de commun, et un faux positif ne coûterait qu'un fait vrai de trop.
_COLONNE_INTROUVABLE = re.compile(
    r"does not have a column named"
    r"|not found in FROM clause"
    r"|referenced column"
    r"|column .* does not exist"
    r"|unknown column",
    re.IGNORECASE,
)


def signature(sql: str) -> str:
    """Ce qui fait que deux requêtes sont LA MÊME : le texte, aux blancs près.

    On ne normalise ni la casse ni les guillemets : ``'ANN'`` et ``'ann'`` ne
    filtrent pas les mêmes lignes, et deux requêtes qui ne diffèrent que par là
    sont bien deux requêtes. Les blancs, eux, ne changent rien à ce que la base
    exécute — et un modèle qui réindente sa requête ne l'a pas corrigée.
    """
    return " ".join(sql.split()).rstrip(";").strip()


def porte_sur_une_colonne(erreur: str) -> bool:
    return bool(_COLONNE_INTROUVABLE.search(erreur))


@dataclass(frozen=True)
class Relation:
    """Une relation de la requête — table, sous-requête ou ``WITH`` — et ses colonnes."""

    alias: str
    colonnes: tuple[str, ...]


def colonnes_exposees(sql: str, schema: SchemaInfo) -> list[Relation]:
    """Ce que chaque relation de ce SQL expose réellement, dans l'ordre d'écriture.

    Une table rend les colonnes du schéma ; une sous-requête ou un ``WITH``
    rendent les noms de leur projection — leurs alias, qui sont les seuls noms
    sous lesquels la portée qui les enferme peut les désigner. C'est là que le
    modèle se trompe : il redemande la colonne d'AVANT l'agrégation.

    Une relation dont on ne sait pas dire les colonnes est omise, jamais
    devinée. La liste peut donc être vide, et l'appelant n'a alors rien à dire.
    """
    try:
        arbre = sqlglot.parse_one(sql)
    except Exception:  # SQL invalide : le moteur le dit déjà
        return []
    if arbre is None:
        return []
    du_schema = {t.name.lower(): tuple(c.name for c in t.columns) for t in schema.tables}
    des_with = {
        sans_guillemets(cte.alias).lower(): _projection(cte.this) for cte in arbre.find_all(exp.CTE)
    }
    trouvees: dict[str, Relation] = {}
    for select in arbre.find_all(exp.Select):
        for source in sources_de_la_portee(select):
            relation = _relation(source, du_schema, des_with)
            if relation is not None and relation.alias.lower() not in trouvees:
                trouvees[relation.alias.lower()] = relation
    return list(trouvees.values())


def pour_le_modele(relations: list[Relation]) -> str:
    """Le fait rendu au modèle : chaque relation, et ce qu'elle expose."""
    dites = " ; ".join(
        f"`{r.alias}` expose {', '.join(f'`{c}`' for c in r.colonnes)}" for r in relations
    )
    return f"CE QUE CHAQUE RELATION DE TA REQUÊTE EXPOSE RÉELLEMENT — {dites}."


def _relation(
    source: exp.Expression,
    du_schema: dict[str, tuple[str, ...]],
    des_with: dict[str, tuple[str, ...] | None],
) -> Relation | None:
    if isinstance(source, exp.Table):
        nom = sans_guillemets(source.name)
        alias = sans_guillemets(source.alias) or nom
        colonnes = des_with.get(nom.lower(), du_schema.get(nom.lower()))
        return Relation(alias=alias, colonnes=colonnes) if colonnes else None
    if isinstance(source, exp.Subquery):
        alias = sans_guillemets(source.alias)
        colonnes = _projection(source.this)
        return Relation(alias=alias, colonnes=colonnes) if alias and colonnes else None
    return None


def _projection(interieur: exp.Expression | None) -> tuple[str, ...] | None:
    """Les noms que cette sous-requête rend — ``None`` dès qu'on n'en est pas sûr.

    Une étoile est le cas qu'on refuse de déplier : elle rend les colonnes de
    SES tables, que la portée d'ici ne connaît pas, et les inventer donnerait un
    fait faux à l'endroit exact où l'on prétend dire le vrai.
    """
    if not isinstance(interieur, exp.Select):
        return None
    noms: list[str] = []
    for projetee in interieur.expressions:
        if isinstance(projetee, exp.Star) or projetee.find(exp.Star) is not None:
            return None
        nom = sans_guillemets(projetee.alias_or_name)
        if not nom:
            return None
        noms.append(nom)
    return tuple(noms) or None
