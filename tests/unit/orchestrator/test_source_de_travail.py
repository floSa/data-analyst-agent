"""La source de travail d'une conversation : proposée, validée, portée par le fil.

Le parcours demandé tient en trois tours, et chacun a son test ici : l'agent
**propose** ses sources, l'utilisateur **en valide une**, et c'est celle sur
laquelle on travaille **ensuite** — sans que le planificateur ait à la redeviner.

Trois propriétés valent d'être dites, parce qu'elles sont ce qui rend le
mécanisme sûr plutôt que seulement pratique :

- **la reconnaissance du choix est déterministe** (le nom d'une source du
  catalogue, et le fait que le message ne dise presque rien d'autre) : aucun
  aller-retour LLM pour comparer deux chaînes de caractères ;
- **elle ne dépend d'aucun état** — ni d'un drapeau posé au tour d'avant, ni de
  l'ordre des tours. C'est une correction : le parcours mesuré de bout en bout
  a vu un « titanic » de validation recevoir l'inventaire du catalogue, parce
  que le tour précédent n'avait pas posé le drapeau attendu ;
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
from data_analyst_agent.orchestrator.graph import Orchestrator
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
    """Un planificateur qui classe en `query`, et un agent SQL qui répond.

    Le plan est scripté DEUX fois, et la seconde n'est consommée que par les
    tours qui allaient servir l'inventaire : depuis C63, un plan qui demande des
    données sans désigner de source est reposé une fois au planificateur
    (``_relire_faute_de_source_designee``). La seconde réponse est la même que
    la première — elle ne désigne toujours rien —, donc rien n'est retenu et le
    tour se déroule comme avant, à un appel près. C'est exactement ce que ces
    tests doivent continuer de voir : l'inventaire, proposé.
    """
    return (
        ScriptedLLM()
        .script(
            PLANNER,
            [
                plan_response(Plan(capability="query", source=source)),
                plan_response(Plan(capability="query", source=source)),
            ],
        )
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
        "combien de lignes ?", source_de_travail=""
    )

    assert "ventes" in reponse.answer
    assert "La source clients." in reponse.answer  # la description déclarée
    assert reponse.answer.strip().endswith("?")
    # rien n'est lié : le tour suivant sera lu comme un choix s'il en est un
    assert reponse.source_de_travail == ""


def test_une_source_unique_est_annoncee_au_lieu_d_etre_demandee(tmp_path: Path, registre: Registry):
    """« S'il n'y en a qu'une, il l'annonce au lieu de poser une question inutile. »

    Elle était déjà choisie en silence par ``_resolve_source`` ; ce qui change,
    c'est que l'utilisateur l'apprend, et que le fil la retient.
    """
    catalogue = Catalog(sources=[csv(tmp_path, "ventes")])

    reponse = orchestrateur(une_requete(), catalogue, registre).ask(
        "combien de lignes ?", source_de_travail=""
    )

    assert reponse.answer.startswith("Je travaille sur la source `ventes`.")
    assert "Deux lignes." in reponse.answer  # et la question est répondue au passage
    assert reponse.source_de_travail == "ventes"


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
        "prédis pour ce cas", source_de_travail=""
    )

    assert "Sur laquelle veux-tu travailler" not in reponse.answer
    assert reponse.source_de_travail == ""


# --- tour 2 : la validation ----------------------------------------------------


def test_le_nom_donne_en_reponse_lie_la_source_sans_appel_llm(
    deux_sources: Catalog, registre: Registry
):
    """La reconnaissance est du code : un nom du catalogue, et rien d'autre.

    Aucune réponse n'est scriptée — si un agent était appelé, la doublure
    lèverait faute de script. C'est la preuve, pas l'illustration : ni le
    planificateur ni l'agent système ne voient ce message.
    """
    llm = ScriptedLLM()

    reponse = orchestrateur(llm, deux_sources, registre).ask("clients", source_de_travail="")

    assert reponse.source_de_travail == "clients"
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
        "va pour ventes, merci", source_de_travail=""
    )

    assert reponse.source_de_travail == "ventes"


def test_un_message_qui_ne_choisit_rien_n_est_pas_piege_dans_la_question(
    deux_sources: Catalog, registre: Registry
):
    """La porte de sortie : sans elle, « laisse tomber, autre chose » se ferait
    reposer la même question indéfiniment.

    Le message repart au planificateur comme une question ordinaire : il est
    PLANIFIÉ, pas traité comme une réponse à la question du tour d'avant, et
    rien n'est lié au fil. Comme il ne nomme aucune source et que le fil n'en
    porte aucune, il se voit reproposer l'inventaire — mais parce qu'il en a
    besoin, pas parce qu'une question restait ouverte. Le tour suivant, lui,
    sera un choix s'il en est un.
    """
    reponse = orchestrateur(une_requete("ventes"), deux_sources, registre).ask(
        "laisse tomber, combien de lignes en tout ?",
        source_de_travail="",
    )

    assert reponse.plan is not None  # planifié, pas court-circuité
    assert "Entendu : on travaille sur" not in reponse.answer  # pas un accusé de réception
    assert "Sur laquelle veux-tu travailler" in reponse.answer
    assert reponse.source_de_travail == ""


def test_deux_sources_nommees_dans_le_meme_message_ne_valident_rien(
    deux_sources: Catalog, registre: Registry
):
    """En choisir une serait deviner."""
    reponse = orchestrateur(une_requete("ventes"), deux_sources, registre).ask(
        "ventes ou clients ?", source_de_travail=""
    )

    assert reponse.plan is not None  # traité comme une question, pas comme un choix


# --- tour 3 et suivants : la source est portée par la conversation -------------


def test_le_planificateur_recoit_la_source_liee_et_n_a_plus_a_la_deviner(
    deux_sources: Catalog, registre: Registry
):
    llm = une_requete(source=None)  # le modèle ne choisit rien

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "combien de lignes ?", source_de_travail="clients"
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
        "combien de lignes ?", source_de_travail="clients"
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
        "combien de lignes ?", source_de_travail="clients"
    )

    assert reponse.plan.source == "clients"
    assert reponse.source_de_travail == "clients"


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
        "et dans ventes, combien de lignes ?", source_de_travail="clients"
    )

    assert reponse.answer.startswith("Je passe sur la source `ventes` — on travaillait sur")
    assert "clients" in reponse.answer.splitlines()[0]
    assert reponse.plan.source == "ventes"
    assert reponse.source_de_travail == "ventes"


def test_une_source_imposee_par_l_appelant_ne_lie_rien(deux_sources: Catalog, registre: Registry):
    """``ask(source=…)`` est un paramètre d'API pour un tour, pas le choix de
    l'utilisateur : il prime sur le plan, il ne touche pas au fil."""
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "combien de lignes ?",
        source="ventes",
        source_de_travail="clients",
    )

    assert reponse.plan.source == "ventes"
    assert reponse.source_de_travail == "clients"


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
        source_de_travail="clients",
    )

    assert reponse.source_de_travail == "clients"


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
    assert fil.source_de_travail == ""


