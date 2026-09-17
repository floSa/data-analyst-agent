"""Ce que le catalogue métier doit tenir, sans rien semer.

Le catalogue de `sources/metier/` est engendré : sa base Postgres, sa base
DuckDB, son classeur et ses deux CSV de référence n'existent pas sur une machine
qui vient de cloner le dépôt, et n'existent pas non plus en CI. Ces tests ne les
demandent donc jamais. Ils tiennent ce qui EST versionné, et qui se casserait
sans bruit :

- la déclaration — cinq sources, les trois types, un dictionnaire chacune, une
  désignation de colonne de date là où il en faut une, et les `features` sans
  lesquelles la prédiction devinerait ses colonnes ;
- les trois pièges, chacun dans le dictionnaire de SA source, chacun nommant le
  chiffre juste ET le chiffre faux — sans les deux, la divergence ne se lit plus ;
- le budget des dictionnaires, qui est une contrainte de produit et non un
  confort : au-delà, le texte est amputé par sections, et chez nous les pièges
  sont en fin de document ;
- le fait que les fichiers engendrés et recopiés restent HORS du dépôt.

Ce qu'ils ne tiennent pas, et qui se mesure ailleurs (cf. `docs/sources-metier.md`) :
les réponses de l'agent. Elles demandent un serveur LLM, et ce n'est pas à la
suite unitaire de les attendre.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from seed_catalogue_metier import CLIENTS, ENTREPOTS, MACHINES, PRODUITS

from data_analyst_agent.agents.retrieval.catalog import load_catalog
from data_analyst_agent.config import get_settings

RACINE = Path(__file__).resolve().parents[2]
DOSSIER = RACINE / "sources" / "metier"
CATALOGUE = DOSSIER / "catalogue.yaml"

# Les cinq sources, et ce que chacune déclare. Écrit ici plutôt que relu du
# YAML : un test qui relit le fichier qu'il vérifie ne vérifie rien.
DECLARATIONS = {
    "ventes": ("postgres", "commandes.date_commande"),
    "production": ("duckdb", "ordres_fabrication.date_lancement"),
    "stocks": ("file", "mouvements.date_mouvement"),
    "iris": ("file", None),
    "titanic": ("file", None),
}


@pytest.fixture(scope="module")
def catalogue():
    return load_catalog(CATALOGUE)


# --------------------------------------------------------------------------
# La déclaration
# --------------------------------------------------------------------------


def test_les_cinq_sources_couvrent_les_trois_types(catalogue):
    """Trois types dans un seul catalogue : c'est ce qu'il est censé montrer."""
    types = {s.name: s.type for s in catalogue.sources}
    assert types == {nom: type_ for nom, (type_, _) in DECLARATIONS.items()}
    assert set(types.values()) == {"postgres", "file", "duckdb"}


def test_chaque_source_declare_son_dictionnaire(catalogue):
    """Le fichier est LU, pas seulement déclaré.

    Un chemin qui ne résout pas passerait la validation du catalogue et ne se
    verrait qu'à la première question de sens posée par un utilisateur.
    """
    for source in catalogue.sources:
        assert source.dictionary is not None, f"{source.name} n'a pas de dictionnaire"
        texte = source.dictionary_text()
        assert texte, f"dictionnaire vide : {source.name}"
        assert source.name in texte, f"dictionnaire hors sujet : {source.name}"


def test_la_colonne_de_date_est_designee_la_ou_il_y_en_a_plusieurs(catalogue):
    """`iris` et `titanic` n'en désignent pas : ils n'ont aucune colonne de date.

    Les trois sources métier en portent deux chacune, et c'est exactement le cas
    où la désignation est un choix de métier et non une formalité.
    """
    for source in catalogue.sources:
        assert source.date_reference == DECLARATIONS[source.name][1], (
            f"date_reference de {source.name}"
        )


def test_les_jeux_de_reference_declarent_leurs_features(catalogue):
    """Sans `features`, `fetch_then_predict` devinerait la colonne.

    Et pour `titanic`, la correspondance n'est PAS l'identité : le fichier porte
    les en-têtes CamelCase d'origine, que le modèle du registre n'attend pas.
    C'est le cas précis que `features` existe pour couvrir, et il ne se voit
    qu'ici.
    """
    iris = {f: d.column for f, d in catalogue.get("iris").features["iris"].items()}
    assert iris == {c: c for c in iris}, "la correspondance d'iris n'est plus l'identité"

    titanic = {f: d.column for f, d in catalogue.get("titanic").features["titanic"].items()}
    assert titanic["pclass"] == "Pclass"
    assert titanic["sex"] == "Sex"
    assert set(titanic) == {"sex", "pclass", "age", "sibsp", "parch", "fare", "embarked"}
    assert all(k != v for k, v in titanic.items()), (
        "titanic ne renomme plus rien : en-têtes changés ?"
    )


def test_les_sources_metier_ne_declarent_pas_de_features(catalogue):
    """Aucun modèle du registre ne porte sur les vélos : rien à déclarer.

    Le dire ici évite qu'on en ajoute « par symétrie » avec iris et titanic :
    `features` refuse ce qui n'existe pas au schéma, et une déclaration vide de
    sens finirait par casser une requête sans rien apporter.
    """
    for nom in ("ventes", "production", "stocks"):
        assert not catalogue.get(nom).features, f"{nom} déclare des features sans modèle"


# --------------------------------------------------------------------------
# Les trois pièges, chacun dans le dictionnaire de sa source
# --------------------------------------------------------------------------


def test_le_piege_du_statut_dit_quel_filtre_pour_quelle_question(catalogue):
    """PIÈGE Nº 1 — un code de statut, filtré pour une mesure et pas pour une autre.

    Le test ne pèse pas une tournure : il exige que le dictionnaire nomme le cas
    où l'on filtre ET le cas où l'on ne filtre pas, plus les deux chiffres que
    l'ambiguïté fait diverger. Sans les quatre, il ne lève plus rien.
    """
    texte = catalogue.get("ventes").dictionary_text()
    piege = texte[texte.index("### 1.") : texte.index("### 2.")]
    assert "AUCUN" in piege, "le cas NON filtré n'est plus énoncé aussi nettement"
    assert "statut <> 'ANN'" in piege, "le cas filtré n'est plus énoncé"
    for chiffre in ("180", "1 496 743,00", "1 636 093,00"):
        assert chiffre in piege, f"{chiffre} n'est plus cité : la divergence ne se lit plus"


def test_le_piege_de_la_sentinelle_nomme_aussi_ce_qui_lui_ressemble(catalogue):
    """PIÈGE Nº 2 — `-1` n'est pas une durée, et `0` en est une.

    Nommer la sentinelle sans nommer ce qui lui ressemble fait sur-corriger :
    l'agent écarte « les valeurs négatives ou nulles » et fausse la moyenne dans
    l'autre sens. Les deux moitiés sont donc exigées.
    """
    texte = catalogue.get("production").dictionary_text()
    piege = texte[texte.index("### 1.") : texte.index("### 2.")]
    assert "-1" in piege
    assert "sentinelle" in piege.lower()
    assert "duree_minutes >= 0" in piege, "le filtre juste n'est plus donné"
    assert "`0` lui ressemble" in piege, "le contre-cas du zéro n'est plus énoncé"
    for chiffre in ("202,10", "175,99"):
        assert chiffre in piege, f"{chiffre} n'est plus cité"


def test_le_piege_de_la_colonne_signee_oppose_le_volume_et_la_variation(catalogue):
    """PIÈGE Nº 3 — `sum(quantite)` répond, mais à une autre question.

    Les deux chiffres doivent être là, et opposés l'un à l'autre : c'est la
    seule façon de dire qu'il s'agit de deux questions et non d'une erreur.
    """
    texte = catalogue.get("stocks").dictionary_text()
    piege = texte[texte.index("### 1.") : texte.index("### 2.")]
    assert "sens = 'SOR'" in piege
    assert "variation" in piege.lower(), "l'autre question n'est plus nommée"
    for chiffre in ("2 279", "4 293", "6 572"):
        assert chiffre in piege, f"{chiffre} n'est plus cité"


def test_les_trois_pieges_sont_de_familles_differentes(catalogue):
    """Trois fois le même piège n'éprouverait qu'une seule chose.

    Le vocabulaire de chacun est donc exigé ABSENT des deux autres : si le
    catalogue dérivait vers trois sentinelles, ce test le dirait avant qu'une
    campagne ne le suggère.
    """
    textes = {
        nom: catalogue.get(nom).dictionary_text() for nom in ("ventes", "production", "stocks")
    }
    assert "sentinelle" in textes["production"].lower()
    assert "sentinelle" not in textes["ventes"].lower()
    assert "signé" in textes["stocks"].lower()
    assert "signé" not in textes["production"].lower()
    assert "statut" not in textes["stocks"].lower()


def test_le_recoupement_par_le_produit_est_ecrit_des_deux_cotes(catalogue):
    """« Combien de produits ? » doit avoir deux réponses justes, et les deux dites.

    C'est ce qui donne au verrou de source quelque chose à protéger. Si une
    seule des deux sources portait l'écart, l'autre laisserait croire à une
    donnée manquante.
    """
    assert "12" in catalogue.get("ventes").dictionary_text()
    production = catalogue.get("production").dictionary_text()
    assert "8" in production, "le 8 de production n'est plus dit"
    assert "12" in production, "l'écart avec ventes n'est plus dit côté production"
    assert "12" in catalogue.get("stocks").dictionary_text()


def test_les_dictionnaires_tiennent_sous_le_plafond(catalogue):
    """Au-delà du budget, le texte est amputé PAR SECTIONS, dans l'ordre.

    Et chez nous les pièges sont en fin de document : un dictionnaire qui
    déborde perd donc exactement la partie pour laquelle il existe. Le test le
    dit au commit, pas à la campagne suivante.
    """
    # `dictionary_max_chars` et non `retrieval_dictionary_max_chars` : le second
    # est l'ancien nom, qui vaut None tant que personne ne l'a posé. C'est le
    # premier qui porte le plafond effectif, 8 000 par défaut.
    plafond = get_settings().dictionary_max_chars
    for source in catalogue.sources:
        taille = len(source.dictionary_text())
        assert taille <= plafond, (
            f"{source.name} : {taille} caractères pour un plafond de {plafond}"
        )


# --------------------------------------------------------------------------
# Les volumes, et ce qui les rend citables de tête
# --------------------------------------------------------------------------


def test_les_douze_produits_se_partagent_en_huit_veles_et_quatre_accessoires():
    """L'écart 12 / 8 entre les sources ne vient d'aucun tirage : il vient d'ici."""
    assert len(PRODUITS) == 12
    codes = [p[0] for p in PRODUITS]
    assert len(set(codes)) == 12, "deux produits partagent un code"
    fabriques = [p[0] for p in PRODUITS if p[4]]
    achetes = [p[0] for p in PRODUITS if not p[4]]
    assert len(fabriques) == 8
    assert all(c.startswith("VEL-") for c in fabriques)
    assert len(achetes) == 4
    assert all(c.startswith("ACC-") for c in achetes)


