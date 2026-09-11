"""Les faits d'une source sont LUS dedans, ou ne sont pas rendus.

Le catalogue déclare un nom, un type et une phrase écrite à la main. Ça suffit
pour router, pas pour choisir : « employes » et « titanic » se ressemblent sur
trois lignes de YAML. Ce module vérifie les trois faits qui les distinguent —
tables, lignes, période — et surtout la propriété qui les rend dignes de
confiance : **rien n'est rendu qui n'ait été lu**.

C'est la reprise, par une autre porte, du défaut corrigé par ``acfd8f5``
(« décris le dataset iris » répondu de mémoire, avec zéro requête). Une
volumétrie devinée du nom du fichier serait le même défaut sous un autre nom :
d'où le test de la source injoignable, qui doit rendre la RAISON de son silence
et aucun chiffre.
"""

from pathlib import Path

from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.agents.retrieval.faits import (
    FaitsDeSource,
    RelevesDuCatalogue,
    relever,
)

VENTES = """date_vente,client,montant
2024-01-15,alice,100
2024-06-02,bob,250
2024-11-30,carole,80
"""

SANS_DATE = "a,b\n1,2\n3,4\n"


def source(tmp_path: Path, nom: str, contenu: str) -> FileSource:
    chemin = tmp_path / f"{nom}.csv"
    chemin.write_text(contenu, encoding="utf-8")
    return FileSource(name=nom, path=chemin, description=f"La source {nom}.")


def test_le_releve_compte_les_tables_et_les_lignes(tmp_path: Path):
    faits = relever(source(tmp_path, "ventes", VENTES))

    assert faits.lu
    assert faits.tables == 1
    assert faits.lignes == 3
    assert faits.lignes_par_table == {"ventes": 3}


def test_la_periode_vient_d_un_min_max_sur_la_colonne_de_date(tmp_path: Path):
    """La période couverte, et la colonne d'où elle sort — nommée, parce qu'une
    source peut en porter plusieurs et que le lecteur doit savoir laquelle a
    été lue."""
    faits = relever(source(tmp_path, "ventes", VENTES))

    assert faits.periode is not None
    assert faits.periode.debut == "2024-01-15"
    assert faits.periode.fin == "2024-11-30"
    assert faits.periode.colonne == "date_vente"
    assert "du 2024-01-15 au 2024-11-30" in faits.en_clair()


def test_une_source_sans_colonne_de_date_n_en_invente_pas(tmp_path: Path):
    """« S'il existe une colonne de date » : sinon il n'y a pas de période, et
    c'est une réponse, pas un manque à combler."""
    faits = relever(source(tmp_path, "plat", SANS_DATE))

    assert faits.periode is None
    assert "période" not in faits.en_clair()
    assert "2 ligne(s)" in faits.en_clair()


def test_une_source_injoignable_dit_pourquoi_et_ne_chiffre_rien(tmp_path: Path):
    """La propriété qui compte : aucun chiffre inventé pour combler un silence.

    Et l'inventaire ne tombe pas avec elle — c'est précisément quand on ne sait
    pas encore quoi demander qu'on demande un inventaire.
    """
    faits = relever(FileSource(name="envolee", path=tmp_path / "absent.csv"))

    assert not faits.lu
    assert faits.lignes_par_table == {}
    assert "volumétrie non relevée" in faits.en_clair()


def test_une_source_vide_n_est_pas_une_source_illisible(tmp_path: Path):
    """« 0 ligne » est une information ; « je n'ai pas pu ouvrir » en est une
    autre. Les confondre ferait passer une panne pour un fait."""
    faits = relever(source(tmp_path, "vide", "a,b\n"))

    assert faits.lu
    assert faits.lignes == 0
    assert "0 ligne(s)" in faits.en_clair()


def test_le_releve_n_est_fait_qu_une_fois_par_source(tmp_path: Path):
    """Le cache, vérifié sur le disque plutôt que sur un compteur d'appels : le
    fichier est effacé après le premier relevé, et le second rend la même
    chose. S'il relisait, il rendrait un échec."""
    catalogue = Catalog(sources=[source(tmp_path, "ventes", VENTES)])
    releves = RelevesDuCatalogue(catalogue)

    premier = releves.de("ventes")
    (tmp_path / "ventes.csv").unlink()
    second = releves.de("ventes")

    assert premier.lignes == 3
    assert second == premier


def test_une_source_inconnue_du_catalogue_ne_rend_rien(tmp_path: Path):
    releves = RelevesDuCatalogue(Catalog(sources=[source(tmp_path, "ventes", VENTES)]))

    assert releves.de("jamais-declaree") is None


def test_tous_rend_les_sources_dans_l_ordre_du_catalogue(tmp_path: Path):
    """L'ordre du YAML ne décide plus du comportement de l'agent, mais il reste
    celui dans lequel l'inventaire se lit : celui que l'exploitant a écrit."""
    catalogue = Catalog(
        sources=[source(tmp_path, "ventes", VENTES), source(tmp_path, "plat", SANS_DATE)]
    )

    assert list(RelevesDuCatalogue(catalogue).tous()) == ["ventes", "plat"]


def test_des_faits_sans_table_le_disent(tmp_path: Path):
    """Un cas qui n'existe pas sur un CSV mais qu'une base sans table produirait."""
    assert FaitsDeSource(nom="vide").en_clair() == "aucune table"


def test_un_classeur_excel_compte_ses_feuilles_comme_des_tables(tmp_path: Path):
    """Un classeur est une source multi-tables : le relevé doit compter par
    feuille, pas rendre un total sans structure."""
    import pandas as pd

    chemin = tmp_path / "classeur.xlsx"
    with pd.ExcelWriter(chemin) as classeur:
        pd.DataFrame({"a": [1, 2, 3]}).to_excel(classeur, sheet_name="mesures", index=False)
        pd.DataFrame({"b": [9]}).to_excel(classeur, sheet_name="postes", index=False)

    faits = relever(FileSource(name="classeur", path=chemin))

    assert faits.tables == 2
    assert faits.lignes_par_table == {"mesures": 3, "postes": 1}


def test_une_date_sans_heure_ne_traine_pas_un_minuit(tmp_path: Path):
    """Une colonne de dates d'un classeur ressort en horodatage : « minuit pile »
    n'est pas une information, c'est le type de la colonne qui transparaît."""
    import pandas as pd

    chemin = tmp_path / "dates.xlsx"
    pd.DataFrame({"jour": pd.to_datetime(["2024-01-01", "2024-12-28"])}).to_excel(
        chemin, sheet_name="jours", index=False
    )

    faits = relever(FileSource(name="dates", path=chemin))

    assert faits.periode.debut == "2024-01-01"
    assert faits.periode.fin == "2024-12-28"


def test_une_heure_reelle_est_gardee(tmp_path: Path):
    """Le pendant du test précédent : on retire du bruit, pas de l'information.

    L'horodatage garde sa forme ISO, séparateur compris — c'est celle que rend
    la couche SQL, et la normaliser davantage serait réécrire une donnée.
    """
    chemin = tmp_path / "horodates.csv"
    chemin.write_text("instant\n2024-01-01 08:30:00\n2024-01-01 19:45:00\n", encoding="utf-8")

    faits = relever(FileSource(name="horodates", path=chemin))

    assert faits.periode.debut == "2024-01-01T08:30:00"
    assert faits.periode.fin == "2024-01-01T19:45:00"
