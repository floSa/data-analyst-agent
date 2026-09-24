"""Deux propriétés du SQL produit, vérifiées avant que ses chiffres soient servis.

Le défaut, mesuré le 2026-09-24 sur le catalogue métier, trois tirages sur trois
et deux questions sur deux : « pour le VEL-01, combien on en a fabriqué et
combien on en a vendu ? » rend **26 871 fabriqués et 3 384 vendus**, là où les
oracles disent 689 et 123. Le SQL somme ``production_ordres_fabrication`` et
``ventes_lignes_commande`` dans UNE requête, en les rapprochant par le produit :
chaque ordre de fabrication est apparié à chacune des lignes de commande du même
produit, et réciproquement. Les deux sommes sont multipliées l'une par l'autre
(689 fois 39 = 26 871). Aucune erreur n'est levée, la phrase de réponse est
parfaitement lisible, et le chiffre est faux d'un facteur quarante.

Deux fautes empilées, et deux propriétés pour les fermer. Ni l'une ni l'autre ne
regarde la question : elles sont vraies ou fausses quelle que soit la tournure,
comme `classement` pour le palmarès et `agents/analysis/consigne` pour le code.

**① Une somme lue dans une table ne doit pas être multipliée par une jointure.**
Elle se MESURE dans les données, et ne se devine pas sur les mots : pour chaque
table que la requête rejoint, on demande à la base si la colonne de jointure y
identifie une ligne. Une table qu'on atteint par une clé unique est une table de
DIMENSION — elle décore, elle ne duplique pas. Une table qu'on n'atteint par
aucune clé unique, ou qu'aucune égalité de colonnes ne relie, apparie plusieurs
de ses lignes à chaque ligne sommée : la somme est multipliée d'autant.

C'est la même lecture que ``croisement.relier_les_sources``, qui décide dans les
données quelle colonne peut être RÉFÉRENCÉE. Une différence, et elle est
voulue : là-bas une clé doit être sans NULL, ici seulement sans DOUBLON. Un NULL
ne multiplie rien — il fait tomber la ligne d'une jointure interne —, et exiger
son absence ferait signaler une jointure parfaitement saine.

**② La règle ``filtre_des_sommes`` vaut aussi pour le SQL.** Elle est DÉCLARÉE
au catalogue, une fois, et le chemin d'analyse la vérifie déjà sur le code
produit (C60). Le chemin SQL ne la vérifiait pas : la même requête qui multiplie
somme aussi ``quantite`` sans écarter les commandes annulées. Rien n'est réécrit
ici — la déclaration est celle de C60, et ``FiltreMonte`` dit sous quel nom ses
tables sont montées.

**Chaque somme est jugée dans SA portée** (C62). Les deux propriétés ne
lisaient d'abord que le niveau zéro de la requête. Or la relance contre la
multiplication pousse le modèle vers des sous-requêtes agrégées — c'est la forme
RÉPARÉE —, et une somme écrite là, ou dans un ``WITH``, n'était plus vue du
tout : sur « pour le VEL-02, combien fabriqués et combien vendus ? », un tirage
sur sept servait 147 vendus quand le juste est 130, sans un mot. Le SQL est donc
découpé en portées — la requête principale, chaque sous-requête, chaque ``WITH``
—, et chacune est pesée avec SES tables, SES jointures et SES sommes. Un filtre
compte là où il agit : dans la portée qui somme, ou dans une portée qui
l'enferme, jamais dans une portée sœur.

**Une somme sommée DANS une expression reste une somme** (C64). Les deux
propriétés n'ont jamais lu que ``SUM(colonne)`` : dès que la colonne était
enrobée — un ``CASE``, un ``COALESCE``, ``quantite * prix``, un cast —, la
portée était comptée illisible et plus rien n'était dit. Mesuré sur « pour le
VEL-01, combien on en a fabriqué et combien on en a vendu ? » : **141 vendus
servis quand le juste est 123**, le chiffre sans le filtre des annulées, dans
un ``SUM(CASE WHEN code_produit = 'VEL-01' THEN quantite ELSE 0 END)``, sans
une remarque et sans un avertissement. On descend donc jusqu'aux colonnes,
sous l'expression qui les porte. Pas dans les CONDITIONS : la colonne d'un
``WHEN`` filtre les lignes, elle ne dit pas ce qu'on somme, et la lire comme
sommée ferait juger la multiplication depuis la mauvaise table.

**Un comptage n'est jamais touché**, par construction : les deux propriétés
exigent un ``SUM(``. « combien de commandes » rend 180, pas 164, et
``COUNT(*)`` sur une jointure de dimension ne déclenche rien.

**Partout où il doute, ce module se tait.** Une table qu'il ne reconnaît pas
dans le schéma — le nom d'un ``WITH`` en est une —, une sous-requête en guise de
table, un SQL que l'analyseur refuse, une colonne dont la base ne peut pas dire
si elle est unique : il rend ``None``. Un doute coûte au pire le chiffre
d'avant ; un faux positif coûterait un aller-retour de modèle et pourrait
pousser à corriger une requête juste. ``Lecture.illisibles`` compte les portées
sommantes ainsi manquées : le silence est mesurable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from data_analyst_agent.agents.analysis.consigne import FiltreMonte


def sans_guillemets(nom: str) -> str:
    return nom.strip().strip('"').strip("`")


@dataclass(frozen=True)
class Cardinalite:
    """Ce que la base répond d'une colonne : combien de lignes, combien de valeurs.

    ``duplique`` est la seule chose qu'on en tire, et c'est la question posée :
    cette colonne apparie-t-elle PLUSIEURS lignes de sa table à une même valeur ?
    Les NULL sont hors du compte — une ligne dont la clé est nulle ne rejoint
    rien et ne multiplie personne.
    """

    lignes: int
    renseignees: int
    distinctes: int

    @property
    def duplique(self) -> bool:
        return self.distinctes < self.renseignees

    def en_clair(self, table: str, colonne: str) -> str:
        return (
            f"`{table}.{colonne}` porte {self.renseignees} ligne(s) renseignée(s) "
            f"pour {self.distinctes} valeur(s) distincte(s)"
        )


# Ce qu'on demande à la base d'une colonne de jointure. ``None`` = la question
# n'a pas pu être posée (table ou colonne inconnue) : on se tait.
Sonde = Callable[[str, str], Cardinalite | None]


@dataclass(frozen=True)
class Table:
    """Une table de la clause FROM : son alias dans la requête, son nom réel."""

    alias: str
    nom: str


@dataclass(frozen=True)
class Egalite:
    """Une égalité de colonnes entre deux tables de la requête."""

    gauche: str
    colonne_gauche: str
    droite: str
    colonne_droite: str


@dataclass(frozen=True)
class Requete:
    """Une PORTÉE lue : ses tables, ses égalités, ses sommes, ce qui la contraint.

    Une portée est un ``SELECT`` et un seul : la requête principale, une
    sous-requête du ``SELECT``, du ``FROM`` ou du ``WHERE``, le corps d'un
    ``WITH``. Les tables d'une portée sont celles de SON ``FROM``, ses sommes
    celles qui s'ouvrent chez elle, et ses jointures les siennes : c'est là, et
    nulle part ailleurs, que se joue la multiplication d'une somme.

    ``contexte`` est le texte contre lequel on cherche un filtre : la portée
    elle-même, et les conditions des portées qui l'enferment — un ``WHERE``
    extérieur restreint bien les lignes qu'une sous-requête du ``FROM`` a
    rendues. Le texte d'une portée SŒUR n'y est pas : le filtre posé dans une
    sous-requête ne filtre pas celle d'à côté.
    """

    tables: dict[str, Table]
    egalites: list[Egalite]
    # (alias, colonne) pour chaque colonne sommée, dans l'ordre d'écriture.
    sommees: list[tuple[str, str]]
    contexte: str = ""


@dataclass(frozen=True)
class Lecture:
    """Ce qu'on a su lire d'un SQL : ses portées sommantes, et celles qu'on a manquées.

    ``illisibles`` ne compte QUE les portées qui somment : une portée sans
    ``SUM(`` n'a rien à faire juger, et la manquer ne coûte rien. Ce compte est
    la mesure honnête du silence de ce module — il se tait, et il dit combien
    de fois.
    """

    portees: list[Requete] = field(default_factory=list)
    illisibles: int = 0


def lire_les_portees(sql: str, tables_connues: Mapping[str, str]) -> Lecture:
    """Chaque ``SELECT`` du SQL, lu dans SA portée — les sommantes seulement.

    L'arbre est celui de ``sqlglot`` (pur Python, licence MIT) et non la lecture
    maison de ``lecture.py``. Celle-ci repère des mots-clés hors parenthèses :
    elle voit le niveau zéro, et rien d'autre. Or une somme écrite en
    sous-requête ou dans un ``WITH`` est justement hors du niveau zéro — c'est
    même la forme vers laquelle la relance contre la multiplication pousse le
    modèle. Tenir les portées à la parenthèse près demande un arbre ; le faire à
    la main redonnerait un analyseur SQL, en moins sûr. ``lecture.py`` reste ce
    qu'il est, et ``classement`` continue de s'en servir.

    ``tables_connues`` vient du SCHÉMA de la source : nom en minuscules -> nom
    réel. Il résout la casse — le modèle écrit ``Commandes``, Postgres range
    ``commandes`` — et il ARRÊTE la lecture d'une portée dont une table lui est
    inconnue : le nom d'un ``WITH``, une fonction de table, un ``VALUES`` n'ont
    pas de cardinalité à mesurer, et conclure sur leur compte serait conclure
    sur rien.
    """
    if not sql or not sql.strip():
        return Lecture()
    try:
        arbre = sqlglot.parse_one(sql)
    except Exception:  # SQL invalide : le moteur le dira, il n'y a rien à ajouter
        return Lecture()
    if arbre is None:
        return Lecture()
    portees: list[Requete] = []
    illisibles = 0
    for select in arbre.find_all(exp.Select):
        sommes = list(_sommes_de_la_portee(select))
        if not sommes:
            continue  # rien à juger ici : un comptage, une projection, un rapprochement
        lue = _lire_une_portee(select, sommes, tables_connues)
        if lue is None:
            illisibles += 1
            continue
        portees.append(lue)
    return Lecture(portees=portees, illisibles=illisibles)


def _lire_une_portee(
    select: exp.Select, sommes: list[exp.Sum], tables_connues: Mapping[str, str]
) -> Requete | None:
    tables = _tables_de_la_portee(select, tables_connues)
    if tables is None:
        return None
    sommees = _colonnes_sommees(sommes, tables)
    if sommees is None:
        return None
    return Requete(
        tables=tables,
        egalites=_egalites(select, tables),
        sommees=sommees,
        contexte=_contexte(select),
    )


def _descendre(noeud) -> Iterator[exp.Expression]:
    """Les nœuds de CETTE portée : on s'arrête à l'entrée d'une portée fille."""
    if isinstance(noeud, list):
        for element in noeud:
            yield from _descendre(element)
        return
    if not isinstance(noeud, exp.Expression):
        return
    if isinstance(noeud, exp.Select | exp.Subquery):
        return
    yield noeud
    for valeur in noeud.args.values():
        yield from _descendre(valeur)


def _sommes_de_la_portee(select: exp.Select) -> Iterator[exp.Sum]:
    """Les ``SUM(`` qui s'ouvrent dans cette portée, et non dans une de ses filles."""
    for valeur in select.args.values():
        for noeud in _descendre(valeur):
            if isinstance(noeud, exp.Sum):
                yield noeud


def sources_de_la_portee(select: exp.Select) -> list[exp.Expression]:
    """Le ``FROM`` de la portée et ses ``JOIN``, dans l'ordre d'écriture."""
    depart = select.args.get("from_") or select.args.get("from")
    sources = [depart.this] if depart is not None else []
    return sources + [jointure.this for jointure in select.args.get("joins") or []]


def _tables_de_la_portee(
    select: exp.Select, tables_connues: Mapping[str, str]
) -> dict[str, Table] | None:
    """Les tables de la portée, par alias — ``None`` si l'une ne se lit pas.

    L'alias est la clé parce que c'est sous lui que la requête désigne ses
    colonnes. Une table sans alias est sa propre clé : ``FROM commandes`` se
    référence ``commandes.statut``.
    """
    tables: dict[str, Table] = {}
    for source in sources_de_la_portee(select):
        if not isinstance(source, exp.Table):
            return None  # une sous-requête, un VALUES, une fonction : rien à mesurer
        nom = sans_guillemets(source.name)
        reel = tables_connues.get(nom.lower())
        if reel is None:
            return None  # une table que le schéma ne porte pas : on se tait
        alias = sans_guillemets(source.alias) or nom
        tables[alias.lower()] = Table(alias=alias, nom=reel)
    return tables or None


def _egalites(select: exp.Select, tables: dict[str, Table]) -> list[Egalite]:
    """Les égalités de colonnes qui relient les tables de cette portée.

    Les ``ON`` des jointures, et le ``WHERE`` — la jointure à l'ancienne, par
    virgules. Le ``WHERE`` est écarté dès qu'un ``OR`` le traverse : une égalité
    sous un ``OR`` n'est pas une condition de jointure, et la lire comme telle
    ferait passer pour saine une requête qui ne l'est pas.

    Un ``USING (colonne)`` relie la table qu'il suit à celle d'avant : il ne
    nomme pas les deux côtés, et c'est la seule lecture que la syntaxe permette.

    Une égalité dont un côté n'est pas une colonne qualifiée d'une table de la
    portée n'est pas retenue : ``ON t.code = (SELECT …)`` ne relie rien qu'on
    sache lire, et c'est exactement le cas qui produit le pire des résultats —
    une table qu'aucune égalité ne rattache, donc appariée à tout le reste.
    """
    trouvees: list[Egalite] = []
    ordre = list(tables.values())
    for rang, jointure in enumerate(select.args.get("joins") or [], start=1):
        trouvees += _egalites_du_texte(jointure.args.get("on"), tables)
        utilisees = jointure.args.get("using") or []
        if rang < len(ordre):
            for colonne in utilisees:
                nom = sans_guillemets(colonne.name)
                trouvees.append(
                    Egalite(ordre[rang].alias.lower(), nom, ordre[rang - 1].alias.lower(), nom)
                )
    clause = select.args.get("where")
    if clause is not None and not any(isinstance(n, exp.Or) for n in _descendre(clause)):
        trouvees += _egalites_du_texte(clause, tables)
    return trouvees


def _egalites_du_texte(noeud, tables: dict[str, Table]) -> list[Egalite]:
    """Les ``a.x = b.y`` d'un fragment, sans descendre dans une portée fille."""
    trouvees = []
    for egalite in _descendre(noeud):
        if not isinstance(egalite, exp.EQ):
            continue
        gauche, droite = egalite.this, egalite.expression
        if not isinstance(gauche, exp.Column) or not isinstance(droite, exp.Column):
            continue
        cle_g, cle_d = (
            sans_guillemets(gauche.table).lower(),
            sans_guillemets(droite.table).lower(),
        )
        if cle_g in tables and cle_d in tables:
            trouvees.append(
                Egalite(cle_g, sans_guillemets(gauche.name), cle_d, sans_guillemets(droite.name))
            )
    return trouvees


# Ce qui, dans l'argument d'une somme, est une CONDITION et non une valeur.
# ``SUM(CASE WHEN statut <> 'ANN' THEN quantite END)`` somme `quantite` ; `statut`
# y pose un filtre. Les confondre ferait juger la somme depuis la table du
# filtre, et signalerait comme multipliée une requête qui ne l'est pas.
_CONDITIONS = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
    exp.In,
    exp.Is,
    exp.Between,
    exp.Not,
    exp.And,
    exp.Or,
)


