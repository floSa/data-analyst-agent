"""Les prompts servis depuis des fichiers, et le couplage qu'ils avaient aux tests.

Externaliser les prompts sans traiter ce couplage aurait été un piège : la
doublure `ScriptedLLM` route ses réponses vers le bon agent en cherchant un
morceau du prompt système, et ces morceaux étaient recopiés à la main dans le
helper (audit §5.3). Une reformulation de prompt faisait alors tomber toute la
suite sur « aucun script pour le prompt système ». Les marqueurs sont maintenant
dérivés des fichiers ; ce fichier vérifie que la dérivation tient.
"""

import hashlib
import zipfile
from pathlib import Path

import pytest

from data_analyst_agent import prompts
from data_analyst_agent.orchestrator.plan import planner_system_prompt, planner_template

# Les SEPT prompts du paquet, et non cinq. `rappel.txt` et `reparation.txt`
# manquaient : la doublure de test dérivait pourtant déjà leurs marqueurs, et
# c'est par cet écart qu'un prompt échappait à la couverture de ce fichier — il
# n'était ni lu, ni rendu, ni confronté aux autres pour l'unicité de son
# marqueur. `test_le_helper_de_test_derive_ses_marqueurs_des_fichiers` compare
# ce tuple TERME À TERME, ce qui est la raison pour laquelle le compléter
# demandait de compléter les deux listes en même temps.
TOUS = (
    prompts.PLANNER,
    prompts.RETRIEVAL,
    prompts.ANALYSIS,
    prompts.SYNTHESIS,
    prompts.SYSTEME,
    prompts.RAPPEL,
    prompts.REPARATION,
)


# --- lecture ---------------------------------------------------------------------


@pytest.mark.parametrize("nom", TOUS)
def test_chaque_prompt_est_lisible_et_non_vide(nom: str):
    assert prompts.gabarit(nom).strip()


def test_le_gabarit_du_planificateur_garde_ses_marqueurs():
    """L'orchestrateur pèse le gabarit AVANT substitution : il doit les contenir."""
    gabarit = planner_template()

    assert "{sources}" in gabarit
    assert "{datasets}" in gabarit


def test_le_planificateur_interdit_de_substituer_une_valeur_autorisee():
    """Les valeurs autorisées traduisent ce qui est dit ; elles ne corrigent rien.

    Le remède de ce défaut est ICI, et pas dans la validation — qui faisait
    déjà son travail. Le planificateur lit « valeurs autorisées : 1, 2, 3 »
    dans le prompt et, sur « une passagère de 4e classe », rendait `pclass=3` :
    la validation recevait un 3 valide et ne pouvait rien voir. L'utilisateur
    recevait une probabilité sur une question qu'il n'avait pas posée, et la
    substitution n'apparaissait que dans `reason`, que l'interface ne montre
    pas (mesuré sur les deux moteurs, docs/surface-conversationnelle.md §17).

    Ce test garde la RÈGLE, pas sa rédaction : ses deux faces, sans lesquelles
    elle ne tient pas. Rendre l'agent littéral casserait l'extraction, qui est
    sa raison d'être — « 1re classe », « classe 1 », « embarquée à
    Southampton » doivent continuer d'aboutir ; une valeur que rien n'autorise
    doit remonter telle quelle pour que le refus la cite.
    """
    # à plat : le prompt est replié à 79 colonnes, et une règle ne vaut pas
    # moins parce qu'un retour à la ligne tombe au milieu d'elle
    gabarit = " ".join(planner_template().split())

    # traduire : la face sans laquelle l'extraction meurt
    assert "embarked='S'" in gabarit
    assert "pclass=1" in gabarit
    # transmettre : la face sans laquelle la substitution revient
    assert "telle qu'il l'a écrite" in gabarit
    assert "la valeur autorisée la plus proche" in gabarit


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


def test_les_marqueurs_distinguent_bien_chaque_agent():
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
        scripted_llm.SYSTEME,
        scripted_llm.RAPPEL,
        scripted_llm.REPARATION,
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


# --- ce qu'un prompt tient, et ce qu'il ne tient pas -----------------------------
#
# Ce fichier porte UN test de contenu, et sa raison a changé. Il tenait la
# consigne de classement du prompt SQL comme si elle était LE correctif : la
# grandeur qui classe va dans le SELECT, annoncée 0/5 → 5/5.
#
# Remesurée sur DIX formulations de la même demande, trois tirages chacune,
# elle tient 27 SQL en règle sur 30 — elle lâche sur « les plus sollicitées »,
# qu'elle n'énumère pas. Sa reformulation abstraite, la règle dite sur la
# requête au lieu des mots de la question, fait PIRE : 12/30. Un prompt ne tient
# pas cette propriété.
#
# Elle est tenue par du CODE : `agents/retrieval/classement` lit le SQL produit
# et signale toute expression du ORDER BY absente du SELECT ; la boucle de
# `run_sql` renvoie la remarque au modèle. 30/30, avec ou sans la consigne
# (cf. `tests/unit/retrieval/test_classement.py` et
# `tests/unit/retrieval/test_retrieval_agent.py`).
#
# La consigne reste, DÉGRADÉE de garantie en économie : elle fait écrire la
# projection juste du premier coup plus souvent, donc la vérification relance
# moins — 153 appels LLM contre 164 sans elle, sur les mêmes 30 tirages. C'est
# ce que ce test tient maintenant, et rien de plus.


