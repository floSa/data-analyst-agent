"""Routage des questions SUR le système, bout en bout dans le graphe.

Un seul chemin d'entrée désormais, et c'est le **modèle** qui l'ouvre : le nœud
`system` est en tête du graphe, il soumet la question à un agent muni de cinq
outils rendant les faits du dépôt, et l'appel d'un outil est le signal de
routage. Rien n'est appelé : le tour repart au planificateur comme avant.

Ce qui est vérifié ici n'est donc pas un lexique — il n'y en a plus — mais les
trois façons de ne PAS servir le modèle : il n'appelle rien, il invente un nom,
il en oublie un. Dans les deux derniers cas ce sont les faits eux-mêmes qui
partent à l'utilisateur, et c'est le rôle qu'a pris l'ancien chemin
déterministe : une ceinture, plus le chemin principal.
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
from data_analyst_agent.orchestrator.systeme import build_systeme_agent
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from helpers.doubles import FakeClassifier
from helpers.scripted_llm import (
    PLANNER,
    RETRIEVAL,
    SYNTHESIS,
    SYSTEME,
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


def agent_systeme(outil: str, args: dict, reponse: str) -> ScriptedLLM:
    """Un modèle qui appelle ``outil`` puis formule ``reponse``.

    Deux allers-retours, comme en vrai : le premier choisit l'outil, le second
    rédige à partir de ce qu'il a rendu.
    """
    return ScriptedLLM().script(SYSTEME, [tool_call(outil, args), text(reponse)])


# --- le chemin principal : le modèle appelle, l'outil rend, le modèle formule ---


def test_les_sources_viennent_de_l_outil_et_le_modele_les_formule(
    mini_csv: Path, registre: Registry
):
    """Le tour de force attendu du changement : la réponse est du modèle, et les
    noms sont ceux du catalogue.

    Le lexique reconnaissait cette question par la tournure « sources de
    données ». Ici, rien de tel n'est écrit nulle part : c'est le modèle qui a
    décidé d'appeler l'outil.
    """
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "Je n'ai qu'une source pour l'instant : `ventes_2026`, un fichier.",
    )
    catalogue = Catalog(
        sources=[FileSource(name="ventes_2026", path=mini_csv, description="Les ventes.")]
    )

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask("tu bosses sur quoi ?")

    assert reponse.error is None
    assert "ventes_2026" in reponse.answer
    # la formulation du MODÈLE, pas le gabarit : sa phrase, mot pour mot
    assert reponse.answer.startswith("Je n'ai qu'une source")
    assert [s.node for s in reponse.trace] == ["system", "synthesize"]
    assert llm.prompts_for(PLANNER) == []  # le planificateur n'a jamais été appelé
    # Aucun plan : il n'y a pas eu de planification, parce qu'il n'y avait rien
    # à planifier. La trace, elle, dit quel outil a fondé la réponse.
    assert reponse.plan is None
    assert "sources_de_donnees" in next(s for s in reponse.trace if s.node == "system").detail


def test_le_schema_est_lu_dans_la_source_et_non_narre_de_memoire(
    mini_csv: Path, registre: Registry
):
    """L'outil ouvre la source ; le modèle n'a que ce qu'elle a rendu."""
    llm = agent_systeme(
        "schema_d_une_source",
        {"cible": "mini"},
        "La table `mini` a deux colonnes : `sexe` et `survie`, toutes deux en VARCHAR "
        "et BIGINT selon le schéma.",
    )
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "il y a quoi comme colonnes ?"
    )

    assert "sexe" in reponse.answer
    assert "survie" in reponse.answer


def test_les_attributs_d_un_modele_designe_sont_rendus(registre: Registry):
    """L'entrée « De quels attributs as-tu besoin ? » de axes-amelioration.md."""
    champs = ("sex", "pclass", "age", "sibsp", "parch", "fare", "embarked")
    llm = agent_systeme(
        "attributs_d_un_modele",
        {"modele": "titanic"},
        "Pour **titanic** (classification de `survived`) il me faut : "
        + ", ".join(f"`{c}`" for c in champs)
        + ".",
    )

    reponse = orchestrateur(llm, registry=registre).ask(
        "il te faut quoi pour deviner si un passager a survécu ?"
    )

    for champ in champs:
        assert champ in reponse.answer


