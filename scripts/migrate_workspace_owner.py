"""Reprise de l'existant : range les conversations sous un propriétaire.

Avant le cloisonnement, toutes les conversations vivaient côte à côte à la
racine de ``workspace_dir``. Elles y vivent toujours : le code, lui, les cherche
désormais sous ``workspace_dir/<utilisateur>/``. Sans cette reprise, elles ne
sont pas perdues — elles sont invisibles, et un fil rouvert repartirait vide.

    # à blanc (défaut) : montre ce qui serait fait, n'écrit rien
    uv run python scripts/migrate_workspace_owner.py --workspace <dossier> --owner floSa

    # pour de vrai
    uv run python scripts/migrate_workspace_owner.py --workspace <dossier> --owner floSa --appliquer

**Le mode à blanc est le défaut.** Une migration qui déplace des dossiers de
données ne doit pas pouvoir partir d'une faute de frappe ; c'est ``--appliquer``
qui engage.

Trois propriétés tenues :

- **idempotente** — relancée, elle ne trouve plus rien à faire et le dit ;
- **tout ou rien sur les collisions** — si un fil à déplacer porte le nom d'un
  fil déjà rangé sous le propriétaire, RIEN n'est déplacé. Écraser serait perdre
  une conversation, et déplacer la moitié du lot laisserait un état que la
  relance ne saurait pas démêler ;
- **reprenable** — le propriétaire est inscrit dans la transcription AVANT le
  déplacement du dossier. Une interruption laisse donc soit un fil encore à la
  racine (que la relance déplacera), soit un fil rangé et estampillé. Jamais un
  fil rangé sans propriétaire, qui serait illisible pour son propre compte.

Ce n'est volontairement pas une migration implicite au démarrage : elle déplace
des données, elle doit être lancée par quelqu'un qui l'a décidé, sur un dossier
qu'il a nommé, après l'avoir vue à blanc.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from data_analyst_agent.auth.accounts import normalize_login
from data_analyst_agent.config import get_settings
from data_analyst_agent.orchestrator.conversations import Conversation, ConversationStore
from data_analyst_agent.orchestrator.workspace import (
    LOCKS_DIR,
    make_private_dir,
    safe_dir_name,
    write_text_atomic,
)

TRANSCRIPT = ConversationStore.TRANSCRIPT


class MigrationError(RuntimeError):
    """Refus de migrer : l'état sur disque ne permet pas un déplacement sûr."""


@dataclass
class Plan:
    """Ce que la migration ferait, avant de le faire."""

    workspace: Path
    proprietaire: str
    cible: Path
    a_deplacer: list[Path] = field(default_factory=list)
    a_estampiller: list[Path] = field(default_factory=list)
    illisibles: list[Path] = field(default_factory=list)
    ignores: list[Path] = field(default_factory=list)

    @property
    def vide(self) -> bool:
        return not self.a_deplacer and not self.a_estampiller


def _lire(dossier: Path) -> Conversation | None:
    """La transcription d'un dossier, ou ``None`` si absente ou illisible."""
    path = dossier / TRANSCRIPT
    if not path.exists():
        return None
    try:
        return Conversation.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _est_une_conversation(dossier: Path) -> bool:
    return dossier.is_dir() and (dossier / TRANSCRIPT).exists()


def construire_le_plan(workspace: Path, proprietaire: str) -> Plan:
    """Inspecte le dossier et dit ce qu'il y aurait à faire. N'écrit rien."""
    cible = workspace / safe_dir_name(proprietaire)
    plan = Plan(workspace=workspace, proprietaire=proprietaire, cible=cible)
    if not workspace.is_dir():
        return plan

    if _est_une_conversation(cible):
        raise MigrationError(
            f"{cible} est une CONVERSATION, pas une racine d'utilisateur — "
            f"un fil porte le nom du propriétaire {proprietaire!r}. Renommez-le d'abord."
        )

    for entree in sorted(workspace.iterdir()):
        if entree == cible or entree.name == LOCKS_DIR:
            continue
        if not entree.is_dir():
            plan.ignores.append(entree)
        elif not _est_une_conversation(entree):
            # Racine d'un autre utilisateur, dossier étranger, reliquat : on n'y
            # touche pas. Seul ce qui porte une transcription est une conversation.
            plan.ignores.append(entree)
        elif _lire(entree) is None:
            plan.illisibles.append(entree)
        else:
            plan.a_deplacer.append(entree)

    # Déjà rangés sous le propriétaire mais sans son estampille : le cas d'une
    # migration interrompue, ou d'un dossier restauré à la main.
    if cible.is_dir():
        for entree in sorted(cible.iterdir()):
            if entree.name == LOCKS_DIR or not _est_une_conversation(entree):
                continue
            fil = _lire(entree)
            if fil is None:
                plan.illisibles.append(entree)
            elif fil.owner != proprietaire:
                plan.a_estampiller.append(entree)

    collisions = [d for d in plan.a_deplacer if (cible / d.name).exists()]
    if collisions:
        raise MigrationError(
            "collision de noms — rien n'a été déplacé. Ces fils existent déjà sous "
            f"{cible} : {', '.join(d.name for d in collisions)}"
        )
    return plan


