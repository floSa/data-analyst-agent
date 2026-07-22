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
from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    DuckDBSource,
    FileSource,
    PostgresSource,
)
from data_analyst_agent.agents.retrieval.duckdb_source import DuckDBAdapter
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import MimeOutput, SandboxResult
from helpers.doubles import FakeRegressor, ScriptedSandbox
from helpers.maxizoo import build_duckdb
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

VENTES_OK = {
    "store_type": "grand",
    "commodity_group": "Chien",
    "brand_type": "nationale",
    "base_price": 49.90,
    "day_of_week": 5,
    "month": 11,
    "discount_rate": 0.30,
    "promo_type": "produits",
    "temp_anomaly": 0.0,
}

REGISTRY_YAML = """
models:
  - dataset: maxizoo_sales
    task: regression
    model_path: maxizoo_sales.joblib
    target: quantity
    unit: unités vendues
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeRegressor(), tmp_path / "maxizoo_sales.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return csv


FEATURES_HEADER = (
    "ligne_id,store_type,commodity_group,brand_type,base_price,"
    "day_of_week,month,discount_rate,promo_type,temp_anomaly"
)


@pytest.fixture
def ventes_csv(tmp_path: Path) -> Path:
    """Un tableau au format exact des features du modèle (chaînage SQL -> predict)."""
    csv = tmp_path / "ventes.csv"
    csv.write_text(
        f"{FEATURES_HEADER}\n"
        "1,grand,Chien,nationale,49.90,5,11,0.30,produits,0.0\n"
        "2,petit,Chat,distributeur,12.50,2,6,0.0,aucune,1.5\n",
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
        [plan_response(Plan(capability="predict", dataset="maxizoo_sales", features=VENTES_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Combien d'unités vendra-t-on un samedi de novembre en promo -30 % ?")
    assert answer.error is None
    assert "4.1391" in answer.answer
    assert "unités vendues" in answer.answer  # l'unité est citée
    assert [s.node for s in answer.trace] == ["plan", "inference", "synthesize"]
    assert answer.plan.capability == "predict"


def test_flux_predict_incomplet_redemande(registry: Registry):
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict", dataset="maxizoo_sales", features={"store_type": "grand"}
                )
            )
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Combien vendra-t-on dans un grand magasin ?")
    assert answer.error is None
    assert "commodity_group" in answer.answer
    assert answer.answer.strip().endswith("?")
    assert answer.artifacts == []


# --- multi-tours (slot-filling conversationnel) ----------------------------------


def test_relance_puis_complement_multi_tours(registry: Registry):
    """Tour 1 : features partielles -> relance + pending. Tour 2 : complément -> prédiction."""
    acquis = {"store_type": "grand", "commodity_group": "Chien"}
    llm1 = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="maxizoo_sales", features=acquis))],
    )
    orchestrator1 = orchestrator_with(llm1, registry=registry)
    tour1 = orchestrator1.ask("Combien de croquettes chien vendra-t-on dans un grand magasin ?")
    assert tour1.answer.strip().endswith("?")
    assert tour1.pending is not None
    assert tour1.pending.dataset == "maxizoo_sales"
    assert tour1.pending.features == acquis

    # tour 2 : le planificateur n'extrait QUE les nouvelles valeurs du message
    llm2 = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(
                    capability="predict",
                    dataset="maxizoo_sales",
                    features={
                        "brand_type": "nationale",
                        "base_price": 49.90,
                        "day_of_week": 5,
                        "month": 11,
                        "discount_rate": 0.30,
                        "promo_type": "produits",
                        "temp_anomaly": 0.0,
                    },
                )
            )
        ],
    )
    orchestrator2 = orchestrator_with(llm2, registry=registry)
    tour2 = orchestrator2.ask(
        "Marque nationale à 49,90 €, un samedi de novembre en promo -30 %, température de saison",
        pending=tour1.pending,
    )
    assert tour2.error is None
    assert "4.1391" in tour2.answer  # fusion acquis + complément -> prédiction
    assert tour2.pending is None  # plus rien en attente
    # le contexte multi-tours a bien été donné au planificateur
    assert "CONTEXTE DE CONVERSATION" in llm2.systems_for(PLANNER)[0]
    assert "store_type='grand'" in llm2.systems_for(PLANNER)[0]


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
        pending=PendingInference(dataset="maxizoo_sales", features={"store_type": "grand"}),
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
                    dataset="maxizoo_sales",
                    features={"store_type": "grand", "commodity_group": "Chien"},
                )
            )
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry)  # catalogue vide
    answer = orchestrator.ask("Combien de croquettes chien vendra-t-on ?")
    assert answer.error is None  # pas de crash « catalogue vide »
    assert answer.plan.capability == "predict"
    assert answer.answer.strip().endswith("?")  # relance sur les features manquantes
    assert answer.pending is not None


def test_correction_de_valeur_hors_bornes_multi_tours(registry: Registry):
    """Le complément corrige une valeur invalide de l'acquis (le nouveau prime)."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    pending = PendingInference(dataset="maxizoo_sales", features={**VENTES_OK, "day_of_week": 250})
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(
                Plan(capability="predict", dataset="maxizoo_sales", features={"day_of_week": 5})
            )
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    answer = orchestrator.ask("Pardon, un samedi", pending=pending)
    assert answer.error is None
    assert "4.1391" in answer.answer


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


