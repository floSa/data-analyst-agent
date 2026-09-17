"""Ce que rend une question qui NOMME ses sources — et ce qu'elle rend en trop.

« Qu'est-ce que t'appelles source vente, production, stock ? » recevait les CINQ
fiches du catalogue, `iris` et `titanic` compris, pour une question qui en visait
trois. Ce runner mesure cette famille-là : un message qui nomme deux, trois ou
quatre sources déclarées, et la réponse qu'il reçoit.

**Ce qui est jugé est le texte SERVI, pas la formulation du modèle**, et c'est la
correction qui a le plus changé ce runner. Il appelait l'agent système et lisait
sa sortie brute ; le socle, lui, ne sert cette sortie que si elle porte les faits
(``introspection.defaut_de_fondation``), et sert les faits eux-mêmes sinon. Un
runner qui lit la sortie brute mesure donc un tour que personne ne reçoit — et il
l'a fait dire : « resume moi vite fait ventes, stocks, iris » y apparaissait comme
un tour perdu au planificateur, alors que l'outil était bel et bien appelé, que la
ceinture écartait le ``AUTRE`` du modèle et que l'utilisateur recevait les trois
fiches. Ce runner passe désormais par ``systeme.run_systeme`` et rejoue la
ceinture telle que le nœud du graphe l'applique. La colonne **voie** dit lequel
des deux chemins a servi.

**Les ensembles attendus sont écrits À LA MAIN**, un par message, et c'est un
piège corrigé et non un choix de style. L'oracle les calculait avec
``introspection.sources_nommees`` — la fonction même qu'on mesure. Une source
qu'elle ratait sortait de l'attendu, la barre baissait d'autant, et le tour était
déclaré conforme pour avoir omis ce qu'on ne lui demandait plus. Un oracle qui
appelle le code testé ne mesure que sa cohérence avec lui-même.

Trois exigences sur le texte servi, et la troisième est celle qui manquait :

1. chaque source nommée est CITÉE ;
2. aucune source déclarée qui n'a pas été nommée ne l'est ;
3. chaque source nommée est DÉCRITE — le texte porte ce que sa fiche en dit,
   pas seulement son nom. « Tu travailles sur les sources `production` et
   `stocks`. Dis-moi ce que tu souhaites savoir sur ces sources. » satisfait les
   deux premières : deux noms, zéro fait, pour neuf cent quatre-vingt-six
   caractères servis. C'est le défaut jumeau de l'inventaire complet.

**Et deux témoins, qui doivent ÉCHOUER si la restriction déborde.** Un message
qui ne nomme aucune source (la recherche par sujet) et un message qui nomme un
nom inconnu doivent continuer de recevoir le catalogue ENTIER, pour y choisir ou
pour y corriger. Sans eux, un correctif qui restreint tout passerait pour un
progrès.

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
from dataclasses import dataclass, field
from pathlib import Path

from mesure_surface_conversationnelle import ModeleCompteur
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.retrieval.catalog import Catalog, Source, load_catalog
from data_analyst_agent.agents.retrieval.faits import ReglagesDuReleve, RelevesDuCatalogue
from data_analyst_agent.config import get_settings
from data_analyst_agent.llm import build_model
from data_analyst_agent.orchestrator import introspection
from data_analyst_agent.orchestrator.systeme import run_systeme

# Comment l'inventaire complet s'annonce. C'est NOTRE texte
# (``introspection._liste_des_sources``), donc la marque est fiable — et c'est
# la seule chose que les deux témoins vérifient : ont-ils bien REÇU le catalogue
# entier, à choisir ou à corriger ?
#
# Reçu, et non récité : la marque est cherchée dans les FAITS servis au tour,
# pas dans le texte rendu à l'utilisateur. Une recherche par sujet se répond par
# UNE source — « tu n'as pas à réciter les autres » —, et exiger les cinq dans
# la réponse ferait échouer le témoin sur le comportement qu'il protège. C'est
# la faute que cet oracle a commise à son premier tirage : 0/3 sur les deux
# témoins, pour deux tours parfaitement justes.
MARQUE_DE_L_INVENTAIRE = "j ai acces a"


@dataclass(frozen=True)
class Cas:
    """Un message, et ce que le tour doit en faire — écrit à la main.

    ``attendues`` : les sources que le texte servi doit CITER et DÉCRIRE, et
    elles seules. Écrites ici, jamais calculées : c'est la fonction qu'on mesure
    qui les calculerait, et elle baisserait sa propre barre.

    ``catalogue_entier`` : le tour doit recevoir TOUT le catalogue. C'est le cas
    des deux témoins — chercher par sujet, et se tromper de nom — et c'est ce
    qui fait échouer un correctif qui restreindrait sans discernement.
    """

    cle: str
    message: str
    attendues: tuple[str, ...] = ()
    catalogue_entier: bool = False


# Le message d'usage réel qui a ouvert le chantier, les six formulations écrites
# après le premier correctif, les deux faces du défaut du 2026-09-17, quatre
# formulations neuves et deux témoins.
#
# Les ensembles attendus sont écrits ici et nulle part ailleurs. Les noms sont
# lus dans le message : « source vente » attend `ventes`, « stock » attend
# `stocks` — c'est le même service que rendre « Télémétrie » pour `telemetrie`.
CAS: tuple[Cas, ...] = (
    # --- le message d'usage réel qui a ouvert le chantier
    Cas(
        "defaut-usage-reel",
        "Qu'est-ce que t'appelles source vente, production, stock ?",
        ("ventes", "production", "stocks"),
    ),
    # --- les six formulations de la première campagne
    Cas(
        "apercu-quatre",
        "Donne-moi un aperçu de ventes, production, stocks et titanic.",
        ("ventes", "production", "stocks", "titanic"),
    ),
    Cas("reference-deux", "titanic et iris, c'est quoi au juste ?", ("titanic", "iris")),
    Cas(
        "servent-trois",
        "Explique-moi à quoi servent production, stocks et iris.",
        ("production", "stocks", "iris"),
    ),
    Cas("contient-deux", "Ça contient quoi, ventes et stocks ?", ("ventes", "stocks")),
    Cas(
        "presente-trois",
        "Je voudrais comprendre ventes, production et stocks : présente-les-moi.",
        ("ventes", "production", "stocks"),
    ),
    Cas(
        "entre-deux",
        "Entre ventes et production, qu'y a-t-il dans chacune ?",
        ("ventes", "production"),
    ),
    # --- les DEUX faces du défaut mesuré le 2026-09-17, telles quelles
    #
    # (a) passe la ceinture et ne porte pas un fait : l'outil sert les deux
    # fiches, la réponse rend deux noms. (b) n'appelle rien qui compte : le
    # modèle répond AUTRE, et rien ne ramène le tour.
    Cas(
        "fondation-deux-noms",
        "je bosse sur quoi si je prends stocks et production ?",
        ("stocks", "production"),
    ),
    Cas(
        "autre-trois-noms",
        "resume moi vite fait ventes, stocks, iris",
        ("ventes", "stocks", "iris"),
    ),
    # --- les deux autres formulations du pilote du 2026-09-17
    #
    # Elles sont ici pour ce qu'elles montrent du REPLI, et non pour la
    # restriction : les deux reçoivent bien les fiches qu'elles nomment. La
    # première demande « deux mots » et recevait 774 caractères de fiche ; la
    # seconde est la SEULE des cinq dont le texte servi diffère des faits, donc
    # la seule où le modèle avait formulé.
    Cas(
        "deux-mots-brefs",
        "parle-moi un peu de stocks et de titanic, en deux mots",
        ("stocks", "titanic"),
    ),
    Cas(
        "hesite-deux-dedans",
        "j'hesite : ventes ou production, qu'est-ce qu'il y a dedans ?",
        ("ventes", "production"),
    ),
    # --- quatre formulations neuves, écrites AVANT de savoir ce qu'elles rendent
    Cas(
        "contenu-deux-metier",
        "c'est quoi le contenu de production et de stocks ?",
        ("production", "stocks"),
    ),
    Cas(
        "dedans-trois",
        "dis-moi ce qu'il y a dans ventes, production et iris",
        ("ventes", "production", "iris"),
    ),
    Cas("ressemble-deux", "iris et ventes, ça ressemble à quoi ?", ("iris", "ventes")),
    Cas(
        "tour-de-quatre",
        "fais-moi le tour de ventes, production, stocks et iris",
        ("ventes", "production", "stocks", "iris"),
    ),
    # --- trois formulations neuves de plus, écrites pour le tour de réparation
    #
    # Elles ne visent pas la restriction, qui tient : elles visent la VOIE. Deux
    # brièvetés explicites — « en une phrase », « vite » — et une comparaison,
    # c'est-à-dire trois messages auxquels un pavé de fiches répond mal même
    # quand il est juste.
    Cas(
        "une-phrase-trois",
        "en une phrase chacune : ventes, stocks, production, c'est quoi ?",
        ("ventes", "stocks", "production"),
    ),
    Cas(
        "difference-deux",
        "quelle est la différence entre iris et titanic ?",
        ("iris", "titanic"),
    ),
    Cas(
        "vite-deux",
        "dis-moi vite ce que je trouve dans production et dans ventes",
        ("production", "ventes"),
    ),
    # --- les deux témoins : ils doivent recevoir TOUT le catalogue
    #
    # Le premier ne nomme aucune source et cherche par sujet : il lui faut les
    # cinq descriptions pour en désigner une. Le second se trompe de nom : celui
    # qui se trompe a besoin de voir les vrais.
    Cas(
        "temoin-par-sujet",
        "as-tu quelque chose sur la maintenance des machines ?",
        catalogue_entier=True,
    ),
    Cas("temoin-nom-inconnu", "c'est quoi la source comptabilite ?", catalogue_entier=True),
)

# Ce qu'on tient pour « décrite » : un mot ou un nombre qui n'appartient qu'à la
# FICHE de cette source-là, repéré dans le texte servi. Jamais une liste écrite à
# la main — un oracle qui s'écrit à la main mesure son auteur.
#
# **Cet oracle a sa propre copie du calcul, et c'est délibéré.** Le socle en
# porte une depuis le correctif (``introspection.marques_des_fiches``) : c'est
# elle qui décide si la formulation du modèle est servie. L'appeler ici rendrait
# le verdict circulaire — une marque trop généreuse côté socle laisserait passer
# une réponse creuse ET la déclarerait conforme. Deux calculs écrits séparément
# peuvent diverger, et c'est précisément ce qu'on veut voir si cela arrive.
#
# **Première version, et pourquoi elle était fausse.** L'oracle cherchait des
# suites de quatre mots prises telles quelles dans la description du catalogue.
# Il comptait donc en échec « la source `ventes` contient 4 tables, dont
# `clients`, `commandes`, `lignes_commande` et `produits`, 673 lignes au
# total » — une réponse qui décrit la source avec les faits qu'on venait de lui
# servir, mais qui les REFORMULE. Un oracle qui exige les mots du catalogue
# mesure la recopie, pas la description, et il aurait fait « corriger » un
# comportement juste. Ce qu'on demande est qu'un fait de CETTE source-ci
# traverse le texte servi, dans les mots du modèle ou dans les nôtres.
#
# **Trois caractères et non quatre**, et c'est le même piège une seconde fois :
# à quatre, « la source `titanic` (file) est le Dataset Titanic, contenant 1
# table, `titanic`, avec 891 lignes » comptait pour NON DÉCRITE. Elle l'est —
# `891` est un fait qu'aucune autre fiche ne porte — mais le compte de lignes
# tient en trois chiffres. Trois ne fait pas de bruit ici : une marque doit de
# toute façon être absente de TOUTES les autres fiches pour être retenue.
LONGUEUR_D_UNE_MARQUE = 3


class ModeleQuiNoteLesAppels(ModeleCompteur):
    """Le compteur d'allers-retours, plus le FIL BRUT des appels d'outil.

    Les appels tels qu'ils sont émis, arguments compris. C'est ce relevé-là qui
    a nommé la cause du défaut du 2026-09-17 : la trace disait
    `sources_de_donnees`, le fil brut disait `chercher_une_source({"sujet": …})`
    — l'outil dont l'argument n'est pas lu, et qui rendait tout le catalogue.
    """

    def __init__(self, wrapped) -> None:
        super().__init__(wrapped)
        self.appels_d_outil: list[str] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        reponse = await super().request(messages, model_settings, model_request_parameters)
        self.appels_d_outil.extend(
            f"{p.tool_name}({json.dumps(p.args_as_dict(), ensure_ascii=False)})"
            for p in reponse.parts
            if isinstance(p, ToolCallPart)
        )
        return reponse


@dataclass
class Releve:
    cle: str
    message: str
    tirage: int
    attendues: tuple[str, ...]
    citees: tuple[str, ...]
    decrites: tuple[str, ...]
    appels: tuple[str, ...]
    outils_retenus: tuple[str, ...]
    voie: str
    defaut: str
    faits: str
    caracteres_servis: int
    caracteres_rendus: int
    appels_llm: int
    verdict: str
    pourquoi: str
    reponse: str
    servie: str = field(default="")


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


def juger(cas: Cas, releve: Releve) -> tuple[str, str]:
    """Le verdict, sur le texte SERVI — jamais sur la formulation brute.

    L'ordre des marches importe. Un tour qui n'atteint pas l'agent système est
    d'abord cela : ce que le planificateur en fait ensuite n'entre pas dans
    cette mesure-ci, et le compter reviendrait à noter un autre agent.
    """
    if not releve.outils_retenus:
        return "échec", "aucun outil appelé — le tour repart au planificateur"
    if cas.catalogue_entier:
        if MARQUE_DE_L_INVENTAIRE not in introspection.replie(releve.faits):
            return "échec", "le catalogue entier n'a pas été servi au tour"
        return "conforme", f"catalogue entier servi, {len(releve.appels)} appel(s)"
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
    modele: ModeleQuiNoteLesAppels,
    catalogue: Catalog,
    registre: Registry,
    releves_du_catalogue: RelevesDuCatalogue,
    fiches: dict[str, str],
    cas: Cas,
    tirage: int,
) -> Releve:
    """Un tour, joué comme le nœud du graphe le joue.

    ``run_systeme`` puis la ceinture, dans cet ordre et avec les mêmes arguments
    que ``Orchestrator._system_node`` : c'est la seule façon de mesurer ce que
    l'utilisateur reçoit. La liaison d'une source est le seul embranchement qui
    n'est pas rejoué — aucun de ces messages ne la déclenche, et la rejouer
    ferait entrer dans cette mesure-ci le contenu d'une autre.
    """
    avant = modele.appels
    modele.appels_d_outil.clear()
    reglages = get_settings()
    resultat = run_systeme(
        cas.message,
        model=modele,
        catalogue_declare=catalogue,
        catalogue_effectif=catalogue,
        registre=registre,
        releves=releves_du_catalogue,
        request_limit=reglages.systeme_request_limit,
    )
    defaut = introspection.defaut_de_fondation(
        resultat.reponse, resultat.faits, resultat.faits_a_enumerer, resultat.marques_a_porter
    )
    servie = resultat.faits if defaut else resultat.reponse
    plat = introspection.replie(servie)
    citees = tuple(
        s.name for s in catalogue.sources if f" {introspection.replie(s.name).strip()} " in plat
    )
    decrites = tuple(
        s.name for s in catalogue.sources if any(f" {m} " in plat for m in _marques(s, fiches))
    )
    if not resultat.outils_appeles:
        voie = "planificateur"
    elif defaut:
        voie = "repli"
    else:
        voie = "modèle"
    releve = Releve(
        cle=cas.cle,
        message=cas.message,
        tirage=tirage,
        attendues=cas.attendues,
        citees=citees,
        decrites=decrites,
        appels=tuple(modele.appels_d_outil),
        outils_retenus=resultat.outils_appeles,
        voie=voie,
        defaut=defaut,
        faits=resultat.faits,
        caracteres_servis=len(resultat.faits),
        caracteres_rendus=len(servie),
        appels_llm=modele.appels - avant,
        verdict="",
        pourquoi="",
        reponse=resultat.reponse,
        servie=servie,
    )
    releve.verdict, releve.pourquoi = juger(cas, releve)
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
        f"{sum(len(r.appels) for r in releves)} appels d'outil, "
        f"{sum(r.caracteres_servis for r in releves) // max(len(releves), 1)} caractères servis "
        "par tour en moyenne.",
        "",
        "| message | attendues | appels d'outil émis | servis | rendus | appels LLM "
        "| voie | score | ce qui a décidé |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cle in cles:
        lot = [r for r in releves if r.cle == cle]
        bons = sum(1 for r in lot if r.verdict == "conforme")
        echecs = dict.fromkeys(r.pourquoi for r in lot if r.verdict != "conforme")
        premier = lot[0]
        appels = "<br>".join(dict.fromkeys(a for r in lot for a in r.appels)) or "(aucun)"
        voies = ", ".join(dict.fromkeys(r.voie for r in lot))
        lignes.append(
            f"| `{cle}` — {premier.message} | {', '.join(premier.attendues) or '(catalogue)'} "
            f"| `{appels}` | {premier.caracteres_servis} | {premier.caracteres_rendus} "
            f"| {premier.appels_llm} | {voies} | **{bons}/{len(lot)}** "
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
    modele = ModeleQuiNoteLesAppels(build_model(reglages))
    print(f"Serveur LLM : {reglages.llm_base_url} ({reglages.llm_model})")
    print(f"Catalogue : {reglages.catalog_path}\n")

    cas = [c for c in CAS if not args.seulement or c.cle in args.seulement]
    releves: list[Releve] = []
    total = len(cas) * args.tirages
    numero = 0
    for tirage in range(1, args.tirages + 1):
        for un_cas in cas:
            numero += 1
            print(f"[{numero}/{total}] {un_cas.cle}·{tirage} — « {un_cas.message} »")
            depart = time.monotonic()
            releve = poser(
                modele, catalogue, registre, releves_du_catalogue, fiches, un_cas, tirage
            )
            releves.append(releve)
            duree = int((time.monotonic() - depart) * 1000)
            print(f"    appels : {', '.join(releve.appels) or '(aucun)'}")
            print(
                f"    → {releve.verdict} ({releve.pourquoi}) — {releve.caracteres_servis} car. "
                f"servis, {releve.caracteres_rendus} rendus, {releve.appels_llm} appels LLM, "
                f"voie {releve.voie}, {duree} ms"
            )
            print(f"    servie : {' '.join(releve.servie.split())[:200]}\n", flush=True)

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
