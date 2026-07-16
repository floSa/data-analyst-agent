"""Orchestrateur : graphe complet avec LLM scripté, sans Docker ni réseau.

DuckDB (in-process) joue les sources SQL ; la sandbox et les modèles ML sont
doublés ; seul le routage, le chaînage et la synthèse sont sous test.
"""

import json
from pathlib import Path

import joblib
import pytest
from pydantic_ai import UnexpectedModelBehavior

from data_analyst_agent.agents.inference.predict import InferenceOutcome, Prediction
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import MimeOutput, SandboxResult
from helpers.doubles import FakeClassifier, ScriptedSandbox
from helpers.scripted_llm import (
    ANALYSIS,
    PLANNER,
    RETRIEVAL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

TITANIC_OK = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
    labels:
      "0": "n'a pas survécu"
      "1": "a survécu"
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return csv


@pytest.fixture
def passager_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "passagers.csv"
    csv.write_text(
        "passenger_id,sex,pclass,age,sibsp,parch,fare,embarked\n"
        "1,female,1,28,0,0,80.0,S\n"
        "2,male,3,45,0,0,8.0,S\n",
        encoding="utf-8",
    )
    return csv


def orchestrator_with(llm: ScriptedLLM, **kwargs) -> Orchestrator:
    kwargs.setdefault("catalog", Catalog(sources=[]))
    kwargs.setdefault("settings", make_settings())
    return Orchestrator(model=llm.model(), **kwargs)


# --- predict ------------------------------------------------------------------


def test_flux_predict_complet(registry: Registry):
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features=TITANIC_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Prédis la survie pour sexe=female, classe=1, âge=28...")
    assert answer.error is None
    assert "a survécu" in answer.answer
    assert "88" in answer.answer  # probabilité citée
    assert [s.node for s in answer.trace] == ["plan", "inference", "synthesize"]
    assert answer.plan.capability == "predict"


def test_flux_predict_incomplet_redemande(registry: Registry):
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"sex": "female"}))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Prédis la survie d'une femme")
    assert answer.error is None
    assert "pclass" in answer.answer
    assert answer.answer.strip().endswith("?")
    assert answer.artifacts == []


# --- multi-tours (slot-filling conversationnel) ----------------------------------


def test_relance_puis_complement_multi_tours(registry: Registry):
    """Tour 1 : features partielles -> relance + pending. Tour 2 : complément -> prédiction."""
    llm1 = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict", dataset="titanic", features={"sex": "female", "pclass": 1}
                )
            )
        ],
    )
    orchestrator1 = orchestrator_with(llm1, registry=registry)
    tour1 = orchestrator1.ask("Prédis la survie d'une femme en 1re classe")
    assert tour1.answer.strip().endswith("?")
    assert tour1.pending is not None
    assert tour1.pending.dataset == "titanic"
    assert tour1.pending.features == {"sex": "female", "pclass": 1}

    # tour 2 : le planificateur n'extrait QUE les nouvelles valeurs du message
    llm2 = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict",
                    dataset="titanic",
                    features={"age": 28, "sibsp": 0, "parch": 0, "fare": 80.0, "embarked": "S"},
                )
            )
        ],
    )
    orchestrator2 = orchestrator_with(llm2, registry=registry)
    tour2 = orchestrator2.ask(
        "Elle a 28 ans, pas de famille à bord, billet à 80 livres, embarquée à Southampton",
        pending=tour1.pending,
    )
    assert tour2.error is None
    assert "a survécu" in tour2.answer  # fusion acquis + complément -> prédiction
    assert tour2.pending is None  # plus rien en attente
    # le contexte multi-tours a bien été donné au planificateur
    assert "CONTEXTE DE CONVERSATION" in llm2.systems_for(PLANNER)[0]
    assert "sex='female'" in llm2.systems_for(PLANNER)[0]


