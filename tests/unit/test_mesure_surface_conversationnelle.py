"""L'oracle de la surface conversationnelle (scripts/mesure_surface_conversationnelle.py).

Un oracle de campagne rend des verdicts que personne ne recalcule à la main :
c'est un instrument, et un instrument se vérifie sur des cas dont on connaît la
réponse. Celui de la PÉRIODE en a deux, et ils tirent en sens contraires —

- il doit REFUSER les réponses fausses réellement rendues par le système : une
  réponse sur les âges à une question de dates (relevée le 2026-09-22, deux
  campagnes, comptée juste), une période « non spécifiée » (deux campagnes avant
  C52), une clarification qui renvoie la question ;
- il doit ACCEPTER le constat d'absence quelle qu'en soit l'écriture, parce
  qu'un oracle qui exige UN mot refuse des réponses justes — c'est le défaut
  réparé en C51, et l'allonger en liste de tournures le referait.

Les textes de ce fichier ne sont pas inventés : ce sont ceux des relevés, et
leurs variantes sont des façons de dire la même chose qu'aucune liste n'aurait
prévues.
"""

from __future__ import annotations

import pytest
from mesure_surface_conversationnelle import (
    QuestionMeta,
    VeriteTerrain,
    classer,
    constate_l_absence_de_date,
    porte_un_renvoi,
    porte_une_date,
    replie,
)

# La réponse FAUSSE que l'oracle d'avant bénissait : on demande des dates, elle
# rend des âges — et `age` étant une colonne de `passengers`, l'oracle était
# satisfait.
REPONSE_SUR_LES_AGES = (
    "Les données disponibles dans la table `passengers` couvrent des âges "
    "allant de 0.42 à 80.0 ans."
)

# La réponse JUSTE, celle que C52 a rendue possible.
REPONSE_SANS_DATE = (
    "La source `titanic` est une base de données qui ne porte aucune colonne "
    "de date, elle ne couvre donc aucune période."
)


def verite(temporelles: tuple[str, ...]) -> VeriteTerrain:
    """Un relevé réduit à ce que l'oracle de période lit : les colonnes de date."""
    return VeriteTerrain(
        sources=("titanic",),
        datasets=(),
        tables={"titanic": ("passengers",)},
        colonnes={"titanic.passengers": ("passenger_id", "name", "age", "fare")},
        temporelles={"titanic.passengers": temporelles},
        lignes={"titanic.passengers": 891},
        features={},
        ages_max=80.0,
        survivants=342,
        colonnes_a_trous=("age",),
        renvois={"titanic.passengers.class_id": "classes"},
    )


@pytest.mark.parametrize(
    "texte",
    [
        REPONSE_SUR_LES_AGES,
        "La source titanic couvre une période non spécifiée dans sa description.",
        "Les données que j'ai datent de la période couverte par les sources titanic et iris.",
        "Sur quelle source veux-tu travailler : titanic, iris ?",
        "La table passengers contient 891 lignes et 12 colonnes.",
    ],
)
def test_une_reponse_sans_date_ni_constat_ne_passe_pas(texte: str) -> None:
    """Aucune des réponses fausses réellement rendues ne porte de fait de date.

    La deuxième et la troisième sont les plus instructives : elles écrivent le
    mot « période » et le mot « datent ». Un oracle qui aurait cherché ces
    mots-là les aurait bénies ; celui-ci cherche une NÉGATION qui précède, ou
    une année.
    """
    assert not porte_une_date(texte)
    assert not constate_l_absence_de_date(texte)


@pytest.mark.parametrize(
    "texte",
    [
        REPONSE_SANS_DATE,
        "Aucune de mes sources ne contient de colonne temporelle.",
        "Mes données sont dépourvues de dates : ni titanic ni iris n'en portent.",
        "Elles ne comportent pas de date, donc aucune période ne peut être calculée.",
        "Il n'y a aucune information temporelle dans ces sources.",
        "Ces données ne sont pas datées.",
    ],
)
def test_le_constat_d_absence_est_reconnu_quelle_qu_en_soit_l_ecriture(texte: str) -> None:
    """Six façons de dire la même chose, dont une seule est celle du système.

    C'est l'exigence que le défaut de C51 avait rendue nécessaire : la famille
    des façons de dire une absence est ouverte, et une liste de tournures en
    refuserait la première qu'elle n'aurait pas prévue.
    """
    assert constate_l_absence_de_date(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "Les données couvrent la période du 1912-04-10 au 1912-04-15.",
        "Du 10 avril 1912 au 15 avril 1912.",
        "De 1912 à 1913.",
    ],
)
def test_une_date_est_reconnue_a_son_millesime(texte: str) -> None:
    assert porte_une_date(texte)


