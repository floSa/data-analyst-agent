"""Ce qui entre dans le contexte du modèle : combien d'objets, combien de tokens.

La mémoire d'une conversation n'est **pas** son transcript — celui-ci n'est
jamais renvoyé au modèle. Ce qui remonte, c'est la liste des tableaux
intermédiaires produits au fil des tours, et cette liste-là n'avait aucun
plafond : à 100 tours, l'audit de septembre 2026 (§3.4) relevait 100 arguments
``--volume`` sur le ``docker run`` de l'analyse et ~13 000 caractères de
catalogue d'objets dans le prompt du planificateur.

Le plafond vit ici, et il est le **même pour les trois axes** qui consomment
cette mémoire (prompt du planificateur, montages de la sandbox, catalogue
effectif) : un objet décrit au planificateur mais non monté dans la sandbox — ou
l'inverse — produirait des erreurs incompréhensibles.

**Ce qu'on plafonne, c'est ce qu'on injecte, pas ce qu'on conserve.** Les objets
évincés restent sur le disque et dans le manifeste : ils appartiennent à
l'historique de la conversation, que le fil affiche intégralement.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel

from data_analyst_agent.config import Settings

# -- compteur de tokens approché ---------------------------------------------

# Rapport caractères/token retenu pour l'estimation. L'audit comptait 4, ce qui
# est la moyenne observée sur du texte anglais ; le contenu réel des prompts est
# du français accentué, des noms de colonnes et du JSON, tous plus coûteux. On
# compte donc 3, DÉLIBÉRÉMENT bas : voir la note d'exactitude ci-dessous.
CARACTERES_PAR_TOKEN = 3.0

# Chaque fragment devient un message du gabarit de chat, avec ses balises de
# rôle. Une dizaine de tokens par message, jamais comptés autrement.
SURCOUT_MESSAGE_TOKENS = 8


def estimate_tokens(*parts: str | None) -> int:
    """Estimation **prudente** du nombre de tokens de ``parts``.

    Ce n'est pas un tokeniseur : il n'y en a pas côté client, le serveur ne
    prête pas le sien, et en embarquer un ferait dépendre le plafond du modèle
    servi. On compte donc des caractères.

    **De quel côté cette estimation se trompe :** elle **surestime**, toujours.
    Mesurée contre le tokeniseur réel de ``gemma4:e4b`` (``prompt_eval_count``)
    sur des prompts de planificateur de 3 712 à 108 762 caractères, elle rend
    1,01 à 1,17 fois le compte du serveur, et jamais moins de 1,00 :

        car.   estimé   réel   estimé/réel
        3712     1246   1081         1,153
        4303     1443   1234         1,169
       10609     3545   3326         1,066
       30162    10062   9979         1,008

    C'est le sens d'erreur voulu : un compteur qui surestime coupe **trop tôt**,
    ce qui dégrade un peu la mémoire ; un compteur qui sous-estime laisse passer
    le débordement, c'est-à-dire exactement le défaut qu'on corrige. Ne pas
    remonter cette constante vers 4 sans remesurer : ce serait échanger une
    marge de sécurité contre quelques tokens.

    Les fragments vides ne comptent pas — ils ne deviennent pas un message.
    """
    total = 0
    for part in parts:
        if part:
            total += math.ceil(len(part) / CARACTERES_PAR_TOKEN) + SURCOUT_MESSAGE_TOKENS
    return total


# -- plafonds -----------------------------------------------------------------


class ContextLimits(BaseModel):
    """Les plafonds appliqués à un tour de conversation.

    Injectés dans ``ConversationWorkspace`` plutôt que lus des réglages : la
    mémoire de conversation reste testable sans environnement, et un appel
    direct (hors API) garde les défauts.
    """

    # Nombre d'objets intermédiaires réinjectés, les plus récents d'abord.
    # 0 désactive la fenêtre — le budget de tokens reste alors le seul plafond.
    artifact_window: int = 8
    # Plafond en tokens du prompt du planificateur, décompté avant l'appel.
    # 0 désactive le budget — la fenêtre reste alors le seul plafond.
    token_budget: int = 8000
    # Fenêtre réellement servie par le serveur (0 = inconnue) et rapport
    # plancher du filet : tous deux servent à CONSTATER, jamais à couper.
    model_window: int = 32768
    overflow_ratio: float = 0.4

    @classmethod
    def from_settings(cls, settings: Settings) -> ContextLimits:
        return cls(
            artifact_window=settings.context_artifact_window,
            token_budget=settings.context_token_budget,
            model_window=settings.context_model_window,
            overflow_ratio=settings.context_overflow_ratio,
        )


class ContextTrim(BaseModel):
    """Ce qui a été écarté du contexte au tour courant, et pourquoi.

    Objet de constat, pas de décision : il est produit par la mémoire de
    conversation, recopié dans la trace et rendu à l'utilisateur. Quelqu'un qui
    perd du contexte doit l'apprendre de l'application, pas le deviner à la
    qualité des réponses.
    """

    total: int = 0  # objets présents sur le disque de la conversation
    kept: int = 0  # objets effectivement réinjectés dans le contexte
    cause: str = ""  # le réglage qui a coupé, nommé en clair ("" si rien n'a été coupé)
    # Le prompt dépasse le budget même une fois TOUS les objets retirés : ce
    # n'est plus la mémoire de conversation qu'il faut couper, et le dire est
    # tout ce que cette couche peut faire.
    over_budget: bool = False

    @property
    def dropped(self) -> int:
        return self.total - self.kept

    @property
    def truncated(self) -> bool:
        return self.dropped > 0 or self.over_budget

    def message(self) -> str:
        """Ce que lit l'utilisateur quand du contexte a été coupé ("" sinon)."""
        parts = []
        if self.dropped > 0:
            parts.append(
                f"Contexte tronqué : {self.dropped} des {self.total} tableaux intermédiaires "
                f"de cette conversation ne sont plus transmis au modèle ({self.cause}). "
                "Ils restent enregistrés — le fil, lui, reste complet."
            )
        if self.over_budget:
            parts.append(
                f"Le prompt dépasse le budget ({self.cause}) même sans aucun tableau "
                "intermédiaire : la réponse peut être dégradée."
            )
        return " ".join(parts)

    def planner_notice(self) -> str:
        """Ce qu'on dit au planificateur : ne propose pas ce qu'il ne voit plus.

        Sans cette phrase, le planificateur désignerait comme source un tableau
        qui n'est plus ni au catalogue effectif ni monté dans la sandbox, et
        l'utilisateur lirait « source introuvable » sans comprendre pourquoi.
        """
        if not self.truncated:
            return ""
        return (
            f"{self.dropped} tableau(x) plus ancien(s) de cette conversation ne sont PLUS "
            "accessibles (hors de la fenêtre de contexte) : ne les propose pas comme source."
        )


