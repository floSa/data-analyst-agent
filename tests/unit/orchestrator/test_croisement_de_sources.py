"""Le croisement de deux sources déclarées : ce qui l'ouvre, et ce qui le refuse.

`Plan.source` porte UN nom. Le planificateur, lui, écrit `source='ventes,
production'` quand la question en croise deux — les deux noms empaquetés dans un
champ qui en attend un. L'information ne manquait donc pas : le code la jetait.

Ces tests tiennent les deux bords à la fois, et le second compte autant que le
premier : une question qui croise doit ouvrir un périmètre, et une question qui
NOMME deux sources sans rien demander dessus doit continuer de faire choisir.
"""

from pathlib import Path

import pytest
from test_regles_de_plan import UN_MODELE_YAML, contexte, registre, source

from data_analyst_agent.agents.inference.registry import Registry  # noqa: F401
from data_analyst_agent.agents.retrieval.catalog import Catalog, DuckDBSource, FileSource
from data_analyst_agent.agents.retrieval.croisement import (
    dictionnaire_du_croisement,
    ouvrir_le_croisement,
)
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from data_analyst_agent.orchestrator.systeme import (
    PLANCHER_DES_SOURCES_NOMMEES,
    ResultatSysteme,
)
from helpers.scripted_llm import PLANNER, ScriptedLLM, plan_response, text

CROISEMENT = "compare les quantités produites et les quantités vendues par produit"


@pytest.fixture
def orchestrateur(tmp_path: Path) -> Orchestrator:
    """Un orchestrateur nu : les règles n'appellent jamais le LLM."""
    return Orchestrator(
        model=ScriptedLLM().model(),
        catalog=Catalog(sources=[]),
        registry=registre(tmp_path / "registre_1", UN_MODELE_YAML),
        settings=Settings(_env_file=None),
    )


def deux(tmp_path: Path) -> list:
    return [source("ventes", tmp_path), source("production", tmp_path)]


# --- ce qui ouvre un périmètre ---------------------------------------------------


def test_deux_noms_empaquetes_et_une_vraie_demande_ouvrent_un_perimetre(
    orchestrateur, tmp_path: Path
):
    """Le cas du relevé : le plan empaquette, le message demande."""
    plan = Plan(capability="query", source="ventes, production")

    croise = orchestrateur._perimetre_croise(
        plan, contexte(declare=deux(tmp_path), question=CROISEMENT)
    )

    assert [s.name for s in croise] == ["ventes", "production"]


def test_la_regle_reecrit_les_noms_canoniques(orchestrateur, tmp_path: Path):
    """« Production, ventes » devient « ventes, production » : la suite compare des chaînes."""
    plan = Plan(capability="query", source="Production, VENTES")

    assert (
        orchestrateur._regle_croiser_les_sources(
            plan, contexte(declare=deux(tmp_path), question=CROISEMENT)
        )
        is None
    )
    assert plan.source == "ventes, production"


def test_la_source_du_fil_n_ecrase_pas_un_croisement(orchestrateur, tmp_path: Path):
    """Le périmètre S'AJOUTE au fil, il ne s'y réduit pas.

    Sans cette garde, un fil lié à `ventes` verrait sa source reposée sur un
    tour qui croise `ventes` et `production` : l'agent répondrait sur la moitié
    de la question, et ne le dirait pas.
    """
    plan = Plan(capability="query", source="ventes, production")

    orchestrateur._regle_source_de_la_conversation(
        plan,
        contexte(declare=deux(tmp_path), question=CROISEMENT, source_de_travail="ventes"),
    )

    assert plan.source == "ventes, production"


def test_un_croisement_ne_se_fait_pas_demander_de_choisir(orchestrateur, tmp_path: Path):
    """La normalisation laisse passer : elle trancherait un champ voulu multiple."""
    plan = Plan(capability="query", source="ventes, production")

    assert (
        orchestrateur._regle_normaliser_le_nom_de_source(
            plan, contexte(declare=deux(tmp_path), question=CROISEMENT)
        )
        is None
    )