def test_pending_ignore_si_changement_de_sujet(mini_csv: Path, registry: Registry):
    """L'utilisateur digresse : le contexte en attente n'est pas appliqué de force."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}),
                text("4 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask(
        "Finalement, combien de lignes dans la table ?",
        pending=PendingInference(dataset="titanic", features={"sex": "female"}),
    )
    assert answer.error is None
    assert answer.answer == "4 lignes."
    assert answer.pending is None  # la digression solde le contexte


def test_fetch_then_predict_degrade_en_predict_sans_source(registry: Registry):
    """Catalogue vide + route fetch_then_predict impossible -> predict + relance."""
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="fetch_then_predict",
                    dataset="titanic",
                    features={"sex": "female", "pclass": 1},
                )
            )
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry)  # catalogue vide
    answer = orchestrator.ask("Prédis la survie d'une femme en 1re classe")
    assert answer.error is None  # pas de crash « catalogue vide »
    assert answer.plan.capability == "predict"
    assert answer.answer.strip().endswith("?")  # relance sur les features manquantes
    assert answer.pending is not None


def test_correction_de_valeur_hors_bornes_multi_tours(registry: Registry):
    """Le complément corrige une valeur invalide de l'acquis (le nouveau prime)."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    pending = PendingInference(dataset="titanic", features={**TITANIC_OK, "age": 250})
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"age": 25}))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Pardon, 25 ans", pending=pending)
    assert answer.error is None
    assert "a survécu" in answer.answer


# --- query --------------------------------------------------------------------


def test_flux_query_sur_fichier(mini_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE sexe = 'f'"}),
                text("Il y a 3 femmes dans la table."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de femmes ?")
    assert answer.error is None
    assert answer.answer == "Il y a 3 femmes dans la table."
    assert len(answer.artifacts) == 1
    table = json.loads(answer.artifacts[0].data)
    assert table["rows"] == [[3]]
    assert "retrieval" in [s.node for s in answer.trace]


def test_query_multi_lignes_ne_recopie_pas_le_tableau(mini_csv: Path, registry: Registry):
    """Résultat à plusieurs lignes : synthèse brève déterministe, pas de recopie ligne à ligne."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM mini"}),
                # le LLM bavarde et recopie les lignes : on ne doit PAS s'y fier
                text("Ligne 1 : f, 1. Ligne 2 : f, 1. Ligne 3 : f, 0. Ligne 4 : m, 0."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Donne-moi toutes les lignes de la table")
    assert answer.error is None
    # le tableau (4 lignes) est bien joint
    table = json.loads(answer.artifacts[0].data)
    assert len(table["rows"]) == 4
    # ... mais la synthèse ne recopie pas les lignes : phrase courte renvoyant au tableau
    assert "Ligne 1" not in answer.answer
    assert "4 lignes" in answer.answer
    assert "tableau" in answer.answer


def test_query_sans_resultat_le_dit_au_lieu_de_raconter(mini_csv: Path, registry: Registry):
    """Zéro ligne : réponse déterministe honnête, jamais la synthèse du LLM.

    Cas observé en vrai : sur un `WHERE label LIKE '%First%'` (libellés en base
    français), la requête ne ramenait rien et le LLM affirmait pourtant « le
    résultat affiche les informations de toutes les passagères de première
    classe ». Un tableau vide doit se dire vide.
    """
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM mini WHERE sexe = 'inexistant'"}),
                # le LLM raconte un résultat qu'il n'a pas : on ne doit PAS le relayer
                text("Le résultat affiche toutes les passagères de première classe."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Et pour les femmes de 1re classe ?")

    assert answer.error is None
    assert "passagères" not in answer.answer  # la fabulation du LLM est écartée
    assert "aucune ligne" in answer.answer.lower()
    table = json.loads(answer.artifacts[0].data)
    assert table["rows"] == []


# --- mémoire de conversation (objets intermédiaires persistés) -------------------

REPO = Path(__file__).parents[3]


def _iris_registry() -> Registry:
    """Le vrai registre (iris + son artefact) pour prédire des lignes iris réelles."""
    return Registry.load(REPO / "models" / "registry.yaml")


def test_query_memorise_le_tableau(tmp_path: Path, registry: Registry):
    """Un résultat de requête est persisté dans l'espace de travail de la conversation."""
    iris = REPO / "sources" / "iris.csv"
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="iris"))])
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql", {"query": "SELECT * FROM iris ORDER BY sepal_length DESC LIMIT 3"}
                ),
                text("Voici 3 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="iris", path=iris)])
    settings = make_settings(workspace_dir=tmp_path)
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry, settings=settings)
    orchestrator.ask("les 3 plus grandes fleurs", conversation_id="c1")

    ws = ConversationWorkspace(tmp_path, "c1")
    assert [a.name for a in ws.artifacts] == ["resultat_1"]
    assert ws.artifacts[0].row_count == 3
    assert ws.path_of(ws.artifacts[0]).exists()


