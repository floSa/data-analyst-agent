# Livraison de la version 1

Ce document décrit la version 1 de data-analyst-agent : son périmètre, sa qualité mesurée, ses limites connues et les étapes suivantes.

| Section | Contenu |
|---|---|
| [1. Périmètre](#1-périmètre) | Ce que fait l'application |
| [2. Qualité mesurée](#2-qualité-mesurée) | Les scores de la version livrée |
| [3. Les limites connues](#3-les-limites-connues) | Ce qui ne fonctionne pas, ou pas toujours |
| [4. Les prochaines étapes](#4-les-prochaines-étapes) | Ce qui est à faire, par ordre d'intérêt pour l'utilisateur |
| [5. Documentation associée](#5-documentation-associée) | Où lire la suite |

## 1. Périmètre

data-analyst-agent est un agent conversationnel d'analyse de données, installé sur site.

- **Sources** : fichiers CSV et Excel, bases Postgres et DuckDB, déclarées dans un catalogue.
- **Interrogation** : l'agent écrit le SQL, jointures comprises, et l'exécute en lecture seule.
- **Analyse** : l'agent écrit du Python (statistiques, graphiques) et l'exécute dans un bac à sable Docker sans réseau.
- **Prédiction** : l'agent appelle un modèle de machine learning déclaré, après avoir validé les attributs.
- **Croisement** : l'agent monte deux sources ensemble et les relie par une clé commune.
- **Contrôle des chiffres** : une somme multipliée par une jointure, ou privée du filtre que la source impose, est renvoyée au modèle pour correction, ou signalée dans la réponse.
- **Questions sur le système** : sources disponibles, capacités, contenu d'une source, ce que la conversation a produit.
- **Reprise** : un tableau ou un graphique produit peut être repris et modifié.
- **Comptes** : chaque compte est cloisonné. Hormis `/health`, aucune route n'est accessible sans session.
- **Modèle de langage** : tout LLM servi sur site derrière une API compatible OpenAI avec appel d'outils. Aucune donnée ne sort de la machine.

La démonstration de ces capacités : [DEMONSTRATION.md](DEMONSTRATION.md).

## 2. Qualité mesurée

Mesures du 25 septembre 2026 sur la version livrée, contre le moteur en service.
Le détail, question par question : [releve-de-livraison.md](releve-de-livraison.md).

| Campagne | Catalogue | Score |
|---|---|---|
| Parcours de démonstration, trois formulations | démonstration | 48/48 |
| Classement sans mot-clé | démonstration | 10/10 |
| Ouverture de source | démonstration | 16/17 |
| Questions métier | métier | 12/12 |
| Croisement de sources | métier | 13/17 |
| Sources nommées dans la question | métier | 60/60 |
| Choix de source | par défaut | 6/6 |
| Ambiguïté entre deux sources | catalogues d'essai | 2/2 |
| Suite de tests | — | 1 623 tests, couverture 98,94 % |

Les campagnes se lancent une par une : deux mesures simultanées sur le même moteur ne sont pas comparables.
Les commandes de chaque campagne : [scripts/README.md](../scripts/README.md).

## 3. Les limites connues

### Réponses fausses et plausibles

- **Grandeur substituée.** Une grandeur absente de la source est remplacée par une voisine, sans avertissement. Exemple : des commandes demandées « par région » sont rendues par ville, la source n'ayant pas de région.
- **Extrait au lieu du tout.** Une question qui demande chaque produit peut recevoir un « Top 5 ».
- **Trois sources à la fois.** Sur une question qui croise trois sources, le filtre des commandes annulées peut être perdu.
- **Somme hors d'un `WITH`.** Le contrôle du SQL ne juge pas une somme écrite dans la requête qui entoure un `WITH`.

### Données non consultées

- **Questions croisées en conversation neuve.** Certaines formulations reçoivent l'inventaire des sources au lieu d'une réponse. Sur une conversation déjà liée à une source, les mêmes questions aboutissent.
- **Choix entre deux sources.** « titanic ou iris ? » ne reçoit pas toujours la même réponse.
- **Source non liée.** Une conversation liée à `ventes` ne répond pas à une question qui ne porte que sur `production`. Il faut changer de source en le disant.

### Mémoire

- Le contexte conversationnel retient le tour précédent. Les tableaux et graphiques produits restent disponibles pour toute la conversation.
- Aucune mémoire entre conversations.
- Le contexte trop long est tronqué, du plus ancien au plus récent ; il n'est pas résumé.

### Graphiques

- Un graphique aboutit souvent au dernier des trois essais autorisés.
- La bibliothèque `tabulate` est absente du bac à sable : `DataFrame.to_markdown()` échoue.

### Non traité

- Téléchargement d'un tableau ou d'un graphique.
- Ajout d'une source depuis l'interface.
- Recherche par le sens (aucun index vectoriel).

### Exploitation

- Le redémarrage complet de la machine n'a pas été testé.
- L'accès depuis un poste distant par navigateur n'a pas été testé.
- La migration des conversations antérieures au cloisonnement par compte n'a été testée que sur copie.
- Toutes les sources livrées sont synthétiques.
- Aucun fichier `LICENSE`.

La dette technique, ligne de code par ligne de code : [axes-amelioration.md](axes-amelioration.md).

## 4. Les prochaines étapes

1. **Enrichir le périmètre d'une conversation liée.**
   Une conversation liée à `ventes` doit pouvoir répondre sur `production` quand la question le demande.
   Une première version a été retirée avant la livraison : elle croisait les sources même quand la question n'en demandait qu'une, et le croisement, tronqué à 10 000 lignes, faussait les sommes du catalogue de démonstration.
   Conditions pour la reprendre : ne croiser que si la question porte sur les deux sources, et mesurer le parcours de démonstration avant de conclure.
2. **Supprimer les réponses fausses et plausibles** listées au §3 : grandeur absente signalée au lieu d'être substituée, toutes les lignes demandées rendues, filtre tenu sur trois sources, somme hors d'un `WITH` contrôlée.
3. **Télécharger un tableau (CSV) et un graphique (PNG)** depuis la page de chat.
4. **Configurer sans réinstaller** : ajout d'une source depuis l'interface, prompts propres à chaque déploiement.
5. **Prolonger la mémoire** : contexte au-delà du tour précédent, résumé plutôt que troncature.
6. **Compléter l'exploitation** : redémarrage de la machine, accès depuis un poste, migration des conversations, fichier `LICENSE`, `tabulate` dans le bac à sable.
7. **Tenir une charge plus forte**, si le nombre d'utilisateurs dépasse la vingtaine : voir [axes-amelioration.md](axes-amelioration.md#récapitulatif-priorisé).

## 5. Documentation associée

- [Démonstration](DEMONSTRATION.md)
- [Guide utilisateur](GUIDE-UTILISATEUR.md)
- [Ajouter une source](AJOUTER-UNE-SOURCE.md)
- [Installation](INSTALLATION.md)
- [Exploitation](EXPLOITATION.md)
- [Architecture](ARCHITECTURE.md)
- [Relevé de livraison](releve-de-livraison.md)
- [Historique du projet](historique/README.md)