# --- ce qui le refuse : LES TÉMOINS ----------------------------------------------


def test_deux_sources_nommees_sans_rien_demander_ne_croisent_pas(orchestrateur, tmp_path: Path):
    """« ventes ou production ? » hésite, il ne demande rien : on fait choisir.

    C'est le témoin, et il tient par CONSTRUCTION et non par chance : le
    décompte des mots hors des noms est celui de ``choix_de_source``, mesuré sur
    ces formulations-là.
    """
    plan = Plan(capability="query", source="ventes, production")

    croise = orchestrateur._perimetre_croise(
        plan, contexte(declare=deux(tmp_path), question="ventes ou production ?")
    )

    assert croise == []


def test_une_seule_source_designee_n_ouvre_aucun_perimetre(orchestrateur, tmp_path: Path):
    """Le témoin qui compte le plus : rien ne doit changer pour une question mono-source."""
    plan = Plan(capability="query", source="ventes")

    assert (
        orchestrateur._perimetre_croise(
            plan, contexte(declare=deux(tmp_path), question="quel chiffre d'affaires en 2025 ?")
        )
        == []
    )


def test_une_prediction_n_ouvre_aucun_perimetre(orchestrateur, tmp_path: Path):
    """Seules les capacités qui LISENT une source peuvent en croiser deux."""
    plan = Plan(capability="predict", source="ventes, production")

    assert (
        orchestrateur._perimetre_croise(plan, contexte(declare=deux(tmp_path), question=CROISEMENT))
        == []
    )


# --- le budget du dictionnaire ---------------------------------------------------


def test_le_budget_du_dictionnaire_suit_le_nombre_de_sources(orchestrateur, tmp_path: Path):
    """Deux dictionnaires sous le budget d'un seul, c'est une section de pièges coupée.

    ``preparer`` garde les sections dans l'ordre jusqu'au budget : ce qui saute
    est la fin du document, c'est-à-dire précisément les pièges. Le croisement
    rendrait alors des chiffres faux en silence.
    """
    plan = Plan(capability="query", source="ventes, production")
    un = orchestrateur.settings.dictionary_max_chars

    orchestrateur.catalog = Catalog(sources=deux(tmp_path))
    assert orchestrateur._budget_du_perimetre(plan).dictionary_max_chars == 2 * un
    assert (
        orchestrateur._budget_du_perimetre(
            Plan(capability="query", source="ventes")
        ).dictionary_max_chars
        == un
    )


# --- l'adaptateur ----------------------------------------------------------------


def catalogue_reel(tmp_path: Path) -> list:
    un = tmp_path / "un.csv"
    un.write_text("code_produit,vendu\nVEL-01,3\n", encoding="utf-8")
    deux_ = tmp_path / "deux.csv"
    deux_.write_text("code_produit,fabrique\nVEL-01,9\n", encoding="utf-8")
    return [FileSource(name="ventes", path=un), FileSource(name="production", path=deux_)]


def test_les_tables_portent_le_nom_de_leur_source(tmp_path: Path):
    """Le préfixe est ce qui rend le croisement lisible ET sans collision."""
    croisement = ouvrir_le_croisement(catalogue_reel(tmp_path), max_rows=100)

    try:
        assert set(croisement.adapter.schema().table_names()) == {"ventes_un", "production_deux"}
    finally:
        croisement.close()


def test_une_jointure_traverse_les_deux_sources(tmp_path: Path):
    croisement = ouvrir_le_croisement(catalogue_reel(tmp_path), max_rows=100)

    try:
        resultat = croisement.adapter.run(
            "SELECT v.code_produit, v.vendu, p.fabrique FROM ventes_un v "
            "JOIN production_deux p ON p.code_produit = v.code_produit"
        )
    finally:
        croisement.close()

    assert resultat.rows == [["VEL-01", 3, 9]]


