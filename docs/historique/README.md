# Les journaux de chantier

Un journal de chantier n'est pas une référence.
Il ne dit pas ce que le produit **est** : il dit ce qu'on a mesuré, ce qu'on a
décidé, et pourquoi.
On ne le lit pas pour se servir du produit.
On le lit quand on veut savoir **pourquoi c'est comme ça**, avant de le changer.

Aucun n'a été effacé : ce sont eux qui portent la preuve des choix.

Pour se servir du produit, l'entrée est [LIVRAISON.md](../LIVRAISON.md).

---

## Ici

| Document | La question à laquelle il répond |
|---|---|
| [concurrence.md](concurrence.md) | que tient le produit quand plusieurs personnes parlent en même temps ? |
| [spike-vanna.md](spike-vanna.md) | fallait-il prendre une brique de texte-vers-SQL du commerce plutôt que le socle maison ? |

## Restés dans `docs/`, parce que le code les cite

Un document nommé dans une docstring, un commentaire ou un test est lu **par là**.
Le déplacer changerait ce qu'un lecteur a sous les yeux — et, pour une docstring
d'outil, ce que le modèle reçoit.
Ils sont donc listés ici et rangés là-bas.

| Document | La question à laquelle il répond |
|---|---|
| [CADRAGE.md](../CADRAGE.md) | quelles contraintes et quelles décisions de départ ont donné ce produit-là ? |
| [AUDIT-2026-09.md](../AUDIT-2026-09.md) | où en était le produit le 3 septembre 2026, et dans quel ordre fallait-il reprendre ? |
| [surface-conversationnelle.md](../surface-conversationnelle.md) | que sait-il répondre **sur lui-même**, formulation par formulation, et ce que chacune coûte ? |
| [sources-de-demonstration.md](../sources-de-demonstration.md) | quel catalogue a **durci** le socle, et qu'a-t-il fait apparaître ? |
| [sources-metier.md](../sources-metier.md) | quel catalogue **montre** le produit, avec quels chiffres vérifiables de tête ? |
| [croisement-de-sources.md](../croisement-de-sources.md) | comment une question qui porte sur deux sources est servie, et où elle échoue encore ? |
| [memoire-de-conversation.md](../memoire-de-conversation.md) | que retient un fil d'un tour à l'autre, et qui s'en sert ? |
| [releve-de-la-memoire-de-conversation.md](../releve-de-la-memoire-de-conversation.md) | le relevé brut de la campagne précédente — 81 tours, six fils, écrit par son script. |
| [releve-des-parcours.md](../releve-des-parcours.md) | le relevé brut dont [parcours-de-l-agent.md](../parcours-de-l-agent.md) est tiré, écrit par son script. |
| [releve-de-livraison.md](../releve-de-livraison.md) | quels chiffres le produit rendait **le jour de la livraison**, et lesquels étaient rouges ? |

**Deux d'entre eux ne se corrigent pas à la main** :
`releve-de-la-memoire-de-conversation.md` et `releve-des-parcours.md` sont
écrits par leur script.
Le second est en plus **figé par une empreinte SHA-256** déclarée dans
`parcours-de-l-agent.md` et vérifiée par
`tests/unit/docs/test_parcours_de_l_agent.py` : y ajouter fût-ce une ligne fait
rougir la suite.
C'est pourquoi il est le seul document de cette liste à ne porter aucune ligne
de tête.

## Ce qui n'est pas un journal

| Document | Ce qu'il est |
|---|---|
| [axes-amelioration.md](../axes-amelioration.md) | vivant : la dette ouverte, ancrée `fichier:ligne`, pour qui va toucher au code. |
| [parcours-de-l-agent.md](../parcours-de-l-agent.md) | vivant : comment l'agent répond, tour par tour, tenu par un test contre son relevé. |
