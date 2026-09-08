"""Replis et cas limites du graphe restés hors couverture (audit §5.1).

Chacun correspond à un comportement que le code décide déjà : un catalogue vide,
un modèle enregistré sans schéma de features, un repli qui n'a rien à reprendre,
un lot dont aucune ligne ne passe la validation, un résultat coupé par la limite
de lignes. Rien ici n'est écrit pour faire monter un chiffre : ce sont les
chemins que personne ne regarde et où la prochaine régression passera.

Fichier séparé de ``test_graph.py`` à dessein : la branche Maxizoo le réécrit
presque entièrement (audit §8.3), et ces tests-ci n'ont pas à disparaître avec.
"""

from pathlib import Path

import joblib
import pytest
from pydantic_ai import UnexpectedModelBehavior

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator, PendingInference
from data_analyst_agent.orchestrator.plan import Plan
from helpers.doubles import FakeClassifier, FakeRegressor
from helpers.scripted_llm import PLANNER, RETRIEVAL, ScriptedLLM, plan_response, text, tool_call

TITANIC_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
    labels:
      "0": "n'a pas survécu"
      "1": "a survécu"
"""

# Un modèle enregistré dont aucun schéma Pydantic ne décrit les features. Ce
# n'est pas une hypothèse : `SCHEMAS` ne connaît que les datasets jouets, et
# tout modèle métier ajouté au registre passe par là avant d'avoir son schéma.
SANS_SCHEMA_YAML = """
models:
  - dataset: ventes
    task: regression
    model_path: ventes.joblib
    target: quantite
"""

MAXIZOO_YAML = """
models:
  - dataset: maxizoo_sales
    task: regression
    model_path: maxizoo_sales.joblib
    target: quantity
    unit: unités vendues
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def registre(tmp_path: Path, yaml_source: str, artefact: str, modele) -> Registry:
    (tmp_path / "registry.yaml").write_text(yaml_source, encoding="utf-8")
    joblib.dump(modele, tmp_path / artefact)
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    return registre(tmp_path, TITANIC_YAML, "titanic.joblib", FakeClassifier())


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return csv


def orchestrateur(llm: ScriptedLLM, **kwargs) -> Orchestrator:
    kwargs.setdefault("catalog", Catalog(sources=[]))
    kwargs.setdefault("settings", make_settings())
    return Orchestrator(model=llm.model(), **kwargs)


def requete_sur(nom: str) -> ScriptedLLM:
    return (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source=nom))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": f"SELECT * FROM {nom}"}), text("Voici.")],
        )
    )


# --- résolution de source -------------------------------------------------------


def test_question_de_donnees_sans_aucune_source_declaree(registry: Registry):
    """Catalogue vide : le nœud échoue proprement, sans fuir la cause technique.

    Le planificateur ne demande PAS de préciser dans ce cas (il ne le fait qu'à
    partir de deux sources) : c'est donc la résolution, dans le nœud, qui
    tranche — et elle ne doit pas laisser remonter un ``KeyError`` brut.
    """
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source=None))])
    reponse = orchestrateur(llm, registry=registry).ask("Combien de lignes ?")

    assert reponse.error is not None
    assert "la source de données n'a pas pu être interrogée" in reponse.error
    assert "KeyError" not in reponse.answer
    etape = next(s for s in reponse.trace if s.node == "retrieval")
    assert "catalogue vide" in etape.detail  # la cause reste, côté trace


# --- description des modèles au planificateur -----------------------------------


def test_modele_sans_schema_de_features_est_annonce_comme_tel(tmp_path: Path, mini_csv: Path):
    """Le planificateur doit voir le modèle même sans savoir ce qu'il attend.

    Le taire reviendrait à rendre le modèle inatteignable ; prétendre connaître
    ses features reviendrait à en inventer.
    """
    llm = requete_sur("mini")
    reponse = orchestrateur(
        llm,
        catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]),
        registry=registre(tmp_path, SANS_SCHEMA_YAML, "ventes.joblib", FakeRegressor()),
    ).ask("Combien de lignes ?")

    assert reponse.error is None
    assert "ventes (regression) : features attendues : ?" in llm.systems_for(PLANNER)[0]