def _colonnes_sommables(noeud) -> Iterator[exp.Column]:
    """Les colonnes dont la somme prend la VALEUR, sous l'expression qui les porte.

    On descend l'argument d'une ``SUM(`` jusqu'aux colonnes, quelle que soit
    l'expression qui les enrobe — ``CASE``, ``COALESCE``, ``quantite * prix``,
    un cast. On s'arrête à une portée fille, qui se juge chez elle, et l'on
    n'entre pas dans une condition : ce qui y est lu filtre les lignes, il ne
    dit pas ce qu'on somme.
    """
    if isinstance(noeud, list):
        for element in noeud:
            yield from _colonnes_sommables(element)
        return
    if not isinstance(noeud, exp.Expression):
        return
    if isinstance(noeud, (exp.Select, exp.Subquery, *_CONDITIONS)):
        return
    if isinstance(noeud, exp.Column):
        yield noeud
        return
    if isinstance(noeud, exp.If):
        # ``CASE WHEN condition THEN valeur ELSE valeur`` : les deux branches,
        # jamais la condition — même quand celle-ci est une colonne nue.
        yield from _colonnes_sommables(noeud.args.get("true"))
        yield from _colonnes_sommables(noeud.args.get("false"))
        return
    for valeur in noeud.args.values():
        yield from _colonnes_sommables(valeur)


