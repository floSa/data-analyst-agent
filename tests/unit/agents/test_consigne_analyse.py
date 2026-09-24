"""Une somme qui exige un filtre, vérifiée sur le code d'analyse produit (C60).

Les codes « faux » sont ceux relevés dans le bac à sable, catalogue métier,
« compare les quantités produites et les quantités vendues par produit » :
lus tels quels, abrégés de ce qui ne décide rien.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.analysis.agent import message_de_consigne, run_analysis
from data_analyst_agent.agents.analysis.consigne import FiltreMonte, somme_sans_son_filtre
from data_analyst_agent.agents.retrieval.catalog import FiltreDesSommes, load_catalog
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import _filtres_montes
from data_analyst_agent.sandbox.client import SandboxResult

VENTES = FiltreDesSommes(
    colonne="commandes.statut",
    exclure="ANN",
    sommes=[
        "lignes_commande.quantite",
        "lignes_commande.montant_ligne_eur",
        "commandes.montant_total_eur",
    ],
)
CROISE = [FiltreMonte("ventes", "ventes_", VENTES)]
SEULE = [FiltreMonte("ventes", "", VENTES)]

# Relevé C60, essai réussi : la quantité vendue sans le filtre des annulées.
CODE_SANS_FILTRE = """
import pandas as pd
df_ventes_produits = pd.read_csv('/data/ventes_produits.csv')
df_ventes_lignes_commande = pd.read_csv('/data/ventes_lignes_commande.csv')
df_production_ordres_fabrication = pd.read_csv('/data/production_ordres_fabrication.csv')
# Règle de filtrage: Aucune règle spécifique mentionnée pour la vente de produits
df_ventes_merged = pd.merge(df_ventes_lignes_commande,
                            df_ventes_produits[['produit_id', 'code_produit']], on='produit_id')
