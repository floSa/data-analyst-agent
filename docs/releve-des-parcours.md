# Le relevé des parcours

Ce document est ÉCRIT PAR `scripts/releve_des_parcours.py`. Il n'est pas
rédigé à la main, et toute correction qu'on y apporterait serait perdue à
la prochaine exécution. Il est la trace sur laquelle
`docs/parcours-de-l-agent.md` est établi.

Moteur : `http://localhost:8100/v1` (`google/gemma-4-E4B-it-qat-w4a16-ct`)
Catalogue : `sources/metier/catalogue.yaml`

    uv run python scripts/releve_des_parcours.py --markdown docs/releve-des-parcours.md

## Le fil en un coup d'œil

| # | message | nœuds traversés | appels LLM | artefacts | réponse |
|---|---|---|---|---|---|
| 1 | quelles sont les sources à ta disposition ? | system → synthesize | 2 | — | 149 car. |
| 2 | peux-tu me préciser ventes et production ? | system → synthesize | 2 | — | 695 car. |
| 3 | et plus de détails sur ventes ? | system → synthesize | 2 | — | 2243 car. |
| 4 | parle-moi un peu de stocks et de titanic, en deux mots | system → synthesize | 2 | — | 93 car. |
| 5 | quel est le chiffre d'affaires par revendeur, les 5 premiers ? | system → plan → retrieval → synthesize | 5 | `application/json` | 102 car. |
| 6 | fais-moi un graphique de ça | system → rappel → plan → analysis → synthesize | 5 | `image/png` | 172 car. |
| 7 | prédis la survie d'une passagère de 1re classe de 28 ans, sans famille à bord, billet à 80 livres, embarquée à Cherbourg | system → rappel → plan → inference → synthesize | 3 | — | 105 car. |
| 8 | reprends le tableau précédent et donne-moi les pourcentages | system → rappel → synthesize | 6 | — | 237 car. |

**Total : 27 appels LLM** pour 8 tours, 73 s.

## Tour 1 — « quelles sont les sources à ta disposition ? »

Ce qu'il montre : une question sur le système : aucune base ouverte, aucun SQL écrit.

**2 appel(s) LLM**, 1833 ms, réponse de 149 car..

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees — formulé par le modèle | 1830 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `—` |

Artefacts rendus :

Aucun.

Réponse servie :

> J'ai accès aux sources suivantes : `ventes`, `production`, `stocks`, `iris`, et `titanic`.
>
> Que souhaitez-vous savoir sur une source en particulier ?

## Tour 2 — « peux-tu me préciser ventes et production ? »

Ce qu'il montre : deux sources nommées, servies dans la MÊME réponse.

**2 appel(s) LLM**, 4670 ms, réponse de 695 car..

| nœud | détail | ms |
|---|---|---|
| `system` | sources_de_donnees, sources_de_donnees — formulé par le modèle | 4667 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `sources_de_donnees` | `cible='ventes'` |
| 2 | `sources_de_donnees` | `cible='production'` |

Artefacts rendus :

Aucun.

Réponse servie :

> Voici les détails des sources `ventes` et `production` :
>
> La source `ventes` est une base de données PostgreSQL qui contient le carnet de commandes des Cycles du Ponant. Elle comprend 4 tables, 673 lignes, et couvre la période du 2025-01-02 au 2025-12-31. Elle contient 18 revendeurs, 12 produits au catalogue, 180 commandes et leurs 463 lignes.
>
> La source `production` est une base de données DuckDB qui contient les informations de l'atelier des Cycles du Ponant. Elle comprend 4 tables, 222 lignes, et couvre la période du 2025-01-04 au 2025-12-28. Elle contient 3 ateliers, 9 machines, 140 ordres de fabrication et 70 arrêts machine.
>
> Souhaitez-vous explorer le schéma d'une de ces sources ?

## Tour 3 — « et plus de détails sur ventes ? »

Ce qu'il montre : une seule source nommée : elle se LIE au fil.