def _colonnes_sommees(
    sommes: list[exp.Sum], tables: dict[str, Table]
) -> list[tuple[str, str]] | None:
    """``(alias, colonne)`` de chaque colonne sommée de la portée — ``None`` au doute.

    **Chaque colonne ATTEINTE dans l'argument, et non le seul argument nu**
    (C64). La lecture d'avant exigeait ``SUM(colonne)`` et se taisait sur tout
    le reste : ``SUM(CASE WHEN … THEN quantite ELSE 0 END)`` rendait ``None``,
    la portée entière était comptée illisible, et les deux propriétés ne
    disaient plus rien. Mesuré sur « pour le VEL-01, combien on en a fabriqué
    et combien on en a vendu ? » : **141 vendus servis quand le juste est 123**
    — le chiffre sans le filtre des annulées —, sans une remarque et sans un
    avertissement. La somme d'une expression reste une somme de ses colonnes.

    Une colonne dont on ne sait pas de quelle table elle sort — non qualifiée
    alors que la portée en porte plusieurs — rend ``None`` : elle décide du
    verdict, et un verdict sur une table devinée ne vaut rien. Un argument sans
    aucune colonne (``SUM(1)``) aussi : il n'y a rien à mesurer.
    """
    sommees: list[tuple[str, str]] = []
    for somme in sommes:
        colonnes = list(_colonnes_sommables(somme.this))
        if not colonnes:
            return None  # SUM(1) : aucune colonne atteinte, rien à juger
        for argument in colonnes:
            colonne = sans_guillemets(argument.name)
            alias = sans_guillemets(argument.table).lower()
            if alias:
                if alias not in tables:
                    return None
            elif len(tables) != 1:
                return None  # colonne non qualifiée et plusieurs tables : on ne devine pas
            else:
                alias = next(iter(tables))
            if (alias, colonne) not in sommees:
                sommees.append((alias, colonne))
    return sommees or None