def test_les_modeles_sont_ceux_du_registre(registre: Registry):
    llm = agent_systeme(
        "modeles_de_prediction",
        {},
        "Deux modèles : **iris** (cible `species`) et **titanic** (cible `survived`).",
    )

    reponse = orchestrateur(llm, registry=registre).ask("quel genre de prévisions tu peux faire ?")

    assert "titanic" in reponse.answer
    assert "iris" in reponse.answer


def test_la_reponse_du_systeme_n_est_pas_repassee_a_la_synthese(mini_csv: Path, registre: Registry):
    """Le modèle a déjà formulé, avec les faits sous les yeux. La faire reformuler
    par l'agent de synthèse — qui, lui, ne les a pas — rouvrirait la porte à une
    réponse qui n'est plus celle du catalogue (cf. ``acfd8f5``)."""
    llm = agent_systeme(
        "capacites_de_l_agent",
        {},
        "Je sais interroger, analyser, prédire et parler de moi. Ma source : "
        "`mini`. Mes modèles : `iris`, `titanic`.",
    )
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "je peux te demander quoi ?"
    )

    assert llm.prompts_for(SYNTHESIS) == []
    synthese = next(s for s in reponse.trace if s.node == "synthesize")
    assert synthese.detail == "système (déterministe)"


# --- la ceinture : trois façons de ne pas servir le modèle ---------------------


def test_un_nom_absent_des_faits_fait_servir_les_faits(mini_csv: Path, registre: Registry):
    """LE défaut à ne pas laisser revenir par cette porte.

    ``acfd8f5`` corrigeait « décris le dataset iris » répondu de mémoire. Un
    modèle à qui l'on demande les tables d'une base « familière » sait en citer
    de mémoire, et un nom inventé est plus nocif qu'une réponse absente : il a
    l'air d'une lecture de la source. Ici le modèle cite `flights`, que le
    catalogue ne connaît pas — ce sont les faits qui partent, et le nom inventé
    n'atteint JAMAIS l'utilisateur.
    """
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "J'ai deux sources : `mini` et `flights`.",
    )
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "montre-moi ce que tu as"
    )

    assert "flights" not in reponse.answer
    assert "mini" in reponse.answer
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "faits servis tels quels" in detail
    assert "flights" in detail  # la trace dit POURQUOI on a écarté la formulation


def test_une_liste_incomplete_fait_servir_les_faits(mini_csv: Path, registre: Registry):
    """Défaut mesuré : la version narrée avait laissé tomber deux colonnes sur dix
    (mesure du 2026-09-07, §4 de surface-conversationnelle.md). Une liste
    incomplète n'est pas une réponse à « quelles sources ? »."""
    llm = agent_systeme("sources_de_donnees", {}, "Ma source est `mini`.")
    catalogue = Catalog(
        sources=[
            FileSource(name="mini", path=mini_csv),
            FileSource(name="autre", path=mini_csv),
        ]
    )

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask("liste tes bases")

    assert "autre" in reponse.answer  # rendue par les faits, oubliée par le modèle
    assert "fait(s) omis" in next(s for s in reponse.trace if s.node == "system").detail


def test_aucun_outil_appele_renvoie_la_question_au_planificateur(
    mini_csv: Path, registre: Registry
):
    """Le signal de routage est l'appel d'outil, pas le texte : un modèle qui
    répond de mémoire sans rien regarder ne peut pas se faire servir."""
    llm = (
        ScriptedLLM()
        .script(SYSTEME, [text("Iris est un jeu de classification floristique bien connu.")])
        .script(PLANNER, [plan_response(Plan(capability="predict", dataset="titanic"))])
    )

    reponse = orchestrateur(
        llm, catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]), registry=registre
    ).ask("décris le dataset iris")

    assert "floristique" not in reponse.answer
    assert reponse.plan is not None  # un plan a bien été demandé, et rendu
    assert "aucun outil appelé" in next(s for s in reponse.trace if s.node == "system").detail


def test_un_agent_systeme_qui_n_aboutit_pas_laisse_passer_la_question(
    mini_csv: Path, registre: Registry, monkeypatch
):
    """Fail-open, et délibérément : ce nœud est en tête de CHAQUE tour.

    Un planificateur qui aurait su répondre ne doit pas être privé de la
    question par un incident du nœud d'avant. Ce qu'un OUTIL rate, en
    revanche, n'est pas rattrapé (test suivant).
    """

    def _echoue(*args, **kwargs):
        raise UnexpectedModelBehavior("Exceeded maximum retries")

    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.run_systeme", _echoue)
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="predict"))])

    reponse = orchestrateur(
        llm, catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]), registry=registre
    ).ask("quelles sources ?")

    assert reponse.error is None
    assert reponse.plan is not None
    assert "agent système écarté" in next(s for s in reponse.trace if s.node == "system").detail


