"""Les FAITS que le système peut dire de lui-même — lus dans ses artefacts.

Les quatre capacités historiques (`query`, `analyze`, `predict`,
`fetch_then_predict`) sont quatre actions **sur** les données. Aucune ne couvre
« quelles sources possèdes-tu ? » : la demande n'était pas mal classée, elle
était *inclassable*, et le repli du planificateur nommait la source qu'on lui
demandait tout en déclarant ne pas comprendre (mesuré :
[docs/surface-conversationnelle.md](../../../docs/surface-conversationnelle.md),
une question méta sur trois en repli).

**Jamais depuis la mémoire du modèle.** Chaque texte d'ici est construit à
partir d'un artefact du dépôt : le catalogue (`sources/catalogue.yaml`), le
registre (`models/registry.yaml`), les schémas de features (`SCHEMAS`), et
l'ontologie que la source rend elle-même. C'est le défaut corrigé par
``acfd8f5`` — « décris le dataset iris » répondu de mémoire, avec une jolie
prose sur « un ensemble classique de classification floristique » et zéro
requête — et il ne doit pas revenir par cette porte.

**Ces textes ont deux emplois, et c'est délibéré.** Ils sont ce que rendent les
outils de l'agent système (:mod:`data_analyst_agent.orchestrator.systeme`), donc
la matière que le modèle formule ; et ils sont le **repli** servi tel quel quand
la formulation du modèle ne les porte pas (``defaut_de_fondation``). Le même
texte est donc la source du chemin principal et la ceinture — un gabarit qu'on
garde justement parce qu'il vaut mieux qu'une invention.

Le **lexique de mots-clés** qui vivait ici a été retiré. Il reconnaissait le
sujet d'une question par des tournures écrites à la main, et sa précision était
son plafond : mesuré sur dix formulations naturelles de la même question
(« quelles données as-tu ? »), il en court-circuitait trois et laissait les sept
autres partir au planificateur, qui les classait `query` et écrivait du SQL. Le
sujet est désormais reconnu par le modèle, qui appelle l'outil qui porte les
faits — cf. `surface-conversationnelle.md` §9.

Ce module est **pur** : il ne lit ni fichier ni base. L'ontologie d'une source
lui est passée, déjà lue par le nœud qui a ouvert la connexion — l'entrée/sortie
appartient à un nœud du graphe, pas à un formateur de texte.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import get_args

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, describe_features
from data_analyst_agent.agents.retrieval.catalog import Catalog, Source
from data_analyst_agent.agents.retrieval.faits import FaitsDeSource
from data_analyst_agent.agents.retrieval.sql import SchemaInfo, TableInfo

# Les faits lus dans les sources, par nom de source. Le module reste PUR : il
# les reçoit déjà lus, il ne va jamais les chercher (cf. l'en-tête).
Faits = dict[str, FaitsDeSource]


def replie(texte: str) -> str:
    """Minuscules, sans accents ni décoration, ponctuation ramenée à des blancs.

    Sert à deux comparaisons de sens et non de typographie : reconnaître le nom
    d'une source ou d'une table cité dans une question, et vérifier qu'une
    réponse du modèle porte bien les faits qu'un outil lui a rendus.

    La décoration Markdown tombe avec le reste, et ce n'est pas cosmétique :
    le modèle écrit ``passenger\\_id`` — l'antislash échappe le blanc souligné
    pour l'affichage — et une comparaison littérale ne reconnaîtrait plus le
    nom de la colonne qu'il vient pourtant de citer correctement.
    """
    sans_accent = unicodedata.normalize("NFKD", texte.lower())
    nu = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return " " + re.sub(r"[^a-z0-9_]+", " ", nu.replace("\\", "")).strip() + " "


# --- ce qu'on cherche à qualifier dans la question ---------------------------


def _nomme_dans(question: str, noms: list[str]) -> str | None:
    """Le seul nom de la liste cité dans la question, ou ``None``.

    ``None`` quand aucun n'y est **et** quand plusieurs y sont : deux noms
    cités ne désignent pas une cible, et en choisir un serait deviner.
    """
    plat = replie(question)
    trouves = [n for n in noms if f" {replie(n).strip()} " in plat]
    return trouves[0] if len(trouves) == 1 else None


def source_visee(question: str, catalogue: Catalog) -> Source | None:
    """La source que la question nomme, s'il n'y en a qu'une — sinon ``None``.

    Repli sur l'unique source du catalogue quand il n'en contient qu'une : la
    question ne peut alors désigner qu'elle, et exiger qu'elle soit nommée
    ferait poser une question dont la réponse est déjà connue.
    """
    nom = _nomme_dans(question, [s.name for s in catalogue.sources])
    if nom is None and len(catalogue.sources) == 1:
        return catalogue.sources[0]
    return catalogue.get(nom) if nom else None


def _singulier(nom: str) -> str:
    """Le nom sans son `s` final, quand il en a un et qu'il reste un mot.

    Le pendant, pour le nombre, de ce que ``replie`` fait pour les accents et
    les majuscules. L'argument est le même, et il est déjà écrit dans
    ``systeme._trouver_la_source`` : l'utilisateur écrit le nom d'une source
    comme un mot FRANÇAIS, le catalogue l'écrit comme un identifiant, et exiger
    que les deux coïncident au caractère près, c'est exiger qu'on tape comme un
    fichier de configuration. « La source vente » désigne `ventes` aussi
    sûrement que « Télémétrie » désigne `telemetrie`.

    Trois lettres au minimum une fois le `s` ôté : en deçà, replier ferait se
    rencontrer des noms qui n'ont rien à voir, et le gain — reconnaître un
    pluriel sur un nom de deux lettres — n'existe pas.

    Vit ici et PAS dans ``replie``, et c'est délibéré : ``replie`` sert aussi à
    vérifier qu'une réponse porte les faits qu'on lui a servis
    (``defaut_de_fondation``), et une comparaison plus lâche y laisserait passer
    une réponse qui écrit un autre nom que celui qu'elle a reçu.
    """
    nu = nom.strip()
    return nu[:-1] if len(nu) > 3 and nu.endswith("s") else nu


def sources_nommees(question: str, catalogue: Catalog) -> list[Source]:
    """TOUTES les sources déclarées que le message nomme, dans l'ordre du catalogue.

    Le pluriel de ``source_nommee``, et il manquait. La version au singulier
    rend ``None`` dès que deux noms sont cités — « deux noms cités ne désignent
    pas une cible, et en choisir un serait deviner » — ce qui est juste quand on
    cherche UNE cible, et faux quand la question en vise plusieurs. Rien ne
    savait donc répondre à « qu'est-ce que t'appelles source vente, production,
    stock ? » autrement qu'en rendant le catalogue entier.

    Mesuré le 2026-09-17 : cette question-là ne recevait pas trois fiches mais
    les cinq sources du catalogue, `iris` et `titanic` compris — et une réponse
    de deux cents caractères qui se contentait de nommer les trois sans en
    décrire aucune. Le détail du fil brut est dans `docs/sources-metier.md`.

    Le pluriel de l'utilisateur comme son singulier (``_singulier``) : « la
    source vente » désigne `ventes`, et c'est le service que ``replie`` rend
    déjà pour les accents et les majuscules.

    Vide quand le message ne nomme rien : c'est le cas de la recherche par sujet
    (« as-tu quelque chose sur la maintenance ? »), qui doit continuer de
    recevoir tout le catalogue pour y choisir.
    """
    plat = replie(question)
    nommees = []
    for source in catalogue.sources:
        nom = replie(source.name).strip()
        if f" {nom} " in plat or f" {_singulier(nom)} " in plat:
            nommees.append(source)
    return nommees


def mots_hors_des_noms(question: str, sources: list[Source]) -> list[str]:
    """Ce que le message dit EN PLUS des noms de sources qu'il porte.

    Le pendant, au pluriel, du décompte de ``choix_de_source`` : un message
    réduit à des noms de sources et à ce qui les relie ne DEMANDE rien sur elles,
    il hésite entre elles. « ventes ou clients ? » n'appelle pas deux fiches,
    il appelle la question qui fait choisir — et c'est le contrat du premier tour
    (``proposer_les_sources``), qui finit par « sur laquelle veux-tu
    travailler ? » et lie la réponse à la conversation.

    Le singulier compte comme le pluriel, comme partout ici : « source vente »
    écrit `ventes`, et le laisser dans le reste ferait passer un message pour
    plus bavard qu'il n'est.
    """
    noms = set()
    for source in sources:
        nom = replie(source.name).strip()
        noms |= {nom, _singulier(nom)}
    return [mot for mot in replie(question).split() if mot not in noms]


def source_nommee(question: str, catalogue: Catalog) -> str | None:
    """Le nom de source que le TEXTE cite, sans repli sur l'unique source.

    Distincte de ``source_visee``, et pour une raison de fond : celle-ci sert à
    savoir si **l'utilisateur** a désigné une source — donc à lier la
    conversation à elle, ou à en changer. Le repli sur l'unique source du
    catalogue serait ici une désignation qu'il n'a pas faite, et le nom choisi
    par le planificateur une supposition du modèle. Ni l'un ni l'autre ne doit
    faire basculer la source de travail de quelqu'un.
    """
    return _nomme_dans(question, [s.name for s in catalogue.sources])


# Au-delà de trois mots EN PLUS du nom de la source, le message dit autre chose
# que « celle-là » : il porte une question, et c'est elle qu'il faut traiter.
# Trois, parce que « va pour titanic, merci » en compte trois et « combien de
# passagers dans titanic » quatre — la frontière est posée sur les formulations
# réellement mesurées, pas sur une intuition.
#
# Se tromper ici ne coûte pas une réponse fausse : une source nommée est liée
# de toute façon (``_regle_source_de_la_conversation``), et le seul écart est
# un accusé de réception là où on aurait pu répondre du même coup.
MOTS_EN_PLUS_D_UN_CHOIX = 3


def choix_de_source(question: str, catalogue: Catalog) -> str | None:
    """Le nom de source que ce message DÉSIGNE, s'il ne dit presque rien d'autre.

    « titanic », « va pour titanic, merci » : l'utilisateur choisit, il ne
    demande rien — on lie la source et on en accuse réception, sans le moindre
    aller-retour. « et dans titanic, combien de lignes ? » nomme aussi une
    source, mais porte une question : c'est elle qu'il faut traiter, et la
    source se lie en chemin.

    La distinction est mesurée, pas théorique : sans elle, le premier runner de
    parcours a vu un « titanic » de validation recevoir le catalogue en réponse,
    et un « et dans iris, combien de lignes ? » perdre sa question.
    """
    nom = source_nommee(question, catalogue)
    if nom is None:
        return None
    reste = [mot for mot in replie(question).split() if mot != replie(nom).strip()]
    return nom if len(reste) <= MOTS_EN_PLUS_D_UN_CHOIX else None


def dataset_vise(question: str, registre: Registry) -> str | None:
    """Le modèle que la question nomme, ou l'unique modèle du registre."""
    nom = _nomme_dans(question, registre.datasets)
    if nom is None and len(registre.datasets) == 1:
        return registre.datasets[0]
    return nom


