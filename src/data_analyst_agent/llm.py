"""Client LLM mutualisé : Qwen3-Coder servi par Ollama (endpoint OpenAI-compatible).

UN SEUL modèle langage pour tout le système — routage, SQL, code, synthèse
(règle ferme, docs/CADRAGE.md §5). Les agents PydanticAI reçoivent ce modèle ;
les tests le remplacent par TestModel/FunctionModel — jamais d'appel réseau
dans la suite par défaut ni en CI.
"""

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.config import Settings, get_settings


def build_model(settings: Settings | None = None) -> Model:
    """Construit le modèle partagé, pointé sur le serveur Ollama local."""
    settings = settings or get_settings()
    return OpenAIChatModel(
        settings.llm_model,
        provider=OllamaProvider(base_url=settings.ollama_base_url),
        settings=ModelSettings(temperature=settings.llm_temperature),
    )
