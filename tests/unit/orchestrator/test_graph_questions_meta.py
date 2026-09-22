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

import hashlib
from pathlib import Path
from typing import get_args

import joblib
import pytest
from pydantic_ai import UnexpectedModelBehavior

from data_analyst_agent import prompts
from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Capability, Plan
from data_analyst_agent.orchestrator.systeme import (
    VOIE_PREMIERE_PASSE,
    VOIE_REPLI,
    VOIE_SECONDE_PASSE,
    ResultatSysteme,
    SystemeDeps,
    build_systeme_agent,
    run_systeme,
    servir_la_reponse,
)
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from helpers.doubles import FakeClassifier
from helpers.scripted_llm import (
    PLANNER,
    REPARATION,
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
    expériences, en live sur le modèle en service et reproductibles :

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


def test_chercher_une_source_est_un_outil_a_part_et_rend_les_descriptions(registre: Registry):
    """Chercher par sujet et réciter l'inventaire sont deux métiers.

    Mesuré le 2026-09-16 : fondus dans un seul outil, l'argument de recherche
    désarmait la ceinture d'exhaustivité sur des questions qui n'avaient rien à
    voir — « de quand datent tes données ? » repassait de juste à vague, deux
    campagnes sur deux. Séparés, la surface conversationnelle revient à 36/36 et
    « as-tu une source qui parle de maintenance ? » rend UNE source.
    """
    from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource

    (toolset,) = build_systeme_agent().toolsets
    description = toolset.tools["chercher_une_source"].description or ""

    assert "sujet" in description.lower()
    assert "une seule" in description.lower()

    catalogue = Catalog(
        sources=[
            FileSource(name="ventes", path=Path("v.csv"), description="Les ventes du mois."),
            FileSource(name="stocks", path=Path("s.csv"), description="L'état des stocks."),
        ]
    )
    deps = SystemeDeps(
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        question="tu as quelque chose sur ce qu'on a vendu ?",
    )

    rendu = deps.decrire_les_sources(a_enumerer=False)

    assert "ventes" in rendu
    assert deps.faits_a_enumerer == []


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


def test_le_tour_precedent_part_comme_de_vrais_messages():
    """« Oui » n'a de sens que si l'on sait à quoi il répond — et sous quelle forme.

    Mesuré le 2026-09-16, et c'est l'agent qui pose le piège : il demande
    « souhaitez-vous que je consulte le schéma de `referentiel` et
    `facturation` ? », l'utilisateur répond « oui », et le tour repartait au
    planificateur, qui rendait « je n'ai pas bien compris ta demande ».

    Le tour d'avant recopié dans le message ne suffisait pas : le modèle
    répondait ``TextPart("travailler_sur_une_source(source='referentiel')")``
    — il avait compris, il ÉCRIVAIT l'appel au lieu de l'émettre, et un appel
    écrit n'est pas un appel. Deux messages, une question et sa réponse, et le
    modèle émet un vrai appel d'outil.
    """
    from data_analyst_agent.orchestrator.systeme import _le_tour_precedent_en_messages

    messages = _le_tour_precedent_en_messages(
        ("que contient referentiel ?", "Souhaitez-vous que je consulte son schéma ?")
    )

    assert [type(m).__name__ for m in messages] == ["ModelRequest", "ModelResponse"]
    assert messages[0].parts[0].content == "que contient referentiel ?"
    assert messages[1].parts[0].content == "Souhaitez-vous que je consulte son schéma ?"
    # sans tour précédent, aucun historique : rien ne change au premier tour
    assert _le_tour_precedent_en_messages(None) == []


def test_sans_reponse_de_l_agent_il_n_y_a_rien_a_continuer():
    """Une question sans réponse ne porte rien qu'un « oui » puisse reprendre."""
    from data_analyst_agent.orchestrator.systeme import _le_tour_precedent_en_messages

    assert _le_tour_precedent_en_messages(("et ?", "   ")) == []
    assert _le_tour_precedent_en_messages(("", "")) == []


def test_la_reponse_precedente_est_bornee():
    """L'historique repart à chaque aller-retour d'outil : l'inventaire y tiendrait deux fois."""
    from data_analyst_agent.orchestrator.systeme import (
        REPONSE_PRECEDENTE_MAX_CARACTERES,
        _le_tour_precedent_en_messages,
    )

    messages = _le_tour_precedent_en_messages(("et ?", "x" * 5000))

    rendu = messages[-1].parts[0].content
    assert len(rendu) < REPONSE_PRECEDENTE_MAX_CARACTERES + 20
    assert rendu.endswith("[…]")


def test_le_prompt_de_l_agent_systeme_survit_a_l_historique():
    """Des instructions, pas un `system_prompt` — sinon l'historique l'efface.

    ``pydantic-ai`` n'émet les parts de `system_prompt` que sur un historique
    VIDE (``UserPromptNode.run``). L'agent système en reçoit un dès le second
    tour d'une conversation : en `system_prompt`, il tournerait alors sans la
    règle qui lui interdit d'inventer un nom de source.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from data_analyst_agent import prompts
    from data_analyst_agent.orchestrator.systeme import SystemeDeps, build_systeme_agent

    recus: list[str] = []

    def capture(messages, info):
        dernier = messages[-1]
        systeme = dernier.instructions or ""
        for part in dernier.parts:
            if type(part).__name__ == "SystemPromptPart":
                systeme += part.content
        recus.append(systeme)
        return ModelResponse(parts=[TextPart("AUTRE")])

    deps = SystemeDeps(
        catalogue_declare=Catalog(sources=[]),
        catalogue_effectif=Catalog(sources=[]),
        registre=Registry([], Path(".")),
        question="oui",
    )
    historique = [
        ModelRequest(parts=[UserPromptPart(content="quelles sources ?")]),
        ModelResponse(parts=[TextPart(content="`ventes`. Veux-tu son schéma ?")]),
    ]
    build_systeme_agent().run_sync(
        "oui", model=FunctionModel(capture), deps=deps, message_history=historique
    )

    assert len(recus) == 1
    assert prompts.gabarit(prompts.SYSTEME).strip() in recus[0]


def test_le_tour_d_avant_arrive_au_modele_comme_un_dialogue(mini_csv: Path, registre: Registry):
    """Bout en bout : ce que l'appelant passe à `run_systeme` arrive en messages.

    Le contrat qui compte n'est pas la forme de la liste construite plus haut,
    c'est ce que le modèle REÇOIT : une question, sa réponse, puis le message
    du tour. Un dialogue, et pas un paragraphe qui raconte un dialogue — c'est
    la différence entre un appel d'outil émis et un appel d'outil écrit en
    prose (cf. `_le_tour_precedent_en_messages`).
    """
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from data_analyst_agent.orchestrator.systeme import run_systeme

    vus: list[tuple[str, str]] = []

    def capture(messages, info):
        for message in messages:
            for part in message.parts:
                contenu = getattr(part, "content", "")
                if isinstance(contenu, str) and type(part).__name__ != "SystemPromptPart":
                    vus.append((type(part).__name__, contenu))
        return ModelResponse(parts=[TextPart("AUTRE")])

    run_systeme(
        "oui",
        model=FunctionModel(capture),
        catalogue_declare=Catalog(sources=[FileSource(name="ventes", path=mini_csv)]),
        catalogue_effectif=Catalog(sources=[FileSource(name="ventes", path=mini_csv)]),
        registre=registre,
        request_limit=3,
        echange_precedent=("quelles sources ?", "`ventes`. Veux-tu son schéma ?"),
    )

    assert vus == [
        ("UserPromptPart", "quelles sources ?"),
        ("TextPart", "`ventes`. Veux-tu son schéma ?"),
        ("UserPromptPart", "oui"),
    ]


def _catalogue_de_trois() -> Catalog:
    """Trois sources déclarées, pour mesurer ce qu'une question en nomme."""
    return Catalog(
        sources=[
            FileSource(name="ventes", path=Path("v.csv"), description="Le carnet de commandes."),
            FileSource(
                name="production", path=Path("p.csv"), description="L'atelier et ses arrêts."
            ),
            FileSource(
                name="stocks", path=Path("s.csv"), description="Les entrepôts et leurs mouvements."
            ),
        ]
    )


def _deps_sur(question: str, registre: Registry, catalogue: Catalog | None = None) -> SystemeDeps:
    cat = catalogue if catalogue is not None else _catalogue_de_trois()
    return SystemeDeps(
        catalogue_declare=cat, catalogue_effectif=cat, registre=registre, question=question
    )


def test_trois_sources_nommees_rendent_trois_fiches_et_pas_le_catalogue(registre: Registry):
    """Le défaut d'usage réel du 2026-09-17, réduit à ce qu'il était.

    « Qu'est-ce que t'appelles source vente, production, stock ? » recevait les
    CINQ fiches du catalogue de démonstration, `iris` et `titanic` compris, pour
    une question qui en visait trois. Le fil brut a nommé la cause : le modèle
    routait sur `chercher_une_source`, dont l'argument n'est pas lu et qui rend
    tout le catalogue. La règle ne porte donc pas sur l'argument mais sur ce que
    le MESSAGE nomme — et elle vaut pour les deux outils.

    Les noms sont écrits au SINGULIER dans la question et au pluriel dans le
    catalogue : c'est le cas réel, et le reconnaître est le même service que
    reconnaître « Télémétrie » pour `telemetrie`.
    """
    deps = _deps_sur("Qu'est-ce que t'appelles source vente, production, stock ?", registre)

    rendu = deps.decrire_les_sources(a_enumerer=False, outil="chercher_une_source")

    assert "ventes" in rendu
    assert "production" in rendu
    assert "stocks" in rendu
    assert "J'ai accès à" not in rendu  # pas l'inventaire, trois fiches
    # nommées, donc à énumérer : une liste nommée n'est pas une matière à choisir
    assert deps.faits_a_enumerer == [rendu]
    assert deps.outils_appeles == ["chercher_une_source"]


def test_la_recherche_par_sujet_recoit_toujours_tout_le_catalogue(registre: Registry):
    """Ce qui ne bouge pas : chercher sans connaître le nom.

    « As-tu quelque chose sur la maintenance ? » ne nomme aucune source, donc le
    plancher ne s'applique pas — le modèle reçoit tout le catalogue pour y
    choisir, et les faits ne sont PAS à énumérer. C'est la règle mesurée le
    2026-09-16, et le correctif du 2026-09-17 ne doit pas la reprendre.
    """
    deps = _deps_sur("As-tu quelque chose sur la maintenance ?", registre)

    rendu = deps.decrire_les_sources(a_enumerer=False, outil="chercher_une_source")

    assert "J'ai accès à" in rendu
    assert deps.faits_a_enumerer == []


def test_un_nom_inconnu_rend_toujours_le_catalogue(registre: Registry):
    """Celui qui se trompe de nom a besoin de voir les vrais — inchangé."""
    deps = _deps_sur("C'est quoi la source facturation ?", registre)

    rendu = deps.decrire_les_sources(cible="facturation")

    assert "J'ai accès à" in rendu


def test_sans_source_nommee_c_est_de_la_source_liee_qu_on_parle(registre: Registry):
    """« Et elle contient quoi ? » ne nomme personne : le sujet est la source liée."""
    catalogue = _catalogue_de_trois()
    deps = SystemeDeps(
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        question="et elle contient quoi ?",
        source_de_travail="production",
    )

    rendu = deps.decrire_les_sources()

    assert "production" in rendu
    assert "J'ai accès à" not in rendu


def test_une_source_nommee_prime_sur_la_source_liee(registre: Registry):
    """Le message nomme, donc c'est de CELLE-LÀ qu'on parle, pas de la liée."""
    catalogue = _catalogue_de_trois()
    deps = SystemeDeps(
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        question="et stocks, c'est quoi ?",
        source_de_travail="production",
    )

    rendu = deps.decrire_les_sources()

    assert "stocks" in rendu
    assert "atelier" not in rendu


def test_la_trace_nomme_l_outil_reellement_appele(registre: Registry):
    """Une trace qui nomme un autre outil fait chercher le défaut là où il n'est pas.

    `decrire_les_sources` sert deux outils et inscrivait toujours le premier.
    La trace disait donc `sources_de_donnees` là où le modèle avait appelé
    `chercher_une_source` — et c'est ce qui a caché la cause du défaut du
    2026-09-17 pendant toute une relecture de trace.
    """
    deps = _deps_sur("as-tu des données sur les pannes ?", registre)

    deps.decrire_les_sources(a_enumerer=False, outil="chercher_une_source")

    assert deps.outils_appeles == ["chercher_une_source"]


def test_aucune_fiche_d_outil_n_a_bouge(registre: Registry):
    """Le correctif du 2026-09-17 ne touche à AUCUNE description d'outil, et c'est mesuré.

    Deux phrases y avaient été ajoutées — « appelle cet outil une fois par nom »
    sur `sources_de_donnees`, « quand le nom est écrit, la recherche est déjà
    faite » sur `chercher_une_source`. Elles coûtaient `periode-directe` de la
    surface, DEUX campagnes complètes sur deux : « sur quelle période portent
    les données de la source titanic ? » quittait le planificateur, où elle se
    calcule, pour l'agent système, qui n'a que la fiche de la source à rendre.
    Une fiche plus attirante attire aussi ce qui ne la regarde pas.

    Le test le fige : la restriction est mécanique (`sources_nommees` et le
    plancher de `decrire_les_sources`), et rien n'est demandé au modèle.
    """
    (toolset,) = build_systeme_agent().toolsets
    sources = toolset.tools["sources_de_donnees"].description or ""
    recherche = toolset.tools["chercher_une_source"].description or ""

    assert "une fois par nom" not in sources.lower()
    assert "la recherche est" not in recherche.lower()


def test_les_fiches_nommees_sont_annoncees_comme_un_ensemble_clos(registre: Registry):
    """L'en-tête dit que l'ensemble est CLOS, et ce n'est pas un ornement.

    Sans lui, un tour routé sur `chercher_une_source` recevait les trois fiches
    et répondait « j'ai trouvé les sources `ventes`, `production` et `stocks` » :
    trois noms, pas un fait. Le modèle avait lu dans la fiche de cet outil qu'il
    n'a « pas à réciter les autres » et traitait trois fiches choisies comme une
    liste où choisir. Mesuré le 2026-09-17 : 12/21 sans l'en-tête, 18/21 avec.
    """
    deps = _deps_sur("ventes, production et stocks : présente-les-moi", registre)

    rendu = deps.decrire_les_sources(a_enumerer=False, outil="chercher_une_source")

    assert "que ta question nomme" in rendu
    assert "il n'y en a pas d'autres à chercher" in rendu


def test_une_seule_source_nommee_n_a_pas_d_en_tete(registre: Registry):
    """Une source, une fiche : annoncer « toutes les 1 » n'apprendrait rien."""
    deps = _deps_sur("c'est quoi stocks ?", registre)

    rendu = deps.decrire_les_sources()

    assert "que ta question nomme" not in rendu
    assert "stocks" in rendu


# --- le second plancher : le modèle n'appelle rien, le message nomme deux sources ---


def _sans_aucun_outil() -> ScriptedLLM:
    """Un agent système qui décline le tour : ``AUTRE``, et aucun appel d'outil."""
    return ScriptedLLM().script(SYSTEME, [text(introspection.SENTINELLE_HORS_SUJET)])


def test_deux_sources_nommees_sans_outil_appele_atteignent_l_agent_systeme(registre: Registry):
    """« titanic et iris, c'est quoi au juste ? » : aucun outil, et un tour perdu.

    Mesuré le 2026-09-17, trois tirages sur trois : aucun ``ToolCallPart``,
    réponse ``AUTRE``, et le tour repart au planificateur — qui n'a pas de
    capacité pour « décris-moi ces deux sources-là ». C'est la famille laissée à
    0/3 en C41.

    Le plancher ne demande rien au modèle : il constate que le message nomme deux
    sources du catalogue, sert leurs fiches, et laisse la ceinture écarter le
    ``AUTRE``.
    """
    catalogue = _catalogue_de_trois()

    resultat = run_systeme(
        "ventes et stocks, c'est quoi au juste ?",
        model=_sans_aucun_outil().model(),
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        request_limit=3,
    )

    assert resultat.concerne_le_systeme
    assert resultat.outils_appeles == ("plancher_des_sources_nommees",)
    assert "carnet de commandes" in resultat.faits
    assert "entrepôts" in resultat.faits
    assert "atelier" not in resultat.faits  # `production` n'est pas nommée
    # la ceinture fait le reste : le ``AUTRE`` du modèle ne part pas
    assert introspection.defaut_de_fondation(
        resultat.reponse, resultat.faits, resultat.faits_a_enumerer, resultat.marques_a_porter
    )


def test_une_seule_source_nommee_laisse_le_tour_au_planificateur(registre: Registry):
    """« combien de commandes dans ventes ? » nomme une source et se COMPTE.

    C'est la limite du plancher, et la raison pour laquelle il en faut deux et
    non une. Le modèle n'appelle aucun outil sur ce message — mesuré — et il a
    raison : la réponse change avec les lignes. Un plancher qui se déclencherait
    au premier nom cité le contredirait et volerait la question au planificateur.
    """
    catalogue = _catalogue_de_trois()

    resultat = run_systeme(
        "combien de commandes dans ventes ?",
        model=_sans_aucun_outil().model(),
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        request_limit=3,
    )

    assert not resultat.concerne_le_systeme


def test_sans_source_nommee_le_plancher_ne_se_declenche_pas(registre: Registry):
    """Une question sur les données qui ne nomme personne repart, comme avant."""
    catalogue = _catalogue_de_trois()

    resultat = run_systeme(
        "quelle est la moyenne des montants ?",
        model=_sans_aucun_outil().model(),
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        request_limit=3,
    )

    assert not resultat.concerne_le_systeme


def test_un_message_reduit_a_des_noms_laisse_le_premier_tour_faire_son_travail(
    registre: Registry,
):
    """« ventes ou clients ? » hésite ENTRE deux sources, il n'en demande pas la fiche.

    L'autre bord du plancher, et c'est un test qui l'a trouvé — pas une
    relecture. Servir deux fiches ici répondrait à côté et laisserait le fil
    délié : la bonne réponse est la question du premier tour, qui finit par
    « sur laquelle veux-tu travailler ? » et lie la réponse à la conversation.

    Le décompte est celui que ``choix_de_source`` mesure déjà : au-delà de trois
    mots en plus des noms, le message porte une question.
    """
    catalogue = _catalogue_de_trois()

    resultat = run_systeme(
        "ventes ou stocks ?",
        model=_sans_aucun_outil().model(),
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        request_limit=3,
    )

    assert not resultat.concerne_le_systeme


def test_le_plancher_ne_double_pas_un_outil_deja_appele(registre: Registry):
    """Quand le modèle a appelé un outil, c'est lui qui a servi — pas le plancher.

    Le plancher ne répare que les tours où rien n'a été appelé. Le laisser
    ajouter ses fiches par-dessus servirait deux fois les mêmes faits, et la
    trace nommerait un plancher là où un outil avait fait son travail.
    """
    catalogue = _catalogue_de_trois()
    llm = ScriptedLLM().script(
        SYSTEME,
        [tool_call("sources_de_donnees", {}), text("Les sources `ventes` et `stocks`.")],
    )

    resultat = run_systeme(
        "ventes et stocks, c'est quoi ?",
        model=llm.model(),
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        request_limit=3,
    )

    assert resultat.outils_appeles == ("sources_de_donnees",)


def test_une_source_citee_sans_un_fait_de_sa_fiche_fait_servir_le_repli(
    mini_csv: Path, registre: Registry
):
    """Le défaut du 2026-09-17, bout en bout dans le graphe.

    Le modèle reçoit deux fiches et rend deux noms. La ceinture comparait des
    NOMS : les deux y étaient, donc la réponse était réputée fondée et partait
    telle quelle — 108 caractères pour 986 servis, zéro fait. C'est le repli qui
    doit partir, et il est LISIBLE : il a été écrit pour un lecteur.
    """
    catalogue = Catalog(
        sources=[
            FileSource(name="ventes", path=mini_csv, description="Le carnet de commandes."),
            FileSource(name="stocks", path=mini_csv, description="Les entrepôts et leurs flux."),
        ]
    )
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "Tu travailles sur les sources `ventes` et `stocks`. "
        "Dis-moi ce que tu souhaites savoir sur ces sources.",
    )

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "je bosse sur quoi si je prends ventes et stocks ?"
    )

    assert "carnet de commandes" in reponse.answer
    assert "entrepôts" in reponse.answer
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "sans un fait de leur fiche" in detail


