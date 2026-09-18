"""Les marques qu'un champ de schéma peut porter, et rien d'autre.

Module FEUILLE, sans le moindre import : c'est ce qui lui permet d'être lu à la
fois par les schémas — qui posent la marque — et par le code qui la relève,
lequel dépend de l'orchestrateur pour replier du texte. Les réunir ailleurs
refermait un cycle d'imports, mesuré à la première tentative.
"""

# Le champ compte des personnes qui ACCOMPAGNENT le sujet. Deux champs de
# `titanic` la portent : `sibsp` et `parch`. C'est ce qui permet de lire « sans
# famille à bord » comme une valeur pour les deux
# (cf. `agents/inference/accompagnants`).
ACCOMPAGNANTS = "accompagnants"
