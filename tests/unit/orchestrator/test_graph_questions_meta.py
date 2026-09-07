"""Routage des questions SUR le système, bout en bout dans le graphe.

Deux chemins d'entrée, et ils ne coûtent pas la même chose. Le lexique
court-circuite le planificateur — **zéro appel LLM**, et c'est vérifié ici en
n'en scriptant aucun : le moindre appel ferait tomber la doublure. La capacité
``describe_system`` offerte au planificateur est le second chemin, celui des
tournures que le lexique ne connaît pas.
"""

from pathlib import Path
from typing import get_args

import joblib
import pytest
from pydantic_ai import UnexpectedModelBehavior

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Capability, Plan
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from helpers.doubles import FakeClassifier
from helpers.scripted_llm import (
    PLANNER,
    RETRIEVAL,
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
    labels:
      "0": "n'a pas survécu"
      "1": "a survécu"
  - dataset: iris
    task: classification
    model_path: titanic.joblib
    target: species
  - dataset: maxizoo_sales
    task: regression
    model_path: titanic.joblib
    target: quantity
    unit: unités vendues
"""


@pytest.fixture
def registre(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def mini_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return csv


def orchestrateur(llm: ScriptedLLM, **kwargs) -> Orchestrator:
    kwargs.setdefault("catalog", Catalog(sources=[]))
    kwargs.setdefault("settings", Settings(_env_file=None))
    return Orchestrator(model=llm.model(), **kwargs)


# --- le chemin sans LLM --------------------------------------------------------


def test_les_sources_sont_rendues_sans_le_moindre_appel_llm(mini_csv: Path, registre: Registry):
    """La réponse est dans le catalogue : la faire classer par un modèle serait
    payer un aller-retour pour apprendre ce qu'on sait déjà.

    Aucune réponse n'est scriptée : si un agent était appelé, la doublure
    lèverait faute de script. C'est la preuve, pas l'illustration.
    """
    llm = ScriptedLLM()
    catalogue = Catalog(
        sources=[FileSource(name="ventes_2026", path=mini_csv, description="Les ventes.")]
    )
    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ?"
    )
    assert reponse.error is None
    assert "ventes_2026" in reponse.answer
    assert "Les ventes." in reponse.answer
    assert [s.node for s in reponse.trace] == ["plan", "system", "synthesize"]
    assert llm.prompts_for(PLANNER) == []  # le planificateur n'a jamais été appelé
    # Aucun plan : il n'y a pas eu de planification, parce qu'il n'y avait rien
    # à planifier. La trace, elle, dit ce qui a été routé et à quel prix.
    assert reponse.plan is None


def test_la_trace_dit_que_le_plan_n_a_rien_coute(mini_csv: Path, registre: Registry):
    """Le nœud `plan` ne compte aucun token : il n'a rien envoyé."""
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    reponse = orchestrateur(ScriptedLLM(), catalog=catalogue, registry=registre).ask(
        "Que sais-tu faire ?"
    )
    plan = next(s for s in reponse.trace if s.node == "plan")
    assert "sans appel LLM" in plan.detail
    assert plan.prompt_tokens is None
    assert plan.server_prompt_tokens is None


def test_les_colonnes_passent_par_le_systeme(mini_csv: Path, registre: Registry):
    """« quelles colonnes ? » se lit dans l'ontologie, pas dans une réponse narrée.

    C'est ce qui la rend complète par construction : la version narrée par le
    modèle avait laissé tomber deux colonnes sur dix (mesure du 2026-09-07).
    """
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    reponse = orchestrateur(ScriptedLLM(), catalog=catalogue, registry=registre).ask(
        "Quelles colonnes y a-t-il dans la table mini ?"
    )
    assert "sexe" in reponse.answer
    assert "survie" in reponse.answer
    assert "VARCHAR" in reponse.answer  # le type réel, lu dans le schéma


def test_les_modeles_sont_ceux_du_registre(registre: Registry):
    reponse = orchestrateur(ScriptedLLM(), registry=registre).ask(
        "Quels modèles de prédiction sais-tu utiliser ?"
    )
    assert "titanic" in reponse.answer
    assert "iris" in reponse.answer


def test_les_features_d_un_modele_designe_sont_rendues(registre: Registry):
    """L'entrée « De quels attributs as-tu besoin ? » de axes-amelioration.md.

    Le modèle nommé est le seul du registre dont ce dépôt embarque un schéma de
    features : c'est lui qui porte le sens des champs et leurs valeurs admises.
    """
    reponse = orchestrateur(ScriptedLLM(), registry=registre).ask(
        "De quels attributs as-tu besoin pour prédire maxizoo_sales ?"
    )
    for champ in (
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
        assert champ in reponse.answer
    assert "canal e-commerce" in reponse.answer  # le sens du champ, tiré du schéma


def test_les_features_non_qualifiees_repondent_au_lieu_de_demander(registre: Registry):
    """Plusieurs modèles, aucun désigné : on les décrit tous.

    L'utilisateur recevait « Sur quel modèle veux-tu prédire : iris, titanic ? »
    — sa propre question, renvoyée avec la liste. Répondre coûte le même
    nombre d'appels LLM (aucun) et lui épargne un tour.
    """
    reponse = orchestrateur(ScriptedLLM(), registry=registre).ask(
        "De quels attributs as-tu besoin ?"
    )
    assert "titanic" in reponse.answer
    assert "iris" in reponse.answer
    assert "maxizoo_sales" in reponse.answer
    assert "`commodity_group`" in reponse.answer


def test_la_reponse_du_systeme_n_est_pas_reformulee(mini_csv: Path, registre: Registry):
    """Déterministe et rendue telle quelle : la faire reformuler rouvrirait la
    porte à une réponse qui n'est plus celle du catalogue (cf. ``acfd8f5``)."""
    llm = ScriptedLLM()
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask("Que sais-tu faire ?")
    assert llm.prompts_for(SYNTHESIS) == []
    synthese = next(s for s in reponse.trace if s.node == "synthesize")
    assert synthese.detail == "système (déterministe)"


# --- le planificateur n'a PAS cette capacité, et c'est mesuré -----------------


def test_le_contrat_de_sortie_du_llm_reste_a_quatre_capacites():
    """Élargir ``Capability`` n'est pas gratuit, et le prix a été mesuré.

    Le Literal EST le JSON Schema de sortie structurée : le modèle le voit,
    même quand le prompt ne mentionne pas la valeur ajoutée. Les deux
    expériences, en live sur gemma4:e4b et reproductibles :

    - la valeur annoncée AUSSI dans le prompt : 134 tokens à chaque requête, et
      quatre questions de la batterie passées d'une bonne réponse à une
      mauvaise sans une seule dans l'autre sens — « sur quelle période portent
      les données ? » routée en describe_system alors que la réponse est un
      SELECT ;
    - la valeur dans le seul Literal, prompt inchangé : l'extraction des
      features se dégrade sur une capacité qui n'a rien à voir — « prédis la
      survie d'une passagère de 1re classe… » ressortait avec ``pcass`` au lieu
      de ``pclass``, donc une relance au lieu d'une prédiction. Le Literal
      retiré, la prédiction aboutit.

    D'où le routage par du code (``_court_circuit_meta``), qui est de toute
    façon la règle du dépôt. Ce test garde le contrat : l'élargir de nouveau
    demandera une mesure, pas une intuition.
    """
    assert get_args(Capability) == ("query", "analyze", "predict", "fetch_then_predict")


def test_le_planificateur_n_entend_pas_parler_du_systeme(registre: Registry):
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query"))])
    orchestrateur(llm, registry=registre).ask("euh")
    assert "describe_system" not in llm.systems_for(PLANNER)[0]


def test_une_tournure_inconnue_du_lexique_suit_le_chemin_habituel(registre: Registry):
    """Le lexique est précis, pas exhaustif : ce qu'il ne reconnaît pas est
    classé par le planificateur comme avant, sans passer par le nœud système."""
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="predict"))])
    reponse = orchestrateur(llm, registry=registre).ask("Raconte-moi un peu ton périmètre.")
    assert reponse.plan is not None  # un plan a bien été demandé, et rendu
    assert "system" not in [s.node for s in reponse.trace]
    assert len(llm.prompts_for(PLANNER)) == 1