def test_une_reformulation_qui_porte_les_faits_est_toujours_servie(
    mini_csv: Path, registre: Registry
):
    """L'autre bord, et c'est lui qui coûterait cher à durcir.

    La ceinture porte les 36 questions de la surface conversationnelle. Une
    réponse qui résume honnêtement — un fait par source, dans les mots du
    modèle — doit continuer de partir telle quelle. La ceinture d'exhaustivité a
    déjà remplacé « la source `interventions` », qui était juste, par 2 200
    caractères de catalogue : l'exigence est UN fait, jamais la fiche entière.
    """
    catalogue = Catalog(
        sources=[
            FileSource(name="ventes", path=mini_csv, description="Le carnet de commandes."),
            FileSource(name="stocks", path=mini_csv, description="Les entrepôts et leurs flux."),
        ]
    )
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "`ventes` tient le carnet de commandes ; `stocks` couvre les entrepôts.",
    )

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "ventes et stocks, ça contient quoi ?"
    )

    assert reponse.answer.startswith("`ventes` tient le carnet")
    assert "formulé par le modèle" in next(s for s in reponse.trace if s.node == "system").detail


# --- le tour de réparation : une seconde chance avant le pavé ------------------
#
# Le repli est un GARDE-FOU, pas une réponse. Il est juste et il est fondé, et
# ce n'est pas la même chose : « parle-moi de stocks et de titanic, en deux
# mots » recevait 774 caractères de fiche, en-tête compris. Mesuré à travers le
# graphe le 2026-09-17, catalogue métier : sur cinq formulations qui nomment
# plusieurs sources, QUATRE servaient le texte de l'outil au caractère près.
#
# Ce que ces tests tiennent : on redemande AVANT de servir le pavé, la seconde
# formulation est jugée par la MÊME ceinture, le repli n'a pas disparu, et le
# tour de réparation ne coûte rien à un tour que la première passe a passé.


