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

import time
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

import duckdb

from data_analyst_agent.agents.retrieval.catalog import (
    Catalog,
    DuckDBSource,
    FileSource,
    PostgresSource,
)
from data_analyst_agent.agents.retrieval.faits import (
    FaitsDeSource,
    ReglagesDuReleve,
    RelevesDuCatalogue,
    _estimation,
    relever,
)
from data_analyst_agent.agents.retrieval.sql import (
    ColumnInfo,
    QueryResult,
    SchemaInfo,
    TableInfo,
)
from data_analyst_agent.config import Settings

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


def test_une_colonne_de_date_sans_aucune_valeur_ne_donne_pas_de_periode():
    """Il y a bien une colonne de date, et elle ne dit rien.

    C'est encore une absence de période, et pas un intervalle vide à afficher.
    Le cas se produit sur une table qui déclare sa colonne — une base, où le
    type survit à l'absence de données. Un fichier, lui, ne peut pas le
    montrer : DuckDB devine le type sur les valeurs, et une colonne sans
    valeur n'est jamais devinée temporelle. D'où la table montée ici à la main
    plutôt qu'un classeur, qui ne prouverait rien.
    """
    import duckdb

    from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
    from data_analyst_agent.agents.retrieval.faits import _colonne_de_date, _periode

    connexion = duckdb.connect(":memory:")
    connexion.execute("CREATE TABLE jours (jour DATE)")
    with closing(DuckDBAdapter(connexion, ["jours"])) as adaptateur:
        assert _colonne_de_date(adaptateur.schema()).colonne == ("jours", "jour")

        assert _periode(adaptateur, "jours", "jour") is None


# --- la fraîcheur : un relevé se garde, il ne se fige pas ---------------------
#
# Le cache d'origine gardait le premier relevé jusqu'au redémarrage. Défaut
# observé en vrai le 2026-09-14 : Postgres arrêté au démarrage, la réponse
# annonçait « volumétrie non relevée » et continuait de l'annoncer alors que la
# base répondait de nouveau depuis plusieurs minutes. Les tests ci-dessous
# pilotent l'horloge plutôt que d'attendre — un quart d'heure de péremption ne
# se vérifie pas en dormant un quart d'heure.


class Horloge:
    """Une horloge monotone qu'on avance à la main."""

    def __init__(self) -> None:
        self.instant = 0.0

    def __call__(self) -> float:
        return self.instant

    def avance(self, secondes: float) -> None:
        self.instant += secondes


def test_une_source_revenue_cesse_detre_annoncee_injoignable(tmp_path: Path):
    """Le défaut du 2026-09-14, reproduit et corrigé : absente, puis revenue."""
    horloge = Horloge()
    chemin = tmp_path / "ventes.csv"
    catalogue = Catalog(sources=[FileSource(name="ventes", path=chemin)])
    releves = RelevesDuCatalogue(
        catalogue, ReglagesDuReleve(reprise=30.0, peremption=900.0), horloge
    )

    assert not releves.de("ventes").lu  # le fichier n'existe pas encore

    chemin.write_text(VENTES, encoding="utf-8")
    horloge.avance(29.0)
    assert not releves.de("ventes").lu  # toujours dans le délai de reprise

    horloge.avance(2.0)
    assert releves.de("ventes").lignes == 3


def test_un_releve_reussi_se_perime_et_se_refait(tmp_path: Path):
    """Une source qui grossit finit par se redire — au bout de la péremption."""
    horloge = Horloge()
    chemin = tmp_path / "ventes.csv"
    chemin.write_text(VENTES, encoding="utf-8")
    releves = RelevesDuCatalogue(
        Catalog(sources=[FileSource(name="ventes", path=chemin)]),
        ReglagesDuReleve(reprise=30.0, peremption=900.0),
        horloge,
    )

    assert releves.de("ventes").lignes == 3

    chemin.write_text(VENTES + "2024-12-31,denis,10\n", encoding="utf-8")
    horloge.avance(899.0)
    assert releves.de("ventes").lignes == 3  # encore frais

    horloge.avance(2.0)
    assert releves.de("ventes").lignes == 4


def test_la_source_injoignable_est_retentee_bien_avant_la_peremption(tmp_path: Path):
    """Les deux durées répondent à deux questions, et la panne est la plus pressée.

    Une seule durée pour les deux les répondrait mal toutes les deux : courte,
    elle recompte des millions de lignes pour rien ; longue, elle fait mentir la
    réponse pendant toute la session.
    """
    reglages = ReglagesDuReleve()

    assert reglages.reprise < reglages.peremption


