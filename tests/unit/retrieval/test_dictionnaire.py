"""La règle qui décide ce que l'agent SQL lit du dictionnaire de sa source.

Ce qui est tenu ici : le dictionnaire passe ENTIER tant qu'il tient dans le
budget ; au-delà, il est amputé par sections entières et l'amputation est
NOMMÉE. Un dictionnaire coupé en silence remplacerait un chiffre faux silencieux
par un autre, et c'est précisément le défaut qu'on répare.
"""

from pathlib import Path

import pytest

from data_analyst_agent.agents.retrieval.dictionnaire import (
    EN_TETE,
    bloc_de_prompt,
    preparer,
)

DICO = """\
# Dictionnaire — `ventes`

Le modèle en une phrase : une commande porte des lignes.

## `commandes`

| Colonne | Sens |
|---|---|
| `statut` | `T` terminée, `A` annulée. |

## Les pièges de cette source

`statut = 'A'` ne compte pas : ce sont des commandes annulées.
"""


def test_sans_dictionnaire_rien_n_est_injecte():
    """Le cas de `titanic` et `iris` : aucune des deux n'en déclare."""
    for rien in (None, "", "   \n\n "):
        prepare = preparer(rien, 8000)
        assert prepare.texte == ""
        assert not prepare.tronque
        assert bloc_de_prompt(prepare) == ""


def test_sous_le_budget_le_dictionnaire_passe_entier():
    prepare = preparer(DICO, 8000)
    assert prepare.texte == DICO.strip()
    assert not prepare.tronque
    assert prepare.avis == ""
    assert prepare.sections_ecartees == ()


def test_budget_nul_desactive_le_plafond():
    """0 = « je sais ce que je fais » — le dictionnaire passe quelle que soit sa taille."""
    enorme = DICO + "\n\n## Annexe\n\n" + ("blabla " * 5000)
    prepare = preparer(enorme, 0)
    assert prepare.texte == enorme.strip()
    assert not prepare.tronque


def test_au_dela_du_budget_on_coupe_par_sections_entieres():
    """Jamais au milieu d'une phrase : une demi-règle se lit comme une remarque."""
    prepare = preparer(DICO, 120)
    assert prepare.tronque
    # Ce qui est gardé l'est en entier — aucun morceau de section.
    for garde in prepare.sections_gardees:
        assert garde in ("(préambule)", "Dictionnaire — `ventes`", "`commandes`")
    assert "Les pièges de cette source" in prepare.sections_ecartees
    # Et rien de ce qui a été gardé n'est amputé : chaque bloc gardé est
    # présent tel quel dans le document d'origine.
    for ligne in prepare.texte.splitlines():
        assert ligne in DICO


def test_l_amputation_est_nommee_section_par_section():
    prepare = preparer(DICO, 120)
    assert "Les pièges de cette source" in prepare.avis
    assert "DAA_RETRIEVAL_DICTIONARY_MAX_CHARS" in prepare.avis
    assert "120" in prepare.avis


def test_une_section_plus_grosse_que_le_budget_est_coupee_sur_une_ligne():
    """Le seul chemin où une phrase peut manquer sa suite — annoncé comme les autres."""
    monobloc = "# Tout\n" + "\n".join(f"ligne numéro {i}" for i in range(500))
    prepare = preparer(monobloc, 200)
    assert prepare.tronque
    assert prepare.avis
    assert not prepare.texte.endswith("ligne numéro 499")
    # coupé sur une frontière de ligne : la dernière ligne gardée est entière
    assert prepare.texte.splitlines()[-1] in monobloc.splitlines()


def test_le_preambule_est_une_section_comme_les_autres():
    """Ce qui précède le premier titre décrit le modèle de données, et n'a pas de titre.

    Il est gardé en premier parce qu'il vient en premier — pas parce qu'on l'a
    privilégié : la règle ne connaît que l'ordre du document et la taille.
    """
    avec_preambule = "Une commande porte des lignes.\n\n" + DICO
    prepare = preparer(avec_preambule, 120)
    assert "(préambule)" in prepare.sections_gardees
    assert "Une commande porte des lignes." in prepare.texte
    assert prepare.tronque


def test_un_markdown_sans_aucun_titre_reste_traitable():
    """Le dictionnaire est du Markdown LIBRE : rien n'oblige à des titres."""
    plat = "\n".join(f"phrase {i}" for i in range(400))
    prepare = preparer(plat, 100)
    assert prepare.tronque
    assert prepare.texte
    assert len(prepare.texte) <= 100


def test_le_bloc_de_prompt_porte_la_consigne_et_le_texte():
    """Recopier le Markdown sans dire qu'il FAIT AUTORITÉ laisse le modèle arbitrer."""
    bloc = bloc_de_prompt(preparer(DICO, 8000))
    assert EN_TETE in bloc
    assert "commandes annulées" in bloc
    assert "ATTENTION" not in bloc  # rien n'a été coupé, rien à signaler


def test_le_bloc_de_prompt_dit_au_modele_ce_qu_il_ne_voit_pas():
    """Un modèle qui sait son dictionnaire incomplet peut le dire ; sinon il affirme."""
    bloc = bloc_de_prompt(preparer(DICO, 120))
    assert "ATTENTION" in bloc
    assert "Les pièges de cette source" in bloc


@pytest.mark.parametrize(
    "nom", ["exploitation", "telemetrie", "interventions", "referentiel", "facturation"]
)
def test_les_cinq_dictionnaires_de_la_demonstration_passent_entiers(nom: str):
    """Le budget par défaut est choisi AU-DESSUS du plus gros d'entre eux.

    C'est ce qui permet de dire que la règle de coupe ne perd rien sur ce
    catalogue-là : elle ne s'y déclenche jamais. Si un dictionnaire grossit au
    point de la déclencher, ce test le dira avant l'utilisateur.
    """
    from data_analyst_agent.config import Settings

    chemin = Path("sources/demonstration/dictionnaires") / f"{nom}.md"
    budget = Settings(_env_file=None).retrieval_dictionary_max_chars
    prepare = preparer(chemin.read_text(encoding="utf-8"), budget)
    assert not prepare.tronque, f"{nom} déborde le budget de {budget} caractères"
