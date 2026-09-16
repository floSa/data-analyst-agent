"""Lier une source par une PHRASE — le second chemin, et ses deux verrous.

Le court-circuit déterministe de ``_choix_de_source`` ne reconnaît qu'un message
réduit au nom d'une source (« telemetrie », « facturation, vas-y »). Une phrase
polie qui demande la même chose — « J'aimerais reprendre le travail sur la
source exploitation, peux-tu la charger ? » — partait à la récupération, qui
répondait « Je n'ai pas interrogé la source pour cette question… Reformule ».

Le second chemin est un OUTIL de l'agent système, sur le modèle des cinq autres :
le modèle décide d'appeler, l'outil rend l'accueil déterministe de la source, et
le nœud lie. Ce qui est vérifié ici n'est donc pas une liste de tournures — il
n'y en a pas — mais les deux vérifications qui interdisent à ce chemin de faire
basculer une conversation à l'insu de qui la mène :

- la source liée est celle que l'**utilisateur** a nommée, jamais celle que le
  modèle a passée à l'outil ;
- hors conversation, rien ne se lie.

Et le cas du nom inconnu, qui ne lève pas : il rend la liste des vraies sources,
comme ``get_schema`` rend le schéma complet sur une table inventée.
"""

from pathlib import Path

import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from helpers.scripted_llm import PLANNER, SYSTEME, ScriptedLLM, text, tool_call

OUTIL = "travailler_sur_une_source"


@pytest.fixture
def registre(tmp_path: Path) -> Registry:
    """Un registre VIDE : aucune prédiction n'entre dans ce sujet."""
    (tmp_path / "registry.yaml").write_text("models: []\n", encoding="utf-8")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def deux_sources(tmp_path: Path) -> Catalog:
    """Deux sources, parce qu'une seule rendrait tout choix évident.

    Un catalogue à une source lie par repli (``_regle_source_de_la_conversation``)
    et masquerait ce qu'on mesure : c'est le nom écrit par l'utilisateur qui
    décide, pas l'unicité du catalogue.
    """
    ventes = tmp_path / "ventes.csv"
    ventes.write_text("mois,total\n01,10\n02,20\n", encoding="utf-8")
    stocks = tmp_path / "stocks.csv"
    stocks.write_text("article,quantite\na,3\nb,4\n", encoding="utf-8")
    return Catalog(
        sources=[
            FileSource(name="ventes", path=ventes, description="Les ventes du mois."),
            FileSource(name="stocks", path=stocks, description="L'état des stocks."),
        ]
    )


def orchestrateur(llm: ScriptedLLM, catalogue: Catalog, registre) -> Orchestrator:
    return Orchestrator(
        model=llm.model(),
        settings=Settings(_env_file=None),
        catalog=catalogue,
        registry=registre,
    )


def modele_qui_lie(source: str, reponse: str = "Entendu.") -> ScriptedLLM:
    """Un modèle qui appelle l'outil de liaison, puis formule.

    Sa formulation est volontairement pauvre : elle ne doit PAS être servie.
    L'accueil d'une source est déterministe — il accuse réception d'un nom et y
    ajoute le volume lu — et le vérifier ici garde cette propriété explicite.
    """
    return ScriptedLLM().script(SYSTEME, [tool_call(OUTIL, {"source": source}), text(reponse)])


# --- le chemin principal ------------------------------------------------------


def test_une_phrase_polie_lie_la_source_et_rend_son_accueil(deux_sources: Catalog, registre):
    """Le défaut réparé : la phrase lie, et l'accueil porte le volume lu."""
    llm = modele_qui_lie("ventes")

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "J'aimerais reprendre le travail sur la source ventes, peux-tu la charger ?",
        conversation_id="fil",
        source_de_travail="",
    )

    assert reponse.error is None
    assert reponse.source_de_travail == "ventes"
    assert "on travaille sur **ventes**" in reponse.answer
    assert "Les ventes du mois." in reponse.answer
    # Le VOLUME, lu dans la source : c'est ce que l'agent système rendait sans
    # jamais le compter, et c'est ce qui manquait à la réponse.
    assert "1 table(s), 2 ligne(s)" in reponse.answer
    # Pas de planificateur : le tour s'arrête au nœud système.
    assert [s.node for s in reponse.trace] == ["system", "synthesize"]
    assert llm.prompts_for(PLANNER) == []
    assert "source liée : ventes" in next(s for s in reponse.trace if s.node == "system").detail