def test_sans_peremption_le_releve_est_refait_a_chaque_demande(tmp_path: Path):
    """``0`` ne veut pas dire « pour toujours » : il veut dire « pas de cache »."""
    chemin = tmp_path / "ventes.csv"
    chemin.write_text(VENTES, encoding="utf-8")
    releves = RelevesDuCatalogue(
        Catalog(sources=[FileSource(name="ventes", path=chemin)]),
        ReglagesDuReleve(peremption=0.0, reprise=0.0),
    )

    assert releves.de("ventes").lignes == 3
    chemin.unlink()
    assert not releves.de("ventes").lu


def test_les_reglages_par_defaut_sont_ceux_du_parametrage():
    """Un seul jeu de valeurs : celui de ``Settings``, justifié là-bas."""
    assert ReglagesDuReleve.from_settings(Settings()) == ReglagesDuReleve()


# --- le bornage : une source muette ne retient pas l'inventaire ---------------


def test_une_source_qui_ne_repond_pas_est_abandonnee_au_delai():
    """Le cas qui n'a pas de message d'erreur : le TCP part et ne revient jamais.

    Sans délai, ``relever`` attendait le délai du système (mesuré : toujours
    bloqué au bout de 75 s) et tout l'inventaire attendait avec lui. Simulé ici
    par une source qui dort, pour ne pas faire dépendre un test du réseau.
    """

    class Endormie(FileSource):
        pass

    def dormir(_: object) -> None:
        time.sleep(30)

    with patch("data_analyst_agent.agents.retrieval.faits.open_source", dormir):
        debut = time.monotonic()
        faits = relever(
            Endormie(name="muette", path=Path("/inexistant")), ReglagesDuReleve(delai=0.2)
        )
        duree = time.monotonic() - debut

    assert duree < 5  # on n'a pas attendu les trente secondes
    assert not faits.lu
    assert "pas de réponse en moins de 0.2 s" in faits.en_clair()


def test_le_depassement_de_delai_se_dit_autrement_quun_refus(tmp_path: Path):
    """Les deux dégradent la ligne en injoignable ; seule la RAISON les sépare,
    et c'est elle qui dit quoi réparer."""
    absente = relever(FileSource(name="absente", path=tmp_path / "nulle-part.csv"))

    assert "la source n'a pas répondu" in absente.en_clair()


def test_sans_delai_le_releve_attend_ce_quil_faut(tmp_path: Path):
    """``0`` rend le comportement d'avant, pour qui le veut. Et sans fil du tout."""
    faits = relever(source(tmp_path, "ventes", VENTES), ReglagesDuReleve(delai=0.0))

    assert faits.lignes == 3


# --- l'approximation : un ordre de grandeur, annoncé comme tel ----------------
#
# Elle ne concerne que les moteurs où COMPTER coûte — Postgres. La doublure
# ci-dessous en joue un : la suite unitaire n'ouvre aucun serveur, et ce qu'on
# vérifie ici n'est pas le SQL de `pg_class` (c'est l'affaire de
# `scripts/mesure_releve_des_sources.py`, sur une vraie base de 5 M de lignes)
# mais la RÈGLE — au-dessus du seuil on estime, en dessous on compte, et ce qui
# est estimé se dit.


class FauxPostgres:
    """Un adaptateur qui répond comme Postgres, et qui compte ce qu'on lui demande."""

    dialect = "postgresql"

    def __init__(self, lignes: int, estimation: int | None = None) -> None:
        self.lignes = lignes
        self.estimation = lignes if estimation is None else estimation
        self.comptages = 0

    def schema(self) -> SchemaInfo:
        return SchemaInfo(
            dialect=self.dialect,
            tables=[TableInfo(name="mesures", columns=[ColumnInfo(name="id", type="INTEGER")])],
        )

    def run(self, query: str, max_rows: int = 200) -> QueryResult:
        if "pg_class" in query:
            return QueryResult(columns=["reltuples"], rows=[[self.estimation]])
        self.comptages += 1
        return QueryResult(columns=["n"], rows=[[self.lignes]])

    def close(self) -> None:
        pass


@contextmanager
def source_postgres(adaptateur: FauxPostgres):
    """``relever`` ouvre la source par ``open_source`` : on lui substitue la doublure."""
    with patch("data_analyst_agent.agents.retrieval.faits.open_source", lambda _: adaptateur):
        yield PostgresSource(name="volumetrie", dsn="postgresql+pg8000://u:p@h:5432/b")