def test_le_verrou_d_acces_externe_tient_sur_un_croisement(tmp_path: Path):
    """Le chargement a besoin du disque ; le SQL du modèle ne l'a plus.

    Sans ce verrou, ``SELECT * FROM read_csv_auto('/etc/passwd')`` remonte à
    l'utilisateur comme un tableau ordinaire — la requête *est* un SELECT.
    """
    croisement = ouvrir_le_croisement(catalogue_reel(tmp_path), max_rows=100)

    try:
        with pytest.raises(Exception, match=r"disabled|Permission"):
            croisement.adapter.run("SELECT * FROM read_csv_auto('/etc/passwd')")
    finally:
        croisement.close()


def test_une_table_amputee_est_nommee(tmp_path: Path):
    """Une jointure sur une table coupée rend un résultat lisible où rien ne manque."""
    croisement = ouvrir_le_croisement(catalogue_reel(tmp_path), max_rows=0)

    try:
        assert set(croisement.tronquees) == {"ventes_un", "production_deux"}
    finally:
        croisement.close()


def test_un_croisement_exige_au_moins_deux_sources(tmp_path: Path):
    with pytest.raises(ValueError, match="au moins deux"):
        ouvrir_le_croisement(catalogue_reel(tmp_path)[:1], max_rows=100)


# --- le dictionnaire du périmètre ------------------------------------------------


def test_les_deux_dictionnaires_sont_servis_avec_leur_prefixe(tmp_path: Path):
    """Un dictionnaire qui nomme des tables inexistantes se fait ignorer.

    Il parle de `produits` ; dans un croisement la table s'appelle
    `ventes_produits`. Le titre porte le préfixe, sinon la règle est là et ne
    s'applique pas — au moment précis où elle porte le piège.
    """
    sources = catalogue_reel(tmp_path)
    for s, texte in zip(
        sources, ("les annulées sont exclues", "la sentinelle vaut -1"), strict=True
    ):
        chemin = tmp_path / f"{s.name}.md"
        chemin.write_text(texte, encoding="utf-8")
        s.dictionary = chemin

    texte = dictionnaire_du_croisement(sources)

    assert "les annulées sont exclues" in texte
    assert "la sentinelle vaut -1" in texte
    assert "préfixées `ventes_`" in texte
    assert "préfixées `production_`" in texte


def test_sans_aucun_dictionnaire_le_prompt_est_celui_d_avant(tmp_path: Path):
    assert dictionnaire_du_croisement(catalogue_reel(tmp_path)) is None


def test_une_base_duckdb_entre_dans_un_croisement(tmp_path: Path):
    """Les trois types de source se croisent : c'est l'adaptateur natif qui lit."""
    import duckdb

    chemin = tmp_path / "prod.duckdb"
    with duckdb.connect(str(chemin)) as connexion:
        connexion.execute("CREATE TABLE ordres (code_produit VARCHAR, quantite INTEGER)")
        connexion.execute("INSERT INTO ordres VALUES ('VEL-01', 9)")
    sources = [catalogue_reel(tmp_path)[0], DuckDBSource(name="production", path=chemin)]

    croisement = ouvrir_le_croisement(sources, max_rows=100)

    try:
        assert set(croisement.adapter.schema().table_names()) == {"ventes_un", "production_ordres"}
    finally:
        croisement.close()


# --- les clés étrangères, qui décident des jointures ------------------------------