def test_un_outil_qui_tombe_reste_garde(tmp_path: Path, registre: Registry):
    """Le fichier de la source a disparu : une phrase et une référence
    d'incident, pas une exception brute. Et ce n'est PAS rattrapé — un
    catalogue qui pointe dans le vide est un vrai défaut de configuration."""
    llm = agent_systeme("schema_d_une_source", {"cible": "envolee"}, "peu importe")
    catalogue = Catalog(sources=[FileSource(name="envolee", path=tmp_path / "absent.csv")])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "Quelles colonnes a la table envolee ?"
    )

    assert "ma propre configuration" in reponse.answer
    assert "incident" in reponse.answer
    assert "FileNotFoundError" not in reponse.answer


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

    D'où un **outil** plutôt qu'une capacité : il ne touche pas ce contrat. Ce
    test le garde : l'élargir de nouveau demandera une mesure, pas une
    intuition.
    """
    assert get_args(Capability) == ("query", "analyze", "predict", "fetch_then_predict")


def test_le_planificateur_n_entend_pas_parler_du_systeme(registre: Registry):
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query"))])

    orchestrateur(llm, registry=registre).ask("euh")

    assert "describe_system" not in llm.systems_for(PLANNER)[0]
    assert "sources_de_donnees" not in llm.systems_for(PLANNER)[0]


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
    assert "iris, titanic" in reponse.answer  # lu dans le registre
    assert reponse.answer.strip().endswith("?")  # on redemande quand même


# --- ce qui ne doit PAS bouger -------------------------------------------------


def test_une_question_sur_les_donnees_garde_sa_route(mini_csv: Path, registre: Registry):
    """Le témoin : la tournure ressemble à une question de schéma, la réponse
    est un SELECT. C'est le prompt de l'agent système qui trace la frontière —
    « il répond sur ce que l'agent EST, jamais sur ce que les données
    CONTIENNENT » — et la batterie live la mesure sur le vrai modèle."""
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
    assert [s.node for s in reponse.trace] == ["system", "plan", "retrieval", "synthesize"]


def test_un_tableau_intermediaire_n_est_pas_annonce_comme_une_source(
    tmp_path: Path, mini_csv: Path, registre: Registry
):
    """Un tableau du fil est interrogeable, ce n'est pas une source de données :
    l'annoncer comme telle induirait en erreur. Même distinction que dans
    ``PlanContext`` entre catalogue déclaré et catalogue effectif."""
    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "un tour précédent")
    llm = agent_systeme("sources_de_donnees", {}, "Ma seule source est `mini`.")
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
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
    llm = agent_systeme(
        "schema_d_une_source",
        {"cible": "resultat_1"},
        "La table `resultat_1` porte `region` et `chiffre`.",
    )
    catalogue = Catalog(sources=[FileSource(name="mini", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "Quelles colonnes a la table resultat_1 ?",
        conversation_id="fil",
        workspace_root=tmp_path,
    )

    assert "region" in reponse.answer
    assert "chiffre" in reponse.answer


def test_un_complement_de_features_ne_passe_pas_par_l_agent_systeme(
    mini_csv: Path, registre: Registry
):
    """« 28 ans » répond à une question que l'agent a posée : il n'y a rien à
    interpréter, et lui faire passer l'agent système coûterait un aller-retour
    pour apprendre ce qu'on sait déjà — en lui donnant l'occasion de s'emparer
    d'un message qui ne lui est pas adressé."""
    from data_analyst_agent.orchestrator.graph import PendingInference

    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"age": 28.0}))],
    )

    reponse = orchestrateur(
        llm, catalog=Catalog(sources=[FileSource(name="mini", path=mini_csv)]), registry=registre
    ).ask("elle avait 28 ans", pending=PendingInference(dataset="titanic", features={}))

    assert llm.prompts_for(SYSTEME) == []  # l'agent système n'a pas été appelé
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "prédiction en attente" in detail


