"""Un palmarès porte la grandeur qui l'ordonne — vérifié sur le SQL, pas sur la phrase.

Le défaut : « les trois stations avec le plus de sessions ? donne leur code et
leur nom » rendait ``SELECT code, nom … ORDER BY COUNT(*) DESC LIMIT 3``. Les
stations et leur ordre étaient justes, le compte n'était nulle part — un
palmarès sans ses chiffres ne dit pas de combien le premier devance le second,
et ne se vérifie pas.

La première réparation était une ligne de prompt qui ÉNUMÉRAIT des tournures
(« les trois plus… », « le plus gros… », « classe par… »). Elle tenait sur la
phrase qu'on avait écrite et lâchait sur « les plus sollicitées », qui n'y
figurait pas. Ce produit a déjà payé ce pari une fois : le lexique de mots-clés
du planificateur plafonnait à 3 tournures sur 10 et a dû être remplacé.

Ce module dit la même chose SANS regarder la question, en une propriété du SQL
produit : **toute expression du ORDER BY figure aussi dans le SELECT**. La
propriété est vraie ou fausse quelle que soit la langue, la tournure, ou le
modèle qui a écrit la requête.

Ce que ce module ne sait pas faire, et l'assume : ce n'est pas un analyseur SQL
complet. Il masque les littéraux et les commentaires, compte les parenthèses,
et ne regarde que le niveau zéro — une sous-requête, un ``OVER (…)`` ou un
``STRING_AGG(… ORDER BY …)`` vivent entre parenthèses et sont ignorés. Partout
où il doute, il conclut « projetée » : un doute coûte au pire un palmarès sans
ses chiffres, comme avant, là qu'un faux positif coûterait un aller-retour de
modèle à chaque requête ordonnée du produit.
"""

from __future__ import annotations

import re

# Ce qui ferme la liste du ORDER BY dans une requête de haut niveau.
_FINS_DE_ORDER_BY = ("limit", "offset", "fetch", "for", "union", "intersect", "except", "window")

_MOT_SELECT = re.compile(r"\bselect\b", re.IGNORECASE)
_MOT_FROM = re.compile(r"\bfrom\b", re.IGNORECASE)
_MOT_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)
_FIN_DE_LISTE = re.compile(rf"\b(?:{'|'.join(_FINS_DE_ORDER_BY)})\b", re.IGNORECASE)

_SENS_DE_TRI = re.compile(
    r"\s+(?:asc|desc)\b|\s+nulls\s+(?:first|last)\b|\s+using\s+\S+$", re.IGNORECASE
)
_ALIAS_EXPLICITE = re.compile(r"\s+as\s+(\"[^\"]+\"|`[^`]+`|[\w$]+)\s*$", re.IGNORECASE)
_ALIAS_NU = re.compile(r"^(.*[\w$)\"`])\s+(\"[^\"]+\"|`[^`]+`|[a-z_][\w$]*)\s*$", re.IGNORECASE)
_TETE_DE_LISTE = re.compile(r"^\s*(?:distinct(?:\s+on\s*\([^)]*\))?|all)\s+", re.IGNORECASE)
_REFERENCE_SIMPLE = re.compile(r"^[\w$]+(?:\.[\w$]+)*$")
_ORDINAL = re.compile(r"^\d+$")


def grandeurs_non_projetees(sql: str) -> list[str]:
    """Les expressions du ORDER BY absentes du SELECT — vides si le SQL est en règle.

    Rend la liste dans l'ordre du ORDER BY, telle qu'elle y est écrite (pas
    normalisée) : c'est ce qu'on remontre au modèle, et lui remontrer sa propre
    écriture lui évite d'avoir à reconnaître la nôtre.
    """
    if not sql or not sql.strip():
        return []
    masque = _masquer(sql)
    profondeurs = _profondeurs(masque)
    debut_order_by = _dernier_au_niveau_zero(_MOT_ORDER_BY, masque, profondeurs)
    if debut_order_by is None:
        return []  # pas de classement : rien à vérifier
    debut_select = _dernier_au_niveau_zero(_MOT_SELECT, masque, profondeurs, avant=debut_order_by)
    if debut_select is None:
        return []  # pas de SELECT au niveau zéro : on ne sait pas lire, on ne juge pas

    projetees = _liste_projetee(sql, masque, profondeurs, debut_select, debut_order_by)
    if projetees is None:  # SELECT * : tout est projeté
        return []
    triees = _liste_de_tri(sql, masque, profondeurs, debut_order_by)
    return [ecrit for ecrit, couvert in triees if not _est_projetee(couvert, projetees)]


# --- lecture du SQL ----------------------------------------------------------