def test_reutilisation_du_tableau_precedent_pour_prediction(tmp_path: Path):
    """Tour 1 : requête iris -> resultat_1. Tour 2 : « prédis ces lignes » -> prédiction en lot."""
    iris = REPO / "sources" / "iris.csv"
    registry = _iris_registry()
    settings = make_settings(workspace_dir=tmp_path)

    # tour 1 : produit resultat_1 (3 lignes iris) et le mémorise
    llm1 = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="iris"))])
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql", {"query": "SELECT * FROM iris ORDER BY sepal_length DESC LIMIT 3"}
                ),
                text("3 lignes."),
            ],
        )
    )
    orch1 = orchestrator_with(
        llm1,
        catalog=Catalog(sources=[FileSource(name="iris", path=iris)]),
        registry=registry,
        settings=settings,
    )
    orch1.ask("donne-moi les 3 dernières lignes du dataset iris", conversation_id="c2")

    # tour 2 : le planificateur désigne resultat_1 comme source (« ces lignes »)
    llm2 = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="resultat_1",
                        dataset="iris",
                        data_question="ces lignes",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM resultat_1"}),
                text("lignes récupérées."),
            ],
        )
    )
    # catalogue vide : resultat_1 n'existe QUE grâce à la mémoire de conversation
    orch2 = orchestrator_with(
        llm2, catalog=Catalog(sources=[]), registry=registry, settings=settings
    )
    answer = orch2.ask("prédis ces 3 lignes", conversation_id="c2")

    assert answer.error is None
    assert "Prédiction (iris)" in answer.answer
    detail = json.loads(answer.artifacts[0].data)
    assert len(detail["rows"]) == 3  # les 3 lignes du tableau précédent, prédites
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    # le planificateur a bien reçu la description de l'objet intermédiaire
    assert "resultat_1" in llm2.systems_for(PLANNER)[0]