def test_l_outil_de_schema_annonce_qu_il_porte_le_SENS_et_pas_que_la_structure():
    """Ce qu'un outil DIT porter décide du routage de tout un tour.

    Mesuré avant correctif : l'agent système répondait ``AUTRE`` à huit
    formulations sur huit de « que veut dire cette colonne ? ». Le prompt lui
    disait pourtant que le sens d'une colonne le concerne. Mais aucun de ses
    six outils n'annonçait porter un sens — celui-ci disait « tables, colonnes,
    types et clés », c'est-à-dire de la structure. Un modèle qui ne voit aucun
    outil capable de répondre conclut que la question n'est pas pour lui, et il
    a raison de le conclure.

    Ce test ne pèse aucune tournure : il exige que la description dise les deux
    genres de fait que la fonction rend RÉELLEMENT — la structure, et ce que le
    dictionnaire de la source écrit sur la colonne visée
    (``introspection.decrire_le_schema``). Une description qui n'annonce que la
    moitié de son retour est un défaut d'interface, pas de style.
    """
    (toolset,) = build_systeme_agent().toolsets
    description = toolset.tools["schema_d_une_source"].description or ""

    assert "dictionnaire" in description.lower()
    assert "veut dire" in description.lower()
    # et la structure reste annoncée : le correctif ajoute, il ne remplace pas
    assert "colonnes" in description.lower()
    assert "types" in description.lower()


def test_l_agent_systeme_lie_la_source_que_le_message_nomme(mini_csv: Path, registre: Registry):
    """La dette D, tranchée : répondre ET lier, en un seul tour.

    ``_regle_source_de_la_conversation`` lie la source qu'un message nomme,
    mais elle vit dans le nœud du PLAN — que l'agent système court-circuite.
    Une phrase qui nomme une source et demande ce qu'elle contient recevait
    donc une bonne réponse sans que rien ne soit retenu, et la question
    SUIVANTE du même fil repartait sans source. Mesuré 3 fois sur 3.
    """
    llm = agent_systeme(
        "schema_d_une_source", {"cible": "ventes"}, "La source `ventes` porte la table `ventes`."
    )
    catalogue = Catalog(sources=[FileSource(name="ventes", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "je voudrais consulter la source ventes ; qu'est-ce qu'on y trouve ?",
        conversation_id="fil",
        # Comme l'API : TOUJOURS une chaîne, jamais None. Avec None il n'y a pas
        # de fil à lier, et le banc fabriquerait un défaut que le produit n'a pas
        # (cf. le piège de protocole de `docs/sources-de-demonstration.md`).
        source_de_travail="",
    )

    assert reponse.source_de_travail == "ventes"
    assert "Je travaille sur la source `ventes`." in reponse.answer


def test_une_bascule_par_l_agent_systeme_est_ANNONCEE(
    tmp_path: Path, mini_csv: Path, registre: Registry
):
    """Ce qui est dangereux n'est pas de changer de source, c'est de le taire.

    Même règle que ``_lier_la_source`` dans le nœud du plan : la bascule est
    mise en tête de la réponse, et elle nomme la source QUITTÉE — sans quoi
    l'utilisateur lit une réponse juste en croyant qu'elle porte sur ses
    données précédentes.
    """
    autre = tmp_path / "autre.csv"
    autre.write_text("a,b\n1,2\n", encoding="utf-8")
    llm = agent_systeme(
        "schema_d_une_source", {"cible": "autre"}, "La source `autre` porte la table `autre`."
    )
    catalogue = Catalog(
        sources=[FileSource(name="ventes", path=mini_csv), FileSource(name="autre", path=autre)]
    )

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "et dans autre, il y a quelles colonnes ?",
        conversation_id="fil",
        source_de_travail="ventes",
    )

    assert reponse.source_de_travail == "autre"
    assert "Je passe sur la source `autre` — on travaillait sur `ventes`." in reponse.answer


def test_une_source_IMPOSEE_par_l_appelant_ne_lie_rien_depuis_l_agent_systeme(
    mini_csv: Path, registre: Registry
):
    """Le paramètre `source` d'``ask()`` est une contrainte d'API pour UN tour.

    Même garde-fou que dans le nœud du plan : ce n'est pas le choix de
    l'utilisateur, et il ne doit pas s'inscrire dans le fil.
    """
    llm = agent_systeme(
        "schema_d_une_source", {"cible": "ventes"}, "La source `ventes` porte la table `ventes`."
    )
    catalogue = Catalog(sources=[FileSource(name="ventes", path=mini_csv)])

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "qu'est-ce qu'on trouve dans ventes ?",
        conversation_id="fil",
        source_de_travail="",
        source="ventes",
    )

    assert "Je travaille sur la source" not in reponse.answer
