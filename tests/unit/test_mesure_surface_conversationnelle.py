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
    constate_l_absence_de_date,
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