def test_le_magasin_persiste_la_source_et_la_copie_la_suit(tmp_path: Path):
    """Elle se persiste avec le fil comme ``owner``, et une duplication la
    reprend — la copie doit être reprenable comme l'originale."""
    magasin = ConversationStore(tmp_path, "alice")
    magasin.create("fil")

    magasin.record_turn("fil", "clients", "entendu", source_de_travail="clients")
    assert magasin.load("fil").source_de_travail == "clients"

    # un tour qui ne se prononce pas ne défait pas la liaison
    magasin.record_turn("fil", "combien ?", "deux", source_de_travail=None)
    assert magasin.load("fil").source_de_travail == "clients"

    copie = magasin.duplicate("fil")
    assert copie.source_de_travail == "clients"


def test_le_client_ne_choisit_pas_la_source_liee():
    """Comme ``owner`` et ``pending`` : elle vient du disque, jamais du corps de
    la requête. Aucun champ de ``ChatRequest`` ne la porte."""
    from data_analyst_agent.api.app import ChatRequest

    assert "source_de_travail" not in ChatRequest.model_fields


def test_un_fil_neuf_part_sans_source_liee():
    assert Conversation(id="x").source_de_travail == ""


# --- les deux défauts trouvés par la mesure de bout en bout --------------------


def test_un_choix_de_source_ne_passe_pas_par_l_agent_systeme(
    deux_sources: Catalog, registre: Registry
):
    """LE défaut du premier parcours mesuré, et sa correction.

    Le tour 1 avait été répondu par l'agent système, qui avait énuméré les
    sources — une bonne réponse — sans qu'aucun drapeau « une proposition
    attend » soit posé. Le « titanic » du tour 2 n'était donc plus reconnu
    comme un choix : il repartait chez l'agent système, qui lui rendait
    l'inventaire du catalogue. L'utilisateur validait, et recevait la question.

    La reconnaissance ne consulte plus aucun état de conversation. Ici encore,
    rien n'est scripté : le moindre appel ferait tomber la doublure.
    """
    llm = ScriptedLLM()

    reponse = orchestrateur(llm, deux_sources, registre).ask("ventes", source_de_travail="")

    assert reponse.source_de_travail == "ventes"
    assert llm.captured == []
    assert "choix de source" in next(s for s in reponse.trace if s.node == "system").detail