**2 appel(s) LLM**, 14968 ms, réponse de 2243 car., source liée après le tour : `ventes`.

| nœud | détail | ms |
|---|---|---|
| `system` | schema_d_une_source — formulé par le modèle | 14965 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `schema_d_une_source` | `cible='ventes'` |

Artefacts rendus :

Aucun.

Réponse servie :

> Je travaille sur la source `ventes`.
>
> La source `ventes` est une base de données PostgreSQL qui contient le carnet de commandes des Cycles du Ponant. Elle comprend 4 tables : `clients`, `commandes`, `lignes_commande`, et `produits`.
>
> Voici le détail de chaque table :
>
> **Table `clients` (6 colonnes) :**
> *   `client_id` (INTEGER) : clé primaire ; obligatoire
> *   `code_client` (TEXT) : obligatoire
> *   `raison_sociale` (TEXT) : obligatoire
> *   `ville` (TEXT) : obligatoire
> *   `canal` (TEXT) : obligatoire ; valeurs : 'en ligne', 'grossiste', 'magasin'
> *   `date_creation` (DATE) : obligatoire
>
> **Table `commandes` (6 colonnes) :**
> *   `commande_id` (INTEGER) : clé primaire ; obligatoire
> *   `code_commande` (TEXT) : obligatoire
> *   `client_id` (INTEGER) : obligatoire ; clé étrangère vers `clients(client_id)`
> *   `date_commande` (DATE) : obligatoire
> *   `statut` (TEXT) : obligatoire ; valeurs : 'ANN', 'EXP', 'LIV'
> *   `montant_total_eur` (NUMERIC(10, 2)) : obligatoire
>
> **Table `lignes_commande` (6 colonnes) :**
> *   `ligne_id` (INTEGER) : clé primaire ; obligatoire
> *   `commande_id` (INTEGER) : obligatoire ; clé étrangère vers `commandes(commande_id)`
> *   `produit_id` (INTEGER) : obligatoire […]

## Tour 4 — « parle-moi un peu de stocks et de titanic, en deux mots »

Ce qu'il montre : la ceinture écarte la première formulation, on redemande.

**2 appel(s) LLM**, 606 ms, réponse de 93 car., source liée après le tour : `ventes`.