def _deux_sources(mini_csv: Path) -> Catalog:
    """Deux sources aux descriptions distinctes — donc deux fiches à porter."""
    return Catalog(
        sources=[
            FileSource(name="ventes", path=mini_csv, description="Le carnet de commandes."),
            FileSource(name="stocks", path=mini_csv, description="Les entrepôts et leurs flux."),
        ]
    )


def test_une_seconde_formulation_fondee_est_servie_a_la_place_du_pave(
    mini_csv: Path, registre: Registry
):
    """LE tour de ce chantier : le modèle rate, on lui rend les faits, il réussit.

    La première formulation est celle du défaut mesuré — les deux noms servis et
    zéro fait sur eux (« Tu travailles sur les sources `ventes` et `stocks`. »).
    La seconde dit un fait par source, en une phrase, ce qui est exactement ce
    que « en deux mots » demandait. C'est elle qui part, et le pavé reste au
    placard.
    """
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "Tu travailles sur les sources `ventes` et `stocks`.",
    ).script(
        REPARATION,
        [text("`ventes`, c'est le carnet de commandes ; `stocks`, les entrepôts.")],
    )

    reponse = orchestrateur(llm, catalog=_deux_sources(mini_csv), registry=registre).ask(
        "ventes et stocks, en deux mots ?"
    )

    assert reponse.answer.startswith("`ventes`, c'est le carnet")
    assert "J'ai accès à" not in reponse.answer  # le repli n'est pas parti
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "reformulé au second tour" in detail
    assert "sans un fait de leur fiche" in detail  # la trace dit ce qu'on a écarté