def test_prediction_en_attente_sur_un_modele_sans_schema(tmp_path: Path, mini_csv: Path):
    """Sans schéma, on ne peut pas énumérer ce qui manque — on le dit, on ne devine pas."""
    llm = requete_sur("mini")
    reponse = orchestrateur(
        llm,
        catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]),
        registry=registre(tmp_path, SANS_SCHEMA_YAML, "ventes.joblib", FakeRegressor()),
    ).ask(
        "Combien de lignes ?",
        pending=PendingInference(dataset="ventes", features={"magasin": "Lyon"}),
    )

    assert reponse.error is None
    prompt = llm.systems_for(PLANNER)[0]
    assert "Features déjà connues : magasin='Lyon'" in prompt
    assert "Il manque : ?" in prompt


# --- repli du planificateur -----------------------------------------------------


def test_repli_impossible_apres_un_tour_sans_source(
    tmp_path: Path, registry: Registry, monkeypatch
):
    """Le tour précédent était une prédiction : il n'y a aucune source à reprendre.

    Le repli sur « ajustement du tour précédent » ne vaut que pour une action
    qui interrogeait une source. Sinon, redemander est la seule réponse honnête
    — et c'est un chemin distinct du tout premier tour, où il n'y a pas même de
    conversation.
    """
    modeles = registre(tmp_path, MAXIZOO_YAML, "maxizoo_sales.joblib", FakeRegressor())
    orchestrateur_predict = orchestrateur(
        ScriptedLLM().script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="predict",
                        dataset="maxizoo_sales",
                        features={
                            "store_type": "grand",
                            "commodity_group": "Chien",
                            "brand_type": "nationale",
                            "base_price": 49.9,
                            "day_of_week": 5,
                            "month": 11,
                            "discount_rate": 0.0,
                            "promo_type": "aucune",
                            "temp_anomaly": 0.0,
                        },
                    )
                )
            ],
        ),
        registry=modeles,
    )
    premier = orchestrateur_predict.ask(
        "Prédis les ventes", conversation_id="fil-1", workspace_root=tmp_path / "ws"
    )
    assert premier.error is None  # le tour mémorisé est bien une prédiction

    class _PlanificateurQuiEchoue:
        def run_sync(self, *args, **kwargs):
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")

    monkeypatch.setattr(
        "data_analyst_agent.orchestrator.graph.planner_agent",
        lambda *args, **kwargs: _PlanificateurQuiEchoue(),
    )
    second = orchestrateur(ScriptedLLM(), registry=modeles).ask(
        "euh... et sinon ?", conversation_id="fil-1", workspace_root=tmp_path / "ws"
    )

    assert second.error is None
    assert second.answer.strip().endswith("?")
    assert [s.node for s in second.trace] == ["system", "plan", "synthesize"]


# --- récupération puis prédiction : ligne incomplète ----------------------------


def test_ligne_recuperee_incomplete_retient_l_acquis(tmp_path: Path):
    """La ligne lue ne suffit pas : on relance, en gardant ce qu'elle a donné.

    Sans ce ``pending``, le tour suivant repartirait de zéro et redemanderait
    l'attribut que la source venait de fournir.
    """
    csv = tmp_path / "partiel.csv"
    csv.write_text("sku_id,commodity_group\nSKU001,Chien\n", encoding="utf-8")
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="partiel",
                        dataset="maxizoo_sales",
                        data_question="Le SKU001",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM partiel WHERE sku_id = 'SKU001'"}),
                text("Une ligne."),
            ],
        )
    )
    reponse = orchestrateur(
        llm,
        catalog=Catalog(sources=[FileSource(name="partiel", path=csv)]),
        registry=registre(tmp_path, MAXIZOO_YAML, "maxizoo_sales.joblib", FakeRegressor()),
    ).ask("Prédis les ventes du SKU001")

    assert reponse.error is None
    assert reponse.pending is not None
    assert reponse.pending.dataset == "maxizoo_sales"
    assert reponse.pending.features == {"commodity_group": "Chien"}
    assert "store_type" in reponse.answer  # la relance nomme ce qui manque


# --- synthèse -------------------------------------------------------------------


