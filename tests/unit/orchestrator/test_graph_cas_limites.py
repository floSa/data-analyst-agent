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
from helpers.correspondances import identite
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

CALIFORNIA_YAML = """
models:
  - dataset: california_housing
    task: regression
    model_path: california.joblib
    target: med_house_value
    unit: centaines de milliers de dollars
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
    orchestrateur_predict = orchestrateur(
        ScriptedLLM().script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="predict",
                        dataset="titanic",
                        features={
                            "sex": "female",
                            "pclass": 1,
                            "age": 28.0,
                            "sibsp": 0,
                            "parch": 0,
                            "fare": 80.0,
                            "embarked": "S",
                        },
                    )
                )
            ],
        ),
        registry=registry,
    )
    premier = orchestrateur_predict.ask(
        "Prédis la survie", conversation_id="fil-1", workspace_root=tmp_path / "ws"
    )
    assert premier.error is None  # le tour mémorisé est bien une prédiction

    class _PlanificateurQuiEchoue:
        def run_sync(self, *args, **kwargs):
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")

    monkeypatch.setattr(
        "data_analyst_agent.orchestrator.graph.planner_agent",
        lambda *args, **kwargs: _PlanificateurQuiEchoue(),
    )
    second = orchestrateur(ScriptedLLM(), registry=registry).ask(
        "euh... et sinon ?", conversation_id="fil-1", workspace_root=tmp_path / "ws"
    )

    assert second.error is None
    assert second.answer.strip().endswith("?")
    assert [s.node for s in second.trace] == ["system", "plan", "synthesize"]


# --- récupération puis prédiction : ligne incomplète ----------------------------


def test_ligne_recuperee_incomplete_retient_l_acquis(tmp_path: Path, registry: Registry):
    """La ligne lue ne suffit pas : on relance, en gardant ce qu'elle a donné.

    Sans ce ``pending``, le tour suivant repartirait de zéro et redemanderait
    l'attribut que la source venait de fournir.

    La source porte bien toutes les colonnes déclarées — c'est la REQUÊTE qui
    n'en ramène qu'une. Les deux cas se ressemblaient tant que rien ne relisait
    la déclaration ; ils sont désormais distincts, et c'est celui-ci qui laisse
    un acquis à garder. Une déclaration qui nommerait une colonne absente serait
    refusée avant la requête, sans rien à retenir.
    """
    csv = tmp_path / "partiel.csv"
    csv.write_text(
        "passenger_id,sex,pclass,age,sibsp,parch,fare,embarked\n1,female,1,28,0,0,80.0,S\n",
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
                        source="partiel",
                        dataset="titanic",
                        data_question="Le passager 1",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [
                # l'agent ne ramène qu'une colonne des sept demandées
                tool_call(
                    "run_sql",
                    {"query": "SELECT sex FROM partiel WHERE passenger_id = 1"},
                ),
                text("Une ligne."),
            ],
        )
    )
    reponse = orchestrateur(
        llm,
        catalog=Catalog(
            sources=[FileSource(name="partiel", path=csv, features=identite("titanic"))]
        ),
        registry=registry,
    ).ask("Prédis la survie du passager 1")

    assert reponse.error is None
    assert reponse.pending is not None
    assert reponse.pending.dataset == "titanic"
    assert reponse.pending.features == {"sex": "female"}
    assert "pclass" in reponse.answer  # la relance nomme ce qui manque


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


def test_lot_dont_aucune_ligne_ne_passe_la_validation(tmp_path: Path, registry: Registry):
    """Zéro prédiction n'est pas une erreur : c'est un résultat, et il se dit."""
    csv = tmp_path / "groupe.csv"
    csv.write_text(
        "passenger_id,sex,pclass,age,sibsp,parch,fare,embarked\n"
        "1,female,1,-5,0,0,80.0,S\n"
        "2,female,2,-7,0,0,20.0,S\n",
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
                tool_call("run_sql", {"query": "SELECT * FROM groupe"}),
                text("2 lignes."),
            ],
        )
    )
    reponse = orchestrateur(
        llm,
        catalog=Catalog(
            sources=[FileSource(name="groupe", path=csv, features=identite("titanic"))]
        ),
        registry=registry,
    ).ask("Prédis la survie de toutes les femmes")

    assert reponse.error is None
    assert "Aucune des 2 lignes récupérées n'a passé la validation" in reponse.answer
    assert "pas de prédiction" in reponse.answer


def test_lot_de_regression_resume_par_moyenne_et_bornes(tmp_path: Path):
    """Une régression n'a pas de classes à répartir : on rend moyenne, min et max.

    Le lot est en outre coupé par ``retrieval_max_rows`` : la réponse doit le
    dire, sinon la moyenne passe pour celle de l'ensemble demandé.
    """
    csv = tmp_path / "ilots.csv"
    entete = "med_inc,house_age,ave_rooms,ave_bedrms,population,ave_occup,latitude,longitude\n"
    ligne = "3.5,25,5.2,1.1,1200,2.8,34.1,-118.3\n"
    csv.write_text(entete + ligne * 3, encoding="utf-8")
    llm = (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(
                    Plan(
                        capability="fetch_then_predict",
                        source="ilots",
                        dataset="california_housing",
                        data_question="Tous les îlots",
                    )
                )
            ],
        )
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT * FROM ilots"}), text("3 lignes.")],
        )
    )
    reponse = orchestrateur(
        llm,
        catalog=Catalog(
            sources=[FileSource(name="ilots", path=csv, features=identite("california_housing"))]
        ),
        registry=registre(tmp_path, CALIFORNIA_YAML, "california.joblib", FakeRegressor()),
        settings=make_settings(retrieval_max_rows=2),
    ).ask("Prédis le prix de tous les îlots")

    assert reponse.error is None
    assert "sur 2 lignes" in reponse.answer
    assert "moyenne 4.139 centaines de milliers de dollars" in reponse.answer
    assert "min 4.139, max 4.139" in reponse.answer
    assert "Résultat tronqué par la limite de lignes" in reponse.answer
