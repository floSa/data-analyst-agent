"""Le dictionnaire d'une source va-t-il jusqu'à celui qui écrit la requête ?

Il était passé aux ontologies de l'agent SYSTÈME — celui qui répond « que
signifie cette colonne ? » — et pas à l'agent de RÉCUPÉRATION. Mesuré sur le
catalogue de démonstration, dans une seule et même conversation et à un tour
d'écart : l'agent cite « seules les sessions au statut `T` ont abouti », puis
écrit ``WHERE statut = 'E'`` et rend 1 405 au lieu de 42 281.

Pas d'exception, pas de log, un chiffre faux et plausible — la pire forme, et
celle contre laquelle un dictionnaire existe précisément. Ces tests tiennent le
chemin de bout en bout : le catalogue déclare, l'orchestrateur porte, l'agent
SQL lit.
"""

from pathlib import Path

import pytest

from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from helpers.scripted_llm import (
    PLANNER,
    RETRIEVAL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

DICTIONNAIRE = """\
# Dictionnaire — `mini`

## `mini`

| Colonne | Sens |
|---|---|
| `survie` | `1` a survécu, `0` non. |

## Les pièges de cette source

Compter les lignes compte les TENTATIVES. Seules les lignes `survie = 1`
comptent comme des survivants.
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def source(tmp_path: Path) -> FileSource:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    dico = tmp_path / "mini.md"
    dico.write_text(DICTIONNAIRE, encoding="utf-8")
    return FileSource(name="mini", path=csv, dictionary=dico)


def _llm_de_requete() -> ScriptedLLM:
    return (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE survie = 1"}),
                text("2 survivants."),
            ],
        )
        .script(SYNTHESIS, [text("Il y a 2 survivants.")])
    )


def test_le_dictionnaire_declare_arrive_a_l_agent_sql(source: FileSource):
    """Le chemin complet : YAML -> catalogue -> nœud de récupération -> prompt."""
    llm = _llm_de_requete()
    orchestrateur = Orchestrator(
        model=llm.model(), catalog=Catalog(sources=[source]), settings=make_settings()
    )
    answer = orchestrateur.ask("Combien de survivants ?")

    assert answer.error is None
    prompt = llm.systems_for(RETRIEVAL)[0]
    assert "Seules les lignes `survie = 1`" in prompt
    assert "il fait autorité" in prompt


def test_une_source_sans_dictionnaire_ne_change_rien(tmp_path: Path):
    """`titanic` et `iris` n'en déclarent pas : leur prompt doit rester intact."""
    from data_analyst_agent import prompts

    csv = tmp_path / "nu.csv"
    csv.write_text("a\n1\n", encoding="utf-8")
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="nu"))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT count(*) AS n FROM nu"}), text("1 ligne.")],
        )
        .script(SYNTHESIS, [text("Une ligne.")])
    )
    orchestrateur = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[FileSource(name="nu", path=csv)]),
        settings=make_settings(),
    )
    orchestrateur.ask("Combien de lignes ?")
    assert llm.systems_for(RETRIEVAL)[0] == prompts.render(prompts.RETRIEVAL, dialect="duckdb")


def test_un_dictionnaire_ampute_est_dit_a_l_utilisateur(source: FileSource):
    """Un dictionnaire coupé en silence remplacerait un mal par un autre.

    Même exigence que ``Orchestrator._avis_de_troncature`` pour une table
    coupée : la trace n'est pas dépliée par défaut, donc l'avis remonte
    jusqu'à la réponse.
    """
    llm = _llm_de_requete()
    orchestrateur = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[source]),
        settings=make_settings(retrieval_dictionary_max_chars=80),
    )
    answer = orchestrateur.ask("Combien de survivants ?")

    etape = next(s for s in answer.trace if s.node == "retrieval")
    assert etape.truncated
    assert "Les pièges de cette source" in etape.truncation
    assert "Dictionnaire tronqué" in answer.answer


def test_un_dictionnaire_entier_n_ajoute_aucun_avis(source: FileSource):
    llm = _llm_de_requete()
    orchestrateur = Orchestrator(
        model=llm.model(), catalog=Catalog(sources=[source]), settings=make_settings()
    )
    answer = orchestrateur.ask("Combien de survivants ?")

    etape = next(s for s in answer.trace if s.node == "retrieval")
    assert not etape.truncated
    assert "Dictionnaire tronqué" not in answer.answer
