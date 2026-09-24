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

**Un comptage n'est jamais touché**, par construction : les deux propriétés
exigent un ``SUM(`` au niveau zéro de la requête. « combien de commandes »
rend 180, pas 164, et ``COUNT(*)`` sur une jointure de dimension ne déclenche
rien.

**Partout où il doute, ce module se tait.** Une table qu'il ne reconnaît pas
dans le schéma, une sous-requête en guise de table, deux ``FROM`` de niveau
zéro, une colonne dont la base ne peut pas dire si elle est unique : il rend
``None``. Un doute coûte au pire le chiffre d'avant ; un faux positif coûterait
un aller-retour de modèle et pourrait pousser à corriger une requête juste.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from data_analyst_agent.agents.analysis.consigne import FiltreMonte
from data_analyst_agent.agents.retrieval.lecture import (
    fin_de_parenthese,
    masquer,
    premier_au_niveau_zero,
    profondeurs,
    toutes_au_niveau_zero,
)

_MOT_FROM = re.compile(r"\bfrom\b", re.IGNORECASE)
_MOT_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)
_MOT_OU = re.compile(r"\bor\b", re.IGNORECASE)
_SOMME = re.compile(r"\bsum\s*\(", re.IGNORECASE)

# Ce qui ferme la clause FROM dans une requête de haut niveau.
_FIN_DU_FROM = re.compile(
    r"\b(?:where|group|having|order|limit|offset|fetch|window|union|intersect|except)\b",
    re.IGNORECASE,
)
_FIN_DU_WHERE = re.compile(
    r"\b(?:group|having|order|limit|offset|fetch|window|union|intersect|except)\b",
    re.IGNORECASE,
)

# Ce qui introduit une table dans la clause FROM.
_ENTREE_DE_TABLE = re.compile(r"\b(?:from|join)\b[ \t\r\n]+", re.IGNORECASE)
_MOT_ON = re.compile(r"\bon\b", re.IGNORECASE)
_MOT_USING = re.compile(r"\busing\b[ \t\r\n]*\(", re.IGNORECASE)

_NOM = r'(?:"[^"]+"|[\w$]+)'
_TABLE = re.compile(rf"^({_NOM}(?:\.{_NOM})*)[ \t\r\n]*(?:(?i:as)[ \t\r\n]+)?({_NOM})?")
_EGALITE = re.compile(rf"({_NOM})\.({_NOM})[ \t\r\n]*=[ \t\r\n]*({_NOM})\.({_NOM})")
_COLONNE = re.compile(rf"(?:({_NOM})\.)?({_NOM})")

# Les mots qui ne peuvent pas être l'alias d'une table : ils ouvrent la suite de
# la clause. Sans cette liste, « FROM commandes JOIN … » lirait « JOIN » comme
# l'alias de `commandes`.
_PAS_UN_ALIAS = {
    "on",
    "using",
    "join",
    "inner",
    "left",
    "right",
    "full",
    "cross",
    "natural",
    "outer",
    "where",
    "group",
    "having",
    "order",
    "limit",
    "offset",
    "fetch",
    "window",
    "union",
    "intersect",
    "except",
    "lateral",
}


def _sans_guillemets(nom: str) -> str:
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
    """Ce qu'on a su lire d'une requête : ses tables, ses égalités, ses sommes.

    Construite par ``lire``, qui rend ``None`` dès qu'elle doute. Un objet ici
    veut donc dire « tout ce qui suit est lu, pas deviné ».
    """

    tables: dict[str, Table]
    egalites: list[Egalite]
    # (alias, colonne) pour chaque colonne sommée, dans l'ordre d'écriture.
    sommees: list[tuple[str, str]]