def test_le_croisement_porte_les_cles_etrangeres_prefixees(tmp_path: Path):
    """Sans elles, le modèle devine la jointure — et une jointure devinée ne lève rien.

    `CREATE TABLE ... AS SELECT` ne recopie aucune contrainte : le croisement
    arrivait au modèle sans une seule clé étrangère, y compris celles INTERNES à
    chaque source, qu'il avait gratuitement avant. Mesuré : un produit cartésien
    (« 20 557 vendues, 180 669 produites » au lieu de 1 828 et 4 413), une
    jointure sur `produit_id` — clé interne à `ventes` — et un « quantité
    produite : 0 pour chaque produit ». Aucune n'a levé d'erreur.
    """
    import duckdb

    chemin = tmp_path / "prod.duckdb"
    with duckdb.connect(str(chemin)) as connexion:
        connexion.execute("CREATE TABLE machines (machine_id INTEGER PRIMARY KEY, code VARCHAR)")
        connexion.execute(
            "CREATE TABLE ordres (of_id INTEGER PRIMARY KEY, machine_id INTEGER, "
            "FOREIGN KEY (machine_id) REFERENCES machines(machine_id))"
        )
    sources = [catalogue_reel(tmp_path)[0], DuckDBSource(name="production", path=chemin)]

    croisement = ouvrir_le_croisement(sources, max_rows=100)

    try:
        rendu = croisement.adapter.schema().to_prompt()
    finally:
        croisement.close()

    # La référence porte le préfixe : sans lui elle désigne une table absente.
    assert "REFERENCES production_machines(machine_id)" in rendu
    assert "REFERENCES machines(machine_id)" not in rendu.replace("production_machines", "X")


def test_une_cle_vers_une_table_hors_de_la_source_n_est_pas_prefixee(tmp_path: Path):
    """On ne préfixe que ce qu'on a renommé : le reste serait une table inventée."""
    from data_analyst_agent.agents.retrieval.croisement import prefixer
    from data_analyst_agent.agents.retrieval.sql import ForeignKeyInfo, SchemaInfo, TableInfo

    schema = SchemaInfo(
        dialect="duckdb",
        tables=[
            TableInfo(
                name="lignes",
                foreign_keys=[ForeignKeyInfo(column="x", ref_table="ailleurs", ref_column="x")],
            )
        ],
    )

    (table,) = prefixer(schema, "ventes")

    assert table.name == "ventes_lignes"
    assert table.foreign_keys[0].ref_table == "ailleurs"


# --- le CHAMP de périmètre, et l'union des deux désignations ----------------------


def test_le_champ_de_perimetre_seul_ouvre_un_croisement(orchestrateur, tmp_path: Path):
    """`sources=['ventes','production']` suffit : le plan désigne explicitement.

    C'est la forme que le planificateur rend le plus souvent une fois le champ
    au contrat — 3 tirages sur 3 sur `ca-produit-vs-fabrique` et
    `vel04-production-ventes`, là où l'empaquetage de `source` n'en portait
    qu'un seul nom.
    """
    plan = Plan(capability="query", sources=["ventes", "production"])

    croise = orchestrateur._perimetre_croise(
        plan, contexte(declare=deux(tmp_path), question=CROISEMENT)
    )

    assert [s.name for s in croise] == ["ventes", "production"]


def test_l_union_des_deux_champs_ouvre_ce_qu_aucun_n_ouvrait_seul(orchestrateur, tmp_path: Path):
    """Un nom dans chaque champ : le planificateur en a lu deux, et on les prend.

    Le cas qui justifie l'union à lui seul. Mesuré sur `produites-vs-vendues`,
    3 tirages sur 3 : `source='production'` et `sources=['ventes']`. Ni l'un ni
    l'autre ne porte deux noms ; ensemble ils en portent deux, et la question
    demandait bien les deux.
    """
    plan = Plan(capability="query", source="production", sources=["ventes"])

    croise = orchestrateur._perimetre_croise(
        plan, contexte(declare=deux(tmp_path), question=CROISEMENT)
    )

    assert [s.name for s in croise] == ["ventes", "production"]


def test_le_meme_nom_dans_les_deux_champs_n_ouvre_rien(orchestrateur, tmp_path: Path):
    """Un périmètre se compte en SOURCES, pas en désignations.

    Sans cette propriété, un plan qui répète sa source — ce que le modèle fait —
    monterait un « croisement » d'une source avec elle-même, et paierait une
    matérialisation pour rien.
    """
    plan = Plan(capability="query", source="ventes", sources=["ventes"])

    assert (
        orchestrateur._perimetre_croise(plan, contexte(declare=deux(tmp_path), question=CROISEMENT))
        == []
    )


