"""Client LLM mutualisé, servi par un endpoint OpenAI-compatible.

UN SEUL modèle langage pour tout le système — routage, SQL, code, synthèse
(règle ferme, docs/CADRAGE.md §5). Les agents PydanticAI reçoivent ce modèle ;
les tests le remplacent par TestModel/FunctionModel — jamais d'appel réseau
dans la suite par défaut ni en CI.

Le moteur n'est pas nommé ici. `/v1/chat/completions` est servi aussi bien par
Ollama (le moteur en service) que par vLLM (la cible, docs/VLLM.md) : ce module
ne connaît qu'une URL, une clé d'API facultative, un délai et un nombre de
réessais. Basculer de l'un à l'autre ne touche que le `.env`.
"""

from openai import AsyncOpenAI
from pydantic_ai.models import Model, create_async_http_client
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.config import Settings, get_settings

# Le SDK OpenAI refuse une clé vide, même face à un serveur qui n'en demande
# aucune. C'est la valeur que pose déjà PydanticAI dans ce cas.
CLE_FACTICE = "api-key-not-set"


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