def _contexte(select: exp.Select) -> str:
    """La portée, plus les conditions des portées qui l'enferment.

    Ce qui restreint réellement les lignes que cette portée somme : ce qu'elle
    écrit elle-même, et les ``ON`` et ``WHERE`` de ses parents. Pas le texte
    d'une portée sœur — le filtre d'une sous-requête ne filtre pas sa voisine.
    """
    morceaux = [select.sql()]
    parent = select.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            for jointure in parent.args.get("joins") or []:
                if jointure.args.get("on") is not None:
                    morceaux.append(jointure.args["on"].sql())
            if parent.args.get("where") is not None:
                morceaux.append(parent.args["where"].sql())
        parent = parent.parent
    return "\n".join(morceaux)


# --- ① la somme multipliée par une jointure ----------------------------------


@dataclass(frozen=True)
class SommeMultipliee:
    """Ce qui a été mesuré : quelle somme, et quelle table la démultiplie."""

    sommee: str
    table_sommee: str
    multiplicatrices: tuple[str, ...]
    mesure: str

    def pour_le_modele(self) -> str:
        """Le fait rendu au modèle : ce qui est mesuré, et la forme qui répare."""
        quelles = " et ".join(f"`{t}`" for t in self.multiplicatrices)
        return (
            f"REMARQUE — ta requête somme `{self.sommee}` de `{self.table_sommee}`, et la "
            f"jointure DUPLIQUE ses lignes : {quelles} n'est reliée à `{self.table_sommee}` "
            "par aucune clé unique, donc chaque ligne de "
            f"`{self.table_sommee}` est appariée à plusieurs lignes en face et la somme est "
            f"multipliée d'autant. {self.mesure} Agrège chaque table SÉPARÉMENT — une "
            "sous-requête par table, qui groupe et somme — puis rapproche les résultats "
            "déjà agrégés. Rappelle run_sql avant de répondre."
        )

    def pour_l_utilisateur(self) -> str:
        """Ce que l'utilisateur lit quand la dernière requête multiplie encore.

        La réponse est servie — la jeter serait pire, et le tableau peut porter
        d'autres colonnes justes — mais elle n'est pas servie en silence.
        """
        quelles = " et ".join(f"`{t}`" for t in self.multiplicatrices)
        return (
            f"Avertissement sur ce calcul : la requête somme `{self.sommee}` de "
            f"`{self.table_sommee}` en la joignant à {quelles}, qu'aucune clé unique ne "
            "relie. Chaque ligne est comptée plusieurs fois : le chiffre est surévalué."
        )


