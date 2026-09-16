"""Ce que le catalogue de démonstration doit tenir, sans rien semer.

Le catalogue de `sources/demonstration/` est engendré : sa base Postgres, sa
base DuckDB et son classeur n'existent pas sur une machine qui vient de cloner
le dépôt, et n'existent pas non plus en CI. Ces tests ne les demandent donc
jamais. Ils tiennent ce qui EST versionné, et qui se casserait sans bruit :

- la déclaration elle-même — cinq sources, les trois types, un dictionnaire
  chacune, et une désignation de colonne de date là où il en faut une ;
- les deux CSV, qui sont des oracles : leurs comptes, et la correspondance
  libellé → code qui rattache la main courante au reste du catalogue ;
- le fait que les gros fichiers engendrés restent HORS du dépôt ;
- le gel du classeur, qui est ce qui rend le semis rejouable à l'octet près.

Ce qu'ils ne tiennent pas, et qui se mesure ailleurs (cf.
`docs/sources-de-demonstration.md`) : les réponses de l'agent. Elles demandent
un serveur LLM, et ce n'est pas à la suite unitaire de les attendre.
"""

from __future__ import annotations

import csv
import io
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from seed_catalogue_demonstration import (
    NB_INTERVENTIONS,
    NB_STATIONS_ACTIVES,
    NB_STATIONS_RETIREES,
    figer_le_classeur,
)

from data_analyst_agent.agents.retrieval.catalog import load_catalog, open_source

RACINE = Path(__file__).resolve().parents[2]
DOSSIER = RACINE / "sources" / "demonstration"
CATALOGUE = DOSSIER / "catalogue.yaml"

# Les cinq sources, et ce que chacune déclare. Écrit ici plutôt que relu du
# YAML : un test qui relit le fichier qu'il vérifie ne vérifie rien.
DECLARATIONS = {
    "exploitation": ("postgres", "sessions.debut_session"),
    "telemetrie": ("duckdb", "releves_puissance.horodatage"),
    "interventions": ("file", "date_signalement"),
    "referentiel": ("file", None),
    "facturation": ("file", "factures.date_emission"),
}


@pytest.fixture(scope="module")
def catalogue():
    return load_catalog(CATALOGUE)


def lire_csv(nom: str) -> list[dict[str, str]]:
    with (DOSSIER / nom).open(encoding="utf-8", newline="") as fichier:
        return list(csv.DictReader(fichier))


# --------------------------------------------------------------------------
# La déclaration
# --------------------------------------------------------------------------


def test_les_cinq_sources_couvrent_les_trois_types(catalogue):
    """Trois types dans un seul catalogue : c'est ce qu'il est censé démontrer."""
    types = {s.name: s.type for s in catalogue.sources}
    assert types == {nom: type_ for nom, (type_, _) in DECLARATIONS.items()}
    assert set(types.values()) == {"postgres", "file", "duckdb"}


def test_chaque_source_declare_son_dictionnaire(catalogue):
    """Sans dictionnaire, une colonne piégeuse n'est qu'un nom et un type.

    Le fichier est LU, pas seulement déclaré : un chemin qui ne résout pas
    passerait la validation du catalogue et ne se verrait qu'à la première
    question de sens posée par un utilisateur.
    """
    for source in catalogue.sources:
        assert source.dictionary is not None, f"{source.name} n'a pas de dictionnaire"
        texte = source.dictionary_text()
        assert texte, f"dictionnaire vide : {source.name}"
        assert source.name in texte, f"dictionnaire hors sujet : {source.name}"


def test_la_colonne_de_date_est_designee_la_ou_il_y_en_a_plusieurs(catalogue):
    """`referentiel` n'en désigne pas, et c'est le seul cas : il n'a qu'une date.

    La désignation doit se lire là où elle sert. L'écrire partout par précaution
    en ferait une formalité qu'on cesserait de relire.
    """
    for source in catalogue.sources:
        attendue = DECLARATIONS[source.name][1]
        assert source.date_reference == attendue, f"date_reference de {source.name}"


def test_la_main_courante_est_la_seule_a_ne_pas_porter_le_code():
    """Le verrou de source n'a de sens que si plusieurs sources savent répondre.

    `code_station` est la colonne partagée du catalogue. `interventions` est la
    seule à ne pas la porter — elle a le libellé à la place, et c'est le piège
    nº 1. Lu dans les en-têtes des fichiers, pas dans la prose du dictionnaire :
    une prose peut décrire un fichier qui a changé.
    """
    assert "code_station" in lire_csv("referentiel.csv")[0]
    entetes = lire_csv("interventions.csv")[0]
    assert "code_station" not in entetes
    assert "station_libelle" in entetes