# -- débordement constaté côté serveur ----------------------------------------

# Une fenêtre est « pleine » un peu avant son compte exact : Ollama rend 32 767
# pour 32 768 servis. On ne cherche pas l'égalité, on cherche le plafonnement.
PART_DE_FENETRE_PLEINE = 0.99

# Sous cet écart absolu, le filet du rapport ne se déclenche pas : sur de tout
# petits prompts, un rapport bas ne prouve rien.
ECART_MINIMAL_TOKENS = 1024


class ContextOverflow(BaseModel):
    """Un débordement **constaté** : ce qu'on a envoyé, ce que le serveur a lu."""

    estimated: int  # notre estimation, avant l'appel
    server: int  # ce que le serveur dit avoir évalué (`prompt_eval_count`)
    certain: bool  # plafonnement sur la fenêtre déclarée, ou simple soupçon

    def message(self) -> str:
        constat = (
            f"Contexte tronqué par le serveur : ~{self.estimated} tokens envoyés, "
            f"{self.server} seulement évalués"
        )
        if self.certain:
            return (
                f"{constat} — la fenêtre du modèle est pleine, la fin du prompt n'a pas "
                "été lue. Baisse DAA_CONTEXT_TOKEN_BUDGET, ou sers le modèle avec une "
                "fenêtre plus large."
            )
        return (
            f"{constat} — l'écart est trop grand pour être une imprécision d'estimation. "
            "La réponse peut être dégradée."
        )


