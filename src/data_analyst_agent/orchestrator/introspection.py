"""Répondre aux questions SUR le système — depuis ses sources de vérité.

Les quatre capacités historiques (`query`, `analyze`, `predict`,
`fetch_then_predict`) sont quatre actions **sur** les données. Aucune ne couvre
« quelles sources possèdes-tu ? » : la demande n'était pas mal classée, elle
était *inclassable*, et le repli du planificateur nommait la source qu'on lui
demandait tout en déclarant ne pas comprendre (mesuré :
[docs/surface-conversationnelle.md](../../../docs/surface-conversationnelle.md),
une question méta sur trois en repli).

**Jamais depuis la mémoire du modèle.** Chaque réponse d'ici est construite à
partir d'un artefact du dépôt : le catalogue (`sources/catalogue.yaml`), le
registre (`models/registry.yaml`), les schémas de features (`SCHEMAS`), et
l'ontologie que la source rend elle-même. C'est le défaut corrigé par
``acfd8f5`` — « décris le dataset iris » répondu de mémoire, avec une jolie
prose sur « un ensemble classique de classification floristique » et zéro
requête — et il ne doit pas revenir par cette porte.

Ce module est **pur** : il ne lit ni fichier ni base. L'ontologie d'une source
lui est passée, déjà lue par le nœud qui a ouvert la connexion — l'entrée/sortie
appartient à un nœud du graphe, pas à un formateur de texte.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, get_args

from data_analyst_agent.agents.inference.registry import Registry
from data_analyst_agent.agents.inference.schemas import SCHEMAS, describe_features
from data_analyst_agent.agents.retrieval.catalog import Catalog, Source
from data_analyst_agent.agents.retrieval.sql import SchemaInfo, TableInfo

Sujet = Literal["sources", "schema", "modeles", "features", "capacites"]

# Les sujets dont la réponse exige l'ontologie de la source — donc une
# connexion. Tous les autres sont entièrement déterminés par le catalogue, le
# registre et les schémas de features : lisibles sans ouvrir quoi que ce soit.
SUJETS_AVEC_ONTOLOGIE: tuple[Sujet, ...] = ("schema",)


def _replie(texte: str) -> str:
    """Minuscules, sans accents, ponctuation ramenée à des blancs.

    Le lexique est écrit une fois, sous cette forme, et n'a donc pas à
    prévoir « À quelles bases… », « a quelles bases » et « À QUELLES BASES ».
    L'apostrophe et le trait d'union deviennent des blancs : « qu'est-ce que
    tu sais faire » et « qu est ce que tu sais faire » se valent, et c'est ce
    qui évite d'écrire chaque tournure deux fois.
    """
    sans_accent = unicodedata.normalize("NFKD", texte.lower())
    nu = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return " " + re.sub(r"[^a-z0-9_]+", " ", nu).strip() + " "


# Ce qui fait d'une question une question sur les DONNÉES, quelle que soit sa
# tournure. Le lexique s'abstient dès qu'un de ces mots paraît et laisse le
# planificateur trancher : « quelles colonnes de passengers contiennent des
# valeurs manquantes ? » a exactement la tournure d'une question de schéma, et
# la réponse est un SELECT. Mieux vaut rendre une question au planificateur que
# lui voler une question sur les données — la première coûte un appel LLM, la
# seconde donne une réponse fausse sans le dire.
MARQUEURS_DE_CALCUL = (
    " combien ",
    " moyenne ",
    " moyen ",
    " mediane ",
    " median ",
    " somme ",
    " pourcentage ",
    " taux ",
    " manquant",
    " correlation ",
    " histogramme ",
    " graphique ",
    " courbe ",
    " camembert ",
    " nuage de points ",
    " maximum ",
    " minimum ",
    " classement ",
    " repartition ",
    " predis ",
    " calcule ",
)

# Le lexique, par sujet. Des TOURNURES et non des mots isolés : « source » seul
# attraperait « la source titanic contient combien de lignes ? ». L'ordre de ce
# tuple est l'ordre de priorité — du sujet le plus précis au plus général,
# parce que « de quoi as-tu besoin pour prédire ? » parle de features et non de
# modèles, et que « capacites » est le sujet fourre-tout qui doit passer en
# dernier.
LEXIQUE: tuple[tuple[Sujet, tuple[str, ...]], ...] = (
    (
        "features",
        (
            "quels attributs",
            "quelles features",
            "quels champs attend",
            "quelles variables attend",
            "quelles informations te faut il",
            "quelles informations il te faut",
            "quelles donnees te faut il",
            "de quels attributs as tu besoin",
            "de quelles informations as tu besoin",
            "de quoi as tu besoin",
            "que te faut il pour predire",
            "quelles mesures",
            "quelles valeurs dois je",
            "quelles valeurs faut il",
            "que dois je te donner",
        ),
    ),
    (
        "schema",
        (
            "quelles tables",
            "quels tables",
            "les tables de la",
            "quelles colonnes",
            "quels champs",
            "les colonnes de",
            "quelle est la structure",
            "comment est structuree",
            "comment est structure",
            "le schema de",
            "quel est le schema",
            "quel schema",
            "decris le schema",
            "que signifie la colonne",
            "que veut dire la colonne",
            "a quoi correspond la colonne",
            "signification de la colonne",
            "que contient la table",
        ),
    ),
    (
        "modeles",
        (
            "quels modeles",
            "quel modele",
            "tes modeles",
            "vos modeles",
            "modeles de prediction",
            "modeles disponibles",
            "tu sais faire des predictions",
            "tu peux faire des predictions",
            "peux tu faire des predictions",
            "sais tu faire des predictions",
            "que peux tu predire",
            "que sais tu predire",
            "sur quoi peux tu predire",
            "quelles predictions",
        ),
    ),
    (
        "sources",
        (
            "sources de donnees",
            "quelles sources",
            "quels sources",
            "tes sources",
            "vos sources",
            "quelles bases",
            "bases de donnees as tu",
            "sur quoi peux tu travailler",
            "sur quoi sais tu travailler",
            "sur quoi tu peux travailler",
            "sur quelles donnees peux tu",
            "a quelles bases",
            "a quelles sources",
            "ton catalogue",
            "quels jeux de donnees",
            "jeux de donnees disponibles",
            "donnees disponibles",
            "quelles donnees as tu",
            "quelles donnees possedes tu",
            "donnees tu possedes",
            "donnees que tu possedes",
        ),
    ),
    (
        "capacites",
        (
            "que sais tu faire",
            "que peux tu faire",
            "qu est ce que tu sais faire",
            "qu est ce que tu peux faire",
            "a quoi sers tu",
            "quel est ton role",
            "tes capacites",
            "quelles sont tes capacites",
            "comment peux tu m aider",
            "que fais tu",
            "presente toi",
            "qui es tu",
            "aide moi a comprendre ce que tu",
        ),
    ),
)


# « Que signifie X ? » où X est un NOM, et non un groupe nominal. Le lexique
# ci-dessus exige le mot « colonne » (« que signifie la colonne class_id ? ») :
# personne ne l'écrit. On demande « que signifie store_id ? », et la question
# partait au planificateur, qui la classait en `query` et faisait écrire un
# SELECT sur une colonne dont on demandait le SENS — introuvable dans les
# données, il est dans le dictionnaire.
#
# Le garde-fou est le mot qui SUIT : un déterminant ou une préposition annonce
# un groupe nominal, donc une question sur les données (« que signifie une
# progression de 12 % ? », « que signifie ce pic de novembre ? »), et on
# s'abstient. Un nom nu (`store_id`, `revenue`) désigne un champ.
DEMANDES_DE_SENS = (
    "que signifie",
    "que veut dire",
    "a quoi correspond",
    "quel est le sens de",
    "signification de",
)

# Ce qui, juste après, annonce une tournure et non un nom de champ.
ANNONCEURS_DE_GROUPE = (
    "le",
    "la",
    "les",
    "l",
    "un",
    "une",
    "des",
    "du",
    "de",
    "d",
    "ce",
    "cet",
    "cette",
    "ces",
    "mon",
    "ton",
    "son",
    "cela",
    "ca",
)


def _demande_le_sens_d_un_nom(plat: str) -> bool:
    """La question demande-t-elle le sens d'un nom nu (et non d'un groupe) ?"""
    for demande in DEMANDES_DE_SENS:
        marque = f" {demande} "
        if marque not in plat:
            continue
        suite = plat.split(marque, 1)[1].split()
        if suite and suite[0] not in ANNONCEURS_DE_GROUPE:
            return True
    return False


def sujet_de(question: str) -> Sujet | None:
    """Le sujet d'une question sur le système, ou ``None`` si ce n'en est pas une.

    ``None`` n'est pas un échec : c'est « laisse le planificateur trancher ».
    Le lexique est délibérément **précis plutôt qu'exhaustif** — il est le
    chemin rapide (zéro appel LLM), et la capacité ``describe_system`` offerte
    au planificateur est le filet qui rattrape les tournures qu'il ne connaît
    pas. Se tromper de ce côté coûte un appel LLM ; se tromper de l'autre
    donne une réponse fausse à une question sur les données.
    """
    plat = _replie(question)
    if any(marqueur in plat for marqueur in MARQUEURS_DE_CALCUL):
        return None
    for sujet, tournures in LEXIQUE:
        if any(tournure in plat for tournure in tournures):
            return sujet
    return "schema" if _demande_le_sens_d_un_nom(plat) else None


# --- ce qu'on cherche à qualifier dans la question ---------------------------


def _nomme_dans(question: str, noms: list[str]) -> str | None:
    """Le seul nom de la liste cité dans la question, ou ``None``.

    ``None`` quand aucun n'y est **et** quand plusieurs y sont : deux noms
    cités ne désignent pas une cible, et en choisir un serait deviner.
    """
    plat = _replie(question)
    trouves = [n for n in noms if f" {_replie(n).strip()} " in plat]
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
# commentaire de ``plan.py``) : elle est routée par du code avant l'appel au
# modèle, pas choisie par lui. Elle a sa place dans la réponse quand même —
# c'est bel et bien quelque chose que l'agent sait faire.
CAPACITE_SUR_SOI = (
    "**répondre sur moi-même** — mes sources, leurs tables et leurs colonnes, "
    "mes modèles et les attributs qu'ils attendent"
)


def decrire_les_sources(catalogue: Catalog) -> str:
    """Le catalogue, rendu à l'utilisateur — noms, types, descriptions."""
    if not catalogue.sources:
        return "Je n'ai aucune source de données déclarée dans mon catalogue."
    lignes = [f"J'ai accès à {len(catalogue.sources)} source(s) de données :", ""]
    for source in catalogue.sources:
        description = source.description.strip() or "sans description"
        lignes.append(f"- **{source.name}** ({source.type}) — {description}")
    lignes += [
        "",
        "Demande-moi les tables ou les colonnes de l'une d'elles "
        "(« quelles colonnes a la source "
        f"{catalogue.sources[0].name} ? ») pour en voir le détail.",
    ]
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


