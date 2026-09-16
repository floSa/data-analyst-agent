"""Le dictionnaire d'une source va-t-il jusqu'à celui qui écrit le PYTHON ?

Suite de ``test_dictionnaire_jusqu_au_sql`` : le même défaut, à l'agent
suivant. Le dictionnaire atteignait l'agent SQL et s'arrêtait là ; l'agent
d'ANALYSE recevait des CSV, un schéma, et rien de ce que les valeurs veulent
dire.

**Et c'est pire ici**, pour une raison qui tient au support du résultat. Une
requête fausse laisse son texte dans la conversation : on la relit, on voit le
``WHERE`` manquant. Un ``df['puissance_kw'].mean()`` faux ne laisse qu'un nombre
— ou une courbe, et personne ne relit une courbe. Mesuré sur `telemetrie` avant
la réparation : 0 fois sur 5 le code écartait la sentinelle ``-1``, et la
réponse citait 66,32 kW pour une moyenne juste de 68,33.

Ces tests tiennent le chemin de bout en bout : le catalogue déclare,
l'orchestrateur porte, l'agent d'analyse lit — et l'amputation, quand il y en a
une, remonte à l'utilisateur.
"""

from pathlib import Path

import duckdb
import pytest

from data_analyst_agent import prompts
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource, PostgresSource
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.sandbox.client import SandboxResult
from helpers.doubles import ScriptedSandbox
from helpers.scripted_llm import ANALYSIS, PLANNER, SYNTHESIS, ScriptedLLM, plan_response, text

DICTIONNAIRE = """\
# Dictionnaire — `releves`

## `releves`

| Colonne | Sens |
|---|---|
| `puissance_kw` | Puissance mesurée, en kilowatts. |

## Les pièges de cette source

`-1` est une valeur SENTINELLE : le compteur n'a rien remonté. À écarter de
toute moyenne. `0` est une mesure vraie — la borne est à l'arrêt — et se garde.
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def source(tmp_path: Path) -> FileSource:
    csv = tmp_path / "releves.csv"
    csv.write_text("borne,puissance_kw\n1,50\n2,0\n3,-1\n", encoding="utf-8")
    dico = tmp_path / "releves.md"
    dico.write_text(DICTIONNAIRE, encoding="utf-8")
    return FileSource(name="releves", path=csv, dictionary=dico)


def _llm_danalyse() -> ScriptedLLM:
    return (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="releves"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Voici la moyenne.")])
    )


def _orchestrateur(llm: ScriptedLLM, source: FileSource, **reglages) -> Orchestrator:
    return Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[source]),
        settings=make_settings(**reglages),
        sandbox=ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n")]),
    )


def test_le_dictionnaire_declare_arrive_a_l_agent_d_analyse(source: FileSource):
    """Le chemin complet : YAML -> catalogue -> nœud d'analyse -> prompt système."""
    llm = _llm_danalyse()
    answer = _orchestrateur(llm, source).ask("Trace-moi la puissance moyenne.")

    assert answer.error is None
    prompt = llm.systems_for(ANALYSIS)[0]
    assert "valeur SENTINELLE" in prompt
    assert "il fait autorité" in prompt


def test_le_dictionnaire_est_dans_le_prompt_systeme_et_pas_dans_le_message(source: FileSource):
    """Là où la boucle de correction le renverra INTACT à chaque essai.

    Le message du tour, lui, est remplacé dès le deuxième essai par la trace
    d'erreur : un dictionnaire posé là disparaîtrait exactement au moment où le
    modèle réécrit son code, c'est-à-dire quand il peut encore corriger son
    filtre.
    """
    llm = _llm_danalyse()
    _orchestrateur(llm, source).ask("Trace-moi la puissance moyenne.")

    assert "valeur SENTINELLE" in llm.systems_for(ANALYSIS)[0]


def test_l_en_tete_parle_de_code_et_non_de_requete(source: FileSource):
    """Deux lecteurs, deux vocabulaires — la machinerie seule est partagée.

    Un en-tête qui dit « avant d'écrire ta requête » devant un agent qui n'écrit
    jamais de requête lui demande de transposer, et la transposition est
    précisément l'opération qu'il rate : il lit « à écarter de toute moyenne »,
    pense SQL, et écrit `df['x'].mean()` sans filtre.
    """
    llm = _llm_danalyse()
    _orchestrateur(llm, source).ask("Trace-moi la puissance moyenne.")

    prompt = llm.systems_for(ANALYSIS)[0]
    assert "ton code" in prompt
    assert "dropna" in prompt  # une sentinelle n'est pas un NaN, et il faut le dire


def test_une_source_sans_dictionnaire_ne_change_rien(tmp_path: Path):
    """`titanic` et `iris` n'en déclarent pas : leur prompt doit rester intact."""
    csv = tmp_path / "nu.csv"
    csv.write_text("a\n1\n", encoding="utf-8")
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="nu"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Voici.")])
    )
    _orchestrateur(llm, FileSource(name="nu", path=csv)).ask("Trace-moi quelque chose.")

    assert llm.systems_for(ANALYSIS)[0] == prompts.gabarit(prompts.ANALYSIS)


def test_un_dictionnaire_ampute_est_dit_a_l_utilisateur(source: FileSource):
    """Un dictionnaire coupé en silence remplacerait un mal par un autre."""
    llm = _llm_danalyse()
    answer = _orchestrateur(llm, source, dictionary_max_chars=60).ask("Trace-moi la moyenne.")

    etape = next(s for s in answer.trace if s.node == "analysis")
    assert etape.truncated
    assert "Les pièges de cette source" in etape.truncation
    assert "Dictionnaire tronqué" in answer.answer


def test_les_deux_amputations_se_cumulent(tmp_path: Path, monkeypatch):
    """Des LIGNES coupées et des SECTIONS coupées ne disent pas la même chose.

    L'une fait compter sur un échantillon, l'autre fait compter sans la règle.
    Un nœud qui n'en rendrait qu'une laisserait l'utilisateur croire l'autre
    intacte — et c'est le genre de silence que tout ce chantier corrige.

    Source SQL et non fichier : c'est le seul chemin qui MATÉRIALISE des
    tables, donc le seul où des lignes peuvent tomber.
    """
    dico = tmp_path / "releves.md"
    dico.write_text(DICTIONNAIRE, encoding="utf-8")
    base = PostgresSource(name="releves", dsn="postgresql+pg8000://u:p@h/b", dictionary=dico)

    def fabrique(_source) -> DuckDBAdapter:
        connexion = duckdb.connect(":memory:")
        connexion.execute("CREATE TABLE releves AS SELECT * FROM (VALUES (50), (0), (-1)) t(p)")
        return DuckDBAdapter(connexion, ["releves"])

    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.open_source", fabrique)
    llm = _llm_danalyse()
    answer = _orchestrateur(llm, base, dictionary_max_chars=60, analysis_table_max_rows=1).ask(
        "Trace-moi la moyenne."
    )

    etape = next(s for s in answer.trace if s.node == "analysis")
    assert "Données tronquées" in etape.truncation
    assert "Dictionnaire tronqué" in etape.truncation
    assert etape.truncation.count("tronqu") == 2  # les deux, et pas l'une à la place de l'autre


def test_un_dictionnaire_entier_n_ajoute_aucun_avis(source: FileSource):
    llm = _llm_danalyse()
    answer = _orchestrateur(llm, source).ask("Trace-moi la puissance moyenne.")

    etape = next(s for s in answer.trace if s.node == "analysis")
    assert not etape.truncated
    assert "Dictionnaire tronqué" not in answer.answer