# --- les réponses, construites depuis les artefacts --------------------------

# Ce que chaque capacité sait faire, dit à l'utilisateur. Clés = les valeurs de
# ``Capability`` : le test de couverture le vérifie, pour qu'une capacité
# ajoutée sans description se voie au rouge et non en production.
CAPACITES_EN_CLAIR: dict[str, str] = {
    "query": (
        "**interroger une source en SQL** — comptages, agrégats, pourcentages, "
        "listes filtrées, jointures"
    ),
    "analyze": (
        "**analyser et visualiser** — du code Python exécuté en bac à sable "
        "(tests statistiques, ACP, figures)"
    ),
    "predict": (
        "**prédire un cas** dont tu me donnes les attributs (« une passagère de "
        "1re classe, 28 ans, tarif 80… »)"
    ),
    "fetch_then_predict": (
        "**prédire des individus déjà dans une source** — je lis leurs attributs, "
        "puis je prédis (« la survie du passager 42 », « toutes les femmes »)"
    ),
}

# Celle-ci n'est pas une valeur de ``Capability``, et c'est voulu (cf. le
# commentaire de ``plan.py``) : elle n'est pas choisie par le planificateur mais
# reconnue en amont, quand le modèle appelle un outil de faits. Elle a sa place
# dans la réponse quand même — c'est bel et bien quelque chose que l'agent sait
# faire.
CAPACITE_SUR_SOI = (
    "**répondre sur moi-même** — mes sources, leurs tables et leurs colonnes, "
    "mes modèles et les attributs qu'ils attendent"
)


AUCUNE_SOURCE = "Je n'ai aucune source de données déclarée dans mon catalogue."


# Ce qu'on ajoute sous l'inventaire quand des faits y sont relevés. Le dire
# n'est pas de la politesse : un volume lu au premier inventaire ne bouge plus
# jusqu'au redémarrage (cf. ``RelevesDuCatalogue``), et un chiffre qui date sans
# le dire finit par se faire prendre pour un chiffre frais.
MENTION_DU_RELEVE = (
    "_Tables, lignes et périodes sont lues dans les sources elles-mêmes, "
    "au premier inventaire de la session._"
)


def _liste_des_sources(catalogue: Catalog, faits: Faits | None = None) -> list[str]:
    """L'inventaire des sources en lignes — nom, type, description, et faits LUS.

    Les faits (volume, période) viennent en seconde ligne, **sans puce et sans
    accent grave**. Ce n'est pas une question de goût : la ceinture qui vérifie
    la formulation du modèle lit le premier nom décoré de chaque puce comme le
    sujet de la ligne (``_enumeres``), et une sous-puce nommant ``passengers``
    exigerait de toute réponse qu'elle recopie chaque nom de table pour être
    servie. Les faits sont du contexte sur la source, pas de nouveaux sujets.

    ``faits`` absent (le défaut) = l'inventaire d'avant ce relevé : un appelant
    qui n'a pas de quoi lire les sources — ou qui ne veut pas les ouvrir —
    continue de rendre le catalogue déclaré, sans rien inventer pour combler.
    """
    lignes = [f"J'ai accès à {len(catalogue.sources)} source(s) de données :", ""]
    for source in catalogue.sources:
        description = source.description.strip() or "sans description"
        lignes.append(f"- **{source.name}** ({source.type}) — {description}")
        releve = (faits or {}).get(source.name)
        if releve is not None and releve.en_clair():
            lignes.append(f"  {releve.en_clair()}")
    return lignes


def decrire_les_sources(catalogue: Catalog, faits: Faits | None = None) -> str:
    """Le catalogue, rendu à l'utilisateur — noms, types, descriptions, volumes."""
    if not catalogue.sources:
        return AUCUNE_SOURCE
    lignes = [
        *_liste_des_sources(catalogue, faits),
        "",
        "Demande-moi les tables ou les colonnes de l'une d'elles "
        "(« quelles colonnes a la source "
        f"{catalogue.sources[0].name} ? ») pour en voir le détail.",
    ]
    if faits:
        lignes += ["", MENTION_DU_RELEVE]
    return "\n".join(lignes)


def fiche_de_source(source: Source, releve: FaitsDeSource | None) -> str:
    """Ce que le catalogue dit d'une source, et ce qu'on a LU dedans — rien de plus.

    Des FAITS, sans promesse : le type, la description, le volume, la période.
    C'est le moment où savoir qu'elle pèse 300 lignes et ne couvre aucune date
    change ce qu'on va lui demander.

    Séparée de ``accueil_de_source`` à cause d'un défaut trouvé par un test.
    L'outil de liaison de l'agent système rendait directement l'accueil complet,
    « je garde cette source pour la suite de la conversation » comprise — et ce
    texte était servi tel quel même quand le nœud REFUSAIT de lier (nom qui n'est
    pas celui de l'utilisateur, appel hors conversation, cf.
    ``Orchestrator._liaison_demandee``). L'utilisateur lisait une promesse que
    personne n'avait tenue, et son tour suivant retombait sur « sur quelle source
    veux-tu travailler ? ». La promesse appartient au nœud qui la tient ; l'outil
    ne rend que les faits.
    """
    description = source.description.strip() or "sans description"
    faits = f"\n\n{releve.en_clair()}" if releve is not None and releve.en_clair() else ""
    return f"La source **{source.name}** ({source.type}) — {description}{faits}"


def fiches_des_sources(sources: list[Source], faits: Faits | None = None) -> str:
    """Les fiches des sources qu'un message NOMME, annoncées comme telles.

    Une ligne d'en-tête, puis une fiche par source. L'en-tête n'est pas un
    ornement : c'est lui qui dit que cet ensemble-ci est CLOS — ce sont les
    sources demandées, il n'y en a pas d'autres à aller chercher, et il n'y a
    donc rien à y choisir.

    Mesuré le 2026-09-17, et c'est ce qui l'a imposé. Sans lui, un tour routé
    sur `chercher_une_source` recevait bien les trois fiches et répondait
    « j'ai trouvé les sources `ventes`, `production` et `stocks` » : cent
    soixante-treize caractères, trois noms, pas un fait. Le modèle avait lu
    dans la fiche de cet outil qu'il n'a « pas à réciter les autres », et
    traitait donc trois fiches choisies comme une liste où choisir. Douze tours
    sur vingt et un se jouaient là. L'en-tête posé, vingt et un sur vingt et un.

    Il est écrit pour un LECTEUR et non pour le modèle, parce qu'il peut lui
    être servi tel quel : quand la formulation du modèle ne porte pas les faits,
    c'est ce texte qui part à l'utilisateur (``defaut_de_fondation``).
    """
    tete = (
        f"Les {len(sources)} sources que ta question nomme, et ce qu'on sait "
        f"de chacune — toutes les {len(sources)}, il n'y en a pas d'autres à chercher :"
        if len(sources) > 1
        else ""
    )
    corps = [fiche_de_source(s, (faits or {}).get(s.name)) for s in sources]
    return "\n\n".join(([tete] if tete else []) + corps)


def accueil_de_source(source: Source, releve: FaitsDeSource | None, precedente: str = "") -> str:
    """Ce qu'on répond quand une source vient d'être retenue pour la conversation.

    Déterministe, et c'est assumé : il n'y a rien à formuler. La phrase accuse
    réception d'un nom que l'utilisateur vient d'écrire et y ajoute la fiche de
    la source. Un aller-retour LLM pour la reformuler ne changerait pas un fait
    et ferait attendre l'utilisateur avant sa première vraie question.

    Elle dit la source **quittée** s'il y en avait une : un choix qui en remplace
    un autre doit se voir, exactement comme une bascule au milieu d'une question.

    Vit ici, dans le module PUR, et non dans l'orchestrateur : les deux chemins
    de liaison la servent — le court-circuit déterministe du nœud de plan, et
    l'outil que le modèle appelle depuis l'agent système. Deux copies auraient
    divergé au premier mot changé.
    """
    description = source.description.strip() or "sans description"
    quittee = f" (on travaillait sur `{precedente}`)" if precedente else ""
    faits = f"\n\n{releve.en_clair()}" if releve is not None and releve.en_clair() else ""
    return (
        f"Entendu : on travaille sur **{source.name}** ({source.type}){quittee} — "
        f"{description}{faits}\n\n"
        "Je garde cette source pour la suite de la conversation. Nomme-en une "
        "autre à tout moment et je basculerai dessus.\n\n"
        "Que veux-tu savoir ?"
    )