# --------------------------------------------------------------------------
# Les deux CSV, qui sont des oracles
# --------------------------------------------------------------------------


def test_le_referentiel_porte_les_stations_demontees():
    """150 stations ici, 120 dans `exploitation` — c'est l'écart qu'on éprouve."""
    lignes = lire_csv("referentiel.csv")
    assert len(lignes) == NB_STATIONS_ACTIVES + NB_STATIONS_RETIREES == 150
    statuts = [ligne["statut"] for ligne in lignes]
    assert statuts.count("ACT") == NB_STATIONS_ACTIVES
    assert statuts.count("RET") == NB_STATIONS_RETIREES


def test_le_libelle_de_station_est_unique():
    """La table de correspondance n'en est une que si sa clé ne se répète pas.

    Le premier tirage la répétait — 116 libellés pour 150 stations — et la
    jointure depuis `interventions` multipliait les lignes au lieu de les
    rattacher. Le semis le refuse désormais ; ce test le tient côté fichier.
    """
    libelles = [ligne["libelle_station"] for ligne in lire_csv("referentiel.csv")]
    assert len(set(libelles)) == len(libelles)


def test_la_main_courante_se_rattache_par_le_libelle():
    """Le piège nº 1 doit être franchissable : un libellé, un code, toujours.

    Une seule intervention sans correspondance rendrait la source injoignable
    au reste du catalogue, et le piège deviendrait un défaut.
    """
    connus = {ligne["libelle_station"] for ligne in lire_csv("referentiel.csv")}
    interventions = lire_csv("interventions.csv")
    assert len(interventions) == NB_INTERVENTIONS
    orphelines = {i["station_libelle"] for i in interventions} - connus
    assert not orphelines, f"libellés sans correspondance : {sorted(orphelines)[:5]}"


def test_la_main_courante_porte_des_stations_demontees():
    """L'autre moitié du piège : joindre par le code en perdrait une part.

    Silencieusement — le résultat resterait un nombre plausible, simplement trop
    petit. C'est ce qui rend le passage par `referentiel` obligatoire et pas
    seulement recommandé.
    """
    retirees = {
        ligne["libelle_station"]
        for ligne in lire_csv("referentiel.csv")
        if ligne["statut"] == "RET"
    }
    concernees = [i for i in lire_csv("interventions.csv") if i["station_libelle"] in retirees]
    assert concernees, "aucune intervention sur station démontée : le piège ne se voit plus"


def test_la_sentinelle_de_duree_est_presente_et_documentee(catalogue):
    """Une valeur sentinelle qui ne serait pas dans les données ne piègerait rien."""
    durees = [int(i["duree_indispo_min"]) for i in lire_csv("interventions.csv")]
    assert any(d == -1 for d in durees)
    assert all(d == -1 or d > 0 for d in durees)
    assert "-1" in catalogue.get("interventions").dictionary_text()


def test_le_code_de_statut_dit_quel_filtre_pour_quelle_question(catalogue):
    """Le dictionnaire a un SECOND lecteur : celui qui écrit le SQL.

    Il a été écrit pour une personne. Depuis que son texte entre dans le prompt
    de l'agent de récupération, il décide aussi de ce que la requête filtre — et
    une ambiguïté qu'un humain levait tout seul devient un chiffre faux.

    Mesuré : tant que le piège nº 1 énonçait « le nombre de recharges réelles
    est `WHERE statut = 'T'` » en tête et rangeait le contre-cas dans un
    paragraphe de fin, le modèle filtrait AUSSI les sommes d'énergie — 0 fois
    sur 3 juste sur « quelle énergie totale a été délivrée ? », 1 730 823,72 au
    lieu de 1 757 519,23 — et sur les deux moteurs. Trois formulations d'en-tête
    successives n'y ont rien changé ; dire dans le dictionnaire QUEL FILTRE POUR
    QUELLE QUESTION a suffi, 3/3 sur les deux moteurs.

    Ce test tient les deux bords de cette précision. Il ne pèse pas une
    tournure : il exige que le dictionnaire nomme le cas où l'on filtre ET le
    cas où l'on ne filtre pas, sans quoi il ne lève plus l'ambiguïté.
    """
    texte = catalogue.get("exploitation").dictionary_text()
    piege = texte[texte.index("### 1.") :]
    assert "WHERE statut = 'T'" in piege, "le cas filtré n'est plus énoncé"
    assert "AUCUN" in piege, "le cas NON filtré n'est plus énoncé aussi nettement"
    # Les deux chiffres que l'ambiguïté fait diverger, l'un et l'autre présents :
    # ce sont eux qui disent qu'on parle bien de deux mesures et pas d'une.
    for chiffre in ("48 000", "42 281", "1 757 519,23", "1 730 823,72"):
        assert chiffre in piege, f"{chiffre} n'est plus cité : la divergence ne se lit plus"


