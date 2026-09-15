"""Rappeler un artefact par son nom, le relire, le rejouer modifié.

Le sujet de ce fichier est la **profondeur** : « reprends le graphe de tout à
l'heure » doit marcher au tour +2 et au-delà, pas seulement au tour
immédiatement suivant. C'est la limite que le mécanisme lève, et c'est donc ce
que chaque parcours ici mesure — deux tours SANS rapport entre la figure et sa
reprise.

Sans Docker ni réseau : la sandbox est doublée, le LLM est scripté. Ce qui est
sous test est le magasin, le routage vers l'agent de rappel, ce qui entre dans
son prompt et ce qu'il refuse.
"""

from pathlib import Path

import joblib
import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.context_budget import ContextLimits, estimate_tokens
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.orchestrator.rappel import (
    designation_dun_artefact_passe,
    noms_inventes,
    refus_dartefact,
)
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import MimeOutput, SandboxResult
from helpers.doubles import FakeClassifier, ScriptedSandbox
from helpers.scripted_llm import (
    ANALYSIS,
    PLANNER,
    RAPPEL,
    RETRIEVAL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

CODE_ROUGE = "import matplotlib.pyplot as plt\nplt.bar([1, 2], [3, 4], color='red')\nplt.show()\n"
CODE_BLEU = CODE_ROUGE.replace("red", "blue")
PNG = MimeOutput(mime="image/png", data="iVBORw0KGgo=")

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


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def iris_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "iris.csv"
    csv.write_text(
        "sepal_length,species\n5.1,setosa\n7.0,versicolor\n6.3,virginica\n", encoding="utf-8"
    )
    return csv


def orchestrateur(llm: ScriptedLLM, iris_csv: Path, tmp_path: Path, sandbox, registry):
    return Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[FileSource(name="iris", path=iris_csv)]),
        registry=registry,
        sandbox=sandbox,
        settings=Settings(_env_file=None, workspace_dir=tmp_path),
    )


def figure_ok(code_rendu: str) -> list:
    """Ce que l'agent d'analyse et la sandbox rendent pour une figure qui aboutit."""
    return [text(f"```python\n{code_rendu}```")]


# --- le magasin lui-même ------------------------------------------------------