def proposer_les_sources(catalogue: Catalog, faits: Faits | None = None) -> str:
    """Le même inventaire, mais posé comme une QUESTION : laquelle prend-on ?

    L'ancienne clarification énumérait des noms nus — « Sur quelle source
    veux-tu travailler : titanic, iris ? ». Deux noms sans un mot de contexte
    ne permettent pas de choisir quand on découvre l'agent, et la réponse était
    de toute façon perdue au tour suivant. Celle-ci rend ce que le catalogue
    dit de chaque source **et ce qu'on lit dedans** — volume, période —, et la
    réponse est liée à la conversation.

    Les faits comptent ici plus qu'ailleurs : c'est le texte sur lequel
    quelqu'un choisit. Deux descriptions écrites à la main se ressemblent ; « 891
    lignes, de 1912-04-10 à 1912-04-15 » et « 300 lignes, aucune date » ne se
    ressemblent pas.

    Elle **finit** par la question, comme le repli du planificateur et pour la
    même raison : ce qu'on lit en dernier est ce à quoi on répond.
    """
    if not catalogue.sources:
        return AUCUNE_SOURCE
    lignes = [
        *_liste_des_sources(catalogue, faits),
        "",
        "Donne-moi le nom de celle qui t'intéresse : je la garde pour la suite de la conversation.",
    ]
    if faits:
        lignes += ["", MENTION_DU_RELEVE]
    lignes += ["", "Sur laquelle veux-tu travailler ?"]
    return "\n".join(lignes)


def decrire_les_modeles(registre: Registry) -> str:
    """Le registre de modèles — tâche, cible, libellés de classes, unité."""
    if not registre.datasets:
        return "Je n'ai aucun modèle de prédiction dans mon registre."
    lignes = [f"Je dispose de {len(registre.datasets)} modèle(s) de prédiction :", ""]
    for dataset in registre.datasets:
        entree = registre.get(dataset)
        tache = "classification" if entree.task == "classification" else "régression"
        unite = f", en {entree.unit}" if entree.unit else ""
        description = entree.description.strip() or "sans description"
        lignes.append(f"- **{dataset}** ({tache}{unite}) — cible `{entree.target}` — {description}")
        if entree.labels:
            classes = ", ".join(f"{k} = {v}" for k, v in entree.labels.items())
            lignes.append(f"  Classes prédites : {classes}.")
        if dataset not in SCHEMAS:
            # Une entrée de registre sans schéma de features ne peut PAS être
            # appelée : le dire vaut mieux que la lister comme les autres.
            lignes.append("  (aucun schéma de features déclaré : je ne peux pas l'appeler.)")
    lignes += [
        "",
        "Demande-moi les attributs attendus par l'un d'eux "
        f"(« de quoi as-tu besoin pour prédire {registre.datasets[0]} ? »).",
    ]
    return "\n".join(lignes)


def decrire_les_features(registre: Registry, dataset: str | None) -> str:
    """Les attributs attendus par un modèle, ou le tour d'horizon des modèles.

    Un dataset désigné donne le détail complet — le sens de chaque champ et
    ses valeurs autorisées, tels que le schéma les porte
    (``describe_features``). Plusieurs modèles et aucun désigné : les noms des
    attributs de chacun, sur une ligne. C'est **une réponse**, et non la
    question « sur quel modèle veux-tu prédire ? » que l'utilisateur recevait
    — qui lui renvoyait sa question en énumérant les modèles.
    """
    if not registre.datasets:
        return "Je n'ai aucun modèle de prédiction dans mon registre."
    if dataset is not None and dataset in SCHEMAS:
        entree = registre.get(dataset)
        champs = SCHEMAS[dataset].model_fields
        return "\n".join(
            [
                f"Pour prédire avec le modèle **{dataset}** ({entree.task} de "
                f"`{entree.target}`), il me faut {len(champs)} attribut(s) :",
                "",
                describe_features(SCHEMAS[dataset]),
                "",
                "Donne-les-moi comme tu veux, en une phrase : j'en extrais les valeurs. "
                "Tu peux aussi désigner des individus d'une source "
                "(« prédis la survie du passager 42 ») et je lirai leurs attributs moi-même.",
            ]
        )
    lignes = ["Ça dépend du modèle. Voici ce que chacun attend :", ""]
    for connu in registre.datasets:
        if connu in SCHEMAS:
            noms = ", ".join(f"`{n}`" for n in SCHEMAS[connu].model_fields)
            lignes.append(f"- **{connu}** ({registre.get(connu).task}) : {noms}")
        else:
            lignes.append(f"- **{connu}** : aucun schéma de features déclaré.")
    lignes += [
        "",
        "Demande-moi le détail de l'un d'eux "
        f"(« de quoi as-tu besoin pour prédire {registre.datasets[0]} ? ») "
        "pour avoir le sens de chaque attribut et ses valeurs autorisées.",
    ]
    return "\n".join(lignes)


def decrire_les_capacites(catalogue: Catalog, registre: Registry) -> str:
    """Ce que l'agent sait faire — la liste des capacités, plus l'inventaire.

    Construite depuis ``CAPACITES_EN_CLAIR``, dont les clés sont les valeurs
    de ``Capability`` : la liste suit le code, elle n'est pas une prose à
    maintenir en parallèle. Terminée par l'inventaire, parce que « que sais-tu
    faire ? » n'appelle pas une réponse abstraite — ce qui intéresse, c'est sur
    quoi.
    """
    from data_analyst_agent.orchestrator.plan import Capability

    lignes = ["Je suis un agent d'analyse de données. Je sais :", ""]
    lignes += [f"- {CAPACITES_EN_CLAIR[c]} ;" for c in get_args(Capability)]
    lignes.append(f"- {CAPACITE_SUR_SOI} ;")
    lignes[-1] = lignes[-1].removesuffix(" ;") + "."
    sources = ", ".join(f"`{s.name}`" for s in catalogue.sources) or "(aucune)"
    modeles = ", ".join(f"`{d}`" for d in registre.datasets) or "(aucun)"
    lignes += ["", f"Mes sources : {sources}. Mes modèles de prédiction : {modeles}."]
    return "\n".join(lignes)


def _decrire_la_colonne(table: TableInfo, colonne: str) -> list[str]:
    """Une colonne : son type, sa nullité, ses valeurs, et la clé qui la relie.

    La clé étrangère est le cœur de la réponse à « que signifie class_id ? » :
    le nom seul n'apprend rien, la table qu'il référence tout.
    """
    info = next(c for c in table.columns if c.name == colonne)
    detail = [f"Dans la table `{table.name}`, la colonne `{info.name}` est de type {info.type}."]
    if [info.name] == table.primary_key:
        detail.append("C'est la **clé primaire** de la table.")
    if not info.nullable:
        detail.append("Elle est obligatoire (NOT NULL).")
    for fk in table.foreign_keys:
        if fk.column == info.name:
            detail.append(
                f"C'est une **clé étrangère** : elle référence "
                f"`{fk.ref_table}({fk.ref_column})` — le sens de la valeur se lit "
                f"dans la table `{fk.ref_table}`."
            )
    if info.values:
        detail.append("Valeurs présentes : " + ", ".join(repr(v) for v in info.values) + ".")
    return detail


def _decrire_la_table(table: TableInfo) -> list[str]:
    lignes = [f"- **{table.name}** ({len(table.columns)} colonnes) :"]
    for colonne in table.columns:
        marques = []
        if [colonne.name] == table.primary_key:
            marques.append("clé primaire")
        if not colonne.nullable:
            marques.append("obligatoire")
        for fk in table.foreign_keys:
            if fk.column == colonne.name:
                marques.append(f"clé étrangère vers {fk.ref_table}({fk.ref_column})")
        if colonne.values:
            marques.append("valeurs : " + ", ".join(repr(v) for v in colonne.values))
        suffixe = f" — {' ; '.join(marques)}" if marques else ""
        lignes.append(f"    - `{colonne.name}` ({colonne.type}){suffixe}")
    return lignes


# Le trait de séparation d'un tableau Markdown : `|---|:--:|`. Il ne dit rien,
# il dessine — et recopié dans une réponse il ne dessine même plus.
_SEPARATEUR_DE_TABLEAU = re.compile(r"^:?-+:?$")

# L'ouverture ou la fermeture d'un bloc de code Markdown. Ce qu'il enferme est
# un EXEMPLE — du SQL, du Python — et un exemple ne se recolle pas en prose :
# ses retours à la ligne sont sa syntaxe. Servi sous « ce qu'en dit le
# dictionnaire », il rendait une requête entière sur une seule ligne, entre
# deux triples accents grecs.
_CLOTURE_DE_CODE = re.compile(r"^\s*```")


def _cellules(ligne: str) -> list[str]:
    """Les cellules d'une ligne de tableau Markdown, bords vides retirés."""
    return [c.strip() for c in ligne.strip().strip("|").split("|")]