def test_sans_colonne_de_date_seul_le_constat_passe() -> None:
    """Le verdict complet, sur la question telle qu'elle est posée en campagne.

    Et l'autre bord : une DATE n'y passe pas non plus, parce qu'il n'y en a
    aucune à lire — elle serait inventée.
    """
    question = QuestionMeta(
        "periode",
        "période",
        "De quand datent les données que tu as ?",
        exigence=verite(()).exigence_de_periode("titanic.passengers"),
    )
    assert question.satisfait(REPONSE_SANS_DATE) == (True, [])
    assert question.satisfait(REPONSE_SUR_LES_AGES) == (
        False,
        ["le constat qu'il n'y a aucune date"],
    )
    assert question.satisfait("Les données vont du 1912-04-10 au 1912-04-15.")[0] is False


def test_avec_une_colonne_de_date_c_est_la_date_qui_est_exigee() -> None:
    """L'exigence bascule avec la source, et le constat d'absence y devient faux.

    Personne ne mesure ce bord-là aujourd'hui — ni `titanic` ni `iris` ne
    portent de colonne de date — et c'est précisément pourquoi il est figé ici :
    un oracle dont une moitié n'est jamais exercée est une moitié qui dérive.
    """
    question = QuestionMeta(
        "periode",
        "période",
        "Sur quelle période portent les données ?",
        exigence=verite(("embarked_at",)).exigence_de_periode("titanic.passengers"),
    )
    assert question.satisfait("Du 1912-04-10 au 1912-04-15.") == (True, [])
    assert question.satisfait(REPONSE_SANS_DATE) == (False, ["une date"])


def test_les_noms_de_colonnes_ne_prouvent_plus_rien() -> None:
    """Le défaut, nommé : `age` est une colonne, et l'oracle s'en contentait.

    La réponse fausse porte `age` — le nom de la colonne vit à l'intérieur du
    mot « âges », que l'oracle replie en « ages », et c'était toute la preuve
    qu'il demandait qu'on eût regardé le schéma.
    """
    vt = verite(())
    assert "age" in vt.colonnes["titanic.passengers"]
    assert "age" in replie(REPONSE_SUR_LES_AGES)
    assert not vt.exigence_de_periode("titanic.passengers").juge(REPONSE_SUR_LES_AGES)


# --- les quatre oracles de la SURFACE, durcis ---------------------------------
#
# Les quatre exigences déclarées faibles en C53 et laissées telles quelles. Elles
# tirent dans les deux sens, comme celle de la période : chacune doit refuser la
# réponse fausse RÉELLEMENT rendue ou plausible, et accepter la réponse juste
# RÉELLEMENT rendue — celle des campagnes du 2026-09-22, recopiée telle quelle.


def catalogue_a_deux_sources() -> VeriteTerrain:
    """Le relevé du catalogue par défaut : deux sources, trois modèles.

    C'est celui sur lequel la batterie tourne, et les chiffres en viennent —
    891 passagers, 150 iris, 3 classes.
    """
    return VeriteTerrain(
        sources=("titanic", "iris"),
        datasets=("california_housing", "iris", "titanic"),
        tables={"titanic": ("classes", "passengers"), "iris": ("iris",)},
        colonnes={
            "titanic.classes": ("class_id", "level", "label"),
            "titanic.passengers": ("passenger_id", "name", "age", "class_id"),
            "iris.iris": ("sepal_length", "sepal_width", "petal_length", "petal_width"),
        },
        temporelles={"titanic.classes": (), "titanic.passengers": (), "iris.iris": ()},
        lignes={"titanic.classes": 3, "titanic.passengers": 891, "iris.iris": 150},
        features={"iris": ("sepal_length", "sepal_width", "petal_length", "petal_width")},
        ages_max=80.0,
        survivants=342,
        colonnes_a_trous=("age",),
        renvois={"titanic.passengers.class_id": "classes"},
    )


# La réponse JUSTE, servie par le système le 2026-09-22 : elle dit le renvoi.
REPONSE_SUR_LE_RENVOI = (
    "La colonne `class_id` de la table `passengers` est de type `INTEGER` et est "
    "obligatoire (`NOT NULL`). Elle est une **clé étrangère** qui référence "
    "`classes(class_id)`, ce dont le sens de la valeur se lit dans la table `classes`."
)


@pytest.mark.parametrize(
    "texte",
    [
        # la lecture du NOM de la colonne, sans une ligne de schéma
        "La colonne class_id donne les classes des passagers.",
        "class_id indique à quelle classe appartient le passager : 1re, 2e ou 3e classe.",
        # le mot y est, le renvoi est dans une AUTRE phrase que la table
        "Cette colonne est une clé étrangère. Les passagers ont trois classes.",
    ],
)
def test_le_mot_classes_ne_suffit_plus(texte: str) -> None:
    """« classes » est un mot français ordinaire, et l'oracle s'en contentait."""
    exigence = catalogue_a_deux_sources().exigence_de_renvoi("titanic.passengers.class_id")
    assert "classes" in replie(texte) or "classe" in replie(texte)
    assert not exigence.juge(texte)


