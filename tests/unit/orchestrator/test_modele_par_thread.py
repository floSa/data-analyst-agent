"""L'orchestrateur ne partage pas son client LLM entre les threads.

Un orchestrateur vit pour tout le processus (``app.state.orchestrator``) et sert
les requêtes depuis le pool de threads de Starlette. Tant qu'il figeait son
modèle à la construction, tous ces threads parlaient au moteur par le MÊME
client HTTP, donc le même pool de connexions — alors que chacun a sa propre
boucle d'événements. Le banc de concurrence a fait sortir ce qui s'ensuit :
``RuntimeError: … is bound to a different event loop``, rendu à l'utilisateur en
« la source de données n'a pas pu être interrogée ».

Le mécanisme, et le témoin qui explique pourquoi il faut en arriver là, sont
dans ``tests/unit/test_llm.py``. Ici on vérifie seulement ce que l'orchestrateur
en fait : il demande le modèle DU THREAD, sauf si on lui en a imposé un.
"""

from __future__ import annotations

import threading
from typing import ClassVar

from pydantic_ai.models.test import TestModel

from data_analyst_agent.agents.retrieval.catalog import Catalog
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator


def _orchestrateur(**surcharges) -> Orchestrator:
    """Un orchestrateur sans source ni modèle ML : on ne regarde que son modèle."""
    return Orchestrator(
        settings=Settings(_env_file=None, llm_base_url="http://serveur:8100/v1"),
        catalog=Catalog(sources=[]),
        registry=_RegistreVide(),
        **surcharges,
    )


class _RegistreVide:
    """Un registre sans modèle : le charger depuis le disque n'apprendrait rien."""

    datasets: ClassVar[list[str]] = []

    def get(self, dataset: str):  # pragma: no cover - jamais appelé ici
        raise KeyError(dataset)


def test_un_modele_injecte_reste_partage():
    """C'est ce qu'un test demande en le passant, et un modèle scripté n'ouvre
    aucune connexion : rien à cloisonner."""
    scripte = TestModel(custom_output_text="pong")
    orchestrateur = _orchestrateur(model=scripte)

    modeles: list[object] = []
    fils = [threading.Thread(target=lambda: modeles.append(orchestrateur.model)) for _ in range(3)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()

    assert modeles == [scripte, scripte, scripte]


def test_sans_modele_injecte_chaque_thread_a_le_sien():
    """Le cas du service : deux threads, deux clients HTTP, deux pools."""
    orchestrateur = _orchestrateur()
    clients: list[object] = []

    def relever() -> None:
        clients.append(orchestrateur.model.client._client)

    fils = [threading.Thread(target=relever) for _ in range(3)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()

    assert len({id(client) for client in clients}) == 3


def test_le_modele_du_service_pointe_bien_les_reglages():
    """La propriété ne doit pas changer ce qu'on sert : même URL, même modèle."""
    orchestrateur = _orchestrateur()

    assert "serveur:8100" in str(orchestrateur.model.client.base_url)