def somme_multipliee(
    sql: str, tables_connues: Mapping[str, str], sonde: Sonde
) -> SommeMultipliee | None:
    """La somme de cette requête est-elle multipliée par une jointure ? — mesuré.

    ``sonde`` interroge la BASE : combien de lignes, combien de valeurs
    distinctes, pour une colonne d'une table. C'est elle qui fait la différence
    entre une table de dimension et une table qui démultiplie, et c'est la seule
    façon de la faire — aucun nom de colonne ne la porte.
    """
    for lue in lire_les_portees(sql, tables_connues).portees:
        for alias, colonne in lue.sommees:
            verdict = _multipliee(lue, alias, colonne, sonde)
            if verdict is not None:
                return verdict
    return None


def _multipliee(lue: Requete, alias: str, colonne: str, sonde: Sonde) -> SommeMultipliee | None:
    """Toutes les tables de la requête sont-elles atteintes depuis ``alias`` par une clé unique ?

    On part de la table sommée et l'on n'avance que par une clé UNIQUE du côté
    où l'on arrive : ce pas-là décore la ligne sans la dupliquer. Ce qu'on
    n'atteint jamais ainsi apparie plusieurs de ses lignes à chaque ligne
    sommée — que l'égalité manque (aucune jointure écrite : un produit
    cartésien) ou que sa clé se répète en face.
    """
    atteints = {alias}
    mesures: dict[str, Cardinalite] = {}
    avance = True
    while avance:
        avance = False
        for egalite in lue.egalites:
            for depart, colonne_cible, arrivee in (
                (egalite.gauche, egalite.colonne_droite, egalite.droite),
                (egalite.droite, egalite.colonne_gauche, egalite.gauche),
            ):
                if depart not in atteints or arrivee in atteints:
                    continue
                cardinalite = sonde(lue.tables[arrivee].nom, colonne_cible)
                if cardinalite is None:
                    return None  # la base ne sait pas répondre : on se tait
                mesures[arrivee] = cardinalite
                if not cardinalite.duplique:
                    atteints.add(arrivee)
                    avance = True
    restants = [a for a in lue.tables if a not in atteints]
    if not restants:
        return None
    dite = [
        mesures[a].en_clair(lue.tables[a].nom, _colonne_mesuree(lue, a))
        for a in restants
        if a in mesures
    ]
    return SommeMultipliee(
        sommee=colonne,
        table_sommee=lue.tables[alias].nom,
        multiplicatrices=tuple(lue.tables[a].nom for a in restants),
        mesure=f"Mesuré dans les données : {' ; '.join(dite)}."
        if dite
        else "Aucune égalité de colonnes ne les relie : le rapprochement est un produit cartésien.",
    )


