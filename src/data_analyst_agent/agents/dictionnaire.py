"""Ce qu'un agent reçoit du dictionnaire de sa source, et ce qu'il en perd.

**Deux lecteurs, un seul module.** Celui qui écrit le SQL et celui qui écrit le
PYTHON ont besoin du même texte, avec la même règle de coupe et le même
plafond ; ils n'en ont pas besoin avec les mêmes verbes. La machinerie est donc
partagée — ``preparer`` taille, ``bloc_de_prompt`` colle — et l'EN-TÊTE est
passé par l'appelant : ``EN_TETE_SQL`` parle de requête et de ``WHERE``,
``EN_TETE_CODE`` de ``mean()`` et de courbe. Un en-tête unique aurait dû
choisir un vocabulaire, et il aurait parlé à côté pour l'un des deux.

Le dictionnaire était servi à l'agent SYSTÈME — celui qui répond « que signifie
cette colonne ? » — et pas à celui qui écrit la requête. Mesuré sur le catalogue
de démonstration, dans une même conversation et à un tour d'écart : l'agent cite
« seules les sessions au statut `T` ont abouti », puis écrit ``WHERE statut =
'E'`` et rend 1 405 au lieu de 42 281. Pas d'exception, pas de log — un chiffre
faux et plausible, c'est-à-dire exactement ce contre quoi un dictionnaire existe.

**Le même défaut, une seconde fois, et en pire.** Réparé pour l'agent SQL, il
restait entier pour l'agent d'ANALYSE — celui qui écrit le code exécuté dans le
bac à sable. Mesuré sur ``telemetrie`` (``puissance_kw = -1`` est une sentinelle,
``0`` une puissance vraie), « trace-moi la puissance moyenne par heure » : **0
fois sur 5** le code écarte la sentinelle, et la réponse cite 66,32 kW là où la
moyenne juste vaut 68,33. Pire que le SQL pour une raison tenant au support : une
requête fausse laisse son texte dans la conversation, où on peut la relire ligne
à ligne ; un ``df['puissance_kw'].mean()`` faux ne laisse qu'un nombre, ou une
courbe — et personne ne relit une courbe.

**Pourquoi le prompt système et pas un outil.** Un sixième outil
(``lire_le_dictionnaire``) n'aurait rien réparé : le défaut n'est pas que le
modèle cherche le sens et ne le trouve pas, c'est qu'il ne le cherche PAS — il
a écrit ``statut = 'E'`` avec assurance, 10 fois sur 10. Un outil qu'on n'appelle
pas ne dit rien, et il coûterait en plus un aller-retour sur
``retrieval_request_limit``.

**Pourquoi un budget.** Le dictionnaire est du Markdown libre : celui d'un
client peut peser dix fois celui de la démonstration, et le prompt de cet agent
est renvoyé en entier à CHAQUE aller-retour de la boucle de correction. Sans
plafond, une source bavarde ferait exploser le contexte d'un agent qui en a
déjà le plus besoin. Le plafond vit dans les réglages
(``retrieval_dictionary_max_chars``) et se compte en caractères, comme
``context_budget`` — il n'y a pas de tokeniseur côté client.

**La règle de coupe, et ce qu'elle garantit.** Elle ne coupe jamais au milieu
d'une phrase : elle garde des SECTIONS ENTIÈRES, parcourues dans l'ordre du
document, et passe son chemin quand l'une ne tient pas dans ce qui reste. Une
demi-règle est pire que pas de règle du tout — « la valeur -1 est une
sentinelle » amputé de « à écarter de toute moyenne » se lit comme une
remarque, et pas comme une consigne.

**Pourquoi elle passe son chemin au lieu de s'arrêter**, et c'est le point
mesuré : dans les cinq dictionnaires du catalogue de démonstration, les PIÈGES
sont la DERNIÈRE section. Une règle qui garderait un préfixe — les n premiers
caractères, si propres soient ses coupures — jetterait donc systématiquement la
seule partie que l'agent SQL avait besoin de lire, et garderait les fiches de
colonnes que le schéma lui donne déjà. Le remplissage glouton ne les privilégie
pas, mais il ne les condamne pas non plus.

Ce qu'elle NE garantit pas, et il faut le dire : qu'aucun sens utile n'est
perdu. Une section écartée peut être celle qui comptait, et aucune règle
mécanique ne peut le savoir — le Markdown d'un dictionnaire ne porte aucun
signal de ce qui fait autorité sur une requête, et en inventer un (chercher le
mot « piège » dans les titres) ne mesurerait que les habitudes de rédaction de
ce catalogue-ci.

C'est pourquoi l'amputation n'est jamais silencieuse — elle est écrite dans le
prompt (le modèle sait qu'il ne voit pas tout) et remontée à l'utilisateur par
la trace, sur le modèle de ``Orchestrator._avis_de_troncature``. Le défaut
réparé ici est un chiffre faux rendu sans bruit ; le remplacer par une
amputation muette l'aurait déplacé, pas corrigé.

Le plafond par défaut (8 000 caractères) est choisi AU-DESSUS du plus gros
dictionnaire du catalogue de démonstration (6 797 caractères) : les cinq passent
entiers, et la coupe est un filet, pas un régime.

**Ce que l'en-tête peut, et ce qu'il ne peut pas.** ``EN_TETE`` dit au modèle
que le dictionnaire fait autorité et qu'il ne doit filtrer que ce que le
dictionnaire prescrit pour la mesure demandée. C'est nécessaire et ce n'est pas
suffisant, et ça a été mesuré : tant que le dictionnaire de ``exploitation``
énonçait « le nombre de recharges réelles est ``WHERE statut = 'T'`` » en tête
et rangeait le contre-cas (« pour l'énergie délivrée, les ``I`` comptent ») dans
un paragraphe de fin, le modèle filtrait AUSSI les sommes d'énergie — 0 fois sur
3, sur les deux moteurs. Trois formulations d'en-tête y ont échoué, dont une
délibérément neutre : ce n'était pas la consigne, c'était le texte lu.

La leçon vaut pour toute source qu'on branche ici : **un dictionnaire qui entre
dans ce prompt a un second lecteur**, et ce lecteur-là ne lève pas les
ambiguïtés qu'une personne lève toute seule. Il doit dire quel filtre se pose
pour quelle question, et pas seulement ce que les codes veulent dire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Une section commence à un titre Markdown de niveau 1 ou 2, en début de ligne.
# Les niveaux plus profonds restent DANS leur section : « ### 1. `statut` est un
# code » est un piège, et le détacher de l'introduction qui le cadre le rendrait
# moins lisible, pas plus.
TITRE = re.compile(r"^(#{1,2}) +(.*)$", re.MULTILINE)

# Ce qu'on écrit au-dessus du dictionnaire dans le prompt de l'agent SQL. La
# consigne compte autant que le texte : recopier un Markdown descriptif sans
# dire qu'il FAIT AUTORITÉ laisse le modèle arbitrer entre ce qu'il lit et ce
# qu'il croit, et c'est l'arbitrage qu'il rate.
EN_TETE_SQL = """
DICTIONNAIRE DE LA SOURCE — il fait autorité sur le SENS des données, et il
prime sur ce que tu crois savoir. Le schéma dit les TYPES ; lui dit ce que les
valeurs VEULENT DIRE : les codes, les valeurs sentinelles, les unités, et les
jointures qui ne passent pas par la clé qu'on croit.

