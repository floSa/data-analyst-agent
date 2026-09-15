"""Rappeler un artefact par son nom, le relire, le rejouer modifié.

Deux outils typés (`pydantic-ai`), sur le modèle de l'agent système de C13 :
le modèle décide s'il y a lieu de les appeler, l'outil rend les faits, le
modèle formule. **Pas de lexique de mots-clés** — « reprends le graphe de tout
à l'heure », « le camembert de tantôt », « remets-moi ça en bleu » sont une
famille ouverte, et un lexique est une liste (mesuré à 3 formulations sur 10,
`surface-conversationnelle.md` §9).

**Ce qui entre dans le prompt est le CATALOGUE, pas le contenu.** Une ligne par
artefact : son nom, ce qu'il est, la question qui l'a produit. Le code d'une
figure pèse quelques milliers de caractères ; l'injecter à chaque tour ferait
exploser la fenêtre au bout de quelques figures, et c'est précisément ce qu'on
cherche à éviter en donnant un nom aux choses. C'est le motif « le système de
fichiers comme contexte » : on injecte l'index, on ouvre à la demande.

**Ce module n'exécute rien.** Le rejeu passe par un rappel (``rejouer``) que le
graphe lui fournit : c'est lui qui remonte le décor de données, appelle le bac
à sable et en garde les garde-fous — réseau coupé, mémoire bornée, sémaphore
de sessions. L'entrée/sortie appartient à un nœud du graphe, comme dans
:mod:`data_analyst_agent.orchestrator.systeme`.

**Un refus est un résultat.** Un nom qui ne désigne rien, ou qui désigne un
artefact évincé du contexte, rend un texte de refus *déterministe* qui dit
lequel des deux cas c'est et ce qui reste disponible. Et si le modèle passe
outre — s'il formule une réponse alors que tous les outils ont refusé — c'est le
refus qui est servi, pas sa formulation : c'est la reprise de la famille
``acfd8f5``, où un modèle à qui l'on demande une chose absente la fabrique
plutôt que de dire qu'elle est absente.

**Et quand le modèle ne prononce aucun nom ?** Il reste un trou, et c'est le
dernier de cette famille : « reprends le camembert des ports que tu m'avais
fait » dans un fil qui n'en porte aucun. L'agent décline — rien au catalogue n'y
ressemble — le planificateur fabrique un camembert neuf, correct, et personne ne
dit qu'il n'existait pas. Aucun outil n'a refusé, donc aucun refus ne part.
``designation_dun_artefact_passe`` et ``aveu_dabsence`` bouchent ce trou **sans
le modèle** : le message dit qu'il DÉSIGNE (c'est une construction
grammaticale), le catalogue dit ce qui EXISTE, et le nœud met l'absence en tête
de la réponse. Trois reformulations du prompt avaient été essayées avant, sans
succès — on demandait à un modèle d'affirmer une absence, et un modèle n'affirme
pas une absence : il produit ce qu'il peut.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from data_analyst_agent import prompts
from data_analyst_agent.agents.analysis.agent import AnalysisResult
from data_analyst_agent.orchestrator.introspection import SENTINELLE_HORS_SUJET, replie
from data_analyst_agent.orchestrator.workspace import ConversationWorkspace, WorkspaceArtifact

# Ce qu'on dit au modèle d'un rejeu qui a échoué. La cause technique reste dans
# la trace : ce que le modèle a besoin de savoir, c'est qu'il n'y a pas de
# figure neuve à annoncer.
REJEU_EN_ECHEC = "Le code rejoué n'a pas pu s'exécuter, même après correction."


@dataclass(frozen=True)
class Rejeu:
    """Ce qu'un rejeu a produit : le code réexécuté et son exécution.

    ``artefact`` est le nom de l'artefact d'origine — celui que l'utilisateur
    désignait. Le nom du NOUVEL artefact n'est pas ici : il est attribué par le
    nœud après coup, une fois qu'on sait que l'exécution a abouti.
    """

    artefact: str
    modification: str
    resultat: AnalysisResult


# Ce que le graphe fournit à l'agent pour qu'il puisse rejouer : l'artefact à
# reprendre et la modification demandée, en retour l'exécution.
Rejoueur = Callable[[WorkspaceArtifact, str], AnalysisResult]


def refus_dartefact(workspace: ConversationWorkspace, nom: str) -> str:
    """Pourquoi ce nom ne désigne rien d'exploitable — en distinguant les deux cas.

    « Il n'existe pas » et « il a été évincé du contexte » sont deux refus, et
    ils n'appellent pas la même réaction : le second dit qu'on a bien produit la
    chose demandée mais qu'elle est sortie de la fenêtre, ce qui est une
    information sur le système et non sur la demande. Les confondre reviendrait
    à dire à quelqu'un qu'il n'a jamais demandé ce graphique.

    Dans les deux cas, ce qui RESTE disponible est énuméré : un refus qui ne dit
    pas ce qu'on peut demander à la place oblige à deviner une deuxième fois.
    """
    disponibles = ", ".join(a.name for a in workspace.catalogue()) or "(aucun)"
    if workspace.sur_le_disque(nom) is None:
        return (
            f"Aucun artefact ne s'appelle « {nom} » dans cette conversation. "
            f"Artefacts disponibles : {disponibles}."
        )
    cause = workspace.trim.cause or "plafond du contexte"
    return (
        f"L'artefact « {nom} » a bien été produit dans cette conversation, mais il a été "
        f"ÉVINCÉ du contexte de ce tour ({cause}) : je ne peux ni le relire ni le rejouer. "
        f"Artefacts encore disponibles : {disponibles}."
    )


# -- « tu m'avais fait un camembert » : désigner ce qui n'existe pas ----------

# Ce qu'on dit quand on produit à la place de rappeler. C'est LA phrase du
# mécanisme, et elle porte tout : l'utilisateur ne doit pas repartir en croyant
# qu'on a retrouvé son travail alors qu'on en a refait un autre.
SUITE_EST_NEUVE = "Ce qui suit est neuf, pas un rappel."

# Les participes d'une PRODUCTION, et la liste est fermée : ce qu'on cherche
# n'est pas un passé quelconque, c'est un passé où l'agent a FABRIQUÉ quelque
# chose. Les accents sont tombés au repliement, « montré » s'y lit « montre ».
#
# « donné » y est au singulier seulement (``donnes?``) : « données » est le mot
# le plus fréquent du domaine, et l'y admettre faisait de « qu'est-ce que tu as
# comme données ? » une désignation d'artefact — mesuré, et c'est le faux
# positif le plus coûteux qu'on ait trouvé.
_PARTICIPES_DE_PRODUCTION = (
    "faits?|faites?|produits?|produites?|sortis?|sorties?|montres?|montrees?|"
    "affiches?|affichees?|generes?|generees?|crees?|creees?|traces?|tracees?|"
    "dessines?|dessinees?|donnes?|calcules?|calculees?|construits?|construites?|"
    "prepares?|preparees?|ecrits?|ecrites?|rendus?|rendues?|presentes?|presentees?|"
    "envoyes?|envoyees?|obtenus?|obtenues?|proposes?|proposees?|realises?|realisees?"
)

# ① Le passé ATTRIBUÉ À L'AGENT : « tu m'avais fait », « que tu as tracée ».
# L'auxiliaire seul ne suffit pas — « de quand datent les données que tu as ? »
# porte « tu as » et ne désigne aucun artefact. C'est le participe de production
# qui fait la présupposition, pas l'auxiliaire. Deux mots au plus les séparent
# (« tu m'as bien montré »).
_PASSE_ATTRIBUE = re.compile(
    r"\btu\s+(?:m|me|nous|l|le|la|les|lui|en|y)?\s*(?:as|avais|avait|aviez|avons|a)\s+"
    rf"(?:\w+\s+){{0,2}}(?:{_PARTICIPES_DE_PRODUCTION})\b"
)

# ② Le DÉICTIQUE du passé : le message situe la chose avant ce tour.
# « déjà » en a été retiré : trop fréquent hors désignation (« tu sais déjà
# faire des prédictions ? »), et il ne présuppose rien à lui seul.
_DEICTIQUE_DU_PASSE = re.compile(
    r"\b(?:tout a l heure|d avant|precedent|precedente|precedents|precedentes|"
    r"tantot|plus tot|la derniere fois|du debut|le meme|la meme|les memes|d hier)\b"
)

# ③ Le verbe de REPRISE : « reprends », « remontre », « reviens au ». Il
# présuppose que la chose existe — on ne reprend pas ce qui n'a jamais été.
# « refais » n'y est PAS : « refais-moi un camembert » se lit aussi bien comme
# une demande neuve, et l'ambiguïté se règle du côté du silence.
_VERBE_DE_REPRISE = re.compile(
    r"\b(?:reprends|reprend|reprendre|reprenez|remontre|remontrer|remontres|"
    r"reaffiche|reafficher|redonne|redonner|remets|remettre|rappelle|rappeler|"
    r"reviens|revenir|ressors|ressortir|repasse|repasser)\b"
)

# La CIBLE : ce que le message pointe. Un nom d'objet produisible, un
# anaphorique (« celui », « ce que »), ou un nom d'artefact. Sans cible, un
# marqueur de passé ne désigne pas un artefact — « tu m'as dit que » parle
# d'une phrase, pas d'une figure.
#
# « précédent » y figure ET parmi les déictiques, et c'est le seul mot à jouer
# les deux rôles : employé substantivement (« affiche le précédent »), il est à
# lui seul l'antériorité et la chose désignée.
_CIBLE_DESIGNEE = re.compile(
    r"\b(?:celui|celle|ceux|celles|ca|cela|ce que|ce qu|"
    r"graphique|graphiques|graphe|graphes|camembert|camemberts|diagramme|diagrammes|"
    r"histogramme|histogrammes|courbe|courbes|figure|figures|schema|schemas|"
    r"tableau|tableaux|barres|nuage|carte|analyse|analyses|resultat|resultats|"
    r"visualisation|visualisations|plot|chart|calcul|liste|"
    r"precedent|precedente|precedents|precedentes|"
    r"graphique_\d+|resultat_\d+|analyse_\d+)\b"
)


def designation_dun_artefact_passe(question: str) -> str:
    """Le marqueur par lequel ce message DÉSIGNE un artefact déjà produit — ``""`` sinon.

    C'est le signal qui sépare les deux demandes que le catalogue, lui, ne peut
    pas distinguer :

    - « reprends le camembert des ports **que tu m'avais fait** » présuppose que
      la chose existe. Si elle n'existe pas, il faut le DIRE avant d'en produire
      une autre : sans ça, l'utilisateur repart en croyant qu'on a retrouvé son
      travail ;
    - « fais-moi un camembert des ports » ne présuppose rien. On la produit, et
      un commentaire serait du bruit.

    Le signal est dans le MESSAGE, pas dans le catalogue — le catalogue dit ce
    qui existe, il ne dit pas ce que la phrase prétend. Il se compose de deux
    moitiés, et **les deux sont exigées** :

    1. un **marqueur d'antériorité** — le passé attribué à l'agent
       (``_PASSE_ATTRIBUE``), un déictique du passé (``_DEICTIQUE_DU_PASSE``),
       ou un verbe de reprise (``_VERBE_DE_REPRISE``) ;
    2. une **cible** (``_CIBLE_DESIGNEE``) — un objet produisible ou un
       anaphorique qui en tient lieu.

    Ce n'est pas le lexique disqualifié au §9 de
    ``docs/surface-conversationnelle.md``. Celui-là décidait de la ROUTE — quel
    agent traite la demande — et plafonnait à 3 formulations sur 10 parce qu'il
    fallait reconnaître *ce qu'on veut*. Ici on ne reconnaît qu'une construction
    grammaticale — « tu » + auxiliaire + participe de production, ou un
    déictique du passé — et elle décide seulement d'AJOUTER UNE PHRASE à une
    réponse qui, elle, part de toute façon. Se tromper ne fait perdre ni une
    réponse ni un tour : au pire une phrase de trop, au pire une phrase de
    moins.

    Rend le marqueur trouvé plutôt qu'un booléen : c'est ce qui part dans la
    trace, et une décision déterministe doit pouvoir dire sur quoi elle s'est
    fondée.
    """
    plat = replie(question)
    marqueur = (
        _PASSE_ATTRIBUE.search(plat)
        or _DEICTIQUE_DU_PASSE.search(plat)
        or _VERBE_DE_REPRISE.search(plat)
    )
    if marqueur is None or not _CIBLE_DESIGNEE.search(plat):
        return ""
    return marqueur.group(0)


def aveu_dabsence(workspace: ConversationWorkspace, question: str) -> str:
    """Ce qu'on met EN TÊTE quand on produit à la place de rappeler — ``""`` sinon.

    Le défaut que ce texte corrige n'est pas une invention : à « reprends le
    camembert des ports que tu m'avais fait », l'agent de rappel décline — rien
    au catalogue n'y ressemble — le planificateur fabrique un camembert neuf, et
    il est correct. Rien de faux n'est affirmé, aucun artefact n'est inventé.
    Mais **l'utilisateur repart en croyant qu'on a retrouvé son travail**, alors
    qu'on en a refait un autre. C'est la dernière poche de la famille
    ``acfd8f5`` : répondre avec ce qu'on peut produire au lieu de dire ce qui
    manque.

    **Déterministe, et côté nœud.** Trois reformulations du prompt ont été
    essayées au chantier précédent (§20.10) : aucune n'a tenu, parce qu'on
    demandait au modèle d'affirmer une absence — et un modèle n'affirme pas une
    absence, il produit ce qu'il peut. Le catalogue, lui, SAIT ce qui existe.
    C'est donc lui qui le dit.

    **Trois cas, trois phrases**, et les confondre serait mentir :

    - le fil n'a rien produit du tout ;
    - le fil a produit des choses, aucune n'est celle-là ;
    - des artefacts ont été **ÉVINCÉS** du contexte de ce tour. Celui-là est le
      piège : on ne PEUT PAS affirmer l'absence de ce qu'on ne voit plus, et le
      dire absent reviendrait à dire à quelqu'un qu'il n'a jamais demandé ce
      graphique. C'est la même frontière que ``refus_dartefact``, du côté où
      aucun nom n'a été prononcé.

    **On ne bloque jamais.** Cette phrase se met en tête d'une réponse qui part
    de toute façon : le défaut n'est pas de produire la figure, c'est de laisser
    croire qu'on l'a retrouvée.
    """
    if not designation_dun_artefact_passe(question):
        return ""
    disponibles = ", ".join(a.name for a in workspace.catalogue())
    if workspace.trim.truncated:
        cause = workspace.trim.cause or "plafond du contexte"
        reste = f" Artefacts encore disponibles : {disponibles}." if disponibles else ""
        return (
            f"Je ne peux pas affirmer ne l'avoir jamais produit : {workspace.trim.dropped} "
            f"objet(s) plus ancien(s) de cette conversation ont été ÉVINCÉS du contexte de "
            f"ce tour ({cause}), et je ne peux ni les relire ni les rejouer. "
            f"{SUITE_EST_NEUVE}{reste}"
        )
    if not disponibles:
        return (
            "Je n'ai encore rien produit dans cette conversation : il n'y a rien à "
            f"reprendre. {SUITE_EST_NEUVE}"
        )
    return (
        "Je n'ai pas produit dans cette conversation ce que tu me demandes de reprendre. "
        f"{SUITE_EST_NEUVE} Ce que j'ai produit ici : {disponibles}."
    )


# La forme d'un nom d'artefact, et elle est fermée : c'est le code qui les
# fabrique (``PREFIXE`` + un numéro), personne d'autre. Une forme fermée permet
# de chercher les noms d'artefacts dans une prose SANS confondre avec une
# variable Python d'un code relu — ce qu'un extracteur d'identifiants
# générique ferait à toutes les lignes.
FORME_D_UN_NOM = re.compile(r"\b(?:resultat|analyse|graphique)_\d+\b")


def noms_inventes(reponse: str, workspace: ConversationWorkspace) -> list[str]:
    """Les noms d'artefacts cités par le modèle que cette conversation n'a jamais produits.

    C'est la reprise de la famille ``acfd8f5`` sur ce chemin-ci : un modèle à
    qui l'on demande « le graphe de tout à l'heure » dans un fil qui n'en a pas
    sait en inventer un, nom compris, avec l'aplomb d'une lecture. Un nom
    inventé est plus nocif qu'un refus — il a l'air d'un rappel.

    Comparé à TOUT ce que porte le disque, et non au seul catalogue du tour :
    citer un artefact évincé n'est pas une invention, c'est un fait exact sur
    une conversation qui a duré.
    """
    connus = {a.name for a in workspace.artifacts}
    return sorted({nom for nom in FORME_D_UN_NOM.findall(reponse) if nom not in connus})


# Un jeton : un identifiant ou un nombre. Sert à vérifier qu'une formulation
# porte QUELQUE CHOSE de ce que l'outil a rendu — un nom de colonne, une valeur,
# le nom de l'artefact. Volontairement généreux : un seul jeton commun suffit.
_JETON = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")


def _jetons(texte: str) -> set[str]:
    return set(_JETON.findall(texte))


def defaut_de_formulation(
    reponse: str, workspace: ConversationWorkspace, attendus: frozenset[str] = frozenset()
) -> str:
    """Ce qui interdit de servir la phrase du modèle — ``""`` si rien.

    Le modèle formule, il ne décide pas de ce qui est vrai. Trois défauts le
    disqualifient, et dans les trois ce sont les FAITS — ce que les outils ont
    rendu — qui partent à l'utilisateur :

    - **la sentinelle après un appel d'outil.** Mesuré : le modèle a lu
      ``resultat_1``, puis a répondu « AUTRE ». Sans cette garde, l'utilisateur
      recevait le mot ``AUTRE`` en guise de réponse — pire qu'un refus, parce
      que ce n'en est même pas un. Un tour où un outil a été appelé n'est plus
      « pas pour moi » : la contradiction se tranche du côté de ce qui a été lu.
    - **une réponse vide**, pour la même raison ;
    - **un nom d'artefact que la conversation n'a jamais produit**, qui est la
      famille ``acfd8f5`` (cf. ``noms_inventes``) ;
    - **une formulation qui ne porte RIEN de ce que l'outil a rendu.** Mesuré
      sur vLLM : l'agent a correctement reconnu « le premier tableau que tu m'as
      sorti », lu ``resultat_1``, reçu ``count / 891`` — puis répondu « je ne
      peux pas répondre à cette demande ». L'outil avait fait son travail ; le
      modèle a renoncé après coup, et sans cette garde son renoncement partait
      à l'utilisateur à la place de la réponse qu'il tenait.

    ``attendus`` porte ce que les lectures ont rendu : le NOM de chaque artefact
    lu et les jetons de son contenu. Un seul en commun suffit — c'est
    délibérément généreux, parce qu'on cherche à distinguer « formulé
    autrement » de « n'a rien formulé du tout », pas à noter un style.

    Le message rendu est destiné à la TRACE, pas à l'utilisateur : ce qu'il lit,
    lui, ce sont les faits, et ils ne s'annoncent pas comme un repli.
    """
    if not reponse.strip():
        return "réponse vide"
    if reponse.strip().upper() == SENTINELLE_HORS_SUJET:
        return "sentinelle rendue alors qu'un outil a été appelé"
    inventes = noms_inventes(reponse, workspace)
    if inventes:
        return "nom(s) inventé(s) : " + ", ".join(inventes)
    if attendus and not (attendus & _jetons(reponse)):
        return "formulation sans aucun fait de l'outil"
    return ""


@dataclass
class RappelDeps:
    """Ce que les outils ont le droit de lire, et ce qu'ils ont fait.

    Le workspace est celui de **cette** conversation, et il n'y en a pas
    d'autre : un artefact ne franchit ni la frontière d'un fil ni celle d'un
    utilisateur, parce que le dossier où on le cherche est déjà celui de l'un et
    de l'autre. Le cloisonnement est un chemin, pas un filtre (cf.
    ``orchestrator/conversations``).
    """

    workspace: ConversationWorkspace
    rejouer: Rejoueur
    outils_appeles: list[str] = field(default_factory=list)
    # Ce que les outils ont rendu, dans l'ordre : matière de la vérification
    # d'après-coup, et repli servi si elle échoue.
    faits: list[str] = field(default_factory=list)
    refus: list[str] = field(default_factory=list)
    rejeu: Rejeu | None = None
    # Ce que les LECTURES ont rendu : le nom de chaque artefact lu et les jetons
    # de son contenu. C'est la matière de `defaut_de_formulation`.
    attendus: set[str] = field(default_factory=set)

    def retenir(self, outil: str, texte: str) -> str:
        self.outils_appeles.append(outil)
        self.faits.append(texte)
        return texte

    def refuser(self, outil: str, nom: str) -> str:
        texte = refus_dartefact(self.workspace, nom)
        self.refus.append(texte)
        return self.retenir(outil, texte)


@dataclass(frozen=True)
class ResultatRappel:
    """Ce qu'a produit l'agent de rappel, et de quoi le juger.

    ``outils_appeles`` vide = la demande n'était pas pour lui, et le tour repart
    au planificateur exactement comme avant ce mécanisme.
    """

    reponse: str
    faits: str
    refus: tuple[str, ...]
    rejeu: Rejeu | None
    outils_appeles: tuple[str, ...]
    attendus: frozenset[str] = frozenset()

    @property
    def concerne_le_rappel(self) -> bool:
        return bool(self.outils_appeles)

    @property
    def tout_a_ete_refuse(self) -> bool:
        """Chaque outil appelé a refusé : il n'y a rien à formuler.

        C'est la condition qui fait servir le refus tel quel plutôt que la
        phrase du modèle. Un seul outil ayant abouti suffit à la lever — une
        demande qui lit un artefact et en rate un autre a bien quelque chose à
        raconter.
        """
        return bool(self.outils_appeles) and len(self.refus) == len(self.outils_appeles)


def build_rappel_agent() -> Agent[RappelDeps, str]:
    """L'agent et ses deux outils : relire un artefact, rejouer un code modifié."""
    agent: Agent[RappelDeps, str] = Agent(deps_type=RappelDeps, output_type=str)

    @agent.system_prompt
    def system_prompt(ctx: RunContext[RappelDeps]) -> str:
        return prompts.render(
            prompts.RAPPEL, catalogue=catalogue_pour_le_prompt(ctx.deps.workspace)
        )

    @agent.tool
    def lire_un_artefact(ctx: RunContext[RappelDeps], nom: str) -> str:
        """Le contenu d'un artefact de cette conversation, désigné par son NOM.

        `nom` : le nom exact tel qu'il apparaît au catalogue (« graphique_1 »,
        « resultat_2 »). Rend le code Python pour une analyse ou une figure, la
        tête du tableau pour un résultat de requête.
        """
        artefact = ctx.deps.workspace.retenu(nom)
        if artefact is None:
            return ctx.deps.refuser("lire_un_artefact", nom)
        contenu = ctx.deps.workspace.lire(artefact)
        ctx.deps.attendus |= {artefact.name} | _jetons(contenu)
        return ctx.deps.retenir("lire_un_artefact", f"{nom} ({artefact.description}) :\n{contenu}")

    @agent.tool
    def rejouer_un_code(ctx: RunContext[RappelDeps], nom: str, modification: str) -> str:
        """Reprend le code d'un artefact, y applique une modification, le réexécute.

        `nom` : le nom de l'artefact de code (« graphique_1 »).
        `modification` : ce que l'utilisateur veut changer, dans ses mots
        (« mets les barres en bleu au lieu de rouge »).
        """
        artefact = ctx.deps.workspace.retenu(nom)
        if artefact is None or not artefact.est_du_code:
            # Un tableau n'est pas du code : le refus le dit comme il dirait un
            # nom inconnu, plutôt que de tenter d'exécuter un CSV.
            if artefact is not None:
                return ctx.deps.refuser("rejouer_un_code", f"{nom} (ce n'est pas du code)")
            return ctx.deps.refuser("rejouer_un_code", nom)
        resultat = ctx.deps.rejouer(artefact, modification)
        ctx.deps.rejeu = Rejeu(artefact=nom, modification=modification, resultat=resultat)
        if not resultat.succeeded:
            return ctx.deps.retenir("rejouer_un_code", REJEU_EN_ECHEC)
        figures = len([r for r in resultat.execution.results if r.mime == "image/png"])
        return ctx.deps.retenir(
            "rejouer_un_code",
            f"Le code de {nom} a été rejoué avec « {modification} » : "
            f"{figures} figure(s) produite(s).",
        )

    return agent