def test_un_plan_sans_aucune_designation_n_ouvre_rien(orchestrateur, tmp_path: Path):
    """`source` vide et `sources` vide : il n'y a rien à croiser, et rien à lire."""
    assert (
        orchestrateur._perimetre_croise(
            plan := Plan(capability="query"), contexte(declare=deux(tmp_path), question=CROISEMENT)
        )
        == []
    )
    assert plan.sources == []


def test_le_temoin_tient_aussi_quand_le_champ_est_rempli(orchestrateur, tmp_path: Path):
    """« ventes ou production ? » : même si le plan désignait un périmètre, on fait choisir.

    Le second garde-fou est sur le MESSAGE, et il ne dépend pas de la façon dont
    le planificateur a désigné. Le mesurer avec le champ REMPLI est ce qui le
    prouve : le témoin ne tient pas parce que le modèle s'est abstenu, il tient
    parce que le message n'a rien demandé.
    """
    plan = Plan(capability="query", sources=["ventes", "production"])

    assert (
        orchestrateur._perimetre_croise(
            plan, contexte(declare=deux(tmp_path), question="ventes ou production ?")
        )
        == []
    )


# --- le PLANCHER cède au périmètre : ce qui le fait céder, et ce qui le retient ----


def _orchestrateur_sur(llm: ScriptedLLM, tmp_path: Path) -> Orchestrator:
    """Un orchestrateur dont le catalogue porte `ventes` et `production`."""
    return Orchestrator(
        model=llm.model(),
        catalog=Catalog(sources=deux(tmp_path)),
        registry=registre(tmp_path / "registre_p", UN_MODELE_YAML),
        settings=Settings(_env_file=None),
    )


def _retenu_par_le_plancher() -> ResultatSysteme:
    """Ce que rend l'agent système quand SEUL le plancher a retenu le tour."""
    return ResultatSysteme(
        reponse="AUTRE",
        faits="les deux fiches",
        faits_a_enumerer="",
        outils_appeles=(PLANCHER_DES_SOURCES_NOMMEES,),
    )


def _etat(question: str, **extra) -> dict:
    return {"question": question, "workspace": None, **extra}


def test_le_plancher_cede_quand_le_plan_designe_un_perimetre(tmp_path: Path):
    """« compare la production et les ventes du VEL-04 » : le plan désigne, le plancher rend.

    Le verrou nommé par C54. Le message nomme deux sources et dit plus que leurs
    noms : le plancher le retient, et sert deux FICHES à qui demandait deux
    CHIFFRES. Rien dans le message ne le distingue de « quelle est la différence
    entre iris et titanic ? » — c'est ce que le plan en fait qui les sépare.
    """
    llm = ScriptedLLM().script(
        PLANNER, [plan_response(Plan(capability="query", sources=["production", "ventes"]))]
    )
    orchestrateur = _orchestrateur_sur(llm, tmp_path)

    cede = orchestrateur._le_plancher_cede_au_perimetre(
        _etat("compare la production et les ventes du VEL-04"), _retenu_par_le_plancher(), 0.0
    )

    assert cede is not None
    assert cede["plan_davance"].sources == ["production", "ventes"]
    assert "le plancher cède" in cede["trace"][0].detail


def test_le_plancher_sert_quand_le_plan_ne_designe_aucun_perimetre(tmp_path: Path):
    """« titanic et iris, c'est quoi au juste ? » : le plan reste vide, les fiches partent.

    L'autre bord, et c'est lui qui rend la cession sans risque. Mesuré sur le
    planificateur, 3 tirages sur 3 : les deux questions de fiches qui passent par
    ce plancher laissent `sources` VIDE et ne nomment aucune source dans
    `source`. Le plancher garde donc le tour, exactement comme avant.
    """
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source=""))])
    orchestrateur = _orchestrateur_sur(llm, tmp_path)

    cede = orchestrateur._le_plancher_cede_au_perimetre(
        _etat("ventes et production, c'est quoi au juste ?"), _retenu_par_le_plancher(), 0.0
    )

    assert cede is None