def _blocs_du_dictionnaire(dictionnaire: str) -> list[tuple[str, list[str]]]:
    """Le dictionnaire découpé en BLOCS : ``("tableau"|"prose", lignes)``.

    Le bloc et non la ligne, et c'est toute la réparation. Un dictionnaire est
    du Markdown écrit pour un humain : ses paragraphes sont coupés à la largeur
    d'un éditeur, et prendre une LIGNE d'un paragraphe prend une phrase par le
    milieu. Relevé tel quel avant :

        > `statut = 'RET'` marque les 30 stations démontées. Le référentiel les
          garde,

    La virgule finale n'est pas une faute du dictionnaire, c'est un retour à la
    ligne lu comme une fin. Une ligne de tableau, elle, EST un bloc : elle se
    suffit, et c'est pourquoi les deux genres se séparent ici.

    Une PUCE ouvre un bloc, elle aussi : c'est l'autre forme sous laquelle un
    dictionnaire définit ses entrées — « - `class_id` : 1 = pont supérieur » —
    et coller deux puces voisines dirait d'un terme ce qu'une autre entrée dit
    d'un autre. Ce qui SUIT une puce sans en être une la continue : c'est le
    même paragraphe, renfoncé.
    """
    blocs: list[tuple[str, list[str]]] = []
    dans_un_code = False
    for brute in dictionnaire.splitlines():
        ligne = brute.strip()
        if _CLOTURE_DE_CODE.match(brute):
            dans_un_code = not dans_un_code
            blocs.append(("fin", []))
        elif dans_un_code:
            continue
        elif not ligne or ligne.startswith("#"):
            blocs.append(("fin", []))
        elif _LIGNE_DE_TABLEAU.fullmatch(brute):
            blocs.append(("tableau", [ligne]))
        elif _PUCE.match(brute):
            blocs.append(("prose", [ligne]))
        elif blocs and blocs[-1][0] == "prose":
            blocs[-1][1].append(ligne)
        else:
            blocs.append(("prose", [ligne]))
    return [b for b in blocs if b[0] != "fin"]


def _en_prose(genre: str, lignes: list[str], terme: str) -> str:
    """Le bloc rendu en une phrase lisible — "" s'il n'a rien à dire de ``terme``.

    Un paragraphe se recolle : ses retours à la ligne étaient de la mise en
    page, pas de la ponctuation.

    Une ligne de tableau perd ses barres. Elle n'est retenue que si le terme y
    est DÉCORÉ et seul dans sa cellule, c'est-à-dire si la ligne le DÉFINIT ou
    l'ÉNUMÈRE. C'est ce qui écarte la ligne de contrôle que le relevé montrait
    au milieu d'une fiche de colonne :

        > | En service | 120 (`WHERE statut = 'ACT'`) | 120 |

    Elle cite `statut` dans une clause SQL, elle n'en dit rien ; servie sous
    « ce qu'en dit le dictionnaire de `referentiel` », elle donne à lire un
    fragment de tableau de comptage à qui demandait un sens.

    Quand la première cellule EST le terme, elle devient le sujet de la phrase
    (« `statut` : … ») ; sinon les cellules se suivent, séparées par un tiret —
    c'est la forme d'une table de correspondance, dont les colonnes sont
    elles-mêmes des termes.
    """
    if genre == "prose":
        return " ".join(lignes)
    cellules = [c for c in _cellules(lignes[0]) if c and not _SEPARATEUR_DE_TABLEAU.fullmatch(c)]
    decore = f"`{terme}`"
    if not any(c == decore for c in cellules):
        return ""
    if cellules[0] == decore:
        return f"{decore} : {' — '.join(cellules[1:])}" if len(cellules) > 1 else ""
    return " — ".join(cellules)


def extrait_du_dictionnaire(dictionnaire: str, terme: str, maximum: int = 4) -> str:
    """Ce que le dictionnaire dit de ``terme``, en prose ("" s'il n'en dit rien).

    Le dictionnaire d'une source dit ce que les données **veulent dire**, là où
    le DDL ne dit que des types. Le rendre en entier noierait la réponse — sur
    une base réelle il fait plusieurs milliers de mots — d'où l'extrait, borné,
    autour du terme demandé.

    **Le contenu était juste et la forme illisible.** Cette fonction recopiait
    les LIGNES du fichier, telles quelles, chacune sous un chevron. Relevé tel
    quel, sur « le statut RET, il recouvre quoi au juste ? » :

        > | `statut` | `ACT` (en service) ou `RET` (retirée du service,
          matériel démonté). Voir le piège nº 1. |
        > `statut = 'RET'` marque les 30 stations démontées. Le référentiel les
          garde,
        > | En service | 120 (`WHERE statut = 'ACT'`) | 120 |

    Trois défauts, et trois causes distinctes : des barres verticales de tableau
    Markdown, que la page ne sait pas mettre en tableau ; une phrase coupée au
    milieu, parce qu'un paragraphe de Markdown est coupé à la largeur d'un
    éditeur ; et une ligne de comptage sans rapport, retenue parce qu'elle
    contient le mot. Les trois se règlent en lisant des BLOCS plutôt que des
    lignes (``_blocs_du_dictionnaire``) et en ne retenant d'un tableau que les
    lignes qui DÉFINISSENT le terme (``_en_prose``).

    **Ce qui ne change pas : le chevron.** Il n'est pas une décoration, c'est la
    marque à laquelle trois ceintures reconnaissent le seul texte de ce module
    que nous n'écrivons pas — ``_citations`` y cherche la consigne du
    dictionnaire, ``_noms_exiges`` et ``_actions_exigees`` s'abstiennent d'y
    réclamer quoi que ce soit. Ni l'en-tête d'attribution, qui est ce à quoi les
    ceintures reconnaissent une provenance (``_attribution_qui_ne_colle_pas``).
    """
    dits: dict[str, None] = {}
    for genre, lignes in _blocs_du_dictionnaire(dictionnaire):
        if not any(terme.lower() in ligne.lower() for ligne in lignes):
            continue
        dit = _en_prose(genre, lignes, terme).strip()
        if dit:
            dits.setdefault(dit, None)
    return "\n".join(f"> {dit}" for dit in list(dits)[:maximum])


def bloc_du_dictionnaire(source: str, extrait: str) -> str:
    """L'extrait, sous l'en-tête qui dit d'où il vient — "" si l'extrait est vide.

    UN seul endroit écrit cette phrase, et c'est ce qui la rend fiable ailleurs :
    ``_attribution_qui_ne_colle_pas`` reconnaît les faits qui citent un
    dictionnaire à cet en-tête-là, et ``_citations`` y cherche la consigne. Deux
    appelants la composaient ; deux copies d'une marque que du code reconnaît,
    ce sont deux marques qui divergent en silence.
    """
    if not extrait:
        return ""
    # ``.capitalize()`` et non deux littéraux : la marque que le code cherche
    # est écrite en minuscules parce qu'elle se compare à un texte replié, et
    # la phrase servie commence une ligne. Un seul des deux se déduit de
    # l'autre ; les écrire tous les deux, c'est les laisser diverger.
    return f"{_EN_TETE_DE_DICTIONNAIRE.capitalize()} `{source}` :\n{extrait}"


# Une LIGNE de tableau Markdown. C'est la forme sous laquelle les dictionnaires
# de ce dépôt définissent leurs termes, et c'est donc le dictionnaire lui-même
# qui déclare son vocabulaire : rien n'est écrit à la main ici, la liste suit la
# source et elle ne mesure pas la mémoire de son auteur.
_LIGNE_DE_TABLEAU = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)

# Et TOUTES ses cellules décorées, pas seulement la première. Un dictionnaire
# définit de deux façons : une ligne par colonne — « | `puissance_kw` |
# Puissance instantanée… | » — et une table de correspondance dont les colonnes
# SONT les termes, « | `code_tarif` | `libelle` | `prix_kwh_eur` | `tva_pct` |».
# Ne lire que la première cellule laissait tomber la seconde forme, et avec elle
# `prix_kwh_eur`, mesuré.
_CELLULE_DECOREE = re.compile(r"`([^`\n|]{1,64})`")

# En deçà, le nom d'une entrée se confond avec un mot français ordinaire. C'est
# une collision réelle et non une précaution : `BASE` est une entrée du
# dictionnaire de `facturation` — le poste tarifaire — et « dans la base » est
# une phrase que n'importe qui écrit. Les colonnes, elles, font toutes cinq
# caractères ou plus dans les cinq dictionnaires du catalogue de démonstration.
_LONGUEUR_D_UNE_ENTREE = 5


def entrees_du_dictionnaire(dictionnaire: str) -> list[str]:
    """Les termes que ce dictionnaire DÉFINIT, dans son ordre, dédoublonnés."""
    vues: dict[str, None] = {}
    for ligne in _LIGNE_DE_TABLEAU.findall(dictionnaire):
        for terme in _CELLULE_DECOREE.findall(ligne):
            nu = terme.strip()
            if len(nu) >= _LONGUEUR_D_UNE_ENTREE and _FORME_D_IDENTIFIANT.fullmatch(nu):
                vues.setdefault(nu, None)
    return list(vues)