def _masquer(sql: str) -> str:
    """Le SQL, littéraux de chaîne et commentaires remplacés par des blancs.

    Les identifiants cités (``"ma colonne"``, `` `ma colonne` ``) sont
    CONSERVÉS, à la différence de ``sql.mask_literals`` : un ORDER BY porte
    couramment sur eux, et les blanchir ferait disparaître la moitié de la
    comparaison. Les positions sont préservées à l'octet près — le masque sert
    à REPÉRER, le texte rendu au lecteur est découpé dans l'original.
    """
    sortie: list[str] = []
    position = 0
    fin = len(sql)
    while position < fin:
        caractere = sql[position]
        if caractere == "'":
            debut = position
            position += 1
            while position < fin:
                if sql[position] == "'":
                    if sql[position + 1 : position + 2] == "'":
                        position += 2
                        continue
                    position += 1
                    break
                position += 1
            sortie.append(_blanchir(sql[debut:position]))
            continue
        if sql.startswith("--", position):
            saut = sql.find("\n", position)
            borne = fin if saut == -1 else saut
            sortie.append(_blanchir(sql[position:borne]))
            position = borne
            continue
        if sql.startswith("/*", position):
            ferme = sql.find("*/", position + 2)
            borne = fin if ferme == -1 else ferme + 2
            sortie.append(_blanchir(sql[position:borne]))
            position = borne
            continue
        sortie.append(caractere)
        position += 1
    return "".join(sortie)


def _blanchir(fragment: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in fragment)


def _profondeurs(masque: str) -> list[int]:
    """Pour chaque position, le nombre de parenthèses ouvertes AVANT elle."""
    niveaux: list[int] = []
    courant = 0
    for caractere in masque:
        if caractere == ")":
            courant = max(0, courant - 1)
        niveaux.append(courant)
        if caractere == "(":
            courant += 1
    return niveaux


def _dernier_au_niveau_zero(
    motif: re.Pattern[str], masque: str, profondeurs: list[int], avant: int | None = None
) -> int | None:
    """La dernière occurrence du motif hors de toute parenthèse (``None`` si aucune).

    LA DERNIÈRE, et non la première : ``SELECT a UNION SELECT b ORDER BY c``
    porte deux SELECT au niveau zéro, et c'est le dernier qui décrit les
    colonnes rendues.
    """
    trouvee = None
    for occurrence in motif.finditer(masque):
        if avant is not None and occurrence.start() >= avant:
            break
        if profondeurs[occurrence.start()] == 0:
            trouvee = occurrence.start()
    return trouvee


def _premier_au_niveau_zero(
    motif: re.Pattern[str], masque: str, profondeurs: list[int], depuis: int, jusqu_a: int
) -> int | None:
    for occurrence in motif.finditer(masque, depuis, jusqu_a):
        if profondeurs[occurrence.start()] == 0:
            return occurrence.start()
    return None


def _decouper(fragment: str, masque_du_fragment: str) -> list[tuple[str, str]]:
    """Le fragment coupé aux virgules de niveau zéro, chaque morceau avec son masque.

    Deux textes et non un : **toute comparaison porte sur le masque**, où un
    commentaire et un littéral ne sont plus que des blancs, et l'original ne
    sert qu'à REMONTRER au modèle ce qu'il a écrit. Les avoir confondus faisait
    lire « total -- puis ORDER BY ruse » comme une grandeur.
    """
    couples: list[tuple[str, str]] = []
    depart = 0
    profondeur = 0
    for index, caractere in enumerate(masque_du_fragment):
        if caractere == "(":
            profondeur += 1
        elif caractere == ")":
            profondeur = max(0, profondeur - 1)
        elif caractere == "," and profondeur == 0:
            couples.append((fragment[depart:index], masque_du_fragment[depart:index]))
            depart = index + 1
    couples.append((fragment[depart:], masque_du_fragment[depart:]))
    return [c for c in (_rogner(*couple) for couple in couples) if c[1].strip()]


def _rogner(original: str, masque: str) -> tuple[str, str]:
    """Le couple, débarrassé de ce qui n'est que blanc DANS LE MASQUE aux deux bouts.

    C'est ainsi qu'un commentaire de fin — masqué, donc blanc — disparaît aussi
    du texte qu'on remontre, sans qu'on ait à le reconnaître une seconde fois.
    """
    debut = len(masque) - len(masque.lstrip())
    fin = len(masque.rstrip())
    return original[debut:fin], masque[debut:fin]


# --- les deux listes qu'on compare -------------------------------------------


