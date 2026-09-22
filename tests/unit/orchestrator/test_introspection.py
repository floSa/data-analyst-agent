"""Les FAITS du système : ce qu'ils disent, et ce qu'ils refusent de laisser dire.

Deux choses séparées, et testées séparément. Les **textes de faits** sont des
formateurs purs : on les juge sur le fait qu'ils ne disent rien qui ne soit lu
dans un artefact du dépôt. La **ceinture** (``defaut_de_fondation``) juge la
formulation du modèle contre ces faits ; on la juge, elle, sur les deux fautes
qu'elle doit attraper — un nom inventé, un nom oublié — et sur le fait qu'elle
laisse passer une reformulation honnête.

Ce fichier testait aussi une **reconnaissance par lexique** (``sujet_de``),
retirée : le sujet d'une question est désormais reconnu par le modèle, qui
appelle l'outil qui porte les faits. Ce que le lexique garantissait — ne pas
voler une question sur les données — est maintenant l'affaire du prompt de
l'agent système et des témoins de la batterie live, pas d'un test unitaire :
c'est un comportement de modèle, il se mesure, il ne s'assied pas.
"""

from pathlib import Path
from typing import get_args

import pytest

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource, load_catalog
from data_analyst_agent.agents.retrieval.sql import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaInfo,
    TableInfo,
)
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.plan import Capability

REGISTRY_YAML = """
models:
  - dataset: titanic
    task: classification
    model_path: titanic.joblib
    target: survived
    labels:
      "0": "n'a pas survécu"
      "1": "a survécu"
    description: Survie d'un passager du Titanic.
  - dataset: california_housing
    task: regression
    model_path: california.joblib
    target: MedHouseVal
    unit: centaines de milliers de dollars
    description: Prix médian d'un îlot.
"""

# Un modèle au registre SANS schéma de features : il ne peut pas être appelé,
# et la réponse doit le dire au lieu de le lister comme les autres.
REGISTRY_SANS_SCHEMA = """
models:
  - dataset: inconnu_au_bataillon
    task: classification
    model_path: nulle-part.joblib
    target: cible
"""


