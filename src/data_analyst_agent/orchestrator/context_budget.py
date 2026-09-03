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

    @classmethod
    def from_settings(cls, settings: Settings) -> ContextLimits:
        return cls(
            artifact_window=settings.context_artifact_window,
            token_budget=settings.context_token_budget,
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