def test_la_seconde_formulation_est_jugee_par_la_meme_ceinture(mini_csv: Path, registre: Registry):
    """La seconde chance ne relâche RIEN, et c'est ce qui la rend sans risque.

    Le tour de réparation invente ici `flights` — le défaut d'``acfd8f5``, par
    la dernière porte qui restait. Il est écarté comme la première formulation
    l'a été, et c'est le repli qui part : un nom inventé n'atteint jamais
    l'utilisateur, quelle que soit la passe qui l'a écrit.
    """
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "Tu travailles sur les sources `ventes` et `stocks`.",
    ).script(REPARATION, [text("J'ai `ventes`, `stocks` et `flights`, trois fichiers.")])

    reponse = orchestrateur(llm, catalog=_deux_sources(mini_csv), registry=registre).ask(
        "ventes et stocks, en deux mots ?"
    )

    assert "flights" not in reponse.answer
    assert "carnet de commandes" in reponse.answer  # les faits, tels quels
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "faits servis tels quels" in detail
    assert "2e passe : nom(s) qu'aucun fait ne porte : flights" in detail


def test_le_repli_part_toujours_quand_les_deux_passes_echouent(mini_csv: Path, registre: Registry):
    """Le repli n'a pas disparu — c'était la condition de ce correctif.

    Le tour de réparation décline (le refus par défaut de la doublure), donc les
    deux formulations sont écartées, donc les faits partent : exactement le
    comportement d'avant, sur un tour où il n'y avait rien à gagner.
    """
    llm = agent_systeme("sources_de_donnees", {}, "Ma source est `ventes`.")

    reponse = orchestrateur(llm, catalog=_deux_sources(mini_csv), registry=registre).ask(
        "liste tes bases"
    )

    assert "stocks" in reponse.answer  # rendue par les faits, oubliée par le modèle
    assert llm.prompts_for(REPARATION) != []  # le tour a bien eu lieu
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "1re passe : fait(s) omis : stocks" in detail
    assert "2e passe : réponse hors sujet" in detail


