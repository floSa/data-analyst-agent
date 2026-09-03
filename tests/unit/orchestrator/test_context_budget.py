"""Plafond de ce qui entre dans le contexte : fenêtre glissante sur les trois axes.

L'audit (§3.4) a mesuré une croissance linéaire sans plafond des objets
intermédiaires, sur trois axes simultanés. Ces tests vérifient que le plafond
existe, qu'il est le MÊME sur les trois axes, et qu'il ne détruit rien.
"""

from __future__ import annotations

from pathlib import Path

from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.context_budget import (
    ContextLimits,
    ContextTrim,
    estimate_tokens,
)
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace
from data_analyst_agent.sandbox.client import docker_run_command


def memoire(
    tmp_path: Path, tours: int, fenetre: int = 8, budget: int = 8000
) -> ConversationWorkspace:
    """Une conversation de ``tours`` tours, chacun ayant produit un tableau."""
    limites = ContextLimits(artifact_window=fenetre, token_budget=budget)
    ws = ConversationWorkspace(tmp_path, "conv", limits=limites)
    for tour in range(1, tours + 1):
        ws.save_table(["a", "b"], [[tour, tour * 2]], f"question du tour {tour}")
    return ConversationWorkspace(tmp_path, "conv", limits=limites)


# --- fenêtre glissante --------------------------------------------------------


def test_fenetre_garde_les_objets_les_plus_recents(tmp_path: Path):
    ws = memoire(tmp_path, tours=12, fenetre=5)
    assert len(ws.artifacts) == 12  # tout est là
    assert [a.name for a in ws.injected] == [f"resultat_{n}" for n in range(8, 13)]


def test_les_objets_evinces_restent_sur_le_disque(tmp_path: Path):
    """On plafonne ce qu'on INJECTE, pas ce qu'on conserve : rien n'est effacé."""
    ws = memoire(tmp_path, tours=12, fenetre=5)
    evince = ws.artifacts[0]
    assert evince not in ws.injected
    assert ws.path_of(evince).exists()
    assert evince.name in (ws.dir / ConversationWorkspace.MANIFEST).read_text(encoding="utf-8")


def test_les_trois_axes_voient_exactement_les_memes_objets(tmp_path: Path):
    """Décrit au planificateur mais pas monté (ou l'inverse) = erreur incompréhensible."""
    ws = memoire(tmp_path, tours=30, fenetre=4)
    decrits = {a.name for a in ws.injected if a.name in (ws.describe() or "")}
    montes = {Path(nom).stem for nom in ws.sandbox_files().values()}
    catalogue = {s.name for s in ws.as_sources()}
    assert decrits == montes == catalogue
    assert len(catalogue) == 4


def test_le_nombre_de_montages_docker_est_borne(tmp_path: Path):
    """L'axe mesuré par l'audit : 100 arguments `--volume` à 100 tours."""
    settings = Settings(_env_file=None)
    ws = memoire(tmp_path, tours=100, fenetre=8)
    commande = docker_run_command(settings, ws.sandbox_files())
    assert len([arg for arg in commande if arg.startswith("--volume=")]) == 8


def test_le_catalogue_injecte_cesse_de_grandir(tmp_path: Path):
    """Au-delà de la fenêtre, la description du planificateur est stationnaire.

    « Stationnaire » et non « constante » : il reste la largeur des nombres
    (``resultat_30`` puis ``resultat_100``, « 22 » puis « 92 » tableaux
    évincés). C'est une croissance en logarithme du nombre de tours, contre les
    9 033 caractères que les mêmes 70 tours ajoutaient avant le plafond.
    """
    a_30, a_100 = (
        len(memoire(tmp_path / f"c{n}", tours=n, fenetre=8).describe()) for n in (30, 100)
    )
    assert a_100 - a_30 <= 10


def test_describe_annonce_l_eviction_au_planificateur(tmp_path: Path):
    """Sans cet avis, le planificateur proposerait une source devenue introuvable."""
    description = memoire(tmp_path, tours=12, fenetre=5).describe()
    assert "resultat_1 " not in description  # évincé, donc pas décrit
    assert "7 tableau(x) plus ancien(s)" in description
    assert "ne les propose pas comme source" in description


def test_fenetre_a_zero_desactive_le_plafond(tmp_path: Path):
    """Réglage de secours, assumé : 0 = aucune fenêtre."""
    ws = memoire(tmp_path, tours=20, fenetre=0)
    assert len(ws.injected) == 20
    assert ws.trim.truncated is False


def test_un_nouveau_tableau_entre_dans_la_fenetre_et_en_chasse_un(tmp_path: Path):
    """« ces lignes » désigne le tableau du tour courant : il doit être injecté."""
    limites = ContextLimits(artifact_window=3, token_budget=8000)
    ws = memoire(tmp_path, tours=3, fenetre=3)
    ws = ConversationWorkspace(tmp_path, "conv", limits=limites)
    nouveau = ws.save_table(["a"], [[9]], "le tour courant")
    assert ws.injected[-1].name == nouveau.name
    assert [a.name for a in ws.injected] == ["resultat_2", "resultat_3", "resultat_4"]