def ce_qu_en_dit_le_dictionnaire(question: str, dictionnaire: str | None, source: str) -> str:
    """Ce que le dictionnaire écrit sur les termes que le MESSAGE nomme — "" sinon.

    **Le chemin des données n'a pas de ceinture, et c'est là que le sens se
    perd.** L'agent système, lui, en a une : ce qu'un outil lui rend est comparé
    à ce qu'il formule, et les faits partent tels quels si la formulation ne les
    porte pas (``defaut_de_fondation``). Mais il ne voit pas tous les tours. Sur
    cinq des six questions de sens mesurées le 2026-09-18, il n'appelle AUCUN
    outil — trois tirages sur trois, le fil brut dit « aucun outil appelé » — et
    le tour repart au planificateur, qui le classe `query` ou `analyze`.

    Le dictionnaire est pourtant bien là : il est injecté dans le prompt de
    l'agent SQL comme dans celui de l'agent d'analyse (cf.
    `agents/dictionnaire`). Ce qui manque n'est donc pas la lecture, c'est la
    vérification — rien, sur ce chemin-là, n'exige que ce qui a été lu
    ressorte. « le statut RET, il recouvre quoi au juste ? » recevait « 2 lignes
    retournées — voir le tableau ci-dessous », et le tableau comptait les
    statuts sans dire ce qu'ils veulent dire.

    **Ce qu'on sert, et pourquoi tel quel.** Les lignes du dictionnaire qui
    citent le terme, sous l'en-tête qui dit de quelle source elles viennent —
    exactement ce que ``decrire_le_schema`` sert déjà à l'agent système. On ne
    demande pas au modèle de les reformuler, et on ne cherche pas non plus à
    deviner s'il les a déjà dites : sa formulation a le droit d'être juste, le
    texte de la source le reste de toute façon, et c'est le choix que le repli
    de la ceinture fait depuis le début.

    **Les termes ne sont pas une liste écrite à la main**, et c'est ce qui
    empêche cette fonction de devenir le lexique qu'on a retiré de ce module :
    ce sont les entrées que le dictionnaire se donne à lui-même
    (``entrees_du_dictionnaire``), retenues seulement quand le message les
    NOMME. Une source qui ne déclare aucun dictionnaire ne déclenche donc rien,
    et c'est le témoin de la mesure — `titanic` et `iris` n'en ont pas.
    """
    if not dictionnaire:
        return ""
    plat = replie(question)
    nommes = [t for t in entrees_du_dictionnaire(dictionnaire) if f" {replie(t).strip()} " in plat]
    lignes: dict[str, None] = {}
    for terme in nommes:
        for ligne in extrait_du_dictionnaire(dictionnaire, terme).splitlines():
            lignes.setdefault(ligne, None)
    return bloc_du_dictionnaire(source, "\n".join(lignes))


@dataclass(frozen=True)
class Ontologie:
    """Ce qu'une source dit d'elle-même, déjà lu par le nœud qui l'a ouverte."""

    source: Source
    schema: SchemaInfo
    dictionnaire: str | None = None


def _entre_les_porteuses(porteuses: list[Ontologie], liee: Ontologie | None) -> Ontologie | None:
    """Laquelle des sources qui portent ce nom la question désigne-t-elle ?

    Une seule le porte : c'est elle, et rien n'est deviné. Plusieurs le
    portent : c'est la source de TRAVAIL qui tranche, si elle est du nombre —
    et c'est le verrou de source, le même qui fait répondre 1 757 519 et non
    531 098 à « quelle énergie totale ? » quand la conversation est sur
    `exploitation`. Aucune des deux voies ne s'applique : ``None``, et l'appelant
    rendra le tour d'horizon plutôt qu'un sens pris au hasard.
    """
    if len(porteuses) == 1:
        return porteuses[0]
    if liee is not None and liee in porteuses:
        return liee
    return None


def _cible(
    question: str, ontologies: list[Ontologie], source_de_travail: str = ""
) -> Ontologie | None:
    """L'ontologie sur laquelle porte la question, ou ``None`` si c'est indécidable.

    Quatre voies, dans cet ordre, et une seule propriété : **un nom cité
    désigne la source qui le porte**. La source nommée ; à défaut la source qui
    porte la TABLE nommée — « quelles colonnes dans la table passengers ? » ne
    nomme aucune source et n'est pourtant pas ambiguë ; à défaut la source qui
    porte la COLONNE nommée, pour exactement la même raison ; à défaut la
    source de travail de la conversation ; à défaut l'unique source, s'il n'y
    en a qu'une.

    **La voie de la colonne est celle qui manquait**, et son absence se lisait
    dans la documentation comme une réserve de forme : « que veut dire la
    colonne puissance_kw ? », sans « dans la source telemetrie », rendait la
    liste des tables. Une colonne est pourtant un nom comme un autre, et
    `puissance_kw` n'existe que dans une source sur cinq. Ce qui était présenté
    comme une contrainte de formulation était un trou dans la cascade.

    La source de travail ne sert jamais à écraser un nom cité : elle départage
    quand plusieurs sources portent le même (``_entre_les_porteuses``), et elle
    ne décide seule que lorsque la question ne nomme rien.
    """
    nomme = _nomme_dans(question, [o.source.name for o in ontologies])
    if nomme:
        return next(o for o in ontologies if o.source.name == nomme)
    liee = next((o for o in ontologies if o.source.name == source_de_travail), None)
    tables = _dedoublonne(t.name for o in ontologies for t in o.schema.tables)
    table = _nomme_dans(question, tables)
    if table:
        porteuses = [o for o in ontologies if table in o.schema.table_names()]
        choisie = _entre_les_porteuses(porteuses, liee)
        if choisie is not None:
            return choisie
    colonnes = _dedoublonne(c.name for o in ontologies for t in o.schema.tables for c in t.columns)
    colonne = _nomme_dans(question, colonnes)
    if colonne:
        porteuses = [
            o
            for o in ontologies
            if any(c.name == colonne for t in o.schema.tables for c in t.columns)
        ]
        choisie = _entre_les_porteuses(porteuses, liee)
        if choisie is not None:
            return choisie
    if liee is not None:
        return liee
    return ontologies[0] if len(ontologies) == 1 else None


def _dedoublonne(noms) -> list[str]:
    """Les noms sans répétition, dans l'ordre — une colonne peut vivre dans deux tables.

    ``_nomme_dans`` rend ``None`` dès que plusieurs noms sont cités : sans ce
    dédoublonnage, ``class_id`` présent dans ``passengers`` ET dans ``classes``
    comptait pour deux, et « que signifie la colonne class_id ? » devenait
    indécidable alors qu'un seul nom était demandé.
    """
    vus: dict[str, None] = {}
    for nom in noms:
        vus.setdefault(nom, None)
    return list(vus)


def decrire_le_schema(
    question: str,
    ontologies: list[Ontologie],
    source_de_travail: str = "",
    designation: str = "",
) -> str:
    """Les tables, les colonnes ou le sens d'une colonne — au bon niveau de détail.

    Quatre niveaux, choisis sur ce que la question nomme réellement. Une
    colonne : sa fiche, clé étrangère et dictionnaire compris. Une table : ses
    colonnes. Une source : toutes ses tables avec leurs colonnes. Rien de
    décidable : le tour d'horizon — chaque source et le nom de ses tables,
    sans les colonnes, parce que déplier trois cents colonnes pour répondre
    « je ne sais pas laquelle tu veux » n'aide personne.

    ``source_de_travail`` est la source liée à la conversation. Elle ne sert
    qu'à départager (cf. ``_cible``) : « c'est quoi code_station ? » se pose
    dans quatre sources sur cinq, et la seule qui puisse trancher est celle sur
    laquelle on travaille. Vide hors conversation, et le comportement est alors
    celui d'avant, au caractère près.

    ``designation`` est ce que l'APPELANT a explicitement visé — l'argument que
    le modèle a passé à l'outil, quand il en a passé un. Elle choisit la source
    AVANT le texte du message, et ce n'est pas un raffinement : c'est un défaut
    mesuré, et il ne se lisait pas dans la trace.

    **« dis-moi ce qu'il y a dans ventes, production et iris »**, mesuré le
    2026-09-17 sur le catalogue métier. Le modèle fait exactement ce qu'il faut
    — trois appels, un par nom :

    - ``schema_d_une_source({"cible": "ventes"})``
    - ``schema_d_une_source({"cible": "production"})``
    - ``schema_d_une_source({"cible": "iris"})``

    Les trois rendaient le schéma d'``iris``. La désignation était collée au
    message avant d'être examinée, le message nomme trois sources, et
    ``_nomme_dans`` rend ``None`` dès que plusieurs noms sont cités — la voie de
    la source tombait donc, et c'était la voie de la TABLE qui tranchait :
    `iris` est aussi le nom d'une table, il est dans la phrase de l'utilisateur,
    et il y reste quel que soit l'argument de l'appel. Trois appels distincts
    décidés par un mot qui n'en distinguait aucun.

    Ce que la désignation décide, elle le décide **seule** : ni la source de
    travail ni le reste de la phrase ne servent à ce premier passage. Sinon un
    argument que le modèle n'aurait pas rempli ferait gagner la source liée
    contre une source nommée dans la question, et la source de travail n'a
    jamais eu ce rang (cf. ``_cible``). Quand la désignation ne décide rien —
    vide, ou un mot qui ne nomme rien — le message décide comme avant, au
    caractère près.

    Le NIVEAU de détail, lui, continue de se lire dans tout le texte : c'est
    « que signifie la colonne class_id ? » avec ``cible="passengers"`` qui l'a
    imposé — la table vient de l'argument, la colonne de la phrase, et il faut
    les deux pour rendre la fiche d'une colonne plutôt que la table entière.
    """
    if not ontologies:
        return "Je n'ai aucune source de données déclarée dans mon catalogue."
    cible = _cible(designation, ontologies) if designation.strip() else None
    if cible is None:
        cible = _cible(question, ontologies, source_de_travail)
    if cible is None:
        lignes = ["Voici les tables de chacune de mes sources :", ""]
        for ontologie in ontologies:
            noms = ", ".join(f"`{t.name}`" for t in ontologie.schema.tables) or "(aucune table)"
            lignes.append(f"- **{ontologie.source.name}** : {noms}")
        lignes += [
            "",
            "Demande-moi une table en particulier pour en voir les colonnes "
            "(« quelles colonnes dans la table "
            f"{ontologies[0].schema.table_names()[0]} ? »).",
        ]
        return "\n".join(lignes)

    schema = cible.schema
    table_visee = _nomme_dans(question, schema.table_names())
    tables = [t for t in schema.tables if t.name == table_visee] if table_visee else schema.tables
    colonne_visee = _nomme_dans(question, _dedoublonne(c.name for t in tables for c in t.columns))

    if colonne_visee:
        porteuse = next(t for t in tables if any(c.name == colonne_visee for c in t.columns))
        lignes = _decrire_la_colonne(porteuse, colonne_visee)
        terme = colonne_visee
    elif table_visee:
        lignes = [f"La table `{table_visee}` de la source `{cible.source.name}` :", ""]
        lignes += _decrire_la_table(tables[0])
        terme = table_visee
    else:
        lignes = [
            f"La source `{cible.source.name}` contient {len(schema.tables)} table(s) "
            f"({schema.dialect}) :",
            "",
        ]
        for table in tables:
            lignes += _decrire_la_table(table)
        terme = None

    extrait = (
        extrait_du_dictionnaire(cible.dictionnaire, terme) if cible.dictionnaire and terme else ""
    )
    bloc = bloc_du_dictionnaire(cible.source.name, extrait)
    if bloc:
        lignes += ["", bloc]
    return "\n".join(lignes)