@pytest.fixture
def registre(tmp_path: Path) -> Registry:
    (tmp_path / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    return Registry.load(tmp_path / "registry.yaml")


@pytest.fixture
def catalogue() -> Catalog:
    return Catalog(
        sources=[
            FileSource(name="titanic", path=Path("titanic.csv"), description="Base Titanic."),
            FileSource(name="iris", path=Path("iris.csv")),
        ]
    )


@pytest.fixture
def ontologie_titanic() -> introspection.Ontologie:
    passengers = TableInfo(
        name="passengers",
        columns=[
            ColumnInfo(name="passenger_id", type="INTEGER", nullable=False),
            ColumnInfo(name="sex", type="TEXT", values=["female", "male"]),
            ColumnInfo(name="class_id", type="INTEGER"),
        ],
        primary_key=["passenger_id"],
        foreign_keys=[
            ForeignKeyInfo(column="class_id", ref_table="classes", ref_column="class_id")
        ],
    )
    classes = TableInfo(
        name="classes",
        columns=[
            ColumnInfo(name="class_id", type="INTEGER"),
            ColumnInfo(name="label", type="TEXT"),
        ],
    )
    return introspection.Ontologie(
        source=FileSource(name="titanic", path=Path("t.csv")),
        schema=SchemaInfo(dialect="postgresql", tables=[classes, passengers]),
    )


# --- ce qui remplace la reconnaissance par lexique ------------------------------


@pytest.mark.parametrize(
    "reponse",
    [
        # les deux noms de sources, tels que les faits les rendent
        "Je vois deux sources : `titanic` et `iris`.",
        # la décoration Markdown du modèle : `titanic` en gras, l'autre en code
        "Mes sources sont **titanic** et `iris`.",
    ],
)
def test_une_formulation_qui_porte_les_faits_est_servie(catalogue: Catalog, reponse: str):
    """Le modèle a le droit de reformuler : c'est tout l'objet du changement."""
    faits = introspection.decrire_les_sources(catalogue)

    assert introspection.defaut_de_fondation(reponse, faits) == ""


def test_un_nom_de_table_absent_du_catalogue_ne_passe_pas(catalogue: Catalog):
    """LE défaut à ne pas laisser revenir par la porte de l'agent système.

    ``acfd8f5`` corrigeait « décris le dataset iris » répondu de mémoire, sans
    regarder la source. Un modèle à qui l'on demande les tables d'une base
    « familière » sait en citer de mémoire — `flights`, `passengers_2`, ce
    qu'on veut. Un nom inventé est plus nocif qu'une réponse absente : il a
    l'air d'une lecture de la source.
    """
    faits = introspection.decrire_les_sources(catalogue)

    defaut = introspection.defaut_de_fondation(
        "Mes sources sont `titanic`, `iris` et `flights`.", faits
    )

    assert "flights" in defaut


def test_un_identifiant_en_serpent_est_repere_meme_hors_accents_graves():
    """Un jeton à blanc souligné n'est jamais de la prose française.

    Le gras, lui, n'est délibérément PAS compté du côté du modèle : il met en
    gras des mots ordinaires, et les prendre pour des noms techniques ferait
    écarter des réponses justes.
    """
    faits = "- `sepal_length`"

    assert introspection.defaut_de_fondation("Il me faut sepal_length.", faits) == ""
    assert "sepal_lenght" in introspection.defaut_de_fondation(
        "Il me faut sepal_length et sepal_lenght.", faits
    )


def test_une_liste_incomplete_n_est_pas_une_reponse(catalogue: Catalog):
    """Défaut mesuré : la version narrée par le modèle avait laissé tomber deux
    colonnes sur dix (mesure du 2026-09-07, §4 de surface-conversationnelle.md)."""
    faits = introspection.decrire_les_sources(catalogue)

    defaut = introspection.defaut_de_fondation("Ma source est `titanic`.", faits)

    assert "iris" in defaut


def test_un_echappement_markdown_ne_compte_pas_pour_un_oubli():
    """Le modèle écrit ``passenger\\_id`` : l'antislash est de l'affichage."""
    assert (
        introspection.defaut_de_fondation("La colonne `passenger\\_id`.", "- `passenger_id`") == ""
    )


@pytest.mark.parametrize(
    ("faits", "reponse"),
    [
        # UN : un nom que les faits portent SANS le décorer. `describe_features`
        # rend ses champs nus (« * sex — Sexe du passager »), et le modèle les
        # écrit entre accents graves : il ne les a pas inventés pour autant.
        ("    * sex — Sexe du passager", "Il me faut `sex`."),
        # DEUX : un nom que les faits portent dans un fragment plus large. La
        # fiche d'une colonne écrit « elle référence `classes(class_id)` » — la
        # portée entière n'est pas un identifiant, `classes` en est un.
        (
            "C'est une **clé étrangère** : elle référence `classes(class_id)`.",
            "Elle pointe vers la table `classes`.",
        ),
        # TROIS : un nom que les faits mettent en EN-TÊTE et non dans une puce.
        # « La table `passengers` de la source `titanic` : » suivie des dix
        # colonnes — une réponse qui liste les dix sans redire `titanic` est
        # complète.
        (
            "La table `passengers` de la source `titanic` :\n\n- **passengers** (1 colonne) :\n"
            "    - `sex` (TEXT)",
            "La table `passengers` a une colonne : `sex`.",
        ),
    ],
)
def test_trois_rejets_mesures_a_tort_ne_le_sont_plus(faits: str, reponse: str):
    """Chacun venait de la mesure live du 2026-09-07, après correction.

    La ceinture est là pour écarter une invention, pas pour rendre le
    déterministe obligatoire. Ces trois-là faisaient servir le gabarit alors
    que la formulation du modèle était juste — et le propriétaire ne veut
    précisément pas du gabarit quand le modèle sait répondre.
    """
    assert introspection.defaut_de_fondation(reponse, faits) == ""


def test_ce_qu_une_puce_dit_de_son_sujet_n_est_pas_exigible():
    """« Quels modèles ? » — une réponse qui nomme les trois modèles sans
    recopier la colonne cible de chacun est une réponse.

    La règle : ce qu'une puce NOMME doit revenir, ce qu'elle en dit est du
    contexte.
    """
    faits = "- **titanic** (classification) — cible `survived` — Survie d'un passager."

    assert introspection.defaut_de_fondation("Je dispose de **titanic**.", faits) == ""
    assert "titanic" in introspection.defaut_de_fondation("Je dispose d'un modèle.", faits)


def test_un_inventaire_enumere_en_ligne_est_exigible(catalogue: Catalog, registre: Registry):
    """Le défaut du 2026-09-14, et la mesure qui l'a montré.

    « Sur quoi peux-tu travailler ? » : le modèle appelle bien
    ``capacites_de_l_agent``, dont le texte finit par « Mes sources :
    `titanic`, `iris`. Mes modèles de prédiction : … » — une ligne ordinaire,
    sans puce. Il en rendait le résumé sans les noms, et la ceinture ne voyait
    rien à redire puisqu'aucune PUCE ne les portait. Le trou tenait à la
    ceinture, pas au modèle : un résumé concis des faits suffit à le découvrir.
    """
    faits = introspection.decrire_les_capacites(catalogue, registre)
    # la réponse réellement mesurée, mot pour mot
    resumee = (
        "Je peux interroger des sources en SQL, analyser et visualiser des données avec "
        "du code Python, prédire des cas ou des individus en utilisant des modèles, et "
        "répondre sur moi-même concernant mes sources, leurs structures, mes modèles et "
        "leurs exigences."
    )

    defaut = introspection.defaut_de_fondation(resumee, faits)

    assert "titanic" in defaut
    assert "iris" in defaut
    # et une formulation qui porte l'inventaire reste servie : la ceinture
    # écarte une omission, elle ne rend pas le gabarit obligatoire. Les cinq
    # actions y sont, chacune dans ses mots — c'est ce que `_actions` exige, et
    # rien de plus : ni l'ordre, ni la ponctuation, ni la phrase des faits.
    assert (
        introspection.defaut_de_fondation(
            "Je sais interroger, analyser et prédire, et je réponds sur moi-même. "
            "Mes sources : `titanic`, `iris` ; "
            "mes modèles : `titanic`, `california_housing`.",
            faits,
        )
        == ""
    )


def test_une_capacite_tue_ne_passe_pas_parce_qu_elle_n_a_pas_de_nom(
    catalogue: Catalog, registre: Registry
):
    """Le trou par lequel « je peux te demander quoi ? » est tombé.

    ``_enumeres`` n'exige d'une puce que les IDENTIFIANTS qu'elle porte, et une
    puce de capacité n'en porte aucun : « - **analyser et visualiser** — du code
    Python… » ne nomme rien au sens technique. La famille tenait quand même,
    mais par ACCIDENT — le modèle qui résumait les capacités laissait aussi
    tomber l'inventaire de la dernière ligne, et se faisait prendre là-dessus.

    Le 2026-09-16, sa formulation a cité `titanic`, `iris` et
    `california_housing` tout en oubliant qu'il savait ANALYSER. L'inventaire
    étant sauf, plus rien ne l'arrêtait. La réponse ci-dessous est celle qui a
    été mesurée, mot pour mot, deux campagnes de suite.
    """
    faits = introspection.decrire_les_capacites(catalogue, registre)
    inventaire_sauf_actions_tues = (
        "Je peux te demander des informations sur mes capacités, mes sources de "
        "données (`titanic`, `iris`), mes modèles de prédiction "
        "(`california_housing`, `titanic`), ainsi que les détails de ces éléments "
        "(schémas, dictionnaires, attributs attendus)."
    )

    defaut = introspection.defaut_de_fondation(inventaire_sauf_actions_tues, faits)

    assert defaut.startswith("action(s) omise(s)")
    assert "analys" in defaut
    assert "interrog" in defaut


def test_une_action_compte_par_son_radical_et_non_par_sa_conjugaison(
    catalogue: Catalog, registre: Registry
):
    """« Je fais des prédictions » DIT qu'on sait prédire.

    La ceinture exige que l'action soit dite, pas qu'elle soit recopiée : une
    réponse a le droit de nominaliser (« une analyse », « des prédictions ») ou
    de conjuguer (« j'interroge »). Sans cette tolérance, elle rejetterait des
    réponses justes et servirait le gabarit à leur place — le travers même que
    ``_enumeres`` a déjà eu à corriger.
    """
    faits = introspection.decrire_les_capacites(catalogue, registre)

    conjuguee = (
        "J'interroge les sources en SQL, je fais des analyses et des figures, "
        "je produis des prédictions, et je réponds sur moi-même. "
        "Mes sources : `titanic`, `iris`. Mes modèles : `california_housing`, `titanic`."
    )

    assert introspection.defaut_de_fondation(conjuguee, faits) == ""


def test_les_faits_sans_puce_de_capacite_n_exigent_aucune_action(
    catalogue: Catalog, registre: Registry
):
    """La nouvelle exigence ne déborde pas sur les autres textes de faits.

    Les puces de l'inventaire, du registre et d'un schéma portent toutes un
    identifiant décoré — c'est ``_enumeres`` qui s'en occupe, et ``_actions``
    n'y voit rien à réclamer. Une exigence d'action sur ces textes-là voudrait
    dire que « titanic » est un verbe.
    """
    for faits in (
        introspection.decrire_les_sources(catalogue),
        introspection.decrire_les_modeles(registre),
    ):
        assert introspection._actions(faits) == []


def test_un_deux_points_en_fin_de_ligne_n_enumere_rien():
    """Il introduit ce qui suit ; c'est la puce suivante qui nomme.

    Sans cette borne, l'énumération en ligne aurait ressuscité le rejet mesuré
    à tort du 2026-09-07 : exiger `titanic` d'une réponse qui listait
    correctement les colonnes de `passengers`.
    """
    faits = (
        "La table `passengers` de la source `titanic` :\n\n"
        "- **passengers** (1 colonne) :\n    - `sex` (TEXT)"
    )

    reponse = "La table `passengers` a une colonne : `sex`."

    assert introspection.defaut_de_fondation(reponse, faits) == ""


def test_une_citation_du_dictionnaire_n_enumere_rien():
    """Le seul texte de faits que ce module n'écrit pas : sa ponctuation
    n'engage personne, et un `>` n'est pas une déclaration de nos artefacts.

    La réponse attribue, parce que les faits attribuent : c'est l'autre règle
    (``_attribution_qui_ne_colle_pas``), et ce test-ci ne porte pas sur elle.
    """
    faits = "Ce qu'en dit le dictionnaire de `titanic` :\n> tarif : en `livres` sterling"

    reponse = "Selon le dictionnaire, le tarif est en livres."
    assert introspection.defaut_de_fondation(reponse, faits) == ""


# --- l'attribution : elle suit les faits, dans les deux sens -------------------


def test_une_provenance_tue_ne_se_sert_pas():
    """Les faits citent le dictionnaire, la réponse le passe sous silence.

    Mesuré 3 fois sur 3 sur « le statut RET, il recouvre quoi au juste ? » : le
    bon fait, sans dire d'où il vient. C'est ce qui sépare une lecture du
    référentiel de quelqu'un d'un savoir général sur les codes d'état.
    """
    faits = "Ce qu'en dit le dictionnaire de `referentiel` :\n> `RET` : station démontée"

    defaut = introspection.defaut_de_fondation("`RET` veut dire démontée.", faits)

    assert defaut == "provenance tue : les faits citent le dictionnaire, la réponse non"


def test_une_provenance_INVENTEE_ne_se_sert_pas():
    """L'autre sens, et c'est le pire des deux : la source n'a pas de dictionnaire.

    Une attribution inventée donne l'autorité de la base à un savoir général.
    Le témoin sans dictionnaire n'était tenu que par une campagne de mesure ;
    il l'est désormais par du code.
    """
    faits = "Dans la table `passengers`, la colonne `sex` est de type TEXT."

    defaut = introspection.defaut_de_fondation(
        "Selon le dictionnaire de la source, `sex` est le sexe du passager.", faits
    )

    assert defaut == "provenance inventée : aucun fait ne cite de dictionnaire"


def test_une_reponse_vide_ou_hors_sujet_ne_se_sert_pas():
    assert introspection.defaut_de_fondation("   ", "- `titanic`") == "réponse vide"
    assert introspection.defaut_de_fondation("AUTRE", "- `titanic`") == "réponse hors sujet"


# --- ce qu'on cherche à qualifier ----------------------------------------------


def test_la_source_nommee_est_retenue(catalogue: Catalog):
    assert introspection.source_visee("les tables de titanic", catalogue).name == "titanic"


def test_deux_sources_nommees_ne_designent_rien(catalogue: Catalog):
    """En choisir une serait deviner."""
    assert introspection.source_visee("titanic ou iris ?", catalogue) is None


def test_l_unique_source_est_retenue_sans_etre_nommee():
    """La question ne peut désigner qu'elle : exiger son nom serait vide de sens."""
    seule = Catalog(sources=[FileSource(name="mini", path=Path("mini.csv"))])
    assert introspection.source_visee("quelles colonnes ?", seule).name == "mini"


def test_le_dataset_nomme_et_l_unique_dataset(registre: Registry, tmp_path: Path):
    assert introspection.dataset_vise("prédire titanic", registre) == "titanic"
    assert introspection.dataset_vise("de quoi as-tu besoin ?", registre) is None
    (tmp_path / "un.yaml").write_text(REGISTRY_SANS_SCHEMA, encoding="utf-8")
    seul = Registry.load(tmp_path / "un.yaml")
    assert introspection.dataset_vise("de quoi as-tu besoin ?", seul) == "inconnu_au_bataillon"


# --- un objet du fil peut-il répondre ? ----------------------------------------


def test_une_colonne_que_l_objet_porte_ne_le_disqualifie_pas():
    """« leur âge » sur un tableau qui a la colonne `age` : rien ne sort de l'objet."""
    assert (
        introspection.colonne_hors_de_l_objet(
            "fais-moi un graphique de leur âge", ["age", "sex", "fare"], ["age", "sex"]
        )
        == ""
    )


def test_une_colonne_reclamee_et_absente_de_l_objet_le_disqualifie():
    """Le cas du fil F : cinq techniciens réclamés à un tableau qui n'en porte pas."""
    assert (
        introspection.colonne_hors_de_l_objet(
            "les 5 techniciens qui sont intervenus le plus souvent",
            ["nature", "technicien", "duree_indispo_min"],
            ["station_libelle", "nature", "duree_indispo_min"],
        )
        == "technicien"
    )


def test_une_question_qui_ne_nomme_aucune_colonne_ne_disqualifie_rien():
    """Rien ne dit qu'elle sort de l'objet : on ne l'invente pas."""
    assert (
        introspection.colonne_hors_de_l_objet(
            "reprends ça et mets-le en camembert", ["nature", "technicien"], ["nature"]
        )
        == ""
    )


def test_le_nombre_grammatical_ne_decide_pas():
    """La question écrit « natures », le schéma écrit `nature` — et l'inverse."""
    assert introspection.colonne_hors_de_l_objet("par natures", ["nature"], ["nature"]) == ""
    assert introspection.colonne_hors_de_l_objet("par nature", ["natures"], ["natures"]) == ""


# --- les réponses : rien qui ne soit lu dans un artefact -----------------------


def test_les_sources_sont_celles_du_catalogue(catalogue: Catalog):
    reponse = introspection.decrire_les_sources(catalogue)
    assert "titanic" in reponse
    assert "iris" in reponse
    assert "Base Titanic." in reponse  # la description déclarée, pas une paraphrase
    assert "sans description" in reponse  # iris n'en déclare pas : on le dit


def test_la_proposition_rend_le_meme_inventaire_mais_finit_par_la_question(catalogue: Catalog):
    """Même matière, autre acte : décrire, ou demander de choisir.

    Elle finit par la question, comme le repli du planificateur et pour la même
    raison : ce qu'on lit en dernier est ce à quoi on répond.
    """
    proposition = introspection.proposer_les_sources(catalogue)

    assert "titanic" in proposition
    assert "Base Titanic." in proposition
    assert proposition.strip().endswith("?")


def test_un_catalogue_vide_se_dit_vide():
    """Y compris quand on allait proposer de choisir : il n'y a rien à choisir."""
    assert "aucune source" in introspection.decrire_les_sources(Catalog(sources=[]))
    assert "aucune source" in introspection.proposer_les_sources(Catalog(sources=[]))


def test_les_modeles_sont_ceux_du_registre(registre: Registry):
    reponse = introspection.decrire_les_modeles(registre)
    assert "titanic" in reponse
    assert "california_housing" in reponse
    assert "classification" in reponse
    assert "régression" in reponse
    assert "survived" in reponse  # la cible déclarée
    assert "centaines de milliers de dollars" in reponse  # l'unité déclarée
    assert "a survécu" in reponse  # les libellés de classes déclarés


def test_un_modele_sans_schema_de_features_est_signale(tmp_path: Path):
    """Il ne peut PAS être appelé : le dire vaut mieux que le lister comme les autres."""
    (tmp_path / "r.yaml").write_text(REGISTRY_SANS_SCHEMA, encoding="utf-8")
    reponse = introspection.decrire_les_modeles(Registry.load(tmp_path / "r.yaml"))
    assert "aucun schéma de features" in reponse


def test_un_registre_vide_se_dit_vide(tmp_path: Path):
    (tmp_path / "vide.yaml").write_text("models: []\n", encoding="utf-8")
    vide = Registry.load(tmp_path / "vide.yaml")
    assert "aucun modèle" in introspection.decrire_les_modeles(vide)
    assert "aucun modèle" in introspection.decrire_les_features(vide, None)


def test_les_features_d_un_modele_designe_viennent_du_schema(registre: Registry):
    """Le sens de chaque champ ET ses valeurs autorisées — ce que porte le schéma."""
    reponse = introspection.decrire_les_features(registre, "titanic")
    assert "embarked" in reponse
    assert "Southampton" in reponse  # la description du champ
    assert "'S'" in reponse  # les valeurs autorisées du Literal


def test_sans_modele_designe_chaque_modele_est_decrit(registre: Registry):
    """Une RÉPONSE, et non la question « sur quel modèle veux-tu prédire ? »."""
    reponse = introspection.decrire_les_features(registre, None)
    assert "titanic" in reponse
    assert "california_housing" in reponse
    assert "`fare`" in reponse
    assert "`med_inc`" in reponse
    assert "?" not in reponse.split("\n")[0]  # la première ligne n'est pas une question


def test_un_modele_designe_sans_schema_retombe_sur_le_tour_d_horizon(tmp_path: Path):
    (tmp_path / "r.yaml").write_text(REGISTRY_SANS_SCHEMA, encoding="utf-8")
    registre = Registry.load(tmp_path / "r.yaml")
    reponse = introspection.decrire_les_features(registre, "inconnu_au_bataillon")
    assert "aucun schéma de features" in reponse


def test_les_capacites_suivent_le_code(catalogue: Catalog, registre: Registry):
    reponse = introspection.decrire_les_capacites(catalogue, registre)
    assert "SQL" in reponse
    assert "bac à sable" in reponse
    assert "titanic" in reponse
    assert "california_housing" in reponse


def test_chaque_capacite_est_decrite():
    """Une capacité ajoutée sans description doit se voir au rouge, pas en production."""
    assert set(introspection.CAPACITES_EN_CLAIR) == set(get_args(Capability))


def test_repondre_sur_soi_est_annonce_sans_etre_une_capacite_du_llm():
    """Elle est routée par du code, pas choisie par le modèle (cf. plan.py) —
    ce qui ne l'empêche pas d'être quelque chose que l'agent sait faire, et
    donc de figurer dans la réponse à « que sais-tu faire ? »."""
    assert "describe_system" not in introspection.CAPACITES_EN_CLAIR
    assert "répondre sur moi-même" in introspection.CAPACITE_SUR_SOI


def test_les_capacites_tiennent_avec_un_inventaire_vide(tmp_path: Path):
    (tmp_path / "vide.yaml").write_text("models: []\n", encoding="utf-8")
    reponse = introspection.decrire_les_capacites(
        Catalog(sources=[]), Registry.load(tmp_path / "vide.yaml")
    )
    assert "(aucune)" in reponse
    assert "(aucun)" in reponse


# --- le schéma, à quatre niveaux de détail -------------------------------------


def test_une_source_designee_rend_toutes_ses_tables(ontologie_titanic):
    reponse = introspection.decrire_le_schema("la structure de titanic", [ontologie_titanic])
    assert "passengers" in reponse
    assert "classes" in reponse
    assert "`sex` (TEXT)" in reponse
    assert "'female'" in reponse  # les valeurs à faible cardinalité


def test_une_table_designee_ne_rend_qu_elle(ontologie_titanic):
    reponse = introspection.decrire_le_schema(
        "quelles colonnes dans la table passengers ?", [ontologie_titanic]
    )
    assert "passenger_id" in reponse
    assert "clé primaire" in reponse
    assert "**classes**" not in reponse  # l'autre table n'a pas été dépliée


def test_une_colonne_designee_rend_la_cle_etrangere(ontologie_titanic):
    """Le nom seul n'apprend rien ; la table qu'il référence, tout."""
    reponse = introspection.decrire_le_schema(
        "que signifie la colonne class_id de la table passengers ?", [ontologie_titanic]
    )
    assert "clé étrangère" in reponse
    assert "classes(class_id)" in reponse


def test_une_colonne_designee_rend_sa_clef_primaire_et_ses_valeurs(ontologie_titanic):
    """La fiche d'une colonne dit tout ce que le schéma sait d'elle.

    Trois faits que le DDL porte et que la question réclame : clé primaire,
    obligation, et — pour une colonne à faible cardinalité — les valeurs
    réellement présentes. Sans elles, on ne sait pas filtrer dessus.
    """
    identifiant = introspection.decrire_le_schema(
        "que signifie la colonne passenger_id ?", [ontologie_titanic]
    )
    assert "clé primaire" in identifiant
    assert "obligatoire (NOT NULL)" in identifiant

    sexe = introspection.decrire_le_schema("que signifie la colonne sex ?", [ontologie_titanic])
    assert "Valeurs présentes : 'female', 'male'." in sexe


def test_la_table_est_trouvee_sans_que_la_source_soit_nommee(ontologie_titanic):
    """« quelles colonnes dans passengers ? » ne nomme aucune source et n'est pas ambiguë."""
    autre = introspection.Ontologie(
        source=FileSource(name="iris", path=Path("i.csv")),
        schema=SchemaInfo(tables=[TableInfo(name="iris", columns=[])]),
    )
    reponse = introspection.decrire_le_schema(
        "les colonnes de passengers", [autre, ontologie_titanic]
    )
    assert "passenger_id" in reponse


def test_rien_de_decidable_rend_le_tour_d_horizon(ontologie_titanic):
    """Déplier toutes les colonnes pour dire « je ne sais pas laquelle » n'aide personne."""
    autre = introspection.Ontologie(
        source=FileSource(name="iris", path=Path("i.csv")),
        schema=SchemaInfo(tables=[TableInfo(name="fleurs", columns=[])]),
    )
    reponse = introspection.decrire_le_schema("quelles colonnes ?", [ontologie_titanic, autre])
    assert "`passengers`" in reponse
    assert "`fleurs`" in reponse
    assert "passenger_id" not in reponse  # les colonnes ne sont PAS dépliées


def test_une_source_sans_table_se_dit_telle_quelle(ontologie_titanic):
    vide = introspection.Ontologie(
        source=FileSource(name="vide", path=Path("v.csv")), schema=SchemaInfo(tables=[])
    )
    reponse = introspection.decrire_le_schema("quelles colonnes ?", [ontologie_titanic, vide])
    assert "(aucune table)" in reponse


def test_sans_aucune_ontologie_on_le_dit():
    assert "aucune source" in introspection.decrire_le_schema("quelles tables ?", [])


# --- une colonne des deux côtés d'une relation ---------------------------------


def test_une_colonne_des_deux_cotes_se_dit_du_cote_qui_REFERENCE(ontologie_titanic):
    """`class_id` est la clé primaire de `classes` ET la clé étrangère de `passengers`.

    Les deux fiches sont vraies et ne disent pas la même chose : « c'est la clé
    primaire de cette table » ferme la question, « elle référence
    `classes(class_id)`, le sens de la valeur se lit là-bas » l'ouvre sur la
    réponse. C'est ce dernier côté qui explique un choix de modélisation, et
    c'est ce qu'on demande quand on demande pourquoi une colonne porte un
    identifiant plutôt qu'un libellé.

    L'ordre des tables du schéma décidait avant, et il met `classes` en premier.
    """
    reponse = introspection.decrire_le_schema("pourquoi class_id ?", [ontologie_titanic])

    assert "Dans la table `passengers`" in reponse
    assert "clé étrangère" in reponse
    assert "le sens de la valeur se lit dans la table `classes`" in reponse


# --- le plancher du repli : ce que le schéma dit quand on renonce --------------


def test_le_schema_dit_quelque_chose_du_terme_que_le_message_nomme(ontologie_titanic):
    """W3, mesuré 0/3 : « pourquoi class_id et pas directement la classe ? »

    Sur `titanic`, qui ne déclare AUCUN dictionnaire : `system → plan →
    synthesize`, et l'utilisateur recevait le repli du planificateur. Le schéma,
    lui, déclare que `class_id` est une clé étrangère vers `classes` — le
    plancher le sert.
    """
    dit = introspection.ce_que_le_schema_en_dit(
        "pourquoi class_id et pas directement la classe ?", [ontologie_titanic], "titanic"
    )

    assert "`class_id`" in dit
    assert "`classes`" in dit
    # une source sans dictionnaire n'en fait citer aucun : c'est le témoin
    assert "dictionnaire" not in dit.lower()


def test_un_message_qui_ne_nomme_ni_colonne_ni_table_ne_declenche_pas_le_plancher(
    ontologie_titanic,
):
    """Sans terme nommé, ``decrire_le_schema`` rend le tour d'horizon des sources.

    C'est déjà, à peu de chose près, ce que le repli du planificateur énumère :
    le servir à sa place ne dirait rien de plus à qui n'a pas été compris.
    """
    assert introspection.ce_que_le_schema_en_dit("fais-moi un truc", [ontologie_titanic]) == ""
    assert introspection.ce_que_le_schema_en_dit("pourquoi class_id ?", []) == ""


def test_le_plancher_rend_le_MEME_texte_que_l_outil_de_l_agent_systeme(ontologie_titanic):
    """Deux textes pour la même fiche, ce seraient deux textes qui divergent."""
    question = "que signifie la colonne class_id ?"

    assert introspection.ce_que_le_schema_en_dit(
        question, [ontologie_titanic], "titanic"
    ) == introspection.decrire_le_schema(question, [ontologie_titanic], "titanic")


# --- le dictionnaire : ce que le DDL ne dit pas --------------------------------


def test_le_dictionnaire_est_cite_sur_une_colonne(ontologie_titanic):
    """Le DDL dit les types ; le dictionnaire dit ce que la valeur VEUT dire."""
    avec = introspection.Ontologie(
        source=ontologie_titanic.source,
        schema=ontologie_titanic.schema,
        dictionnaire="# Base\n- class_id : 1 = pont supérieur, 3 = entrepont.\n- autre chose.\n",
    )
    reponse = introspection.decrire_le_schema("que signifie la colonne class_id ?", [avec])
    assert "pont supérieur" in reponse
    assert "autre chose" not in reponse  # seules les lignes qui citent le terme


# --- la cascade de ciblage : un nom cité désigne la source qui le porte --------


def _deux_sources(ontologie_titanic) -> list[introspection.Ontologie]:
    """`titanic` et une source qui ne partage AUCUN nom avec elle, sauf un."""
    fleurs = introspection.Ontologie(
        source=FileSource(name="iris", path=Path("i.csv")),
        schema=SchemaInfo(
            tables=[
                TableInfo(
                    name="fleurs",
                    columns=[
                        ColumnInfo(name="petal_length", type="REAL"),
                        # Partagée avec `passengers` : c'est le cas où le nom
                        # seul ne suffit pas, et où la source de travail tranche.
                        ColumnInfo(name="class_id", type="INTEGER"),
                    ],
                )
            ]
        ),
    )
    return [fleurs, ontologie_titanic]


def test_une_colonne_que_seule_une_source_porte_designe_cette_source(ontologie_titanic):
    """La voie qui manquait, et qui passait pour une réserve de formulation.

    « c'est quoi petal_length ? » ne nomme ni source ni table. Elle n'est
    pourtant pas ambiguë : une seule source porte ce nom. Avant, la cascade
    s'arrêtait à la table et rendait le tour d'horizon — ce que la
    documentation appelait « la question doit nommer sa source ».
    """
    reponse = introspection.decrire_le_schema(
        "c'est quoi petal_length ?", _deux_sources(ontologie_titanic)
    )
    assert "petal_length" in reponse
    assert "REAL" in reponse
    # Le tour d'horizon aurait listé les tables des DEUX sources.
    assert "passengers" not in reponse


def test_une_colonne_portee_par_deux_sources_est_tranchee_par_celle_du_travail(
    ontologie_titanic,
):
    """C'est le verrou de source, appliqué au sens comme il l'est aux chiffres.

    `class_id` vit des deux côtés. Sans source de travail, rien ne le départage
    et le tour d'horizon est la seule réponse honnête ; avec elle, la question
    se lit dans la source où l'on travaille.
    """
    ontologies = _deux_sources(ontologie_titanic)
    indecidable = introspection.decrire_le_schema("que veut dire class_id ?", ontologies)
    assert "Voici les tables de chacune de mes sources" in indecidable

    tranchee = introspection.decrire_le_schema(
        "que veut dire class_id ?", ontologies, source_de_travail="titanic"
    )
    # La fiche de la colonne, servie depuis `titanic` — et non le tour
    # d'horizon. Quelle table de `titanic` la porte n'est pas le sujet : le
    # verrou a tranché la SOURCE, ce que personne d'autre ne pouvait faire.
    assert "la colonne `class_id` est de type INTEGER" in tranchee
    assert "Voici les tables de chacune de mes sources" not in tranchee


def test_la_designation_de_l_appelant_choisit_la_source_avant_le_message(ontologie_titanic):
    """Le défaut mesuré le 2026-09-17, réduit à son os.

    « dis-moi ce qu'il y a dans ventes, production et iris » fait émettre au
    modèle TROIS appels, un par nom, et les trois rendaient le schéma d'`iris`.
    La désignation était collée au message avant examen ; le message nomme
    plusieurs sources, donc la voie de la source tombait, et c'était la TABLE
    qui tranchait — `fleurs` ici, `iris` là-bas — un nom qui est dans la phrase
    de l'utilisateur et qui y reste quel que soit l'argument de l'appel.

    Ici la phrase nomme les deux sources, donc elle ne tranche rien. La
    désignation, elle, tranche : c'est ce que l'appelant a explicitement visé.
    """
    ontologies = _deux_sources(ontologie_titanic)
    message = "dis-moi ce qu'il y a dans iris et titanic"

    sans = introspection.decrire_le_schema(f"iris {message}", ontologies)
    avec = introspection.decrire_le_schema(f"titanic {message}", ontologies, designation="titanic")

    # sans désignation, la table `fleurs` nommée nulle part ne tranche rien et
    # c'est le tour d'horizon qui part — les deux appels rendaient le même texte
    assert "Voici les tables de chacune de mes sources" in sans
    assert "La source `titanic` contient 2 table(s)" in avec
    assert "petal_length" not in avec


def test_une_designation_qui_ne_nomme_rien_laisse_decider_le_message(ontologie_titanic):
    """L'autre bord : la désignation ne prime que quand elle DÉCIDE.

    Le modèle laisse souvent l'argument vide, ou y met un mot qui n'est le nom
    de rien. Le message décide alors comme avant, au caractère près — sinon ce
    correctif aurait coûté toute la famille des questions de sens, qui ne nomme
    presque jamais sa source dans l'argument.
    """
    ontologies = _deux_sources(ontologie_titanic)

    vide = introspection.decrire_le_schema("c'est quoi petal_length ?", ontologies, designation="")
    bruit = introspection.decrire_le_schema(
        "quoi que ce soit c'est quoi petal_length ?",
        ontologies,
        designation="quoi que ce soit",
    )

    assert "petal_length" in vide
    assert "REAL" in vide
    assert bruit == vide


def test_la_designation_ne_fait_pas_gagner_la_source_de_travail(ontologie_titanic):
    """Et elle décide SEULE, sans la source liée derrière elle.

    ``_cible`` finit par la source de travail quand rien n'est nommé. Si le
    premier passage la lui passait, un argument vide de sens ferait gagner la
    source liée contre une source que la QUESTION nomme — et la source de
    travail n'a jamais eu ce rang : elle départage, elle ne décide pas à la
    place de l'utilisateur (test suivant).
    """
    ontologies = _deux_sources(ontologie_titanic)

    reponse = introspection.decrire_le_schema(
        "bidule c'est quoi petal_length ?",
        ontologies,
        source_de_travail="titanic",
        designation="bidule",
    )

    assert "petal_length" in reponse
    assert "passengers" not in reponse


def test_la_source_de_travail_n_ecrase_jamais_un_nom_cite(ontologie_titanic):
    """Elle départage, elle ne décide pas à la place de l'utilisateur.

    La conversation est sur `iris` ; la question nomme `petal_length`, qui n'y
    est pas ambiguë — mais elle nomme surtout `passengers`. C'est la table
    citée qui gagne, sinon la source de travail deviendrait un filtre et non un
    défaut.
    """
    reponse = introspection.decrire_le_schema(
        "les colonnes de passengers", _deux_sources(ontologie_titanic), source_de_travail="iris"
    )
    assert "passenger_id" in reponse
    assert "petal_length" not in reponse


def test_sans_rien_de_cite_la_source_de_travail_repond(ontologie_titanic):
    """« quelles colonnes ? » dans une conversation liée n'est plus indécidable."""
    reponse = introspection.decrire_le_schema(
        "quelles colonnes ?", _deux_sources(ontologie_titanic), source_de_travail="iris"
    )
    assert "petal_length" in reponse


def test_le_ciblage_par_colonne_sert_le_dictionnaire_de_la_bonne_source(ontologie_titanic):
    """Le point de tout l'exercice : la question de sens reçoit le SENS.

    Sans la voie de la colonne, `decrire_le_schema` rendait la liste des tables
    et le dictionnaire n'était jamais atteint — la réponse à « c'est quoi
    puissance_kw ? » ne pouvait donc pas porter ce que seul le dictionnaire
    sait.
    """
    avec = introspection.Ontologie(
        source=FileSource(name="telemetrie", path=Path("t.duckdb")),
        schema=SchemaInfo(
            tables=[
                TableInfo(name="releves", columns=[ColumnInfo(name="puissance_kw", type="DOUBLE")])
            ]
        ),
        dictionnaire="- `puissance_kw` : -1 n'est pas une puissance, à écarter des moyennes.\n",
    )
    reponse = introspection.decrire_le_schema(
        "c'est quoi puissance_kw exactement ?", [avec, ontologie_titanic]
    )
    assert "à écarter des moyennes" in reponse
    assert "dictionnaire de `telemetrie`" in reponse


def test_une_source_sans_dictionnaire_n_en_voit_apparaitre_aucun(ontologie_titanic):
    """Le témoin de tout ce chantier, tenu par du code et pas seulement mesuré.

    La voie de la colonne fait atteindre la fiche d'une colonne sans que la
    source soit nommée. Elle ne doit pas, ce faisant, faire naître une
    provenance : `titanic` ne déclare aucun dictionnaire, et une réponse qui
    citerait le sien donnerait l'autorité de la base à un savoir général.
    """
    reponse = introspection.decrire_le_schema("c'est quoi sex ?", [ontologie_titanic])
    assert "`sex` (TEXT)" in reponse or "colonne `sex`" in reponse
    assert "dictionnaire" not in reponse.lower()


def test_le_dictionnaire_borne_son_extrait():
    dictionnaire = "\n".join(f"- fare, ligne {n}" for n in range(20))
    extrait = introspection.extrait_du_dictionnaire(dictionnaire, "fare", maximum=3)
    assert len(extrait.splitlines()) == 3


def test_un_terme_absent_du_dictionnaire_ne_produit_rien():
    assert introspection.extrait_du_dictionnaire("- fare : le tarif.", "embarked") == ""


def test_le_dictionnaire_relatif_est_resolu_face_au_yaml(tmp_path: Path):
    (tmp_path / "dico.md").write_text("- sexe : f ou m.", encoding="utf-8")
    (tmp_path / "cat.yaml").write_text(
        "sources:\n  - type: file\n    name: mini\n    path: mini.csv\n    dictionary: dico.md\n",
        encoding="utf-8",
    )
    source = load_catalog(tmp_path / "cat.yaml").sources[0]
    assert source.dictionary == tmp_path / "dico.md"
    assert source.dictionary_text() == "- sexe : f ou m."


def test_une_source_sans_dictionnaire_n_en_lit_aucun(catalogue: Catalog):
    assert catalogue.sources[0].dictionary_text() is None


# --- l'inventaire du repli -----------------------------------------------------


def test_l_inventaire_est_lu_et_non_recopie(catalogue: Catalog, registre: Registry):
    """Le repli citait « titanic, iris… » EN DUR, faux dès qu'un déploiement change."""
    assert introspection.inventaire(catalogue, registre) == (
        "Mes sources : titanic, iris. Mes modèles de prédiction : california_housing, titanic."
    )


def test_l_inventaire_d_une_installation_nue(tmp_path: Path):
    (tmp_path / "vide.yaml").write_text("models: []\n", encoding="utf-8")
    inventaire = introspection.inventaire(
        Catalog(sources=[]), Registry.load(tmp_path / "vide.yaml")
    )
    assert "aucune source déclarée" in inventaire
    assert "aucun modèle" in inventaire


# --- le contrat du module ------------------------------------------------------


def test_le_module_ne_lit_ni_fichier_ni_base():
    """Pur : l'entrée/sortie appartient au nœud du graphe, pas au formateur."""
    source = Path(introspection.__file__).read_text(encoding="utf-8")
    for interdit in ("open(", "read_text", "connect(", "open_source"):
        assert interdit not in source


# --- la consigne : elle suit les faits, dans les deux sens aussi --------------

# L'extrait réel servi par `decrire_le_schema` pour « puissance_kw » sur le
# catalogue de démonstration : la ligne de fiche du dictionnaire, telle que
# `extrait_du_dictionnaire` la recopie. C'est elle qui porte la consigne.
FAITS_AVEC_CONSIGNE = """\
- `puissance_kw` (DOUBLE)

Ce qu'en dit le dictionnaire de `telemetrie` :
> | `puissance_kw` | Puissance instantanée mesurée, en **kilowatts**. \
**`-1` est une valeur sentinelle** — le compteur n'a rien remonté — et n'est \
pas une puissance : à écarter de toute moyenne. |"""

# La même source, la même colonne, mais un extrait qui ne dit que le SENS :
# c'est le témoin qui sépare « les faits portent une consigne » de « les faits
# citent un dictionnaire ». Les deux moitiés de la ceinture sont distinctes.
FAITS_SANS_CONSIGNE = """\
- `cause` (VARCHAR)

Ce qu'en dit le dictionnaire de `telemetrie` :
> | `cause` | Libellé libre : `coupure secteur`, `défaut modem`. |"""


def test_une_consigne_tue_ne_se_sert_pas():
    """Le fait est rendu, l'impératif tombe — mesuré 3/3 sur deux campagnes.

    C'est la dette `I`. La réponse est juste sur le SENS et muette sur ce qu'il
    faut en faire : l'utilisateur sait que `-1` est particulier, il ne sait pas
    que sa moyenne sera fausse s'il la calcule quand même.
    """
    reponse = (
        "Selon le dictionnaire de `telemetrie`, la valeur `-1` de `puissance_kw` "
        "est une valeur sentinelle indiquant que le compteur n'a rien remonté, "
        "et qu'elle ne doit pas être considérée comme une puissance."
    )

    defaut = introspection.defaut_de_fondation(reponse, FAITS_AVEC_CONSIGNE)

    assert defaut.startswith("consigne tue")


def test_une_consigne_INVENTEE_ne_se_sert_pas():
    """L'autre sens : une règle de traitement qu'aucun fait n'énonce.

    Même nocivité que l'attribution inventée — elle donne l'autorité de la base
    à un savoir général, et sur une valeur manquante elle change un calcul.
    """
    reponse = (
        "Selon le dictionnaire de `telemetrie`, la colonne `cause` est un "
        "libellé libre ; les valeurs vides sont à écarter de toute moyenne."
    )

    defaut = introspection.defaut_de_fondation(reponse, FAITS_SANS_CONSIGNE)

    assert defaut.startswith("consigne inventée")


@pytest.mark.parametrize(
    "reponse",
    [
        # le mot du dictionnaire
        "Selon le dictionnaire de `telemetrie`, `puissance_kw` à `-1` n'est pas "
        "une puissance : à écarter de toute moyenne.",
        # la variante qu'on avait vue passer en campagne
        "Selon le dictionnaire de `telemetrie`, `puissance_kw` à `-1` est une "
        "sentinelle et ne doit pas être incluse dans tout calcul de puissance.",
        # un autre verbe, une autre construction
        "Selon le dictionnaire de `telemetrie`, `puissance_kw` vaut `-1` quand le "
        "compteur est muet ; il faut l'exclure du calcul de la moyenne.",
        # le participe, au pluriel
        "Selon le dictionnaire de `telemetrie`, les relevés de `puissance_kw` à "
        "`-1` sont écartés des moyennes.",
    ],
)
def test_quatre_tournures_de_la_MEME_consigne_passent(reponse):
    """La ceinture tient un FAIT, pas une tournure — la leçon de la dette `E`.

    Quatre formulations, aucun mot commun obligatoire, et aucune n'est celle
    que le produit emploie ailleurs. Un oracle qui n'en admettrait qu'une
    mesurerait notre vocabulaire, et il passerait jusqu'au jour où une
    reformulation juste le ferait tomber.
    """
    assert introspection.defaut_de_fondation(reponse, FAITS_AVEC_CONSIGNE) == ""


@pytest.mark.parametrize(
    "reponse",
    [
        "La colonne `age` est de type FLOAT dans la table `passengers`.",
        # « écart type » n'est pas « écarter » : le radical nu aurait crié ici
        "La colonne `age` est de type FLOAT ; son écart type se calcule sur la table `passengers`.",
        # une obligation POSITIVE n'est pas une consigne de dictionnaire
        "La colonne `age` est de type FLOAT dans `passengers`, il faut en "
        "calculer la moyenne sur les lignes renseignées.",
    ],
)
def test_une_source_sans_dictionnaire_ne_declenche_rien(reponse):
    """Le témoin obligatoire : `titanic` et `iris` ne déclarent aucun dictionnaire.

    Aucune consigne à porter, donc rien ne doit se déclencher — ni dans un sens
    ni dans l'autre. Les deux derniers cas sont les faux positifs qu'on a
    cherchés exprès : un mot de statistique qui commence comme le verbe, et une
    obligation qui n'interdit rien.
    """
    faits = "La table `passengers` de la source `titanic` :\n\n- `age` (FLOAT)"

    assert introspection.defaut_de_fondation(reponse, faits) == ""


def test_une_consigne_veut_une_interdiction_ET_un_calcul():
    """Ni l'une ni l'autre ne suffit, et les deux tiennent dans la même phrase."""
    assert introspection.porte_une_consigne("à écarter de toute moyenne") == "a ecarter"
    # une interdiction sans calcul : c'est une phrase sur le sens
    assert introspection.porte_une_consigne("elle ne doit pas être prise pour une puissance") == ""
    # un calcul sans interdiction : c'est une phrase sur le calcul
    assert introspection.porte_une_consigne("la moyenne juste vaut 68,76") == ""
    # les deux, mais dans deux phrases : le texte n'énonce pas la consigne
    assert introspection.porte_une_consigne("Ne pas s'y fier. La moyenne vaut 68,76.") == ""


def test_la_consigne_ne_se_cherche_que_dans_le_dictionnaire_cite():
    """Nos propres impératifs ne sont pas des consignes de la source.

    Les faits que NOUS écrivons en portent — « Demande-moi une table en
    particulier… » — et les compter ferait exiger d'une réponse qu'elle relaie
    une invitation de l'application comme si la source l'avait écrite.
    """
    faits = (
        "Voici les tables de chacune de mes sources :\n\n- **titanic** : `passengers`\n\n"
        "Ne me demande pas de calculer une moyenne sans préciser la table."
    )

    assert (
        introspection.defaut_de_fondation("Ma source est **titanic** : `passengers`.", faits) == ""
    )


def test_sources_nommees_rend_toutes_celles_que_le_message_ecrit():
    """Le pluriel de `source_nommee`, qui rendait `None` dès qu'il y en avait deux.

    « deux noms cités ne désignent pas une cible, et en choisir un serait
    deviner » est juste quand on cherche UNE cible. Une question qui en vise
    plusieurs n'avait alors rien pour la lire, et recevait le catalogue entier.
    """
    catalogue = Catalog(
        sources=[
            FileSource(name="ventes", path=Path("v.csv"), description="Les ventes."),
            FileSource(name="production", path=Path("p.csv"), description="L'atelier."),
            FileSource(name="stocks", path=Path("s.csv"), description="Les entrepôts."),
            FileSource(name="iris", path=Path("i.csv"), description="Le jeu de référence."),
        ]
    )

    nommees = introspection.sources_nommees(
        "Qu'est-ce que t'appelles source vente, production, stock ?", catalogue
    )

    # au singulier dans la question, au pluriel dans le catalogue : c'est le
    # même service que reconnaître « Télémétrie » pour `telemetrie`
    assert [s.name for s in nommees] == ["ventes", "production", "stocks"]


def test_sources_nommees_est_vide_quand_le_message_ne_nomme_rien():
    """La recherche par sujet ne nomme personne, et doit tout recevoir."""
    catalogue = Catalog(
        sources=[
            FileSource(name="ventes", path=Path("v.csv"), description="Les ventes."),
            FileSource(name="production", path=Path("p.csv"), description="L'atelier."),
        ]
    )

    assert introspection.sources_nommees("as-tu des données sur la maintenance ?", catalogue) == []


def test_sources_nommees_reconnait_accents_et_majuscules():
    """`introspection.replie` tient toujours : un nom écrit en français est un nom."""
    catalogue = Catalog(
        sources=[
            FileSource(name="telemetrie", path=Path("t.csv"), description="Les relevés."),
            FileSource(name="facturation", path=Path("f.csv"), description="Les factures."),
        ]
    )

    nommees = introspection.sources_nommees("Télémétrie et Facturation, c'est quoi ?", catalogue)

    assert [s.name for s in nommees] == ["telemetrie", "facturation"]


def test_sources_nommees_ne_replie_pas_les_noms_trop_courts():
    """En deçà de trois lettres, replier le pluriel ferait se rencontrer n'importe quoi."""
    catalogue = Catalog(sources=[FileSource(name="abs", path=Path("a.csv"), description="X.")])

    assert introspection.sources_nommees("parle-moi de ab", catalogue) == []


# --- une fiche citée doit être PORTÉE, pas seulement nommée -------------------


def _fiches_de_trois() -> dict[str, str]:
    """Trois fiches comme les outils les servent, avec ce qu'on a lu dedans."""
    return {
        "ventes": "La source **ventes** (postgres) — Le carnet de commandes.\n\n"
        "4 table(s), 673 ligne(s) (clients : 18, commandes : 180)",
        "production": "La source **production** (duckdb) — L'atelier et ses arrêts.\n\n"
        "4 table(s), 222 ligne(s) (machines : 9, arrets_machine : 70)",
        "stocks": "La source **stocks** (file) — Les entrepôts et leurs mouvements.\n\n"
        "3 table(s), 519 ligne(s) (mouvements : 480, inventaire : 36)",
    }


def test_les_marques_d_une_fiche_excluent_le_nom_de_la_source():
    """Le nom ne prouve rien : c'est justement ce qu'on vient de donner au modèle.

    Une réponse qui répète les trois noms qu'elle a reçus porte trois noms et
    zéro fait. Si le nom comptait pour marque, le défaut mesuré le 2026-09-17
    serait déclaré conforme par la mesure censée l'attraper.
    """
    marques = introspection.marques_des_fiches(_fiches_de_trois())

    assert "ventes" not in marques["ventes"]
    assert "commandes" in marques["ventes"]
    assert "entrepots" in marques["stocks"]


def test_les_marques_ecartent_ce_que_toutes_les_fiches_portent():
    """`source`, `table`, `ligne` traversent le catalogue : elles ne distinguent rien."""
    marques = introspection.marques_des_fiches(_fiches_de_trois())

    for propres in marques.values():
        assert "source" not in propres
        assert "table" not in propres
        assert "ligne" not in propres


def test_un_mot_de_liaison_n_est_pas_une_marque():
    """`les` ne distinguait `stocks` que par accident de rédaction.

    Une seule fiche l'écrivait, il devenait donc « propre » à elle — et il
    suffisait alors d'écrire « sur les sources » pour être réputé avoir lu la
    fiche. Le défaut du 2026-09-17 passait ainsi la mesure censée l'attraper.
    Quatre caractères pour un mot, deux chiffres pour un nombre : `891` reste un
    fait, `les` et `9` n'en sont pas.
    """
    marques = introspection.marques_des_fiches(_fiches_de_trois())

    assert "les" not in marques["stocks"]
    assert "entrepots" in marques["stocks"]
    assert "9" not in marques["production"]
    assert "70" in marques["production"]


def test_deux_fiches_identiques_n_ont_aucune_marque():
    """Exiger l'impossible ferait servir le repli sur des réponses justes.

    Deux sources décrites des mêmes mots n'ont rien qui les distingue. La
    ceinture ne doit alors rien réclamer — c'est la seule façon qu'elle a de ne
    pas punir une réponse pour un défaut du catalogue.
    """
    fiches = {
        "une": "La source **une** (file) — Un export.",
        "autre": "La source **autre** (file) — Un export.",
    }
    faits = "\n\n".join(fiches.values())

    marques = introspection.marques_des_fiches(fiches)

    assert marques == {"une": frozenset(), "autre": frozenset()}
    assert (
        introspection.defaut_de_fondation("Les sources `une` et `autre`.", faits, faits, marques)
        == ""
    )


def test_une_reponse_qui_ne_rend_que_les_noms_est_ecartee():
    """Le défaut du 2026-09-17, réduit à ce qu'il était.

    « Tu travailles sur les sources `production` et `stocks`. Dis-moi ce que tu
    souhaites savoir sur ces sources. » : 108 caractères pour 986 servis, deux
    noms, pas un fait — et la ceinture la servait, parce qu'elle comparait des
    NOMS et que les deux noms y étaient.
    """
    fiches = _fiches_de_trois()
    marques = introspection.marques_des_fiches(fiches)
    faits = f"{fiches['production']}\n\n{fiches['stocks']}"

    defaut = introspection.defaut_de_fondation(
        "Tu travailles sur les sources `production` et `stocks`. "
        "Dis-moi ce que tu souhaites savoir sur ces sources.",
        faits,
        faits,
        {nom: marques[nom] for nom in ("production", "stocks")},
    )

    assert defaut == "source(s) citée(s) sans un fait de leur fiche : production, stocks"


def test_un_seul_fait_par_fiche_suffit():
    """Le modèle a le droit de résumer — pas celui de ne rien retenir.

    Un élément, pas la fiche entière : ici le nombre d'arrêts pour `production`
    et le mot `entrepôts` pour `stocks`, chacun absent de toutes les autres
    fiches. C'est le bord qu'il ne faut pas durcir : exiger la fiche complète
    ferait jeter des réponses justes, comme la ceinture d'exhaustivité l'a
    déjà fait en remplaçant « la source interventions » par tout le catalogue.
    """
    fiches = _fiches_de_trois()
    marques = introspection.marques_des_fiches(fiches)
    faits = f"{fiches['production']}\n\n{fiches['stocks']}"

    defaut = introspection.defaut_de_fondation(
        "`production` est une base DuckDB, avec 70 arrêts machine relevés ; "
        "`stocks` décrit les entrepôts et leurs mouvements.",
        faits,
        faits,
        {nom: marques[nom] for nom in ("production", "stocks")},
    )

    assert defaut == ""


def test_les_marques_se_calculent_sur_tout_le_catalogue():
    """Pas sur les seules fiches servies, et c'est ce qui les rend fiables.

    `entrepots` ne distingue `stocks` que parce que les quatre autres fiches ne
    le portent pas. Calculées sur deux fiches, les marques compteraient comme
    propre tout mot absent de l'autre — et un tour à deux sources jugerait plus
    large qu'un tour à cinq, pour la même réponse.
    """
    fiches = _fiches_de_trois()
    fiches["entrepots_bis"] = "La source **entrepots_bis** (file) — Les entrepôts, autrement."

    marques = introspection.marques_des_fiches(fiches)

    assert "entrepots" not in marques["stocks"]


def test_sans_fiche_servie_la_ceinture_ne_reclame_rien():
    """L'inventaire du catalogue n'est pas une fiche : il énumère des noms.

    C'est sa réponse, et exiger un fait par source y ferait servir le repli sur
    « quelles sources as-tu ? » — la question la mieux tenue de la surface.
    """
    faits = "J'ai accès à 2 source(s) de données :\n\n- **ventes** (postgres) — Le carnet."

    assert introspection.defaut_de_fondation("Mes sources : `ventes`.", faits, faits) == ""