Avant d'écrire ta requête, lis-le EN ENTIER et cherche ce qu'il prescrit POUR
LA MESURE QU'ON TE DEMANDE. Applique ce qu'il dit, exactement ce qu'il dit :

- n'ajoute un filtre que s'il l'exige POUR CETTE MESURE-LÀ ;
- respecte ses exceptions, et lis-les jusqu'au bout — un code qu'il faut
  écarter d'un COMPTAGE peut devoir être gardé dans une SOMME, et le
  dictionnaire le dit quand c'est le cas ;
- n'invente aucune règle qu'il ne porte pas, et n'en généralise aucune d'une
  colonne à une autre ni d'une question à une autre.

ÉPREUVE À PASSER AVANT D'AJOUTER UN FILTRE : tu dois pouvoir citer la PHRASE du
dictionnaire qui le prescrit POUR LA GRANDEUR DEMANDÉE. Si la phrase que tu as
en tête parle d'une AUTRE grandeur — compter des événements quand on te demande
d'en sommer une valeur, ou l'inverse — elle ne justifie pas ce filtre-ci : ne
l'ajoute pas, et relis ce que le dictionnaire dit de la grandeur qu'on te
demande.

Un filtre oublié et un filtre de trop rendent le même genre de résultat : un
chiffre faux et plausible, que personne ne verra passer.