def test_une_premiere_formulation_fondee_ne_coute_aucun_tour_de_plus(
    mini_csv: Path, registre: Registry
):
    """Le coût, et c'est la moitié du contrat : rien sur les tours déjà verts.

    Un tour que la ceinture laisse passer ne rappelle pas le modèle. Sans ce
    test, la seconde chance pourrait devenir un appel LLM sur CHAQUE question
    méta — le nœud est en tête du graphe, et il reçoit tout.
    """
    llm = agent_systeme(
        "sources_de_donnees",
        {},
        "`ventes` tient le carnet de commandes ; `stocks` couvre les entrepôts.",
    )

    reponse = orchestrateur(llm, catalog=_deux_sources(mini_csv), registry=registre).ask(
        "ventes et stocks, ça contient quoi ?"
    )

    assert reponse.answer.startswith("`ventes` tient le carnet")
    assert llm.prompts_for(REPARATION) == []


def test_le_tour_de_reparation_recoit_les_memes_faits_et_la_meme_question(
    mini_csv: Path, registre: Registry
):
    """Ce qu'on lui rend, et rien de plus : les faits de l'outil, et le message.

    C'est la définition du tour : pas un indice de plus, pas un outil, pas de
    consigne neuve sur la question. Il ne peut donc rien apprendre que le
    premier tour n'avait pas — ce qui est précisément ce qui autorise à le juger
    à la même ceinture.
    """
    llm = agent_systeme(
        "sources_de_donnees", {}, "Tu travailles sur les sources `ventes` et `stocks`."
    )

    orchestrateur(llm, catalog=_deux_sources(mini_csv), registry=registre).ask(
        "ventes et stocks, en deux mots ?"
    )

    (demande,) = llm.prompts_for(REPARATION)
    assert "ventes et stocks, en deux mots ?" in demande
    assert "Le carnet de commandes." in demande
    assert "Les entrepôts et leurs flux." in demande
    # et le prompt du tour est bien le sien, pas celui de l'agent système
    (systeme_du_tour,) = llm.systems_for(REPARATION)
    assert systeme_du_tour == prompts.gabarit(prompts.REPARATION)
    assert prompts.gabarit(prompts.SYSTEME) not in systeme_du_tour


