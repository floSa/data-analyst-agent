"""Analyse sur source SQL : ce qui est matérialisé, et ce qui est coupé (audit §5.1).

C'est le seul endroit du code qui écrit des fichiers dérivés d'une base et leur
applique un plafond de lignes (``analysis_table_max_rows``) — donc le seul qui
peut livrer au code généré une donnée AMPUTÉE sans que rien, dans le fichier
livré, ne le dise. Un agrégat calculé là-dessus est faux et se présente comme
juste : c'est un résultat erroné, pas une erreur.

Le point d'observation est ``run_analysis`` : c'est ce que le nœud lui passe
(fichiers montés, contexte du code) qui détermine le résultat, et les CSV vivent
dans un dossier temporaire refermé à la sortie du nœud — il faut donc les lire
depuis l'intérieur.

Fichier séparé de ``test_graph.py`` à dessein : la branche Maxizoo le réécrit
presque entièrement (audit §8.3), et ces tests-ci n'ont pas à disparaître avec.
"""

from pathlib import Path

import duckdb
import joblib
import pytest

from data_analyst_agent.agents.analysis.agent import AnalysisResult
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, PostgresSource
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import SandboxResult
from helpers.doubles import FakeClassifier, ScriptedSandbox
from helpers.scripted_llm import (
    ANALYSIS,
    PLANNER,
    RAPPEL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
"""


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


class AnalyseObservee:
    """Espionne ce que le nœud passe à ``run_analysis``, CSV matérialisés compris.

    Les fichiers sont relus ici et non après coup : le nœud les écrit dans un
    ``TemporaryDirectory`` qu'il referme en sortant.
    """

    def __init__(self) -> None:
        self.montes: dict[str, str] = {}
        self.data_context = ""

    def __call__(self, question, *, data_files, data_context, **kwargs) -> AnalysisResult:
        self.montes = {
            nom: Path(chemin).read_text(encoding="utf-8") for chemin, nom in data_files.items()
        }
        self.data_context = data_context
        return AnalysisResult(
            code="print('ok')", execution=SandboxResult(status="ok", stdout="ok\n"), attempts=1
        )


def base_sql(monkeypatch, tables: dict[str, list[tuple]]) -> None:
    """Fait de toute source du catalogue une base DuckDB à plusieurs tables.

    Un vrai adaptateur est dessous — le test porte sur le nœud, pas sur une mise
    en scène de l'adaptateur. Une source déclarée ``postgres`` évite la branche
    ``FileSource`` du nœud sans exiger de serveur Postgres.
    """

    def fabrique(_source) -> DuckDBAdapter:
        connection = duckdb.connect(":memory:")
        for nom, lignes in tables.items():
            valeurs = ", ".join(f"({n}, '{v}')" for n, v in lignes)
            connection.execute(f"CREATE TABLE {nom} AS SELECT * FROM (VALUES {valeurs}) t(n, v)")
        return DuckDBAdapter(connection, list(tables))

    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.open_source", fabrique)


def orchestrateur(llm: ScriptedLLM, registry: Registry, **reglages) -> Orchestrator:
    return Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[PostgresSource(name="base", dsn="postgresql+pg8000://u:p@h/b")]),
        registry=registry,
        settings=Settings(_env_file=None, **reglages),
        sandbox=ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n")]),
    )


def llm_danalyse() -> ScriptedLLM:
    return (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="base"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Voici l'analyse.")])
    )


# --- matérialisation -----------------------------------------------------------


def test_chaque_table_de_la_base_devient_un_csv_monte(registry: Registry, monkeypatch):
    """Une table = un fichier sous /data, avec son en-tête et ses lignes réelles."""
    base_sql(monkeypatch, {"ventes": [(1, "a"), (2, "b")], "clients": [(7, "z")]})
    espion = AnalyseObservee()
    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.run_analysis", espion)
    llm = llm_danalyse()

    reponse = orchestrateur(llm, registry).ask("Trace la répartition")

    assert reponse.error is None
    assert sorted(espion.montes) == ["clients.csv", "ventes.csv"]
    assert espion.montes["ventes.csv"] == "n,v\n1,a\n2,b\n"
    assert espion.montes["clients.csv"] == "n,v\n7,z\n"
    # le schéma de la base est décrit au code généré, pas seulement les fichiers
    assert "TABLE ventes" in espion.data_context
    assert "TABLE clients" in espion.data_context


def test_le_code_genere_recoit_les_fichiers_dans_son_prompt(registry: Registry, monkeypatch):
    """Sans cette liste, le code généré ne sait pas quoi lire sous /data."""
    base_sql(monkeypatch, {"ventes": [(1, "a")]})
    llm = llm_danalyse()

    reponse = orchestrateur(llm, registry).ask("Trace la répartition")

    assert reponse.error is None
    assert "- /data/ventes.csv" in llm.prompts_for(ANALYSIS)[0]


# --- troncature ----------------------------------------------------------------


def test_le_plafond_de_lignes_coupe_bien_le_csv_materialise(registry: Registry, monkeypatch):
    base_sql(monkeypatch, {"ventes": [(i, "x") for i in range(1, 6)]})
    espion = AnalyseObservee()
    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.run_analysis", espion)
    llm = llm_danalyse()

    reponse = orchestrateur(llm, registry, analysis_table_max_rows=2).ask("Trace la répartition")

    assert reponse.error is None
    assert espion.montes["ventes.csv"] == "n,v\n1,x\n2,x\n"  # 2 des 5 lignes


def test_une_table_coupee_est_dite_au_code_genere_et_a_l_utilisateur(
    registry: Registry, monkeypatch
):
    """Le défaut coûteux : un agrégat sur un échantillon présenté comme complet.

    Le CSV coupé est lisible sans anomalie ; si personne ne dit qu'il manque des
    lignes, la somme calculée dessus est fausse et la réponse la cite sans
    réserve. D'où les trois destinataires : le contexte du code, la trace, et la
    réponse rendue — la trace n'étant pas dépliée par défaut.
    """
    base_sql(monkeypatch, {"ventes": [(i, "x") for i in range(1, 6)]})
    espion = AnalyseObservee()
    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.run_analysis", espion)
    llm = llm_danalyse()

    reponse = orchestrateur(llm, registry, analysis_table_max_rows=2).ask("Somme des ventes")

    assert "Données tronquées : ventes" in espion.data_context
    assert "2 lignes" in espion.data_context
    etape = next(s for s in reponse.trace if s.node == "analysis")
    assert etape.truncated is True
    assert "ventes" in etape.truncation
    assert "DAA_ANALYSIS_TABLE_MAX_ROWS" in reponse.answer
    assert "Voici l'analyse." in reponse.answer  # l'avis s'ajoute, il ne remplace pas


def test_seule_la_table_reellement_coupee_est_nommee(registry: Registry, monkeypatch):
    """Accuser une table complète ferait douter d'un chiffre juste."""
    base_sql(
        monkeypatch,
        {"ventes": [(i, "x") for i in range(1, 6)], "clients": [(7, "z")]},
    )
    llm = llm_danalyse()

    reponse = orchestrateur(llm, registry, analysis_table_max_rows=2).ask("Somme des ventes")

    etape = next(s for s in reponse.trace if s.node == "analysis")
    assert "ventes" in etape.truncation
    assert "clients" not in etape.truncation


def test_aucun_avis_quand_rien_n_est_coupe(registry: Registry, monkeypatch):
    base_sql(monkeypatch, {"ventes": [(1, "a"), (2, "b")]})
    llm = llm_danalyse()

    reponse = orchestrateur(llm, registry, analysis_table_max_rows=1000).ask("Trace la répartition")

    etape = next(s for s in reponse.trace if s.node == "analysis")
    assert etape.truncated is False
    assert etape.truncation == ""
    assert reponse.answer == "Voici l'analyse."


# --- la tranche se qualifie elle-même, PARTOUT où elle sort ---------------------
#
# Trois coupes, une seule propriété : *une grandeur qui sort d'une tranche porte
# la mention de sa tranche jusque dans la réponse*. Une seule des trois la
# tenait, et c'est ce qui rendait le trou invisible — la première suffisait à
# faire croire que la propriété l'était.


def test_un_REJEU_sur_une_table_coupee_le_dit_aussi(registry: Registry, monkeypatch, tmp_path):
    """Le même chiffre, redevenu muet en changeant de nœud — mesuré 3/3 sur vLLM.

    Le tour 1 trace une figure sur `sessions` coupée : la réponse dit « sur les
    10 000 relevés » et porte l'avis. Le tour 2 demande de la remettre en bleu,
    et c'est le nœud de RAPPEL qui répond — il rejoue le code sur le MÊME décor,
    donc sur la même tranche, et rendait 290 relevés sur 547 200 sans un mot.

    L'avis ne pouvait pas remonter : il naît dans le décor, qui meurt avec le
    bloc qui le monte, et le rejeu traverse l'agent de rappel avant d'être
    rendu. Il voyage désormais avec le résultat d'analyse
    (``AnalysisResult.truncation_notice``).
    """
    base_sql(monkeypatch, {"ventes": [(i, "x") for i in range(1, 6)]})
    espace = ConversationWorkspace(tmp_path, "fil")
    espace.save_code("print('rouge')", "trace les ventes", source="base", figures=1)
    llm = (
        ScriptedLLM()
        .script(
            RAPPEL,
            [
                tool_call("rejouer_un_code", {"nom": "graphique_1", "modification": "en bleu"}),
                text("Le graphique est en bleu : 2 ventes."),
            ],
        )
        .script(ANALYSIS, [text("```python\nprint('bleu')\n```")])
        .script(SYNTHESIS, [text("Voici le graphique repris.")])
    )
    orch = orchestrateur(llm, registry, analysis_table_max_rows=2, workspace_dir=tmp_path)

    reponse = orch.ask("remets-le en bleu", conversation_id="fil")

    etape = next(s for s in reponse.trace if s.node == "rappel")
    assert etape.truncated is True
    assert "ventes" in etape.truncation
    assert "DAA_ANALYSIS_TABLE_MAX_ROWS" in reponse.answer


def test_un_TABLEAU_INTERMEDIAIRE_coupe_reste_une_tranche_au_tour_suivant(
    registry: Registry, monkeypatch, tmp_path
):
    """La même coupe, vue un tour plus tard — et personne ne la regardait là.

    Le tableau d'un tour précédent est le produit d'une requête, donc il peut
    être un extrait ; le CSV qu'on en garde ne porte aucune marque. Un tour
    ultérieur qui le remonte pour y compter recommence le défaut, sans que rien
    n'ait changé de place.
    """
    espace = ConversationWorkspace(tmp_path, "fil")
    espace.save_table(["n"], [[1], [2]], "les ventes", tronque=True)
    base_sql(monkeypatch, {"ventes": [(1, "a")]})
    espion = AnalyseObservee()
    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.run_analysis", espion)
    llm = llm_danalyse()
    orch = orchestrateur(llm, registry, retrieval_max_rows=2, workspace_dir=tmp_path)

    reponse = orch.ask("trace ce tableau", conversation_id="fil")

    assert "TRONQUÉ" in espion.data_context  # le code généré sait sur quoi il travaille
    etape = next(s for s in reponse.trace if s.node == "analysis")
    assert "resultat_1" in etape.truncation
    assert "DAA_RETRIEVAL_MAX_ROWS" in reponse.answer


def test_un_tableau_intermediaire_ENTIER_ne_se_qualifie_de_rien(
    registry: Registry, monkeypatch, tmp_path
):
    """Le pendant obligatoire : accuser un tableau complet ferait douter d'un chiffre juste."""
    espace = ConversationWorkspace(tmp_path, "fil")
    espace.save_table(["n"], [[1], [2]], "les ventes")
    base_sql(monkeypatch, {"ventes": [(1, "a")]})
    llm = llm_danalyse()
    orch = orchestrateur(llm, registry, workspace_dir=tmp_path)

    reponse = orch.ask("trace ce tableau", conversation_id="fil")

    etape = next(s for s in reponse.trace if s.node == "analysis")
    assert etape.truncated is False
    assert reponse.answer == "Voici l'analyse."
