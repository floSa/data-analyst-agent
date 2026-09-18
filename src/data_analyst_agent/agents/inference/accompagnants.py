"""« Sans famille à bord » : une absence qui fixe des valeurs, et qui se lit.

Le défaut que ce module corrige est mesuré, et il fermait le fil. À

    « prédis la survie d'une passagère de 1re classe de 28 ans, tarif 80
      livres, embarquée à Southampton, sans famille à bord »

le planificateur rendait, 3 tirages sur 3 et à l'identique :

    features = {'age': '28', 'embarked': 'S', 'fare': '80', 'parch': '0',
                'pclass': '1', 'sex': 'female'}

`parch` y est — le modèle a bien lu l'absence de famille, et il l'a portée sur
UN des deux compteurs. `sibsp` n'y est pas. La prédiction ressortait donc en
`invalid` sur « sibsp (Frères/sœurs + conjoint à bord) : valeur manquante »,
alors que la phrase le disait. Et comme le tour suivant n'a plus la phrase
d'origine sous la main, aucune réponse ne pouvait en sortir : répondre « c'est
une femme » rendait la MÊME relance, au caractère près — non parce que l'acquis
se perdait (il ne se perd pas : la fusion marche, les fils `b` et `e` le
montrent 3 tirages sur 3) mais parce que ce qui manquait n'avait jamais été lu.

**La cause est l'extraction, pas la fusion.** C'est ce qui décide de l'endroit
où on répare : ni dans le prompt du planificateur — cinq formulations ont déjà
été écrites puis retirées sur ce projet, chacune coûtait une question de la
surface conversationnelle — ni dans la fusion, qui fait déjà son travail.

**Ce qu'on ajoute est une propriété, en deux moitiés qui ne se devinent pas
l'une l'autre :**

1. le SCHÉMA déclare quels champs comptent des personnes qui accompagnent le
   sujet. C'est un fait sur le jeu de données, écrit là où les autres faits sur
   le jeu de données sont écrits (CADRAGE §7-③) — pas une phrase adressée à un
   modèle ;
2. le MESSAGE porte, ou non, une construction fermée qui dit que le sujet
   voyage sans personne. « Sans famille à bord », « elle voyageait seule ».

Les deux réunies, les compteurs d'accompagnants que le message n'a pas
autrement renseignés valent zéro. Se tromper ne peut faire perdre ni un tour ni
une réponse : au pire deux valeurs de trop sur un message qui dit pourtant
qu'il n'y a personne, et les valeurs que l'utilisateur a données priment
toujours.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from data_analyst_agent.agents.inference.schemas.marques import ACCOMPAGNANTS
from data_analyst_agent.orchestrator.introspection import replie


def champs_daccompagnants(schema: type[BaseModel]) -> tuple[str, ...]:
    """Les champs que ce schéma déclare comme des compteurs d'accompagnants.

    Vide pour un schéma qui n'en déclare aucun — `iris` mesure des pétales,
    `california_housing` des pièces par logement — et la règle ne s'y applique
    alors jamais. Un schéma sans déclaration est un schéma que ce module ne
    touche pas : il n'y a pas de repli, pas d'heuristique sur les noms de
    champs, et un `sibsp` ajouté ailleurs sans la marque restera ignoré.
    """
    marques = []
    for nom, info in schema.model_fields.items():
        extra = info.json_schema_extra
        if isinstance(extra, dict) and extra.get(ACCOMPAGNANTS) is True:
            marques.append(nom)
    return tuple(marques)


# La construction est FERMÉE, et courte à dessein. Elle ne cherche pas à
# reconnaître « ce que l'utilisateur veut » — c'est ce qui plafonnait à 3
# formulations sur 10 au §9 de `docs/surface-conversationnelle.md` — mais une
# seule chose, dite d'une des quelques façons dont on la dit : personne
# n'accompagne le sujet.
#
# Le texte est REPLIÉ avant (minuscules, sans accents, ponctuation en blancs) :
# « à bord » s'y lit « a bord », « seule » reste « seule ».
#
# Ce qui n'y est PAS, et pourquoi : « sans enfant » et « sans conjoint » ne
# disent rien de l'autre compteur — ils sont une valeur pour UN champ, et c'est
# au modèle de les extraire, pas à cette règle de décider pour les deux.
_PERSONNE_A_BORD = re.compile(
    r"\b(?:"
    r"sans (?:aucune? )?(?:famille|proche|proches|accompagnant|accompagnants|"
    r"accompagnateur|accompagnateurs|parent ni enfant ni conjoint)"
    r"|(?:seul|seule) a bord"
    r"|voyage(?:ait|ant|nt)? (?:seul|seule)"
    r"|(?:sans|aucun|aucune) (?:personne|proche) (?:a bord|l accompagnant)"
    r"|non accompagnee?"
    r")\b"
)


def absence_daccompagnants(question: str) -> str:
    """La construction par laquelle ce message dit « personne » — ``""`` sinon.

    Rend la construction trouvée plutôt qu'un booléen, comme
    ``designation_dun_artefact_passe`` : une décision déterministe doit pouvoir
    dire sur quoi elle s'est fondée, et c'est elle qui part dans la trace.
    """
    trouve = _PERSONNE_A_BORD.search(replie(question))
    return trouve.group(0) if trouve else ""


def sans_la_clause_dabsence(question: str) -> str:
    """Le message privé du segment qui dit « personne » — inchangé s'il n'y est pas.

    **Pourquoi retirer un morceau du message.** Mesuré sur le planificateur, 5
    tirages par variante, même phrase à une clause près :

        sans la clause  -> ['age', 'embarked', 'fare', 'pclass', 'sex']   5/5
        avec la clause  -> ['embarked', 'fare', 'pclass', 'sex', 'sibsp'] 4/5

    La clause ne se contente pas de ne remplir qu'UN des deux compteurs : elle
    COÛTE au modèle un attribut qu'il extrayait sans faillir. Il échange `age`
    contre `sibsp`. Le tour ressort donc `invalid` de toute façon — sur `age`
    au lieu de `sibsp` — et le fil se referme exactement comme avant.

    Or ce segment-là, on sait le lire sans modèle : c'est tout l'objet de
    ``absence_daccompagnants``. Le lui laisser porter, c'est lui faire payer
    deux fois la même information. On le retire donc de la SECONDE lecture, et
    d'elle seule (cf. ``Orchestrator._relire_sans_la_clause_dabsence``) : la
    première lecture, elle, voit le message entier, parce que c'est elle qui
    décide de la capacité et que ce segment peut compter pour une autre — «
    combien de passagers sans famille à bord ? » est une requête, et sa clause
    est son filtre.

    Le découpage se fait sur les SÉPARATEURS de la phrase — virgules,
    points-virgules, tirets — et un segment n'est retiré que s'il est
    entièrement la construction. Une clause enchâssée dans une proposition plus
    longue n'est pas découpable sans réécrire la phrase, et réécrire la phrase
    de quelqu'un pour la lui reposer est précisément ce qu'on ne fait pas : dans
    ce cas, rien n'est retiré et il n'y a pas de seconde lecture.
    """
    segments = re.split(r"([,;]|\s+—\s+)", question)
    garde = [
        segment
        for indice, segment in enumerate(segments)
        if indice % 2 == 1 or not _est_toute_la_clause(segment)
    ]
    allege = "".join(garde)
    if allege == question:
        return question
    # les séparateurs orphelins que le retrait laisse derrière lui
    return re.sub(r"\s*([,;])\s*(?=[,;]|$)", "", allege).strip().strip(",; ")


def _est_toute_la_clause(segment: str) -> bool:
    """Ce segment n'est QUE la construction — pas une proposition qui la contient."""
    plat = replie(segment)
    trouve = _PERSONNE_A_BORD.search(plat)
    if trouve is None:
        return False
    reste = (plat[: trouve.start()] + plat[trouve.end() :]).strip()
    # « sans famille à bord » laisse « a bord » ; « et sans famille » laisse « et »
    return reste in ("", "a bord", "et", "et a bord", "elle", "il")
