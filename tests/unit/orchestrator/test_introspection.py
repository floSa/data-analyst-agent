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


# --- les réponses : rien qui ne soit lu dans un artefact -----------------------


def test_les_sources_sont_celles_du_catalogue(catalogue: Catalog):
    reponse = introspection.decrire_les_sources(catalogue)
    assert "titanic" in reponse
    assert "iris" in reponse
    assert "Base Titanic." in reponse  # la description déclarée, pas une paraphrase
    assert "sans description" in reponse  # iris n'en déclare pas : on le dit


def test_un_catalogue_vide_se_dit_vide():
    assert "aucune source" in introspection.decrire_les_sources(Catalog(sources=[]))


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