def test_un_message_qui_nomme_une_source_ET_pose_une_question_est_repondu(
    deux_sources: Catalog, registre: Registry
):
    """Le second défaut : « et dans iris, combien de lignes ? » perdait sa question.

    Le message nomme une source, mais il ne fait pas que ça. Il est donc traité
    comme la question qu'il est — la source se lie en chemin, et la bascule est
    annoncée en tête au lieu de remplacer la réponse.
    """
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "et dans ventes, combien de lignes en tout ?", source_de_travail="clients"
    )

    assert "Deux lignes." in reponse.answer  # la question a bien été répondue
    assert reponse.answer.startswith("Je passe sur la source `ventes`")
    assert reponse.source_de_travail == "ventes"


def test_un_choix_qui_en_remplace_un_autre_dit_lequel(deux_sources: Catalog, registre: Registry):
    """Un choix nu dans un fil déjà lié est une bascule : elle se dit aussi."""
    reponse = orchestrateur(ScriptedLLM(), deux_sources, registre).ask(
        "ventes", source_de_travail="clients"
    )

    assert reponse.source_de_travail == "ventes"
    assert "on travaillait sur `clients`" in reponse.answer


def test_une_source_liee_survit_a_un_noeud_qui_echoue(tmp_path: Path, registre: Registry):
    """Un incident ne délie pas ce que l'utilisateur a validé.

    C'est ce que garantit ``_source_retenue`` : une branche du graphe qui ne
    s'est pas prononcée laisse la liaison telle quelle. Sans ça, une source
    validée disparaîtrait au premier fichier introuvable, et l'utilisateur
    devrait la revalider.
    """
    catalogue = Catalog(
        sources=[csv(tmp_path, "clients"), FileSource(name="envolee", path=tmp_path / "absent.csv")]
    )

    reponse = orchestrateur(une_requete(), catalogue, registre).ask(
        "combien de lignes ?", source_de_travail="envolee"
    )

    assert reponse.error is not None
    assert reponse.source_de_travail == "envolee"


# --- ne jamais deviner : l'ordre du YAML ne décide plus de rien ---------------


@pytest.mark.parametrize("devinee", ["ventes", "clients"])
def test_la_supposition_du_planificateur_ne_choisit_pas_une_source(
    deux_sources: Catalog, registre: Registry, devinee: str
):
    """LE défaut mesuré, et sa correction.

    Le planificateur remplit ``source`` presque à chaque tour, au hasard des
    descriptions du catalogue. Tant que cette supposition suffisait, le
    comportement dépendait de **l'ordre de déclaration du YAML** : la source
    écrite en premier était répondue sans rien demander, l'autre non — même
    question, mêmes octets (`tests/catalogues/ambiguite/`).

    Le tour est paramétré sur les deux suppositions possibles : quelle que soit
    celle que le modèle a faite, l'agent propose et attend.
    """
    llm = une_requete(source=devinee)

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "quel est le pourcentage de femmes ?", source_de_travail=""
    )

    assert "Sur laquelle veux-tu travailler" in reponse.answer
    assert reponse.source_de_travail == ""  # rien n'est lié
    assert reponse.plan.source is None  # la supposition est effacée, pas contournée
    # et surtout : aucune requête n'a été lancée sur aucune source
    assert llm.prompts_for(RETRIEVAL) == []


def test_les_deux_ordres_de_declaration_donnent_le_meme_tour(tmp_path: Path, registre: Registry):
    """La propriété, dite comme une égalité plutôt que comme deux comportements.

    Deux catalogues, les mêmes sources, l'ordre inversé. C'est la variable de
    l'expérience, et la seule : ce que l'agent répond ne doit pas en dépendre.
    """
    a, b = csv(tmp_path, "ventes"), csv(tmp_path, "clients")
    reponses = [
        orchestrateur(une_requete(source=ordre.sources[0].name), ordre, registre)
        .ask("combien de lignes ?", source_de_travail="")
        .answer
        for ordre in (Catalog(sources=[a, b]), Catalog(sources=[b, a]))
    ]

    assert all("Sur laquelle veux-tu travailler" in r for r in reponses)


def test_une_source_nommee_dans_le_message_reste_une_designation(
    deux_sources: Catalog, registre: Registry
):
    """Le durcissement ne referme pas la porte que l'utilisateur ouvre lui-même.

    Il n'a pas validé de source, mais il en nomme une dans sa question : c'est
    une désignation, pas une devinette du modèle. On répond, et on lie.
    """
    reponse = orchestrateur(une_requete(source="clients"), deux_sources, registre).ask(
        "combien de lignes dans ventes ?", source_de_travail=""
    )

    assert "Deux lignes." in reponse.answer
    assert reponse.plan.source == "ventes"  # celle qu'il a nommée, pas celle du plan
    assert reponse.source_de_travail == "ventes"