def test_predict_sans_features_chaine_sur_le_tableau_memorise(tmp_path: Path):
    """« prédis ces fleurs » routé en predict sans features -> chaîné sur le dernier tableau."""
    registry = _iris_registry()
    settings = make_settings(workspace_dir=tmp_path)
    # un tableau mémorisé fournissant exactement les features iris
    ConversationWorkspace(tmp_path, "cp").save_table(
        ["sepal_length", "sepal_width", "petal_length", "petal_width", "species"],
        [[7.2, 3.6, 6.1, 2.5, "virginica"], [7.9, 3.8, 6.4, 2.0, "virginica"]],
        "grandes fleurs",
    )
    llm = (
        ScriptedLLM()
        # le LLM se trompe : predict sans features (au lieu de fetch_then_predict)
        .script(PLANNER, [plan_response(Plan(capability="predict", dataset="iris", features={}))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT * FROM resultat_1"}), text("récupéré.")],
        )
    )
    orchestrator = orchestrator_with(
        llm, catalog=Catalog(sources=[]), registry=registry, settings=settings
    )
    answer = orchestrator.ask("prédis l'espèce de ces fleurs", conversation_id="cp")
    assert answer.error is None
    # le pont a re-routé vers une prédiction sur le tableau mémorisé
    assert answer.plan.capability == "fetch_then_predict"
    assert answer.plan.source == "resultat_1"
    assert "Prédiction (iris)" in answer.answer
    assert [s.node for s in answer.trace] == ["plan", "fetch_predict", "synthesize"]


def test_predict_sans_features_sans_tableau_utilisable_redemande(registry: Registry):
    """Pas de tableau mémorisé compatible : on redemande les features (pas de chaînage forcé)."""
    llm = ScriptedLLM().script(
        PLANNER, [plan_response(Plan(capability="predict", dataset="titanic", features={}))]
    )
    orchestrator = orchestrator_with(
        llm, registry=registry
    )  # pas de conversation_id -> pas de mémoire
    answer = orchestrator.ask("prédis la survie")
    assert answer.error is None
    assert answer.plan.capability == "predict"  # inchangé
    assert answer.answer.strip().endswith("?")  # relance sur les features


def test_contexte_du_tour_precedent_donne_au_planificateur(
    tmp_path: Path, mini_csv: Path, registry: Registry
):
    """Le planificateur reçoit la question/action précédente (résolution des ajustements)."""
    settings = make_settings(workspace_dir=tmp_path)
    ConversationWorkspace(tmp_path, "chist").record_turn("fais un graphique", "analyze", "mini")
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}), text("4.")],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry, settings=settings)
    orchestrator.ask("et le total ?", conversation_id="chist")
    system = llm.systems_for(PLANNER)[0]
    assert "CONTEXTE CONVERSATIONNEL" in system
    assert "fais un graphique" in system


def test_ajustement_flou_reprend_la_derniere_action(
    tmp_path: Path, mini_csv: Path, registry: Registry, monkeypatch
):
    """Planificateur en échec + tour précédent = analyse : on reprend analyze sur la même source."""
    settings = make_settings(workspace_dir=tmp_path)
    ConversationWorkspace(tmp_path, "cadj").record_turn(
        "fais un graphique", "analyze", "mini", code="print('ancien code')"
    )

    class _PlannerQuiEchoue:
        def run_sync(self, *args, **kwargs):
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")

    monkeypatch.setattr(
        "data_analyst_agent.orchestrator.graph.build_planner",
        lambda *args, **kwargs: _PlannerQuiEchoue(),
    )
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                status="ok", stdout="ok\n", results=[MimeOutput(mime="image/png", data="cGl4ZWxz")]
            )
        ]
    )
    llm = (
        ScriptedLLM()
        .script(ANALYSIS, [text("```python\nprint('nouveau graphique coloré')\n```")])
        .script(SYNTHESIS, [text("Voici le graphique avec des couleurs plus vives.")])
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(
        llm, catalog=catalog, registry=registry, settings=settings, sandbox=sandbox
    )
    answer = orchestrator.ask("mets des couleurs plus vives", conversation_id="cadj")
    assert answer.error is None
    assert answer.plan.capability == "analyze"  # repris du tour précédent
    assert answer.plan.source == "mini"
    assert [a.mime for a in answer.artifacts] == ["image/png"]
    # l'agent d'analyse a bien reçu le code précédent pour l'ajuster
    assert "ancien code" in llm.prompts_for(ANALYSIS)[0]


def test_code_genere_accede_aux_objets_intermediaires(tmp_path: Path, registry: Registry):
    """Le CSV mémorisé est monté dans la sandbox et annoncé au code d'analyse."""
    # pré-remplit la mémoire avec un objet intermédiaire
    ws = ConversationWorkspace(tmp_path, "c3")
    ws.save_table(["a", "b"], [[1, 2], [3, 4]], "un tableau précédent")

    sandbox = ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n", results=[])])
    iris = REPO / "sources" / "iris.csv"
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="iris"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Analyse faite.")])
    )
    settings = make_settings(workspace_dir=tmp_path)
    orchestrator = orchestrator_with(
        llm,
        catalog=Catalog(sources=[FileSource(name="iris", path=iris)]),
        registry=registry,
        settings=settings,
        sandbox=sandbox,
    )
    orchestrator.ask("analyse", conversation_id="c3")
    # le prompt d'analyse mentionne le fichier intermédiaire réutilisable
    analysis_prompt = llm.prompts_for(ANALYSIS)[0]
    assert "resultat_1.csv" in analysis_prompt