def test_la_bascule_dit_la_source_quittee(deux_sources: Catalog, registre):
    """Ce qui est dangereux n'est pas de changer de source, c'est de le faire en silence."""
    llm = modele_qui_lie("stocks")

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "on peut passer sur la source stocks maintenant s'il te plaît ?",
        conversation_id="fil",
        source_de_travail="ventes",
    )

    assert reponse.source_de_travail == "stocks"
    assert "on travaillait sur `ventes`" in reponse.answer


# --- les deux verrous ---------------------------------------------------------


def test_la_source_liee_est_celle_que_l_utilisateur_a_nommee(deux_sources: Catalog, registre):
    """Le modèle propose `stocks`, l'utilisateur a écrit `ventes` : rien ne bascule.

    Le verrou qui compte. Le modèle passe un argument à chaque appel, parfois au
    hasard des descriptions ; laisser cet argument lier la conversation ferait
    changer de source à l'insu de qui la mène — et sur deux sources qui
    partagent une colonne, la réponse suivante serait juste sur les mauvaises
    données.
    """
    llm = ScriptedLLM().script(
        SYSTEME, [tool_call(OUTIL, {"source": "stocks"}), text("Je passe sur stocks.")]
    )

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "on se met sur la source ventes pour la suite ?",
        conversation_id="fil",
        source_de_travail="",
    )

    # La liaison est refusée : le tour retombe sur le comportement ordinaire du
    # nœud système, qui sert les faits rendus par l'outil.
    assert reponse.source_de_travail != "stocks"
    assert "on travaille sur **stocks**" not in reponse.answer


def test_hors_conversation_rien_ne_se_lie(deux_sources: Catalog, registre):
    """Sans fil, il n'y a rien à lier : le tour se comporte comme avant ce chemin."""
    llm = modele_qui_lie("ventes")

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "on se met sur la source ventes pour la suite ?"
    )

    assert reponse.source_de_travail is None
    assert "Je garde cette source pour la suite" not in reponse.answer


def test_un_nom_inconnu_rend_les_vrais_noms_au_lieu_de_lever(deux_sources: Catalog, registre):
    """Le modèle qui invente un nom a besoin de voir les vrais, pas d'un refus.

    Même boucle que ``get_schema`` sur une table inventée : l'outil compte comme
    appelé, rien n'est lié, et les faits rendus partent à l'utilisateur.
    """
    llm = ScriptedLLM().script(
        SYSTEME, [tool_call(OUTIL, {"source": "achats"}), text("Voici ce que j'ai.")]
    )

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "on se met sur la source achats ?", conversation_id="fil", source_de_travail=""
    )

    assert reponse.error is None
    assert not reponse.source_de_travail
    assert "ventes" in reponse.answer
    assert "stocks" in reponse.answer


def test_le_court_circuit_deterministe_passe_toujours_devant(deux_sources: Catalog, registre):
    """Un message réduit au nom d'une source ne paie AUCUN aller-retour.

    C'est la propriété qu'on ajoute un second chemin pour ne pas perdre : elle
    est gratuite, elle est juste, et elle ne passe même pas par le nœud système
    (``_tour_deja_engage``). Un script vide suffit à le prouver — s'il était
    appelé, le test tomberait sur « aucun script ».
    """
    llm = ScriptedLLM()

    reponse = orchestrateur(llm, deux_sources, registre).ask(
        "ventes", conversation_id="fil", source_de_travail=""
    )

    assert reponse.source_de_travail == "ventes"
    assert "on travaille sur **ventes**" in reponse.answer
    assert llm.prompts_for(SYSTEME) == []
    assert llm.prompts_for(PLANNER) == []