@pytest.mark.parametrize(
    ("table", "attendu"), [(PRODUITS, 12), (CLIENTS, 18), (MACHINES, 9), (ENTREPOTS, 3)]
)
def test_les_volumes_tiennent_dans_la_tete(table, attendu):
    """Le critère de conception du catalogue, tenu par un test.

    « Une réponse fausse doit se voir SANS avoir à la vérifier » ne survit pas à
    une table de huit cents lignes. Ces plafonds sont bas exprès, et c'est la
    seule chose qui empêche ce catalogue de redevenir celui qu'il remplace.
    """
    assert len(table) == attendu


def test_les_codes_se_citent_de_memoire():
    """On doit pouvoir écrire un code dans une question sans l'avoir sous les yeux."""
    assert [m[0] for m in MACHINES] == [f"M-{i:03d}" for i in range(1, 10)]
    assert {e[0] for e in ENTREPOTS} == {"E-NAN", "E-LYO", "E-LIL"}
    for code, ville, _, _ in ENTREPOTS:
        assert code == f"E-{ville[:3].upper()}", f"{code} ne se devine plus depuis {ville}"


# --------------------------------------------------------------------------
# Les jeux de référence, qui sont recopiés et jamais engendrés
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("fichier", "lignes"), [("iris.csv", 150), ("titanic.csv", 891)])
def test_les_jeux_de_reference_sont_intacts_a_la_source(fichier, lignes):
    """Le semis les RECOPIE depuis `sources/` : c'est l'original qu'on tient ici.

    Les modifier — ne serait-ce que d'une ligne, ne serait-ce que pour « les
    mettre au thème » du fabricant de vélos — ferait mentir d'un coup la
    littérature, les modèles du registre et toutes les campagnes d'avant.
    """
    with (RACINE / "sources" / fichier).open(encoding="utf-8", newline="") as flux:
        assert len(list(csv.DictReader(flux))) == lignes


