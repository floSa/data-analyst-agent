"""Les règles d'ajustement du plan, une par une, et leur ordre (audit §5.1).

`_plan_node` enchaînait huit décisions indépendantes dans un bloc de cent
cinquante lignes : l'ordre des règles y était significatif mais implicite, et
aucune ne pouvait être vérifiée sans faire tourner tout un tour de graphe. Le
découpage donne à chacune un nom, une raison d'être et une place explicite —
ce fichier est ce que ce découpage permet.

Chaque test appelle UNE règle, sur un plan et un contexte construits à la main.
La vérification de l'ordre est un test à part : c'est le seul endroit où il est
écrit, et le déplacer doit être un geste conscient.
"""

from pathlib import Path

import joblib
import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator, PendingInference, PlanContext
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from helpers.doubles import FakeClassifier, FakeRegressor
from helpers.scripted_llm import ScriptedLLM

TITANIC_COMPLET = {
    "sex": "female",
    "pclass": 1,
    "age": 28.0,
    "sibsp": 0,
    "parch": 0,
    "fare": 80.0,
    "embarked": "S",
}

UN_MODELE_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
"""

DEUX_MODELES_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
  - dataset: iris
    task: classification
    model_path: iris.joblib
    target: species
"""


def registre(dossier: Path, yaml_source: str) -> Registry:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "registry.yaml").write_text(yaml_source, encoding="utf-8")
    for artefact in ("titanic.joblib", "iris.joblib"):
        joblib.dump(FakeClassifier(), dossier / artefact)
    return Registry.load(dossier / "registry.yaml")


@pytest.fixture
def orchestrateur(tmp_path: Path) -> Orchestrator:
    """Un orchestrateur nu : les règles n'appellent jamais le LLM."""
    return Orchestrator(
        model=ScriptedLLM().model(),
        catalog=Catalog(sources=[]),
        registry=registre(tmp_path / "registre_1", UN_MODELE_YAML),
        settings=Settings(_env_file=None),
    )


def source(nom: str, tmp_path: Path) -> FileSource:
    chemin = tmp_path / f"{nom}.csv"
    chemin.write_text("a\n1\n", encoding="utf-8")
    return FileSource(name=nom, path=chemin)


def contexte(
    *,
    source_imposee: str | None = None,
    pending: PendingInference | None = None,
    workspace: ConversationWorkspace | None = None,
    declare: list[FileSource] | None = None,
    effectif: list[FileSource] | None = None,
    question: str = "une question quelconque",
    source_de_travail: str | None = None,
) -> PlanContext:
    """Un contexte de règles. ``effectif`` vaut ``declare`` à défaut.

    ``source_de_travail`` à ``None`` est le cas « hors conversation » : c'est
    le défaut, et c'est ce qui fait que les règles historiques se testent ici
    exactement comme avant que la source de travail existe.
    """
    declarees = declare or []
    return PlanContext(
        source_imposee=source_imposee,
        pending=pending,
        workspace=workspace,
        catalogue_declare=Catalog(sources=declarees),
        catalogue_effectif=Catalog(sources=effectif if effectif is not None else declarees),
        question=question,
        source_de_travail=source_de_travail,
    )


# --- l'ordre ---------------------------------------------------------------------