def catalogue_pour_le_prompt(workspace: ConversationWorkspace) -> str:
    """Le catalogue des artefacts RETENUS — une ligne chacun, et l'éviction dite.

    Le contenu n'y est jamais : c'est ce qui fait tenir la fenêtre. Mais
    l'ÉVICTION y est, et ce n'est pas un ornement : un catalogue qui montre ce
    qui reste sans dire ce qui est sorti fait croire au modèle qu'il voit tout.
    Mesuré — cf. ``ContextTrim.rappel_notice``.
    """
    lignes = [a.ligne_de_catalogue() for a in workspace.catalogue()]
    catalogue = "\n".join(lignes) or "(aucun artefact produit pour l'instant)"
    avis = workspace.trim.rappel_notice()
    return f"{catalogue}\n\n{avis}" if avis else catalogue


def run_rappel(
    question: str,
    *,
    model: Model,
    workspace: ConversationWorkspace,
    rejouer: Rejoueur,
    request_limit: int,
) -> ResultatRappel:
    """Soumet la demande à l'agent de rappel et rend ce qu'il en a fait."""
    deps = RappelDeps(workspace=workspace, rejouer=rejouer)
    run = build_rappel_agent().run_sync(
        question,
        model=model,
        deps=deps,
        usage_limits=UsageLimits(request_limit=request_limit),
    )
    return ResultatRappel(
        reponse=run.output,
        faits="\n\n".join(deps.faits),
        refus=tuple(deps.refus),
        rejeu=deps.rejeu,
        outils_appeles=tuple(deps.outils_appeles),
        attendus=frozenset(deps.attendus),
    )