def test_un_tour_de_reparation_qui_n_aboutit_pas_sert_les_faits(
    mini_csv: Path, registre: Registry, monkeypatch
):
    """Fail-CLOSED, à l'inverse du nœud système, et la raison est dans le sens.

    Le nœud système est fail-open : un incident lui fait rendre la question au
    planificateur, qui aurait peut-être su répondre. Ici il n'y a rien à rendre à
    personne — le repli est déjà prêt et il est juste. Un incident du tour de
    réparation ne doit donc rien coûter à l'utilisateur.
    """

    def _echoue(*args, **kwargs):
        raise UnexpectedModelBehavior("Exceeded maximum retries")

    monkeypatch.setattr("data_analyst_agent.orchestrator.systeme.build_reparation_agent", _echoue)
    llm = agent_systeme("sources_de_donnees", {}, "Ma source est `ventes`.")

    reponse = orchestrateur(llm, catalog=_deux_sources(mini_csv), registry=registre).ask(
        "liste tes bases"
    )

    assert reponse.error is None
    assert "stocks" in reponse.answer
    detail = next(s for s in reponse.trace if s.node == "system").detail
    assert "2e passe : tour de réparation écarté (UnexpectedModelBehavior)" in detail


def test_les_trois_voies_se_nomment_et_se_lisent_dans_la_trace():
    """La propriété, sans modèle : trois voies, trois détails, et pas un de plus.

    ``servir_la_reponse`` est la seule chose qui décide de ce que l'utilisateur
    lit ; ce test fige le vocabulaire que la trace et la mesure partagent. Ils
    doivent le partager : un runner qui nommerait les voies autrement que la
    trace mesurerait un tour que personne ne peut retrouver dans un journal.
    """
    assert (VOIE_PREMIERE_PASSE, VOIE_SECONDE_PASSE, VOIE_REPLI) == (
        "1re passe",
        "2e passe",
        "repli",
    )
    faits = "- `ventes` : le carnet de commandes"
    resultat = ResultatSysteme(
        reponse=faits, faits=faits, faits_a_enumerer=faits, outils_appeles=("sources_de_donnees",)
    )

    rendue = servir_la_reponse(resultat, question="?", model=None, request_limit=1)

    assert (rendue.voie, rendue.texte) == (VOIE_PREMIERE_PASSE, faits)
    assert rendue.detail == "formulé par le modèle"


