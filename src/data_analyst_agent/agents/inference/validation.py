"""Validation des features + messages de relance (slot-filling, CADRAGE §7-③).

Logique unique : on valide TOUT payload (dump partiel comme formulaire
complet) ; ce qui manque ou déborde devient une liste d'anomalies structurées
et une question de relance en français. Pas de predict tant que ça ne valide
pas.

Le payload arrive ici **non typé** — ``Plan.features`` est un ``dict[str, Any]``
et c'est délibéré : le planificateur ne connaît pas encore le dataset quand il
l'extrait. C'est donc ICI, et nulle part avant, que le type attendu de chaque
feature existe : ce module réaligne les clés (``align_keys``) puis convertit les
valeurs (``coerce_values``) avant de confier le tout au schéma.
"""

from __future__ import annotations

import re
import types
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError

ProblemKind = Literal[
    "manquant", "hors_bornes", "valeur_non_autorisee", "type_invalide", "champ_inconnu"
]

_KIND_BY_PYDANTIC_TYPE: dict[str, ProblemKind] = {
    "missing": "manquant",
    "greater_than": "hors_bornes",
    "greater_than_equal": "hors_bornes",
    "less_than": "hors_bornes",
    "less_than_equal": "hors_bornes",
    "literal_error": "valeur_non_autorisee",
    "enum": "valeur_non_autorisee",
    "extra_forbidden": "champ_inconnu",
}


class FeatureIssue(BaseModel):
    field: str
    problem: ProblemKind
    message: str


class ValidationOutcome(BaseModel):
    valid: bool
    features: dict | None = None
    issues: list[FeatureIssue] = Field(default_factory=list)

    @property
    def missing_fields(self) -> list[str]:
        return [i.field for i in self.issues if i.problem == "manquant"]


def _describe_field(schema: type[BaseModel], field: str) -> str:
    info = schema.model_fields.get(field)
    if info is not None and info.description:
        return f" ({info.description})"
    return ""


def _to_issue(schema: type[BaseModel], error: dict) -> FeatureIssue:
    field = str(error["loc"][0]) if error["loc"] else "(racine)"
    kind = _KIND_BY_PYDANTIC_TYPE.get(error["type"], "type_invalide")
    received = error.get("input")
    if kind == "manquant":
        message = f"{field}{_describe_field(schema, field)} : valeur manquante"
    elif kind == "champ_inconnu":
        message = f"{field} : champ inconnu pour ce modèle"
    else:
        message = f"{field}{_describe_field(schema, field)} : {error['msg']} (reçu : {received!r})"
    return FeatureIssue(field=field, problem=kind, message=message)


def _canonical(name: str) -> str:
    """Forme comparable d'un nom de feature : « MedInc », « med_inc » → « medinc »."""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def align_keys(schema: type[BaseModel], payload: dict) -> dict:
    """Réaligne les clés du payload sur les noms de champs du schéma.

    L'utilisateur (et donc le LLM qui le recopie) écrit les features avec leur
    nom d'usage — « MedInc=8.3 », le nom canonique du dataset California chez
    scikit-learn — alors que le schéma les déclare en snake_case. Sans ce
    réalignement, la validation réclamait « med_inc : valeur manquante » pour
    une valeur que l'utilisateur venait de donner, et refusait « MedInc » comme
    champ inconnu : une impasse, le même reproche à chaque tour.

    Un nom exact prime toujours ; un nom sans correspondance est laissé tel quel
    pour rester signalé comme champ inconnu.
    """
    champs = set(schema.model_fields)
    par_canon = {_canonical(champ): champ for champ in champs}
    aligne = {cle: valeur for cle, valeur in payload.items() if cle in champs}
    for cle, valeur in payload.items():
        if cle in champs:
            continue
        champ = par_canon.get(_canonical(str(cle)))
        if champ is None:
            aligne[cle] = valeur  # vraiment inconnu : le schéma le dira
        elif champ not in aligne:
            aligne[champ] = valeur
    return aligne


# Les chaînes qu'on accepte de lire comme un booléen. Écrites en clair plutôt
# que confiées à `bool()`, qui rendrait True pour « false ».
_BOOLEENS: dict[str, bool] = {
    "true": True,
    "false": False,
    "vrai": True,
    "faux": False,
    "yes": True,
    "no": False,
    "oui": True,
    "non": False,
    "on": True,
    "off": False,
    "1": True,
    "0": False,
}