@pytest.mark.parametrize(
    "texte",
    [
        REPONSE_SUR_LE_RENVOI,
        "`class_id` renvoie à la table `classes`.",
        "C'est la clé étrangère vers classes.",
        "La table classes est jointe par cette colonne.",
        "Elle référence classes(class_id).",
    ],
)
def test_le_renvoi_est_reconnu_quelle_qu_en_soit_l_ecriture(texte: str) -> None:
    """Quatre radicaux et une phrase : aucune de ces cinq écritures n'est listée."""
    exigence = catalogue_a_deux_sources().exigence_de_renvoi("titanic.passengers.class_id")
    assert exigence.juge(texte)


def test_le_renvoi_se_lit_dans_la_phrase_et_pas_dans_le_texte() -> None:
    """La portée est la phrase — deux mots à dix lignes d'écart n'affirment rien."""
    assert porte_un_renvoi("C'est une clé étrangère. Il y a trois classes.", "classes") is False
    assert porte_un_renvoi("C'est une clé étrangère vers classes.", "classes") is True


def test_un_nombre_n_est_pas_une_sous_chaine() -> None:
    """« 342 » portait « 3 », et 3 est le nombre de classes : l'oracle passait."""
    exigence = catalogue_a_deux_sources().exigence_de_volumetrie()
    assert not exigence.juge("342 passagers ont survécu au naufrage.")
    assert not exigence.juge("Mes données pèsent 8915 octets.")
    assert exigence.juge("La table passengers contient 891 lignes.")
    assert exigence.juge("iris fait 150 lignes, et titanic 891.")


def test_un_nom_de_modele_ne_vaut_pas_un_jeu_d_attributs() -> None:
    """Ces noms sont AUSSI des noms de sources, et le catalogue est dans tous les prompts."""
    exigence = catalogue_a_deux_sources().exigence_d_attributs()
    assert not exigence.juge("Je peux travailler sur la source titanic.")
    assert not exigence.juge("iris, par exemple.")
    # les attributs d'un modèle, en entier
    assert exigence.juge("Il me faut sepal_length, sepal_width, petal_length et petal_width.")
    # ou le choix entre TOUS les modèles
    assert exigence.juge("Pour quel modèle : california_housing, iris ou titanic ?")


def test_un_choix_ne_deroule_pas_les_fiches() -> None:
    """Faire choisir tient en une ligne ; les volumes sont la signature de l'inventaire."""
    exigence = catalogue_a_deux_sources().exigence_de_choix_bref("titanic", "iris")
    assert exigence.juge("Sur quelle source veux-tu travailler : titanic, iris ?")
    assert not exigence.juge(
        "J'ai accès à 2 sources : titanic (891 lignes) et iris (150 lignes). "
        "Sur laquelle veux-tu travailler ?"
    )
    # et un choix qui n'en nomme qu'une ne fait choisir personne
    assert not exigence.juge("Veux-tu travailler sur titanic ?")


def test_le_plancher_des_dates_se_reconnait_a_l_inventaire() -> None:
    """Les deux témoins de C53 : nommer TOUTES les sources, c'est avoir servi l'inventaire."""
    exigence = catalogue_a_deux_sources().exigence_hors_de_l_inventaire()
    assert exigence.juge("Le dataset `iris` compte 150 lignes, sans période couverte.")
    assert not exigence.juge(
        "J'ai accès à 2 source(s) : titanic (postgres) et iris (file). "
        "Aucune ne porte de colonne de date."
    )


def test_une_clarification_admise_reste_jugee_sur_ce_qu_elle_porte() -> None:
    """La marche corrigée : `clarification_admise` autorise la question, pas le vide.

    Le cas est celui de `volumetrie-globale` — « quelle est la taille de tes
    données ? ». Une clarification qui énumère les sources était comptée juste
    sans qu'on lise ce qu'elle portait ; elle doit encore porter un compte de
    lignes, puisque c'est ce qu'on lui demandait.
    """
    question = QuestionMeta(
        "volumetrie-globale",
        "volumétrie",
        "Quelle est la taille de tes données ?",
        exigence=catalogue_a_deux_sources().exigence_de_volumetrie(),
        clarification_admise=("titanic", "iris"),
    )
    nue = "Sur quelle source veux-tu travailler : titanic, iris ?"
    chiffree = "titanic fait 891 lignes, iris 150. Sur laquelle veux-tu travailler ?"
    assert classer(question, nue, None, "clarification", None)[0] == "a_cote"
    assert classer(question, chiffree, None, "clarification", None)[0] == "correct"
    # une clarification qui n'énumère pas les choix reste « à côté », comme avant
    assert classer(question, "Précise ta demande.", None, "clarification", None) == (
        "a_cote",
        ["clarification renvoyée au lieu d'une réponse"],
    )
