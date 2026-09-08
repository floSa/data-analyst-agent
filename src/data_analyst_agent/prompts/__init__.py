"""Prompts système de l'application, servis depuis des fichiers.

Les prompts vivaient en chaînes Python, dans quatre modules différents
(`orchestrator/plan.py`, `agents/retrieval/agent.py`, `agents/analysis/agent.py`,
`orchestrator/graph.py`). Ce dépôt est un socle : plusieurs cas d'usage seront
bâtis dessus, et le prompt est le premier endroit qu'on voudra ajuster pour
chacun — les prompts en place sont écrits pour un modèle *coder*, alors que le
serveur en service sert un modèle généraliste (audit §5.3). Les garder dans le
code, c'est demander une modification de source pour changer une consigne.

Même mécanique que ``api/pages.py`` pour les pages HTML : lecture une fois,
substitution minuscule, aucun moteur de gabarits.

**Substitution par remplacement littéral, pas par ``str.format``.** C'est le
point qui change par rapport au code d'avant : dès qu'un prompt vit dans un
fichier qu'on édite sans relancer les tests, une accolade ordinaire — un
exemple JSON, un dictionnaire Python montré au modèle — ferait lever
``str.format`` en pleine requête. Un ``replace`` sur ``{cle}`` ignore les
autres accolades.

**Extension ``.txt`` et non ``.md``, à dessein.** ruff 0.16 reformate les blocs
de code des fichiers Markdown : un ``uv run ruff format .`` réécrirait le
contenu du prompt d'analyse, qui montre du Python au modèle. Un ``.txt``
n'intéresse aucun formateur.

Les prompts sont **identifiés par leur première ligne** hors substitution
(``marqueur``) : c'est ce qui permet à la doublure de test de router les
réponses par agent sans recopier à la main un morceau de prompt — cf.
``tests/helpers/scripted_llm.py``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

PLANNER = "planner.txt"
RETRIEVAL = "retrieval.txt"
ANALYSIS = "analysis.txt"
SYNTHESIS = "synthesis.txt"
SYSTEME = "systeme.txt"

# Deux FRAGMENTS, et non deux prompts : ils s'ajoutent à ``RETRIEVAL`` quand la
# source déclare un dictionnaire, et quand la conversation a un tour précédent.
# Ils sont ici pour la même raison que les quatre autres — un prompt s'ajuste
# sans toucher au code — mais n'ont pas de marqueur : ils ne désignent aucun
# agent, ils complètent celui de la récupération.
RETRIEVAL_DICTIONARY = "retrieval_dictionary.txt"
RETRIEVAL_HISTORY = "retrieval_history.txt"


@lru_cache
def gabarit(nom: str) -> str:
    """Contenu brut d'un prompt, lu une fois (le relire à chaque appel ne sert rien).

    Rendu tel quel, marqueurs de substitution compris : l'orchestrateur pèse le
    gabarit AVANT de le composer, parce qu'un budget de tokens se décompte sur
    ce qui sera réellement envoyé et que les quelques caractères de ``{sources}``
    vont du bon côté (cf. ``Orchestrator._peser_le_prompt``).
    """
    return (PROMPTS_DIR / nom).read_text(encoding="utf-8")


def render(nom: str, **valeurs: str) -> str:
    """Rend un prompt en remplaçant chaque ``{cle}`` par sa valeur, telle quelle.

    Aucun échappement, contrairement à ``api/pages.render`` : la destination
    est un modèle de langage, pas un navigateur — il n'y a pas de balise à
    neutraliser, et échapper le schéma d'une base le rendrait illisible.
    """
    prompt = gabarit(nom)
    for cle, valeur in valeurs.items():
        prompt = prompt.replace("{" + cle + "}", str(valeur))
    return prompt


def marqueur(nom: str) -> str:
    """Ce qui identifie ce prompt dans un prompt système déjà composé.

    Sa première ligne, arrêtée avant le premier marqueur de substitution : la
    plus longue portion dont on soit sûr qu'elle traverse ``render`` intacte.

    Existe pour une raison précise. La doublure de test route ses réponses vers
    le bon agent en cherchant un morceau du prompt système, et ces morceaux
    étaient recopiés à la main dans le helper — « planificateur », « expert
    SQL »… (audit §5.3). Externaliser les prompts sans traiter ce couplage
    aurait laissé une suite entière tomber sur une reformulation de prompt, avec
    pour seul message « aucun script pour le prompt système ». Dérivé du
    fichier, le marqueur suit le prompt : le fichier reste la seule source.
    """
    return gabarit(nom).splitlines()[0].split("{", 1)[0].strip()
