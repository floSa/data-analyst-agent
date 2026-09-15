"""Correspondance DÉCLARÉE entre les colonnes d'une source et les features d'un modèle.

`fetch_then_predict` va chercher des lignes en base puis prédit dessus. Entre la
ligne lue et le schéma de features, il y a DEUX écarts, et il faut les tenir
séparés :

- un écart de **nom** — la feature s'appelle ``pclass``, le nom du dataset
  d'entraînement ; la base, elle, modélise la classe du billet dans une table
  ``classes`` jointe par ``class_id``, et la porte sous ``level`` ;
- un écart de **représentation** — la même classe existe aussi sous
  ``classes.label``, et vaut alors ``'3e classe'`` là où le schéma attend ``3``.

Ces deux écarts étaient laissés à l'agent SQL, qui devait les franchir en
devinant. Mesuré sur la source ``titanic`` avec la question « Prédis la survie
des cinq premiers passagers de la base », cinq tirages par moteur : vLLM choisit
``passengers.class_id`` sans alias (``pclass`` manquant, 0/5 lignes prédites,
5 fois sur 5) ; Ollama choisit ``classes.label`` (``'3e classe'``, 0/5) quatre
fois sur cinq, et ``class_id AS pclass`` — juste, par chance — une fois. Trois
colonnes candidates, trois réponses, aucune raison de trancher : c'est une
devinette, pas une correspondance.

**Elle est donc déclarée, dans le catalogue, par la source** — c'est la source
qui sait quelle colonne porte quoi, et elle seule : le même modèle ``titanic``
est alimenté par la base Postgres (``classes.level``) ET par le CSV vendorisé
(``Pclass``). Un schéma de features qui déclarerait la colonne devrait donc les
énumérer toutes : ce serait la table en dur que le projet a retirée en C13,
déplacée d'un cran.

Ce que cette déclaration donne, et que la prose ne donne pas :

1. la consigne SQL devient EXACTE — on nomme les colonnes à sélectionner et
   leurs alias, au lieu d'espérer que le modèle retrouve les bonnes ;
2. la ligne récupérée est rapprochée du schéma **mécaniquement**, par le nom
   déclaré, sans dépendre de ce que l'agent SQL a bien voulu aliaser ;
3. une source qui ne déclare rien est REFUSÉE avec un message qui dit quoi
   écrire et où — là où l'absence de déclaration ne produisait qu'un silence,
   ou une prédiction sur la mauvaise colonne.

Ce n'est pas un relâchement de la garde. Une valeur qu'aucune traduction
déclarée ne couvre est laissée TELLE QUELLE : le schéma la refuse en citant ce
qu'il a lu — ``'3e classe'`` reste une erreur de validation lisible, et une
valeur hors bornes venue de la base est refusée comme celle d'un utilisateur.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from data_analyst_agent.agents.inference.schemas import get_schema


class FeatureDeclaration(BaseModel):
    """Ce qui, dans une source, porte UNE feature du schéma de prédiction.

    ``column`` : la colonne de la source, qualifiée par sa table quand la source
    en compte plusieurs (``classes.level``). ``values`` : la traduction des
    valeurs, pour les sources qui ne représentent pas la feature comme le schéma
    l'attend (``'3e classe'`` -> ``3``).

    Forme courte acceptée : ``pclass: classes.level`` vaut
    ``pclass: {column: classes.level}``. Le cas fréquent — un nom qui diffère,
    une représentation qui ne diffère pas — s'écrit donc sur une ligne.
    """

    model_config = ConfigDict(extra="forbid")

    column: str
    values: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _accepte_la_forme_courte(cls, data: Any) -> Any:
        return {"column": data} if isinstance(data, str) else data

    @property
    def nom_de_colonne(self) -> str:
        """Le nom nu de la colonne : ``classes.level`` -> ``level``.

        C'est sous ce nom-là qu'une ligne peut revenir si l'agent SQL n'a pas
        posé l'alias demandé — le rapprochement doit le reconnaître aussi.
        """
        return self.column.rsplit(".", 1)[-1]

    def traduire(self, valeur: Any) -> Any:
        """La valeur lue, traduite si la source déclare une correspondance pour elle.

        Une valeur qu'aucune traduction ne couvre est rendue inchangée : c'est au
        schéma de refuser, en citant ce qui a été lu. Substituer ici une valeur
        légale serait exactement la supposition qu'on cherche à interdire.
        """
        if not self.values:
            return valeur
        return self.values.get(str(valeur), valeur)


# Déclarations d'une source, par dataset puis par feature. C'est le type du
# champ `features` d'une source de catalogue.
Declarations = dict[str, dict[str, FeatureDeclaration]]


class CorrespondanceIndisponible(Exception):
    """Aucune correspondance exploitable — le message est destiné à l'utilisateur."""


