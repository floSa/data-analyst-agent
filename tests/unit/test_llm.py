"""Tests du client LLM mutualisé (sans réseau : rien n'est appelé, on inspecte).

Le moteur n'est pas nommé dans la configuration : ce qui est vérifié ici, c'est
que tout ce dont un serveur OpenAI-compatible peut avoir besoin — URL, clé
d'API, délai, réessais — arrive bien jusqu'au client HTTP. C'est la condition
pour qu'une bascule Ollama → vLLM ne soit qu'une affaire de `.env`.
"""

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from data_analyst_agent.config import Settings
from data_analyst_agent.llm import CLE_FACTICE, build_model


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_build_model_utilise_les_reglages():
    settings = make_settings(llm_model="qwen-test:1b", llm_base_url="http://serveur:11434/v1")
    model = build_model(settings)
    assert model.model_name == "qwen-test:1b"
    assert "serveur:11434" in str(model.client.base_url)


def test_temperature_transmise():
    model = build_model(make_settings(llm_temperature=0.0))
    assert model.settings["temperature"] == 0.0


def test_ancienne_url_reprise_par_build_model():
    """Un `.env` en service porte encore DAA_OLLAMA_BASE_URL : il doit marcher."""
    settings = make_settings(ollama_base_url="http://ancien:11434/v1")
    assert "ancien:11434" in str(build_model(settings).client.base_url)


def test_cle_dapi_transmise():
    """Un vLLM lancé avec --api-key rejette toute requête sans Authorization."""
    model = build_model(make_settings(llm_api_key="jeton-vllm"))
    assert model.client.api_key == "jeton-vllm"


def test_cle_dapi_vide_remplacee_par_une_factice():
    """Ollama n'en demande aucune, mais le SDK OpenAI refuse une clé vide."""
    model = build_model(make_settings(llm_api_key=""))
    assert model.client.api_key == CLE_FACTICE


def test_delai_et_reessais_transmis_au_client():
    """Les défauts du SDK (600 s, 2 réessais) retenaient un thread ~30 min."""
    model = build_model(make_settings(llm_timeout=42.0, llm_max_retries=1))
    assert model.client.timeout.read == 42.0
    assert model.client.max_retries == 1


def test_delai_court_ne_depasse_pas_le_delai_de_connexion():
    """Le délai de connexion (5 s par défaut) ne doit pas survivre au délai total."""
    model = build_model(make_settings(llm_timeout=2.0))
    assert model.client.timeout.connect == 2.0


def test_agent_pydantic_ai_cable_sur_un_modele_de_test():
    # Vérifie le câblage Agent <-> modèle sans aucun réseau.
    agent = Agent(TestModel(custom_output_text="pong"))
    result = agent.run_sync("ping")
    assert result.output == "pong"
