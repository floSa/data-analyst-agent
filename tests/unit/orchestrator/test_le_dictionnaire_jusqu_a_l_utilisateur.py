"""Le dictionnaire va-t-il jusqu'à l'UTILISATEUR, ou seulement jusqu'au prompt ?

`test_dictionnaire_jusqu_au_sql` tient la première moitié du chemin : le
catalogue déclare, l'orchestrateur porte, l'agent SQL lit. Ces tests-ci tiennent
la seconde, et elle manquait — ce qui est lu n'est vérifié nulle part sur ce
chemin-là.

**Ce que le fil brut a montré, le 2026-09-18, catalogue de démonstration, trois
tirages.** Sur les six formulations de `mesure_question_de_sens`, cinq ne
voient jamais l'agent système : il répond `AUTRE` sans appeler le moindre
outil, 3 fois sur 3 pour chacune, et le tour repart au planificateur. Or c'est
l'agent système qui porte la ceinture (`introspection.defaut_de_fondation`) :
le chemin des données n'en a aucune, et le sens s'y perd sans que rien ne le
dise. « le statut RET, il recouvre quoi au juste ? » recevait « 2 lignes
retournées — voir le tableau ci-dessous ».
"""

from pathlib import Path

import pytest

from data_analyst_agent.agents.retrieval.catalog import Catalog, FileSource
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.graph import Orchestrator
from data_analyst_agent.orchestrator.plan import Plan
from helpers.scripted_llm import (
    PLANNER,
    RETRIEVAL,
    SYNTHESIS,
    ScriptedLLM,
    plan_response,
    text,
    tool_call,
)

# Les deux formes sous lesquelles un dictionnaire de ce dépôt définit un terme :
# une ligne par colonne, et une table de correspondance dont les COLONNES sont
# les termes. La seconde est celle qui portait `prix_kwh_eur` dans
# `facturation`, et ne lire que la première cellule la laissait tomber.
DICTIONNAIRE = """\
# Dictionnaire — `mini`

| Colonne | Sens |
|---|---|
| `survie` | `1` a survécu, `0` non. |
| `duree_min` | Durée en **minutes**. `-1` n'est pas une durée : à écarter de toute moyenne. |

| `code_tarif` | `libelle` | `prix_unitaire` |
|---|---|---|
| `ABO` | Forfait mensuel | 0,00 |
"""


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def source(tmp_path: Path) -> FileSource:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    dico = tmp_path / "mini.md"
    dico.write_text(DICTIONNAIRE, encoding="utf-8")
    return FileSource(name="mini", path=csv, dictionary=dico)


def _llm(reponse: str = "Il y a 2 survivants.") -> ScriptedLLM:
    return (
        ScriptedLLM()
        .script(PLANNER, [plan_response(Plan(capability="query", source="mini"))])
        .script(
            RETRIEVAL,
            [
                tool_call("run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE survie = 1"}),
                text(reponse),
            ],
        )
        .script(SYNTHESIS, [text(reponse)])
    )


def _repondre(catalogue: Catalog, question: str, reponse: str = "Il y a 2 survivants.") -> str:
    llm = _llm(reponse)
    orchestrateur = Orchestrator(model=llm.model(), catalog=catalogue, settings=make_settings())
    answer = orchestrateur.ask(question)
    assert answer.error is None
    return answer.answer


# --- les termes : c'est le dictionnaire qui déclare son vocabulaire -----------


def test_les_entrees_sont_lues_dans_le_dictionnaire_et_non_ecrites_a_la_main():
    """Les deux formes de définition, et les deux seulement.

    `ABO` n'en est pas une : trois caractères, et un nom d'entrée qui se
    confond avec un mot ordinaire ne distingue plus rien — c'est la collision
    que `BASE`, poste tarifaire de `facturation`, provoque avec « dans la
    base ».
    """
    entrees = introspection.entrees_du_dictionnaire(DICTIONNAIRE)

    assert entrees == ["survie", "duree_min", "code_tarif", "libelle", "prix_unitaire"]
    assert "ABO" not in entrees