def test_un_tour_retenu_par_un_vrai_outil_ne_paie_aucun_plan(tmp_path: Path):
    """Le modèle a appelé un outil : c'est LUI qui a jugé, et on ne le contredit pas.

    Six des huit messages de `sources-nommees` mesurés le 2026-09-22 sont dans ce
    cas, « Qu'est-ce que t'appelles source vente, production, stock ? » compris.
    Aucun n'atteint ce chemin, et aucun ne paie l'appel au planificateur : le
    script du planificateur reste intact, et c'est ce qui le prouve.
    """
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", sources=["x"]))])
    orchestrateur = _orchestrateur_sur(llm, tmp_path)
    retenu_par_le_modele = ResultatSysteme(
        reponse="…", faits="…", faits_a_enumerer="", outils_appeles=("chercher_une_source",)
    )

    cede = orchestrateur._le_plancher_cede_au_perimetre(
        _etat("ventes et production, c'est quoi ?"), retenu_par_le_modele, 0.0
    )

    assert cede is None
    assert llm.prompts_for(PLANNER) == []


def test_une_source_imposee_ferme_la_cession(tmp_path: Path):
    """`source=` est un paramètre d'API : quelqu'un a tranché, on ne défait pas.

    Ouvrir un périmètre de deux sources contre une source imposée serait
    l'élargir en silence — et c'est le seul endroit où l'élargissement se
    déciderait sans que personne le demande.
    """
    llm = ScriptedLLM().script(
        PLANNER, [plan_response(Plan(capability="query", sources=["ventes", "production"]))]
    )
    orchestrateur = _orchestrateur_sur(llm, tmp_path)

    cede = orchestrateur._le_plancher_cede_au_perimetre(
        _etat("compare la production et les ventes", source_name="ventes"),
        _retenu_par_le_plancher(),
        0.0,
    )

    assert cede is None
    assert llm.prompts_for(PLANNER) == []


def test_un_plan_que_le_modele_rate_laisse_le_plancher_servir(tmp_path: Path):
    """Sortie structurée manquée : le repli est déjà prêt, et il est juste.

    Le nœud système est fail-open pour ce que le MODÈLE rate ; cette cession-ci
    l'est aussi, et dans l'autre sens — un incident ne doit pas faire perdre les
    fiches qu'on avait sous la main.
    """
    # Trois refus : `pydantic-ai` réessaie avant d'abandonner, et un script
    # trop court ferait échouer le test sur sa propre longueur.
    llm = ScriptedLLM().script(PLANNER, [text("pas un plan")] * 3)
    orchestrateur = _orchestrateur_sur(llm, tmp_path)

    cede = orchestrateur._le_plancher_cede_au_perimetre(
        _etat("compare la production et les ventes du VEL-04"), _retenu_par_le_plancher(), 0.0
    )

    assert cede is None


def test_le_noeud_du_plan_reprend_le_plan_davance_sans_le_redemander(tmp_path: Path):
    """Le tour qui cède coûte ce qu'il aurait coûté sans ce plancher, et pas un appel de plus.

    C'est la troisième raison pour laquelle la cession reste bon marché : le
    plan obtenu pour décider n'est pas jeté. Le redemander coûterait un second
    appel LLM sur la même question — et rien ne garantit la même réponse, donc
    rien ne garantirait qu'on croise ce pour quoi on a cédé.
    """
    llm = ScriptedLLM().script(PLANNER, [plan_response(Plan(capability="query", source="ventes"))])
    orchestrateur = _orchestrateur_sur(llm, tmp_path)
    davance = Plan(capability="query", sources=["ventes", "production"])

    rendu = orchestrateur._plan_node(
        _etat(CROISEMENT, plan_davance=davance, source_in=None, source_name=None)
    )

    assert rendu["plan"] is davance
    assert rendu["plan"].source == "ventes, production"  # la règle a canonisé le périmètre
    assert llm.prompts_for(PLANNER) == []  # aucun appel : le plan était déjà là