def test_sans_plafond_atteint_rien_n_est_signale(tmp_path: Path):
    ws = memoire(tmp_path, tours=3, fenetre=8)
    assert ws.trim.truncated is False
    assert ws.trim.message() == ""
    assert ws.trim.planner_notice() == ""


# --- le constat rendu ---------------------------------------------------------


def test_le_constat_nomme_le_reglage_en_cause(tmp_path: Path):
    trim = memoire(tmp_path, tours=12, fenetre=5).trim
    assert trim.total == 12
    assert trim.kept == 5
    assert trim.dropped == 7
    message = trim.message()
    assert "7 des 12 tableaux" in message
    assert "DAA_CONTEXT_ARTIFACT_WINDOW=5" in message
    assert "restent enregistrés" in message


def test_limits_lues_des_reglages():
    settings = Settings(_env_file=None, context_artifact_window=3)
    assert ContextLimits.from_settings(settings).artifact_window == 3


# --- compteur de tokens approché ---------------------------------------------


def test_estimation_de_tokens_prudente():
    """Le compteur SURESTIME : c'est le sens d'erreur voulu (il coupe trop tôt)."""
    texte = "a" * 300
    assert estimate_tokens(texte) == 108  # 300/3 + 8 de surcoût de message
    # un tokeniseur réel rendrait moins : l'estimation est un majorant de travail
    assert estimate_tokens(texte) > len(texte) / 4


def test_estimation_ignore_les_fragments_vides():
    assert estimate_tokens("", None) == 0
    assert estimate_tokens("abc", None, "") == estimate_tokens("abc")


def test_trim_vide_par_defaut():
    assert ContextTrim().truncated is False


# --- budget de tokens, décompté avant l'appel ---------------------------------


def test_le_budget_retire_les_plus_anciens_d_abord(tmp_path: Path):
    """La dégradation est ordonnée : c'est l'ancien qui part, pas le tour courant."""
    ws = memoire(tmp_path, tours=10, fenetre=0, budget=400)
    trim = ws.fit_to_budget(overhead_tokens=100)
    assert 0 < trim.kept < 10
    # ce qui reste est la QUEUE de la liste : les plus récents
    assert [a.name for a in ws.injected] == [a.name for a in ws.artifacts[-trim.kept :]]
    assert "DAA_CONTEXT_TOKEN_BUDGET=400" in trim.message()


def test_le_budget_est_respecte_apres_resserrement(tmp_path: Path):
    ws = memoire(tmp_path, tours=40, fenetre=0, budget=400)
    ws.fit_to_budget(overhead_tokens=120)
    assert 120 + estimate_tokens(ws.describe()) <= 400


def test_le_budget_ne_rouvre_pas_ce_qu_il_a_ferme(tmp_path: Path):
    """`save_table` réapplique la fenêtre en cours de tour : elle doit rester fermée.

    Sinon les montages de la sandbox déborderaient du budget décompté pour le
    prompt, et les trois axes cesseraient d'être d'accord.
    """
    ws = memoire(tmp_path, tours=10, fenetre=0, budget=400)
    retenus = ws.fit_to_budget(overhead_tokens=100).kept
    ws.save_table(["a"], [[99]], "le tour courant")
    assert len(ws.injected) == retenus
    assert len(ws.sandbox_files()) == retenus
    assert ws.injected[-1].question == "le tour courant"


def test_prompt_trop_gros_meme_sans_aucun_objet(tmp_path: Path):
    """Le budget est crevé par le reste du prompt : plus rien à couper ici, on le dit."""
    ws = memoire(tmp_path, tours=5, fenetre=8, budget=200)
    trim = ws.fit_to_budget(overhead_tokens=5000)
    assert ws.injected == []
    assert trim.over_budget is True
    assert trim.truncated is True
    assert "même sans aucun tableau intermédiaire" in trim.message()


def test_budget_a_zero_desactive_le_decompte(tmp_path: Path):
    ws = memoire(tmp_path, tours=5, fenetre=8, budget=0)
    trim = ws.fit_to_budget(overhead_tokens=10**6)
    assert len(ws.injected) == 5
    assert trim.truncated is False


def test_budget_confortable_ne_coupe_rien(tmp_path: Path):
    ws = memoire(tmp_path, tours=5, fenetre=8, budget=8000)
    assert ws.fit_to_budget(overhead_tokens=900).truncated is False
    assert len(ws.injected) == 5


def test_limits_lues_des_reglages_pour_le_budget():
    settings = Settings(_env_file=None, context_token_budget=1234)
    assert ContextLimits.from_settings(settings).token_budget == 1234