def test_un_message_qui_ne_nomme_aucune_entree_ne_tire_rien():
    assert (
        introspection.ce_qu_en_dit_le_dictionnaire("Combien de lignes ?", DICTIONNAIRE, "mini")
        == ""
    )


def test_une_source_sans_dictionnaire_ne_tire_rien():
    """Le témoin obligatoire — `titanic` et `iris` n'en déclarent aucun."""
    assert introspection.ce_qu_en_dit_le_dictionnaire("survie ?", None, "titanic") == ""
    assert introspection.ce_qu_en_dit_le_dictionnaire("survie ?", "", "titanic") == ""


def test_les_termes_de_PLUSIEURS_entrees_nommees_se_cumulent():
    """« montant_ttc_eur et montant_ht_eur, quelle différence ? » en nomme deux.

    C'est la forme qui tombait ailleurs : ``_nomme_dans`` rend ``None`` dès que
    plusieurs noms sont cités, et le tour perdait les deux. Ici, chacun apporte
    ses lignes, et l'ordre est celui du dictionnaire.
    """
    dit = introspection.ce_qu_en_dit_le_dictionnaire(
        "survie et duree_min, quelle différence ?", DICTIONNAIRE, "mini"
    )

    assert "`survie` | `1` a survécu" in dit
    assert "à écarter de toute moyenne" in dit


def test_l_attribution_est_celle_que_la_ceinture_reconnait():
    """La même phrase que ``decrire_le_schema`` sert, et elle n'est écrite qu'une fois.

    C'est à cet en-tête que ``_attribution_qui_ne_colle_pas`` reconnaît des
    faits qui citent un dictionnaire, et ``_citations`` y cherche la consigne.
    Deux copies de cette marque, ce seraient deux marques qui divergent.
    """
    dit = introspection.ce_qu_en_dit_le_dictionnaire("survie ?", DICTIONNAIRE, "mini")

    assert dit.startswith("Ce qu'en dit le dictionnaire de `mini` :\n> ")
    assert introspection._EN_TETE_DE_DICTIONNAIRE in dit.lower()


# --- de bout en bout : ce que l'utilisateur lit -------------------------------


def test_ce_que_le_dictionnaire_dit_du_terme_arrive_a_l_utilisateur(source: FileSource):
    """Le défaut mesuré : la réponse répond, et le sens reste dans le prompt."""
    lu = _repondre(Catalog(sources=[source]), "duree_min, c'est en minutes ou en heures ?")

    assert "Ce qu'en dit le dictionnaire de `mini` :" in lu
    assert "à écarter de toute moyenne" in lu


def test_une_question_qui_ne_nomme_aucun_terme_ne_recoit_rien_de_plus(source: FileSource):
    """Il ne parle qu'aux tours où il a quelque chose à dire.

    « Combien de lignes ? » ne nomme aucune entrée : la réponse est celle
    d'avant, au caractère près.
    """
    lu = _repondre(Catalog(sources=[source]), "Combien de lignes ?")

    assert lu == "Il y a 2 survivants."


def test_une_source_sans_dictionnaire_rend_la_reponse_d_avant(tmp_path: Path):
    """Le volet témoin de la mesure, tenu par du code."""
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nm,0\n", encoding="utf-8")

    lu = _repondre(
        Catalog(sources=[FileSource(name="mini", path=csv)]), "survie, ça veut dire quoi ?"
    )

    assert lu == "Il y a 2 survivants."
    assert "dictionnaire" not in lu.lower()


def test_le_meme_paragraphe_n_est_jamais_servi_deux_fois(source: FileSource):
    """Une réponse qui porte DÉJÀ l'extrait au caractère près ne le reçoit pas deux fois.

    C'est le cas du repli de l'agent système, qui sert le texte de l'outil tel
    quel. Deux fois le même paragraphe serait un défaut visible.
    """
    deja = introspection.ce_qu_en_dit_le_dictionnaire("survie ?", DICTIONNAIRE, "mini")

    lu = _repondre(Catalog(sources=[source]), "survie ?", reponse=f"Voici.\n\n{deja}")

    assert lu.count("Ce qu'en dit le dictionnaire de `mini` :") == 1
