"""La source de travail d'une conversation : proposée, validée, portée par le fil.

Le parcours demandé tient en trois tours, et chacun a son test ici : l'agent
**propose** ses sources, l'utilisateur **en valide une**, et c'est celle sur
laquelle on travaille **ensuite** — sans que le planificateur ait à la redeviner.

Deux propriétés valent d'être dites, parce qu'elles sont ce qui rend le
mécanisme sûr plutôt que seulement pratique :

- **la reconnaissance du choix est déterministe** (le nom d'une source du
  catalogue, cité dans le message) : aucun aller-retour LLM pour comparer deux
  chaînes de caractères ;
- **une bascule est annoncée**. Le risque n'est pas de changer de source, c'est
  de répondre sur d'autres données sans le dire.

Et une propriété de compatibilité : une conversation **antérieure** à ce champ
continue de fonctionner. Sa transcription ne le porte pas, il vaut donc son
défaut — aucune source liée — et le tour se déroule comme avant.
"""

from pathlib import Path

import joblib
import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.conversations import Conversation, ConversationStore
from data_analyst_agent.orchestrator.graph import Orchestrator, SourceDeTravail
from data_analyst_agent.orchestrator.plan import Plan
from helpers.doubles import FakeClassifier
from helpers.scripted_llm import PLANNER, RETRIEVAL, ScriptedLLM, plan_response, text, tool_call

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
"""


@pytest.fixture
def registre(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    joblib.dump(FakeClassifier(), tmp_path / "titanic.joblib")
    return Registry.load(tmp_path / "registry.yaml")


def csv(tmp_path: Path, nom: str) -> FileSource:
    chemin = tmp_path / f"{nom}.csv"
    chemin.write_text("a,b\n1,2\n", encoding="utf-8")
    return FileSource(name=nom, path=chemin, description=f"La source {nom}.")


@pytest.fixture
def deux_sources(tmp_path: Path) -> Catalog:
    return Catalog(sources=[csv(tmp_path, "ventes"), csv(tmp_path, "clients")])


def orchestrateur(llm: ScriptedLLM, catalogue: Catalog, registre: Registry) -> Orchestrator:
    return Orchestrator(
        model=llm.model(),
        catalog=catalogue,
        registry=registre,
        settings=Settings(_env_file=None),
    )


def une_requete(source: str | None = None) -> ScriptedLLM:
    """Un planificateur qui classe en `query`, et un agent SQL qui répond."""
    return (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source=source))])
        .script(
            RETRIEVAL,
            [tool_call("run_sql", {"query": "SELECT count(*) AS n FROM t"}), text("Deux lignes.")],
        )
    )


# --- tour 1 : la proposition ---------------------------------------------------


def test_le_premier_tour_propose_les_sources_et_attend_une_reponse(
    deux_sources: Catalog, registre: Registry
):
    """Deux sources déclarées, aucune désignée : on propose, avec ce que le
    catalogue dit de chacune — pas deux noms nus."""
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "combien de lignes ?", source_de_travail=SourceDeTravail()
    )

    assert "ventes" in reponse.answer
    assert "La source clients." in reponse.answer  # la description déclarée
    assert reponse.answer.strip().endswith("?")
    # une réponse est attendue au tour suivant, et le fil s'en souvient
    assert reponse.source_de_travail == SourceDeTravail(nom="", a_valider=True)


def test_une_source_unique_est_annoncee_au_lieu_d_etre_demandee(tmp_path: Path, registre: Registry):
    """« S'il n'y en a qu'une, il l'annonce au lieu de poser une question inutile. »

    Elle était déjà choisie en silence par ``_resolve_source`` ; ce qui change,
    c'est que l'utilisateur l'apprend, et que le fil la retient.
    """
    catalogue = Catalog(sources=[csv(tmp_path, "ventes")])

    reponse = orchestrateur(une_requete(), catalogue, registre).ask(
        "combien de lignes ?", source_de_travail=SourceDeTravail()
    )

    assert reponse.answer.startswith("Je travaille sur la source `ventes`.")
    assert "Deux lignes." in reponse.answer  # et la question est répondue au passage
    assert reponse.source_de_travail == SourceDeTravail(nom="ventes")


def test_une_question_qui_ne_demande_aucune_source_ne_declenche_rien(
    deux_sources: Catalog, registre: Registry
):
    """« Prédis pour une passagère de 1re classe » n'interroge aucune source :
    lui proposer un catalogue serait un tour perdu. C'est la raison pour
    laquelle la proposition n'arrive qu'après le plan, quand on sait qu'une
    source est réellement nécessaire."""
    llm = ScriptedLLM().script(
        PLANNER,
        [plan_response(Plan(capability="predict", dataset="titanic", features={"a": 1}))],
    )

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "prédis pour ce cas", source_de_travail=SourceDeTravail()
    )

    assert "Sur laquelle veux-tu travailler" not in reponse.answer
    assert reponse.source_de_travail == SourceDeTravail(nom="")


# --- tour 2 : la validation ----------------------------------------------------


def test_le_nom_donne_en_reponse_lie_la_source_sans_appel_llm(
    deux_sources: Catalog, registre: Registry
):
    """La reconnaissance est du code : un nom du catalogue cité dans le message.

    Aucune réponse n'est scriptée — si un agent était appelé, la doublure
    lèverait faute de script. C'est la preuve, pas l'illustration.
    """
    llm = ScriptedLLM()

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "clients", source_de_travail=SourceDeTravail(nom="", a_valider=True)
    )

    assert reponse.source_de_travail == SourceDeTravail(nom="clients")
    assert "clients" in reponse.answer
    assert "La source clients." in reponse.answer
    assert llm.captured == []  # pas un seul aller-retour, agent système compris
    # Aucun plan : rien n'a été planifié, il n'y avait rien à planifier.
    assert reponse.plan is None
    assert "sans appel LLM" in next(s for s in reponse.trace if s.node == "plan").detail


def test_une_phrase_entiere_vaut_choix_si_elle_nomme_une_source(
    deux_sources: Catalog, registre: Registry
):
    """« va pour ventes, merci » est une réponse : on n'exige pas un nom nu."""
    reponse = orchestrateur(ScriptedLLM(), deux_sources, registre).ask(
        "va pour ventes, merci", source_de_travail=SourceDeTravail(nom="", a_valider=True)
    )

    assert reponse.source_de_travail == SourceDeTravail(nom="ventes")


