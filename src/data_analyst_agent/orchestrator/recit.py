"""Ce qu'une réponse dit de SES PROPRES ÉTAPES, et qu'on ne sert pas.

Le résumé d'une récupération est le DERNIER message d'un agent à outils : il est
écrit après une boucle d'appels, et il lui arrive de la raconter. Relevé le
2026-09-23, catalogue métier, sur « est-ce qu'on vend plus que ce qu'on
produit ? » — une réponse par ailleurs JUSTE, chiffres compris, qui s'ouvre par :

    « Je m'excuse pour la confusion. J'ai déjà exécuté les deux requêtes
      nécessaires dans mes étapes précédentes. »

L'utilisateur n'a demandé aucune requête, n'en a vu aucune, et n'a rien à
excuser : la boucle interne n'est pas son affaire.

**On coupe, on n'ordonne pas.** Une consigne de plus dans le prompt de l'agent
SQL aurait tenu sur la phrase qu'on lui aurait montrée, et l'empreinte des sept
prompts et des huit fiches d'outils n'aurait plus rien attesté. Ce qui est servi,
en revanche, se lit : on retire de la TÊTE du texte les phrases qui ne parlent
que du déroulé, et rien d'autre.

**Deux garde-fous, parce que le remède serait pire que le mal.**

1. *Un chiffre est un fait, et on ne coupe jamais un fait.* Une phrase qui porte
   un nombre reste, même si elle raconte une requête : « j'ai exécuté une requête
   qui rend 1 828 unités » porte 1 828, et 1 828 est la réponse.
2. *En TÊTE seulement, et jamais tout.* On s'arrête à la première phrase qui
   n'est pas du récit — couper au milieu reviendrait à recomposer la réponse du
   modèle — et si tout le texte est du récit, on le rend intact : une réponse
   vide serait une régression, pas une correction.
"""

from __future__ import annotations

import re

# Une phrase se termine sur un point, un « ! » ou un « ? », suivis d'un blanc.
# La découpe garde le séparateur : ce qui n'est pas coupé doit ressortir au
# caractère près.
_PHRASE_RE = re.compile(r"(?<=[.!?])\s+")

# Qui parle : le modèle de lui-même. Sans ce marqueur, rien n'est coupé — « les
# deux requêtes nécessaires ont été exécutées » décrit le travail, pas le
# narrateur, et une réponse qui décrit son travail n'est pas ce qu'on retire.
_PREMIERE_PERSONNE_RE = re.compile(r"\b(?:je|j'|me|m'|mes|mon|ma)\b|\bj'", re.IGNORECASE)

# De quoi il parle : de la mécanique du tour. `étape`, `requête`, `outil` et
# `exécuter` sont les mots par lesquels la boucle interne remonte ; `excuser` et
# `désolé` sont l'autre forme du même défaut — le modèle s'adresse à lui-même
# devant l'utilisateur.
_MECANIQUE_RE = re.compile(
    r"\b(?:étapes?|requêtes?|outils?|tentatives?|appels?"
    r"|exécut\w*|excuse\w*|désolée?|confusion|précédent\w*|précédemment)\b",
    re.IGNORECASE,
)

_CHIFFRE_RE = re.compile(r"\d")


def _est_du_recit(phrase: str) -> bool:
    """La phrase ne parle-t-elle QUE du déroulé ? — premier·e personne, mécanique, zéro chiffre."""
    if _CHIFFRE_RE.search(phrase):
        return False
    return bool(_PREMIERE_PERSONNE_RE.search(phrase) and _MECANIQUE_RE.search(phrase))


def sans_le_recit_des_etapes(texte: str) -> str:
    """Le texte sans les phrases de tête qui ne racontent que la boucle interne.

    Rendu inchangé quand il n'y a rien à couper — c'est le cas de l'écrasante
    majorité des tours — et inchangé aussi quand TOUT serait coupé.
    """
    phrases = _PHRASE_RE.split(texte.strip())
    garde = 0
    while garde < len(phrases) and _est_du_recit(phrases[garde]):
        garde += 1
    if garde == 0 or garde == len(phrases):
        return texte
    return " ".join(phrases[garde:])
