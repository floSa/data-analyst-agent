"""La correspondance source -> features : déclarée, ou refusée. Jamais devinée."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_analyst_agent.agents.inference.correspondance import (
    Correspondance,
    CorrespondanceIndisponible,
    FeatureDeclaration,
)
from data_analyst_agent.agents.inference.schemas import TitanicFeatures
from data_analyst_agent.agents.inference.validation import validate_features
from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.agents.retrieval.sql import ColumnInfo, SchemaInfo, TableInfo

# La base Postgres du catalogue : `pclass` y est porté par `classes.level`.
PAR_LE_NIVEAU = {
    "sex": "passengers.sex",
    "pclass": "classes.level",
    "age": "passengers.age",
    "sibsp": "passengers.sibsp",
    "parch": "passengers.parch",
    "fare": "passengers.fare",
    "embarked": "passengers.embarked",
}
# La même base, déclarée sur la colonne qui porte le LIBELLÉ : l'écart de
# représentation, tenu séparément de l'écart de nom.
PAR_LE_LIBELLE = {
    **PAR_LE_NIVEAU,
    "pclass": {
        "column": "classes.label",
        "values": {"1re classe": 1, "2e classe": 2, "3e classe": 3},
    },
}
COLONNES = ["sex", "pclass", "age", "sibsp", "parch", "fare", "embarked"]
LIGNE_NIVEAU = ["male", 3, 22.0, 1, 0, 7.25, "S"]


def titanic(declaration: dict) -> Correspondance:
    return Correspondance.declaree(
        source="titanic", dataset="titanic", declarations={"titanic": declaration}
    )


# --- la déclaration ---------------------------------------------------------


def test_la_forme_courte_vaut_la_forme_longue():
    """`pclass: classes.level` est `pclass: {column: classes.level}`."""
    courte = FeatureDeclaration.model_validate("classes.level")
    longue = FeatureDeclaration.model_validate({"column": "classes.level"})

    assert courte == longue
    assert courte.nom_de_colonne == "level"


def test_une_cle_inconnue_de_la_declaration_est_refusee():
    with pytest.raises(ValidationError, match="colonne"):
        FeatureDeclaration.model_validate({"column": "classes.level", "colonne": "level"})


def test_le_catalogue_porte_la_declaration(tmp_path):
    """C'est la SOURCE qui déclare, et le catalogue la relit telle quelle."""
    csv = tmp_path / "p.csv"
    csv.write_text("sex\nfemale\n", encoding="utf-8")
    catalogue = Catalog.model_validate(
        {
            "sources": [
                {
                    "type": "file",
                    "name": "p",
                    "path": str(csv),
                    "features": {"titanic": PAR_LE_NIVEAU},
                }
            ]
        }
    )

    assert catalogue.get("p").features["titanic"]["pclass"].column == "classes.level"


def test_une_source_sans_declaration_n_en_porte_aucune(tmp_path):
    csv = tmp_path / "p.csv"
    csv.write_text("sex\nfemale\n", encoding="utf-8")

    assert FileSource(name="p", path=csv).features == {}


# --- la consigne donnée à l'agent SQL ---------------------------------------


def test_la_consigne_nomme_la_colonne_source_et_son_alias():
    consigne = titanic(PAR_LE_NIVEAU).consigne_sql()

    assert "classes.level AS pclass" in consigne
    assert "passengers.embarked AS embarked" in consigne
    # la consigne ne porte QUE sur les colonnes : le reste vient de la demande
    assert "le filtre et le nombre de lignes restent ceux de la demande" in consigne


# --- le rapprochement de la ligne lue ---------------------------------------


def test_la_feature_est_reconnue_sous_son_alias():
    payload = titanic(PAR_LE_NIVEAU).payload(COLONNES, LIGNE_NIVEAU)

    assert payload == dict(zip(COLONNES, LIGNE_NIVEAU, strict=True))
    assert validate_features(TitanicFeatures, payload).valid


def test_la_feature_est_reconnue_sous_le_nom_nu_de_la_colonne_declaree():
    """L'agent SQL peut avoir omis l'alias : `level` doit encore être reconnu."""
    colonnes = ["sex", "level", "age", "sibsp", "parch", "fare", "embarked"]

    payload = titanic(PAR_LE_NIVEAU).payload(colonnes, LIGNE_NIVEAU)

    assert payload["pclass"] == 3