ventes = df_ventes_merged.groupby('code_produit')['quantite'].sum().reset_index()
prod = df_production_ordres_fabrication.groupby('code_produit')['quantite_produite'].sum()
print(ventes)
"""

# Relevé C60, fil lié à `ventes` : le même calcul, filtre posé.
CODE_FILTRE = """
import pandas as pd
commandes = pd.read_csv('/data/commandes.csv')
lignes_commande = pd.read_csv('/data/lignes_commande.csv')
# Règle : Pour toute somme d'unités vendues ou expédiées, statut <> 'ANN' (Piège 1).
df = pd.merge(lignes_commande, commandes[['commande_id', 'statut']], on='commande_id')
df = df[df['statut'] != 'ANN']
print(df.groupby('produit_id')['quantite'].sum())
"""


def test_la_somme_des_quantites_sans_le_filtre_est_relevee():
    constat = somme_sans_son_filtre(CODE_SANS_FILTRE, CROISE)
    assert constat is not None
    message = constat.pour_le_modele()
    assert "`quantite` (lue dans `ventes_lignes_commande.csv`)" in message
    assert "`statut <> 'ANN'`" in message
    assert "`ventes_commandes.csv`" in message
    assert "Ne filtre aucun comptage" in message


def test_la_somme_filtree_n_est_pas_relevee():
    assert somme_sans_son_filtre(CODE_FILTRE, SEULE) is None


def test_un_commentaire_qui_cite_la_regle_ne_filtre_rien():
    code = CODE_FILTRE.replace("df = df[df['statut'] != 'ANN']\n", "")
    assert "'ANN'" in code  # dans le commentaire seulement
    assert somme_sans_son_filtre(code, SEULE) is not None


@pytest.mark.parametrize(
    "code",
    [
        # « combien de commandes ? » → 180, aucun filtre (dictionnaire, piège nº 1)
        "import pandas as pd\nc = pd.read_csv('/data/commandes.csv')\nprint(len(c))",
        # « combien de lignes de commande ? » → 463
        "import pandas as pd\n"
        "l = pd.read_csv('/data/lignes_commande.csv')\n"
        "print(l['ligne_id'].count())",
        # un comptage par canal qui ADDITIONNE ses effectifs pour le total
        "import pandas as pd\n"
        "c = pd.read_csv('/data/commandes.csv')\n"
        "k = pd.read_csv('/data/clients.csv')\n"
        "n = c.merge(k, on='client_id').groupby('canal').size()\n"
        "print(n, n.sum())",
        # des lignes par produit, comptées puis sommées
        "import pandas as pd\n"
        "l = pd.read_csv('/data/lignes_commande.csv')\n"
        "print(l['produit_id'].value_counts().sum())",
    ],
)
def test_un_comptage_n_est_jamais_releve(code):
    assert somme_sans_son_filtre(code, SEULE) is None


def test_la_somme_d_une_autre_source_n_est_pas_relevee():
    code = (
        "import pandas as pd\n"
        "o = pd.read_csv('/data/production_ordres_fabrication.csv')\n"
        "print(o['quantite_produite'].sum())"
    )
    assert somme_sans_son_filtre(code, CROISE) is None


def test_le_prefixe_du_croisement_distingue_les_fichiers():
    # Dans un croisement la table se lit `ventes_lignes_commande.csv` ; seule,
    # `lignes_commande.csv`. Chaque montage ne reconnaît que ses fichiers.
    sans_filtre_seul = CODE_FILTRE.replace("df = df[df['statut'] != 'ANN']\n", "")
    assert somme_sans_son_filtre(CODE_SANS_FILTRE, SEULE) is None
    assert somme_sans_son_filtre(sans_filtre_seul, CROISE) is None
    assert somme_sans_son_filtre(sans_filtre_seul, SEULE) is not None


def test_une_somme_en_sql_duckdb_est_lue_aussi():
    code = (
        "import duckdb\n"
        'print(duckdb.sql("SELECT produit_id, SUM(quantite) FROM '
        "read_csv('/data/lignes_commande.csv') GROUP BY 1\"))"
    )
    # Le fichier n'est pas une chaîne à part : il vit dans la requête.
    assert somme_sans_son_filtre(code, SEULE) is not None
    assert (
        somme_sans_son_filtre(code.replace("GROUP", "WHERE statut <> 'ANN' GROUP"), SEULE) is None
    )
    code = (
        "import duckdb\n"
        "f = '/data/lignes_commande.csv'\n"
        'print(duckdb.sql("SELECT produit_id, SUM(quantite) FROM read_csv(?) GROUP BY 1"))'
    )
    assert somme_sans_son_filtre(code, SEULE) is not None


def test_ce_qui_ne_se_lit_pas_ne_rend_rien():
    assert somme_sans_son_filtre("ha='right)", SEULE) is None
    assert somme_sans_son_filtre(CODE_SANS_FILTRE, []) is None


def test_la_declaration_exige_table_point_colonne():
    with pytest.raises(ValidationError):
        FiltreDesSommes(colonne="statut", exclure="ANN", sommes=["lignes_commande.quantite"])
    with pytest.raises(ValidationError):
        FiltreDesSommes(colonne="commandes.statut", exclure="ANN", sommes=["quantite"])


def test_le_catalogue_metier_declare_le_filtre_de_ventes_et_lui_seul():
    catalogue = load_catalog(Path("sources/metier/catalogue.yaml"))
    ventes = catalogue.get("ventes")
    assert ventes is not None
    assert ventes.filtre_des_sommes == VENTES
    autres = [s.name for s in catalogue.sources if s.filtre_des_sommes and s.name != "ventes"]
    assert autres == []
    assert _filtres_montes(catalogue.sources, croise=True) == CROISE
    assert _filtres_montes([ventes], croise=False) == SEULE


# ---- la boucle ---------------------------------------------------------------


def _deroule(codes: list[str], executions: list[SandboxResult], settings=None):
    recus: list[str] = []

    def responder(messages, info):
        recus.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart(f"```python\n{codes.pop(0)}\n```")])

    class Noyau:
        def execute(self, code, timeout=None):
            return executions.pop(0)

    result = run_analysis(
        "compare",
        model=FunctionModel(responder),
        settings=settings or Settings(_env_file=None),
        sandbox=Noyau(),
        filtres=CROISE,
    )
    return result, recus


def test_un_code_reussi_sans_le_filtre_est_renvoye_au_modele():
    result, recus = _deroule(
        [CODE_SANS_FILTRE, CODE_FILTRE],
        [SandboxResult(status="ok", stdout="141"), SandboxResult(status="ok", stdout="123")],
    )
    assert result.succeeded
    assert result.attempts == 2
    assert result.execution.stdout == "123"
    assert result.consigne_notice == ""
    assert recus[1].startswith("L'exécution a réussi, mais le calcul ne respecte pas")
    assert "`statut <> 'ANN'`" in recus[1]


def test_essais_epuises_le_dernier_calcul_reussi_est_servi_avec_son_avis():
    result, _ = _deroule(
        [CODE_SANS_FILTRE, CODE_SANS_FILTRE, "raise SystemExit"],
        [
            SandboxResult(status="ok", stdout="141"),
            SandboxResult(status="ok", stdout="141 bis"),
            SandboxResult(status="error", error="boom"),
        ],
    )
    # Un troisième essai qui plante ne jette pas le calcul qui avait abouti.
    assert result.succeeded
    assert result.execution.stdout == "141 bis"
    assert result.attempts == 3
    assert result.consigne_notice.startswith("Avertissement sur ce calcul")
    assert "`statut = 'ANN'`" in result.consigne_notice


def test_sans_filtre_declare_la_boucle_est_celle_d_avant():
    recus: list[str] = []

    def responder(messages, info):
        recus.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart(f"```python\n{CODE_SANS_FILTRE}\n```")])

    class Noyau:
        def execute(self, code, timeout=None):
            return SandboxResult(status="ok", stdout="141")

    result = run_analysis(
        "compare",
        model=FunctionModel(responder),
        settings=Settings(_env_file=None),
        sandbox=Noyau(),
    )
    assert result.attempts == 1
    assert len(recus) == 1
    assert result.consigne_notice == ""


def test_le_message_de_consigne():
    assert message_de_consigne("X") == (
        "L'exécution a réussi, mais le calcul ne respecte pas le dictionnaire.\n\n"
        "Constat :\nX\n\n"
        "Corrige le calcul et renvoie le code COMPLET corrigé."
    )
