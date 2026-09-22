"""Mémoire de conversation : persistance CSV et réexposition des objets."""

import stat
from pathlib import Path

from data_analyst_agent.agents.retrieval.catalog import FileSource, open_source
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace, WorkspaceArtifact


def test_save_table_ecrit_csv_et_manifeste(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "conv-1")
    artifact = ws.save_table(
        ["sepal_length", "species"], [[7.9, "virginica"], [7.7, "virginica"]], "les 2 plus grandes"
    )
    assert artifact.name == "resultat_1"
    assert artifact.row_count == 2
    csv = ws.path_of(artifact)
    assert csv.exists()
    assert csv.read_text(encoding="utf-8").splitlines()[0] == "sepal_length,species"
    # le manifeste est écrit à côté
    assert (ws.dir / ConversationWorkspace.MANIFEST).exists()


def test_persistance_relue_par_une_nouvelle_instance(tmp_path: Path):
    """Le tour suivant (nouvelle instance) retrouve les objets sur disque."""
    ConversationWorkspace(tmp_path, "conv-2").save_table(["a"], [[1], [2]], "q1")
    rechargee = ConversationWorkspace(tmp_path, "conv-2")
    assert [a.name for a in rechargee.artifacts] == ["resultat_1"]
    assert rechargee.artifacts[0].columns == ["a"]


def test_numerotation_incrementale(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "conv-3")
    ws.save_table(["a"], [[1]], "q1")
    ws.save_table(["b"], [[2]], "q2")
    assert [a.name for a in ws.artifacts] == ["resultat_1", "resultat_2"]


def test_as_sources_interrogeable_en_sql(tmp_path: Path):
    """Un objet mémorisé devient une source fichier requêtable (table DuckDB homonyme)."""
    ws = ConversationWorkspace(tmp_path, "conv-4")
    ws.save_table(["x", "y"], [[1, 2], [3, 4]], "q")
    sources = ws.as_sources()
    assert isinstance(sources[0], FileSource)
    assert sources[0].name == "resultat_1"
    adapter = open_source(sources[0])
    # le nom de table DuckDB coïncide avec le nom de la source
    assert adapter.schema().table_names() == ["resultat_1"]
    assert adapter.run("SELECT count(*) FROM resultat_1").rows[0][0] == 2


def test_describe_et_sandbox_files(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "conv-5")
    ws.save_table(["sepal_length"], [[7.9]], "3 dernières lignes iris")
    description = ws.describe()
    assert "resultat_1" in description
    assert "sepal_length" in description
    assert "ces lignes" in description  # aiguille le planificateur sur le plus récent
    files = ws.sandbox_files()
    assert list(files.values()) == ["resultat_1.csv"]


# --- ce qu'on sait d'un tableau DÉRIVÉ -----------------------------------------
#
# Une source primaire est décrite finement : le type de chaque colonne, ses
# clés, et les VALEURS POSSIBLES des colonnes catégorielles. Un tableau produit
# dans le fil n'avait que ses noms de colonnes et son nombre de lignes — le
# modèle en savait donc moins sur ce qu'il venait de produire que sur ce dont
# il était parti, ce qui est une raison de plus de repartir de la base.


def test_un_tableau_derive_porte_le_type_de_chaque_colonne(tmp_path: Path):
    ws = ConversationWorkspace(tmp_path, "types")
    artefact = ws.save_table(
        ["station", "pannes", "duree_h", "sous_contrat"],
        [["Gare Nord", 3, 1.5, True], ["Place Bleue", 1, 0.25, False]],
        "les pannes par station",
    )

    assert artefact.types == {
        "station": "texte",
        "pannes": "entier",
        "duree_h": "décimal",
        "sous_contrat": "booléen",
    }


def test_une_colonne_texte_a_faible_cardinalite_montre_ses_valeurs(tmp_path: Path):
    """La MÊME règle qu'ailleurs, et par le même code : un modèle ne filtre pas
    sur des valeurs qu'il n'a jamais vues — il devine, et il devine dans sa langue."""
    ws = ConversationWorkspace(tmp_path, "valeurs")
    artefact = ws.save_table(
        ["nature", "n"],
        [["borne hors service", 141], ["câble endommagé", 140]],
        "les natures",
    )

    assert artefact.valeurs == {"nature": ["borne hors service", "câble endommagé"]}
    assert "nature (texte : 'borne hors service', 'câble endommagé')" in artefact.description
    assert "n (entier)" in artefact.description


