"""Ce que rend une question qui NOMME ses sources — et ce qu'elle rend en trop.

« Qu'est-ce que t'appelles source vente, production, stock ? » recevait les CINQ
fiches du catalogue, `iris` et `titanic` compris, pour une question qui en visait
trois. Ce runner mesure cette famille-là : un message qui nomme deux, trois ou
quatre sources déclarées, et la réponse qu'il reçoit.

**L'oracle est tiré du message lui-même**, et il n'est pas écrit à la main : les
sources que la question nomme sont lues dans le catalogue
(``introspection.sources_nommees``), et le verdict compare la réponse à cet
ensemble-là. Trois exigences, et la troisième est celle qui manquait :

1. chaque source nommée est CITÉE dans la réponse ;
2. aucune source déclarée qui n'a pas été nommée ne l'est ;
3. chaque source nommée est DÉCRITE — la réponse porte ce que sa fiche en dit,
   pas seulement son nom. Une réponse qui se contente de répéter les trois noms
   (« j'ai trouvé les sources `ventes`, `production` et `stocks` ») satisfait les
   deux premières et ne répond pas : c'est le défaut jumeau de l'inventaire
   complet, et c'est celui qu'on mesurait avant sans le voir.

**Le fil brut est relevé**, et c'est lui qui a nommé la cause : les appels
d'outil réellement émis avec leurs arguments. Une trace qui dit l'outil sans dire
l'appel ne distingue pas un tour qui a bouclé d'un tour qui a tout demandé.

    uv run python scripts/mesure_sources_nommees.py
    uv run python scripts/mesure_sources_nommees.py --tirages 3
    uv run python scripts/mesure_sources_nommees.py --seulement defaut-usage-reel

Prérequis : ``DAA_CATALOG_PATH=sources/metier/catalogue.yaml``, le catalogue semé
(``scripts/seed_catalogue_metier.py``) et le serveur LLM en place.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import UsageLimits

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Source, load_catalog
from data_analyst_agent.agents.retrieval.faits import ReglagesDuReleve, RelevesDuCatalogue
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.systeme import SystemeDeps, build_systeme_agent

# Le message d'usage réel qui a ouvert le chantier, puis SIX formulations
# écrites APRÈS le correctif — elles n'ont donc rien réglé et ne mesurent pas
# la mémoire de leur auteur. Deux, trois ou quatre sources nommées ; une qui
# écrit les noms au singulier, une qui mêle une source métier à un jeu de
# référence, une qui nomme quatre sources sur cinq.
MESSAGES: tuple[tuple[str, str], ...] = (
    ("defaut-usage-reel", "Qu'est-ce que t'appelles source vente, production, stock ?"),
    ("apercu-quatre", "Donne-moi un aperçu de ventes, production, stocks et titanic."),
    ("reference-deux", "titanic et iris, c'est quoi au juste ?"),
    ("servent-trois", "Explique-moi à quoi servent production, stocks et iris."),
    ("contient-deux", "Ça contient quoi, ventes et stocks ?"),
    ("presente-trois", "Je voudrais comprendre ventes, production et stocks : présente-les-moi."),
    ("entre-deux", "Entre ventes et production, qu'y a-t-il dans chacune ?"),
)

# Ce qu'on tient pour « décrite » : un mot ou un nombre qui n'appartient qu'à la
# FICHE de cette source-là, repéré dans la réponse. Jamais une liste écrite à la
# main — un oracle qui s'écrit à la main mesure son auteur.
#
# **Première version, et pourquoi elle était fausse.** L'oracle cherchait des
# suites de quatre mots prises telles quelles dans la description du catalogue.
# Il comptait donc en échec « la source `ventes` contient 4 tables, dont
# `clients`, `commandes`, `lignes_commande` et `produits`, 673 lignes au
# total » — une réponse qui décrit la source avec les faits qu'on venait de lui
# servir, mais qui les REFORMULE. Un oracle qui exige les mots du catalogue
# mesure la recopie, pas la description, et il aurait fait « corriger » un
# comportement juste. Ce qu'on demande est qu'un fait de CETTE source-ci
# traverse la réponse, dans les mots du modèle ou dans les nôtres.
#
# **Trois caractères et non quatre**, et c'est le même piège une seconde fois :
# à quatre, « la source `titanic` (file) est le Dataset Titanic, contenant 1
# table, `titanic`, avec 891 lignes » comptait pour NON DÉCRITE. Elle l'est —
# `891` est un fait qu'aucune autre fiche ne porte — mais le compte de lignes
# tient en trois chiffres. Trois ne fait pas de bruit ici : une marque doit de
# toute façon être absente de TOUTES les autres fiches pour être retenue.
LONGUEUR_D_UNE_MARQUE = 3


@dataclass
class Releve:
    cle: str
    message: str
    tirage: int
    attendues: tuple[str, ...]
    citees: tuple[str, ...]
    decrites: tuple[str, ...]
    appels: tuple[str, ...]
    caracteres_servis: int
    caracteres_rendus: int
    appels_llm: int
    verdict: str
    pourquoi: str
    reponse: str


def _marques(source: Source, fiches: dict[str, str]) -> set[str]:
    """Les mots de la fiche de cette source qu'aucune autre fiche ne porte.

    Le NOM de la source en est retiré, et c'est tout l'objet de la mesure : une
    réponse qui se contente de répéter les trois noms qu'on lui a donnés porte
    trois noms et zéro fait. Ce qu'on cherche est ce qui prouve qu'elle a LU la
    fiche — son type, un de ses volumes, un mot de ce qu'elle contient.
    """
    a_moi = set(introspection.replie(fiches[source.name]).split())
    ailleurs = {
        mot
        for nom, fiche in fiches.items()
        if nom != source.name
        for mot in introspection.replie(fiche).split()
    }
    propre = {m for m in a_moi - ailleurs if len(m) >= LONGUEUR_D_UNE_MARQUE}
    return propre - {introspection.replie(source.name).strip()}


def juger(releve: Releve) -> tuple[str, str]:
    manquantes = [n for n in releve.attendues if n not in releve.citees]
    if manquantes:
        return "échec", f"source(s) nommée(s) et non citée(s) : {', '.join(manquantes)}"
    en_trop = [n for n in releve.citees if n not in releve.attendues]
    if en_trop:
        return "échec", f"source(s) en trop : {', '.join(en_trop)}"
    muettes = [n for n in releve.attendues if n not in releve.decrites]
    if muettes:
        return "échec", f"source(s) citée(s) mais non décrite(s) : {', '.join(muettes)}"
    return "conforme", f"{len(releve.attendues)} source(s) nommée(s), {len(releve.appels)} appel(s)"


def poser(
    agent, modele, catalogue, registre, releves_du_catalogue, fiches, cle, message, tirage
) -> Releve:
    avant = modele.appels
    deps = SystemeDeps(
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        question=message,
        releves=releves_du_catalogue,
    )
    reglages = get_settings()
    run = agent.run_sync(
        message,
        model=modele,
        deps=deps,
        usage_limits=UsageLimits(request_limit=reglages.systeme_request_limit),
    )
    reponse = run.output
    plat = introspection.replie(reponse)
    attendues = tuple(s.name for s in introspection.sources_nommees(message, catalogue))
    citees = tuple(
        s.name for s in catalogue.sources if f" {introspection.replie(s.name).strip()} " in plat
    )
    decrites = tuple(
        s.name for s in catalogue.sources if any(f" {m} " in plat for m in _marques(s, fiches))
    )
    appels = tuple(
        f"{p.tool_name}({json.dumps(p.args_as_dict(), ensure_ascii=False)})"
        for m in run.all_messages()
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    )
    releve = Releve(
        cle=cle,
        message=message,
        tirage=tirage,
        attendues=attendues,
        citees=citees,
        decrites=decrites,
        appels=appels,
        caracteres_servis=len("\n\n".join(deps.faits)),
        caracteres_rendus=len(reponse),
        appels_llm=modele.appels - avant,
        verdict="",
        pourquoi="",
        reponse=reponse,
    )
    releve.verdict, releve.pourquoi = juger(releve)
    return releve


def rapport(releves: list[Releve], reglages) -> str:
    conformes = sum(1 for r in releves if r.verdict == "conforme")
    cles = list(dict.fromkeys(r.cle for r in releves))
    lignes = [
        "## Une question qui nomme ses sources, mesurée",
        "",
        f"Moteur : `{reglages.llm_base_url}` (`{reglages.llm_model}`)",
        f"Catalogue : `{reglages.catalog_path}`",
        "",
        f"**{conformes}/{len(releves)} tours conformes** "
        f"({len(cles)} messages, {len(releves) // max(len(cles), 1)} tirages chacun), "
        f"{sum(r.appels_llm for r in releves)} appels LLM, "
        f"{sum(r.caracteres_servis for r in releves) // max(len(releves), 1)} caractères servis "
        "par tour en moyenne.",
        "",
        "| message | nommées | appels d'outil émis | servis | rendus | appels LLM "
        "| score | ce qui a décidé |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cle in cles:
        lot = [r for r in releves if r.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "conforme")
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        premier = lot[0]
        appels = "<br>".join(dict.fromkeys(a for r in lot for a in r.appels)) or "(aucun)"
        lignes.append(
            f"| `{cle}` — {premier.message} | {', '.join(premier.attendues) or '(aucune)'} "
            f"| `{appels}` | {premier.caracteres_servis} | {premier.caracteres_rendus} "
            f"| {premier.appels_llm} | **{bons}/{len(lot)}** "
            f"| {' ; '.join(echecs) if echecs else premier.pourquoi} |"
        )
    return "\n".join(lignes)


def main() -> None:
    parseur = argparse.ArgumentParser(description="Les questions qui nomment leurs sources.")
    parseur.add_argument("--markdown", type=Path, default=None)
    parseur.add_argument("--json", type=Path, default=None)
    parseur.add_argument("--seulement", nargs="*", default=None)
    parseur.add_argument("--tirages", type=int, default=3)
    args = parseur.parse_args()

    reglages = get_settings()
    catalogue = load_catalog(reglages.catalog_path)
    registre = Registry.load(reglages.models_registry_path)
    releves_du_catalogue = RelevesDuCatalogue(catalogue, ReglagesDuReleve.from_settings(reglages))
    # Les fiches telles que les outils les servent : c'est en elles, et non dans
    # le YAML, que l'oracle va chercher ce qui distingue une source d'une autre.
    fiches = {
        s.name: introspection.fiche_de_source(s, releves_du_catalogue.de(s.name))
        for s in catalogue.sources
    }
    modele = ModeleCompteur(build_model(reglages))
    agent = build_systeme_agent()
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")

    messages = [(c, m) for c, m in MESSAGES if not args.seulement or c in args.seulement]
    releves: list[Releve] = []
    total = len(messages) * args.tirages
    numero = 0
    for tirage in range(1, args.tirages + 1):
        for cle, message in messages:
            numero += 1
            print(f"[{numero}/{total}] {cle}·{tirage} — « {message} »")
            depart = time.monotonic()
            releve = poser(
                agent,
                modele,
                catalogue,
                registre,
                releves_du_catalogue,
                fiches,
                cle,
                message,
                tirage,
            )
            releves.append(releve)
            duree = int((time.monotonic() - depart) * 1000)
            print(f"    appels : {', '.join(releve.appels) or '(aucun)'}")
            print(
                f"    → {releve.verdict} ({releve.pourquoi}) — {releve.caracteres_servis} car. "
                f"servis, {releve.caracteres_rendus} rendus, {releve.appels_llm} appels LLM, "
                f"{duree} ms"
            )
            print(f"    réponse : {' '.join(releve.reponse.split())[:200]}\n", flush=True)

    texte = rapport(releves, reglages)
    print(texte)
    if args.markdown:
        args.markdown.write_text(texte + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps([r.__dict__ for r in releves], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