def test_le_code_est_un_artefact_nomme_au_meme_titre_quun_tableau(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_table(["a"], [[1]], "combien de lignes ?")
    figure = ws.save_code(CODE_ROUGE, "fais un graphe", source="iris", figures=1)
    analyse = ws.save_code("print(1)", "calcule la variance", source="iris", figures=0)

    assert [a.name for a in ws.artifacts] == ["resultat_1", "graphique_1", "analyse_1"]
    assert figure.kind == "figure"
    assert analyse.kind == "code"
    # le contenu est bien sur le disque, et se relit tel quel
    assert ws.lire(figure) == CODE_ROUGE
    # un magasin rouvert retrouve les trois — c'est ce qui fait la profondeur
    assert [a.name for a in ConversationWorkspace(tmp_path, "fil").artifacts] == [
        "resultat_1",
        "graphique_1",
        "analyse_1",
    ]


def test_le_prompt_ne_recoit_que_le_catalogue_jamais_le_code(tmp_path: Path):
    """Le point qui fait tenir la fenêtre : une ligne par artefact, pas son contenu."""
    ws = ConversationWorkspace(tmp_path, "fil")
    long_code = CODE_ROUGE + "# " + "x" * 5000 + "\n"
    ws.save_code(long_code, "fais un graphe des espèces", source="iris", figures=1)

    catalogue = ws.catalogue_du_code()
    assert "graphique_1" in catalogue
    assert "fais un graphe des espèces" in catalogue
    assert "matplotlib" not in catalogue  # le CODE n'y est pas
    assert len(catalogue) < 250  # une ligne, pas cinq mille caractères
    assert "matplotlib" not in (ws.describe() or "")


def test_la_fenetre_du_code_est_distincte_de_celle_des_tableaux(tmp_path: Path):
    """Quatre tableaux ne doivent pas chasser la figure du premier tour.

    C'est la raison d'être de la fenêtre séparée : les deux natures ne coûtent
    pas la même chose, et les faire partager un plafond de huit évincerait
    exactement ce qu'on cherche à pouvoir rappeler.
    """
    limites = ContextLimits(artifact_window=4, code_window=4, token_budget=0)
    ws = ConversationWorkspace(tmp_path, "fil", limits=limites)
    ws.save_code(CODE_ROUGE, "fais un graphe", source="iris", figures=1)
    for tour in range(6):
        ws.save_table(["a"], [[tour]], f"question {tour}")

    assert [a.name for a in ws.injected] == [f"resultat_{n}" for n in (3, 4, 5, 6)]
    assert [a.name for a in ws.codes_injectes] == ["graphique_1"]
    assert ws.retenu("graphique_1") is not None


def test_un_manifeste_ecrit_avant_ce_mecanisme_se_relit_en_tableau(tmp_path: Path):
    """Aucune migration : un manifeste sans `kind` décrit ce qu'il décrivait."""
    ancien = ConversationWorkspace(tmp_path, "fil")
    ancien.save_table(["a"], [[1]], "q")
    manifeste = tmp_path / "fil" / "manifest.json"
    manifeste.write_text(
        manifeste.read_text(encoding="utf-8")
        .replace('"kind": "table",', "")
        .replace('"description": "tableau de 1 ligne(s) ; colonnes : a",', ""),
        encoding="utf-8",
    )
    relu = ConversationWorkspace(tmp_path, "fil")
    assert [a.name for a in relu.injected] == ["resultat_1"]
    assert relu.artifacts[0].kind == "table"


# --- le refus : jamais une invention ------------------------------------------


def test_un_nom_inconnu_est_refuse_en_disant_ce_qui_existe(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "fais un graphe", source="iris", figures=1)

    refus = refus_dartefact(ws, "graphique_9")
    assert "Aucun artefact ne s'appelle « graphique_9 »" in refus
    assert "graphique_1" in refus  # ce qui existe est dit


def test_un_artefact_evince_est_refuse_autrement_quun_inexistant(tmp_path: Path):
    """Deux refus, pas un : « il a été évincé » n'est pas « il n'existe pas ».

    Le second dit qu'on a bien produit la chose demandée, mais qu'elle est
    sortie de la fenêtre. Les confondre reviendrait à dire à quelqu'un qu'il n'a
    jamais demandé ce graphique.
    """
    limites = ContextLimits(artifact_window=1, code_window=1, token_budget=0)
    ws = ConversationWorkspace(tmp_path, "fil", limits=limites)
    ws.save_code(CODE_ROUGE, "le premier graphe", source="iris", figures=1)
    ws.save_code(CODE_BLEU, "le second graphe", source="iris", figures=1)

    evince = refus_dartefact(ws, "graphique_1")
    assert "ÉVINCÉ" in evince
    assert "DAA_CONTEXT" in evince  # le réglage qui a coupé est nommé
    assert "graphique_2" in evince  # ce qui reste est dit
    assert "Aucun artefact" in refus_dartefact(ws, "graphique_7")


def test_un_nom_dartefact_jamais_produit_est_reconnu_comme_invente(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "fais un graphe", source="iris", figures=1)

    assert noms_inventes("J'ai repris `graphique_1`.", ws) == []
    assert noms_inventes("J'ai repris `graphique_4` et resultat_2.", ws) == [
        "graphique_4",
        "resultat_2",
    ]
    # une variable du code relu n'est pas un nom d'artefact : la forme est fermée
    assert noms_inventes("plt.bar(x, hauteur_max)", ws) == []


# --- le parcours, de bout en bout ---------------------------------------------


def parcours_de_six_tours(llm: ScriptedLLM, orch: Orchestrator, fil: str) -> list:
    """Figure, deux tours SANS rapport, puis la reprise. Le tour +2 est la mesure."""
    reponses = [orch.ask("fais-moi un graphe des espèces", conversation_id=fil)]
    reponses.append(orch.ask("combien de lignes dans iris ?", conversation_id=fil))
    reponses.append(orch.ask("et combien d'espèces ?", conversation_id=fil))
    reponses.append(
        orch.ask(
            "reprends le graphe de tout à l'heure et mets les barres en bleu",
            conversation_id=fil,
        )
    )
    return reponses


def test_le_graphe_se_reprend_deux_tours_plus_tard(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le parcours qui mesure la profondeur : produire, digresser deux fois, reprendre.

    Avant ce mécanisme, `last_code` était ÉCRASÉ à chaque tour : la reprise ne
    marchait qu'au tour +1. Ici elle est demandée au tour +3, après deux
    requêtes qui n'ont rien à voir, et elle repart du code d'origine.
    """
    sandbox = ScriptedSandbox(
        [
            SandboxResult(status="ok", results=[PNG]),  # tour 1 : la figure
            SandboxResult(status="ok", results=[PNG]),  # tour 4 : le rejeu
        ]
    )
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="iris"))])
        .script(ANALYSIS, figure_ok(CODE_ROUGE))
        .script(SYNTHESIS, [text("Voici le graphe des espèces.")])
        # tours 2 et 3 : deux requêtes sans rapport
        .script(
            PLANNER,
            [
                plan_response(Plan(capability="query", source="iris")),
                plan_response(Plan(capability="query", source="iris")),
            ],
        )
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT COUNT(*) AS n FROM iris"}),
                text("Il y a 3 lignes."),
                tool_call("run_sql", {"query": "SELECT COUNT(DISTINCT species) AS n FROM iris"}),
                text("Il y a 3 espèces."),
            ],
        )
        # tours 2 et 3 : le fil PORTE un artefact, l'agent de rappel est donc
        # consulté — et décline, puisque ces questions ne le concernent pas.
        .script(RAPPEL, [text(ScriptedLLM.REFUS_DU_SYSTEME)] * 2)
        # tour 4 : l'agent de rappel reconnaît l'artefact et le rejoue
        .script(
            RAPPEL,
            [
                tool_call(
                    "rejouer_un_code",
                    {"nom": "graphique_1", "modification": "mets les barres en bleu"},
                ),
                text("J'ai rejoué `graphique_1` avec les barres en bleu."),
            ],
        )
        .script(ANALYSIS, figure_ok(CODE_BLEU))
        .script(SYNTHESIS, [text("Voici le graphe, barres en bleu.")])
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponses = parcours_de_six_tours(llm, orch, "fil")

    dernier = reponses[-1]
    assert dernier.error is None
    # le rejeu est bien PASSÉ PAR LA SANDBOX, avec le code d'origine en base
    assert sandbox.executed[-1] == CODE_BLEU.strip()
    assert "red" in llm.prompts_for(ANALYSIS)[-1]  # le code rappelé a servi de base
    assert [a.mime for a in dernier.artifacts] == ["image/png"]
    # un rejeu EST une analyse : même plan, même synthèse, même mémorisation
    assert dernier.plan.capability == "analyze"
    assert "rappel" in [s.node for s in dernier.trace]

    # et le rejeu est lui-même devenu un artefact, rejouable à son tour
    ws = ConversationWorkspace(tmp_path, "fil")
    assert [a.name for a in ws.artifacts if a.est_du_code] == ["graphique_1", "graphique_2"]
    assert ws.lire(ws.sur_le_disque("graphique_2")) == CODE_BLEU.strip()


def test_un_tableau_intermediaire_se_reprend_par_son_nom(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le même parcours, sur un TABLEAU désigné par son nom, deux tours plus tard."""
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_table(["espece", "n"], [["setosa", 50]], "répartition des espèces")
    ws.save_table(["a"], [[1]], "une digression")
    ws.save_table(["b"], [[2]], "une autre digression")

    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("lire_un_artefact", {"nom": "resultat_1"}),
            text("`resultat_1` contient la répartition des espèces : setosa, 50 lignes."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("remontre-moi le tableau des espèces", conversation_id="fil")

    assert reponse.error is None
    assert "resultat_1" in reponse.answer
    # le contenu du tableau a bien été OUVERT, et n'était pas dans le prompt
    assert "setosa" not in llm.systems_for(RAPPEL)[0]
    assert "répartition des espèces" in llm.systems_for(RAPPEL)[0]  # le catalogue, lui, y est


def test_un_artefact_absent_est_refuse_pas_invente(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le refus est servi TEL QUEL, même si le modèle avait mieux à raconter.

    Famille `acfd8f5` : un modèle à qui l'on demande une chose absente sait la
    fabriquer, nom compris. Un nom inventé est plus nocif qu'un refus — il a
    l'air d'un rappel.
    """
    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "une question")
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("rejouer_un_code", {"nom": "graphique_1", "modification": "en bleu"}),
            text("J'ai remis `graphique_1` en bleu, le voici."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("remets le camembert en bleu", conversation_id="fil")

    assert reponse.error is None
    assert "Aucun artefact ne s'appelle « graphique_1 »" in reponse.answer
    assert "le voici" not in reponse.answer  # la phrase du modèle est écartée
    assert not reponse.artifacts


def test_une_formulation_qui_invente_un_nom_fait_servir_les_faits(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "fais un graphe", source="iris", figures=1)
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("lire_un_artefact", {"nom": "graphique_1"}),
            text("Le code de `graphique_3` trace des barres."),  # nom jamais produit
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("montre-moi le code du graphe", conversation_id="fil")

    assert "graphique_3" not in reponse.answer
    assert "matplotlib" in reponse.answer  # les faits — le code lu — servis tels quels
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert "nom(s) inventé(s) : graphique_3" in trace.detail


def test_le_noeud_ne_coute_rien_dans_un_fil_qui_na_rien_produit(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Précondition structurelle, pas lexique : sans artefact, pas d'appel du tout.

    C'est ce qui garantit que la batterie de
    `scripts/mesure_surface_conversationnelle.py` — une conversation neuve par
    question — ne paie pas ce nœud et n'en voit pas le comportement changer.
    """
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source="iris"))])
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("combien de lignes ?", conversation_id="neuf")

    assert llm.prompts_for(RAPPEL) == []  # le modèle n'a pas été sollicité
    assert "rappel" not in [s.node for s in reponse.trace]  # ni trace, ni bruit