def inventaire(catalogue: Catalog, registre: Registry) -> str:
    """L'inventaire en une phrase, pour un repli qui reste utile.

    Le repli du planificateur disait « je n'ai pas bien compris » en citant des
    sources écrites EN DUR dans la chaîne (« titanic, iris… »), y compris quand
    ce n'étaient pas celles du catalogue. S'il est capable de nommer la source,
    il est capable de répondre à la question qui la demande : le repli rend
    désormais l'inventaire réel, lu là où il vit.
    """
    sources = ", ".join(s.name for s in catalogue.sources) or "aucune source déclarée"
    modeles = ", ".join(registre.datasets) or "aucun modèle"
    return f"Mes sources : {sources}. Mes modèles de prédiction : {modeles}."


# --- la ceinture : une formulation qui ne porte pas les faits est écartée -----

# Ce que le modèle doit répondre quand la question n'est PAS pour lui (cf.
# `prompts/systeme.txt`). Elle ne sert qu'à raccourcir sa réponse : le signal
# de routage est l'absence d'appel d'outil, pas ce mot — un modèle qui
# paraphrase la consigne au lieu de la recopier ne doit pas pour autant voir
# sa réponse servie à l'utilisateur.
SENTINELLE_HORS_SUJET = "AUTRE"

# Un identifiant technique : nom de source, de table, de colonne, de modèle.
_FORME_D_IDENTIFIANT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Tout jeton de cette forme, où qu'il soit. Sert du côté des FAITS, et
# généreusement : un nom que les faits portent en toutes lettres est un nom
# connu, même s'ils ne l'ont pas décoré. `describe_features` rend ses champs
# nus (« * sex — Sexe du passager »), et sans cette générosité un modèle
# écrivant `sex` entre accents graves se faisait accuser de l'avoir inventé.
_JETON = re.compile(rf"\b{_FORME_D_IDENTIFIANT.pattern}\b")
# Comment les faits présentent un nom : entre accents graves (`class_id`) ou en
# gras (**titanic**). Ce sont NOS textes, donc les deux marques sont fiables.
_DECORE = re.compile(r"`([^`\n]{1,64})`|\*\*([^*\n]{1,64})\*\*")
# Une puce d'énumération.
_PUCE = re.compile(r"^\s*[-*]\s")
# Ce qui, dans la réponse du MODÈLE, vaut affirmation d'un nom technique. Le
# gras en est absent volontairement : le modèle met en gras des mots français
# ordinaires, et les compter ferait écarter des réponses justes. Un jeton
# contenant un blanc souligné, en revanche, n'est jamais de la prose française.
_ENTRE_ACCENTS = re.compile(r"`([^`\n]{1,64})`")
_EN_SERPENT = re.compile(r"\b([A-Za-z]+(?:_[A-Za-z0-9]+)+)\b")
# Une énumération tenue sur UNE ligne : un deux-points suivi de noms, jusqu'au
# point qui clôt la phrase (« Mes sources : `titanic`, `iris`. »). Le groupe
# s'arrête sur `.` et sur `:` — la seconde borne parce qu'une même ligne en
# porte deux (« … Mes modèles de prédiction : `iris`. ») et que chacune est une
# énumération à part entière. Exige au moins un caractère non blanc : un
# deux-points en FIN de ligne introduit ce qui suit, il n'énumère rien.
_EN_LIGNE = re.compile(r":[^\S\n]*([^.:\n]*\S)")
# Une citation Markdown. Les faits recopient là le dictionnaire de la source —
# le seul texte de ce module que nous n'écrivons pas. Sa ponctuation ne nous
# engage pas : on n'y lit aucune énumération.
_CITATION = re.compile(r"^\s*>")


def _identifiants(texte: str, *motifs: re.Pattern[str]) -> list[str]:
    """Les identifiants que ``texte`` cite, dédoublonnés, en minuscules.

    Les échappements Markdown du modèle sont retirés avant l'examen :
    ``passenger\\_id`` est le nom ``passenger_id``, écrit pour l'affichage.
    """
    trouves: dict[str, None] = {}
    for motif in motifs:
        for brut in motif.findall(texte.replace("\\", "")):
            nom = (brut if isinstance(brut, str) else next(filter(None, brut), "")).strip()
            if _FORME_D_IDENTIFIANT.fullmatch(nom):
                trouves.setdefault(nom.lower(), None)
    return list(trouves)


def _enumeres(faits: str) -> list[str]:
    """Ce dont les faits PARLENT — ce qui doit revenir entier.

    La règle tient en une phrase : *ce qu'un fait nomme doit revenir, ce qu'il
    en dit est du contexte.* Les faits nomment de **deux** façons, et il a
    fallu les mesurer pour le voir.

    **Une puce.** Le premier nom décoré de la ligne en est le sujet ; ce qui
    suit — le type d'une colonne, la cible d'un modèle — est un détail qu'une
    bonne réponse a le droit de ne pas recopier. Trois conséquences, chacune
    corrigeant un rejet mesuré à tort :

    - une ligne d'**en-tête** n'énumère rien (« La table `passengers` de la
      source `titanic` : ») — exiger `titanic` d'une réponse qui listait
      correctement les dix colonnes de `passengers` était pédant ;
    - une puce dont la décoration n'est pas un nom (« - **interroger une source
      en SQL** — … ») ne réclame rien : elle ne nomme aucun identifiant ;
    - une puce **sans** décoration rend son premier jeton, ce qui rattrape les
      champs nus de ``describe_features``.

    **Une énumération tenue sur une ligne**, et c'est ce qui manquait. Tous les
    faits ne listent pas en puces : ``decrire_les_capacites`` termine par « Mes
    sources : `titanic`, `iris`. Mes modèles de prédiction : … », une ligne
    ordinaire qui nomme pourtant tout l'inventaire. La ceinture n'y voyait
    rien, et le trou s'est mesuré le 2026-09-14 : à « Sur quoi peux-tu
    travailler ? », le modèle appelait bel et bien son outil, recevait ces
    noms, et rendait « Je peux interroger des sources en SQL, analyser… » — la
    liste des capacités résumée, l'inventaire tombé. Rien ne l'arrêtait,
    puisque aucune PUCE ne portait `titanic`. Ici, contrairement à la puce, ce
    sont **tous** les noms du segment qui sont exigés : une énumération n'a pas
    de sujet, elle n'a que des membres.

    Le deux-points qui termine une ligne en est exclu — il introduit ce qui
    suit, il n'énumère rien —, et les lignes de **citation** avec lui : elles
    recopient le dictionnaire de la source, seul texte que ce module n'écrit
    pas, dont la ponctuation ne nous engage donc pas.
    """
    noms: dict[str, None] = {}
    for brute in faits.splitlines():
        ligne = brute.replace("\\", "")
        if _CITATION.match(ligne):
            continue
        if _PUCE.match(ligne):
            decore = _DECORE.search(ligne)
            candidats = (
                _identifiants(decore.group(0), _DECORE) if decore else _identifiants(ligne, _JETON)
            )
            if candidats:
                noms.setdefault(candidats[0], None)
            continue
        for segment in _EN_LIGNE.findall(ligne):
            for nom in _identifiants(segment, _DECORE):
                noms.setdefault(nom, None)
    return list(noms)


# Ce qu'on retire d'un verbe pour en garder le RADICAL. Deux caractères : c'est
# ce qui ramène « interroger » à `interrog`, « analyser » à `analys`,
# « prédire » à `predi` et « répondre » à `repond` — et donc ce qui fait qu'une
# réponse compte comme portant l'action, qu'elle la conjugue (« je prédis »),
# la nominalise (« une prédiction ») ou la reprenne telle quelle.
#
# C'est la même convention que l'oracle de `mesure_surface_conversationnelle.py`,
# qui attend « analys » et « predi » plutôt que des mots entiers, et ce n'est pas
# une coïncidence : mesurer une capacité et exiger qu'elle soit dite sont la même
# opération, faite à deux endroits.
_TERMINAISON = 2
# En deçà de cette longueur, le radical ne discriminerait plus rien : « lire »
# donnerait `li`, présent dans la moitié des phrases françaises.
_RADICAL_MINIMAL = 5


