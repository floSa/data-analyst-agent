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


def extrait_du_dictionnaire(dictionnaire: str, terme: str, maximum: int = 8) -> str:
    """Les lignes du dictionnaire qui citent ``terme`` ("" s'il n'y en a aucune).

    Le dictionnaire d'une source dit ce que les données **veulent dire**, là où
    le DDL ne dit que des types. Le rendre en entier noierait la réponse — sur
    une base réelle il fait plusieurs milliers de mots — d'où l'extrait, borné,
    autour du terme demandé.
    """
    citantes = [
        ligne.strip()
        for ligne in dictionnaire.splitlines()
        if terme.lower() in ligne.lower() and ligne.strip()
    ]
    return "\n".join(f"> {ligne}" for ligne in citantes[:maximum])


@dataclass(frozen=True)
class Ontologie:
    """Ce qu'une source dit d'elle-même, déjà lu par le nœud qui l'a ouverte."""

    source: Source
    schema: SchemaInfo
    dictionnaire: str | None = None


def _cible(question: str, ontologies: list[Ontologie]) -> Ontologie | None:
    """L'ontologie sur laquelle porte la question, ou ``None`` si c'est indécidable.

    Trois voies, dans cet ordre. La source nommée ; à défaut la source qui
    porte la table nommée — « quelles colonnes dans la table passengers ? »
    ne nomme aucune source et n'est pourtant pas ambiguë ; à défaut l'unique
    source, s'il n'y en a qu'une.
    """
    nomme = _nomme_dans(question, [o.source.name for o in ontologies])
    if nomme:
        return next(o for o in ontologies if o.source.name == nomme)
    tables = _dedoublonne(t.name for o in ontologies for t in o.schema.tables)
    table = _nomme_dans(question, tables)
    if table:
        porteuses = [o for o in ontologies if table in o.schema.table_names()]
        if len(porteuses) == 1:
            return porteuses[0]
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


def decrire_le_schema(question: str, ontologies: list[Ontologie]) -> str:
    """Les tables, les colonnes ou le sens d'une colonne — au bon niveau de détail.

    Quatre niveaux, choisis sur ce que la question nomme réellement. Une
    colonne : sa fiche, clé étrangère et dictionnaire compris. Une table : ses
    colonnes. Une source : toutes ses tables avec leurs colonnes. Rien de
    décidable : le tour d'horizon — chaque source et le nom de ses tables,
    sans les colonnes, parce que déplier trois cents colonnes pour répondre
    « je ne sais pas laquelle tu veux » n'aide personne.
    """
    if not ontologies:
        return "Je n'ai aucune source de données déclarée dans mon catalogue."
    cible = _cible(question, ontologies)
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
    if extrait:
        lignes += ["", f"Ce qu'en dit le dictionnaire de `{cible.source.name}` :", extrait]
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


def defaut_de_fondation(reponse: str, faits: str) -> str:
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
    omis = [n for n in _enumeres(faits) if f" {n} " not in plat]
    if omis:
        return "fait(s) omis : " + ", ".join(omis)
    return ""