def test_un_message_qui_ne_choisit_rien_n_est_pas_piege_dans_la_question(
    deux_sources: Catalog, registre: Registry
):
    """La porte de sortie : sans elle, « laisse tomber, autre chose » se ferait
    reposer la même question indéfiniment.

    Le message repart au planificateur comme une question ordinaire — donc il
    peut se voir reproposer les sources, mais parce qu'il en a besoin, pas
    parce qu'une question restait ouverte.
    """
    reponse = orchestrateur(une_requete("ventes"), deux_sources, registre).ask(
        "laisse tomber, combien de lignes en tout ?",
        source_de_travail=SourceDeTravail(nom="", a_valider=True),
    )

    assert "Deux lignes." in reponse.answer
    assert reponse.plan is not None


def test_deux_sources_nommees_dans_le_meme_message_ne_valident_rien(
    deux_sources: Catalog, registre: Registry
):
    """En choisir une serait deviner."""
    reponse = orchestrateur(une_requete("ventes"), deux_sources, registre).ask(
        "ventes ou clients ?", source_de_travail=SourceDeTravail(nom="", a_valider=True)
    )

    assert reponse.plan is not None  # traité comme une question, pas comme un choix


def test_la_question_en_suspens_ne_survit_pas_au_tour(deux_sources: Catalog, registre: Registry):
    """``a_valider`` vaut un tour, et un seul : un utilisateur qui part sur autre
    chose ne doit pas rester dans un état d'attente indéfini."""
    reponse = orchestrateur(une_requete("ventes"), deux_sources, registre).ask(
        "combien de lignes de ventes ?",
        source_de_travail=SourceDeTravail(nom="", a_valider=True),
    )

    assert reponse.source_de_travail is not None
    assert reponse.source_de_travail.a_valider is False


# --- tour 3 et suivants : la source est portée par la conversation -------------


def test_le_planificateur_recoit_la_source_liee_et_n_a_plus_a_la_deviner(
    deux_sources: Catalog, registre: Registry
):
    llm = une_requete(source=None)  # le modèle ne choisit rien

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "combien de lignes ?", source_de_travail=SourceDeTravail(nom="clients")
    )

    assert "cette conversation travaille sur la source 'clients'" in llm.systems_for(PLANNER)[0]
    # et quoi qu'il en fasse, elle est reposée : pas de proposition, pas de devinette
    assert reponse.plan.source == "clients"
    assert "Sur laquelle veux-tu travailler" not in reponse.answer


def test_la_source_liee_n_est_pas_reannoncee_a_chaque_tour(
    deux_sources: Catalog, registre: Registry
):
    """Répéter « je travaille sur clients » à chaque réponse serait du bruit."""
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "combien de lignes ?", source_de_travail=SourceDeTravail(nom="clients")
    )

    assert reponse.answer == "Deux lignes."


def test_une_supposition_du_planificateur_ne_fait_pas_basculer_la_source(
    deux_sources: Catalog, registre: Registry
):
    """LA propriété de sûreté du mécanisme.

    Le planificateur choisit une source à chaque tour, souvent au hasard des
    descriptions du catalogue. Si sa supposition suffisait à faire basculer la
    conversation, une question sans rapport ferait changer de données sans que
    personne l'ait demandé. La désignation est donc lue dans le TEXTE de
    l'utilisateur, pas dans le plan.
    """
    reponse = orchestrateur(une_requete(source="ventes"), deux_sources, registre).ask(
        "combien de lignes ?", source_de_travail=SourceDeTravail(nom="clients")
    )

    assert reponse.plan.source == "clients"
    assert reponse.source_de_travail == SourceDeTravail(nom="clients")