def test_reponse_de_memoire_sans_aucune_requete_est_ecartee(mini_csv: Path, registry: Registry):
    """Le modèle répond de mémoire sur un dataset célèbre, sans rien interroger.

    Observé en vrai sur « décris le dataset iris » : zéro requête, et une jolie
    prose encyclopédique servie comme une lecture de la source. Sur des données
    privées ce serait de l'invention — une réponse non fondée est refusée.
    """
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        # aucun tool_call : le modèle répond directement de son savoir
        .script(RETRIEVAL, [text("Le jeu de données iris est un classique de la classification.")])
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("peux-tu me faire une description du dataset ?")

    assert "classique" not in answer.answer  # la prose de mémoire est écartée
    assert "je ne peux donc rien en affirmer" in answer.answer.lower()


def test_reponse_fondee_sur_le_schema_seul_reste_acceptee(mini_csv: Path, registry: Registry):
    """« quelles colonnes ? » se répond avec get_schema, sans run_sql : c'est fondé."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [tool_call("get_schema", {}), text("La table mini a deux colonnes : sexe, survie.")],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("quelles colonnes y a-t-il ?")

    assert answer.answer == "La table mini a deux colonnes : sexe, survie."


# --- mémoire de conversation (objets intermédiaires persistés) -------------------

REPO = Path(__file__).parents[3]

# Les features du modèle, telles qu'une requête SQL les produit : c'est le
# chaînage « récupère puis prédis » (une ligne de tableau = un payload).
FEATURES_SQL = (
    "SELECT st.store_type, p.commodity_group, p.brand_type, p.base_price,"
    " 5 AS day_of_week, 11 AS month, 0.30 AS discount_rate,"
    " 'produits' AS promo_type, 0.0 AS temp_anomaly"
    " FROM sales_daily s JOIN stores st ON st.store_id = s.store_id"
    " JOIN products p ON p.sku_id = s.sku_id LIMIT 3"
)


@pytest.fixture
def maxizoo_source(tmp_path: Path) -> DuckDBSource:
    """La mini-base Maxizoo, en source DuckDB pour l'orchestrateur."""
    return DuckDBSource(name="maxizoo", path=build_duckdb(tmp_path / "maxizoo.duckdb"))


def _vrai_registry() -> Registry:
    """Le vrai registre (maxizoo_sales + son artefact) pour prédire de vraies lignes."""
    return Registry.load(REPO / "models" / "registry.yaml")


