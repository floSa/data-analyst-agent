"""Garde-fous sur le prompt de l'agent de récupération (text-to-SQL)."""

from data_analyst_agent import prompts


def test_prompt_demande_toutes_les_colonnes_pour_une_liste():
    """Lister des individus doit ramener toutes les colonnes (résultat réutilisable)."""
    # sans cette consigne, un « donne-moi les fleurs » peut projeter une seule
    # colonne (ex. SELECT DISTINCT species) et casser la réutilisation en mémoire
    prompt = prompts.gabarit(prompts.RETRIEVAL)

    assert "SELECT *" in prompt
    assert "DISTINCT" in prompt