def test_le_rapprochement_ignore_la_casse():
    """Les sources fichier gardent des en-têtes capitalisés (« Pclass », « Sex »)."""
    colonnes = ["Sex", "Pclass", "Age", "SibSp", "Parch", "Fare", "Embarked"]

    payload = titanic({feature: feature for feature in COLONNES}).payload(colonnes, LIGNE_NIVEAU)

    assert payload == dict(zip(COLONNES, LIGNE_NIVEAU, strict=True))


def test_une_colonne_declaree_absente_de_la_ligne_n_est_pas_inventee():
    payload = titanic(PAR_LE_NIVEAU).payload(["sex", "age"], ["male", 22.0])

    assert payload == {"sex": "male", "age": 22.0}
    manquants = validate_features(TitanicFeatures, payload).missing_fields
    assert "pclass" in manquants  # le schéma le réclame, sous son nom


# --- l'écart de REPRÉSENTATION ----------------------------------------------


def test_le_libelle_declare_est_traduit_vers_la_valeur_du_schema():
    ligne = ["male", "3e classe", 22.0, 1, 0, 7.25, "S"]

    payload = titanic(PAR_LE_LIBELLE).payload(COLONNES, ligne)

    assert payload["pclass"] == 3
    assert validate_features(TitanicFeatures, payload).valid


def test_un_libelle_non_declare_reste_tel_quel_et_le_schema_le_refuse():
    """Substituer ici une valeur légale serait la supposition qu'on interdit."""
    ligne = ["male", "troisième classe", 22.0, 1, 0, 7.25, "S"]

    payload = titanic(PAR_LE_LIBELLE).payload(COLONNES, ligne)

    assert payload["pclass"] == "troisième classe"
    issue = next(
        i for i in validate_features(TitanicFeatures, payload).issues if i.field == "pclass"
    )
    assert issue.problem == "valeur_non_autorisee"
    assert "'troisième classe'" in issue.message


def test_une_valeur_hors_bornes_venue_de_la_base_est_refusee_comme_une_autre():
    """La garde ne se relâche pas parce que la valeur vient de la source."""
    ligne = ["male", 3, 150.0, 1, 0, 7.25, "S"]

    payload = titanic(PAR_LE_NIVEAU).payload(COLONNES, ligne)

    issue = next(i for i in validate_features(TitanicFeatures, payload).issues if i.field == "age")
    assert issue.problem == "hors_bornes"


# --- les refus --------------------------------------------------------------


def test_aucune_declaration_refuse_en_disant_quoi_ecrire():
    with pytest.raises(CorrespondanceIndisponible) as refus:
        Correspondance.declaree(source="titanic", dataset="titanic", declarations={})

    message = str(refus.value)
    assert "'titanic'" in message
    assert "features: {titanic: ...}" in message
    for feature in COLONNES:
        assert feature in message


def test_une_declaration_pour_un_autre_modele_ne_vaut_pas_declaration():
    with pytest.raises(CorrespondanceIndisponible):
        Correspondance.declaree(
            source="titanic", dataset="titanic", declarations={"iris": PAR_LE_NIVEAU}
        )


def test_une_declaration_incomplete_nomme_ce_qui_manque():
    partielle = {k: v for k, v in PAR_LE_NIVEAU.items() if k not in ("pclass", "fare")}

    with pytest.raises(CorrespondanceIndisponible) as refus:
        titanic(partielle)

    message = str(refus.value)
    assert "pclass" in message
    assert "fare" in message
    assert "incomplète" in message


def test_une_feature_inconnue_du_modele_est_refusee():
    with pytest.raises(CorrespondanceIndisponible) as refus:
        titanic({**PAR_LE_NIVEAU, "cabine": "passengers.cabin"})

    assert "cabine" in str(refus.value)


# --- l'identité, pour ce qui n'a aucun YAML où déclarer ----------------------


def test_par_le_nom_est_l_identite():
    correspondance = Correspondance.par_le_nom(source="resultat_1", dataset="titanic")

    assert {f: d.column for f, d in correspondance.par_feature.items()} == {
        feature: feature for feature in COLONNES
    }
    assert correspondance.payload(COLONNES, LIGNE_NIVEAU)["pclass"] == 3


# --- la déclaration relue CONTRE la source ----------------------------------
#
# Une déclaration est du texte dans un YAML : rien n'empêche d'y écrire
# `classes.levelx`. Ce qui suivait était lisible et tard — SQL en erreur sur une
# colonne inconnue, correction au jugé de l'agent, feature absente du payload.
# `confronter` lit le schéma et refuse avant la requête.