def test_query_memorise_le_tableau(tmp_path: Path, registry: Registry, maxizoo_source):
    """Un résultat de requête est persisté dans l'espace de travail de la conversation."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="maxizoo"))])
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {"query": "SELECT * FROM products ORDER BY base_price DESC LIMIT 3"},
                ),
                text("Voici 3 lignes."),
            ],
        )
    )
    catalog = Catalog(sources=[maxizoo_source])
    settings = make_settings(workspace_dir=tmp_path)
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry, settings=settings)
    orchestrator.ask("les 3 SKU les plus chers", conversation_id="c1")

    ws = ConversationWorkspace(tmp_path, "c1")
    assert [a.name for a in ws.artifacts] == ["resultat_1"]
    assert ws.artifacts[0].row_count == 3
    assert ws.path_of(ws.artifacts[0]).exists()


def test_reutilisation_du_tableau_precedent_pour_prediction(tmp_path: Path, maxizoo_source):
    """Tour 1 : requête -> resultat_1. Tour 2 : « prédis ces lignes » -> prédiction en lot."""
    registry = _vrai_registry()
    settings = make_settings(workspace_dir=tmp_path)

    # tour 1 : produit resultat_1 (3 lignes de features) et le mémorise
    llm1 = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="maxizoo"))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": FEATURES_SQL}), text("3 lignes.")],
        )
    )
    orch1 = orchestrator_with(
        llm1,
        catalog=Catalog(sources=[maxizoo_source]),
        registry=registry,
        settings=settings,
    )
    orch1.ask("donne-moi 3 lignes de vente avec leurs attributs", conversation_id="c2")

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
                        dataset="maxizoo_sales",
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
    assert "Prédiction (maxizoo_sales)" in answer.answer
    detail = json.loads(answer.artifacts[0].data)
    assert len(detail["rows"]) == 3  # les 3 lignes du tableau précédent, prédites
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    # le planificateur a bien reçu la description de l'objet intermédiaire
    assert "resultat_1" in llm2.systems_for(PLANNER)[0]


def test_predict_sans_features_chaine_sur_le_tableau_memorise(tmp_path: Path):
    """« prédis ces lignes » routé en predict sans features -> chaîné sur le dernier tableau."""
    registry = _vrai_registry()
    settings = make_settings(workspace_dir=tmp_path)
    # un tableau mémorisé fournissant exactement les features du modèle
    ConversationWorkspace(tmp_path, "cp").save_table(
        list(VENTES_OK),
        [list(VENTES_OK.values()), [*list(VENTES_OK.values())[:-1], 3.5]],
        "lignes de vente",
    )
    llm = (
        ScriptedLLM()
        # le LLM se trompe : predict sans features (au lieu de fetch_then_predict)
        .script(
            PLANNER,
            [plan_response(Plan(capability="predict", dataset="maxizoo_sales", features={}))],
        )
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT * FROM resultat_1"}), text("récupéré.")],
        )
    )
    orchestrator = orchestrator_with(
        llm, catalog=Catalog(sources=[]), registry=registry, settings=settings
    )
    answer = orchestrator.ask("prédis les ventes de ces lignes", conversation_id="cp")
    assert answer.error is None
    # le pont a re-routé vers une prédiction sur le tableau mémorisé
    assert answer.plan.capability == "fetch_then_predict"
    assert answer.plan.source == "resultat_1"
    assert "Prédiction (maxizoo_sales)" in answer.answer
    assert [s.node for s in answer.trace] == ["plan", "fetch_predict", "synthesize"]


def test_predict_sans_features_sans_tableau_utilisable_redemande(registry: Registry):
    """Pas de tableau mémorisé compatible : on redemande les features (pas de chaînage forcé)."""
    llm = ScriptedLLM().script(
        PLANNER, [plan_response(Plan(capability="predict", dataset="maxizoo_sales", features={}))]
    )
    orchestrator = orchestrator_with(
        llm, registry=registry
    )  # pas de conversation_id -> pas de mémoire
    answer = orchestrator.ask("prédis les ventes")
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


def test_contexte_du_tour_precedent_donne_aussi_a_lagent_sql(
    tmp_path: Path, mini_csv: Path, registry: Registry
):
    """L'anaphore d'un tour au suivant se résout dans l'agent SQL, pas juste le planner.

    « Affiche ceux des autres années » après « le CA de 2025 » : l'agent SQL
    doit voir le tour précédent, sinon il reçoit une phrase orpheline et répond
    « demande trop vague ».
    """
    settings = make_settings(workspace_dir=tmp_path)
    ConversationWorkspace(tmp_path, "csql").record_turn(
        "quel est le chiffre d'affaires total de 2025 ?", "query", "mini"
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini"}), text("ok.")],
        )
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry, settings=settings)
    orchestrator.ask("affiche ceux des autres années", conversation_id="csql")
    system = llm.systems_for(RETRIEVAL)[0]
    assert "Contexte de la conversation" in system
    assert "chiffre d'affaires total de 2025" in system


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


TRACEBACK_MATPLOTLIB = """\
---------------------------------------------------------------------------
TypeError                                 Traceback (most recent call last)
Cell In[4], line 15
     13 plt.figure(figsize=(10, 6))