def test_le_prompt_sql_garde_la_consigne_de_classement_comme_economie():
    """Elle ne garantit rien — elle évite des allers-retours, et c'est mesuré.

    La retirer ne casserait aucun chiffre : la vérification structurelle rend
    30/30 sans elle. Elle coûterait 14 relances au lieu de 3 sur trente tirages.
    Si ce test tombe, relire `docs/sources-de-demonstration.md` avant de
    conclure qu'il ne sert plus à rien.
    """
    prompt = prompts.render(prompts.RETRIEVAL, dialect="postgresql")

    assert "CLASSEMENT" in prompt
    assert "ORDER BY" in prompt


# --- l'empreinte : on peut changer un prompt, pas en silence --------------------

# Les empreintes SHA-256 de TOUS les fichiers de `src/data_analyst_agent/prompts/`,
# au 2026-09-22. Elles ne sont pas là pour empêcher de les modifier : elles sont
# là pour qu'une modification soit un GESTE — on change le prompt, on met
# l'empreinte à jour dans le MÊME commit, et le message de commit le dit.
#
# Pourquoi ce garde-fou existe, et il est mesuré : cinq formulations ont été
# écrites dans le prompt de l'agent système pour lui dire ce que deux planchers
# font désormais tout seuls. Les cinq ont été retirées — chacune coûtait une
# question de la surface conversationnelle, trois tirages sur trois. Un témoin
# de quatre lignes VIDES au même endroit ne coûtait rien : ce n'est donc pas la
# longueur du prompt, c'est son contenu. Un paragraphe ajouté à un prompt est
# une modification du produit, au même titre qu'une ligne de code.
#
# Pourquoi le dictionnaire ENTIER et non un fichier par test : comparer les deux
# dictionnaires d'un coup fait rougir les trois façons de passer au travers —
# modifier un prompt, en AJOUTER un que personne ne couvre, en retirer un.
# C'est le trou par lequel `prompts/rappel.txt` était passé : aucun test ne le
# nommait, et il a reçu un paragraphe sans que rien ne bronche.
EMPREINTES_DES_PROMPTS = {
    "analysis.txt": "9b3c242af88aaca2e9191d6251e11a4b6521fadf5c2c16084109fb60a6954c3e",
    "planner.txt": "daaf6c8ad0060112f7011c64d3b1df170416497be02cb1aaca306f32c8ee125d",
    "rappel.txt": "65b0dc0106aeacfd6f3a0929f5370b9c699726ffe011729fd7cdfec15b58fabc",
    "reparation.txt": "f46ed7dd8f774dd5144ad408d4db334c9523d8772e7b90db75c24b46ac43ddd7",
    "retrieval.txt": "0059d13d1cee25f8aafe2c6257d02f4252a784e21b567e4e68c8d38686c3ac45",
    "synthesis.txt": "c09c6fb74cf472e35d83658bd320b8fb527ef278d9d90e27bba2de95012bc423",
    "systeme.txt": "7694ea81f55286685d618f976105942bdc446cb0ab6d03a454ba0c0d1a5c5ed0",
}


def test_aucun_prompt_n_a_bouge_au_caractere_pres():
    """Les sept prompts, à l'octet près — et le dossier au complet.

    Ce test remplace une empreinte qui ne gardait RIEN : elle hachait
    `prompts.SYSTEME`, c'est-à-dire la chaîne « systeme.txt » — le NOM du
    fichier — et non son contenu. Elle serait restée verte quoi qu'on écrive
    dans le prompt système, ce qui est précisément ce qu'elle prétendait
    interdire. Le nom d'un fichier ne change pas quand on édite le fichier.

    La liste est confrontée à ce que porte le DOSSIER, jamais à une liste de
    constantes écrite à la main : `prompts.py` en déclare sept, mais le tuple
    `TOUS` de ce fichier n'en énumère que cinq, et c'est par ce genre d'écart
    qu'un prompt échappe à sa couverture.
    """
    reels = {
        chemin.name: hashlib.sha256(chemin.read_bytes()).hexdigest()
        for chemin in prompts.PROMPTS_DIR.glob("*.txt")
    }

    assert reels == EMPREINTES_DES_PROMPTS, (
        "un prompt a changé, a été ajouté ou retiré. Si c'est voulu : mets son "
        "empreinte à jour DANS LE MÊME COMMIT, et dis-le dans le message de commit."
    )


def test_l_empreinte_couvre_tout_fichier_de_prompt_du_paquet():
    """Le témoin de l'exhaustivité, et il vise le mode de panne réel.

    Un prompt ajouté demain ne doit pas pouvoir vivre non couvert. Ce test le
    dit autrement que le précédent — lui compare des empreintes, celui-ci
    compare des NOMS — pour que l'échec nomme le fichier oublié plutôt qu'une
    différence de dictionnaire à déchiffrer.
    """
    du_disque = {chemin.name for chemin in prompts.PROMPTS_DIR.glob("*.txt")}

    assert du_disque == set(EMPREINTES_DES_PROMPTS), (
        f"non couvert(s) par l'empreinte : {sorted(du_disque - set(EMPREINTES_DES_PROMPTS))} ; "
        f"disparu(s) du dossier : {sorted(set(EMPREINTES_DES_PROMPTS) - du_disque)}"
    )
