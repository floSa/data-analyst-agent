"""Ce qu'un mandataire dit de l'appelant, et ce qu'on a le droit d'en croire.

Derrière une terminaison TLS, ``request.client.host`` ne désigne plus
l'appelant : il désigne le mandataire. Tout ce qui compte par adresse se met
alors à compter **la même** — et l'anti-force brute, qui verrouille au bout de
N échecs par adresse, verrouillerait tout le monde d'un coup au cinquième mot de
passe raté de n'importe qui. C'est la panne exacte que ce module évite.

L'adresse réelle voyage dans ``X-Forwarded-For``. Mais cet en-tête est posé par
un client, donc **falsifiable** : le croire sans condition, c'est offrir à un
attaquant le choix du compteur sur lequel ses échecs seront inscrits, donc
l'exemption du verrouillage. D'où la règle, qui est la seule sûre :

    on ne lit ces en-têtes QUE si le pair immédiat est un mandataire déclaré,
    et on retient la première adresse qui n'en est pas un, en remontant depuis
    la droite.

La droite, parce que c'est le mandataire le plus proche qui écrit en dernier :
``$proxy_add_x_forwarded_for`` ajoute le pair réel à la fin de ce que le client
avait envoyé. Ce que le client s'est inventé reste donc à gauche, et n'est
jamais atteint. Sans mandataire déclaré (``trusted_proxies`` vide, le défaut),
rien n'est cru et l'on rend le pair : une configuration qu'on n'a pas posée ne
peut pas nous affaiblir.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from ipaddress import ip_address, ip_network

from starlette.requests import Request

# L'appelant dont on ne sait rien : `request.client` est `None` sous certains
# transports (ASGI sans `client`, tests). Une chaîne plutôt qu'un `None`, parce
# que c'est une CLÉ de compteur : deux appelants inconnus partagent la leur, et
# c'est le comportement prudent.
ADRESSE_INCONNUE = "inconnue"

EN_TETE_POUR = "X-Forwarded-For"
EN_TETE_PROTOCOLE = "X-Forwarded-Proto"

SCHEMAS_CONNUS = frozenset({"http", "https"})


def reseaux_de_confiance(declares: Sequence[str]) -> tuple:
    """Compile les CIDR déclarés, en ignorant ce qui n'en est pas un.

    Une entrée illisible est écartée plutôt que fatale : un réglage mal tapé ne
    doit pas empêcher le service de démarrer — il doit seulement ne rien
    autoriser. Une adresse nue (``10.1.3.6``) vaut son réseau /32.
    """
    reseaux = []
    for entree in declares:
        try:
            reseaux.append(ip_network(entree.strip(), strict=False))
        except ValueError:
            continue
    return tuple(reseaux)


def _est_de_confiance(adresse: str, reseaux: Iterable) -> bool:
    try:
        ip = ip_address(adresse)
    except ValueError:
        return False
    return any(ip.version == reseau.version and ip in reseau for reseau in reseaux)


def _pair(request: Request) -> str:
    return request.client.host if request.client else ADRESSE_INCONNUE


def _chaine(request: Request) -> list[str]:
    """Les adresses VALIDES annoncées par ``X-Forwarded-For``, de gauche à droite.

    Les entrées illisibles sont retirées, et pas conservées telles quelles : ce
    sont des chaînes que l'appelant choisit, et elles finiraient en clés de
    compteur. ``unknown``, qu'autorise la RFC 7239, en fait partie.
    """
    brut = request.headers.get(EN_TETE_POUR, "")
    entrees = (morceau.strip() for morceau in brut.split(","))
    return [entree for entree in entrees if entree and _est_adresse(entree)]


def _est_adresse(entree: str) -> bool:
    try:
        ip_address(entree)
    except ValueError:
        return False
    return True


def adresse_client(request: Request, reseaux: Iterable) -> str:
    """L'adresse de l'appelant, mandataires de confiance traversés.

    Rend le pair immédiat dès qu'il n'est pas un mandataire déclaré : c'est le
    cas sans mandataire du tout, et c'est aussi ce qui empêche un appelant
    direct de se réécrire une adresse.
    """
    pair = _pair(request)
    if not _est_de_confiance(pair, reseaux):
        return pair
    # On remonte la chaîne depuis le mandataire le plus proche. Le premier
    # maillon qui n'est pas des nôtres est l'appelant : tout ce qui précède est
    # ce qu'il a bien voulu raconter.
    for candidat in reversed(_chaine(request)):
        if not _est_de_confiance(candidat, reseaux):
            return candidat
    # Toute la chaîne est de confiance — un mandataire s'est adressé à nous en
    # son nom propre, sonde de disponibilité comprise. Le pair est alors la
    # réponse honnête.
    return pair


def schema_client(request: Request, reseaux: Iterable) -> str:
    """``http`` ou ``https`` tel que vu par l'APPELANT, pas par l'application.

    L'application est en clair derrière le mandataire : son propre schéma dit
    toujours ``http`` et ne renseigne sur rien. Même règle que pour l'adresse —
    l'en-tête n'est lu que si le pair est un mandataire déclaré — et même repli.
    """
    if not _est_de_confiance(_pair(request), reseaux):
        return request.url.scheme
    annonce = request.headers.get(EN_TETE_PROTOCOLE, "").split(",")[0].strip().lower()
    return annonce if annonce in SCHEMAS_CONNUS else request.url.scheme