---> 15 plt.bar(df_resultat_3['embarked'], df_resultat_3['count'])
     16
File /usr/local/lib/python3.12/site-packages/matplotlib/pyplot.py:3138, in bar(x, height)
   3127 @_copy_docstring_and_deprecators(Axes.bar)
TypeError: unhashable type: 'list'"""


def test_ajustement_apres_prediction_reussie(tmp_path: Path, registry: Registry):
    """« et sans la promo ? » après une prédiction ABOUTIE.

    Le pending est vidé dès qu'une prédiction réussit : sans mémoire des features
    validées, ce tour redemandait un base_price donné deux tours plus haut. Les
    features étant réparties sur PLUSIEURS messages, le planificateur ne pouvait
    pas non plus les relire dans la seule question précédente.
    """
    settings = make_settings(workspace_dir=tmp_path)
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(Plan(capability="predict", dataset="maxizoo_sales", features=VENTES_OK)),
            # tour 2 : le planificateur n'extrait QUE le changement demandé
            plan_response(
                Plan(
                    capability="predict",
                    dataset="maxizoo_sales",
                    features={"discount_rate": 0.0, "promo_type": "aucune"},
                )
            ),
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry, settings=settings)

    tour1 = orchestrator.ask("prédis les ventes de ce produit...", conversation_id="ajust")
    assert tour1.pending is None  # prédiction aboutie : plus rien en attente
    assert "4.1391" in tour1.answer

    tour2 = orchestrator.ask("et sans la promo ?", conversation_id="ajust")

    assert tour2.error is None
    assert "valeur manquante" not in tour2.answer  # ne redemande PAS l'acquis
    assert tour2.plan.features["discount_rate"] == 0.0  # le nouveau prime
    assert tour2.plan.features["promo_type"] == "aucune"
    assert tour2.plan.features["base_price"] == VENTES_OK["base_price"]  # l'acquis est repris
    assert tour2.plan.features["commodity_group"] == VENTES_OK["commodity_group"]


def test_ajustement_nherite_pas_dun_autre_dataset(tmp_path: Path, registry: Registry):
    """Une prédiction sur un AUTRE dataset ne récupère pas l'acquis du précédent."""
    settings = make_settings(workspace_dir=tmp_path)
    llm = ScriptedLLM().script(
        PLANNER,
        [
            plan_response(Plan(capability="predict", dataset="maxizoo_sales", features=VENTES_OK)),
            plan_response(Plan(capability="predict", dataset="autre_modele", features={})),
        ],
    )
    orchestrator = orchestrator_with(llm, registry=registry, settings=settings)
    orchestrator.ask("prédis les ventes de ce produit...", conversation_id="autre")

    tour2 = orchestrator.ask("et sur l'autre modèle ?", conversation_id="autre")

    assert "base_price" not in tour2.plan.features  # rien d'hérité
    assert "commodity_group" not in tour2.plan.features


