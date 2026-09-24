"""Lire un SQL sans l'analyser : masquer, compter les parenthèses, découper.

Les propriétés qu'on vérifie sur le SQL produit — `classement` pour le palmarès
qui porte ses chiffres, `verification` pour la somme multipliée et la somme non
filtrée — ne sont pas des analyseurs SQL. Elles repèrent des mots-clés HORS de
toute parenthèse et découpent des listes aux virgules de même niveau. Ce module
porte cette lecture-là, et rien d'autre.

Il est né d'un dédoublement : `classement` l'avait écrite pour lui seul, et la
seconde propriété en avait besoin au caractère près. Deux masqueurs de SQL, ce
sont deux façons de traiter un commentaire ou un littéral, donc deux verdicts
possibles sur la même requête.

**Le masque garde les positions à l'octet près.** Il sert à REPÉRER ; ce qu'on
remontre au modèle est toujours découpé dans l'original. Et il garde les
identifiants cités (``"ma colonne"``), à la différence de ``sql.mask_literals``
qui les blanchit : un ORDER BY porte couramment sur eux, et une table peut
s'écrire ``"Mes Ventes"``.
"""

from __future__ import annotations

import re


def masquer(sql: str) -> str:
    """Le SQL, littéraux de chaîne et commentaires remplacés par des blancs."""
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
            sortie.append(blanchir(sql[debut:position]))
            continue
        if sql.startswith("--", position):
            saut = sql.find("\n", position)
            borne = fin if saut == -1 else saut
            sortie.append(blanchir(sql[position:borne]))
            position = borne
            continue
        if sql.startswith("/*", position):
            ferme = sql.find("*/", position + 2)
            borne = fin if ferme == -1 else ferme + 2
            sortie.append(blanchir(sql[position:borne]))
            position = borne
            continue
        sortie.append(caractere)
        position += 1
    return "".join(sortie)


def blanchir(fragment: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in fragment)


def profondeurs(masque: str) -> list[int]:
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


def dernier_au_niveau_zero(
    motif: re.Pattern[str], masque: str, niveaux: list[int], avant: int | None = None
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
        if niveaux[occurrence.start()] == 0:
            trouvee = occurrence.start()
    return trouvee


def premier_au_niveau_zero(
    motif: re.Pattern[str], masque: str, niveaux: list[int], depuis: int, jusqu_a: int
) -> int | None:
    for occurrence in motif.finditer(masque, depuis, jusqu_a):
        if niveaux[occurrence.start()] == 0:
            return occurrence.start()
    return None


def toutes_au_niveau_zero(
    motif: re.Pattern[str], masque: str, niveaux: list[int], depuis: int = 0, jusqu_a: int = -1
) -> list[re.Match[str]]:
    """Toutes les occurrences du motif hors de toute parenthèse, dans l'ordre."""
    borne = len(masque) if jusqu_a < 0 else jusqu_a
    return [o for o in motif.finditer(masque, depuis, borne) if niveaux[o.start()] == 0]


def decouper(fragment: str, masque_du_fragment: str) -> list[tuple[str, str]]:
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
    return [c for c in (rogner(*couple) for couple in couples) if c[1].strip()]


def rogner(original: str, masque: str) -> tuple[str, str]:
    """Le couple, débarrassé de ce qui n'est que blanc DANS LE MASQUE aux deux bouts.

    C'est ainsi qu'un commentaire de fin — masqué, donc blanc — disparaît aussi
    du texte qu'on remontre, sans qu'on ait à le reconnaître une seconde fois.
    """
    debut = len(masque) - len(masque.lstrip())
    fin = len(masque.rstrip())
    return original[debut:fin], masque[debut:fin]


def normaliser(expression: str) -> str:
    """Minuscules, espaces réduits, ponctuation recollée, guillemets d'identifiant retirés.

    ``COUNT( s.id )`` et ``count(s.id)`` sont la même grandeur ; ``"code"`` et
    ``code`` sont la même colonne. La comparaison porte sur ce qui est calculé,
    pas sur la façon de l'écrire.
    """
    texte = re.sub(r"\s+", " ", expression.strip()).lower()
    texte = re.sub(r"\s*([(),.])\s*", r"\1", texte)
    return texte.replace('"', "").replace("`", "").strip()


def fin_de_parenthese(masque: str, ouvrante: int) -> int:
    """L'index de la parenthèse fermante qui répond à celle de ``ouvrante``.

    Rend la fin de la chaîne quand elle n'est jamais refermée — le SQL est alors
    invalide, le moteur le rejettera, et il n'y a rien à conclure de plus.
    """
    profondeur = 0
    for index in range(ouvrante, len(masque)):
        if masque[index] == "(":
            profondeur += 1
        elif masque[index] == ")":
            profondeur -= 1
            if profondeur == 0:
                return index
    return len(masque)