def schema_titanic() -> SchemaInfo:
    """Le schéma de la base Postgres du catalogue, réduit à ce qui compte ici."""
    return SchemaInfo(
        dialect="postgresql",
        tables=[
            TableInfo(
                name="passengers",
                columns=[
                    ColumnInfo(name=nom, type=type_)
                    for nom, type_ in (
                        ("passenger_id", "INTEGER"),
                        ("sex", "VARCHAR"),
                        ("age", "DOUBLE PRECISION"),
                        ("sibsp", "INTEGER"),
                        ("parch", "INTEGER"),
                        ("fare", "DOUBLE PRECISION"),
                        ("embarked", "VARCHAR"),
                        ("class_id", "INTEGER"),
                    )
                ],
            ),
            TableInfo(
                name="classes",
                columns=[
                    ColumnInfo(name="id", type="INTEGER"),
                    ColumnInfo(name="level", type="INTEGER"),
                    ColumnInfo(name="label", type="VARCHAR"),
                ],
            ),
        ],
    )


def test_une_declaration_juste_passe_la_confrontation():
    titanic(PAR_LE_NIVEAU).confronter(schema_titanic())
    titanic(PAR_LE_LIBELLE).confronter(schema_titanic())


def test_une_colonne_declaree_qui_n_existe_pas_est_refusee_avec_les_vraies():
    """Le cas du §19.11 : `classes.levelx` au lieu de `classes.level`.

    Le message doit suffire à corriger le YAML sans ouvrir la base : la colonne
    introuvable, la colonne réelle qui lui ressemble, et la liste de ce que la
    source porte.
    """
    fautive = {**PAR_LE_NIVEAU, "pclass": "classes.levelx"}

    with pytest.raises(CorrespondanceIndisponible) as refus:
        titanic(fautive).confronter(schema_titanic())

    message = str(refus.value)
    assert "classes.levelx" in message  # ce qui est écrit dans le catalogue
    assert "peut-être classes.level ?" in message  # ce qu'on voulait sûrement
    assert "classes.label" in message  # les colonnes réelles, proposées
    assert "passengers.fare" in message
    assert "rien n'a été interrogé" in message


def test_toutes_les_colonnes_absentes_sont_dites_d_un_coup():
    """Corriger un YAML trois fois de suite parce qu'il rend une faute à la fois
    est une perte de temps qu'aucune contrainte n'impose."""
    fautive = {**PAR_LE_NIVEAU, "pclass": "classes.levelx", "fare": "passengers.prix"}

    with pytest.raises(CorrespondanceIndisponible) as refus:
        titanic(fautive).confronter(schema_titanic())

    assert "classes.levelx" in str(refus.value)
    assert "passengers.prix" in str(refus.value)


def test_une_colonne_declaree_sans_sa_table_est_reconnue():
    """La source à une table ne se qualifie pas, et n'a pas à le faire."""
    plat = {feature: feature for feature in PAR_LE_NIVEAU}
    schema = SchemaInfo(
        dialect="duckdb",
        tables=[
            TableInfo(
                name="titanic",
                columns=[ColumnInfo(name=f, type="VARCHAR") for f in plat],
            )
        ],
    )

    titanic(plat).confronter(schema)


def test_la_confrontation_ignore_la_casse():
    """Un en-tête de CSV garde ses majuscules (`Pclass`), Postgres non."""
    schema = SchemaInfo(
        dialect="duckdb",
        tables=[
            TableInfo(
                name="titanic",
                columns=[ColumnInfo(name=f.capitalize(), type="VARCHAR") for f in PAR_LE_NIVEAU],
            )
        ],
    )

    titanic({feature: feature for feature in PAR_LE_NIVEAU}).confronter(schema)


def test_une_colonne_sans_ressemblance_est_refusee_sans_proposition_au_hasard():
    """Muet quand rien ne ressemble : une suggestion tirée au sort coûterait la
    confiance qu'on gagne à ne rien deviner. La liste réelle, elle, reste là."""
    fautive = {**PAR_LE_NIVEAU, "pclass": "zzzzzzzz"}

    with pytest.raises(CorrespondanceIndisponible) as refus:
        titanic(fautive).confronter(schema_titanic())

    assert "peut-être" not in str(refus.value)
    assert "classes.level" in str(refus.value)