QUAND TU RÉPONDS AVEC CE QU'IL DIT, DIS D'OÙ ÇA VIENT. Si ta phrase énonce ce
qu'une colonne VEUT DIRE — un code, une unité, une valeur sentinelle, une
correspondance — et que tu le tiens de ce dictionnaire, écris-le : « selon le
dictionnaire de la source ». C'est ce qui distingue une lecture de CETTE
installation d'un savoir général sur des codes répandus, et l'utilisateur n'a
aucun autre moyen de faire la différence. N'écris JAMAIS cette mention pour
autre chose : un chiffre que tu as compté vient de ta requête, un type de
colonne vient du schéma, et une source sans dictionnaire n'en a aucun à citer.
""".strip()


# Le même contrat, pour celui qui écrit le PYTHON. Ce n'est pas une traduction
# de politesse : le vocabulaire du texte décide de ce que le modèle y reconnaît.
# Un en-tête qui dit « avant d'écrire ta requête » devant un agent qui n'écrit
# jamais de requête lui demande de transposer, et la transposition est
# exactement l'opération qu'il rate — il lit « à écarter de toute moyenne »,
# pense SQL, et écrit `df['x'].mean()` sans filtre.
#
# Deux choses de plus ici, qui n'ont pas lieu d'être côté SQL :
#
# - la MOYENNE est nommée avec ses cousines (`mean`, `sum`, `plot`, `describe`,
#   `resample`, `groupby`), parce que le piège d'une sentinelle se referme sur
#   n'importe laquelle et que le modèle ne généralise pas de l'une à l'autre ;
# - le GRAPHIQUE est traité pour ce qu'il est : un résultat que personne ne
#   relit. Une courbe fausse se regarde sans déplaisir, là où un tableau de
#   chiffres faux finit par se faire remarquer.
EN_TETE_CODE = """
DICTIONNAIRE DE LA SOURCE — il fait autorité sur le SENS des données, et il
prime sur ce que tu crois savoir. Le schéma et les CSV montés sous /data/
donnent les COLONNES et leurs types ; lui seul dit ce que les VALEURS veulent
dire : les codes, les valeurs sentinelles, les unités.

Avant d'écrire ton code, lis-le EN ENTIER et cherche ce qu'il prescrit POUR LA
GRANDEUR QU'ON TE DEMANDE. Applique ce qu'il dit, exactement ce qu'il dit :

- une VALEUR SENTINELLE (-1, 999, une date à 1900…) n'est pas une mesure :
  c'est l'absence de mesure, écrite dans la colonne. Elle doit être écartée
  AVANT tout calcul — mean, sum, min, max, median, describe, groupby, resample
  — et avant tout tracé. `dropna()` ne la voit pas : ce n'est pas un NaN, c'est
  un nombre, et pandas la moyennera sans broncher ;
- n'écarte QUE ce que le dictionnaire désigne comme sentinelle. Une valeur qui
  lui ressemble peut être une mesure VRAIE — un 0 qui dit « à l'arrêt » est une
  donnée, pas un trou — et la jeter fausse le résultat dans l'autre sens ;
- respecte ses exceptions, et lis-les jusqu'au bout : un code qu'il faut
  écarter d'un COMPTAGE peut devoir être gardé dans une SOMME ;
- n'invente aucune règle qu'il ne porte pas, et n'en généralise aucune d'une
  colonne à une autre.

ÉPREUVE À PASSER AVANT DE FILTRER, ET AVANT DE NE PAS FILTRER : pour chaque
colonne que ton code agrège ou trace, tu dois pouvoir citer ce que le
dictionnaire en dit. S'il la dit porteuse d'une sentinelle, ton code doit
l'écarter explicitement ; s'il n'en dit rien, ne filtre pas.