def test_un_rejeu_qui_echoue_est_dit_et_ne_livre_aucune_figure(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Un rejeu raté est une analyse ratée : même message, mêmes axes vides écartés."""
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "fais un graphe", source="iris", figures=1)
    sandbox = ScriptedSandbox(
        [SandboxResult(status="error", error="Traceback\nValueError: x", results=[PNG])] * 3
    )
    llm = (
        ScriptedLLM()
        .script(
            RAPPEL,
            [
                tool_call("rejouer_un_code", {"nom": "graphique_1", "modification": "en bleu"}),
                text("Le rejeu n'a pas abouti."),
            ],
        )
        .script(ANALYSIS, figure_ok(CODE_BLEU) * 3)
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask("remets le graphe en bleu", conversation_id="fil")

    assert "n'a pas abouti" in reponse.answer
    assert not reponse.artifacts  # pas d'axes vides livrés
    assert "ValueError" not in reponse.answer  # le traceback reste dans la trace
    # un code qui ne tourne pas n'entre pas au catalogue : ce serait un échec rejouable
    assert [a.name for a in ConversationWorkspace(tmp_path, "fil").artifacts] == ["graphique_1"]


def test_le_catalogue_injecte_ne_fait_pas_exploser_le_prompt(tmp_path: Path):
    """Vingt figures produites : le prompt en porte l'index, jamais le contenu."""
    limites = ContextLimits(artifact_window=8, code_window=8, token_budget=0)
    ws = ConversationWorkspace(tmp_path, "fil", limits=limites)
    for tour in range(20):
        ws.save_code(CODE_ROUGE + "#" + "y" * 3000, f"graphe du tour {tour}", figures=1)

    catalogue = ws.catalogue_du_code()
    assert catalogue.count("\n- ") == 8  # huit lignes retenues sur vingt produites
    # Le rapport est ce qui compte, et il est de deux ordres de grandeur : le
    # CONTENU des huit codes retenus pèserait des milliers de tokens, leur index
    # en pèse quelques centaines. C'est ce qui permet à une conversation de
    # durer sans que le prompt du planificateur suive la courbe.
    contenu = estimate_tokens(*(ws.lire(a) for a in ws.codes_injectes))
    assert estimate_tokens(catalogue) < contenu / 20
    assert estimate_tokens(catalogue) < 400


# --- les chemins de bordure ---------------------------------------------------


def test_lire_un_nom_inconnu_refuse_au_lieu_de_lever(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    ConversationWorkspace(tmp_path, "fil").save_code(CODE_ROUGE, "un graphe", figures=1)
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("lire_un_artefact", {"nom": "resultat_7"}),
            text("Voici le tableau `resultat_7`."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("remontre-moi le tableau", conversation_id="fil")

    assert "Aucun artefact ne s'appelle « resultat_7 »" in reponse.answer


def test_rejouer_un_TABLEAU_est_refuse_sans_tenter_de_l_executer(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Un CSV n'est pas du code : on le dit, on ne l'envoie pas au bac à sable."""
    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "une question")
    sandbox = ScriptedSandbox([])  # tout appel ferait lever IndexError
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("rejouer_un_code", {"nom": "resultat_1", "modification": "en bleu"}),
            text("C'est rejoué."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask("remets ça en bleu", conversation_id="fil")

    assert "ce n'est pas du code" in reponse.answer
    assert sandbox.executed == []


def test_un_message_qui_complete_une_prediction_ne_passe_pas_par_le_rappel(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Les features d'une prédiction en attente ne sont pas une question.

    Y faire passer l'agent de rappel lui donnerait l'occasion de s'emparer d'un
    message qui ne lui est pas adressé — c'est le défaut du §12, par une autre
    porte.
    """
    from data_analyst_agent.orchestrator.graph import PendingInference

    ConversationWorkspace(tmp_path, "fil").save_code(CODE_ROUGE, "un graphe", figures=1)
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"sex": "female"}))],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask(
        "elle était en 1re classe",
        conversation_id="fil",
        pending=PendingInference(dataset="titanic", features={"age": 28.0}),
    )

    assert llm.prompts_for(RAPPEL) == []
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert "prédiction en attente" in trace.detail