def _colonne_mesuree(lue: Requete, alias: str) -> str:
    """La colonne par laquelle on a tenté d'atteindre cette table."""
    for egalite in lue.egalites:
        if egalite.gauche == alias:
            return egalite.colonne_gauche
        if egalite.droite == alias:
            return egalite.colonne_droite
    return ""


# --- ② la somme servie sans le filtre que sa source déclare ------------------


@dataclass(frozen=True)
class SommeSqlNonFiltree:
    """Une somme du SQL que la source oblige à filtrer, et le filtre qui manque."""

    source: str
    lues: str
    colonne: str
    exclure: str
    table_du_filtre: str

    def pour_le_modele(self) -> str:
        return (
            f"REMARQUE — ta requête somme {self.lues} sans écarter "
            f"`{self.colonne} = '{self.exclure}'`. Le dictionnaire de `{self.source}` impose "
            f"`{self.colonne} <> '{self.exclure}'` pour toute somme de ces colonnes. "
            f"`{self.colonne}` vit dans `{self.table_du_filtre}` : joins-la avant de sommer. "
            "Ne filtre aucun comptage : cette règle ne vaut que pour les sommes. Rappelle "
            "run_sql avant de répondre."
        )

    def pour_l_utilisateur(self) -> str:
        return (
            f"Avertissement sur ce calcul : la requête somme {self.lues} sans écarter "
            f"`{self.colonne} = '{self.exclure}'`, que le dictionnaire de `{self.source}` "
            "exclut de toute somme de ces colonnes. Le chiffre peut être surévalué."
        )