def test_l_ordre_des_regles_est_explicite_et_verrouille():
    """L'ordre EST le comportement : le changer doit être un geste conscient.

    Quatre dépendances au moins sont réelles : la source imposée doit précéder
    toute règle qui raisonne sur la source ; la dégradation faute de source doit
    précéder la reprise des features (sinon un `fetch_then_predict` dégradé
    n'hérite de rien) et la source de la conversation (sinon on lie une source à
    une capacité qui vient de la perdre) ; le chaînage sur le dernier tableau
    doit venir après le choix du modèle et la reprise des features (il se
    déclenche sur leur absence).

    Une cinquième depuis : la lecture de « sans famille à bord » vient après le
    choix du modèle — elle a besoin du dataset résolu pour savoir quel schéma
    interroger — et avant le chaînage sur le dernier tableau, qui ne se
    déclenche que sur des features vides.
    """
    assert [regle.__name__ for regle in Orchestrator._REGLES_DU_PLAN] == [
        "_regle_source_imposee",
        "_regle_degrader_faute_de_source",
        "_regle_source_de_la_conversation",
        "_regle_reprendre_les_features_acquises",
        "_regle_normaliser_le_nom_de_source",
        "_regle_choisir_la_source",
        "_regle_choisir_le_modele",
        "_regle_lire_labsence_daccompagnants",
        "_regle_chainer_sur_le_dernier_tableau",
    ]


