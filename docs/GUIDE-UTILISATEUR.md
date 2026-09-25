# Guide utilisateur

Ce guide décrit l'utilisation de data-analyst-agent depuis la page de chat, sans connaissance du code ni du SQL.

| Section | Contenu |
|---|---|
| [1. Se connecter](#1-se-connecter) | Accès et comptes |
| [2. Gérer ses conversations](#2-gérer-ses-conversations) | Ouvrir, dupliquer, supprimer |
| [3. Choisir une source](#3-choisir-une-source) | Désigner les données de travail |
| [4. Poser une question](#4-poser-une-question) | Les types de questions et ce que l'agent en fait |
| [5. Poursuivre une conversation](#5-poursuivre-une-conversation) | Reprendre un résultat, le modifier |
| [6. Lire les avertissements](#6-lire-les-avertissements) | Bascule de source, données tronquées, calcul suspect |
| [7. Limites](#7-limites) | Ce que l'agent ne fait pas |

Les exemples portent sur le catalogue livré : les Cycles du Ponant, fabricant de vélos.
Quelques repères pour vérifier une réponse de tête :

| Repère | Valeur |
|---|---|
| Commandes reçues en 2025 | 180 |
| Chiffre d'affaires 2025 | 1 496 743,00 € |
| Produits au catalogue | 12, dont 8 fabriqués |
| Entrepôts | 3 |

Les mêmes questions, en captures d'écran : [DEMONSTRATION.md](DEMONSTRATION.md).

## 1. Se connecter

- L'adresse du service commence par `https://`. Elle est fournie par l'exploitant.
- Il n'existe pas d'inscription. Les comptes sont créés par l'exploitant ([INSTALLATION.md](INSTALLATION.md#9-créer-le-premier-compte)).
- L'identifiant ne tient pas compte de la casse.
- Chaque compte ne voit que ses propres conversations.

## 2. Gérer ses conversations

- La colonne de gauche liste les conversations, de la plus récente à la plus ancienne.
- Chaque conversation peut être rouverte, dupliquée ou supprimée.
- **« + Nouvelle conversation »** ouvre une conversation vide.

## 3. Choisir une source

La page n'a pas de menu de sources. La source se désigne dans la conversation.

| Message | Effet |
|---|---|
| « Sur quelles sources pouvons-nous travailler ? » | L'agent liste les sources disponibles. |
| « J'aimerais travailler sur les ventes. » | La source `ventes` est liée à la conversation. L'agent décrit son contenu. |
| « Tu peux me la décrire ? » | L'agent détaille les tables, les colonnes et leurs liens. |
| « Passons sur production. » | La conversation change de source. L'agent annonce le changement. |

- Une source liée le reste pour toute la conversation, y compris après réouverture.
- Un catalogue à une seule source n'a pas à être désigné.
- Un message qui nomme une source et pose une question fait les deux.

## 4. Poser une question

| Type | Exemple | Ce que fait l'agent |
|---|---|---|
| Comptage | « Combien de commandes avons-nous reçues en 2025 ? » | SQL sur la source. Réponse : 180. |
| Somme | « Quel chiffre d'affaires avons-nous réalisé en 2025 ? » | SQL, en appliquant les règles du dictionnaire : les commandes annulées sont exclues. Réponse : 1 496 743,00 €. |
| Classement | « Quel est notre meilleur client en chiffre d'affaires ? » | SQL avec tri. Réponse : Vélocité Bordeaux, 170 149,00 €. |
| Jointure | « Quel produit s'est le plus vendu en nombre d'unités ? » | SQL sur plusieurs tables. Réponse : ACC-03, 264 unités. |
| Graphique | « Fais-moi un graphique du chiffre d'affaires 2025 par canal de vente. » | Python dans le bac à sable. La figure et ses chiffres s'affichent. |
| Sens d'une donnée | « Que signifie le statut ANN ? » | Lecture du dictionnaire de la source. Réponse : annulée avant expédition. |
| Croisement | « Est-ce qu'on vend plus que ce qu'on produit ? » | Deux sources montées ensemble. Réponse : 4 413 fabriqués, 1 828 vendus. |
| Prédiction | « Un homme de 30 ans en 3e classe… aurait-il survécu ? » | Appel du modèle déclaré. Réponse : n'a pas survécu, 91,1 %. |

- Un attribut manquant pour une prédiction est réclamé, jamais deviné.
- Les réponses citent la règle du dictionnaire qu'elles appliquent.
- Les questions croisées sont les moins fiables : une réponse importante mérite une vérification.

## 5. Poursuivre une conversation

- **Le tour précédent** sert de contexte : « Et pour les femmes ? » s'applique à la question d'avant.
- **Les résultats produits** restent disponibles pour toute la conversation. Chacun porte un nom (`resultat_1`, `graphique_1`).
- Un résultat se reprend par son nom ou sa description : « Refais-le en ne gardant que les canaux magasin et en ligne. »
- Un graphique repris est recalculé.
- « Qu'avons-nous produit depuis le début de cette conversation ? » liste les résultats.
- Aucune information ne passe d'une conversation à une autre.

## 6. Lire les avertissements

Certaines réponses se terminent par un avertissement. Il fait partie de la réponse.

| Avertissement | Signification |
|---|---|
| « Je passe sur la source `stocks` — on travaillait sur `ventes`. » | La conversation a changé de source. |
| « Données tronquées : resultat_1 coupée(s) à 200 lignes » | Le calcul porte sur une partie des données seulement. |
| « Avertissement sur ce calcul : … le chiffre peut être surévalué. » | Le contrôle des chiffres a détecté une somme suspecte, non corrigée. |

Un chiffre accompagné d'un avertissement ne doit pas être utilisé sans vérification.

## 7. Limites

- Aucun téléchargement de tableau ou de graphique.
- Aucun ajout de source depuis l'interface : voir [AJOUTER-UNE-SOURCE.md](AJOUTER-UNE-SOURCE.md).
- Une grandeur absente de la source peut être remplacée par une voisine sans avertissement (par exemple, la ville à la place de la région).
- Une question qui demande chaque produit peut recevoir un extrait.
- Une conversation liée à une source ne répond pas sur une autre source tant que celle-ci n'est pas désignée.

Liste complète : [LIVRAISON.md](LIVRAISON.md#3-les-limites-connues).