Écris ce filtre en clair dans ton code, avec un commentaire qui cite la règle :
le code est relu, la courbe ne l'est pas. Un graphique faux ne lève aucune
exception, ne déclenche aucun essai de correction, et se regarde sans déplaisir
pendant des mois.
""".strip()


@dataclass(frozen=True)
class DictionnaireInjecte:
    """Ce qui a été mis dans le prompt, et ce qui en est tombé.

    ``texte`` vide = rien à injecter (la source n'en déclare pas). ``avis`` vide
    = rien n'a été coupé ; c'est le cas nominal, et le seul où l'utilisateur n'a
    rien à apprendre.
    """

    texte: str = ""
    sections_gardees: tuple[str, ...] = ()
    sections_ecartees: tuple[str, ...] = ()
    avis: str = ""

    @property
    def tronque(self) -> bool:
        return bool(self.sections_ecartees) or bool(self.avis)


def _decouper(markdown: str) -> list[tuple[str, str]]:
    """Le document en (nom de section, texte), préambule compris.

    Le préambule — ce qui précède le premier titre — porte le nom "" : c'est la
    phrase qui décrit le modèle de données, et elle n'a pas de titre à elle.
    """
    titres = list(TITRE.finditer(markdown))
    if not titres:
        return [("", markdown)]
    sections: list[tuple[str, str]] = []
    preambule = markdown[: titres[0].start()]
    if preambule.strip():
        sections.append(("", preambule))
    for i, m in enumerate(titres):
        fin = titres[i + 1].start() if i + 1 < len(titres) else len(markdown)
        sections.append((m.group(2).strip(), markdown[m.start() : fin]))
    return sections


def preparer(markdown: str | None, budget_caracteres: int) -> DictionnaireInjecte:
    """Le dictionnaire tel qu'il entrera dans le prompt de l'agent SQL.

    ``budget_caracteres <= 0`` désactive le plafond — le dictionnaire passe
    entier, quelle que soit sa taille. C'est un choix qu'on peut faire en
    connaissance de cause (une source dont on sait le dictionnaire court, une
    fenêtre large) ; ce n'est pas le défaut.
    """
    if markdown is None or not markdown.strip():
        return DictionnaireInjecte()
    texte = markdown.strip()
    if budget_caracteres <= 0 or len(texte) <= budget_caracteres:
        return DictionnaireInjecte(texte=texte)

    sections = _decouper(texte)
    gardees: list[str] = []
    noms_gardes: list[str] = []
    noms_ecartes: list[str] = []
    reste = budget_caracteres
    for nom, corps in sections:
        if len(corps) <= reste:
            gardees.append(corps)
            noms_gardes.append(nom or "(préambule)")
            reste -= len(corps)
        else:
            noms_ecartes.append(nom or "(préambule)")
    if not gardees:
        # Une seule section, plus grosse que le budget à elle seule : on coupe
        # sur une frontière de LIGNE, faute de frontière de section. C'est le
        # seul chemin où une phrase peut manquer sa suite, et il est annoncé
        # comme les autres.
        coupe = texte[:budget_caracteres].rsplit("\n", 1)[0]
        return DictionnaireInjecte(
            texte=coupe,
            sections_ecartees=("(fin du document)",),
            avis=_avis(("(fin du document)",), budget_caracteres),
        )
    return DictionnaireInjecte(
        texte="\n".join(s.strip("\n") for s in gardees),
        sections_gardees=tuple(noms_gardes),
        sections_ecartees=tuple(noms_ecartes),
        avis=_avis(tuple(noms_ecartes), budget_caracteres),
    )


def _avis(ecartees: tuple[str, ...], budget: int) -> str:
    """Ce qu'on dit — au modèle et à l'utilisateur — quand le dictionnaire est amputé.

    Un seul texte pour les deux destinataires, et c'est voulu : ce que le
    modèle n'a pas lu est exactement ce que l'utilisateur doit savoir qu'il n'a
    pas lu.

    L'avis ne nomme PAS l'agent qui a lu, et c'est un choix : ``preparer`` taille
    le même texte pour les deux, l'utilisateur n'a pas à savoir lequel des deux
    l'a reçu tronqué, et une phrase qui dit « l'agent SQL » quand c'est le code
    d'analyse qui a été amputé serait fausse pour l'un des deux appels.
    """
    return (
        f"Dictionnaire tronqué : {len(ecartees)} section(s) non transmise(s) au "
        f"modèle — {', '.join(ecartees)} — le dictionnaire dépasse "
        f"{budget} caractères (réglage DAA_DICTIONARY_MAX_CHARS). "
        "Les règles qu'elles portent ne sont PAS appliquées au résultat."
    )


def bloc_de_prompt(injecte: DictionnaireInjecte, en_tete: str) -> str:
    """Le fragment prêt à coller au prompt système ("" s'il n'y a rien à dire).

    ``en_tete`` est passé et non choisi ici : le module ne sait pas lequel de
    ses deux lecteurs l'appelle, et un défaut le ferait deviner. Un en-tête qui
    parle de requêtes à celui qui écrit du pandas est une consigne perdue, et
    elle se perdrait en silence.
    """
    if not injecte.texte:
        return ""
    morceaux = [en_tete, injecte.texte]
    if injecte.avis:
        # Dans le prompt AUSSI : un modèle qui sait son dictionnaire incomplet
        # peut le dire dans sa réponse ; un modèle qui l'ignore affirme.
        morceaux.append(f"ATTENTION — {injecte.avis}")
    return "\n\n".join(morceaux)