# --- ce qu'on s'interdit d'écrire, figé ---------------------------------------

# Les empreintes des sept fiches d'outils de l'agent système. Elles ne sont pas
# là pour empêcher de les modifier : elles sont là pour qu'une modification
# soit un GESTE, avec une campagne à l'appui.
#
# Cinq formulations ont déjà été écrites pour dire au modèle ce que les deux
# planchers font désormais tout seuls — trois dans la démarche du prompt, deux
# dans les fiches de `sources_de_donnees` et de `chercher_une_source`. Les cinq
# ont été retirées : chacune coûtait une question de la surface
# conversationnelle, trois tirages sur trois (`features-familier`,
# `volumetrie-globale`, `periode-directe`). Un témoin de quatre lignes VIDES au
# même endroit ne coûtait rien — ce n'est donc pas la longueur du prompt, c'est
# son contenu. Une fiche plus attirante attire aussi ce qui ne la regarde pas.
#
# L'empreinte du PROMPT système n'est plus ici. Celle qui s'y trouvait ne
# gardait rien : elle hachait `prompts.SYSTEME`, c'est-à-dire la chaîne
# « systeme.txt » — le nom du fichier — et non son contenu. Elle serait restée
# verte quoi qu'on écrive dans le prompt. Les sept prompts du paquet sont
# désormais couverts, sur leur CONTENU et sur le dossier au complet, par
# `tests/unit/test_prompts.py`.
EMPREINTES_DES_FICHES_D_OUTIL = {
    "capacites_de_l_agent": "eb78b85c45d3d067d33da6c14fd4f14e9be2f9010541a62bd80256d92e5b51d2",
    "sources_de_donnees": "9fe6ce293a71bb0049bdcbccf5f7131936eec1794fcbb14bd7baba4dc850442c",
    "chercher_une_source": "fa0c1fed82a2afd9a25581b5efacdf08878e63316cf46edaba8a06a02d4492c5",
    "schema_d_une_source": "1eea42390673be0faaba9b306b95992d989c0e61be3d06aa4c3fe25d6275f3db",
    "travailler_sur_une_source": "6d5b6232f652a60652e517b5e11318a923440766b342aae1989d4cb89391f989",
    "modeles_de_prediction": "e6acc1ec8c22ab79a40fa83b6072253d2f310619c89c2a031248f8702394f80f",
    "attributs_d_un_modele": "412f4dea2265524db4cbe947ef4c841f16e1a5da4b8449b0348d8ac5d3e061e1",
}