def test_un_incident_du_modele_de_rappel_rend_la_main_au_planificateur(
    tmp_path: Path, iris_csv: Path, registry: Registry, monkeypatch: pytest.MonkeyPatch
):
    """Fail-open, et seulement pour ce que le MODÈLE rate.

    Un planificateur qui aurait su répondre ne doit pas être privé de la
    question par un incident de ce nœud-ci.
    """
    from pydantic_ai import UnexpectedModelBehavior

    ConversationWorkspace(tmp_path, "fil").save_code(CODE_ROUGE, "un graphe", figures=1)
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="iris"))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT COUNT(*) AS n FROM iris"}), text("3 lignes.")],
        )
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)

    def tombe(*_args, **_kwargs):
        raise UnexpectedModelBehavior("sortie invalide")

    monkeypatch.setattr("data_analyst_agent.orchestrator.graph.run_rappel", tombe)
    reponse = orch.ask("combien de lignes ?", conversation_id="fil")

    assert reponse.error is None
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert "agent de rappel écarté" in trace.detail
    assert "plan" in [s.node for s in reponse.trace]  # le tour a bien continué


def test_un_noeud_en_erreur_ne_repart_pas_au_planificateur():
    """Le garde-fou a renseigné `error` : on synthétise, on ne replanifie pas."""
    assert Orchestrator._apres_le_rappel({"error": "quelque chose"}) == "synthesize"
    assert Orchestrator._apres_le_rappel({}) == "plan"