def _estampiller(dossier: Path, proprietaire: str) -> None:
    """Inscrit le propriétaire dans la transcription, atomiquement.

    L'écriture est une retouche du JSON, et non un aller-retour par le modèle :
    ``Conversation`` ignore les champs qu'il ne connaît pas, donc re-sérialiser
    effacerait tout ce qu'une version antérieure — ou postérieure — aurait mis
    dans le fichier, et remplirait au passage des défauts que personne n'a
    écrits. Une migration ne doit changer que ce qu'elle vient changer.

    La relecture par le modèle sert quand même : elle vérifie que le fichier est
    lisible avant qu'on y touche.
    """
    path = dossier / TRANSCRIPT
    if _lire(dossier) is None:  # pragma: no cover - le plan a déjà écarté les illisibles
        raise MigrationError(f"transcription illisible : {path}")
    charge = json.loads(path.read_text(encoding="utf-8"))
    charge["owner"] = proprietaire
    write_text_atomic(path, json.dumps(charge, ensure_ascii=False, indent=2))


def _nettoyer_le_verrou(workspace: Path, nom: str) -> None:
    """Retire le verrou resté à la racine pour un fil qui n'y est plus.

    Les verrous sont des fichiers vides recréés à la demande, et le nouveau vit
    sous la racine du propriétaire : celui de la racine commune ne sérialise
    plus rien.
    """
    (workspace / LOCKS_DIR / f"{nom}.lock").unlink(missing_ok=True)


def appliquer(plan: Plan) -> None:
    """Exécute le plan. L'estampille précède le déplacement (cf. « reprenable »)."""
    if plan.vide:
        return
    make_private_dir(plan.cible)
    for dossier in plan.a_deplacer:
        _estampiller(dossier, plan.proprietaire)
        # `os.replace` sur un dossier : atomique, et refuse d'écraser une cible
        # non vide. Le plan a déjà vérifié qu'elle n'existe pas.
        os.replace(dossier, plan.cible / dossier.name)
        _nettoyer_le_verrou(plan.workspace, dossier.name)
    for dossier in plan.a_estampiller:
        _estampiller(dossier, plan.proprietaire)

    verrous = plan.workspace / LOCKS_DIR
    if verrous.is_dir() and not any(verrous.iterdir()):
        verrous.rmdir()


def rendre_compte(plan: Plan, applique: bool) -> str:
    """Le compte rendu, identique en forme à blanc et pour de vrai."""
    fait, ferait = (
        ("déplacée(s)", "estampillée(s)") if applique else ("à déplacer", "à estampiller")
    )
    lignes = [
        f"dossier      : {plan.workspace}",
        f"propriétaire : {plan.proprietaire}",
        f"cible        : {plan.cible}",
        "",
    ]
    if not plan.workspace.is_dir():
        return "\n".join([*lignes, "dossier inexistant — rien à faire."])
    if plan.vide:
        lignes.append("rien à faire : aucune conversation à la racine, tout est déjà rangé.")
    else:
        lignes.append(f"{len(plan.a_deplacer)} conversation(s) {fait} :")
        lignes += [f"  {d.name}" for d in plan.a_deplacer]
        if plan.a_estampiller:
            lignes.append(f"{len(plan.a_estampiller)} conversation(s) {ferait} sur place :")
            lignes += [f"  {d.name}" for d in plan.a_estampiller]
    if plan.illisibles:
        lignes.append(
            f"{len(plan.illisibles)} transcription(s) illisible(s), laissée(s) en place :"
        )
        lignes += [f"  {d.name}" for d in plan.illisibles]
    if plan.ignores:
        lignes.append(f"{len(plan.ignores)} entrée(s) ignorée(s) (pas une conversation) :")
        lignes += [f"  {d.name}" for d in plan.ignores]
    if not applique and not plan.vide:
        lignes += ["", "MODE À BLANC — rien n'a été écrit. Ajoutez --appliquer pour exécuter."]
    return "\n".join(lignes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Par défaut, la commande n'écrit rien : elle montre ce qu'elle ferait.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        metavar="DOSSIER",
        help="dossier des conversations (défaut : DAA_WORKSPACE_DIR)",
    )
    parser.add_argument(
        "--owner",
        required=True,
        metavar="LOGIN",
        help="compte à qui attribuer les conversations trouvées à la racine",
    )
    parser.add_argument(
        "--appliquer",
        action="store_true",
        help="écrire réellement (sans ce drapeau, la commande tourne à blanc)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    workspace = Path(args.workspace or settings.workspace_dir)
    # La MÊME normalisation que celle des comptes : le dossier doit être celui
    # où l'application ira chercher, pas celui que la graphie de l'argument
    # suggère.
    proprietaire = normalize_login(args.owner)
    if not proprietaire:
        print("refusé : propriétaire vide", file=sys.stderr)
        return 1
    try:
        plan = construire_le_plan(workspace, proprietaire)
        if args.appliquer:
            appliquer(plan)
    except MigrationError as refus:
        print(f"refusé : {refus}", file=sys.stderr)
        return 1
    print(rendre_compte(plan, applique=args.appliquer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
