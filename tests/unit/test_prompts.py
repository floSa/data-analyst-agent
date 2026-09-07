"""Les prompts servis depuis des fichiers, et le couplage qu'ils avaient aux tests.

Externaliser les prompts sans traiter ce couplage aurait été un piège : la
doublure `ScriptedLLM` route ses réponses vers le bon agent en cherchant un
morceau du prompt système, et ces morceaux étaient recopiés à la main dans le
helper (audit §5.3). Une reformulation de prompt faisait alors tomber toute la
suite sur « aucun script pour le prompt système ». Les marqueurs sont maintenant
dérivés des fichiers ; ce fichier vérifie que la dérivation tient.
"""

import zipfile
from pathlib import Path

import pytest

from data_analyst_agent import prompts
from data_analyst_agent.orchestrator.plan import planner_system_prompt, planner_template

TOUS = (prompts.PLANNER, prompts.RETRIEVAL, prompts.ANALYSIS, prompts.SYNTHESIS)


# --- lecture ---------------------------------------------------------------------


@pytest.mark.parametrize("nom", TOUS)
def test_chaque_prompt_est_lisible_et_non_vide(nom: str):
    assert prompts.gabarit(nom).strip()


def test_le_gabarit_du_planificateur_garde_ses_marqueurs():
    """L'orchestrateur pèse le gabarit AVANT substitution : il doit les contenir."""
    gabarit = planner_template()

    assert "{sources}" in gabarit
    assert "{datasets}" in gabarit


# --- substitution ----------------------------------------------------------------


def test_la_substitution_remplit_les_marqueurs():
    rendu = prompts.render(prompts.PLANNER, sources="- mini (file)", datasets="- titanic")

    assert "- mini (file)" in rendu
    assert "- titanic" in rendu
    assert "{sources}" not in rendu
    assert "{datasets}" not in rendu


def test_la_substitution_n_echappe_rien():
    """La destination est un modèle, pas un navigateur : rien à neutraliser.

    Un schéma de base ou un dictionnaire de données contient des chevrons et des
    apostrophes ; les échapper — comme le fait `api/pages.render` pour le HTML —
    rendrait le prompt illisible au modèle.
    """
    rendu = prompts.render(prompts.RETRIEVAL, dialect="duckdb & <co>")

    assert "duckdb & <co>" in rendu


def test_une_accolade_ordinaire_ne_casse_pas_la_substitution(tmp_path: Path, monkeypatch):
    """C'est pour cela que ce n'est pas `str.format`.

    Un prompt vit maintenant dans un fichier qu'on édite sans relancer la
    suite : y montrer un exemple JSON ou un dict Python au modèle est une
    tentation immédiate, et ferait lever `str.format` en pleine requête.
    """
    (tmp_path / "essai.txt").write_text(
        'Réponds {"a": 1} pour {cle}, et {autre} reste tel quel.\n', encoding="utf-8"
    )
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    prompts.gabarit.cache_clear()
    try:
        rendu = prompts.render("essai.txt", cle="X")
    finally:
        prompts.gabarit.cache_clear()

    assert rendu == 'Réponds {"a": 1} pour X, et {autre} reste tel quel.\n'


# --- marqueurs (le couplage traité) ----------------------------------------------


@pytest.mark.parametrize("nom", TOUS)
def test_un_marqueur_est_une_portion_stable_du_prompt_rendu(nom: str):
    """Il doit traverser la substitution intact — sinon la doublure ne route plus."""
    marqueur = prompts.marqueur(nom)

    assert marqueur
    assert "{" not in marqueur
    # rendu avec des valeurs quelconques : le marqueur survit
    rendu = prompts.render(nom, sources="S", datasets="D", dialect="postgresql")
    assert marqueur in rendu


def test_les_marqueurs_distinguent_bien_les_quatre_agents():
    """Un marqueur présent dans deux prompts enverrait les réponses au mauvais agent."""
    for nom in TOUS:
        marqueur = prompts.marqueur(nom)
        porteurs = [autre for autre in TOUS if marqueur in prompts.gabarit(autre)]
        assert porteurs == [nom], (marqueur, porteurs)


def test_le_marqueur_du_planificateur_survit_au_prompt_reellement_compose():
    """Le prompt du planificateur est composé en plusieurs morceaux : le pire cas."""
    compose = planner_system_prompt(
        "- mini (file)",
        "- titanic",
        pending_context="CONTEXTE DE CONVERSATION : …",
        history_context="CONTEXTE CONVERSATIONNEL : …",
    )

    assert prompts.marqueur(prompts.PLANNER) in compose


def test_le_helper_de_test_derive_ses_marqueurs_des_fichiers():
    """Le point du correctif : plus une seule formulation de prompt recopiée."""
    from helpers import scripted_llm

    derives = (
        scripted_llm.PLANNER,
        scripted_llm.RETRIEVAL,
        scripted_llm.ANALYSIS,
        scripted_llm.SYNTHESIS,
    )
    assert derives == tuple(prompts.marqueur(nom) for nom in TOUS)


# --- distribution ----------------------------------------------------------------


def test_les_prompts_sont_embarques_dans_la_distribution(tmp_path: Path):
    """Un prompt hors du wheel, c'est une application qui ne démarre plus.

    Même vérification que pour le gabarit de la page de chat : les fichiers de
    données d'un package se laissent oublier par un backend de build.
    """
    import subprocess

    racine = Path(__file__).resolve().parents[2]
    construction = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=racine,
        capture_output=True,
        text=True,
    )
    if construction.returncode != 0:  # pas de réseau, pas de backend : on ne bloque pas
        pytest.skip(f"construction du wheel indisponible : {construction.stderr[-200:]}")
    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        embarques = set(archive.namelist())

    for nom in TOUS:
        assert f"data_analyst_agent/prompts/{nom}" in embarques
