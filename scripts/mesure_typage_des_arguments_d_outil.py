"""Mesure ce que chaque serveur LLM rend comme TYPE dans les arguments d'un tool.

Une prédiction complète aboutissait sous Ollama et était refusée sous vLLM,
pour la même question et le même modèle : le planificateur rendait `pclass=1`
d'un côté, `pclass='1'` de l'autre, et `Literal[1, 2, 3]` refuse `'1'`.

L'explication de travail — « vLLM rend ses arguments d'outil en chaînes » —
était fausse, et ce runner est ce qui l'a montré. Il pose à chaque serveur un
tool dont le JSON Schema DÉCLARE ses types (`integer`, `number`, `boolean`, et
un `integer` sous `enum`, qui est la forme d'un `Literal[1, 2, 3]`) et affiche
le type Python de chaque argument rendu. Les deux serveurs rendent la même
chose.

L'écart est ailleurs : dans le seul endroit du système où le schéma ne déclare
rien — `Plan.features`, un `dict[str, Any]`, soit `additionalProperties: true`.
Sans type annoncé, chaque serveur devine. C'est pourquoi la conversion vit dans
`agents/inference/validation.py`, où le type attendu existe enfin.

    uv run python scripts/mesure_typage_des_arguments_d_outil.py
    uv run python scripts/mesure_typage_des_arguments_d_outil.py --moteur vllm

Contre les VRAIS serveurs, comme `live_scenarios.py` et à la différence de la
suite pytest. Un moteur injoignable est signalé et n'interrompt pas les autres.

Mesures dans docs/VLLM.md §8.4 et docs/surface-conversationnelle.md §16.
"""

from __future__ import annotations

import argparse
import json
import sys

from openai import OpenAI, OpenAIError

# Les deux moteurs de la machine de dev. Les URL sont écrites ici et non lues
# dans le `.env` : ce runner COMPARE les serveurs, il ne s'adresse pas à celui
# qui est en service.
MOTEURS: dict[str, tuple[str, str]] = {
    "ollama": ("http://localhost:11434/v1", "gemma4:e4b"),
    "vllm": ("http://localhost:8100/v1", "google/gemma-4-E4B-it-qat-w4a16-ct"),
}

# Un tool qui déclare tout ce qu'un schéma de features peut demander : un
# entier, un flottant, un booléen, et un entier sous `enum` — la forme que
# prend un `Literal[1, 2, 3]` une fois traduit en JSON Schema.
OUTIL = {
    "type": "function",
    "function": {
        "name": "reserver",
        "description": "Réserve une table au restaurant.",
        "parameters": {
            "type": "object",
            "properties": {
                "nom": {"type": "string", "description": "Nom du client"},
                "convives": {"type": "integer", "description": "Nombre de convives"},
                "acompte": {"type": "number", "description": "Acompte versé en euros"},
                "terrasse": {"type": "boolean", "description": "Table en terrasse ?"},
                "etage": {"type": "integer", "enum": [1, 2, 3], "description": "Étage"},
            },
            "required": ["nom", "convives", "acompte", "terrasse", "etage"],
        },
    },
}

QUESTION = (
    "Réserve une table pour Dupont, 4 convives, 25.5 euros d'acompte, en terrasse, au 2e étage."
)

# Ce qu'on attend de chaque argument, pour que le verdict soit mécanique.
ATTENDUS: dict[str, type] = {
    "nom": str,
    "convives": int,
    "acompte": float,
    "terrasse": bool,
    "etage": int,
}


def _conforme(valeur: object, attendu: type) -> bool:
    """Le type rendu convient-il ? Un entier JSON vaut pour un `number`.

    `bool` est vérifié en premier : en Python, `True` est aussi un `int`, et
    sans cette marche un booléen passerait pour un entier conforme.
    """
    if attendu is bool:
        return isinstance(valeur, bool)
    if isinstance(valeur, bool):
        return False
    if attendu is float:
        return isinstance(valeur, int | float)
    return isinstance(valeur, attendu)


def interroger(nom_moteur: str, base_url: str, modele: str) -> bool:
    """Pose la question au serveur et rend True si tous les types conviennent."""
    print(f"\n=== {nom_moteur} — {base_url} ({modele})")
    client = OpenAI(base_url=base_url, api_key="api-key-not-set", timeout=120.0, max_retries=0)
    try:
        reponse = client.chat.completions.create(
            model=modele,
            messages=[{"role": "user", "content": QUESTION}],
            tools=[OUTIL],
            tool_choice="required",
            temperature=0.0,
        )
    except OpenAIError as exc:
        print(f"  INJOIGNABLE — {exc}")
        return False

    appels = reponse.choices[0].message.tool_calls or []
    if not appels:
        print(f"  AUCUN APPEL D'OUTIL — texte rendu : {reponse.choices[0].message.content!r}")
        return False

    brut = appels[0].function.arguments
    print(f"  arguments bruts : {brut}")
    arguments = json.loads(brut)
    tout_va_bien = True
    for cle, attendu in ATTENDUS.items():
        if cle not in arguments:
            print(f"    - {cle:9} ABSENT (attendu : {attendu.__name__})")
            tout_va_bien = False
            continue
        valeur = arguments[cle]
        ok = _conforme(valeur, attendu)
        tout_va_bien &= ok
        marque = "ok" if ok else "NON CONFORME"
        print(
            f"    - {cle:9} = {valeur!r:10} rendu {type(valeur).__name__:5} "
            f"| déclaré {attendu.__name__:5} | {marque}"
        )
    return tout_va_bien


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--moteur",
        nargs="*",
        choices=sorted(MOTEURS),
        help="n'interroger que ces moteurs (par défaut : tous)",
    )
    args = parser.parse_args()

    demandes = args.moteur or sorted(MOTEURS)
    verdicts = {nom: interroger(nom, *MOTEURS[nom]) for nom in demandes}

    print("\n--- verdict")
    for nom, ok in verdicts.items():
        print(f"  {nom:8} : {'types déclarés respectés' if ok else 'ÉCART ou serveur muet'}")
    print(
        "\nUn type DÉCLARÉ est respecté par les deux serveurs. Ce qui les sépare est\n"
        "ce qu'ils font quand le schéma ne déclare rien — cf. docs/VLLM.md §8.4."
    )
    sys.exit(0 if all(verdicts.values()) else 1)


if __name__ == "__main__":
    main()