def test_la_premiere_question_court_circuite_les_suivantes(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Un plan qu'on va renvoyer à l'utilisateur n'a pas à continuer d'être ajusté."""
    plan = Plan(capability="query", source="inconnue")

    question = orchestrateur._appliquer_les_regles(
        plan, contexte(declare=[source("mini", tmp_path), source("autre", tmp_path)])
    )

    # la règle de normalisation a tranché ; celle d'ambiguïté n'a pas reposé la sienne
    assert question is not None
    assert "introuvable" in question


# --- 1. source imposée -----------------------------------------------------------


def test_source_imposee_ecrase_celle_du_plan(orchestrateur: Orchestrator):
    plan = Plan(capability="query", source="celle-du-llm")

    assert orchestrateur._regle_source_imposee(plan, contexte(source_imposee="mini")) is None
    assert plan.source == "mini"


def test_sans_source_imposee_le_plan_est_intact(orchestrateur: Orchestrator):
    plan = Plan(capability="query", source="celle-du-llm")

    orchestrateur._regle_source_imposee(plan, contexte())

    assert plan.source == "celle-du-llm"


# --- 2. dégradation faute de source ---------------------------------------------


def test_fetch_then_predict_sans_aucune_source_retombe_en_predict(orchestrateur: Orchestrator):
    plan = Plan(capability="fetch_then_predict", dataset="titanic")

    assert orchestrateur._regle_degrader_faute_de_source(plan, contexte()) is None
    assert plan.capability == "predict"


def test_fetch_then_predict_garde_sa_capacite_des_qu_une_source_existe(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Y compris quand la seule source est un tableau mémorisé du fil."""
    plan = Plan(capability="fetch_then_predict", dataset="titanic")

    orchestrateur._regle_degrader_faute_de_source(
        plan, contexte(effectif=[source("resultat_1", tmp_path)])
    )

    assert plan.capability == "fetch_then_predict"


def test_une_requete_sans_source_n_est_pas_degradee(orchestrateur: Orchestrator):
    """La règle ne vise que le chaînage : `query` sans source a son propre chemin."""
    plan = Plan(capability="query")

    orchestrateur._regle_degrader_faute_de_source(plan, contexte())

    assert plan.capability == "query"


# --- 3. reprise des features acquises -------------------------------------------


def test_le_pending_est_fusionne_le_nouveau_message_primant(orchestrateur: Orchestrator):
    plan = Plan(capability="predict", dataset="titanic", features={"pclass": 3})
    pending = PendingInference(dataset="titanic", features={"sex": "female", "pclass": 1})

    orchestrateur._regle_reprendre_les_features_acquises(plan, contexte(pending=pending))

    assert plan.features == {"sex": "female", "pclass": 3}  # le 3 du message l'emporte


def test_le_pending_fournit_le_dataset_quand_le_plan_l_omet(orchestrateur: Orchestrator):
    plan = Plan(capability="predict", features={"age": 30})
    pending = PendingInference(dataset="titanic", features={"sex": "female"})

    orchestrateur._regle_reprendre_les_features_acquises(plan, contexte(pending=pending))

    assert plan.dataset == "titanic"
    assert plan.features == {"sex": "female", "age": 30}


def test_un_pending_d_un_autre_dataset_n_est_pas_fusionne(orchestrateur: Orchestrator):
    """Une digression n'hérite de rien : mélanger deux schémas ne prédirait rien de bon."""
    plan = Plan(capability="predict", dataset="iris", features={"petal_length": 1.4})
    pending = PendingInference(dataset="titanic", features={"sex": "female"})

    orchestrateur._regle_reprendre_les_features_acquises(plan, contexte(pending=pending))

    assert plan.features == {"petal_length": 1.4}


def test_les_features_d_une_prediction_aboutie_sont_reprises(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Le pending est vidé dès qu'une prédiction réussit : sans cet acquis, on redemande."""
    workspace = ConversationWorkspace(tmp_path / "ws", "fil-1")
    workspace.record_turn(
        "Prédis la survie", "predict", None, dataset="titanic", features=TITANIC_COMPLET
    )
    plan = Plan(capability="predict", dataset="titanic", features={"pclass": 3})

    orchestrateur._regle_reprendre_les_features_acquises(plan, contexte(workspace=workspace))

    assert plan.features == {**TITANIC_COMPLET, "pclass": 3}


def test_le_pending_l_emporte_sur_la_prediction_aboutie(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Les deux acquis existent, jamais ensemble : le slot-filling en cours passe devant."""
    workspace = ConversationWorkspace(tmp_path / "ws", "fil-1")
    workspace.record_turn(
        "Prédis la survie", "predict", None, dataset="titanic", features={"embarked": "Q"}
    )
    plan = Plan(capability="predict", dataset="titanic")
    pending = PendingInference(dataset="titanic", features={"sex": "female"})

    orchestrateur._regle_reprendre_les_features_acquises(
        plan, contexte(pending=pending, workspace=workspace)
    )

    assert plan.features == {"sex": "female"}


# --- 4. normalisation du nom de source ------------------------------------------


def test_un_nom_de_source_decore_par_le_llm_est_ramene_au_catalogue(
    orchestrateur: Orchestrator, tmp_path: Path
):
    plan = Plan(capability="query", source="titanic (postgres)")

    assert (
        orchestrateur._regle_normaliser_le_nom_de_source(
            plan, contexte(declare=[source("titanic", tmp_path)])
        )
        is None
    )
    assert plan.source == "titanic"


def test_une_source_vraiment_inconnue_provoque_une_question(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Plutôt qu'un KeyError brut remonté depuis le nœud de capacité."""
    plan = Plan(capability="query", source="ventes")

    question = orchestrateur._regle_normaliser_le_nom_de_source(
        plan, contexte(declare=[source("titanic", tmp_path)])
    )

    assert question is not None
    assert "« ventes » est introuvable" in question
    assert "titanic" in question


def test_une_designation_qui_empaquette_deux_sources_declarees_ne_dit_pas_introuvable(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """« ventes ou production ? » : la question est juste, la phrase était fausse.

    Mesuré à travers le graphe le 2026-09-17, catalogue métier : aucun outil
    appelé côté agent système, le planificateur classe `query` et rend
    ``source="ventes, production"`` — la concaténation des deux noms qu'il a lus.
    L'utilisateur lisait « La source « ventes, production » est introuvable. Sur
    quelle source veux-tu travailler : ventes, production, stocks, iris,
    titanic ? », donc une source déclarée introuvable ET proposée dans la même
    phrase. La question reste ; la phrase part.
    """
    plan = Plan(capability="query", source="ventes, production")

    question = orchestrateur._regle_normaliser_le_nom_de_source(
        plan,
        contexte(declare=[source("ventes", tmp_path), source("production", tmp_path)]),
    )

    assert question == "Sur quelle source veux-tu travailler : ventes, production ?"
    assert "introuvable" not in question


def test_un_seul_nom_inconnu_garde_sa_phrase(orchestrateur: Orchestrator, tmp_path: Path):
    """L'autre bord : une faute de frappe ne nomme aucune source déclarée.

    C'est là que « introuvable » est l'information utile — celui qui écrit
    « comptabilite » a besoin de savoir que ce n'est pas un nom d'ici, et pas
    seulement qu'on lui repose la question.
    """
    plan = Plan(capability="query", source="comptabilite")

    question = orchestrateur._regle_normaliser_le_nom_de_source(
        plan, contexte(declare=[source("ventes", tmp_path), source("production", tmp_path)])
    )

    assert question is not None
    assert "« comptabilite » est introuvable" in question
    assert "Sur quelle source veux-tu travailler : ventes, production ?" in question


def test_source_inconnue_et_catalogue_vide_le_dit_sans_liste_vide(orchestrateur: Orchestrator):
    question = orchestrateur._regle_normaliser_le_nom_de_source(
        Plan(capability="query", source="ventes"), contexte()
    )

    assert question is not None
    assert "(aucune)" in question


def test_un_plan_sans_source_traverse_la_normalisation(orchestrateur: Orchestrator):
    plan = Plan(capability="query")

    assert orchestrateur._regle_normaliser_le_nom_de_source(plan, contexte()) is None
    assert plan.source is None


# --- 5. choix de la source -------------------------------------------------------


def test_plusieurs_sources_declarees_et_aucun_choix_pose_la_question(
    orchestrateur: Orchestrator, tmp_path: Path
):
    question = orchestrateur._regle_choisir_la_source(
        Plan(capability="query"),
        contexte(declare=[source("mini", tmp_path), source("titanic", tmp_path)]),
    )

    # C'est la PROPOSITION du démarrage de conversation : les noms, ce que le
    # catalogue en dit, et la demande de choisir. L'ancienne version énumérait
    # deux noms nus (« Sur quelle source veux-tu travailler : mini, titanic ? »),
    # ce qui ne permet pas de choisir quand on découvre l'agent.
    assert question is not None
    assert "mini" in question
    assert "titanic" in question
    assert question.strip().endswith("?")


def test_une_seule_source_declaree_ne_pose_pas_de_question(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Le repli sur l'unique source est posé par `_regle_source_de_la_conversation`
    (puis annoncé), pas demandé ici."""
    assert (
        orchestrateur._regle_choisir_la_source(
            Plan(capability="query"), contexte(declare=[source("mini", tmp_path)])
        )
        is None
    )


def test_les_tableaux_memorises_ne_font_pas_reposer_la_question(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """C'est pour cela que la règle lit le catalogue DÉCLARÉ et non l'effectif.

    Un fil qui a mémorisé des tableaux verrait sinon la question revenir à
    chaque tour, alors que l'unique source déclarée reste le choix évident.
    """
    declarees = [source("mini", tmp_path)]
    assert (
        orchestrateur._regle_choisir_la_source(
            Plan(capability="query"),
            contexte(declare=declarees, effectif=[*declarees, source("resultat_1", tmp_path)]),
        )
        is None
    )


def test_une_prediction_pure_ne_demande_aucune_source(orchestrateur: Orchestrator, tmp_path: Path):
    assert (
        orchestrateur._regle_choisir_la_source(
            Plan(capability="predict", dataset="titanic"),
            contexte(declare=[source("mini", tmp_path), source("titanic", tmp_path)]),
        )
        is None
    )


# --- 6. choix du modèle ----------------------------------------------------------


def test_un_seul_modele_est_choisi_d_office(orchestrateur: Orchestrator):
    plan = Plan(capability="predict")

    assert orchestrateur._regle_choisir_le_modele(plan, contexte()) is None
    assert plan.dataset == "titanic"


def test_plusieurs_modeles_et_aucun_choix_pose_la_question(tmp_path: Path):
    orchestrateur = Orchestrator(
        model=ScriptedLLM().model(),
        catalog=Catalog(sources=[]),
        registry=registre(tmp_path / "registre_2", DEUX_MODELES_YAML),
        settings=Settings(_env_file=None),
    )

    question = orchestrateur._regle_choisir_le_modele(Plan(capability="predict"), contexte())

    assert question == "Sur quel modèle veux-tu prédire : iris, titanic ?"


def test_un_registre_vide_ne_pose_pas_de_question(tmp_path: Path):
    """Il n'y a rien à demander : l'inférence dira que le modèle est inconnu."""
    (tmp_path / "registry.yaml").write_text("models: []\n", encoding="utf-8")
    orchestrateur = Orchestrator(
        model=ScriptedLLM().model(),
        catalog=Catalog(sources=[]),
        registry=Registry.load(tmp_path / "registry.yaml"),
        settings=Settings(_env_file=None),
    )

    plan = Plan(capability="predict")

    assert orchestrateur._regle_choisir_le_modele(plan, contexte()) is None
    assert plan.dataset is None


def test_un_modele_deja_choisi_n_est_pas_touche(orchestrateur: Orchestrator):
    plan = Plan(capability="predict", dataset="california_housing")

    orchestrateur._regle_choisir_le_modele(plan, contexte())

    assert plan.dataset == "california_housing"


def test_une_requete_ne_reclame_aucun_modele(orchestrateur: Orchestrator):
    plan = Plan(capability="query", source="mini")

    assert orchestrateur._regle_choisir_le_modele(plan, contexte()) is None
    assert plan.dataset is None


# --- 7. chaînage sur le dernier tableau ------------------------------------------


def _workspace_avec_tableau(tmp_path: Path, colonnes: list[str]) -> ConversationWorkspace:
    workspace = ConversationWorkspace(tmp_path / "ws", "fil-1")
    workspace.save_table(colonnes, [[1] * len(colonnes)], "Donne-moi les passagers")
    return workspace


def test_predict_sans_features_chaine_sur_le_dernier_tableau(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """« Prédis ces lignes » : le LLM route en predict, les valeurs sont déjà affichées."""
    workspace = _workspace_avec_tableau(
        tmp_path, ["Sex", "Pclass", "Age", *(["sibsp", "parch", "fare", "embarked"])]
    )
    plan = Plan(capability="predict", dataset="titanic")

    assert (
        orchestrateur._regle_chainer_sur_le_dernier_tableau(plan, contexte(workspace=workspace))
        is None
    )
    assert plan.capability == "fetch_then_predict"
    assert plan.source == workspace.injected[-1].name
    assert plan.data_question == f"toutes les lignes de {workspace.injected[-1].name}"


def test_un_tableau_qui_ne_couvre_pas_les_features_ne_declenche_rien(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Chaîner dessus produirait une relance sur des colonnes absentes."""
    workspace = _workspace_avec_tableau(tmp_path, ["sex", "pclass"])
    plan = Plan(capability="predict", dataset="titanic")

    orchestrateur._regle_chainer_sur_le_dernier_tableau(plan, contexte(workspace=workspace))

    assert plan.capability == "predict"


def test_des_features_deja_donnees_empechent_le_chainage(
    orchestrateur: Orchestrator, tmp_path: Path
):
    """Le message décrit un cas hypothétique : il ne parle pas du tableau affiché."""
    workspace = _workspace_avec_tableau(
        tmp_path, ["sex", "pclass", "age", "sibsp", "parch", "fare", "embarked"]
    )
    plan = Plan(capability="predict", dataset="titanic", features={"sex": "female"})

    orchestrateur._regle_chainer_sur_le_dernier_tableau(plan, contexte(workspace=workspace))

    assert plan.capability == "predict"


def test_un_dataset_sans_schema_ne_declenche_pas_le_chainage(tmp_path: Path):
    """On ne sait pas quelles colonnes il faudrait : impossible de vérifier la couverture."""
    (tmp_path / "registry.yaml").write_text(
        "models:\n  - dataset: ventes\n    task: regression\n"
        "    model_path: ventes.joblib\n    target: quantite\n",
        encoding="utf-8",
    )
    joblib.dump(FakeRegressor(), tmp_path / "ventes.joblib")
    orchestrateur = Orchestrator(
        model=ScriptedLLM().model(),
        catalog=Catalog(sources=[]),
        registry=Registry.load(tmp_path / "registry.yaml"),
        settings=Settings(_env_file=None),
    )
    workspace = _workspace_avec_tableau(tmp_path, ["magasin", "semaine"])
    plan = Plan(capability="predict", dataset="ventes")

    orchestrateur._regle_chainer_sur_le_dernier_tableau(plan, contexte(workspace=workspace))

    assert plan.capability == "predict"


def test_sans_tableau_memorise_le_chainage_ne_s_applique_pas(
    orchestrateur: Orchestrator, tmp_path: Path
):
    workspace = ConversationWorkspace(tmp_path / "ws", "fil-vide")
    plan = Plan(capability="predict", dataset="titanic")

    orchestrateur._regle_chainer_sur_le_dernier_tableau(plan, contexte(workspace=workspace))

    assert plan.capability == "predict"


# --- 9. « sans famille à bord » : l'absence d'accompagnants ----------------------


def test_sans_famille_a_bord_fixe_les_deux_compteurs(orchestrateur: Orchestrator):
    """Le défaut, à l'endroit exact où il se jouait.

    Le planificateur rend ce qu'il rendait en mesure — `parch` seul — et la
    règle complète `sibsp`. Sans elle, la prédiction ressort `invalid` sur une
    valeur que la phrase donnait.
    """
    plan = Plan(capability="predict", dataset="titanic", features={"parch": 0, "age": 28})

    orchestrateur._regle_lire_labsence_daccompagnants(
        plan,
        contexte(
            question=(
                "prédis la survie d'une passagère de 1re classe de 28 ans, "
                "tarif 80 livres, embarquée à Southampton, sans famille à bord"
            )
        ),
    )

    assert plan.features["sibsp"] == 0
    assert plan.features["parch"] == 0


def test_ce_que_l_utilisateur_a_donne_prime_sur_l_absence(orchestrateur: Orchestrator):
    """La règle ne peut qu'AJOUTER. « Seule à bord avec ses deux enfants » est
    contradictoire, et c'est la valeur explicite qui gagne — jamais la nôtre."""
    plan = Plan(capability="predict", dataset="titanic", features={"parch": 2})

    orchestrateur._regle_lire_labsence_daccompagnants(
        plan, contexte(question="une passagère seule à bord avec ses deux enfants")
    )

    assert plan.features["parch"] == 2
    assert plan.features["sibsp"] == 0


def test_sans_la_construction_rien_n_est_ajoute(orchestrateur: Orchestrator):
    plan = Plan(capability="predict", dataset="titanic", features={"age": 28})

    orchestrateur._regle_lire_labsence_daccompagnants(
        plan, contexte(question="prédis la survie d'une passagère de 28 ans")
    )

    assert "sibsp" not in plan.features
    assert "parch" not in plan.features


def test_un_dataset_sans_compteur_d_accompagnants_n_est_pas_touche(orchestrateur: Orchestrator):
    """`iris` mesure des pétales : « sans famille » n'y met aucun zéro."""
    plan = Plan(capability="predict", dataset="iris", features={})

    orchestrateur._regle_lire_labsence_daccompagnants(
        plan, contexte(question="une fleur sans famille à bord")
    )

    assert plan.features == {}


def test_la_regle_ne_s_applique_qu_aux_capacites_de_prediction(orchestrateur: Orchestrator):
    plan = Plan(capability="query", dataset="titanic", features={})

    orchestrateur._regle_lire_labsence_daccompagnants(
        plan, contexte(question="combien de passagers sans famille à bord ?")
    )

    assert plan.features == {}