def _empreinte(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def test_aucune_fiche_d_outil_n_a_bouge_au_caractere_pres():
    """Les sept fiches d'outils, à l'octet près.

    `test_aucune_fiche_d_outil_n_a_bouge` interdisait deux phrases nommément ;
    celui-ci interdit tout ajout. La différence compte : ce qui a coûté
    `periode-directe` n'était pas une phrase en particulier, c'était le fait
    d'avoir rendu une fiche plus attirante.
    """
    (toolset,) = build_systeme_agent().toolsets

    empreintes = {nom: _empreinte(outil.description or "") for nom, outil in toolset.tools.items()}

    assert empreintes == EMPREINTES_DES_FICHES_D_OUTIL


# --- le plancher du repli : ce que le schéma dit quand le planificateur renonce


def test_le_repli_du_planificateur_sert_ce_que_le_schema_DIT_du_terme_nomme(
    tmp_path: Path, registre: Registry
):
    """W3, mesuré 0/3 : « pourquoi class_id et pas directement la classe ? »

    Le fil brut, trois tirages sur trois : `system → plan → synthesize`, l'agent
    système n'appelle aucun outil, le planificateur ne sait pas classer la
    demande, et l'utilisateur reçoit « Je n'ai pas bien compris ta demande »
    suivi de l'inventaire des sources. `titanic` ne déclare AUCUN dictionnaire,
    donc le plancher de C47 ne peut rien pour elle.

    Le schéma, lui, en dit quelque chose — `class_id` est une clé étrangère vers
    `classes` — et l'agent système sait déjà l'écrire : c'est ce que rend
    ``schema_d_une_source``, vert sur la surface. Ce qui manquait n'était ni le
    fait ni la phrase, c'était qu'on les serve au tour qui renonce.
    """
    csv = tmp_path / "passengers.csv"
    csv.write_text("passenger_id,class_id\n1,3\n2,1\n", encoding="utf-8")
    catalogue = Catalog(sources=[FileSource(name="titanic", path=csv)])
    # l'agent système décline (par défaut), et le planificateur n'aboutit pas
    llm = ScriptedLLM().script(PLANNER, [text("je ne sais pas")] * 4)

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "pourquoi class_id et pas directement la classe ?",
        conversation_id="fil",
        source_de_travail="titanic",
    )

    assert reponse.error is None
    assert "Je n'ai pas bien compris" not in reponse.answer
    assert "`class_id`" in reponse.answer
    # une source sans dictionnaire n'en fait citer aucun : c'est le témoin
    assert "dictionnaire" not in reponse.answer.lower()


def test_un_repli_sans_terme_declare_reste_le_repli(tmp_path: Path, registre: Registry):
    """Le plancher ne parle QUE là où l'installation déclare le terme nommé.

    Sans cela il remplacerait une demande de précision par un tour d'horizon des
    sources — c'est-à-dire par ce que le repli énumère déjà.
    """
    csv = tmp_path / "passengers.csv"
    csv.write_text("passenger_id,class_id\n1,3\n", encoding="utf-8")
    catalogue = Catalog(sources=[FileSource(name="titanic", path=csv)])
    llm = ScriptedLLM().script(PLANNER, [text("je ne sais pas")] * 4)

    reponse = orchestrateur(llm, catalog=catalogue, registry=registre).ask(
        "fais-moi un truc sympa", conversation_id="fil", source_de_travail="titanic"
    )

    assert "Je n'ai pas bien compris" in reponse.answer