| nœud | détail | ms |
|---|---|---|
| `system` | plancher_des_sources_nommees — reformulé au second tour (réponse hors sujet) | 603 |
| `synthesize` | système (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

Aucun.

Artefacts rendus :

Aucun.

Réponse servie :

> Stocks : entrepôts, mouvements ; Titanic : survie, âge.
> Que souhaitez-vous analyser ensuite ?

## Tour 5 — « quel est le chiffre d'affaires par revendeur, les 5 premiers ? »

Ce qu'il montre : la première vraie question de données : plan, SQL, synthèse.

**5 appel(s) LLM**, 6429 ms, réponse de 102 car., plan : `query · source=ventes`, source liée après le tour : `ventes`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 110 |
| `plan` | query sur ventes | 1935 |
| `retrieval` | SELECT c.raison_sociale, SUM(o.montant_total_eur) AS chiffre_affaires FROM commandes o JOIN clients c ON o.client_id = c.client_id WHERE o.statut <> 'ANN' GROUP BY c.raison_sociale ORDER BY chiffre_affaires DESC LIMIT 5; | 4374 |
| `synthesize` | résumé déterministe (multi-lignes) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='query', data_question='SELECT revendeur, SUM(montant_vente) AS chiffre_affaires FROM ventes GROUP BY revendeur ORDER BY chiffre_affaires DESC LIMIT 5', reason="La demande est une requête SQL directe pour agréger le chiffre d'affaires par revendeur et ne retourner que les 5 premiers. La source est implicitement 'ventes' car elle est le contexte de conversation.", source='ventes'` |
| 2 | `get_schema` | `—` |
| 3 | `run_sql` | `query="SELECT c.raison_sociale, SUM(o.montant_total_eur) AS chiffre_affaires FROM commandes o JOIN clients c ON o.client_id = c.client_id WHERE o.statut <> 'ANN' GROUP BY c.raison_sociale ORDER BY chiffre_affaires DESC LIMIT 5;"` |

Artefacts rendus :

| type | taille |
|---|---|
| `application/json` | 247 car. |

Réponse servie :

> 5 lignes retournées, classées par `chiffre_affaires` (ordre décroissant) — voir le tableau ci-dessous.

## Tour 6 — « fais-moi un graphique de ça »

Ce qu'il montre : le rappel du tableau précédent, puis du Python en conteneur.

**5 appel(s) LLM**, 23275 ms, réponse de 172 car., plan : `analyze · source=ventes`, source liée après le tour : `ventes`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 112 |
| `rappel` | aucun outil appelé — passe au planificateur | 321 |
| `plan` | analyze sur ventes | 1615 |
| `analysis` | 1 essai(s), 1 figure(s), statut ok — retenu sous le nom graphique_1 | 20541 |
| `synthesize` | LLM | 675 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='analyze', data_question='créer un graphique à partir du résultat précédent', reason='L\'utilisateur demande une visualisation ("fais-moi un graphique de ça") basée sur le résultat de la conversation précédente, qui provient de la source \'ventes\'.', source='resultat_1'` |

Artefacts rendus :

| type | taille |
|---|---|
| `image/png` | 76548 car. |

Réponse servie :

> Un graphique a été généré à partir des données fournies, montrant la relation entre la raison sociale et le chiffre d'affaires. Ce graphique est disponible en pièce jointe.

## Tour 7 — « prédis la survie d'une passagère de 1re classe de 28 ans, sans famille à bord, billet à 80 livres, embarquée à Cherbourg »

Ce qu'il montre : un modèle de prédiction, qui n'est pas le moteur de langage.

**3 appel(s) LLM**, 3502 ms, réponse de 105 car., plan : `predict · source=—`, source liée après le tour : `ventes`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 115 |
| `rappel` | aucun outil appelé — passe au planificateur | 328 |
| `plan` | predict | 2170 |
| `inference` | statut ok | 879 |
| `synthesize` | modèle (déterministe) | 0 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `final_result` | `capability='predict', dataset='titanic', features="{'age': '28', 'embarked': 'C', 'fare': '80', 'parch': '0', 'pclass': '1', 'sex': 'female'}", reason='Prédiction de survie pour un cas hypothétique avec toutes les features nécessaires pour le modèle titanic.'` |

Artefacts rendus :

Aucun.

Réponse servie :

> Prédiction (titanic) : a survécu (probabilité 95.6%) — détail : n'a pas survécu : 4.4%, a survécu : 95.6%

## Tour 8 — « reprends le tableau précédent et donne-moi les pourcentages »

Ce qu'il montre : le rappel d'un artefact déjà produit.

**6 appel(s) LLM**, 18144 ms, réponse de 237 car., plan : `analyze · source=ventes`, source liée après le tour : `ventes`.

| nœud | détail | ms |
|---|---|---|
| `system` | aucun outil appelé — passe au planificateur | 114 |
| `rappel` | rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok — retenu sous le nom analyse_1 | 16609 |
| `synthesize` | LLM | 1413 |

Appels d'outil émis par le modèle, dans l'ordre :

| # | outil | arguments |
|---|---|---|
| 1 | `rejouer_un_code` | `modification='donne-moi les pourcentages', nom='resultat_1'` |

Artefacts rendus :

Aucun.

Réponse servie :

> Voici les pourcentages de chaque raison sociale par rapport au total du chiffre d'affaires : Vélocité Bordeaux représente 26.15%, pedalier-online.fr 19.95%, Cycles de la Rade 19.05%, Atelier du Rayon 18.37%, et Nord Cycles Négoce 16.48%.