def test_rejouer_quand_la_source_dorigine_a_disparu_du_catalogue(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Source renommée ou retirée : on remonte le décor disponible plutôt que de refuser.

    Le code échouera peut-être, et il échouera DANS le bac à sable avec un
    message — ce qui vaut mieux que de refuser un rejeu qui aurait pu marcher.
    """
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "un graphe", source="une-source-disparue", figures=1)
    sandbox = ScriptedSandbox([SandboxResult(status="ok", results=[PNG])])
    llm = (
        ScriptedLLM()
        .script(
            RAPPEL,
            [
                tool_call("rejouer_un_code", {"nom": "graphique_1", "modification": "en bleu"}),
                text("Rejoué."),
            ],
        )
        .script(ANALYSIS, figure_ok(CODE_BLEU))
        .script(SYNTHESIS, [text("Voici.")])
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask("remets le graphe en bleu", conversation_id="fil")

    assert reponse.error is None
    assert sandbox.executed[-1] == CODE_BLEU.strip()


def test_rejouer_sans_aucune_source_au_catalogue_est_une_erreur_dite(
    tmp_path: Path, registry: Registry
):
    """Rien à monter : le nœud échoue proprement, avec le message de son garde-fou."""
    ConversationWorkspace(tmp_path, "fil").save_code(CODE_ROUGE, "un graphe", figures=1)
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("rejouer_un_code", {"nom": "graphique_1", "modification": "en bleu"}),
            text("Rejoué."),
        ],
    )
    orch = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[]),
        registry=registry,
        settings=Settings(_env_file=None, workspace_dir=tmp_path),
    )
    reponse = orch.ask("remets le graphe en bleu", conversation_id="fil")

    assert "n'ai pas pu reprendre ce qui a déjà été produit" in reponse.answer
    assert "incident" in reponse.answer  # la référence, pas la cause technique


def test_lire_un_grand_tableau_en_rend_la_tete_et_dit_le_reste(tmp_path: Path):
    """On ouvre un tableau pour savoir de quoi on parle, pas pour le recopier."""
    ws = ConversationWorkspace(tmp_path, "fil")
    artefact = ws.save_table(["n"], [[i] for i in range(200)], "deux cents lignes")

    lu = ws.lire(artefact)
    assert lu.count("\n") == 21  # l'en-tête, vingt lignes, et l'avertissement
    assert "200 lignes au total, 20 montrées" in lu


def test_le_catalogue_du_rappel_DIT_ce_qui_a_ete_evince(tmp_path: Path):
    """Un catalogue qui montre ce qui reste sans dire ce qui est sorti fait croire
    au modèle qu'il voit tout — et il rejoue alors le voisin le plus proche."""
    from data_analyst_agent.orchestrator.rappel import catalogue_pour_le_prompt

    limites = ContextLimits(artifact_window=8, code_window=1, token_budget=0)
    ws = ConversationWorkspace(tmp_path, "fil", limits=limites)
    ws.save_code(CODE_ROUGE, "le graphe par classe", figures=1)
    ws.save_code(CODE_BLEU, "l'histogramme des âges", figures=1)

    catalogue = catalogue_pour_le_prompt(ws)
    assert "graphique_2" in catalogue
    assert "graphique_1" not in catalogue  # évincé : il n'est PAS au catalogue
    assert "ÉVINCÉS" in catalogue  # mais son éviction, elle, est dite
    assert "ne rejoue surtout pas un autre artefact à sa place" in catalogue


# --- les trois défauts trouvés en mesure live --------------------------------


def test_la_sentinelle_apres_un_appel_doutil_fait_servir_les_faits(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Mesuré sur vLLM : outil appelé, tableau lu — puis « AUTRE » en guise de réponse.

    L'utilisateur recevait le mot `AUTRE`. Pire qu'un refus : ce n'en est même
    pas un. Un tour où un outil a répondu n'est plus « pas pour moi », et la
    contradiction se tranche du côté de ce qui a été lu.
    """
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_table(["count"], [[891]], "combien de passagers ?")
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("lire_un_artefact", {"nom": "resultat_1"}),
            text(ScriptedLLM.REFUS_DU_SYSTEME),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("le tableau que tu m'as sorti, il disait quoi ?", conversation_id="fil")

    assert reponse.answer.strip() != ScriptedLLM.REFUS_DU_SYSTEME
    assert "891" in reponse.answer  # les faits, servis tels quels
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert "sentinelle rendue alors qu'un outil a été appelé" in trace.detail


def test_une_formulation_sans_aucun_fait_de_loutil_fait_servir_les_faits(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Mesuré sur vLLM : l'outil rend `count / 891`, le modèle répond qu'il ne peut pas.

    Il tenait la réponse et l'a rendue vide. La garde est généreuse — UN seul
    jeton commun suffit : on distingue « formulé autrement » de « n'a rien
    formulé du tout », on ne note pas un style.
    """
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_table(["count"], [[891]], "combien de passagers ?")
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("lire_un_artefact", {"nom": "resultat_1"}),
            text("Je suis désolé, mais je ne peux pas répondre à cette demande."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("le tableau que tu m'as sorti, il disait quoi ?", conversation_id="fil")

    assert "désolé" not in reponse.answer
    assert "891" in reponse.answer
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert "formulation sans aucun fait de l'outil" in trace.detail


def test_une_formulation_qui_porte_un_seul_fait_est_servie_telle_quelle(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """La garde est une ceinture, pas un goût : une phrase juste passe."""
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_table(["count"], [[891]], "combien de passagers ?")
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("lire_un_artefact", {"nom": "resultat_1"}),
            text("Il y avait 891 passagers."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("le tableau que tu m'as sorti, il disait quoi ?", conversation_id="fil")

    assert reponse.answer == "Il y avait 891 passagers."


def test_une_reponse_vide_se_disqualifie_aussi(tmp_path: Path):
    """Le troisième cas de la ceinture, sans passer par un tour entier."""
    from data_analyst_agent.orchestrator.rappel import defaut_de_formulation

    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "un graphe", figures=1)

    assert defaut_de_formulation("   ", ws) == "réponse vide"
    assert defaut_de_formulation("J'ai relu `graphique_1`.", ws) == ""


# --- désigner un artefact qui n'existe pas : le dire avant d'en produire un ---
#
# Le défaut, mesuré en live sur les DEUX moteurs : « reprends le camembert des
# ports que tu m'avais fait » dans un fil qui n'en porte aucun. L'agent de
# rappel décline, le planificateur fabrique un camembert neuf — correct, bons
# chiffres — et rien ne dit qu'il n'existait pas. L'utilisateur repart en
# croyant qu'on a retrouvé son travail alors qu'on en a refait un autre.

# Six tournures qui DÉSIGNENT, dont trois sans un mot du catalogue (« celui
# d'avant », « le précédent », « ce que tu m'avais sorti ») : c'est là que le
# signal doit être grammatical et non lexical.
DESIGNATIONS = [
    "Reprends le camembert des ports d'embarquement que tu m'avais fait.",
    "Tu peux me remontrer le camembert des ports d'embarquement que tu avais fait ?",
    "Reprends le graphe de tout à l'heure et mets les barres en bleu.",
    "Reviens au tout premier graphique, celui par classe, et repasse-le en vert.",
    "Le premier tableau que tu m'as sorti, redis-moi ce qu'il y avait dedans.",
    "Le graphique de tantôt, en vert.",
    "Refais le même graphique mais en bleu.",
    "Je voudrais revoir la courbe que tu as tracée plus tôt.",
    # les trois sans aucun mot du catalogue
    "Remontre-moi celui d'avant.",
    "Affiche le précédent.",
    "Ce que tu m'avais sorti, tu peux me le remettre ?",
]

# Le défaut SYMÉTRIQUE : une tournure innocente prise pour une désignation
# coûterait une phrase de repentir à une demande neuve. Les six premières sont
# des questions RÉELLES de `scripts/mesure_surface_conversationnelle.py` —
# celles qui portent « tu as », le piège du détecteur.
INNOCENTES = [
    "De quand datent les données que tu as ?",
    "qu'est-ce que tu as comme données ?",
    "À quelles bases de données as-tu accès ?",
    "montre-moi ce que tu as",
    "tu as accès à quelles données",
    "Quelles colonnes de la table passengers contiennent des valeurs manquantes ?",
    "Fais-moi un camembert des ports d'embarquement.",
    "Fais-moi un graphique en barres du nombre de passagers par classe.",
    "Combien de passagers y a-t-il en tout ?",
    "Et quel est l'âge moyen des passagers ?",
    "Peux-tu me faire un histogramme des âges ?",
    "Quel est l'âge du passager le plus âgé ?",
    "Compare les résultats de la première classe et de la deuxième.",
    "Ajoute une légende au graphique.",
    "Fais un tableau des survivants par sexe.",
    "Trace la courbe des âges.",
    "Est-ce que tu sais faire des prédictions, et sur quoi ?",
    "Quelle est la taille de tes données ?",
]


@pytest.mark.parametrize("message", DESIGNATIONS)
def test_une_tournure_qui_designe_un_artefact_passe_est_reconnue(message: str):
    """Le signal est dans le MESSAGE : un passé attribué à l'agent, ou un déictique.

    Trois de ces tournures ne portent aucun mot du catalogue — « celui
    d'avant », « le précédent », « ce que tu m'avais sorti ». C'est bien la
    construction qui les qualifie, pas un lexique d'objets.
    """
    assert designation_dun_artefact_passe(message) != ""


@pytest.mark.parametrize("message", INNOCENTES)
def test_une_demande_neuve_nest_pas_prise_pour_une_designation(message: str):
    """Le défaut symétrique, et il coûterait aussi cher.

    « De quand datent les données que tu as ? » porte « tu as » et ne désigne
    rien : c'est le PARTICIPE DE PRODUCTION qui fait la présupposition, pas
    l'auxiliaire. Sans cette exigence, six questions de la batterie recevaient
    un repentir qui n'a pas lieu d'être.
    """
    assert designation_dun_artefact_passe(message) == ""


def test_le_camembert_jamais_produit_est_dit_avant_detre_refait(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le défaut mesuré, et sa correction : la figure part, précédée de l'aveu.

    On ne bloque pas l'utilisateur — le camembert est bien produit. Ce qui
    change est qu'il ne peut plus le prendre pour un rappel.
    """
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "un graphe en barres par classe", source="iris", figures=1)
    sandbox = ScriptedSandbox([SandboxResult(status="ok", results=[PNG])])
    llm = (
        ScriptedLLM()
        .script(RAPPEL, [text(ScriptedLLM.REFUS_DU_SYSTEME)])
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="iris"))])
        .script(ANALYSIS, figure_ok(CODE_BLEU))
        .script(SYNTHESIS, [text("Voici le camembert des ports d'embarquement.")])
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask(
        "Reprends le camembert des ports d'embarquement que tu m'avais fait.",
        conversation_id="fil",
    )

    assert reponse.error is None
    assert "Je n'ai pas produit dans cette conversation" in reponse.answer
    assert "Ce qui suit est neuf, pas un rappel." in reponse.answer
    assert "graphique_1" in reponse.answer  # ce qui EXISTE, lui, est énuméré
    # et la figure est bien produite : on dit le manque, on ne refuse pas
    assert [a.mime for a in reponse.artifacts] == ["image/png"]
    assert "Voici le camembert" in reponse.answer
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert "absence dite" in trace.detail


def test_une_figure_neuve_non_designee_est_produite_sans_un_mot(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """L'autre moitié du mécanisme, et elle compte autant.

    « Fais-moi un camembert des ports » ne présuppose rien : commenter une
    demande neuve serait du bruit à chaque tour.
    """
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "un graphe en barres par classe", source="iris", figures=1)
    sandbox = ScriptedSandbox([SandboxResult(status="ok", results=[PNG])])
    llm = (
        ScriptedLLM()
        .script(RAPPEL, [text(ScriptedLLM.REFUS_DU_SYSTEME)])
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="iris"))])
        .script(ANALYSIS, figure_ok(CODE_BLEU))
        .script(SYNTHESIS, [text("Voici le camembert des ports d'embarquement.")])
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask("Fais-moi un camembert des ports d'embarquement.", conversation_id="fil")

    assert reponse.answer == "Voici le camembert des ports d'embarquement."
    assert [a.mime for a in reponse.artifacts] == ["image/png"]
    trace = next(s for s in reponse.trace if s.node == "rappel")
    assert trace.detail == "aucun outil appelé — passe au planificateur"


def test_un_artefact_EVINCE_ne_se_dit_pas_absent(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le cas distinct, et le dire absent serait un mensonge.

    Il a bien été produit ; il est sorti de la fenêtre. On ne PEUT pas affirmer
    l'absence de ce qu'on ne voit plus — c'est la même frontière que
    `refus_dartefact`, du côté où aucun nom n'a été prononcé.
    """
    limites = ContextLimits(artifact_window=8, code_window=1, token_budget=0)
    ws = ConversationWorkspace(tmp_path, "fil", limits=limites)
    ws.save_code(CODE_ROUGE, "le graphe par classe", source="iris", figures=1)
    ws.save_code(CODE_BLEU, "l'histogramme des âges", source="iris", figures=1)
    sandbox = ScriptedSandbox([SandboxResult(status="ok", results=[PNG])])
    llm = (
        ScriptedLLM()
        .script(RAPPEL, [text(ScriptedLLM.REFUS_DU_SYSTEME)])
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="iris"))])
        .script(ANALYSIS, figure_ok(CODE_ROUGE))
        .script(SYNTHESIS, [text("Voici un graphe par classe.")])
    )
    orch = Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=[FileSource(name="iris", path=iris_csv)]),
        registry=registry,
        sandbox=sandbox,
        settings=Settings(
            _env_file=None, workspace_dir=tmp_path, context_code_window=1, context_token_budget=0
        ),
    )
    reponse = orch.ask(
        "Reviens au tout premier graphique, celui par classe, et repasse-le en vert.",
        conversation_id="fil",
    )

    assert "Je ne peux pas affirmer ne l'avoir jamais produit" in reponse.answer
    assert "ÉVINCÉS" in reponse.answer
    assert "Je n'ai pas produit dans cette conversation" not in reponse.answer
    assert "Ce qui suit est neuf, pas un rappel." in reponse.answer


