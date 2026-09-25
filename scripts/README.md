# Les scripts

Trois familles, et elles ne se lancent pas dans les mêmes circonstances :
l'**exploitation** (des comptes, une migration, un semis), les **semis de
catalogue** (qui fabriquent les données d'une mesure), et les **campagnes de
mesure** (qui parlent au vrai serveur et rendent un score).

Aucun script n'est importable depuis `src/` : ils s'importent entre eux, et le
produit ne dépend d'aucun. En revanche la documentation cite leurs noms et leurs
commandes — un script renommé casse des phrases ailleurs.

---

## Exploitation

| Script | Ce qu'il fait |
|---|---|
| [`manage_users.py`](manage_users.py) | créer, lister, désactiver, réactiver un compte, réinitialiser un mot de passe — le **seul** moyen d'ouvrir un compte. |
| [`migrate_workspace_owner.py`](migrate_workspace_owner.py) | range sous un propriétaire les conversations d'une installation antérieure au cloisonnement ; à blanc par défaut. |
| [`seed_titanic_postgres.py`](seed_titanic_postgres.py) | crée la base Postgres `titanic` du catalogue livré — deux tables jointes par clé étrangère. |

## Semis de catalogue

Chacun engendre ses données à **graine fixe** : rejoué, il rend octet pour octet
le même catalogue. Rien de ce qu'ils écrivent n'est versionné.

| Script | Le catalogue qu'il fabrique |
|---|---|
| [`seed_catalogue_metier.py`](seed_catalogue_metier.py) | `sources/metier/` — les Cycles du Ponant, le catalogue **en service** ([sources-metier.md](../docs/sources-metier.md)). |
| [`seed_catalogue_demonstration.py`](seed_catalogue_demonstration.py) | `sources/demonstration/` — le réseau de recharge, qui a **durci** le socle ([sources-de-demonstration.md](../docs/sources-de-demonstration.md)). |
| [`seed_catalogue_realiste.py`](seed_catalogue_realiste.py) | `tests/catalogues/realiste/` — deux bases Postgres et un classeur, pour le choix de source. |
| [`seed_catalogue_trois_types.py`](seed_catalogue_trois_types.py) | `tests/catalogues/trois-types/` — les trois natures de source à la fois. |

## Bancs

| Script | Ce qu'il éprouve |
|---|---|
| [`vllm_bench.py`](vllm_bench.py) | le *tool calling* d'un serveur OpenAI-compatible : ce dont le système dépend, avant de s'y brancher ([MOTEUR.md](../docs/MOTEUR.md)). |
| [`mesure_concurrence.py`](mesure_concurrence.py) | N utilisateurs **distincts** en même temps : le débit et le cloisonnement ensemble ([historique/concurrence.md](../docs/historique/concurrence.md)). |
| [`live_scenarios.py`](live_scenarios.py) | une batterie de bout en bout contre l'API en marche, avec un compte réel. |
| [`releve_des_parcours.py`](releve_des_parcours.py) | ne juge rien : il **trace** huit messages nœud par nœud et écrit [releve-des-parcours.md](../docs/releve-des-parcours.md). |

---

## Les campagnes de mesure

**Une à la fois, jamais deux de front.** Le moteur est unique : deux campagnes en
parallèle ne mesurent plus le produit, elles mesurent la contention. Le relevé de
livraison en porte la trace — une question verte seule, rouge en passage groupé,
et une suite pytest bloquée une heure sous charge.

La commande a toujours la même forme :

```bash
DAA_CATALOG_PATH=<le catalogue de la ligne> uv run python scripts/<la campagne> [--tirages N]
```

Trois exceptions, notées dans le tableau : deux campagnes portent leurs
catalogues **en dur** et ne lisent pas `DAA_CATALOG_PATH`, et une parle
directement au serveur sans passer par l'application.

**Le repère** est le score de référence de la campagne. Celui donné ici est le
dernier mesuré sur le code livré — la mesure du 25 septembre 2026 *après le
retrait de C67 et C68*, qui est celle de `main`. Quand la campagne n'a pas été
rejouée ce jour-là, c'est le chiffre de la mesure du matin qui est donné, et la
colonne le dit. Tout est dans [releve-de-livraison.md](../docs/releve-de-livraison.md).

| Campagne | Ce qu'elle mesure | Catalogue | Repère | D'où il vient |
|---|---|---|---|---|
| [`mesure_questions_metier.py`](mesure_questions_metier.py) | les douze questions de démonstration du catalogue en service | `sources/metier/catalogue.yaml` | **12/12** | après le retrait, 1 tirage |
| [`mesure_croisement_de_sources.py`](mesure_croisement_de_sources.py) | une question qui porte sur **deux** sources reçoit-elle une réponse fondée ? | `sources/metier/catalogue.yaml` | **13/17** | après le retrait, 1 tirage |
| [`mesure_sources_nommees.py`](mesure_sources_nommees.py) | ce que rend une question qui **nomme** ses sources — et ce qu'elle rend en trop | `sources/metier/catalogue.yaml` | **20/20** par tirage | après le retrait, 3 tirages |
| [`mesure_fils_de_prediction.py`](mesure_fils_de_prediction.py) | quatorze fils où ce qu'un tour laisse derrière lui change le suivant | `sources/metier/catalogue.yaml` | **13/14** | mesure du matin, 1 tirage |
| [`mesure_parcours_de_demonstration.py`](mesure_parcours_de_demonstration.py) | les seize tours du parcours de démonstration, en trois formulations | `sources/demonstration/catalogue.yaml` | **48/48** | après le retrait, 1 tirage |
| [`mesure_ouverture_de_source.py`](mesure_ouverture_de_source.py) | dix façons de dire « on travaille sur cette source » — et ce qui se lie vraiment | `sources/demonstration/catalogue.yaml` | **16/17** | après le retrait, 1 tirage |
| [`mesure_classement_sans_lexique.py`](mesure_classement_sans_lexique.py) | dix façons de demander le même palmarès, et ce que le SQL en fait | `sources/demonstration/catalogue.yaml` | **10/10** | après le retrait, 1 tirage |
| [`mesure_question_de_sens.py`](mesure_question_de_sens.py) | une question de **sens** reçoit-elle un sens, ou des valeurs ? | *portés en dur* | **36/36** | mesure du matin, 3 tirages |
| [`mesure_provenance_du_sens.py`](mesure_provenance_du_sens.py) | une réponse sur le sens d'une colonne dit-elle **d'où** elle le tient ? | *portés en dur* | **30/30** | mesure du matin, 3 tirages |
| [`mesure_surface_conversationnelle.py`](mesure_surface_conversationnelle.py) | ce que l'agent sait répondre **sur lui-même**, et ce que chaque réponse coûte | `sources/catalogue.yaml` | **44/44** | mesure du matin, 1 tirage |
| [`mesure_choix_de_source.py`](mesure_choix_de_source.py) | le parcours complet du choix de source : proposer, lier, basculer | `sources/catalogue.yaml` | **6/6** | après le retrait, 1 tirage |
| [`mesure_ambiguite_de_source.py`](mesure_ambiguite_de_source.py) | ce que fait l'agent quand une question peut porter sur **deux** sources | `tests/catalogues/ambiguite/*.yaml` | **1/1 et 1/1** | après le retrait, 1 essai par ordre |
| [`mesure_memoire_de_conversation.py`](mesure_memoire_de_conversation.py) | six fils, et ce que l'agent fait de ce qu'ils ont produit | `sources/catalogue.yaml` (fil A), `sources/demonstration/` (B à F) | *un relevé, pas un score* | mesure du matin |

### Les campagnes hors du relevé de livraison

Elles n'y figurent pas : elles ont été menées à leur chantier, et leur résultat
vit dans le document qui les porte. Pas de repère ici, donc — il serait inventé.

| Campagne | Ce qu'elle mesure | Catalogue | Où est son relevé |
|---|---|---|---|
| [`mesure_rappel_dartefact.py`](mesure_rappel_dartefact.py) | un artefact nommé se retrouve-t-il **deux tours plus tard** ? | `sources/catalogue.yaml` | [surface-conversationnelle.md](../docs/surface-conversationnelle.md) |
| [`mesure_artefact_absent.py`](mesure_artefact_absent.py) | l'agent **dit**-il qu'il n'a pas produit ce qu'on lui demande de reprendre ? | `sources/catalogue.yaml` | [surface-conversationnelle.md](../docs/surface-conversationnelle.md) |
| [`mesure_continuation.py`](mesure_continuation.py) | ce que l'agent fait d'un message qui ne se suffit pas à lui-même (« oui », « et dedans ? ») | `sources/catalogue.yaml` | [surface-conversationnelle.md](../docs/surface-conversationnelle.md) |
| [`mesure_tranche_ou_source.py`](mesure_tranche_ou_source.py) | un chiffre calculé sur une **tranche** sort-il en se disant chiffre de la source ? | `sources/demonstration/catalogue.yaml` | [sources-de-demonstration.md](../docs/sources-de-demonstration.md) |
| [`mesure_dictionnaire_analyse.py`](mesure_dictionnaire_analyse.py) | ce que le **code d'analyse** fait d'une valeur sentinelle que le dictionnaire nomme | `sources/demonstration/catalogue.yaml` | [sources-de-demonstration.md](../docs/sources-de-demonstration.md) |
| [`mesure_dictionnaire_redige_pour_un_humain.py`](mesure_dictionnaire_redige_pour_un_humain.py) | un dictionnaire écrit pour une **personne** tient-il devant l'agent ? | `sources/demonstration/catalogue.yaml` | [rediger-un-dictionnaire-de-source.md](../docs/rediger-un-dictionnaire-de-source.md) |
| [`mesure_trois_types_de_source.py`](mesure_trois_types_de_source.py) | l'agent répond-il juste sur les **trois** types de source sans les confondre ? | `tests/catalogues/trois-types/catalogue.yaml` | nulle part en propre — ses oracles sont dans `tests/catalogues/trois-types/`, et [ARCHITECTURE §6](../docs/ARCHITECTURE.md#6-stratégie-de-tests) dit ce qu'elle vaut |
| [`mesure_typage_des_arguments_d_outil.py`](mesure_typage_des_arguments_d_outil.py) | ce que le serveur rend comme **type** dans les arguments d'un outil — il parle au moteur, pas à l'application | *aucun* | [MOTEUR.md §8.4](../docs/MOTEUR.md) |

### Les deux qui ne coûtent aucune minute de moteur

| Campagne | Ce qu'elle mesure |
|---|---|
| [`mesure_contexte.py`](mesure_contexte.py) | ce qu'une conversation injecte dans le contexte, tour après tour — elle n'appelle aucun modèle et n'ouvre aucun réseau. |
| [`mesure_releve_des_sources.py`](mesure_releve_des_sources.py) | les trois bornes du relevé d'une source — fraîcheur, patience, approximation — avant et après, sur des sources qu'on fait tomber puis revenir. |
