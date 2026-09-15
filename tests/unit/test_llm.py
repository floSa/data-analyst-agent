"""Tests du client LLM mutualisé (sans réseau : rien n'est appelé, on inspecte).

Le moteur n'est pas nommé dans la configuration : ce qui est vérifié ici, c'est
que tout ce dont un serveur OpenAI-compatible peut avoir besoin — URL, clé
d'API, délai, réessais — arrive bien jusqu'au client HTTP. C'est la condition
pour qu'une bascule Ollama → vLLM ne soit qu'une affaire de `.env`.
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from data_analyst_agent.config import Settings
from data_analyst_agent.llm import CLE_FACTICE, build_model, modele_du_thread


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
    with pytest.deprecated_call():
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


# -- un client par thread (défaut mesuré au banc de concurrence) ----------------
#
# `POST /chat` est un `def` : Starlette le sert dans son pool de threads, et
# PydanticAI y appelle `run_sync`, qui prend la boucle d'événements DU THREAD et
# la laisse ouverte. Plusieurs requêtes en même temps, c'est donc plusieurs
# boucles vivantes — et un client HTTP partagé entre elles finit par reprendre,
# depuis l'une, une connexion ouverte par l'autre.
#
# Le témoin ci-dessous le montre sur httpx directement : c'est la RAISON de
# `modele_du_thread`, et le jour où elle disparaîtra, c'est ce test-là qui le
# dira. Les suivants vérifient que notre code, lui, ne partage plus rien.


def _client_httpx(model) -> object:
    """Le client httpx réellement utilisé par un modèle PydanticAI."""
    return model.client._client


def test_temoin_un_client_partage_entre_deux_boucles_finit_par_rompre():
    """Pourquoi `modele_du_thread` existe — reproduit sans le moindre LLM.

    Deux threads, deux boucles d'événements vivantes, UN client httpx partagé :
    le second tour reprend la connexion gardée ouverte par l'autre boucle et lève
    ``RuntimeError: … is bound to a different event loop``. C'est exactement ce
    que le journal du serveur a montré sous charge, rendu à l'utilisateur en
    « Connection error. ».
    """
    import asyncio
    import http.server
    import socketserver
    import threading
    import time

    import httpx

    class Lent(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # connexion gardée ouverte : c'est le point

        def do_GET(self):
            time.sleep(0.2)  # assez pour que les deux tours se chevauchent
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            return

    serveur = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Lent)
    serveur.daemon_threads = True
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{serveur.server_address[1]}/"
    partage = httpx.AsyncClient()
    verdicts: dict[str, str] = {}
    depart = threading.Barrier(2)

    def deux_tours(nom: str) -> None:
        boucle = asyncio.new_event_loop()  # une boucle par thread, comme run_sync
        asyncio.set_event_loop(boucle)
        try:
            boucle.run_until_complete(partage.get(url))  # ouvre et garde
            depart.wait()
            boucle.run_until_complete(partage.get(url))  # reprend… celle de l'autre
            verdicts[nom] = "ok"
        except RuntimeError as rupture:
            verdicts[nom] = str(rupture)

    try:
        for _ in range(5):  # la reprise croisée n'est pas garantie à chaque tour
            verdicts.clear()
            depart = threading.Barrier(2)
            fils = [threading.Thread(target=deux_tours, args=(f"t{i}",)) for i in range(2)]
            for fil in fils:
                fil.start()
            for fil in fils:
                fil.join()
            if any("different event loop" in v for v in verdicts.values()):
                return
    finally:
        serveur.shutdown()

    pytest.skip("httpx ne partage plus ses connexions entre boucles : la raison a disparu")


def test_chaque_thread_a_son_propre_client():
    """La correction, vue de l'extérieur : deux threads, deux clients HTTP.

    C'est l'invariant qui compte — pas « deux objets modèle », mais deux POOLS
    DE CONNEXIONS : c'est le pool qui garde les sockets, et c'est le socket qui
    porte la boucle.
    """
    import threading

    reglages = make_settings(llm_base_url="http://serveur:8100/v1")
    clients: dict[str, object] = {}

    def relever(nom: str) -> None:
        clients[nom] = _client_httpx(modele_du_thread(reglages))

    fils = [threading.Thread(target=relever, args=(f"t{i}",)) for i in range(3)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()

    assert len(clients) == 3
    assert len({id(client) for client in clients.values()}) == 3


def test_le_meme_thread_garde_son_client():
    """Un client par thread, pas un client par requête : le pool garde son
    intérêt — les connexions restent réutilisées DANS le thread, là où c'est sûr.
    """
    reglages = make_settings(llm_base_url="http://serveur:8100/v1")

    premier = modele_du_thread(reglages)
    second = modele_du_thread(reglages)

    assert premier is second


def test_un_changement_de_reglages_refait_le_modele():
    """Sans ça, un thread qui a servi une configuration garderait la première —
    ce qui n'arrive qu'en test, et y rendrait la suite dépendante de son ordre.
    """
    premier = modele_du_thread(make_settings(llm_model="a:1b"))
    second = modele_du_thread(make_settings(llm_model="b:1b"))

    assert premier is not second
    assert second.model_name == "b:1b"