def somme_sql_sans_son_filtre(
    sql: str, tables_connues: Mapping[str, str], filtres: Sequence[FiltreMonte]
) -> SommeSqlNonFiltree | None:
    """Ce qui manque à cette requête si elle somme sans le filtre déclaré, sinon ``None``.

    Même règle que ``agents/analysis/consigne`` et même déclaration : seule la
    forme lue change — du SQL ici, du Python là-bas. La valeur à écarter est
    cherchée dans le texte de la requête ; un commentaire qui la cite fait donc
    taire la vérification, ce qui est le bon sens de l'erreur.
    """
    if not filtres:
        return None
    for lue in lire_les_portees(sql, tables_connues).portees:
        manquant = _sans_son_filtre(lue, filtres)
        if manquant is not None:
            return manquant
    return None


def _sans_son_filtre(lue: Requete, filtres: Sequence[FiltreMonte]) -> SommeSqlNonFiltree | None:
    """La même règle, pesée dans UNE portée : ses sommes, et ce qui la contraint."""
    sommees = {(lue.tables[a].nom.lower(), c.lower()) for a, c in lue.sommees}
    for monte in filtres:
        regle = monte.filtre
        if re.search(rf"\b{re.escape(regle.exclure)}\b", lue.contexte):
            continue
        concernees = [
            (monte.table(table), colonne)
            for table, colonne in (s.split(".", 1) for s in regle.sommes)
            if (monte.table(table).lower(), colonne.lower()) in sommees
        ]
        if not concernees:
            continue
        table_du_filtre, colonne_du_filtre = regle.colonne.split(".", 1)
        return SommeSqlNonFiltree(
            source=monte.source,
            lues=", ".join(f"`{c}` (sommée dans `{t}`)" for t, c in concernees),
            colonne=colonne_du_filtre,
            exclure=regle.exclure,
            table_du_filtre=monte.table(table_du_filtre),
        )
    return None


def sonde_de_l_adaptateur(adapter, schema) -> Sonde:
    """Une ``Sonde`` qui interroge la source, et ne la réinterroge jamais deux fois.

    Trois agrégats sur une colonne, et rien de plus : c'est ce que coûte la
    vérification. Les tables du croisement sont en mémoire, et une source seule
    répond en une passe d'index. Le résultat est gardé le temps de la
    récupération — la même jointure revient à chaque essai de correction.

    ``None`` dès que la question ne peut pas être posée : colonne absente du
    schéma, ou requête refusée. Le doute se rend au demandeur, qui se tait.
    """
    colonnes = {
        (table.name.lower(), colonne.name.lower()): (table.name, colonne.name)
        for table in schema.tables
        for colonne in table.columns
    }
    garde: dict[tuple[str, str], Cardinalite | None] = {}

    def sonder(table: str, colonne: str) -> Cardinalite | None:
        cle = (table.lower(), colonne.lower())
        if cle in garde:
            return garde[cle]
        reels = colonnes.get(cle)
        garde[cle] = None if reels is None else _mesurer(adapter, *reels)
        return garde[cle]

    return sonder


def _mesurer(adapter, table: str, colonne: str) -> Cardinalite | None:
    requete = f'SELECT count(*), count("{colonne}"), count(DISTINCT "{colonne}") FROM "{table}"'
    try:
        lignes = adapter.run(requete, max_rows=1).rows
    except Exception:  # une vérification n'empêche jamais de servir un résultat
        return None
    if not lignes:
        return None
    return Cardinalite(*(int(v) for v in lignes[0][:3]))