# --- le repli, qui ne repart plus les mains vides -----------------------------


def test_le_repli_rend_l_inventaire_reel(mini_csv: Path, registre: Registry, monkeypatch):
    """Le repli citait « titanic, iris… » EN DUR — faux dès qu'un déploiement
    change de catalogue, et absurde puisqu'il nommait la source demandée tout
    en déclarant ne pas comprendre."""

    class _PlanificateurQuiEchoue:
        def run_sync(self, *args, **kwargs):
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")

    monkeypatch.setattr(
        "data_analyst_agent.orchestrator.graph.planner_agent",
        lambda *args, **kwargs: _PlanificateurQuiEchoue(),
    )
    catalogue = Catalog(sources=[FileSource(name="ventes_2026", path=mini_csv)])
    reponse = orchestrateur(ScriptedLLM(), catalog=catalogue, registry=registre).ask(
        "euh... fais un truc"
    )
    assert "ventes_2026" in reponse.answer  # lu dans le catalogue
    assert "iris, maxizoo_sales, titanic" in reponse.answer  # lu dans le registre
    assert reponse.answer.strip().endswith("?")  # on redemande quand même


# --- ce qui ne doit PAS bouger -------------------------------------------------


def test_une_question_sur_les_donnees_garde_sa_route(mini_csv: Path, registre: Registry):
    """Le témoin : la tournure ressemble à une question de schéma, la réponse
    est un SELECT."""
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call(
                    "run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE sexe IS NULL"}
                ),
                text("Aucune valeur manquante."),
            ],
        )
    )
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "Quelles colonnes de la table mini contiennent des valeurs manquantes ?"
    )
    assert reponse.plan.capability == "query"
    assert [s.node for s in reponse.trace] == ["plan", "retrieval", "synthesize"]