def _actions(faits: str) -> list[str]:
    """Ce que les faits annoncent SAVOIR FAIRE, par le radical du verbe.

    Le pendant de ``_enumeres`` pour les puces qu'il laisse passer, et c'est
    par là qu'un défaut mesuré est entré. ``_enumeres`` n'exige d'une puce que
    les IDENTIFIANTS qu'elle porte ; une puce dont la décoration est une phrase
    française — « - **analyser et visualiser** — du code Python… » — n'en porte
    aucun, donc n'exigeait rien. Toute la liste des capacités pouvait tomber de
    la réponse sans que la ceinture bronche.

    Elle ne bronchait pas, et pourtant la famille « que sais-tu faire ? » tenait
    : par accident. ``decrire_les_capacites`` finit par « Mes sources :
    `titanic`, `iris`… », une énumération EN LIGNE que ``_enumeres`` réclame —
    et le modèle qui résumait les capacités laissait aussi tomber l'inventaire,
    donc se faisait prendre sur l'inventaire. Le jour où sa formulation a cité
    les sources tout en oubliant qu'il savait ANALYSER, plus rien ne l'arrêtait :
    « je peux te demander quoi ? » rendait une liste de SUJETS — mes capacités,
    mes sources, mes modèles — sans une seule des quatre actions (mesuré le
    2026-09-16, deux campagnes).

    Le RADICAL et non le mot : une réponse a le droit d'écrire « je prédis » ou
    « des prédictions » là où le fait dit « prédire ». Ce qui est exigé, c'est
    que l'action soit DITE, pas qu'elle soit recopiée.
    """
    radicaux: dict[str, None] = {}
    for brute in faits.splitlines():
        ligne = brute.replace("\\", "")
        if _CITATION.match(ligne) or not _PUCE.match(ligne):
            continue
        decore = _DECORE.search(ligne)
        if decore is None or _identifiants(decore.group(0), _DECORE):
            continue
        mots = replie(next(filter(None, decore.groups()), "")).split()
        if mots and len(mots[0]) >= _RADICAL_MINIMAL:
            radicaux.setdefault(mots[0][:-_TERMINAISON], None)
    return list(radicaux)


# Ce qu'il faut à un jeton pour prouver qu'on a LU une fiche. Être absent de
# toutes les autres fiches n'y suffit pas, et un test l'a montré : « Tu
# travailles sur les sources `production` et `stocks`. » était déclarée
# DÉCRITE — `les` n'apparaissait que dans la description de `stocks`, donc
# comptait pour marque, donc un mot de liaison français suffisait à prouver une
# lecture. Un mot d'au moins quatre caractères, ou un nombre d'au moins deux
# chiffres : `duckdb`, `entrepots`, `891` sont des faits ; `les`, `ses`, `9` ne
# le sont pas. Deux chiffres et non quatre caractères pour les nombres, parce
# qu'un compte de lignes est un fait quelle que soit sa longueur — `891` est
# porté par la seule fiche de `titanic` — alors qu'un chiffre isolé se retrouve
# dans n'importe quelle phrase.
_LONGUEUR_D_UN_MOT_MARQUANT = 4
_LONGUEUR_D_UN_NOMBRE_MARQUANT = 2


def _fait_une_marque(jeton: str) -> bool:
    if jeton.isdigit():
        return len(jeton) >= _LONGUEUR_D_UN_NOMBRE_MARQUANT
    return len(jeton) >= _LONGUEUR_D_UN_MOT_MARQUANT


def marques_des_fiches(fiches: Mapping[str, str]) -> dict[str, frozenset[str]]:
    """Pour chaque fiche, les jetons qu'elle est SEULE à porter.

    Ce qui, dans une réponse, prouve qu'elle a lu la fiche de cette source-ci :
    son type quand il la distingue, un de ses volumes, une de ses dates, un mot
    de sa description qu'aucune autre fiche ne porte. Rien n'est écrit à la
    main — les marques sont calculées depuis les fiches elles-mêmes, donc elles
    suivent le catalogue et ne mesurent pas la mémoire de leur auteur.

    **Le NOM de la source en est retiré, et c'est tout l'objet.** Une réponse
    qui répète les noms qu'on vient de lui donner porte des noms et zéro fait ;
    c'est le défaut jumeau de l'inventaire complet, et celui qui passait.

    **Et un mot de liaison n'est pas un fait**, même quand une seule fiche le
    porte (``_fait_une_marque``) : `les` n'a distingué `stocks` que par accident
    de rédaction, et il suffisait alors d'écrire « sur les sources » pour être
    réputé avoir lu.

    Les fiches doivent être celles de TOUT le catalogue déclaré et non des
    seules sources servies : un mot commun aux cinq fiches n'est la marque
    d'aucune, et le mesurer sur deux fiches seulement en ferait une marque dès
    que les trois autres sont absentes du tour.
    """
    replies = {nom: set(replie(fiche).split()) for nom, fiche in fiches.items()}
    marques = {}
    for nom, a_moi in replies.items():
        ailleurs = {mot for autre, mots in replies.items() if autre != nom for mot in mots}
        propres = {m for m in a_moi - ailleurs if _fait_une_marque(m)}
        marques[nom] = frozenset(propres - {replie(nom).strip()})
    return marques


def _fiche_citee_sans_un_fait(reponse: str, a_porter: Mapping[str, frozenset[str]]) -> str:
    """Une source citée sans un seul fait de sa fiche — ``""`` si rien.

    **Le défaut jumeau de l'omission**, et il passait. « Tu travailles sur les
    sources `production` et `stocks`. Dis-moi ce que tu souhaites savoir sur ces
    sources. » : cent huit caractères pour neuf cent quatre-vingt-six servis,
    deux noms, pas un fait — et la ceinture la servait, parce qu'elle comparait
    des NOMS et que les deux noms y étaient. Une réponse qui cite une source
    sans porter un mot de sa fiche ne répond pas plus qu'une réponse qui omet
    son nom (mesuré le 2026-09-17, trois tirages sur trois).

    Ce qui est exigé est **un** élément, pas la fiche entière : le modèle a le
    droit de résumer, de reformuler et de choisir ce qu'il retient. Ce qu'il n'a
    pas, c'est le droit de ne rien retenir.

    Une fiche sans aucune marque ne réclame rien : deux sources déclarées avec
    la même description n'ont rien qui les distingue, et exiger l'impossible
    ferait servir le repli sur des réponses justes.
    """
    plat = replie(reponse)
    creuses = [
        nom
        for nom, marques in a_porter.items()
        if marques and not any(f" {marque} " in plat for marque in marques)
    ]
    if creuses:
        return "source(s) citée(s) sans un fait de leur fiche : " + ", ".join(creuses)
    return ""


def defaut_de_fondation(
    reponse: str,
    faits: str,
    a_enumerer: str | None = None,
    a_porter: Mapping[str, frozenset[str]] | None = None,
) -> str:
    """Ce qui interdit de servir la formulation du modèle — ``""`` si rien.

    Le modèle formule, mais il ne décide pas de ce qui est vrai. Deux défauts
    le disqualifient, et dans les deux cas ce sont les ``faits`` qui sont
    servis à l'utilisateur (cf. l'en-tête du module) :

    - **un nom qu'aucun fait ne porte.** C'est la reprise du défaut d'``acfd8f5``
      par une autre porte : un modèle à qui l'on demande les tables d'une base
      « familière » sait en citer de mémoire. Un nom inventé est plus nocif
      qu'une réponse absente — il a l'air d'une lecture de la source.
    - **un nom rendu par l'outil et absent de la réponse.** Une liste
      incomplète n'est pas une réponse à « quelles colonnes ? », et c'est un
      défaut mesuré : la version narrée par le modèle avait laissé tomber deux
      colonnes sur dix (mesure du 2026-09-07, §4 de
      `surface-conversationnelle.md`).
    - **une source citée sans un seul fait de sa fiche.** Le jumeau du
      précédent : les deux noms servis sont bien là, et rien d'autre. « Tu
      travailles sur les sources `production` et `stocks`. » répond à une
      question sur deux sources avec zéro caractère de ce qu'on venait de lire
      dedans, et passait (mesuré le 2026-09-17, trois tirages sur trois).
    - **une attribution qui ne correspond pas aux faits**, dans les DEUX sens
      (``_attribution_qui_ne_colle_pas``).
    - **une consigne qui ne correspond pas aux faits**, dans les DEUX sens
      aussi (``_consigne_qui_ne_colle_pas``). Un dictionnaire ne dit pas
      seulement ce qu'une valeur SIGNIFIE, il dit ce qu'il ne faut pas en
      faire ; une reformulation qui garde le sens et laisse tomber l'impératif
      rend un utilisateur informé et une moyenne fausse.

    ``a_enumerer`` : la part des faits qui doit être reprise EN ENTIER. Par
    défaut, tout — c'est le cas d'une question sur le catalogue (« quelles
    sources as-tu ? »), où une liste incomplète est une réponse fausse.

    ``a_porter`` : les sources dont la fiche a été servie, avec ce qui distingue
    chacune (``marques_des_fiches``). Chacune doit laisser **un** fait dans la
    réponse — pas seulement son nom (``_fiche_citee_sans_un_fait``). Vide par
    défaut : aucun autre appelant n'a de fiche à faire porter, et l'inventaire
    du catalogue n'en est pas une — il énumère des noms, et c'est sa réponse.

    **Elle ne vaut pas pour une question qui CHOISIT.** « As-tu une source qui
    parle de maintenance ? » appelle une réponse à UNE source ; exiger qu'elle
    reprenne les quatre autres, c'est jeter la bonne réponse. Mesuré le
    2026-09-16 : le modèle répondait « la source `interventions` », la ceinture
    criait « fait(s) omis : exploitation, telemetrie, referentiel, facturation »
    et servait les 2 200 caractères du catalogue entier. Quatre questions
    différentes recevaient le même pavé, et l'agent passait pour incapable de
    lire ses propres descriptions alors qu'il les avait lues.

    L'interdit d'INVENTER, lui, ne bouge jamais : il porte sur tous les faits.
    Ce qui est relâché, c'est l'obligation de tout redire — pas celle de ne
    rien ajouter.

    Le message rendu est destiné à la **trace**, pas à l'utilisateur : ce qu'il
    lit, lui, est la réponse déterministe, qui ne dit pas qu'elle est un repli.
    """
    if not reponse.strip():
        return "réponse vide"
    if replie(reponse).strip() == SENTINELLE_HORS_SUJET.lower():
        return "réponse hors sujet"
    connus = _identifiants(faits, _JETON)
    inventes = [n for n in _identifiants(reponse, _ENTRE_ACCENTS, _EN_SERPENT) if n not in connus]
    if inventes:
        return "nom(s) qu'aucun fait ne porte : " + ", ".join(sorted(inventes))
    plat = replie(reponse)
    exhaustifs = faits if a_enumerer is None else a_enumerer
    omis = [n for n in _enumeres(exhaustifs) if f" {n} " not in plat]
    if omis:
        return "fait(s) omis : " + ", ".join(omis)
    tues = [a for a in _actions(exhaustifs) if a not in plat]
    if tues:
        return "action(s) omise(s) : " + ", ".join(tues)
    creuse = _fiche_citee_sans_un_fait(reponse, a_porter or {})
    if creuse:
        return creuse
    return _attribution_qui_ne_colle_pas(reponse, faits) or _consigne_qui_ne_colle_pas(
        reponse, faits
    )


