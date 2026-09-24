"""Ce que la boucle de correction ajoute à la trace d'erreur : un FAIT, pas un conseil.

Relevé sur « Fais-moi un graphique du chiffre d'affaires 2025 par canal de
vente » (C59, trois tirages identiques) : la trace arrivait au modèle entière,
et elle ne suffisait pas. Le premier code importait ``matplotlib.ticker.Func``,
un nom qui n'existe pas ; la trace disait « cannot import name 'Func' », et le
deuxième essai… réimportait ``Func`` sous un alias. Le modèle n'avait aucun
moyen de trouver seul le nom juste : ni CPython ni IPython ne proposent de
voisin pour un ``from … import`` raté, et le bac à sable n'a pas de réseau
pour aller lire la documentation. Trois essais, trois morts, et une figure que
personne n'a vue.

Ce module répond à trois familles d'erreur, et seulement à elles :

- un NOM absent d'un module (``cannot import name``, ``module … has no
  attribute``) : on demande au noyau lui-même les noms voisins, dans le module
  RÉELLEMENT installé — pas dans une liste recopiée ici qui vieillirait avec
  l'image ;
- un MODULE absent (``No module named``) ;
- une dépendance OPTIONNELLE absente (``DataFrame.to_markdown`` sans
  ``tabulate``) : la méthode existe, pandas est annoncé, et elle meurt quand
  même. Rien dans le code ne laisse deviner que
  c'est une autre bibliothèque qui manque.

Pour tout le reste, rien : la trace seule est ce qu'on a de plus précis.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_analyst_agent.agents.analysis.agent import SandboxLike

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_MODULE = rf"{_IDENT}(?:\.{_IDENT})*"
NOM_IMPORTE_RE = re.compile(rf"cannot import name '({_IDENT})' from '({_MODULE})'")
ATTRIBUT_RE = re.compile(rf"module '({_MODULE})' has no attribute '({_IDENT})'")
MODULE_ABSENT_RE = re.compile(rf"No module named '({_MODULE})'")
# Les deux tournures de pandas : `import_optional_dependency` selon la version,
# et celle de `to_markdown`, relevée telle quelle dans l'image (pandas 2.x).
OPTIONNELLE_RE = re.compile(
    rf"Missing optional dependency '({_MODULE})'|`Import ({_MODULE})` failed"
)

# Exécuté DANS le noyau de la session, là où vivent les modules que le code
# importe. Les deux noms sont passés par `repr` et ont été filtrés par les
# expressions ci-dessus : aucun texte de l'erreur n'entre tel quel dans le code.
_SONDE = """\
import difflib as _d, importlib as _i
try:
    _noms = [n for n in dir(_i.import_module({module!r})) if not n.startswith('_')]
    _cible = {nom!r}.lower()
    _proches = [n for n in _noms if _cible in n.lower()]
    _proches += [n for n in _d.get_close_matches({nom!r}, _noms, n=6, cutoff=0.5)
                 if n not in _proches]
    print(', '.join(_proches[:6]))
except Exception:
    pass
del _d, _i
"""


def noms_voisins(sandbox: SandboxLike, module: str, nom: str) -> list[str]:
    """Les noms publics de ``module`` qui ressemblent à ``nom``, lus dans le noyau."""
    sortie = sandbox.execute(_SONDE.format(module=module, nom=nom))
    if sortie.status != "ok":
        return []
    return [n for n in sortie.stdout.strip().split(", ") if n]


def diagnostiquer(sandbox: SandboxLike, erreur: str | None) -> str:
    """Le fait qui manque à la trace pour corriger, ou "" si on n'en sait pas plus."""
    if not erreur:
        return ""
    if match := NOM_IMPORTE_RE.search(erreur):
        nom, module = match.groups()
    elif match := ATTRIBUT_RE.search(erreur):
        module, nom = match.groups()
    else:
        nom = module = ""
    if module:
        voisins = noms_voisins(sandbox, module, nom)
        if voisins:
            return (
                f"`{module}` n'a pas de nom `{nom}` dans la version installée. "
                f"Noms voisins qui existent : {', '.join(f'`{v}`' for v in voisins)}."
            )
        return f"`{module}` n'a pas de nom `{nom}` dans la version installée."
    if match := OPTIONNELLE_RE.search(erreur):
        return (
            f"`{match.group(1) or match.group(2)}` n'est pas installé dans le bac à "
            "sable et ne peut pas l'être : la méthode qui l'a appelé ne marchera à "
            "aucun essai."
        )
    if match := MODULE_ABSENT_RE.search(erreur):
        return f"`{match.group(1)}` n'est pas installé dans le bac à sable et ne peut pas l'être."
    return ""