# Les mots communs par lesquels une question INTRODUIT un nom : « la source
# maxizoo », « la table stores ». Ils appartiennent à la question, pas aux
# données — et une base réelle a des colonnes qui portent les noms du métier.
INTRODUCTEURS = ("source", "sources", "base", "bases", "table", "tables", "colonne", "colonnes")


def _demande_les_tables(question: str) -> bool:
    """La question porte-t-elle sur les TABLES, et non sur les colonnes ?

    Les deux sont le sujet ``schema``, et ce n'est pas la même réponse.
    « Quelles colonnes… » veut le détail ; « quelles tables… » veut la liste.
    """
    plat = _replie(question)
    if any(mot in plat for mot in (" colonne ", " colonnes ", " champ ", " champs ")):
        return False
    return any(mot in plat for mot in (" table ", " tables "))


def _sans_les_introducteurs(question: str, noms: list[str]) -> str:
    """La question, privée du mot commun qui introduit chacun des ``noms`` cités.

    Mesuré sur la base Maxizoo, dix tables : « quelles colonnes a la SOURCE
    maxizoo ? » répondait « dans la table `weather`, la colonne `source` est de
    type VARCHAR » — parce que `weather.source` existe, et que le mot qui
    désignait la source a été pris pour lui. La réponse était fausse et ne le
    disait pas. Deux tables jouets ne pouvaient pas montrer ça ; un schéma en
    étoile de soixante-dix-neuf colonnes le montre au premier essai.

    On ne retire que l'introducteur ACCOLÉ à un nom réellement cité : « que
    signifie la colonne source ? » ne nomme ni source ni table, rien n'est
    retiré, et la colonne `source` reste trouvable.
    """
    plat = _replie(question)
    for nom in noms:
        cible = re.escape(_replie(nom).strip())
        plat = re.sub(rf" (?:{'|'.join(INTRODUCTEURS)}) (?={cible} )", " ", plat)
    return plat


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
    # Le détail se cherche dans la question DÉBARRASSÉE des mots qui ne servent
    # qu'à désigner la source et ses tables : sans quoi le « source » de « la
    # source maxizoo » passe pour la colonne du même nom (cf.
    # ``_sans_les_introducteurs``).
    detail = _sans_les_introducteurs(question, [cible.source.name, *schema.table_names()])
    table_visee = _nomme_dans(detail, schema.table_names())
    tables = [t for t in schema.tables if t.name == table_visee] if table_visee else schema.tables
    colonne_visee = _nomme_dans(detail, _dedoublonne(c.name for t in tables for c in t.columns))

    if colonne_visee:
        porteuse = next(t for t in tables if any(c.name == colonne_visee for c in t.columns))
        lignes = _decrire_la_colonne(porteuse, colonne_visee)
        terme = colonne_visee
    elif table_visee:
        lignes = [f"La table `{table_visee}` de la source `{cible.source.name}` :", ""]
        lignes += _decrire_la_table(tables[0])
        terme = table_visee
    elif _demande_les_tables(question):
        # « Quelles tables ? » demande des TABLES. Déplier leurs colonnes au
        # passage rend 79 lignes de schéma là où dix noms répondaient — mesuré
        # sur Maxizoo : 4 979 caractères contre 590. Ce n'est pas une question
        # de goût, c'est la question qui n'était pas lue.
        lignes = [
            f"La source `{cible.source.name}` contient {len(schema.tables)} table(s) "
            f"({schema.dialect}) : " + ", ".join(f"`{t.name}`" for t in tables) + ".",
            "",
            "Demande-moi l'une d'elles pour en voir les colonnes "
            f"(« quelles colonnes dans la table {tables[0].name} ? »).",
        ]
        terme = None
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