def test_un_tableau_du_fil_n_est_pas_pris_pour_une_source_ambigue(
    tmp_path: Path, deux_sources: Catalog, registre: Registry
):
    """« Combien de lignes dans ce tableau ? » n'a pas à se voir proposer le catalogue.

    Un tableau intermédiaire n'est pas une source déclarée : ce n'est pas un
    choix entre sources, c'est un résultat que la conversation vient de
    produire. La supposition effacée est celle qui porte sur le CATALOGUE.
    """
    from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "un tour précédent")

    reponse = orchestrateur(une_requete(source="resultat_1"), deux_sources, registre).ask(
        "combien de lignes dans ce tableau ?",
        conversation_id="fil",
        workspace_root=tmp_path,
        source_de_travail="",
    )

    assert "Deux lignes." in reponse.answer
    assert reponse.plan.source == "resultat_1"
    assert reponse.source_de_travail == ""  # un tableau ne se lie pas


def test_l_inventaire_propose_porte_ce_qui_est_lu_dans_les_sources(
    deux_sources: Catalog, registre: Registry
):
    """La proposition n'est pas que le YAML recopié : c'est le texte sur lequel
    quelqu'un choisit, et deux descriptions écrites à la main se ressemblent
    toujours plus que deux volumétries."""
    reponse = orchestrateur(une_requete(), deux_sources, registre).ask(
        "combien de lignes ?", source_de_travail=""
    )

    assert "1 table(s), 1 ligne(s)" in reponse.answer  # lu dans le CSV, pas déclaré
    assert "au premier inventaire de la session" in reponse.answer  # et le cache est dit


def test_l_accuse_de_reception_dit_ce_qu_on_a_lu_dans_la_source(
    deux_sources: Catalog, registre: Registry
):
    """Le moment où savoir qu'elle pèse une ligne change ce qu'on va lui demander."""
    reponse = orchestrateur(ScriptedLLM(), deux_sources, registre).ask(
        "clients", source_de_travail=""
    )

    assert "1 table(s), 1 ligne(s) (clients : 1)" in reponse.answer


# --- ce que l'indicateur de la page demande à l'orchestrateur ------------------


def test_l_inventaire_rendu_a_la_page_porte_le_catalogue_et_ses_faits(
    deux_sources: Catalog, registre: Registry
):
    """Ce que ``GET /sources`` sert au menu de la page.

    Les tests d'API le mesurent sur un double ; celui-ci le mesure sur
    l'orchestrateur RÉEL, qui ouvre vraiment les sources — c'est le seul moyen
    de vérifier que les faits affichés dans le menu sont bien ceux du relevé, et
    non une seconde version qui pourrait diverger de l'inventaire proposé en
    conversation.
    """
    inventaire = orchestrateur(ScriptedLLM(), deux_sources, registre).inventaire_des_sources()

    assert [s.name for s in inventaire] == ["ventes", "clients"]
    assert inventaire[0].type == "file"
    assert inventaire[0].description == "La source ventes."
    assert "1 table(s), 1 ligne(s)" in inventaire[0].faits
    assert inventaire[0].lu


def test_une_source_injoignable_reste_dans_l_inventaire_en_le_disant(
    tmp_path: Path, registre: Registry
):
    """Elle doit se voir dans le menu, pas en disparaître : une source absente
    de la liste ressemble à une source non déclarée, ce qui est un tout autre
    problème à aller corriger."""
    catalogue = Catalog(
        sources=[csv(tmp_path, "ventes"), FileSource(name="envolee", path=tmp_path / "absent.csv")]
    )

    inventaire = orchestrateur(ScriptedLLM(), catalogue, registre).inventaire_des_sources()

    assert [s.name for s in inventaire] == ["ventes", "envolee"]
    assert not inventaire[1].lu
    assert "non relevée" in inventaire[1].faits


def test_seule_une_source_declaree_est_reconnue_comme_telle(
    tmp_path: Path, deux_sources: Catalog, registre: Registry
):
    """Le garde-fou du menu : un tableau intermédiaire du fil est interrogeable,
    ce n'est pas une source de données, et le lier remplacerait la source de
    travail par un résultat de requête."""
    from data_analyst_agent.orchestrator.workspace import ConversationWorkspace

    ConversationWorkspace(tmp_path, "fil").save_table(["a"], [[1]], "un tour précédent")
    agent = orchestrateur(ScriptedLLM(), deux_sources, registre)

    assert agent.source_declaree("ventes")
    assert not agent.source_declaree("resultat_1")
    assert not agent.source_declaree("jamais-declaree")