def detect_overflow(
    estimated: int, server: int | None, limits: ContextLimits
) -> ContextOverflow | None:
    """Constate côté serveur ce que le budget calculé en amont n'a pas su prévoir.

    Le budget est une prévision ; ``prompt_eval_count`` est une mesure. Les deux
    sont nécessaires, parce qu'un serveur peut tronquer sans rien dire — mesuré
    contre ``gemma4:e4b`` : 36 262 tokens envoyés, 32 767 évalués, aucune
    erreur, réponse « Je ».

    Deux indices, dans cet ordre :

    1. **Le plafonnement** — le serveur dit avoir évalué de quoi remplir sa
       fenêtre déclarée, alors qu'on lui a envoyé davantage. C'est une preuve.
       Un rapport ne suffirait pas : dans le cas mesuré ci-dessus, le serveur a
       évalué 90 % de notre estimation, exactement ce que rend un appel SAIN.
    2. **L'écart grossier** — filet pour la fenêtre inconnue
       (``context_model_window = 0``) ou mal déclarée. Le rapport retenu est très
       en dessous de ce que l'imprécision du compteur peut expliquer.
    """
    if server is None or server <= 0 or estimated <= 0:
        return None
    fenetre = limits.model_window
    if fenetre > 0 and estimated > fenetre and server >= fenetre * PART_DE_FENETRE_PLEINE:
        return ContextOverflow(estimated=estimated, server=server, certain=True)
    ecart = estimated - server
    if ecart >= ECART_MINIMAL_TOKENS and server < estimated * limits.overflow_ratio:
        return ContextOverflow(estimated=estimated, server=server, certain=False)
    return None


# -- refus explicite (vLLM) ---------------------------------------------------

# Là où Ollama tronque en silence, vLLM répond par une erreur HTTP : un 400 dont
# le corps dit « This model's maximum context length is N tokens. However, you
# requested M tokens ». Le code doit tenir LES DEUX comportements — c'est le
# prérequis de la migration (audit §7, tâches 8-9).
STATUTS_REFUS = frozenset({400, 413, 422})
MOTIFS_REFUS = re.compile(
    r"maximum context length"
    r"|context[ _]length"
    r"|context window"
    r"|context_length_exceeded"
    r"|reduce the length"
    r"|prompt is too long",
    re.IGNORECASE,
)

CONTEXT_REFUSAL_ERROR = "le contexte envoyé au modèle dépasse sa fenêtre"
CONTEXT_REFUSAL_MESSAGE = (
    "Contexte refusé par le serveur : la requête dépasse la fenêtre du modèle, et le "
    "serveur l'a rejetée au lieu de la tronquer. Baisse DAA_CONTEXT_TOKEN_BUDGET ou "
    "DAA_CONTEXT_ARTIFACT_WINDOW, ou sers le modèle avec une fenêtre plus large."
)


def is_context_refusal(exc: BaseException) -> bool:
    """Reconnaît le refus explicite d'un serveur dont la fenêtre est dépassée.

    **Non validé contre un vrai vLLM**, et volontairement : le banc d'essai de
    la migration est une autre tâche (audit §7, 8-9), et le simuler à la légère
    donnerait une fausse assurance. Ce qui est tenu ici, c'est que le refus ne se
    perde plus dans un « Je n'ai pas pu répondre : ModelHTTPError: … » que
    personne ne saurait relier à la longueur du prompt. Le jour où le banc
    tourne, c'est cette fonction qu'il faut confronter au corps d'erreur réel.

    Le statut, quand l'exception en porte un, doit être un refus de requête :
    une panne serveur (500) qui mentionnerait « context » n'est pas un
    dépassement de fenêtre.
    """
    statut = getattr(exc, "status_code", None)
    if statut is not None and statut not in STATUTS_REFUS:
        return False
    return bool(MOTIFS_REFUS.search(f"{exc} {getattr(exc, 'body', '') or ''}"))