def test_une_grande_table_est_estimee_et_le_dit():
    """Au-dessus du seuil, le moteur donne son ordre de grandeur — et la phrase
    rendue porte un ``~``, parce qu'un ordre de grandeur affiché comme un compte
    exact est le petit mensonge que ce module existe pour empêcher."""
    adaptateur = FauxPostgres(lignes=50_000)
    with source_postgres(adaptateur) as source:
        faits = relever(source, ReglagesDuReleve(seuil_approximation=1000))

    assert faits.estimees == ["mesures"]
    assert adaptateur.comptages == 0  # la table n'a PAS été balayée
    assert "mesures : ~50000" in faits.en_clair()
    assert "~50000 ligne(s)" in faits.en_clair()


def test_une_petite_table_reste_comptee_exactement():
    """En dessous du seuil, le comptage est de toute façon gratuit : on ne
    dégrade jamais gratuitement."""
    adaptateur = FauxPostgres(lignes=10)
    with source_postgres(adaptateur) as source:
        faits = relever(source, ReglagesDuReleve(seuil_approximation=1000))

    assert faits.estimees == []
    assert adaptateur.comptages == 1
    assert "mesures : 10" in faits.en_clair()


def test_un_seuil_nul_compte_tout():
    adaptateur = FauxPostgres(lignes=50_000)
    with source_postgres(adaptateur) as source:
        faits = relever(source, ReglagesDuReleve(seuil_approximation=0))

    assert faits.estimees == []
    assert "~" not in faits.en_clair()


def test_une_table_jamais_analysee_est_comptee():
    """``reltuples`` vaut -1 tant qu'aucun ``ANALYZE`` n'est passé. Sans cette
    garde, une table volumineuse mais neuve s'afficherait « ~-1 »."""
    adaptateur = FauxPostgres(lignes=50_000, estimation=-1)
    with source_postgres(adaptateur) as source:
        faits = relever(source, ReglagesDuReleve(seuil_approximation=1000))

    assert faits.estimees == []
    assert faits.lignes_par_table == {"mesures": 50_000}


def test_duckdb_n_est_pas_estime_parce_quil_compte_pour_rien(tmp_path: Path):
    """La décision mesurée : DuckDB répond ``count(*)`` depuis ses métadonnées.

    L'y estimer décorerait d'un « ~ » un chiffre exact et gratuit. Le seuil ne
    change donc rien sur une base DuckDB, si grosse soit-elle.
    """
    chemin = tmp_path / "gros.duckdb"
    connexion = duckdb.connect(str(chemin))
    connexion.execute("CREATE TABLE mesures AS SELECT i AS id FROM range(50000) AS t(i)")
    connexion.close()

    faits = relever(
        DuckDBSource(name="gros", path=chemin), ReglagesDuReleve(seuil_approximation=1000)
    )

    assert faits.estimees == []
    assert faits.lignes_par_table == {"mesures": 50_000}


def test_un_moteur_sans_estimation_retombe_sur_le_comptage():
    """Le dispatch ne devine pas : un moteur qu'on ne connaît pas est compté."""

    class Exotique:
        dialect = "sqlite"

    assert _estimation(Exotique(), "mesures") is None


def test_une_estimation_qui_echoue_ne_bloque_rien():
    """L'estimation est un bonus : un moteur qui refuse la requête de catalogue
    — droits manquants, catalogue inaccessible — rend un relevé compté, pas un
    relevé en échec."""

    class Boudeur(FauxPostgres):
        def run(self, query: str, max_rows: int = 200) -> QueryResult:
            if "pg_class" in query:
                raise RuntimeError("pas le droit de lire le catalogue")
            return super().run(query, max_rows)

    adaptateur = Boudeur(lignes=50_000)
    with source_postgres(adaptateur) as source:
        faits = relever(source, ReglagesDuReleve(seuil_approximation=1000))

    assert faits.lu
    assert faits.estimees == []
    assert faits.lignes_par_table == {"mesures": 50_000}


def test_une_table_absente_du_catalogue_du_moteur_est_comptee():
    """``to_regclass`` rend NULL d'une table que le catalogue ne connaît pas."""

    class Muet(FauxPostgres):
        def run(self, query: str, max_rows: int = 200) -> QueryResult:
            if "pg_class" in query:
                return QueryResult(columns=["reltuples"], rows=[[None]])
            return super().run(query, max_rows)

    adaptateur = Muet(lignes=50_000)
    with source_postgres(adaptateur) as source:
        faits = relever(source, ReglagesDuReleve(seuil_approximation=1000))

    assert faits.estimees == []
    assert adaptateur.comptages == 1