def test_le_dictionnaire_de_titanic_cite_les_chiffres_de_reference(catalogue):
    """Ce sont des oracles connus hors de cet agent : ils doivent rester justes."""
    texte = catalogue.get("titanic").dictionary_text()
    for chiffre in ("891", "342", "38,4", "177", "29,70"):
        assert chiffre in texte, f"{chiffre} n'est plus cité"


# --------------------------------------------------------------------------
# Ce qui est engendré ou recopié reste dehors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engendre", ["production.duckdb", "stocks.xlsx", "iris.csv", "titanic.csv"]
)
def test_les_fichiers_engendres_et_recopies_sont_ignores(engendre):
    """Deux vérités pour un même fichier finissent toujours par diverger.

    Les deux CSV de référence sont des copies OCTET POUR OCTET de fichiers déjà
    versionnés : les versionner une seconde fois ici n'ajouterait qu'une occasion
    de dérive. Le `.gitignore` est la seule chose qui les tient dehors, et il est
    à un `git add -f` de céder.
    """
    assert engendre in (DOSSIER / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "versionne",
    [
        "catalogue.yaml",
        "dictionnaires/ventes.md",
        "dictionnaires/production.md",
        "dictionnaires/stocks.md",
        "dictionnaires/iris.md",
        "dictionnaires/titanic.md",
    ],
)
def test_ce_qui_est_versionne_est_present(versionne):
    assert (DOSSIER / versionne).is_file()
