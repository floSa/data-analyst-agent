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
from data_analyst_agent.orchestrator.systeme import SystemeDeps
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


# --- l'outil appelé SANS nom : il rend l'inventaire, il n'abdique pas ----------


def test_l_outil_appele_sans_nom_rend_l_inventaire_et_ne_lie_rien(
    deux_sources: Catalog, registre: Registry
):
    """« Tu bosses sur quoi ? » — et l'outil qui ne peut pas remplir son argument.

    Le défaut mesuré le 2026-09-16, deux campagnes identiques. Cet outil porte
    le verbe « travailler sur une source » ; c'est ce verbe qui lui fait
    attraper « on se met sur X », « passe-moi la main sur X », « reprendre mon
    travail sur X » — dix ouvertures sur dix. C'est aussi ce verbe qui lui fait
    attraper « tu bosses sur quoi ? », qui n'écrit aucun nom. Le modèle rangeait
    la question sous l'outil, ne pouvait pas remplir `source`, et rendait le
    TOUR : ``AUTRE``, aucun outil appelé, retour au planificateur, qui classait
    `query` et demandait de choisir une source.

    La réparation n'est pas lexicale. Débarrasser le nom et la fiche du verbe
    répare ce tour-là et fait retomber les ouvertures de 10 sur 10 à 4 sur 10 :
    le verbe est ce qui donne à l'outil sa portée. Un outil qui ne peut pas
    remplir son argument doit avoir quelque chose à RENDRE, sinon c'est le tour
    que le modèle rend. Et l'inventaire n'est pas un pis-aller : « sur quoi
    travailles-tu ? » le demande.

    Rien n'est lié — l'utilisateur n'a nommé personne, et c'est la règle de tout
    ce module.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="tu bosses sur quoi ?",
    )

    rendu = deps.retenir_la_liaison("")

    assert deps.source_a_lier == ""
    assert deps.outils_appeles == [OUTIL]
    assert "ventes" in rendu
    assert "stocks" in rendu


def test_un_nom_inconnu_reste_distinct_d_un_nom_absent(deux_sources: Catalog, registre: Registry):
    """Deux situations, deux réponses — et surtout, deux messages.

    Un nom ABSENT est une question sur le catalogue : on répond par
    l'inventaire. Un nom INCONNU est une erreur du modèle : on le lui dit, et on
    lui montre les vrais noms. Les confondre rendrait « Source `` inconnue » à
    quelqu'un qui n'a rien écrit de faux.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="mets-toi sur comptabilite",
    )

    rendu = deps.retenir_la_liaison("comptabilite")

    assert deps.source_a_lier == ""
    assert "inconnue" in rendu
    assert "`ventes`" in rendu
    assert "`stocks`" in rendu


# --- décrire UNE source, quand la question ne porte que sur elle --------------


def test_une_question_sur_une_seule_source_ne_deballe_pas_le_catalogue(
    deux_sources: Catalog, registre: Registry
):
    """« De quoi parle interventions et de quel type est-elle ? » vise UNE source.

    L'outil n'avait pas d'argument : il rendait le catalogue entier, et le
    modèle recopiait les cinq fiches — volumes et périodes compris — pour une
    question qui en visait une. Mesuré sur le catalogue de démonstration, au
    deuxième tour d'une conversation d'ouverture : personne ne lit cinq
    paragraphes pour savoir qu'un CSV porte la main courante de la maintenance.

    Même famille que `get_schema` sans argument : le modèle demande le détail
    d'une chose, l'outil n'a que le tout à rendre.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="de quoi parle la source stocks et de quel type est-elle ?",
    )

    rendu = deps.decrire_les_sources("stocks")

    assert "stocks" in rendu
    assert "L'état des stocks." in rendu
    assert "ventes" not in rendu  # l'autre source n'a rien à faire là
    assert deps.outils_appeles == ["sources_de_donnees"]


def test_sans_cible_l_outil_rend_le_catalogue_entier(deux_sources: Catalog, registre: Registry):
    """« Quelles sources as-tu ? » les demande TOUTES : c'est le cas nominal."""
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="quelles sources as-tu ?",
    )

    rendu = deps.decrire_les_sources()

    assert "ventes" in rendu
    assert "stocks" in rendu