# --------------------------------------------------------------------------
# Ce que le socle lit vraiment dans les sources versionnées
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "table", "colonne"),
    [("interventions", "interventions", "date_signalement"), ("referentiel", "referentiel", None)],
)
def test_les_sources_fichier_s_ouvrent_et_portent_la_colonne_designee(
    catalogue, source, table, colonne
):
    """La désignation doit viser une colonne qui EXISTE dans le schéma lu.

    Une `date_reference` fautive ne lève rien au chargement du catalogue : elle
    se rattrape en silence sur la première colonne de date venue, et la période
    affichée devient juste-mais-pas-celle-qu'on-voulait. Seule la confrontation
    au schéma réel le dit.
    """
    with closing(open_source(catalogue.get(source))) as adaptateur:
        schema = adaptateur.schema()
    assert schema.table_names() == [table]
    if colonne is not None:
        assert colonne in [c.name for c in schema.tables[0].columns]


# --------------------------------------------------------------------------
# Ce qui est engendré reste dehors, et se réengendre à l'identique
# --------------------------------------------------------------------------


@pytest.mark.parametrize("engendre", ["telemetrie.duckdb", "facturation.xlsx"])
def test_les_fichiers_engendres_sont_ignores(engendre):
    """18 Mo de base et un binaire de 230 ko n'ont rien à faire dans l'historique.

    Le `.gitignore` du dossier est la seule chose qui les en tient à l'écart, et
    il est à un `git add -f` de céder. Ce test rend le jour où il aura cédé
    visible autrement qu'à la revue.
    """
    ignores = (DOSSIER / ".gitignore").read_text(encoding="utf-8")
    assert engendre in ignores


@pytest.mark.parametrize("versionne", ["referentiel.csv", "interventions.csv", "catalogue.yaml"])
def test_ce_qui_est_versionne_est_present(versionne):
    assert (DOSSIER / versionne).is_file()


def test_le_classeur_fige_ne_depend_plus_de_l_heure(tmp_path):
    """Deux écritures du même contenu, deux instants, mêmes octets.

    C'est ce qui permet de dire « le semis rejoué rend les mêmes octets » d'un
    classeur Excel — sans ce gel, un `.xlsx` porte l'heure de sa création dans
    `docProps/core.xml` ET dans la date de chaque entrée de son zip, et deux
    exécutions diffèrent sans que rien n'ait changé.
    """
    coeur = (
        '<?xml version="1.0"?><cp:coreProperties xmlns:dcterms="x">'
        "<dcterms:modified>{instant}</dcterms:modified></cp:coreProperties>"
    )

    def ecrire(chemin: Path, instant: str, horodatage: tuple[int, int, int, int, int, int]) -> None:
        with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as archive:
            for nom, contenu in (
                ("xl/worksheets/sheet1.xml", b"<sheet/>"),
                ("docProps/core.xml", coeur.format(instant=instant).encode()),
            ):
                archive.writestr(zipfile.ZipInfo(nom, date_time=horodatage), contenu)

    premier, second = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    ecrire(premier, "2026-09-15T08:00:00Z", (2026, 9, 15, 8, 0, 0))
    ecrire(second, "2027-03-02T21:44:11Z", (2027, 3, 2, 21, 44, 10))
    assert premier.read_bytes() != second.read_bytes()

    figer_le_classeur(premier)
    figer_le_classeur(second)
    assert premier.read_bytes() == second.read_bytes()

    # Et le contenu utile a survécu au gel : figer ne doit pas réécrire les
    # feuilles, seulement ce qui datait le fichier.
    with zipfile.ZipFile(io.BytesIO(premier.read_bytes())) as archive:
        assert archive.read("xl/worksheets/sheet1.xml") == b"<sheet/>"
