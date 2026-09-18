"""Client LLM mutualisé, servi par un endpoint OpenAI-compatible.

UN SEUL modèle langage pour tout le système — routage, SQL, code, synthèse
(règle ferme, docs/CADRAGE.md §5). Les agents PydanticAI reçoivent ce modèle ;
les tests le remplacent par TestModel/FunctionModel — jamais d'appel réseau
dans la suite par défaut ni en CI.

Le serveur n'est pas nommé ici. Il expose `/v1/chat/completions` (vLLM,
docs/MOTEUR.md) et ce module ne connaît qu'une URL, une clé d'API facultative,
un délai et un nombre de réessais. Le déplacer ne touche que le `.env`.
"""

import threading

from openai import AsyncOpenAI
from pydantic_ai.models import Model, create_async_http_client
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.config import Settings, get_settings

# Le SDK OpenAI refuse une clé vide, même face à un serveur qui n'en demande
# aucune. C'est la valeur que pose déjà PydanticAI dans ce cas.
CLE_FACTICE = "api-key-not-set"

# Un modèle — donc un client HTTP, donc un pool de connexions — PAR THREAD.
# Cf. `modele_du_thread` pour la raison, qui n'apparaît que sous charge.
_modeles_par_thread = threading.local()


def build_model(settings: Settings | None = None) -> Model:
    """Construit le modèle partagé, pointé sur le serveur LLM configuré."""
    settings = settings or get_settings()
    # Client construit à la main, et non délégué au provider : `max_retries`
    # n'est réglable que sur le client OpenAI, et le laisser au défaut, c'est
    # garder les 600 s pour 3 essais que personne n'a choisis (audit §4.2).
    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or CLE_FACTICE,
        max_retries=settings.llm_max_retries,
        http_client=create_async_http_client(
            timeout=settings.llm_timeout,
            connect=min(5, settings.llm_timeout),
        ),
    )
    return OpenAIChatModel(
        settings.llm_model,
        provider=OpenAIProvider(openai_client=client),
        settings=ModelSettings(temperature=settings.llm_temperature),
    )


def _empreinte(settings: Settings) -> tuple:
    """Ce qui distingue deux modèles : tout ce que ``build_model`` lit."""
    return (
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_api_key,
        settings.llm_timeout,
        settings.llm_max_retries,
        settings.llm_temperature,
    )


def modele_du_thread(settings: Settings | None = None) -> Model:
    """Le modèle de CE thread, construit au premier besoin et gardé ensuite.

    **Pourquoi pas un seul modèle pour tout le process.** ``POST /chat`` est un
    ``def`` : Starlette le sert dans son pool de threads, et PydanticAI y appelle
    ``run_sync``, qui prend la boucle d'événements *du thread courant* et la
    laisse ouverte après coup (``_utils.get_event_loop``). Plusieurs requêtes en
    même temps, c'est donc plusieurs boucles VIVANTES à la fois — une par thread.

    Un client ``httpx.AsyncClient`` partagé entre elles garde des connexions
    ouvertes dans son pool, et rien n'empêche la boucle B de reprendre une
    connexion créée par la boucle A. Le socket porte alors un ``asyncio.Event``
    lié à A : l'attendre depuis B lève ``RuntimeError: … is bound to a different
    event loop``, que le SDK OpenAI rend en ``APIConnectionError: Connection
    error.`` — et l'utilisateur lit « la source de données n'a pas pu être
    interrogée » alors que le moteur répond parfaitement aux autres au même
    instant.

    Le défaut est INVISIBLE en séquentiel : il faut deux boucles vivantes en même
    temps pour qu'une connexion encore ouverte soit reprise par l'autre. C'est
    exactement ce qu'aucune des mesures des vingt-huit chantiers ne faisait, et
    ce que le banc de concurrence a fait apparaître.

    Un modèle par thread suffit, et c'est la plus petite chose qui suffise : la
    boucle est par thread, le client l'est donc aussi, et le pool garde son
    intérêt (les connexions restent réutilisées *dans* le thread). Le nombre de
    clients est borné par la taille du pool de threads de Starlette.

    L'empreinte des réglages est gardée avec le modèle : un thread qui sert
    successivement deux configurations — ce qui n'arrive qu'en test — n'hérite
    pas de la première.
    """
    settings = settings or get_settings()
    empreinte = _empreinte(settings)
    if getattr(_modeles_par_thread, "empreinte", None) != empreinte:
        _modeles_par_thread.modele = build_model(settings)
        _modeles_par_thread.empreinte = empreinte
    return _modeles_par_thread.modele