def test_un_plan_sans_resultat_ne_rend_pas_une_reponse_vide(registry: Registry):
    """Garde-fou de la synthèse : aucun chemin du graphe n'y mène aujourd'hui.

    Les quatre capacités renseignent toutes leur sortie, et un échec renseigne
    ``error`` — ce cas est donc le filet qui protège d'un nœud futur qui
    oublierait la sienne. Il vaut mieux une phrase qu'une réponse vide, et
    mieux vaut le vérifier qu'y découvrir un ``KeyError`` le jour venu.
    """
    orchestrateur_nu = orchestrateur(ScriptedLLM(), registry=registry)

    update = orchestrateur_nu._synthesize_node({"plan": Plan(capability="query")})

    assert update["answer"] == "Je n'ai rien produit pour cette question."
    assert update["trace"][0].detail == "vide"


def test_resultat_multi_lignes_tronque_le_dit(mini_csv: Path, registry: Registry):
    """La limite de lignes doit s'annoncer : sinon le tableau passe pour complet."""
    llm = requete_sur("mini")
    reponse = orchestrateur(
        llm,
        catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]),
        registry=registry,
        settings=make_settings(retrieval_max_rows=2),
    ).ask("Donne-moi les lignes")

    assert reponse.error is None
    assert "2 lignes retournées" in reponse.answer
    assert "tronqué par la limite de lignes" in reponse.answer


# --- prédiction en lot ----------------------------------------------------------


def test_lot_dont_aucune_ligne_ne_passe_la_validation(tmp_path: Path):
    """Zéro prédiction n'est pas une erreur : c'est un résultat, et il se dit.

    Les deux lignes portent un `base_price` hors des bornes du schéma (0 < prix
    <= 500) : elles sont complètes et pourtant refusées.
    """
    csv = tmp_path / "groupe.csv"
    entete = (
        "store_type,commodity_group,brand_type,base_price,day_of_week,"
        "month,discount_rate,promo_type,temp_anomaly\n"
    )
    csv.write_text(
        entete
        + "grand,Chien,nationale,-5,5,11,0,aucune,0\n"
        + "moyen,Chat,nationale,-7,2,3,0,aucune,0\n",
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
                        dataset="maxizoo_sales",
                        data_question="Tous les SKU du groupe",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT * FROM groupe"}),
                text("2 lignes."),
            ],
        )
    )
    reponse = orchestrateur(
        llm,
        catalog=Catalog(sources=[FileSource(name="groupe", path=csv)]),
        registry=registre(tmp_path, MAXIZOO_YAML, "maxizoo_sales.joblib", FakeRegressor()),
    ).ask("Prédis les ventes de tous les SKU")

    assert reponse.error is None
    assert "Aucune des 2 lignes récupérées n'a passé la validation" in reponse.answer
    assert "pas de prédiction" in reponse.answer


def test_lot_de_regression_resume_par_moyenne_et_bornes(tmp_path: Path):
    """Une régression n'a pas de classes à répartir : on rend moyenne, min et max.

    Le lot est en outre coupé par ``retrieval_max_rows`` : la réponse doit le
    dire, sinon la moyenne passe pour celle de l'ensemble demandé.
    """
    csv = tmp_path / "ventes.csv"
    entete = (
        "store_type,commodity_group,brand_type,base_price,day_of_week,"
        "month,discount_rate,promo_type,temp_anomaly\n"
    )
    ligne = "grand,Chien,nationale,49.9,5,11,0,aucune,0\n"
    csv.write_text(entete + ligne * 3, encoding="utf-8")
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="ventes",
                        dataset="maxizoo_sales",
                        data_question="Toutes les lignes",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT * FROM ventes"}), text("3 lignes.")],
        )
    )
    reponse = orchestrateur(
        llm,
        catalog=Catalog(sources=[FileSource(name="ventes", path=csv)]),
        registry=registre(tmp_path, MAXIZOO_YAML, "maxizoo_sales.joblib", FakeRegressor()),
        settings=make_settings(retrieval_max_rows=2),
    ).ask("Prédis les ventes de toutes les lignes")

    assert reponse.error is None
    assert "sur 2 lignes" in reponse.answer
    assert "moyenne 4.139 unités vendues" in reponse.answer
    assert "min 4.139, max 4.139" in reponse.answer
    assert "Résultat tronqué par la limite de lignes" in reponse.answer
