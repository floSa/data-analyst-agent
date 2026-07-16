"""Garde-fous sur le prompt de l'agent de récupération (text-to-SQL)."""

from data_analyst_agent.agents.retrieval.agent import SYSTEM_PROMPT


def test_prompt_demande_toutes_les_colonnes_pour_une_liste():
    """Lister des individus doit ramener toutes les colonnes (résultat réutilisable)."""
    # sans cette consigne, un « donne-moi les fleurs » peut projeter une seule
    # colonne (ex. SELECT DISTINCT species) et casser la réutilisation en mémoire
    assert "SELECT *" in SYSTEM_PROMPT
    assert "DISTINCT" in SYSTEM_PROMPT