def lire(sql: str, tables_connues: Mapping[str, str]) -> Requete | None:
    """Les tables, les jointures et les sommes de cette requête — ``None`` au moindre doute.

    ``tables_connues`` vient du SCHÉMA de la source : nom en minuscules -> nom
    réel. Il sert à deux choses, et les deux comptent. Il résout la casse — le
    modèle écrit ``Commandes``, Postgres range ``commandes`` — et il ARRÊTE la
    lecture sur une table qu'il ne connaît pas : une sous-requête nommée, un
    ``VALUES``, une fonction de table n'ont pas de cardinalité à mesurer, et
    conclure sur leur compte serait conclure sur rien.
    """
    if not sql or not sql.strip() or not _SOMME.search(sql):
        return None
    masque = masquer(sql)
    niveaux = profondeurs(masque)
    departs = toutes_au_niveau_zero(_MOT_FROM, masque, niveaux)
    if len(departs) != 1:
        return None  # aucune table, ou une union : on ne sait pas lire, on ne juge pas
    debut = departs[0].start()
    arret = premier_au_niveau_zero(_FIN_DU_FROM, masque, niveaux, departs[0].end(), len(masque))
    fin = arret if arret is not None else len(masque)
    tables = _tables_du_from(masque, niveaux, debut, fin, tables_connues)
    if tables is None:
        return None
    egalites = _egalites(masque, niveaux, debut, fin, tables)
    egalites += _egalites_du_where(masque, niveaux, fin, tables)
    sommees = _colonnes_sommees(masque, niveaux, tables)
    if sommees is None:
        return None
    return Requete(tables=tables, egalites=egalites, sommees=sommees)


def _tables_du_from(
    masque: str,
    niveaux: list[int],
    debut: int,
    fin: int,
    tables_connues: Mapping[str, str],
) -> dict[str, Table] | None:
    """Les tables de la clause FROM, par alias — ``None`` si l'une ne se lit pas.

    L'alias est la clé parce que c'est sous lui que la requête désigne ses
    colonnes. Une table sans alias est sa propre clé : ``FROM commandes`` se
    référence ``commandes.statut``.
    """
    tables: dict[str, Table] = {}
    for entree in toutes_au_niveau_zero(_ENTREE_DE_TABLE, masque, niveaux, debut, fin):
        reste = masque[entree.end() : fin]
        if reste.lstrip().startswith("("):
            return None  # une sous-requête en guise de table : rien à mesurer
        trouve = _TABLE.match(reste)
        if trouve is None:
            return None
        nom = _sans_guillemets(trouve.group(1)).rsplit(".", 1)[-1]
        reel = tables_connues.get(nom.lower())
        if reel is None:
            return None  # une table que le schéma ne porte pas : on se tait
        alias = trouve.group(2)
        if alias is None or _sans_guillemets(alias).lower() in _PAS_UN_ALIAS:
            alias = nom
        tables[_sans_guillemets(alias).lower()] = Table(alias=_sans_guillemets(alias), nom=reel)
    return tables or None


def _egalites(
    masque: str, niveaux: list[int], debut: int, fin: int, tables: dict[str, Table]
) -> list[Egalite]:
    """Les égalités de colonnes écrites dans les ``ON`` de la clause FROM.

    Seulement au niveau zéro : ``ON t.code = (SELECT …)`` ne relie rien qu'on
    sache lire, et c'est exactement le cas qui produit le pire des résultats —
    une table qu'aucune égalité ne rattache, donc appariée à tout le reste.

    Un ``USING (colonne)`` relie la table qu'il suit à celle d'avant : il ne
    nomme pas les deux côtés, et c'est la seule lecture que la syntaxe permette.
    """
    trouvees: list[Egalite] = []
    for marque in toutes_au_niveau_zero(_MOT_ON, masque, niveaux, debut, fin):
        borne = premier_au_niveau_zero(_ENTREE_DE_TABLE, masque, niveaux, marque.end(), fin) or fin
        trouvees += _egalites_du_texte(masque[marque.end() : borne], tables)
    ordre = list(tables.values())
    for marque in toutes_au_niveau_zero(_MOT_USING, masque, niveaux, debut, fin):
        colonne = masque[marque.end() : fin_de_parenthese(masque, marque.end() - 1)]
        rang = sum(
            1
            for e in toutes_au_niveau_zero(_ENTREE_DE_TABLE, masque, niveaux, debut, fin)
            if e.start() < marque.start()
        )
        if rang < 2 or rang > len(ordre):
            continue
        nom = _sans_guillemets(colonne)
        if not re.fullmatch(r"[\w$]+", nom):
            continue
        trouvees.append(
            Egalite(ordre[rang - 1].alias.lower(), nom, ordre[rang - 2].alias.lower(), nom)
        )
    return trouvees