# --- analyze ------------------------------------------------------------------


def test_flux_analyze_sur_fichier(mini_csv: Path, registry: Registry):
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                status="ok",
                stdout="taux par classe : 0.66\n",
                results=[MimeOutput(mime="image/png", data="cGl4ZWxz")],
            )
        ]
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="mini"))])
        .script(ANALYSIS, [text("```python\nprint('taux par classe : 0.66')\n```")])
        .script(SYNTHESIS, [text("Voici le bar chart demandé (taux : 0,66).")])
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry, sandbox=sandbox)
    answer = orchestrator.ask("Fais un bar chart de la survie")
    assert answer.error is None
    assert "bar chart" in answer.answer
    assert [a.mime for a in answer.artifacts] == ["image/png"]
    assert sandbox.executed  # le code est bien passé par la sandbox


# --- fetch_then_predict ---------------------------------------------------------


def test_chainage_fetch_then_predict(passager_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="passagers",
                        dataset="titanic",
                        data_question="La ligne du passager 1",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {
                        "query": (
                            "SELECT sex, pclass, age, sibsp, parch, fare, embarked"
                            " FROM passagers WHERE passenger_id = 1"
                        )
                    },
                ),
                text("Ligne du passager 1 récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=passager_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie du passager 1")
    assert answer.error is None
    assert "a survécu" in answer.answer
    nodes = [s.node for s in answer.trace]
    assert nodes == ["plan", "fetch_predict", "synthesize"]


def test_chainage_colonnes_capitalisees(tmp_path: Path, registry: Registry):
    """Les en-têtes capitalisés (CSV/Excel réels) sont mappés malgré la casse."""
    csv = tmp_path / "Passagers.csv"
    csv.write_text(
        "PassengerId,Sex,Pclass,Age,SibSp,Parch,Fare,Embarked\n1,female,1,28,0,0,80.0,S\n",
        encoding="utf-8",
    )
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="passagers",
                        dataset="titanic",
                        data_question="La ligne du passager 1",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {"query": "SELECT * FROM passagers WHERE PassengerId = 1"},
                ),
                text("Ligne récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie du passager 1")
    assert answer.error is None
    assert "a survécu" in answer.answer