def test_une_colonne_a_forte_cardinalite_ne_montre_aucune_valeur(tmp_path: Path):
    """Au-delà du plafond, la colonne est un identifiant ou du texte libre."""
    ws = ConversationWorkspace(tmp_path, "cardinalite")
    artefact = ws.save_table(
        ["libelle"], [[f"station {i}"] for i in range(30)], "toutes les stations"
    )

    assert artefact.valeurs == {}
    assert artefact.colonnes_en_clair() == "libelle (texte)"


def test_une_ligne_de_catalogue_reste_UNE_ligne(tmp_path: Path):
    """Quand tout ne tient pas, ce sont les VALEURS qui tombent — et on le dit."""
    ws = ConversationWorkspace(tmp_path, "plafond")
    artefact = ws.save_table(
        ["a", "b"],
        [
            [f"une valeur plutôt longue numéro {i}", f"une autre valeur numéro {i}"]
            for i in range(9)
        ],
        "beaucoup de texte",
    )

    en_clair = artefact.colonnes_en_clair()

    assert en_clair == (
        "a (texte), b (texte)"
        " (valeurs possibles non montrées : elles ne tiennent pas sur une ligne)"
    )
    assert "\n" not in en_clair


def test_un_manifeste_ecrit_avant_ce_champ_rend_la_liste_nue(tmp_path: Path):
    """Aucune migration : sans ``types``, la ligne de catalogue est celle d'avant."""
    ancien = WorkspaceArtifact(
        name="resultat_1", file="resultat_1.csv", columns=["a", "b"], question="q"
    )

    assert ancien.colonnes_en_clair() == "a, b"


def test_describe_vide_si_aucun_objet(tmp_path: Path):
    assert ConversationWorkspace(tmp_path, "vide").describe() is None


def test_contexte_conversationnel_persiste(tmp_path: Path):
    """La dernière question/action (+ code de figure) survit d'un tour à l'autre."""
    ws = ConversationWorkspace(tmp_path, "c")
    assert ws.describe_context() is None  # rien au premier tour
    ws.record_turn("fais un graphique iris", "analyze", "iris", code="import matplotlib")
    # relu par l'instance du tour suivant
    reloaded = ConversationWorkspace(tmp_path, "c")
    assert reloaded.context.last_capability == "analyze"
    assert reloaded.context.last_source == "iris"
    ctx = reloaded.describe_context()
    assert "graphique iris" in ctx
    assert "AJUSTEMENT" in ctx
    # le code n'est repris que pour la même source
    assert reloaded.last_code_for("iris") == "import matplotlib"
    assert reloaded.last_code_for("titanic") is None


def test_dossiers_crees_en_0700_parents_compris(tmp_path: Path):
    """Sans mode explicite, l'umask donnait 0o775 : tout compte local lisait
    les transcriptions et les CSV de tout le monde. ``mkdir(parents=True)``
    n'applique pas le mode aux parents, d'où la création segment par segment."""
    base = tmp_path / "var" / "workspaces"
    ws = ConversationWorkspace(base, "conv-privee")

    ws.save_table(["a"], [[1]], "q")

    assert oct(stat.S_IMODE(ws.dir.stat().st_mode)) == "0o700"
    assert oct(stat.S_IMODE(base.stat().st_mode)) == "0o700"
    assert oct(stat.S_IMODE(base.parent.stat().st_mode)) == "0o700"


def test_un_dossier_deja_present_garde_ses_droits(tmp_path: Path):
    """Un volume monté avec ses propres droits ne doit pas être re-chmodé."""
    base = tmp_path / "monte"
    base.mkdir(mode=0o750)

    ConversationWorkspace(base, "c").save_table(["a"], [[1]], "q")

    assert oct(stat.S_IMODE(base.stat().st_mode)) == "0o750"
