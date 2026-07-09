"""Tests du client LLM mutualisé (sans réseau : modèles de test PydanticAI)."""

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from data_analyst_agent.config import Settings
from data_analyst_agent.llm import build_model


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_build_model_utilise_les_reglages():
    settings = make_settings(llm_model="qwen-test:1b", ollama_base_url="http://serveur:11434/v1")
    model = build_model(settings)
    assert model.model_name == "qwen-test:1b"
    assert "serveur:11434" in str(model.client.base_url)


def test_temperature_transmise():
    model = build_model(make_settings(llm_temperature=0.0))
    assert model.settings["temperature"] == 0.0


def test_agent_pydantic_ai_cable_sur_un_modele_de_test():
    # Vérifie le câblage Agent <-> modèle sans aucun réseau.
    agent = Agent(TestModel(custom_output_text="pong"))
    result = agent.run_sync("ping")
    assert result.output == "pong"