def test_un_tableau_intermediaire_n_est_pas_annonce_comme_une_source(
    tmp_path: Path, mini_csv: Path, registre: Registry
):
    """Un tableau du fil est interrogeable, ce n'est pas une source de données :
    l'annoncer comme telle induirait en erreur. Même distinction que dans
    ``PlanContext`` entre catalogue déclaré et catalogue effectif."""
    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "un tour précédent")
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    reponse = orchestrateur(ScriptedLLM(), catalog=catalogue, registry=registre).ask(
        "Quelles sont tes sources de données ?", conversation_id="fil", workspace_root=tmp_path
    )
    assert "mini" in reponse.answer
    assert "resultat_1" not in reponse.answer


def test_les_colonnes_d_un_tableau_intermediaire_restent_lisibles(
    tmp_path: Path, mini_csv: Path, registre: Registry
):
    """Pour aller LIRE un schéma, en revanche, le tableau mémorisé compte."""
    ConversationWorkspace(tmp_path, "fil").save_table(
        ["region", "chiffre"], [["nord", 12]], "un tour précédent"
    )
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])
    reponse = orchestrateur(ScriptedLLM(), catalog=catalogue, registry=registre).ask(
        "Quelles colonnes a la table resultat_1 ?",
        conversation_id="fil",
        workspace_root=tmp_path,
    )
    assert "region" in reponse.answer
    assert "chiffre" in reponse.answer


def test_un_noeud_systeme_qui_tombe_reste_garde(tmp_path: Path, registre: Registry):
    """Le fichier de la source a disparu : une phrase et une référence
    d'incident, pas une exception brute."""
    catalogue = Catalog(sources=[FileSource(name="envolee", path=tmp_path / "absent.csv")])
    reponse = orchestrateur(ScriptedLLM(), catalog=catalogue, registry=registre).ask(
        "Quelles colonnes a la table envolee ?"
    )
    assert "ma propre configuration" in reponse.answer
    assert "incident" in reponse.answer
    assert "FileNotFoundError" not in reponse.answer