def _type_attendu(annotation: Any) -> type | None:
    """Le type scalaire qu'attend un champ, ou ``None`` s'il n'y en a pas UN seul.

    Déplie l'optionnel (``int | None``) et le ``Literal`` : ``Literal[1, 2, 3]``
    attend un ``int``, ``Literal['S', 'C', 'Q']`` une ``str``. Un ``Literal``
    panaché de plusieurs types, ou une union de scalaires différents, ne désigne
    rien sans ambiguïté — on rend ``None`` et on ne touche à rien.
    """
    if get_origin(annotation) is Literal:
        types_des_valeurs = {type(valeur) for valeur in get_args(annotation)}
        return types_des_valeurs.pop() if len(types_des_valeurs) == 1 else None
    if get_origin(annotation) in (Union, types.UnionType):
        candidats = {
            attendu
            for membre in get_args(annotation)
            if membre is not type(None) and (attendu := _type_attendu(membre)) is not None
        }
        return candidats.pop() if len(candidats) == 1 else None
    return annotation if isinstance(annotation, type) else None


def _convertir(valeur: str, attendu: type) -> Any:
    """La chaîne lue comme ``attendu``, ou la chaîne inchangée si c'est illisible.

    Rendre la valeur d'origine plutôt que lever : la seule autorité qui refuse
    est le schéma, et il refusera en citant ce que l'utilisateur a réellement
    écrit — « reçu : 'abc' » plutôt qu'une exception avalée quelque part.
    """
    if attendu is bool:  # avant int : en Python, bool EST un int
        return _BOOLEENS.get(valeur.strip().lower(), valeur)
    if attendu in (int, float):
        try:
            return attendu(valeur.strip())
        except ValueError:
            return valeur
    return valeur


def coerce_values(schema: type[BaseModel], payload: dict) -> dict:
    """Convertit les valeurs TEXTUELLES vers le type que le schéma attend.

    Le même modèle, sur la même question, rend ``pclass=1`` servi par Ollama et
    ``pclass='1'`` servi par vLLM. La prédiction complète était donc refusée sur
    l'un et acceptée sur l'autre — « Input should be 1, 2 or 3 (reçu : '1') » —
    alors que l'extraction était juste dans les deux cas.

    La cause n'est pas que vLLM rendrait ses arguments d'outil en chaînes : sur
    un tool dont le JSON Schema DÉCLARE ``integer``/``number``/``boolean``, les
    deux serveurs rendent les mêmes types (mesuré, docs/VLLM.md §8.4). Ce qui
    diffère, c'est ce qu'ils font quand le schéma ne déclare rien —
    ``Plan.features`` est un ``dict[str, Any]``, soit ``additionalProperties:
    true``, le seul endroit de tout le système où un argument d'outil arrive
    sans type annoncé. Sans consigne, un serveur devine des nombres, l'autre des
    chaînes.

    Pydantic rattrapait déjà les ``int``/``float`` en mode souple ; il ne
    rattrape pas ``Literal[1, 2, 3]``, qui compare des valeurs et pour qui
    ``'1'`` n'est pas ``1``. Le moteur ne doit pas décider si une prédiction
    aboutit : la conversion vit donc ici, à la frontière, DEVANT les trois
    schémas — aucun d'eux n'a à s'en soucier, ni le prochain.

    Et elle ne peut pas vivre plus tôt : le type attendu d'une feature n'existe
    nulle part avant ce module. Le planificateur ne connaît pas encore le
    dataset quand il extrait les valeurs — c'est précisément pourquoi
    ``Plan.features`` n'est pas typé.

    Ce n'est pas un relâchement de la garde. On ne convertit que des chaînes,
    que vers un type sans ambiguïté, et une chaîne illisible est laissée telle
    quelle : ``pclass='4'`` devient ``4`` et reste refusé par le ``Literal``,
    ``pclass='abc'`` reste ``'abc'`` et reste refusé aussi. Ce qui était une
    erreur de validation lisible le demeure.
    """
    converti = dict(payload)
    for nom, valeur in payload.items():
        info = schema.model_fields.get(nom)
        if info is None or not isinstance(valeur, str):
            continue  # champ inconnu (le schéma le dira) ou valeur déjà typée
        attendu = _type_attendu(info.annotation)
        if attendu is not None and attendu is not str:
            converti[nom] = _convertir(valeur, attendu)
    return converti


def validate_features(schema: type[BaseModel], payload: dict) -> ValidationOutcome:
    """Valide un payload contre le schéma du dataset ; anomalies structurées sinon."""
    payload = coerce_values(schema, align_keys(schema, payload))
    try:
        instance = schema.model_validate(payload)
    except ValidationError as exc:
        issues = [_to_issue(schema, e) for e in exc.errors()]
        return ValidationOutcome(valid=False, issues=issues)
    return ValidationOutcome(valid=True, features=instance.model_dump())


def format_reask(dataset: str, issues: list[FeatureIssue]) -> str:
    """Question de relance à poser à l'utilisateur pour compléter/corriger."""
    lines = [f"Je ne peux pas encore lancer la prédiction {dataset} :"]
    lines.extend(f"- {issue.message}" for issue in issues)
    lines.append("Peux-tu me donner ces informations ?")
    return "\n".join(lines)