def _liste_projetee(
    sql: str, masque: str, profondeurs: list[int], debut_select: int, fin: int
) -> set[str] | None:
    """Les formes normalisées que le SELECT rend — ``None`` quand il rend tout (``*``)."""
    apres_select = debut_select + len("select")
    borne = _premier_au_niveau_zero(_MOT_FROM, masque, profondeurs, apres_select, fin) or fin
    # `DISTINCT` / `ALL` retirés sur le MASQUE, et la même longueur coupée dans
    # l'original : deux `sub` indépendants pourraient couper deux longueurs
    # différentes et désaligner le découpage qui suit.
    masque_liste = masque[apres_select:borne]
    tete = _TETE_DE_LISTE.match(masque_liste)
    saut = tete.end() if tete else 0
    liste = sql[apres_select + saut : borne]
    masque_liste = masque_liste[saut:]

    formes: set[str] = set()
    for _, item in _decouper(liste, masque_liste):
        normalise = _normaliser(item)
        if normalise == "*" or normalise.endswith(".*"):
            return None
        formes.add(normalise)
        # Un alias vaut pour l'expression qu'il nomme : le ORDER BY peut
        # désigner l'un ou l'autre, et les deux sont projetés.
        for expression, alias in _expression_et_alias(item):
            formes.add(_normaliser(expression))
            formes.add(_normaliser(alias))
    return formes


def _expression_et_alias(item: str) -> list[tuple[str, str]]:
    """``(expression, alias)`` si l'item en porte un — liste vide sinon.

    Deux écritures : ``COUNT(*) AS n`` et ``COUNT(*) n``. La seconde est
    devinée, et peut se tromper sur une expression à plusieurs mots — auquel
    cas on ajoute une forme de trop au SELECT, donc on signale moins. C'est le
    bon sens de l'erreur : un faux positif coûterait un aller-retour de modèle.
    """
    explicite = _ALIAS_EXPLICITE.search(item)
    if explicite is not None:
        return [(item[: explicite.start()], explicite.group(1))]
    nu = _ALIAS_NU.match(item)
    if nu is not None:
        return [(nu.group(1), nu.group(2))]
    return []


def _liste_de_tri(
    sql: str, masque: str, profondeurs: list[int], debut: int
) -> list[tuple[str, str]]:
    """Les expressions du ORDER BY, sens de tri retiré — (telle qu'écrite, masquée)."""
    apres = debut + len(_MOT_ORDER_BY.match(masque, debut).group(0))  # type: ignore[union-attr]
    arret = _premier_au_niveau_zero(_FIN_DE_LISTE, masque, profondeurs, apres, len(masque))
    borne = arret if arret is not None else len(masque)
    longueur = len(masque[apres:borne].rstrip().rstrip(";"))
    fragment = sql[apres : apres + longueur]
    masque_fragment = masque[apres : apres + longueur]
    termes = []
    for original, couvert in _decouper(fragment, masque_fragment):
        # Le sens de tri est repéré sur le masque et coupé sur les DEUX, à la
        # même longueur : un « DESC » ne vit jamais dans un commentaire, mais
        # couper deux fois séparément désalignerait les textes.
        reste = len(_SENS_DE_TRI.sub("", couvert).rstrip())
        termes.append((original[:reste].strip(), couvert[:reste].strip()))
    return termes


def _est_projetee(terme: str, projetees: set[str]) -> bool:
    """Le terme de tri figure-t-il dans le SELECT, sous une forme ou une autre ?"""
    normalise = _normaliser(terme)
    if not normalise or _ORDINAL.match(normalise):
        return True  # ORDER BY 2 : le tri DÉSIGNE une colonne projetée
    if normalise in projetees:
        return True
    # Une référence de colonne se compare sur son dernier segment : `s.code`
    # dans le ORDER BY et `code` dans le SELECT sont la même colonne. Deux
    # tables portant le même nom de colonne les confondraient — on préfère ce
    # doute-là, qui ne coûte rien, à un aller-retour de modèle réclamé à tort.
    if _REFERENCE_SIMPLE.match(normalise):
        court = normalise.rsplit(".", 1)[-1]
        return any(
            _REFERENCE_SIMPLE.match(forme) and forme.rsplit(".", 1)[-1] == court
            for forme in projetees
        )
    return False


def _normaliser(expression: str) -> str:
    """Minuscules, espaces réduits, ponctuation recollée, guillemets d'identifiant retirés.

    ``COUNT( s.id )`` et ``count(s.id)`` sont la même grandeur ; ``"code"`` et
    ``code`` sont la même colonne. La comparaison porte sur ce qui est calculé,
    pas sur la façon de l'écrire.
    """
    texte = re.sub(r"\s+", " ", expression.strip()).lower()
    texte = re.sub(r"\s*([(),.])\s*", r"\1", texte)
    return texte.replace('"', "").replace("`", "").strip()
