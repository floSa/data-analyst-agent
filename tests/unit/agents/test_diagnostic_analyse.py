"""Le diagnostic que la boucle de correction joint à la trace (C59).

La sonde des noms voisins tourne dans le VRAI Python de l'hôte ici : elle ne
dépend que de `difflib` et `importlib`, et un module de la bibliothèque standard
suffit à vérifier qu'elle trouve le voisin et ne rend rien d'autre.
"""

import contextlib
import io

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.analysis.agent import message_de_correction, run_analysis
from data_analyst_agent.agents.analysis.diagnostic import diagnostiquer
from data_analyst_agent.config import Settings
from data_analyst_agent.sandbox.client import SandboxResult

# Relevée telle quelle dans le bac à sable, C59, essai 1.
TRACE_FUNC = (
    "ImportError                               Traceback (most recent call last)\n"
    "---> 52 from matplotlib.ticker import Func\n\n"
    "ImportError: cannot import name 'Func' from 'matplotlib.ticker' "
    "(/usr/local/lib/python3.12/site-packages/matplotlib/ticker.py)"
)
TRACE_TABULATE = (
    "ImportError: `Import tabulate` failed.  Use pip or conda to install the tabulate package."
)


class NoyauLocal:
    """Un noyau doublé qui exécute pour de vrai, dans ce processus."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult:
        self.executed.append(code)
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            exec(code, {})
        return SandboxResult(status="ok", stdout=sortie.getvalue())


class NoyauMuet:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, code: str, timeout: float | None = None) -> SandboxResult:
        self.executed.append(code)
        return SandboxResult(status="error", error="la sonde a planté")


def test_nom_importe_absent_rend_les_voisins_du_module_installe():
    noyau = NoyauLocal()
    trace = "ImportError: cannot import name 'chainmap' from 'collections' (/x.py)"
    diagnostic = diagnostiquer(noyau, trace)
    assert "`collections` n'a pas de nom `chainmap`" in diagnostic
    assert "`ChainMap`" in diagnostic


def test_voisin_par_prefixe_quand_difflib_ne_le_voit_pas():
    # Le cas relevé : `Func` et `FuncFormatter` sont sous le seuil de difflib
    # (ratio 0,47) ; c'est l'inclusion qui le trouve.
    noyau = NoyauLocal()
    diagnostic = diagnostiquer(noyau, "ImportError: cannot import name 'Chain' from 'collections'")
    assert "`ChainMap`" in diagnostic


def test_attribut_absent_d_un_module():
    noyau = NoyauLocal()
    diagnostic = diagnostiquer(noyau, "AttributeError: module 'os.path' has no attribute 'joinn'")
    assert "`os.path` n'a pas de nom `joinn`" in diagnostic
    assert "`join`" in diagnostic


def test_sonde_en_echec_rend_le_fait_sans_voisins():
    noyau = NoyauMuet()
    diagnostic = diagnostiquer(noyau, TRACE_FUNC)
    assert diagnostic == "`matplotlib.ticker` n'a pas de nom `Func` dans la version installée."


def test_aucun_texte_de_l_erreur_n_entre_tel_quel_dans_le_code_sonde():
    noyau = NoyauMuet()
    diagnostiquer(noyau, "cannot import name 'x'); import os; os.system('id') #' from 'm'")
    assert noyau.executed == []


def test_dependance_optionnelle_absente():
    noyau = NoyauMuet()
    diagnostic = diagnostiquer(noyau, TRACE_TABULATE)
    assert diagnostic.startswith("`tabulate` n'est pas installé dans le bac à sable")
    assert noyau.executed == []


def test_module_absent():
    diagnostic = diagnostiquer(NoyauMuet(), "ModuleNotFoundError: No module named 'seaborn'")
    assert diagnostic == "`seaborn` n'est pas installé dans le bac à sable et ne peut pas l'être."


def test_toute_autre_erreur_ne_rend_rien_et_ne_sonde_pas():
    noyau = NoyauMuet()
    assert diagnostiquer(noyau, "NameError: name 'dff' is not defined") == ""
    assert diagnostiquer(noyau, None) == ""
    assert noyau.executed == []


def test_message_sans_diagnostic_inchange_au_caractere_pres():
    execution = SandboxResult(status="error", error="boom")
    assert message_de_correction(execution) == (
        "L'exécution a échoué (statut : error).\nErreur :\nboom\n\n"
        "Corrige le problème et renvoie le code COMPLET corrigé."
    )


def test_la_boucle_envoie_la_trace_puis_le_diagnostic():
    recus: list[str] = []
    reponses = ["```python\nfrom collections import chainmap\n```", "```python\nprint(1)\n```"]

    def responder(messages, info):
        recus.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart(reponses.pop(0))])

    class Noyau(NoyauLocal):
        def __init__(self) -> None:
            super().__init__()
            self.essais = [
                SandboxResult(
                    status="error",
                    error="ImportError: cannot import name 'chainmap' from 'collections'",
                ),
                SandboxResult(status="ok", stdout="1\n"),
            ]

        def execute(self, code, timeout=None):
            if code.startswith("import difflib"):
                return super().execute(code, timeout)
            self.executed.append(code)
            return self.essais.pop(0)

    result = run_analysis(
        "Q",
        model=FunctionModel(responder),
        settings=Settings(_env_file=None),
        sandbox=Noyau(),
    )
    assert result.succeeded
    assert result.attempts == 2
    correction = recus[1]
    assert correction.index("cannot import name") < correction.index("Diagnostic :")
    assert "`ChainMap`" in correction