def test_analyse_en_echec_ne_recrache_pas_le_traceback(mini_csv: Path, registry: Registry):
    """Un traceback de 40 lignes de pyplot/pandas remontait tel quel à l'écran.

    L'utilisateur n'a que faire de la mécanique interne de la sandbox : il lui
    faut une phrase honnête. Le détail, lui, part dans la trace.
    """
    settings = make_settings(analysis_max_attempts=1)
    sandbox = ScriptedSandbox([SandboxResult(status="error", error=TRACEBACK_MATPLOTLIB)])
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="mini"))])
        .script(ANALYSIS, [text("```python\nplt.bar(df['a'], df['b'])\n```")])
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(
        llm, catalog=catalog, registry=registry, settings=settings, sandbox=sandbox
    )
    answer = orchestrator.ask("fais-en un diagramme en barres")

    assert "Traceback" not in answer.answer
    assert "matplotlib/pyplot.py" not in answer.answer
    assert "Cell In[4]" not in answer.answer
    assert "n'a pas abouti" in answer.answer  # une phrase honnête, pas un dump
    # ... mais la cause reste diagnosticable dans la trace
    analyse = next(step for step in answer.trace if step.node == "analysis")
    assert "TypeError: unhashable type: 'list'" in analyse.detail


def test_analyse_en_echec_ne_livre_pas_de_figure_fantome(mini_csv: Path, registry: Registry):
    """Une tentative ratée laisse des axes vides : ne pas les afficher.

    Vu en vrai : sous « l'analyse n'a pas abouti », un graphique BLANC s'affichait
    quand même. Pire que rien — il donne à croire que la donnée est vide, alors
    que c'est le code généré qui a planté.
    """
    settings = make_settings(analysis_max_attempts=1)
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                status="error",
                error="TypeError: 'value' must be an instance of str or bytes, not a float",
                results=[MimeOutput(mime="image/png", data="cGl4ZWxz")],  # figure vide
            )
        ]
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="mini"))])
        .script(ANALYSIS, [text("```python\nplt.bar(df['a'], df['b'])\n```")])
    )
    catalog = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    orchestrator = orchestrator_with(
        llm, catalog=catalog, registry=registry, settings=settings, sandbox=sandbox
    )
    answer = orchestrator.ask("fais-en un diagramme en barres")

    assert answer.artifacts == []  # aucune figure fantôme
    assert "n'a pas abouti" in answer.answer


def test_code_genere_accede_aux_objets_intermediaires(
    tmp_path: Path, mini_csv: Path, registry: Registry
):
    """Le CSV mémorisé est monté dans la sandbox et annoncé au code d'analyse."""
    # pré-remplit la mémoire avec un objet intermédiaire
    ws = ConversationWorkspace(tmp_path, "c3")
    ws.save_table(["a", "b"], [[1, 2], [3, 4]], "un tableau précédent")

    sandbox = ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n", results=[])])
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="mini"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Analyse faite.")])
    )
    settings = make_settings(workspace_dir=tmp_path)
    orchestrator = orchestrator_with(
        llm,
        catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]),
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


def test_analyse_dune_base_duckdb_monte_la_base_sans_la_tronquer(
    registry: Registry, maxizoo_source
):
    """Une base DuckDB est montée telle quelle : le code la requête en SQL.

    Pas de matérialisation en CSV, donc pas de plafond à
    analysis_table_max_rows — c'est ce qui permet d'analyser 1,4 M de lignes
    sans que les agrégats portent en douce sur un extrait.
    """
    sandbox = ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n", results=[])])
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="maxizoo"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Analyse faite.")])
    )
    orchestrator = orchestrator_with(
        llm, catalog=Catalog(sources=[maxizoo_source]), registry=registry, sandbox=sandbox
    )
    orchestrator.ask("analyse le CA par magasin")

    prompt = llm.prompts_for(ANALYSIS)[0]
    assert "/data/maxizoo.duckdb" in prompt
    assert "duckdb.connect" in prompt
    assert "COMPLÈTE (aucune troncature)" in prompt
    assert "sales_daily" in prompt  # le schéma est décrit
    assert "TRONQUÉS" not in prompt