def test_une_cible_inconnue_rend_le_catalogue_plutot_qu_une_erreur(
    deux_sources: Catalog, registre: Registry
):
    """Celui qui se trompe de nom a besoin de voir les vrais, pas d'un refus.

    Même choix que `_schema_lisible` côté récupération : partout où l'on doute,
    on rend le tout. Un refus coûterait un tour à quelqu'un qui n'a rien fait de
    mal — le modèle a mal recopié un nom, l'utilisateur, lui, attend une réponse.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="de quoi parle comptabilite ?",
    )

    rendu = deps.decrire_les_sources("comptabilite")

    assert "ventes" in rendu
    assert "stocks" in rendu


def test_sans_cible_mais_avec_une_source_liee_c_est_d_elle_qu_on_parle(
    deux_sources: Catalog, registre: Registry
):
    """« et elle contient quoi ? » ne nomme personne — le sujet est la source liée.

    Rendre le catalogue entier à cette question-là, c'est répondre à quelqu'un
    d'autre : l'utilisateur vient de lier une source et poursuit dessus. Mesuré
    au troisième tour d'une conversation d'ouverture, sur le catalogue de
    démonstration. Même règle que `schema_d_une_source`, qui la tenait déjà.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="et elle contient quoi ?",
        source_de_travail="stocks",
    )

    rendu = deps.decrire_les_sources()

    assert "stocks" in rendu
    assert "ventes" not in rendu


def test_un_nom_ecrit_comme_un_mot_francais_est_reconnu(deux_sources: Catalog, registre: Registry):
    """« C'est quoi la source Télémétrie ? » — accents et majuscules compris.

    L'utilisateur écrit le nom d'une source comme un mot de sa langue ; le
    catalogue l'écrit comme un identifiant. Comparer les deux tels quels exige
    de lui qu'il tape comme un fichier de configuration. Mesuré : `télémétrie`
    ne trouvait pas `telemetrie`, l'outil repliait sur les cinq fiches — et la
    source, elle, SE LIAIT, parce que ce chemin-là repliait déjà les accents.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="c'est quoi la source Ventes ?",
    )

    rendu = deps.decrire_les_sources("Vêntes")

    assert "ventes" in rendu
    assert "stocks" not in rendu


def test_chercher_par_sujet_rend_de_la_matiere_a_choisir(deux_sources: Catalog, registre: Registry):
    """« As-tu une source qui parle de maintenance ? » se répond par UNE source.

    Les faits d'une recherche ne sont pas une liste à réciter : la ceinture
    interdit toujours d'inventer un nom, elle n'exige plus qu'on les redise
    tous. Sans ça — mesuré le 2026-09-16 — le modèle répondait juste (« la
    source `interventions` »), `defaut_de_fondation` criait « fait(s) omis :
    les quatre autres » et servait les 2 200 caractères du catalogue entier :
    quatre questions différentes recevaient le même pavé.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="tu as une source qui parle de ce qu'on a vendu ?",
    )

    rendu = deps.decrire_les_sources(a_enumerer=False)

    # la matière, elle, est entière : c'est l'obligation de la RÉCITER qui tombe
    assert "ventes" in rendu
    assert "stocks" in rendu
    assert deps.faits == [rendu]
    assert deps.faits_a_enumerer == []  # rien à réciter


def test_l_inventaire_reste_a_reciter_en_entier(deux_sources: Catalog, registre: Registry):
    """« Quelles sources as-tu ? » : une liste incomplète est une réponse fausse.

    C'est le pendant du test précédent, et il tient la frontière entre les deux.
    Mêler la recherche et l'inventaire dans un seul outil a un coût mesuré :
    deux questions de la surface conversationnelle sont passées de justes à
    vagues, deux campagnes sur deux, parce que la ceinture ne réclamait plus
    rien dès que le modèle disait chercher.
    """
    deps = SystemeDeps(
        catalogue_declare=deux_sources,
        catalogue_effectif=deux_sources,
        registre=registre,
        question="quelles sources as-tu ?",
    )

    rendu = deps.decrire_les_sources()

    assert deps.faits_a_enumerer == [rendu]