def test_chainage_indice_de_colonnes_dans_le_prompt(passager_csv: Path, registry: Registry):
    """L'agent SQL reçoit la liste exacte des features attendues (alias forcés)."""
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="passagers",
                        dataset="titanic",
                        data_question="La ligne du passager 1",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {
                        "query": (
                            "SELECT sex, pclass, age, sibsp, parch, fare, embarked"
                            " FROM passagers WHERE passenger_id = 1"
                        )
                    },
                ),
                text("Ligne récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=passager_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    orchestrator.ask("Prédis la survie du passager 1")
    retrieval_prompt = llm.prompts_for(RETRIEVAL)[0]
    assert "nommées exactement" in retrieval_prompt
    for field in ("sex", "pclass", "age", "sibsp", "parch", "fare", "embarked"):
        assert field in retrieval_prompt


def test_chainage_en_lot_avec_lignes_invalides(tmp_path: Path, registry: Registry):
    """N lignes récupérées -> prédiction en lot, invalides écartées, détail joint."""
    csv = tmp_path / "groupe.csv"
    csv.write_text(
        "passenger_id,sex,pclass,age,sibsp,parch,fare,embarked\n"
        "1,female,1,28,0,0,80.0,S\n"
        "2,female,2,-5,0,0,20.0,S\n"  # age hors bornes -> écartée
        "3,female,3,40,1,0,8.0,Q\n",
        encoding="utf-8",
    )
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="groupe",
                        dataset="titanic",
                        data_question="Toutes les femmes",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM groupe WHERE sex = 'female'"}),
                text("3 lignes récupérées."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="groupe", path=csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie de toutes les femmes")

    assert answer.error is None
    assert "sur 2 lignes" in answer.answer
    assert "écartée" in answer.answer
    assert "a survécu : 2 (100%)" in answer.answer
    # table de détail : les colonnes récupérées + prediction + confiance
    detail = json.loads(answer.artifacts[0].data)
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    assert len(detail["rows"]) == 3
    assert detail["rows"][0][-2] == "a survécu"
    assert detail["rows"][1][-2].startswith("écartée")
    assert detail["rows"][1][-1] is None
    trace_step = next(s for s in answer.trace if s.node == "fetch_predict")
    assert "2/3" in trace_step.detail


def test_fetch_then_predict_sans_ligne(passager_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="passagers",
                        dataset="titanic",
                        data_question="Le passager 999",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {"query": "SELECT * FROM passagers WHERE passenger_id = 999"},
                ),
                text("Aucune ligne."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="passagers", path=passager_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis la survie du passager 999")
    assert answer.error is not None
    assert "aucune ligne" in answer.error
    assert answer.answer.startswith("Je n'ai pas pu répondre")


# --- chemins d'erreur -----------------------------------------------------------


def test_source_omise_catalogue_a_une_source(mini_csv: Path, registry: Registry):
    """Le LLM omet parfois la source : repli sur l'unique source du catalogue."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source=None))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}),
                text("4 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    assert answer.error is None
    assert answer.plan.source == "mini"  # repli tracé dans le plan


def test_source_omise_catalogue_multi_sources(
    mini_csv: Path, passager_csv: Path, registry: Registry
):
    """Plusieurs sources et aucun choix : on POSE une question, on ne plante pas."""
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source=None))])
    catalog = Catalog(
        sources=[
            FileSource(name="mini", path=mini_csv),
            FileSource(name="passagers", path=passager_csv),
        ]
    )
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    # clarification, pas d'erreur brute ni de KeyError remontée à l'utilisateur
    assert answer.error is None
    assert "mini" in answer.answer
    assert "passagers" in answer.answer
    assert answer.answer.strip().endswith("?")
    # la capacité n'a pas été exécutée : on s'arrête au plan puis on synthétise
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]
    assert answer.artifacts == []


def test_predict_sans_modele_multi_modeles_clarifie():
    """Prédiction sans dataset + plusieurs modèles : on demande lequel, pas de KeyError."""
    real_registry = Registry.load(Path(__file__).parents[3] / "models" / "registry.yaml")
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="predict", dataset=None))])
    orchestrator = orchestrator_with(llm, registry=real_registry)
    answer = orchestrator.ask("Prédis ces lignes avec le modèle auquel tu as accès")
    # clarification propre (error=null), pas de « KeyError: modèle inconnu : '' »
    assert answer.error is None
    for name in ("california_housing", "iris", "titanic"):
        assert name in answer.answer
    assert answer.answer.strip().endswith("?")
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]


def test_predict_sans_modele_un_seul_modele_repli_auto(registry: Registry):
    """Un seul modèle au registre : on le prend d'office plutôt que de demander."""
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset=None, features=TITANIC_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)  # registre à 1 modèle (titanic)
    answer = orchestrator.ask("Prédis la survie pour ce passager")
    assert answer.error is None
    assert answer.plan.dataset == "titanic"
    assert "a survécu" in answer.answer


def test_query_sur_iris_renvoie_les_colonnes(registry: Registry):
    """La source iris est interrogeable en SQL : les attributs remontent."""
    iris = Path(__file__).parents[3] / "sources" / "iris.csv"
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="iris"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM iris LIMIT 5"}),
                text("Colonnes : sepal_length, sepal_width, petal_length, petal_width, species."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="iris", path=iris)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Donne-moi les attributs du dataset iris")
    assert answer.error is None
    table = json.loads(answer.artifacts[0].data)
    assert table["columns"] == [
        "sepal_length",
        "sepal_width",
        "petal_length",
        "petal_width",
        "species",
    ]


def test_source_inconnue_repond_par_clarification(mini_csv: Path, registry: Registry):
    """Source désignée vraiment introuvable : on demande, pas de KeyError brut."""
    llm = ScriptedLLM().script(
        PLANNER, [plan_response(Plan(capability="query", source="nexiste-pas"))]
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    assert answer.error is None
    assert "KeyError" not in answer.answer
    assert "introuvable" in answer.answer
    assert "mini" in answer.answer  # liste les sources connues
    assert answer.answer.strip().endswith("?")
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]


def test_source_decoree_par_le_llm_est_normalisee(mini_csv: Path, registry: Registry):
    """« mini (file) » (nom décoré par le LLM) est ramené à « mini », pas de crash."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini (file)"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}),
                text("4 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    assert answer.error is None
    assert answer.plan.source == "mini"  # normalisé
    assert "retrieval" in [s.node for s in answer.trace]


def test_planificateur_illisible_repond_proprement(registry: Registry, monkeypatch):
    """Demande trop floue : le planner échoue (retries épuisés) -> message d'aide, pas de crash."""

    class _PlannerQuiEchoue:
        def run_sync(self, *args, **kwargs):
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")

    monkeypatch.setattr(
        "data_analyst_agent.orchestrator.graph.build_planner",
        lambda *args, **kwargs: _PlannerQuiEchoue(),
    )
    orchestrator = orchestrator_with(ScriptedLLM(), registry=registry)
    answer = orchestrator.ask("euh... fais un truc")
    assert answer.error is None  # pas d'exception brute remontée
    assert "UnexpectedModelBehavior" not in answer.answer
    assert answer.answer.strip().endswith("?")  # on redemande de préciser
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]


def test_source_forcee_par_l_utilisateur(mini_csv: Path, registry: Registry):
    # la source passée à ask() prime sur celle du plan
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="autre"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}),
                text("4 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien ?", source="mini")
    assert answer.error is None
    assert answer.plan.source == "mini"


# --- unités pures ----------------------------------------------------------------


def test_format_prediction_regression():
    outcome = InferenceOutcome(
        status="ok",
        prediction=Prediction(
            dataset="california_housing",
            task="regression",
            value=4.1391,
            unit="centaines de milliers de dollars",
        ),
    )
    message = Orchestrator._format_prediction(outcome)
    assert "4.1391" in message
    assert "centaines de milliers de dollars" in message


def test_route():
    assert Orchestrator._route({"error": "boom"}) == "error"
    assert Orchestrator._route({}) == "error"
    assert Orchestrator._route({"plan": Plan(capability="analyze")}) == "analyze"


def test_logs_structures_par_noeud(registry: Registry, caplog):
    import logging

    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features=TITANIC_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    with caplog.at_level(logging.INFO, logger="data_analyst_agent.orchestrator"):
        orchestrator.ask("Prédis la survie")
    messages = [record.getMessage() for record in caplog.records]
    assert any("nœud plan : terminé" in m for m in messages)
    assert any("nœud inference : terminé" in m for m in messages)
    assert any("nœud synthesize : terminé" in m for m in messages)