def test_le_dictionnaire_va_aussi_a_lagent_danalyse(tmp_path: Path, registry: Registry):
    """Le code d'analyse doit connaître les pièges, pas seulement le schéma.

    Sans le dictionnaire, le code généré filtrait « store_id = 'Lyon' » (Lyon
    est un store_name ; le store_id est 'S03') et concluait à tort « aucune
    donnée ». L'agent SQL avait le dictionnaire depuis le début ; l'analyse non.
    """
    from data_analyst_agent.agents.retrieval.catalog import DuckDBSource
    from helpers.maxizoo import build_duckdb

    dico = tmp_path / "dico.md"
    dico.write_text("Le e-commerce est le magasin ONLINE ; store_id va de S01 à S12.", "utf-8")
    source = DuckDBSource(
        name="maxizoo", path=build_duckdb(tmp_path / "maxizoo.duckdb"), dictionary=dico
    )
    sandbox = ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n", results=[])])
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="maxizoo"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Analyse faite.")])
    )
    orchestrator = orchestrator_with(
        llm, catalog=Catalog(sources=[source]), registry=registry, sandbox=sandbox
    )
    orchestrator.ask("fais un graphique de la météo à Lyon")

    prompt = llm.prompts_for(ANALYSIS)[0]
    assert "store_id va de S01 à S12" in prompt  # le dictionnaire est bien joint


def test_extrait_tronque_est_annonce_au_code_genere(
    tmp_path: Path, registry: Registry, monkeypatch
):
    """Une table Postgres coupée à analysis_table_max_rows doit se dire coupée.

    Sinon le code généré calcule « le CA total » sur l'extrait et l'annonce
    comme le total : crédible, précis au centime, et faux de plusieurs ordres
    de grandeur. C'est le pendant du tableau vide qui doit se dire vide.

    Postgres est le seul moteur encore matérialisé en CSV (une base DuckDB, elle,
    est montée entière) : on double son ouverture par un adaptateur sur un CSV
    de 50 lignes, plafonné à 10 — inutile de monter un conteneur pour ça.
    """
    csv = tmp_path / "gros.csv"
    csv.write_text("valeur\n" + "\n".join(str(i) for i in range(50)), encoding="utf-8")
    monkeypatch.setattr(
        "data_analyst_agent.orchestrator.graph.open_source",
        lambda source: DuckDBAdapter.from_file(csv),
    )
    sandbox = ScriptedSandbox([SandboxResult(status="ok", stdout="ok\n", results=[])])
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="pg"))])
        .script(ANALYSIS, [text("```python\nprint('ok')\n```")])
        .script(SYNTHESIS, [text("Analyse faite.")])
    )
    catalog = Catalog(sources=[PostgresSource(name="pg", dsn="postgresql+pg8000://u:p@h:5432/d")])
    orchestrator = orchestrator_with(
        llm,
        catalog=catalog,
        registry=registry,
        settings=make_settings(analysis_table_max_rows=10),
        sandbox=sandbox,
    )
    orchestrator.ask("fais-moi la somme")

    prompt = llm.prompts_for(ANALYSIS)[0]
    assert "TRONQUÉS" in prompt
    assert "gros (10 lignes seulement)" in prompt
    assert "n'annonce jamais un agrégat comme s'il valait pour toute la source" in prompt


# --- fetch_then_predict ---------------------------------------------------------


def _plan_chainage(source: str, question: str = "La ligne 1") -> object:
    return plan_response(
        Plan(
            capability="fetch_then_predict",
            source=source,
            dataset="maxizoo_sales",
            data_question=question,
        )
    )


