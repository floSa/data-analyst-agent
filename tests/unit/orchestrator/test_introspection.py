"""Les questions SUR le système : ce qui est reconnu, et ce qui est répondu.

Deux choses séparées, et testées séparément. La **reconnaissance**
(``sujet_de``) est un lexique : on la juge sur sa précision, c'est-à-dire
autant sur ce qu'elle laisse passer que sur ce qu'elle attrape. La
**réponse** est un formateur pur : on la juge sur le fait qu'elle ne dise rien
qui ne soit lu dans un artefact du dépôt.
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
  - dataset: maxizoo_sales
    task: regression
    model_path: maxizoo_sales.joblib
    target: quantity
    unit: unités vendues
    description: Prévision de la quantité vendue d'un SKU.
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


# --- la reconnaissance : précise avant d'être exhaustive ------------------------


@pytest.mark.parametrize(
    ("question", "sujet"),
    [
        # sources — la question du propriétaire et ses tournures
        (
            "Bonjour, saurais-tu me dire les différentes sources de données que tu possèdes ?",
            "sources",
        ),
        ("Sur quoi peux-tu travailler ?", "sources"),
        ("À quelles bases de données as-tu accès ?", "sources"),
        ("Liste-moi tes sources de données.", "sources"),
        # schéma
        ("Quelles tables contient la source titanic ?", "schema"),
        ("Comment est structurée la base titanic ?", "schema"),
        ("Quelles colonnes y a-t-il dans la table passengers ?", "schema"),
        ("Que signifie la colonne class_id ?", "schema"),
        # le nom NU, sans le mot « colonne » : personne ne l'écrit
        ("Que signifie store_id ?", "schema"),
        ("que veut dire revenue", "schema"),
        ("À quoi correspond quantity ?", "schema"),
        # modèles
        ("Quels modèles de prédiction sais-tu utiliser ?", "modeles"),
        ("Est-ce que tu sais faire des prédictions, et sur quoi ?", "modeles"),
        # features — dont la question exacte de docs/axes-amelioration.md
        ("De quels attributs as-tu besoin ?", "features"),
        ("De quoi as-tu besoin pour prédire ?", "features"),
        ("Quelles mesures faut-il te donner pour que tu prédises l'espèce d'un iris ?", "features"),
        # capacités
        ("Que sais-tu faire ?", "capacites"),
        ("À quoi sers-tu, exactement ?", "capacites"),
        ("Qui es-tu ?", "capacites"),
    ],
)
def test_les_questions_meta_sont_reconnues(question: str, sujet: str):
    assert introspection.sujet_de(question) == sujet


@pytest.mark.parametrize(
    "question",
    [
        # de vraies questions sur les DONNÉES : le lexique doit les laisser passer
        "Combien de passagers ont survécu ?",
        "Quel est l'âge du passager le plus âgé ?",
        "Quel pourcentage des femmes de 1re classe ont survécu ?",
        "Trace un histogramme de l'âge des passagers",
        "Fais une ACP sur iris",
        "Combien de lignes contient la table passengers ?",
        "Sur quelle période portent les données ?",
        "Prédis la survie du passager 42",
        # le piège : la tournure d'une question de schéma pour une question de contenu
        "Quelles colonnes de la table passengers contiennent des valeurs manquantes ?",
        "Quelle est la répartition des colonnes par type ?",
        # « que signifie » suivi d'un GROUPE nominal : c'est une question sur
        # les données, pas sur un champ — le nom nu seul déclenche
        "Que signifie une progression de 12 % sur ce mois ?",
        "Que signifie ce pic de novembre ?",
        "Que signifie cela ?",
    ],
)
def test_les_questions_sur_les_donnees_ne_sont_pas_volees(question: str):
    """Un routeur se juge autant sur ce qu'il laisse passer que sur ce qu'il attrape.

    Se tromper de ce côté coûte un appel LLM ; se tromper de l'autre donne une
    réponse fausse à une question sur les données, sans le dire.
    """
    assert introspection.sujet_de(question) is None


def test_un_marqueur_de_calcul_fait_abandonner_le_lexique():
    """« quelles colonnes » est une tournure de schéma — « combien » tranche."""
    assert introspection.sujet_de("Quelles colonnes a la table passengers ?") == "schema"
    assert introspection.sujet_de("Combien de colonnes a la table passengers ?") is None


# --- une base réelle : ses colonnes portent les noms du métier -----------------


@pytest.fixture
def ontologie_etoile() -> introspection.Ontologie:
    """Une source dont une colonne s'appelle `source` — comme la vraie Maxizoo.

    `weather.source` existe : c'est le mot par lequel on désigne une source de
    données, et c'est aussi un nom de colonne. Deux tables jouets ne pouvaient
    pas montrer la collision.
    """
    return introspection.Ontologie(
        source=FileSource(name="maxizoo", path=Path("m.duckdb")),
        schema=SchemaInfo(
            dialect="duckdb",
            tables=[
                TableInfo(
                    name="weather",
                    columns=[
                        ColumnInfo(name="date", type="DATE", nullable=False),
                        ColumnInfo(name="source", type="VARCHAR", values=["open-meteo"]),
                    ],
                ),
                TableInfo(
                    name="stores",
                    columns=[ColumnInfo(name="store_id", type="VARCHAR", nullable=False)],
                ),
            ],
        ),
    )


def test_le_mot_qui_introduit_la_source_n_est_pas_pris_pour_une_colonne(ontologie_etoile):
    """« la SOURCE maxizoo » désignait la colonne `weather.source` — réponse fausse."""
    reponse = introspection.decrire_le_schema(
        "Quelles colonnes a la source maxizoo ?", [ontologie_etoile]
    )

    assert reponse.startswith("La source `maxizoo` contient 2 table(s)")
    assert "`store_id`" in reponse  # tout le schéma, et non une colonne isolée


def test_une_colonne_qui_porte_un_mot_du_cadre_reste_trouvable(ontologie_etoile):
    """Le retrait ne vaut que pour l'introducteur ACCOLÉ à un nom réellement cité."""
    reponse = introspection.decrire_le_schema(
        "Que signifie la colonne source ?", [ontologie_etoile]
    )

    assert reponse.startswith("Dans la table `weather`, la colonne `source`")