def _egalites_du_where(
    masque: str, niveaux: list[int], depuis: int, tables: dict[str, Table]
) -> list[Egalite]:
    """Les égalités de colonnes du ``WHERE`` — la jointure à l'ancienne, par virgules.

    Écartées dès qu'un ``OR`` traverse la clause : une égalité sous un ``OR``
    n'est pas une condition de jointure, et la lire comme telle ferait passer
    pour saine une requête qui ne l'est pas.
    """
    marque = premier_au_niveau_zero(_MOT_WHERE, masque, niveaux, depuis, len(masque))
    if marque is None:
        return []
    arret = premier_au_niveau_zero(_FIN_DU_WHERE, masque, niveaux, marque, len(masque))
    texte = masque[marque : arret if arret is not None else len(masque)]
    if _MOT_OU.search(texte):
        return []
    return _egalites_du_texte(texte, tables)


def _egalites_du_texte(texte: str, tables: dict[str, Table]) -> list[Egalite]:
    """Les ``a.x = b.y`` d'un fragment, au niveau zéro de CE fragment."""
    niveaux = profondeurs(texte)
    trouvees = []
    for marque in toutes_au_niveau_zero(_EGALITE, texte, niveaux):
        gauche, col_g, droite, col_d = (_sans_guillemets(g) for g in marque.groups())
        if gauche.lower() in tables and droite.lower() in tables:
            trouvees.append(Egalite(gauche.lower(), col_g, droite.lower(), col_d))
    return trouvees


def _colonnes_sommees(
    masque: str, niveaux: list[int], tables: dict[str, Table]
) -> list[tuple[str, str]] | None:
    """``(alias, colonne)`` de chaque ``SUM(colonne)`` de la requête — ``None`` au doute.

    Le ``SUM`` doit s'ouvrir au niveau zéro : celui d'une sous-requête agrège
    déjà dans son propre périmètre, et c'est précisément la forme JUSTE qu'on
    demande au modèle d'écrire. La signaler serait refuser la réparation.

    Une somme dont on ne sait pas de quelle table elle sort — une expression,
    une colonne non qualifiée alors que la requête porte plusieurs tables —
    rend ``None`` : elle décide du verdict, et un verdict sur une table devinée
    ne vaut rien.
    """
    sommees: list[tuple[str, str]] = []
    for marque in toutes_au_niveau_zero(_SOMME, masque, niveaux):
        ouvrante = masque.index("(", marque.start())
        argument = masque[ouvrante + 1 : fin_de_parenthese(masque, ouvrante)].strip()
        trouve = _COLONNE.fullmatch(argument)
        if trouve is None:
            return None  # SUM(a * b), SUM(CASE …) : on ne sait pas de qui c'est la somme
        alias, colonne = trouve.group(1), _sans_guillemets(trouve.group(2))
        if alias is not None:
            cle = _sans_guillemets(alias).lower()
            if cle not in tables:
                return None
            sommees.append((cle, colonne))
            continue
        if len(tables) != 1:
            return None  # colonne non qualifiée et plusieurs tables : on ne devine pas
        sommees.append((next(iter(tables)), colonne))
    return sommees or None


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
    lue = lire(sql, tables_connues)
    if lue is None:
        return None
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
    lue = lire(sql, tables_connues)
    if lue is None:
        return None
    sommees = {(lue.tables[a].nom.lower(), c.lower()) for a, c in lue.sommees}
    for monte in filtres:
        regle = monte.filtre
        if re.search(rf"\b{re.escape(regle.exclure)}\b", sql):
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