def test_chainage_fetch_then_predict(ventes_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(PLANNER, [_plan_chainage("ventes")])
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql",
                    {
                        "query": (
                            "SELECT store_type, commodity_group, brand_type, base_price,"
                            " day_of_week, month, discount_rate, promo_type, temp_anomaly"
                            " FROM ventes WHERE ligne_id = 1"
                        )
                    },
                ),
                text("Ligne 1 récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="ventes", path=ventes_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis les ventes de la ligne 1")
    assert answer.error is None
    assert "4.1391" in answer.answer
    nodes = [s.node for s in answer.trace]
    assert nodes == ["plan", "fetch_predict", "synthesize"]


def test_chainage_colonnes_capitalisees(tmp_path: Path, registry: Registry):
    """Les en-têtes capitalisés (CSV/Excel réels) sont mappés malgré la casse."""
    csv = tmp_path / "Ventes.csv"
    csv.write_text(
        "LigneId,StoreType,CommodityGroup,BrandType,BasePrice,"
        "DayOfWeek,Month,DiscountRate,PromoType,TempAnomaly\n"
        "1,grand,Chien,nationale,49.90,5,11,0.30,produits,0.0\n",
        encoding="utf-8",
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [_plan_chainage("ventes")])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM ventes WHERE LigneId = 1"}),
                text("Ligne récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="ventes", path=csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis les ventes de la ligne 1")
    assert answer.error is None
    assert "4.1391" in answer.answer


def test_chainage_indice_de_colonnes_dans_le_prompt(ventes_csv: Path, registry: Registry):
    """L'agent SQL reçoit la liste exacte des features attendues (alias forcés)."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [_plan_chainage("ventes")])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM ventes WHERE ligne_id = 1"}),
                text("Ligne récupérée."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="ventes", path=ventes_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    orchestrator.ask("Prédis les ventes de la ligne 1")
    retrieval_prompt = llm.prompts_for(RETRIEVAL)[0]
    assert "nommées exactement" in retrieval_prompt
    for field in (
        "store_type",
        "commodity_group",
        "brand_type",
        "base_price",
        "day_of_week",
        "month",
        "discount_rate",
        "promo_type",
        "temp_anomaly",
    ):
        assert field in retrieval_prompt


def test_chainage_en_lot_avec_lignes_invalides(tmp_path: Path, registry: Registry):
    """N lignes récupérées -> prédiction en lot, invalides écartées, détail joint."""
    csv = tmp_path / "groupe.csv"
    csv.write_text(
        f"{FEATURES_HEADER}\n"
        "1,grand,Chien,nationale,49.90,5,11,0.30,produits,0.0\n"
        "2,grand,Chat,nationale,20.00,9,6,0.0,aucune,1.0\n"  # day_of_week=9 hors bornes -> écartée
        "3,petit,Chat,distributeur,12.50,2,6,0.0,aucune,1.5\n",
        encoding="utf-8",
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [_plan_chainage("groupe", "Toutes les lignes")])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM groupe"}),
                text("3 lignes récupérées."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="groupe", path=csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis les ventes de toutes ces lignes")

    assert answer.error is None
    assert "sur 2 lignes" in answer.answer
    assert "écartée" in answer.answer
    assert "moyenne 4.139" in answer.answer
    # table de détail : les colonnes récupérées + prediction + confiance
    detail = json.loads(answer.artifacts[0].data)
    assert detail["columns"][-2:] == ["prediction", "confiance"]
    assert len(detail["rows"]) == 3
    assert detail["rows"][0][-2] == "4.1391"
    assert detail["rows"][1][-2].startswith("écartée")
    assert detail["rows"][1][-1] is None
    trace_step = next(s for s in answer.trace if s.node == "fetch_predict")
    assert "2/3" in trace_step.detail


def test_fetch_then_predict_sans_ligne(ventes_csv: Path, registry: Registry):
    llm = (
        ScriptedLLM()
        .script(PLANNER, [_plan_chainage("ventes", "La ligne 999")])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM ventes WHERE ligne_id = 999"}),
                text("Aucune ligne."),
            ],
        )
    )
    catalog = Catalog(sources=[FileSource(name="ventes", path=ventes_csv)])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Prédis les ventes de la ligne 999")
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


def test_source_omise_catalogue_multi_sources(mini_csv: Path, ventes_csv: Path, registry: Registry):
    """Plusieurs sources et aucun choix : on POSE une question, on ne plante pas."""
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source=None))])
    catalog = Catalog(
        sources=[
            FileSource(name="mini", path=mini_csv),
            FileSource(name="ventes", path=ventes_csv),
        ]
    )
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Combien de lignes ?")
    # clarification, pas d'erreur brute ni de KeyError remontée à l'utilisateur
    assert answer.error is None
    assert "mini" in answer.answer
    assert "ventes" in answer.answer
    assert answer.answer.strip().endswith("?")
    # la capacité n'a pas été exécutée : on s'arrête au plan puis on synthétise
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]
    assert answer.artifacts == []


def test_predict_sans_modele_multi_modeles_clarifie(tmp_path: Path):
    """Prédiction sans dataset + plusieurs modèles : on demande lequel, pas de KeyError.

    Le registre livré n'ayant plus qu'un modèle, il ne peut pas jouer ce cas (il
    déclencherait le repli automatique testé juste en dessous). D'où un registre
    à deux entrées monté ici : c'est la présence d'un CHOIX qui est sous test.
    """
    (tmp_path / "registry.yaml").write_text(
        REGISTRY_YAML + "\n"
        "  - dataset: autre_modele\n"
        "    task: regression\n"
        "    model_path: autre.joblib\n"
        "    target: y\n",
        encoding="utf-8",
    )
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="predict", dataset=None))])
    orchestrator = orchestrator_with(llm, registry=Registry.load(tmp_path / "registry.yaml"))
    answer = orchestrator.ask("Prédis ces lignes avec le modèle auquel tu as accès")
    # clarification propre (error=null), pas de « KeyError: modèle inconnu : '' »
    assert answer.error is None
    for name in ("maxizoo_sales", "autre_modele"):
        assert name in answer.answer
    assert answer.answer.strip().endswith("?")
    assert [s.node for s in answer.trace] == ["plan", "synthesize"]


def test_predict_sans_modele_un_seul_modele_repli_auto(registry: Registry):
    """Un seul modèle au registre : on le prend d'office plutôt que de demander."""
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset=None, features=VENTES_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)  # registre à 1 modèle
    answer = orchestrator.ask("Prédis les ventes de ce produit")
    assert answer.error is None
    assert answer.plan.dataset == "maxizoo_sales"
    assert "4.1391" in answer.answer