class Correspondance:
    """La correspondance d'une source vers les features d'un dataset."""

    def __init__(self, source: str, dataset: str, par_feature: dict[str, FeatureDeclaration]):
        self.source = source
        self.dataset = dataset
        self.par_feature = par_feature

    @classmethod
    def par_le_nom(cls, *, source: str, dataset: str) -> Correspondance:
        """L'identité : chaque feature est portée par la colonne de même nom.

        Réservée aux sources qui n'ont AUCUN endroit où déclarer quoi que ce
        soit — un tableau produit par le tour précédent, réinjecté sous
        ``resultat_1``, n'est écrit dans aucun YAML. Ses colonnes sont celles que
        la requête précédente a nommées : les rapprocher par leur nom n'invente
        rien, et une feature qu'aucune colonne ne porte reste absente du payload,
        donc réclamée par le schéma sous son nom.

        Ce n'est pas la porte de sortie des sources du catalogue : celles-là ont
        un fichier où écrire, et ``declaree`` les y renvoie.
        """
        par_feature = {
            feature: FeatureDeclaration(column=feature)
            for feature in get_schema(dataset).model_fields
        }
        return cls(source, dataset, par_feature)

    @classmethod
    def declaree(
        cls, *, source: str, dataset: str, declarations: Declarations | None
    ) -> Correspondance:
        """La correspondance de ``source`` vers ``dataset``, ou un refus lisible.

        Exigée COMPLÈTE : une feature non déclarée serait une feature devinée,
        et c'est le défaut qu'on retire. Le message nomme la source, le modèle
        et ce qui manque — il doit suffire à écrire la déclaration sans lire ce
        fichier.
        """
        attendues = list(get_schema(dataset).model_fields)
        par_feature = (declarations or {}).get(dataset) or {}
        if not par_feature:
            raise CorrespondanceIndisponible(
                f"la source {source!r} ne déclare pas quelles colonnes alimentent le "
                f"modèle {dataset!r}. Ajoutez-lui dans le catalogue un bloc "
                f"`features: {{{dataset}: ...}}` avec une entrée par feature attendue "
                f"({', '.join(attendues)}) — sur le modèle `{attendues[0]}: "
                "<table>.<colonne>`. Sans lui, la colonne qui porte chaque feature "
                "serait devinée."
            )
        inconnues = sorted(set(par_feature) - set(attendues))
        if inconnues:
            raise CorrespondanceIndisponible(
                f"la source {source!r} déclare pour le modèle {dataset!r} des features "
                f"qu'il n'attend pas : {', '.join(inconnues)} — attendues : "
                f"{', '.join(attendues)}."
            )
        manquantes = [feature for feature in attendues if feature not in par_feature]
        if manquantes:
            raise CorrespondanceIndisponible(
                f"la source {source!r} déclare une correspondance incomplète vers le "
                f"modèle {dataset!r} : aucune colonne déclarée pour "
                f"{', '.join(manquantes)}. Complétez le bloc `features.{dataset}` du "
                "catalogue : une feature non déclarée serait une feature devinée."
            )
        # Relu ici plutôt que supposé déjà typé : la déclaration peut arriver
        # d'un catalogue YAML (déjà validé par pydantic) comme d'un appelant qui
        # la construit en clair. `model_validate` accepte les deux, et la forme
        # courte avec.
        return cls(
            source,
            dataset,
            {
                feature: FeatureDeclaration.model_validate(brut)
                for feature, brut in par_feature.items()
            },
        )

    def consigne_sql(self) -> str:
        """Ce qu'on ajoute à la question posée à l'agent SQL : les colonnes, nommées.

        Nommer les colonnes DE LA SOURCE, et pas seulement les features
        attendues, est la moitié qui manquait : « renvoie une colonne `pclass` »
        laisse choisir entre ``class_id``, ``level`` et ``label``, et les trois
        ont été observées. « sélectionne ``classes.level AS pclass`` » ne laisse
        rien à choisir.

        La dernière phrase n'est pas une politesse : la consigne ne porte QUE sur
        la liste des colonnes. Sans elle, mesuré sous Ollama, une consigne aussi
        détaillée recouvrait la demande elle-même — « les cinq premiers
        passagers » repartait sans ``LIMIT``, et ramenait toute la table.
        """
        lignes = "\n".join(
            f"  {decl.column} AS {feature}" for feature, decl in self.par_feature.items()
        )
        return (
            "\nRenvoie une ligne par individu. Les colonnes attendues sont portées "
            "par des colonnes précises de la source : sélectionne EXACTEMENT "
            f"celles-ci, sous ces alias, en ajoutant les jointures nécessaires :\n"
            f"{lignes}\n"
            "Ajoute si disponible une colonne d'identification (id, nom). Cette "
            "consigne ne porte que sur les COLONNES : le filtre et le nombre de "
            "lignes restent ceux de la demande ci-dessus."
        )

    def payload(self, columns: list[str], row: list) -> dict:
        """La ligne récupérée, rapprochée des features déclarées.

        Une feature est reconnue sous son propre nom (l'alias demandé) ou sous
        le nom nu de la colonne déclarée — l'agent SQL peut avoir omis l'alias.
        Insensible à la casse : les sources fichier gardent souvent des en-têtes
        capitalisés (``Pclass``, ``Sex``).

        Une colonne absente de la ligne n'est pas inventée : la feature est
        simplement absente du payload, et le schéma la réclamera par son nom.
        """
        par_nom = {str(column).lower(): valeur for column, valeur in zip(columns, row, strict=True)}
        payload = {}
        for feature, decl in self.par_feature.items():
            for cle in (feature.lower(), decl.nom_de_colonne.lower()):
                if cle in par_nom:
                    payload[feature] = decl.traduire(par_nom[cle])
                    break
        return payload


__all__ = [
    "Correspondance",
    "CorrespondanceIndisponible",
    "Declarations",
    "FeatureDeclaration",
]