def test_un_fil_vide_dit_labsence_sans_appeler_le_modele(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le fil neuf est le cas où l'absence est la plus certaine — et la moins chère.

    Le nœud se retire avant tout aller-retour : le catalogue est vide, il n'y a
    rien à décider. L'aveu, lui, ne coûte rien puisqu'il est déterministe.
    """
    sandbox = ScriptedSandbox([SandboxResult(status="ok", results=[PNG])])
    llm = (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="analyze", source="iris"))])
        .script(ANALYSIS, figure_ok(CODE_BLEU))
        .script(SYNTHESIS, [text("Voici le camembert des ports.")])
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask("Remontre-moi le camembert que tu m'avais fait.", conversation_id="neuf")

    assert llm.prompts_for(RAPPEL) == []  # pas un seul appel
    assert "Je n'ai encore rien produit dans cette conversation" in reponse.answer
    assert "Ce qui suit est neuf, pas un rappel." in reponse.answer
    assert [a.mime for a in reponse.artifacts] == ["image/png"]


def test_un_rejeu_qui_aboutit_ne_recoit_aucun_aveu(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Le parcours de C24 ne doit rien perdre : quand le rappel MARCHE, on se tait.

    « Reprends le graphe de tout à l'heure, barres en bleu » DÉSIGNE bien un
    artefact passé — et il existe. L'aveu ne se déclenche que sur une
    désignation restée sans réponse.
    """
    ws = ConversationWorkspace(tmp_path, "fil")
    ws.save_code(CODE_ROUGE, "un graphe par classe", source="iris", figures=1)
    sandbox = ScriptedSandbox([SandboxResult(status="ok", results=[PNG])])
    llm = (
        ScriptedLLM()
        .script(
            RAPPEL,
            [
                tool_call(
                    "rejouer_un_code",
                    {"nom": "graphique_1", "modification": "mets les barres en bleu"},
                ),
                text("J'ai rejoué `graphique_1` avec les barres en bleu."),
            ],
        )
        .script(ANALYSIS, figure_ok(CODE_BLEU))
        .script(SYNTHESIS, [text("Voici le graphe, barres en bleu.")])
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, sandbox, registry)
    reponse = orch.ask(
        "Reprends le graphe de tout à l'heure et mets les barres en bleu.",
        conversation_id="fil",
    )

    assert "Ce qui suit est neuf" not in reponse.answer
    assert "Je n'ai pas produit" not in reponse.answer
    assert [a.mime for a in reponse.artifacts] == ["image/png"]
    assert sandbox.executed[-1] == CODE_BLEU.strip()


def test_un_refus_nomme_nest_pas_double_dun_aveu(
    tmp_path: Path, iris_csv: Path, registry: Registry
):
    """Quand l'agent A appelé un outil et s'est fait refuser, le refus suffit.

    Il nomme l'artefact demandé, ce que l'aveu ne peut pas faire : le doubler
    ferait dire deux fois la même chose, la seconde moins précisément.
    """
    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "une question")
    llm = ScriptedLLM().script(
        RAPPEL,
        [
            tool_call("rejouer_un_code", {"nom": "graphique_1", "modification": "en bleu"}),
            text("J'ai remis `graphique_1` en bleu, le voici."),
        ],
    )
    orch = orchestrateur(llm, iris_csv, tmp_path, None, registry)
    reponse = orch.ask("Remets en bleu le graphe que tu m'avais fait.", conversation_id="fil")

    assert "Aucun artefact ne s'appelle « graphique_1 »" in reponse.answer
    assert "Ce qui suit est neuf" not in reponse.answer