# --- la colonne de date de référence -----------------------------------------
#
# Une source qui porte une date de commande ET une date de livraison a deux
# périodes également vraies, et le schéma ne dit pas laquelle décrit la source.
# La période était celle de la PREMIÈRE du DDL : rien de faux — la colonne est
# nommée dans la réponse — mais l'ordre d'un DDL n'est pas une décision. La
# source le déclare désormais, et les trois cas ci-dessous sont les trois
# catalogues de `tests/catalogues/deux-dates/`, joués ici sur les mêmes octets.

CATALOGUES_DEUX_DATES = Path(__file__).resolve().parents[3] / "tests/catalogues/deux-dates"


def _commandes(catalogue: str):
    from data_analyst_agent.agents.retrieval.catalog import load_catalog

    return load_catalog(CATALOGUES_DEUX_DATES / f"{catalogue}.yaml").get("commandes")


def test_sans_designation_la_periode_reste_celle_de_la_premiere_colonne():
    """Le comportement d'avant, gardé tel quel : une source qui ne désigne rien
    n'a rien à perdre, et le défaut n'invente toujours aucun pari sur les noms."""
    faits = relever(_commandes("sans-designation"))

    assert faits.periode is not None
    assert faits.periode.colonne == "date_commande"
    assert (faits.periode.debut, faits.periode.fin) == ("2024-02-11", "2024-11-30")
    assert faits.avertissement == ""


def test_la_source_designe_sa_colonne_de_date_et_la_periode_la_suit():
    """Les mêmes octets, une ligne de YAML en plus, une autre période — celle de
    `date_livraison`, qui n'est pas la première du schéma."""
    faits = relever(_commandes("livraison-designee"))

    assert faits.periode is not None
    assert faits.periode.colonne == "date_livraison"
    assert (faits.periode.debut, faits.periode.fin) == ("2024-02-22", "2025-01-07")
    assert "du 2024-02-22 au 2025-01-07" in faits.en_clair()
    assert faits.avertissement == ""


def test_une_designation_introuvable_se_dit_et_nomme_les_colonnes_reelles():
    """Le repli ne doit pas être un cache-misère.

    Sans ce message, une désignation mal orthographiée retomberait sur la
    première colonne de date et serait indiscernable d'une source qui n'en
    désigne aucune : la déclaration fausse survivrait indéfiniment.
    """
    faits = relever(_commandes("designation-fautive"))

    assert faits.lu  # le relevé ne tombe pas : les faits restent bons
    assert faits.lignes == 120
    assert faits.periode is not None
    assert faits.periode.colonne == "date_commande"
    assert "date_livraision" in faits.avertissement
    assert "commandes.date_livraison" in faits.avertissement
    assert "commandes.date_commande" in faits.avertissement
    assert faits.avertissement in faits.en_clair()


def test_la_designation_se_qualifie_par_sa_table_et_ignore_la_casse(tmp_path: Path):
    """« table.colonne » autant que « colonne », et la casse ne compte pas.

    Postgres replie ses identifiants en minuscules, un en-tête de CSV garde sa
    majuscule, et la désignation est écrite à la main dans un YAML.
    """
    chemin = tmp_path / "ventes.csv"
    chemin.write_text("Ouverture,Cloture\n2024-01-01,2024-03-01\n", encoding="utf-8")

    qualifiee = relever(FileSource(name="v", path=chemin, date_reference="ventes.CLOTURE"))
    nue = relever(FileSource(name="v", path=chemin, date_reference="cloture"))

    assert qualifiee.periode is not None
    assert qualifiee.periode.colonne == "Cloture"
    assert qualifiee.avertissement == ""
    assert nue.periode is not None
    assert nue.periode.colonne == "Cloture"
    assert nue.avertissement == ""


def test_une_designation_sur_une_colonne_qui_n_est_pas_une_date_est_refusee(tmp_path: Path):
    """`client` existe, et n'est pas une colonne de date. La désigner comme telle
    est la même faute qu'en désigner une qui n'existe pas, et se dit pareil."""
    faits = relever(
        source(tmp_path, "ventes", VENTES).model_copy(update={"date_reference": "client"})
    )

    assert faits.periode is not None
    assert faits.periode.colonne == "date_vente"
    assert "client" in faits.avertissement
    assert "ventes.date_vente" in faits.avertissement


def test_une_source_sans_aucune_date_qui_en_designe_une_le_dit(tmp_path: Path):
    """Aucune période à rendre, et une déclaration à corriger : les deux se disent."""
    faits = relever(
        source(tmp_path, "plat", SANS_DATE).model_copy(update={"date_reference": "jour"})
    )

    assert faits.periode is None
    assert "aucune" in faits.avertissement
    assert faits.avertissement in faits.en_clair()