def test_query_sur_maxizoo_renvoie_les_colonnes(registry: Registry, maxizoo_source):
    """La base Maxizoo est interrogeable en SQL : les attributs remontent."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="maxizoo"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM stores"}),
                text("Colonnes du référentiel magasins."),
            ],
        )
    )
    catalog = Catalog(sources=[maxizoo_source])
    orchestrator = orchestrator_with(llm, catalog=catalog, registry=registry)
    answer = orchestrator.ask("Donne-moi le référentiel des magasins")
    assert answer.error is None
    table = json.loads(answer.artifacts[0].data)
    assert table["columns"] == [
        "store_id",
        "store_name",
        "region",
        "store_type",
        "surface_m2",
        "population",
        "latitude",
        "longitude",
        "is_online",
    ]
    # le canal e-commerce est bien une LIGNE du référentiel (piège n°1)
    assert any(row[0] == "ONLINE" for row in table["rows"])


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
        [plan_response(Plan(capability="predict", dataset="maxizoo_sales", features=VENTES_OK))],
    )
    orchestrator = orchestrator_with(llm, registry=registry)
    with caplog.at_level(logging.INFO, logger="data_analyst_agent.orchestrator"):
        orchestrator.ask("Prédis les ventes")
    messages = [record.getMessage() for record in caplog.records]
    assert any("nœud plan : terminé" in m for m in messages)
    assert any("nœud inference : terminé" in m for m in messages)
    assert any("nœud synthesize : terminé" in m for m in messages)
