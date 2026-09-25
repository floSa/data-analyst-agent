# Se servir de l'application

Ce guide s'adresse à qui **ouvre la page de chat** et n'a rien installé.
Il ne demande aucune connaissance du code.
Il ne demande pas de savoir écrire du SQL.

Tous les exemples qui suivent ont été **posés au vrai serveur**, et leur réponse
est celle qui a été rendue.
Chacun renvoie au relevé qui le porte.
Ils tournent sur le catalogue en service — les Cycles du Ponant, un fabricant de
vélos — décrit dans [sources-metier.md](sources-metier.md).

Les mêmes scénarios, en captures d'écran du service : [DEMONSTRATION.md](DEMONSTRATION.md).

Quatre chiffres suffisent à vérifier une réponse de tête :

| | |
|---|---|
| commandes de 2025 | **180** |
| chiffre d'affaires 2025 | **1 496 743,00 €** |
| produits au catalogue | **12** — dont **8** fabriqués, les 4 accessoires étant achetés |
| entrepôts | **3** |

---

## 1. Se connecter

L'adresse est celle que vous a donnée la personne qui a installé le service.
Elle commence par `https://`.

Il n'y a **pas d'inscription**.
Un compte s'ouvre à la main, sur la machine, par la personne qui exploite le
service ([INSTALLATION §8](INSTALLATION.md#8-créer-le-premier-compte)).
Si vous n'en avez pas, c'est à elle qu'il faut le demander.

Vous tapez votre identifiant et votre mot de passe.
La casse de l'identifiant ne compte pas : `floSa`, `FLOSA` et `flosa` sont le
même compte.

**Vous ne voyez que vos conversations.**
Personne ne voit les vôtres.
La colonne de gauche liste vos fils, du plus récent au plus ancien.
On y ouvre une conversation, on la duplique, on la supprime.
Le bouton **« + Nouvelle conversation »** en ouvre une vide.

---

## 2. Choisir sa source, en parlant

**Il n'y a pas de menu de sources sur la page**, et c'est délibéré : un menu a
été monté puis retiré, parce qu'il posait à l'écran une question que le dialogue
pose mieux (`api/templates/chat.html`).

On demande, et on nomme.

> **« Bonjour, quelles sources de données as-tu ? »**
>
> « J'ai accès aux sources suivantes : `ventes`, `production`, `stocks`, `iris`,
> et `titanic`. »

> **« On va travailler sur ventes. »**
>
> « Entendu : on travaille sur **ventes** (postgres) […] 4 tables, 673 lignes
> […] période du 2025-01-02 au 2025-12-31. Je garde cette source. »

*(Relevé : [sources-metier.md](sources-metier.md), « Une conversation complète,
rejouée en HTTPS », tours 1 et 2.)*

Trois choses à savoir :

- **la source reste liée au fil.** Les questions suivantes n'ont plus à la
  nommer. Vous rouvrez la conversation demain : elle est toujours là ;
- **s'il n'y a qu'une source, elle est annoncée** au lieu d'être demandée ;
- **un message qui nomme une source ET pose une question** est traité comme la
  question qu'il est : il lie la source en chemin et répond.

---

## 3. Les questions qui marchent

Une par famille, avec sa réponse réelle.
Les douze questions de démonstration et leurs oracles sont dans
[sources-metier.md](sources-metier.md) ; elles sont à **12/12** au relevé de
livraison.

### Compter

> **« Combien de commandes avons-nous reçues en 2025 ? »**
>
> **180** — et la réponse dit elle-même qu'aucun filtre de statut n'a été posé.

Un comptage compte tout.
Les commandes annulées sont des commandes reçues.

*(Relevé : [releve-de-livraison.md](releve-de-livraison.md), vérification de
bout en bout du 25 septembre 2026, question 2.)*

### Sommer

> **« Quel chiffre d'affaires avons-nous réalisé en 2025 ? »**
>
> « **1 496 743,00 €** […] dont le statut n'est pas 'ANN', conformément à la
> règle stipulant que les sommes en euros doivent exclure les commandes
> annulées. »

Une somme d'euros, elle, **écarte** les annulées.
Ce n'est pas une intuition de l'agent : c'est écrit dans le dictionnaire de la
source, et la réponse cite la règle qu'elle applique.
Sans le filtre, le total serait 1 636 093 € — et il aurait l'air tout aussi juste.

*(Relevé : [sources-metier.md](sources-metier.md), tour 3 de la conversation
HTTPS ; question 2 des douze.)*

### Classer

> **« Quel est notre meilleur client en chiffre d'affaires ? »**
>
> Vélocité Bordeaux, **170 149,00 €**.

> **« Quelle machine a connu le plus d'arrêts en 2025 ? »** *(source `production`)*
>
> **M-009**, 16 arrêts.

*(Relevé : [sources-metier.md](sources-metier.md), questions 4 et 7 des douze.)*

### Faire un graphique

> **« Fais-moi un graphique du chiffre d'affaires 2025 par canal de vente. »**
>
> « […] "magasin" **862 229,0 EUR**, "en ligne" **331 499,0**, "grossiste"
> **303 015,0**. » — plus l'image.

La figure s'affiche dans le fil.
Le texte donne les chiffres : on peut les vérifier sans lire le graphique.

*(Relevé : [sources-metier.md](sources-metier.md), tour 4 de la conversation
HTTPS ; question 5 des douze.)*

### Croiser deux sources

> **« Pour le VEL-01, combien on en a fabriqué et combien on en a vendu ? »**
>
> **689** fabriqués *(source `production`)*, **123** vendus *(source `ventes`,
> annulées exclues)*.

C'est la famille la **moins sûre** des sept.
Au relevé de livraison, les questions qui croisent deux sources sont à
**13 sur 17**, et le document dit lesquelles tombent et pourquoi.
Quand la réponse compte, vérifiez-la.

*(Relevé : [releve-de-livraison.md](releve-de-livraison.md), `vel01-fabrique-vendu` ;
détail dans [croisement-de-sources.md](croisement-de-sources.md).)*

### Demander ce qu'une colonne veut dire

> **« Que signifie le statut ANN d'une commande ? »**
>
> Une commande **annulée** avant expédition.

L'agent ne le devine pas : il le lit dans le dictionnaire de la source, et il
dit qu'il le tient de là.
Une source sans dictionnaire ne peut répondre que par son schéma.

*(Relevé : [croisement-de-sources.md](croisement-de-sources.md), témoin
« une question de sens reste une question de sens », 3/3.)*

### Demander une prédiction

> **« Un homme de 30 ans en 3e classe… aurait-il survécu ? »** *(source `titanic`)*
>
> « Prédiction (titanic) : n'a pas survécu (probabilité **91,1 %**). »

La prédiction appelle un modèle déclaré, pas le modèle de langage.
**S'il manque un attribut, l'agent le réclame au lieu de le deviner** :
« je ne peux pas encore lancer la prédiction — parch : valeur manquante ».
C'est le comportement voulu.

*(Relevé : [sources-metier.md](sources-metier.md), question 12 des douze.)*

---

## 4. Continuer une conversation

Deux mémoires répondent, et elles ne portent pas à la même distance.

**Le tour précédent** — pour un ajustement immédiat.

> « extrais-moi les passagères qui ont survécu »
> puis « **fais-moi un graphique de leur âge** »

Le second tour repart du tableau du premier.
Il ne réinterroge pas la base.

*Deux tours en arrière, c'est oublié.*
« Et pour les femmes ? » marche juste après.
La même phrase trois tours plus loin ne marche pas.

**Ce que la conversation a produit** — aussi loin que le fil.

Chaque tableau, chaque figure et chaque code reçoit un nom : `resultat_1`,
`graphique_1`, `analyse_1`.
On les redemande par leur nom ou par leur description.

> « **remontre-moi le tableau des survivants** »
> « **reprends le graphe de tout à l'heure et mets les barres en bleu** »

Rejouer une figure la **refait** : le code est réexécuté, l'image est neuve.

On peut aussi demander l'inventaire du fil :

> **« Qu'est-ce que tu as en mémoire dans cette conversation ? »**
>
> « J'ai en mémoire dans cette conversation les éléments suivants :
> **Sources TRANSFORMÉES** : `resultat_1` — tableau de 10 ligne(s) […] produit
> par : « les 10 interventions les plus longues » ; **Code produit dans CETTE
> conversation** : `graphique_1` […] »

*(Relevé : [memoire-de-conversation.md](memoire-de-conversation.md), fils A et
B.3, 3/3 chacun. Ce sont les seuls exemples de ce guide qui ne viennent pas du
catalogue en service : cette campagne a été menée sur les catalogues `titanic`
et `demonstration`. Le mécanisme, lui, est le même partout.)*

**Ce qui ne passe pas d'un fil à l'autre :** rien ne passe.
Chaque conversation repart de zéro.

---

## 5. Lire ce que l'agent vous dit en plus de sa réponse

Deux avis reviennent souvent.
Aucun des deux n'est une erreur : ce sont des réponses qui disent ce qu'elles ne
couvrent pas.

### La bascule de source, annoncée

> **« Et maintenant, dans stocks, combien d'unités sont sorties des entrepôts en
> 2025 ? »**
>
> « **Je passe sur la source `stocks`** — on travaillait sur `ventes`. […]
> **2 279** […] en sommant la valeur absolue de `quantite` où le `sens` est
> 'SOR'. »

Nommer une autre source **change** la source du fil, et l'agent le dit avant de
répondre.
La phrase compte : « combien de produits ? » n'a pas la même réponse selon la
source — **12** dans `ventes`, **8** dans `production`, et les deux sont justes.

*(Relevé : [sources-metier.md](sources-metier.md), tour 5 de la conversation
HTTPS.)*

### Le contexte coupé, annoncé

> « Le tableau contient 200 lignes. **Données tronquées : resultat_1 coupée(s) à
> 200 lignes** (réglage `DAA_RETRIEVAL_MAX_ROWS`) […] »

Un tableau peut être une **tranche**, pas le tout.
Une conversation longue peut cesser de transmettre ses plus vieux tableaux au
modèle — ils restent enregistrés, et le fil affiché reste complet.
Dans les deux cas, c'est écrit dans la réponse.

**Lisez ces phrases.** Un chiffre calculé sur une tranche est faux tout en ayant
l'air juste, et c'est l'avis qui vous le dit.

*(Relevé : [memoire-de-conversation.md](memoire-de-conversation.md), fil D, 3/3.)*

---

## 6. Ce que l'agent ne sait pas faire

Dit franchement, et mesuré.
La liste complète, avec le document qui porte chaque limite, est dans
**[LIVRAISON.md §3](LIVRAISON.md#3-les-limites-connues)**.

Ce qui se voit le plus vite depuis la page de chat :

- **rien ne se télécharge.** Ni un tableau, ni une figure. Ils s'affichent, ils
  ne s'emportent pas ;
- **on n'ajoute pas une source depuis l'écran.** Une source se déclare dans un
  fichier, sur la machine, puis le service redémarre — voir
  [AJOUTER-UNE-SOURCE.md](AJOUTER-UNE-SOURCE.md) ;
- **une grandeur qui n'existe pas peut être remplacée par une voisine, en
  silence.** Demandé « par région » sur une source qui n'a que des villes,
  l'agent a rendu un graphique **par ville**, sans un mot. Les chiffres étaient
  justes ; ce n'était pas la question ;
- **une question qui demande « chaque produit » peut recevoir un « Top 5 »** ;
- **un fil lié à une source ne répond pas sur une autre**, même quand la question
  la désigne, tant qu'on ne la nomme pas pour basculer ;
- **rien ne se retient d'une conversation à l'autre.**

Et une habitude qui vaut pour tout le reste : **si un chiffre doit engager une
décision, posez la question deux fois, autrement.**
Le produit est mesuré, pas infaillible — les relevés disent exactement où.
