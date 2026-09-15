"""Une panne interne ne se raconte pas à l'utilisateur (audit §5.1, §6.3).

`_guarded` mettait `f"{type(exc).__name__}: {exc}"` dans `error` et la synthèse
l'affichait tel quel. Mesuré contre un Postgres injoignable, le message rendu
était : « InterfaceError: (pg8000.exceptions.InterfaceError) Can't create a
connection to host 127.0.0.1 and port 65432… » — l'hôte et le port de la base.

Ce que ces tests fixent : la réponse porte une phrase et une référence, la
trace et les logs portent le détail, et les trois se recoupent par la référence.

Fichier séparé de ``test_graph.py`` à dessein (audit §8.3 : la branche Maxizoo
le réécrit).
"""

import logging
import re
from pathlib import Path

import joblib
import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import (
    ERREUR_UTILISATEUR_PAR_DEFAUT,
    ERREURS_UTILISATEUR,
    Orchestrator,
    reference_dincident,
)
from data_analyst_agent.orchestrator.plan import Plan
from helpers.doubles import FakeClassifier
from helpers.scripted_llm import PLANNER, RETRIEVAL, ScriptedLLM, plan_response, tool_call

# Le message exact qui fuyait : hôte et port de la base, en clair.
FUITE_POSTGRES = (
    "(pg8000.exceptions.InterfaceError) Can't create a connection to host "
    "127.0.0.1 and port 65432 (timeout is None and source_address is None)"
)

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
    labels: {"0": "non", "1": "oui"}
"""


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nm,0\n", encoding="utf-8")
    return csv


def orchestrateur_qui_tombe(mini_csv: Path, registry: Registry, monkeypatch) -> Orchestrator:
    """Un orchestrateur dont la source lève l'erreur qui fuyait."""

    def source_injoignable(_source):
        raise RuntimeError(FUITE_POSTGRES)

    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.open_source", source_injoignable)
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(RETRIEVAL, [tool_call("run_sql", {"query": "SELECT 1"})])
    )
    return Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]),
        registry=registry,
        settings=Settings(_env_file=None),
    )


def test_lhote_et_le_port_de_la_base_ne_sortent_pas(
    mini_csv: Path, registry: Registry, monkeypatch
):
    reponse = orchestrateur_qui_tombe(mini_csv, registry, monkeypatch).ask("Combien de lignes ?")

    assert "127.0.0.1" not in reponse.answer
    assert "65432" not in reponse.answer
    assert "pg8000" not in reponse.answer
    assert "RuntimeError" not in reponse.answer
    assert "127.0.0.1" not in (reponse.error or "")
    assert "RuntimeError" not in (reponse.error or "")


def test_le_message_dit_quelle_etape_a_echoue(mini_csv: Path, registry: Registry, monkeypatch):
    """Normalisé n'est pas muet : « une erreur est survenue » n'aide personne."""
    reponse = orchestrateur_qui_tombe(mini_csv, registry, monkeypatch).ask("Combien de lignes ?")

    assert ERREURS_UTILISATEUR["retrieval"] in reponse.error
    assert reponse.answer.startswith("Je n'ai pas pu répondre")


def test_la_trace_garde_le_detail_technique(mini_csv: Path, registry: Registry, monkeypatch):
    """Masquer n'est pas perdre : l'exploitant a besoin de la cause exacte."""
    reponse = orchestrateur_qui_tombe(mini_csv, registry, monkeypatch).ask("Combien de lignes ?")

    etape = next(step for step in reponse.trace if step.node == "retrieval")
    assert "RuntimeError" in etape.detail
    assert "65432" in etape.detail


def test_la_reference_relie_la_reponse_la_trace_et_les_logs(
    mini_csv: Path, registry: Registry, monkeypatch, caplog
):
    """Sans référence commune, l'utilisateur dit « ça n'a pas marché » et
    l'exploitant cherche dans les logs de la journée."""
    with caplog.at_level(logging.ERROR):
        reponse = orchestrateur_qui_tombe(mini_csv, registry, monkeypatch).ask("Combien ?")

    trouvee = re.search(r"incident ([0-9a-f]{8})", reponse.error or "")
    assert trouvee is not None
    reference = trouvee.group(1)
    etape = next(step for step in reponse.trace if step.node == "retrieval")
    assert reference in etape.detail
    assert reference in caplog.text


def test_le_traceback_complet_part_dans_les_logs(
    mini_csv: Path, registry: Registry, monkeypatch, caplog
):
    with caplog.at_level(logging.ERROR):
        orchestrateur_qui_tombe(mini_csv, registry, monkeypatch).ask("Combien ?")

    assert "Traceback" in caplog.text
    assert "65432" in caplog.text


def test_chaque_noeud_a_son_message():
    """Un message par nœud : « la source n'a pas répondu » et « l'analyse n'a pas
    abouti » n'appellent pas la même réaction de l'utilisateur."""
    noeuds = {
        "plan",
        "retrieval",
        "analysis",
        "inference",
        "fetch_predict",
        "system",
        "rappel",
        "synthesize",
    }

    assert set(ERREURS_UTILISATEUR) == noeuds
    assert len(set(ERREURS_UTILISATEUR.values())) == len(noeuds)  # aucun doublon
    for message in [*ERREURS_UTILISATEUR.values(), ERREUR_UTILISATEUR_PAR_DEFAUT]:
        assert message == message.lower()[0] + message[1:]  # s'enchaîne après « : »
        assert not message.endswith(".")


def test_deux_incidents_ne_portent_pas_la_meme_reference():
    assert reference_dincident() != reference_dincident()
    assert re.fullmatch(r"[0-9a-f]{8}", reference_dincident())