def test_une_source_nommee_par_l_utilisateur_fait_basculer_et_le_dit(
    deux_sources: Catalog, registre: Registry
):
    """Bascule plutôt que refus ou question, et c'est un arbitrage.

    Refuser aurait obligé à ouvrir un fil pour une question d'une ligne ;
    demander confirmation aurait dépensé un tour pour une intention déjà écrite
    noir sur blanc. Ce qui est dangereux n'est pas de changer de source, c'est
    de changer **sans le dire** — d'où l'avis, en tête de la réponse.
    """
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "et dans ventes, combien de lignes ?", source_de_travail=SourceDeTravail(nom="clients")
    )

    assert reponse.answer.startswith("Je passe sur la source `ventes` — on travaillait sur")
    assert "clients" in reponse.answer.splitlines()[0]
    assert reponse.plan.source == "ventes"
    assert reponse.source_de_travail == SourceDeTravail(nom="ventes")


def test_une_source_imposee_par_l_appelant_ne_lie_rien(deux_sources: Catalog, registre: Registry):
    """``ask(source=…)`` est un paramètre d'API pour un tour, pas le choix de
    l'utilisateur : il prime sur le plan, il ne touche pas au fil."""
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "combien de lignes ?",
        source="ventes",
        source_de_travail=SourceDeTravail(nom="clients"),
    )

    assert reponse.plan.source == "ventes"
    assert reponse.source_de_travail == SourceDeTravail(nom="clients")


def test_un_tableau_memorise_ne_remplace_pas_la_source_de_travail(
    tmp_path: Path, deux_sources: Catalog, registre: Registry
):
    """Un tableau intermédiaire est interrogeable, ce n'est pas une source de
    données : le retenir remplacerait la source de travail par un résultat de
    requête."""
    from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "un tour précédent")
    llm = une_requete(source="resultat_1")

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "combien de lignes dans ce tableau ?",
        conversation_id="fil",
        workspace_root=tmp_path,
        source_de_travail=SourceDeTravail(nom="clients"),
    )

    assert reponse.source_de_travail == SourceDeTravail(nom="clients")


# --- hors conversation, et compatibilité --------------------------------------


def test_un_appel_direct_sans_conversation_ne_lie_ni_ne_propose(
    deux_sources: Catalog, registre: Registry
):
    """``ask()`` sans fil (la batterie de mesure, un script) : rien à porter.

    C'est ce qui garde le comportement d'avant pour tous les appelants qui ne
    mènent pas de conversation.
    """
    reponse = orchestrateur(une_requete("ventes"), deux_sources, registre).ask(
        "combien de lignes de ventes ?"
    )

    assert reponse.source_de_travail is None
    assert reponse.answer == "Deux lignes."


def test_une_transcription_ecrite_avant_le_champ_reste_lisible(tmp_path: Path):
    """La compatibilité, prouvée sur le disque et non sur un objet en mémoire.

    Aucune migration n'est nécessaire : le champ absent vaut son défaut, donc
    « aucune source liée », donc le comportement d'avant.
    """
    magasin = ConversationStore(tmp_path, "alice")
    dossier = magasin.dir_of("ancien")
    dossier.mkdir(parents=True)
    (dossier / "transcript.json").write_text(
        '{"id": "ancien", "owner": "alice", "title": "Un vieux fil", "messages": []}',
        encoding="utf-8",
    )

    fil = magasin.load("ancien")

    assert fil is not None
    assert fil.source_de_travail == SourceDeTravail()


def test_le_magasin_persiste_la_source_et_la_copie_la_suit(tmp_path: Path):
    """Elle se persiste avec le fil comme ``owner``, et une duplication la
    reprend — la copie doit être reprenable comme l'originale."""
    magasin = ConversationStore(tmp_path, "alice")
    magasin.create("fil")

    magasin.record_turn(
        "fil", "clients", "entendu", source_de_travail=SourceDeTravail(nom="clients")
    )
    assert magasin.load("fil").source_de_travail == SourceDeTravail(nom="clients")

    # un tour qui ne se prononce pas ne défait pas la liaison
    magasin.record_turn("fil", "combien ?", "deux", source_de_travail=None)
    assert magasin.load("fil").source_de_travail == SourceDeTravail(nom="clients")

    copie = magasin.duplicate("fil")
    assert copie.source_de_travail == SourceDeTravail(nom="clients")


def test_le_client_ne_choisit_pas_la_source_liee():
    """Comme ``owner`` et ``pending`` : elle vient du disque, jamais du corps de
    la requête. Aucun champ de ``ChatRequest`` ne la porte."""
    from data_analyst_agent.api.app import ChatRequest

    assert "source_de_travail" not in ChatRequest.model_fields


def test_un_fil_neuf_part_sans_source_liee():
    assert Conversation(id="x").source_de_travail == SourceDeTravail()
