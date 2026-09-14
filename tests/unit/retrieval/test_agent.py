"""Garde-fous sur le prompt de l'agent de récupération (text-to-SQL)."""

from data_analyst_agent import prompts


def test_prompt_demande_toutes_les_colonnes_pour_une_liste():
    """Lister des individus doit ramener toutes les colonnes (résultat réutilisable)."""
    # sans cette consigne, un « donne-moi les fleurs » peut projeter une seule
    # colonne (ex. SELECT DISTINCT species) et casser la réutilisation en mémoire
    prompt = prompts.gabarit(prompts.RETRIEVAL)

    assert "SELECT *" in prompt
    assert "DISTINCT" in prompt


def test_prompt_enseigne_la_mesure_par_colonne_en_une_requete():
    """La famille « compter/agréger/décrire une table sans la lire ligne à ligne ».

    Sans cette consigne, « quelles colonnes ont des valeurs manquantes ? » part
    soit en ``SELECT *`` (179 lignes rendues au lieu de la liste des colonnes),
    soit en une requête PAR colonne — et le plafond d'allers-retours tombe
    avant la réponse. Le prompt doit nommer la famille, pas le cas.
    """
    prompt = prompts.gabarit(prompts.RETRIEVAL)

    assert "valeurs manquantes" in prompt
    assert "distinctes par colonne" in prompt
    assert "la plus remplie" in prompt
    # une seule requête, une seule ligne, toutes les colonnes
    assert "Une SEULE requête" in prompt
    assert "UNE SEULE ligne" in prompt
    assert "TOUTES les colonnes" in prompt
    assert "JAMAIS une requête par colonne" in prompt
    # et la lecture du résultat : 0 n'est pas « concernée »
    assert "une mesure à 0" in prompt