def test_une_question_sur_les_tables_ne_deplie_pas_les_colonnes(ontologie_etoile):
    """« Quelles tables ? » demande des tables. Sur dix tables, la nuance se voit."""
    reponse = introspection.decrire_le_schema("Quelles tables as-tu ?", [ontologie_etoile])

    assert "`weather`, `stores`" in reponse
    assert "store_id" not in reponse  # les colonnes ne sont pas dépliées
    assert "quelles colonnes dans la table weather" in reponse  # on dit comment les avoir


def test_une_question_sur_les_colonnes_les_deplie_bien(ontologie_etoile):
    reponse = introspection.decrire_le_schema("Quelles colonnes as-tu ?", [ontologie_etoile])

    assert "`store_id`" in reponse
    assert "`open-meteo`" in reponse or "'open-meteo'" in reponse


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
    reponse = introspection.decrire_les_features(registre, "maxizoo_sales")
    assert "promo_type" in reponse
    assert "Typologie de la campagne active" in reponse  # la description du champ
    assert "'cadeau_seuil'" in reponse  # les valeurs autorisées du Literal


def test_sans_modele_designe_chaque_modele_est_decrit(registre: Registry):
    """Une RÉPONSE, et non la question « sur quel modèle veux-tu prédire ? »."""
    reponse = introspection.decrire_les_features(registre, None)
    assert "titanic" in reponse
    assert "california_housing" in reponse
    assert "maxizoo_sales" in reponse
    assert "`base_price`" in reponse
    # les deux modèles sans schéma sont dits tels quels, et non passés sous silence
    assert reponse.count("aucun schéma de features déclaré") == 2
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
        "Mes sources : titanic, iris. Mes modèles de prédiction : "
        "california_housing, maxizoo_sales, titanic."
    )


def test_l_inventaire_d_une_installation_nue(tmp_path: Path):
    (tmp_path / "vide.yaml").write_text("models: []\n", encoding="utf-8")
    inventaire = introspection.inventaire(
        Catalog(sources=[]), Registry.load(tmp_path / "vide.yaml")
    )
    assert "aucune source déclarée" in inventaire
    assert "aucun modèle" in inventaire


# --- le contrat du module ------------------------------------------------------


def test_seul_le_schema_exige_une_connexion():
    """Les autres sujets sont lisibles sans ouvrir quoi que ce soit — c'est ce qui
    permet d'y répondre sans le moindre aller-retour."""
    assert introspection.SUJETS_AVEC_ONTOLOGIE == ("schema",)
    assert set(introspection.SUJETS_AVEC_ONTOLOGIE) <= set(get_args(introspection.Sujet))


def test_le_lexique_ne_couvre_que_des_sujets_connus():
    assert {sujet for sujet, _ in introspection.LEXIQUE} == set(get_args(introspection.Sujet))


def test_le_module_ne_lit_ni_fichier_ni_base():
    """Pur : l'entrée/sortie appartient au nœud du graphe, pas au formateur."""
    source = Path(introspection.__file__).read_text(encoding="utf-8")
    for interdit in ("open(", "read_text", "connect(", "open_source"):
        assert interdit not in source