# Comment les faits annoncent qu'ils citent le dictionnaire d'une source. C'est
# NOTRE texte — celui de ``decrire_le_schema`` — donc la marque est fiable, et
# c'est la seule chose qu'on cherche : on ne juge pas la phrase du modèle.
_EN_TETE_DE_DICTIONNAIRE = "ce qu'en dit le dictionnaire de"
_MOTS_DE_PROVENANCE = ("dictionnaire", "dictionary")


def _attribution_qui_ne_colle_pas(reponse: str, faits: str) -> str:
    """L'attribution de la réponse doit être celle des faits — dans les deux sens.

    Une seule règle, et elle se lit à l'endroit comme à l'envers : **la
    formulation attribue si et seulement si les faits attribuent.** Les deux
    moitiés sont des défauts mesurés, et ce sont deux défauts différents.

    **Les faits citent le dictionnaire, la réponse non.** Mesuré 3 fois sur 3
    sur « le statut RET, il recouvre quoi au juste ? » : le modèle rend le bon
    fait — `RET` = matériel retiré du service et démonté — et ne dit pas d'où il
    le tient. Or c'est précisément ce qui distingue une LECTURE du référentiel
    de quelqu'un d'un savoir général sur les codes d'état ; sans elle,
    l'utilisateur ne peut pas savoir laquelle des deux il vient de recevoir.
    C'est la dette B, par une autre porte que celle qui l'avait fermée.

    **Les faits ne citent aucun dictionnaire, et la réponse en cite un.** Mesuré
    sur `titanic`, qui n'en déclare pas : le modèle a écrit « selon le
    dictionnaire de la source ». C'est pire que l'omission — une attribution
    inventée donne l'autorité de la base à un savoir général, et c'est
    exactement la famille ``acfd8f5``. Le témoin sans dictionnaire n'était
    jusqu'ici tenu que par une campagne de mesure ; il est ici tenu par du code.

    Dans les deux cas, ce sont les faits qui sont servis — et les faits, eux,
    portent l'en-tête d'attribution quand il y a lieu et se taisent sinon.
    """
    attendue = _EN_TETE_DE_DICTIONNAIRE in faits.lower()
    donnee = any(mot in reponse.lower() for mot in _MOTS_DE_PROVENANCE)
    if attendue and not donnee:
        return "provenance tue : les faits citent le dictionnaire, la réponse non"
    if donnee and not attendue:
        return "provenance inventée : aucun fait ne cite de dictionnaire"
    return ""


# --- la ceinture de CONSIGNE : un impératif des faits doit survivre ----------

# Ce qui, dans une phrase, marque une INTERDICTION ou une mise à l'écart. Les
# formes positives n'y sont pas, et c'est délibéré : « il faut calculer la
# moyenne » n'est pas la consigne d'un dictionnaire, c'est une phrase
# ordinaire, et l'y admettre ferait crier la ceinture sur les sources qui ne
# déclarent rien. Une consigne de dictionnaire dit ce qu'il NE faut PAS faire
# de la donnée — c'est le genre entier du piège : une valeur qui se laisse
# calculer alors qu'elle n'est pas une mesure.
_MARQUES_D_OBLIGATION = (
    " a ecarter",
    " a exclure",
    " a retirer",
    " a ignorer",
    " a filtrer",
    " a eviter",
    " a proscrire",
    " a ne pas ",
    " ne doit pas ",
    " ne doivent pas ",
    " ne peut pas ",
    " ne peuvent pas ",
    " ne doit jamais ",
    " ne doivent jamais ",
    " il ne faut pas ",
    " ne pas ",
    " sans inclure",
    " sans compter",
    " sans tenir compte",
    " ecarte",
    " ecartant",
    " exclure",
    " exclu",
    " ignorer",
    " proscri",
)

# Ce sur quoi la consigne doit porter pour en être une : un CALCUL. Ce ne sont
# pas nos mots — ce sont les noms des opérations elles-mêmes, ceux que le
# dictionnaire écrit et ceux qu'une réponse juste écrit à son tour. « total »
# n'y est pas : « sur un total de 10 000 relevés » est une tournure de
# dénombrement, pas d'agrégation, et l'admettre confondrait les deux.
_GRANDEURS_CALCULEES = (
    "moyenne",
    "moyenner",
    "calcul",
    "agregat",
    "somme",
    "mediane",
    "percentile",
    "quartile",
    "comptage",
    "statistique",
    "avg",
    "sum(",
    "count(",
    "mean",
    "min(",
    "max(",
)

# Une phrase, au sens où une consigne en tient une. Le point-virgule et le
# deux-points comptent : « n'est pas une puissance : à écarter de toute
# moyenne » est UNE consigne, et couper au seul point la garderait entière.
_FIN_DE_PHRASE = re.compile(r"[.;:!?\n]")


def porte_une_consigne(texte: str) -> str:
    """La consigne que ``texte`` énonce — ``""`` s'il n'en énonce aucune.

    Une consigne est une **interdiction qui porte sur un calcul**, et c'est la
    conjonction des deux qui en fait une. Ni l'une ni l'autre ne suffit, et
    c'est ce qui distingue les trois formulations qu'on a mesurées sur `S4` :

    - « `-1` … n'est pas une puissance : **à écarter de toute moyenne** » —
      interdiction (*à écarter*) sur un calcul (*moyenne*). C'est la consigne
      du dictionnaire ;
    - « elle **ne doit pas** être considérée comme une puissance » —
      interdiction, aucun calcul. C'est une phrase sur le SENS, et c'est
      exactement celle que le modèle rend 3 fois sur 3 en laissant tomber la
      consigne ;
    - « elle **ne doit pas** être incluse dans tout **calcul** de puissance » —
      les deux. C'est la variante qu'on avait vue passer, et elle est juste.

    Les deux doivent tenir dans la **même phrase**. Sans cette borne, un texte
    qui interdit quelque chose au début et parle de moyenne à la fin passerait
    pour porter une consigne qu'il n'énonce pas.

    Rend la marque trouvée plutôt qu'un booléen : c'est ce qui rend la trace
    lisible quand la ceinture écarte une formulation.
    """
    for phrase in _FIN_DE_PHRASE.split(texte):
        plat = replie(phrase)
        marque = next((m for m in _MARQUES_D_OBLIGATION if m in plat), "")
        if marque and any(g in plat for g in _GRANDEURS_CALCULEES):
            return marque.strip()
    return ""


def _citations(faits: str) -> str:
    """Les seules lignes des faits que NOUS n'écrivons pas : le dictionnaire.

    La consigne se cherche là et nulle part ailleurs. Le reste des faits est
    notre propre texte, et il porte ses propres impératifs — « Demande-moi une
    table en particulier… » — qui ne sont pas des consignes de traitement de la
    donnée et n'ont rien à faire dans une réponse.
    """
    return "\n".join(
        _CITATION.sub("", ligne) for ligne in faits.splitlines() if _CITATION.match(ligne)
    )


def _consigne_qui_ne_colle_pas(reponse: str, faits: str) -> str:
    """La consigne de la réponse doit être celle des faits — dans les deux sens.

    Même mécanique que ``_attribution_qui_ne_colle_pas``, sur un autre genre de
    fait, et pour une raison mesurée : la ceinture vérifiait des NOMS, des
    énumérations, des actions et une attribution, et laissait passer la perte
    d'un IMPÉRATIF. C'est la dette `I`.

    **Les faits portent une consigne, la réponse non.** Mesuré 3/3 sur deux
    campagnes, sur « puissance_kw, ça signifie quoi ? » : le dictionnaire écrit
    que `-1` est *à écarter de toute moyenne*, l'extrait servi le porte, et la
    réponse s'arrête à « ce n'est pas une puissance ». L'utilisateur sait alors
    que la valeur est particulière ; il ne sait pas que sa moyenne sera fausse
    s'il la calcule quand même — et c'est l'écart entre 66,67 et 68,76 que ce
    catalogue existe pour montrer. Une consigne perdue coûte un chiffre faux,
    pas une nuance de style.

    **La réponse porte une consigne, les faits non.** C'est la moitié qu'on
    n'aurait pas écrite sans la leçon de l'attribution, et c'est la plus
    nocive : une règle de traitement inventée sur une source qui n'en déclare
    aucune donne l'autorité de la base à un savoir général. `titanic` et `iris`
    ne déclarent aucun dictionnaire ; rien ne doit s'y déclencher, et c'est
    exactement ce que le volet témoin de la mesure vérifie.

    Dans les deux cas, ce sont les faits qui sont servis — et les faits, eux,
    portent la consigne quand la source en écrit une, et se taisent sinon.
    """
    attendue = porte_une_consigne(_citations(faits))
    donnee = porte_une_consigne(reponse)
    if attendue and not donnee:
        return f"consigne tue : les faits l'énoncent (« {attendue} »), la réponse non"
    if donnee and not attendue:
        return f"consigne inventée : aucun fait ne l'énonce (« {donnee} »)"
    return ""
