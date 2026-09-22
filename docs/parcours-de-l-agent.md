# Le parcours de l'agent, en huit conversations

Ce document montre ce que l'application fait **réellement** quand on lui parle :
quel nœud est traversé, quel outil est appelé, combien d'allers-retours avec le
moteur, qui écrit la phrase finale.

Rien n'y est dessiné de mémoire. Chaque diagramme est établi à partir d'une
trace relevée, et le relevé se rejoue :

```bash
uv run python scripts/releve_des_parcours.py --markdown docs/releve-des-parcours.md
```

Le relevé obtenu est dans [`docs/releve-des-parcours.md`](releve-des-parcours.md).
Les chiffres cités ici en sortent. Un autre tirage donnera d'autres phrases :
le modèle n'est pas déterministe. Les chemins, eux, sont stables.

**Ce document est tiré du relevé d'empreinte SHA-256 :**

    RELEVÉ : 5700b60a0777c86d1faee384028e74fb4bdaafa9133e3355f539ac306feffa90

Relevé le 2026-09-22 sur le catalogue `sources/metier/catalogue.yaml`.
Cette ligne n'est pas un ornement : `tests/unit/docs/test_parcours_de_l_agent.py`
la recalcule, et le test rougit dès que le relevé est régénéré sans que cette
prose-ci soit reprise. C'est la seule chose qu'une machine sache vérifier
d'une prose — non qu'elle soit vraie, mais qu'elle ait été relue depuis la
dernière mesure.

---

## Les huit nœuds

Le graphe est construit dans
[`src/data_analyst_agent/orchestrator/graph.py`](../src/data_analyst_agent/orchestrator/graph.py).
Un tour de conversation le traverse une fois, de l'entrée à la fin.

| nœud | ce qu'il fait |
|---|---|
| `system` | Demande au modèle : « est-ce une question sur moi ? ». S'il appelle un outil de faits, la question était sur le système et le tour s'arrête là. |
| `rappel` | Demande : « parle-t-on de quelque chose que ce fil a déjà produit ? ». Se retire sans appeler le modèle quand le fil n'a rien produit. |
| `plan` | Choisit la capacité — interroger, analyser, prédire — et la source. |
| `retrieval` | Écrit du SQL et l'exécute sur la source. |
| `analysis` | Écrit du Python et l'exécute dans un conteneur. |
| `inference` | Appelle un modèle de prédiction du registre. |
| `fetch_predict` | Va chercher une ligne dans une source, puis prédit dessus. |
| `synthesize` | Écrit la phrase que l'utilisateur lit — ou sert un texte déterministe. |

L'ordre n'est pas libre : `system` est l'entrée, `rappel` vient ensuite, `plan`
n'est atteint que si les deux se sont retirés, et tout finit par `synthesize`.

## Les huit outils de l'agent système

Ce sont les seuls outils du nœud `system`. Ils lisent la configuration et les
sources déclarées ; aucun n'écrit ni ne calcule. Ils sont définis dans
[`src/data_analyst_agent/orchestrator/systeme.py`](../src/data_analyst_agent/orchestrator/systeme.py).

| outil | ce qu'il rend |
|---|---|
| `capacites_de_l_agent` | ce que l'agent sait faire, et sur quoi |
| `sources_de_donnees` | les sources déclarées : nom, type, description, volume, période |
| `chercher_une_source` | la source qui parle d'un sujet, quand la question ne la nomme pas |
| `schema_d_une_source` | les tables, colonnes et types d'une source, et ce que son dictionnaire dit de leur sens |
| `travailler_sur_une_source` | retient une source comme source de travail du fil |
| `modeles_de_prediction` | les modèles du registre : tâche, cible, classes, unité |
| `attributs_d_un_modele` | les attributs qu'un modèle attend pour prédire |
| `memoire_de_la_conversation` | les tableaux et le code que CE fil a produits — les sources transformées |

---

# Le fil, tour par tour

Les huit messages s'enchaînent dans une seule conversation. Chaque tour reçoit
ce que le précédent a laissé : la source liée, l'échange précédent, la
prédiction en attente. C'est ce que fait la route `/chat` de
[`src/data_analyst_agent/api/app.py`](../src/data_analyst_agent/api/app.py), et
c'est ce que le runner refait.

## 1 — « quelles sont les sources à ta disposition ? »

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant G as Graphe
    participant S as Noeud system
    participant M as Moteur (port 8100)
    participant C as Catalogue declare
    participant Y as Noeud synthesize

    U->>G: quelles sont les sources a ta disposition ?
    G->>S: entree du graphe
    S->>M: question + 7 fiches d outil
    M-->>S: appel d outil sources_de_donnees()
    S->>C: lit les sources declarees
    C-->>S: 5 sources, volumes, periodes
    S->>M: les faits, a formuler
    M-->>S: J ai acces aux sources suivantes...
    S->>S: ceinture : la phrase porte-t-elle les faits ? oui
    S->>Y: reponse servie (1re passe)
    Y-->>U: 149 car., aucun artefact
```

**Trace relevée.** `system` → `synthesize`. Un seul outil appelé :
`sources_de_donnees` sans argument. **2 appels LLM** — un pour décider et
appeler l'outil, un pour formuler. Réponse de 149 caractères, aucun artefact.

> J'ai accès aux sources suivantes : `ventes`, `production`, `stocks`, `iris`,
> et `titanic`. Que souhaitez-vous savoir sur une source en particulier ?

**Ce qui ne se passe pas.** Aucune base n'est ouverte pour répondre. Aucun SQL
n'est écrit. Aucun Python n'est exécuté. Les nœuds `plan`, `retrieval` et
`analysis` ne sont pas atteints — le tour s'est arrêté à `system`.

## 2 — « peux-tu me préciser ventes et production ? »

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant M as Moteur
    participant C as Catalogue declare
    participant Y as Noeud synthesize

    U->>S: peux-tu me preciser ventes et production ?
    S->>M: question + fiches d outil
    M-->>S: sources_de_donnees(cible='ventes')
    M-->>S: sources_de_donnees(cible='production')
    Note over S,C: deux appels dans la MEME reponse du modele
    S->>C: lit ventes
    S->>C: lit production
    C-->>S: deux fiches, volumes et periodes lus
    S->>M: les deux fiches, a formuler
    M-->>S: Voici les details des sources...
    S->>S: ceinture : les deux sources sont citees, faits portes
    S->>Y: reponse servie
    Y-->>U: 695 car., aucun artefact
```

**Trace relevée.** `system` → `synthesize`. **Deux appels de
`sources_de_donnees` dans la même réponse du modèle**, l'un avec
`cible='ventes'`, l'autre avec `cible='production'`. **2 appels LLM** : le
modèle demande les deux fiches en une fois, puis formule.

> Voici les détails des sources `ventes` et `production` : La source `ventes`
> est une base de données PostgreSQL qui contient le carnet de commandes des
> Cycles du Ponant. Elle comprend 4 tables, 673 lignes, et couvre la
> période […]

## 3 — « et plus de détails sur ventes ? » — la source se lie au fil

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant M as Moteur
    participant V as Source ventes (PostgreSQL)
    participant D as Dictionnaire de ventes
    participant Y as Noeud synthesize

    U->>S: et plus de details sur ventes ?
    Note over S,M: le tour precedent part en historique :<br/>« et » a quelque chose a designer
    S->>M: question + tour precedent
    M-->>S: schema_d_une_source(cible='ventes')
    S->>V: lit le schema (tables, colonnes, types, cles)
    S->>D: lit ce que les colonnes VEULENT DIRE
    V-->>S: 4 tables, colonnes typees
    D-->>S: unites, codes, valeurs sentinelles
    S->>M: les faits, a formuler
    M-->>S: Je travaille sur la source ventes...
    S->>S: le message nomme UNE source : elle est liee au fil
    S->>Y: reponse + source liee = ventes
    Y-->>U: 2 243 car., source de travail = ventes
```

**Trace relevée.** `system` → `synthesize`. Un appel :
`schema_d_une_source(cible='ventes')`. **2 appels LLM**. Réponse de 2 243
caractères. **Après ce tour, la source liée au fil est `ventes`** — elle le
restera pour tous les tours suivants.

Lire le schéma ouvre la source. C'est le premier tour du fil qui le fait, et il
ne lit que la structure : aucune ligne de donnée n'est ramenée.

## 4 — « parle-moi un peu de stocks et de titanic, en deux mots » — la ceinture

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant M as Moteur
    participant P as plancher des sources nommees
    participant Y as Noeud synthesize

    U->>S: parle-moi un peu de stocks et de titanic, en deux mots
    S->>M: question + fiches d outil
    M-->>S: AUCUN appel d outil, une phrase libre
    S->>P: le message nomme 2 sources declarees et rien n a ete appele
    P-->>S: les fiches de stocks et titanic, servies d office
    S->>S: ceinture : la 1re formulation ne porte pas ces faits
    Note over S,M: 1re passe ECARTEE (reponse hors sujet)
    S->>M: les memes faits, la meme question : RECOMMENCE
    M-->>S: Stocks : entrepots, mouvements ; Titanic : survie, age.
    S->>S: ceinture : la 2e formulation porte les faits
    S->>Y: reponse servie (2e passe)
    Y-->>U: 92 car.
```

**Trace relevée.** `system` → `synthesize`. Détail du nœud :
`plancher_des_sources_nommees — reformulé au second tour (réponse hors sujet)`.
**Aucun appel d'outil émis par le modèle** — c'est le plancher, un mécanisme
déterministe, qui a servi les fiches. **2 appels LLM** : la première
formulation, puis la seconde. Réponse de 92 caractères.

> Stocks : entrepôts, mouvements ; Titanic : survie, âge.
> Que souhaitez-vous savoir d'autre ?

**Ce que la ceinture compare.** Elle prend les faits que les outils (ou le
plancher) ont rendus, et la phrase que le modèle a écrite. Elle refuse une
phrase qui invente un nom, qui omet un nom rendu par l'outil, ou qui cite une
source sans porter un seul fait de sa fiche.

**Ce qui arrive ensuite.** Trois voies, dans cet ordre :

1. **1re passe** — la formulation passe, elle part telle quelle ;
2. **2e passe** — elle est écartée, on rend au modèle les mêmes faits et la
   même question pour qu'il recommence ; c'est ce qui s'est passé ici ;
3. **repli** — la seconde échoue aussi : les faits sont servis tels quels, sans
   phrase de modèle.

## 5 — « quel est le chiffre d'affaires par revendeur, les 5 premiers ? »

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant P as Noeud plan
    participant Q as Noeud retrieval
    participant M as Moteur
    participant G as Garde-fou SQL
    participant V as ventes (PostgreSQL)
    participant Y as Noeud synthesize

    U->>S: quel est le chiffre d affaires par revendeur, les 5 premiers ?
    S->>M: question + fiches d outil
    M-->>S: AUCUN appel : ce n est pas une question sur le systeme
    Note over S,P: le noeud rappel n apparait pas dans la trace :<br/>le fil n a encore rien produit
    S->>P: passe au planificateur
    P->>M: quelle capacite, quelle source ?
    M-->>P: capability='query', source='ventes'
    P->>Q: query sur ventes
    Q->>M: ecris le SQL
    M-->>Q: get_schema()
    Q-->>M: les tables et colonnes de ventes
    M-->>Q: run_sql(query='SELECT ... WHERE statut <> ANN ...')
    Q->>G: le SQL, avant execution
    G-->>Q: lecture seule, une seule instruction : accepte
    Q->>V: execute
    V-->>Q: 5 lignes
    Q->>Y: le tableau
    Y-->>U: 102 car. + artefact application/json
```

**Trace relevée.** `system` → `plan` → `retrieval` → `synthesize`. **5 appels
LLM.** Trois appels d'outil émis : `final_result` (le plan : `capability='query'`,
`source='ventes'`), puis `get_schema()` et `run_sql(...)`. Un artefact
`application/json` de 247 caractères. La phrase finale est déterministe —
« résumé déterministe (multi-lignes) » — le modèle n'y touche pas.

Le SQL relevé :

```sql
SELECT c.raison_sociale, SUM(o.montant_total_eur) AS chiffre_affaires
FROM commandes o JOIN clients c ON o.client_id = c.client_id
WHERE o.statut <> 'ANN'
GROUP BY c.raison_sociale ORDER BY chiffre_affaires DESC LIMIT 5;
```

Deux choses s'y lisent. La **jointure** : « revendeur » n'est pas une colonne de
`commandes`, il a fallu aller chercher `clients`. Et le **`statut <> 'ANN'`** :
rien dans la question ne le demande ; il vient du dictionnaire de la source, qui
écrit qu'une somme d'argent exclut les commandes annulées.

**Le SQL est écrit par le modèle et vérifié chez nous avant exécution.** Le
garde-fou est dans
[`src/data_analyst_agent/agents/retrieval/sql.py`](../src/data_analyst_agent/agents/retrieval/sql.py) :
il refuse ce qui n'est pas une lecture. Ce n'est pas le modèle qui décide de ce
qui part à la base.

## 6 — « fais-moi un graphique de ça »

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant R as Noeud rappel
    participant P as Noeud plan
    participant A as Noeud analysis
    participant M as Moteur
    participant B as Conteneur (reseau coupe, disque en lecture seule)
    participant Y as Noeud synthesize

    U->>S: fais-moi un graphique de ca
    S->>M: question + fiches d outil
    M-->>S: AUCUN appel
    S->>R: passe au rappel
    R->>M: parle-t-on d un artefact deja produit ?
    M-->>R: AUCUN appel : ce n est pas une relecture, c est une analyse
    R->>P: passe au planificateur
    P->>M: quelle capacite ?
    M-->>P: capability='analyze', source='resultat_1'
    P->>A: analyze sur ventes, depuis le tableau du tour 5
    A->>M: ecris le Python
    M-->>A: le code matplotlib
    A->>B: execute
    B-->>A: 1 figure PNG, statut ok
    A->>Y: la figure, retenue sous le nom graphique_1
    Y->>M: ecris la phrase
    M-->>Y: Un graphique du Chiffre d Affaires par Client a ete genere
    Y-->>U: 256 car. + artefact image/png (74 720 car.)
```

**Trace relevée.** `system` → `rappel` → `plan` → `analysis` → `synthesize`.
**5 appels LLM.** Le nœud `analysis` relève « 1 essai(s), 1 figure(s), statut
ok — retenu sous le nom `graphique_1` ». Un artefact `image/png` de 74 720
caractères.

Le plan désigne `source='resultat_1'` : ce n'est pas une source du catalogue,
c'est le **tableau produit au tour 5**, réexposé au fil. « ça » a trouvé quelque
chose à désigner.

**Le Python tourne en conteneur, réseau coupé, disque en lecture seule.** Le
contrat est dans
[`src/data_analyst_agent/sandbox/client.py`](../src/data_analyst_agent/sandbox/client.py)
et le cadrage dans [`docs/CADRAGE.md`](CADRAGE.md). Le code écrit par le modèle
ne s'exécute jamais dans le processus de l'application.

C'est le seul tour du fil où `synthesize` appelle le modèle. Ailleurs, la phrase
est déterministe ou vient déjà de `system`.

## 7 — « prédis la survie d'une passagère de 1re classe de 28 ans… »

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant R as Noeud rappel
    participant P as Noeud plan
    participant I as Noeud inference
    participant M as Moteur
    participant W as Registre de modeles (scikit-learn)
    participant Y as Noeud synthesize

    U->>S: predis la survie d une passagere... sans famille a bord...
    S->>M: question + fiches d outil
    M-->>S: AUCUN appel
    S->>R: passe au rappel
    R->>P: rien a rappeler, passe au planificateur
    P->>M: quelle capacite ?
    M-->>P: capability='predict', dataset='titanic', features={age, embarked, fare, parch, pclass, sex}
    Note over P: regle deterministe : « sans famille a bord » est<br/>une valeur pour LES DEUX compteurs d accompagnants,<br/>et le schema dit lesquels -> sibsp=0
    P->>I: predict
    I->>W: valide les features contre le schema du modele
    W-->>I: les sept y sont
    W-->>I: a survecu, probabilite 95.6%
    I->>Y: statut ok
    Y-->>U: 105 car., rendu deterministe
```

**Trace relevée.** `system` → `rappel` → `plan` → `inference` → `synthesize`.
**3 appels LLM.** Le nœud `inference` relève « statut ok ». Rien n'est en
attente après le tour.

> Prédiction (titanic) : a survécu (probabilité 95.6%) — détail : n'a pas
> survécu : 4.4%, a survécu : 95.6%

**« Sans famille à bord » est lu, et ce n'est pas le modèle qui le lit.** Le
planificateur en tire `parch=0` et laisse `sibsp` de côté — c'est ce que le
relevé montre, et c'est ce qu'il montrait avant. Ce qui a changé est en aval :
le schéma
([`titanic.py`](../src/data_analyst_agent/agents/inference/schemas/titanic.py))
DÉCLARE que `sibsp` et `parch` comptent tous deux des accompagnants, une
construction fermée reconnaît dans le message qu'il n'y en a aucun
([`accompagnants.py`](../src/data_analyst_agent/agents/inference/accompagnants.py)),
et une règle du plan remplit ce que l'utilisateur n'a pas donné autrement. Ce
que l'utilisateur a donné explicitement prime toujours : la règle ne peut
qu'ajouter.

**Le modèle de prédiction n'est pas le moteur de langage.** C'est un modèle
scikit-learn du registre
[`src/data_analyst_agent/agents/inference/registry.py`](../src/data_analyst_agent/agents/inference/registry.py),
chargé depuis le disque, avec un schéma de features déclaré. Le moteur de
langage lit la phrase et en extrait les features ; c'est tout ce qu'il fait ici.

## 8 — « reprends le tableau précédent et donne-moi les pourcentages »

```mermaid
sequenceDiagram
    autonumber
    actor U as Utilisateur
    participant S as Noeud system
    participant R as Noeud rappel
    participant W as Espace de travail du fil
    participant B as Bac a sable
    participant M as Moteur
    participant Y as Noeud synthesize

    U->>S: reprends le tableau precedent et donne-moi les pourcentages
    S->>M: question + fiches d outil
    M-->>S: AUCUN appel
    S->>R: passe au rappel
    R->>M: question + catalogue des artefacts du fil
    M-->>R: rejouer_un_code(nom='resultat_1', modification='donne-moi les pourcentages')
    R->>W: resultat_1 est-il du code ?
    W-->>R: non, c est un tableau — et son CSV est monte sous /data/
    Note over R,W: un tableau n a pas de code a rejouer,<br/>mais il a une matiere : on ecrit le calcul
    R->>M: ecris le calcul sur CE tableau, sans reinterroger la source
    M-->>R: du Python qui lit /data/resultat_1.csv
    R->>B: execute
    B-->>R: les cinq pourcentages
    R->>M: voici ce que le calcul a rendu
    M-->>R: la phrase
    R->>Y: la reponse du rappel
    Y-->>U: 237 car., les pourcentages
```

**Trace relevée.** `system` → `rappel` → `synthesize`. **6 appels LLM.** Le
nœud `rappel` relève « rejeu de resultat_1 — 1 essai(s), 0 figure(s), statut ok
— retenu sous le nom analyse_1 ». Le tour ne traverse pas le nœud `plan`, mais
il porte un plan : `analyze · source=ventes`, parce qu'un rejeu EST une analyse
et que `ChatAnswer.plan` doit le dire.

> Voici les pourcentages de chaque raison sociale par rapport au total du
> chiffre d'affaires : Vélocité Bordeaux représente 26.15%, pedalier-online.fr
> 19.95%, Cycles de la Rade 19.05%, Atelier du Rayon 18.37%, et Nord Cycles
> Négoce 16.48%.

**Le modèle demande à REJOUER un tableau, et c'est le bon geste.** On ne
réexécute pas un CSV — un tableau n'a pas de code — mais il a une matière, et
elle est déjà montée sous `/data/` par `_mount_workspace`. L'outil écrit donc
le Python qui lit CE fichier-là et l'exécute dans le bac à sable. La consigne
qui l'accompagne porte la clause décisive : « sans réinterroger la source ».
Sans elle, le code généré repart volontiers du carnet de commandes — il est
monté lui aussi — et le tour répond à une autre question que celle qu'on a
posée.

**Le calcul s'exécute, et les pourcentages sont justes.** 26,15 + 19,95 +
19,05 + 18,37 + 16,48 = 100,00. Le chiffre n'est pas formulé par le moteur de
langage à partir d'un tableau qu'il aurait sous les yeux : il est calculé par
du code, dans le conteneur, et le moteur ne fait que le rapporter. C'est ce
que le prompt de rappel exige — tout chiffre dérivé passe par l'outil — et
c'est ce qui sépare un rejeu d'une lecture à voix haute.

**Le rejeu laisse une trace dans le fil.** Le code écrit ici est retenu sous le
nom `analyse_1` : il entre au catalogue, et un tour ultérieur peut le reprendre
à son tour. Aucune figure n'est produite — la demande n'en réclamait pas — donc
le tour ne rend aucun artefact affichable.


---

# Ce qui ne se passe pas

- Une question sur les sources — tours 1 à 4 — **n'ouvre aucune base pour
  répondre**, **n'écrit aucun SQL**, **n'exécute aucun Python**. Le tour
  s'arrête au nœud `system`. Lire un schéma (tour 3) ouvre la source pour en
  lire la structure, et ne ramène aucune ligne.
- Le nœud `rappel` **n'appelle pas le modèle** quand le fil n'a rien produit.
  Au tour 5, il n'apparaît même pas dans la trace.
- Le nœud `synthesize` **n'appelle le modèle que sur les deux tours qui ont
  exécuté du Python** — 6 et 8. Ailleurs il ne le paie pas : la phrase vient de
  `system` (tours 1 à 4), d'un résumé déterministe (tour 5) ou du rendu du
  modèle de prédiction (tour 7).
- Le tour 8 **n'atteint pas le planificateur** : le rappel a servi, et le
  graphe va directement à la synthèse. Le tour porte quand même un plan
  (`analyze`), parce qu'un rejeu exécute du code sur une source.
- **Un seul service d'inférence est appelé, et il est sur cette machine** :
  celui que `DAA_LLM_BASE_URL` désigne, port 8100. Il n'y en a pas d'autre. Le
  relevé le nomme en tête de chaque exécution.

# Le coût, en appels LLM

| tour | message | nœuds | appels LLM |
|---|---|---|---|
| 1 | quelles sont les sources à ta disposition ? | `system` → `synthesize` | 2 |
| 2 | peux-tu me préciser ventes et production ? | `system` → `synthesize` | 2 |
| 3 | et plus de détails sur ventes ? | `system` → `synthesize` | 2 |
| 4 | parle-moi un peu de stocks et de titanic | `system` → `synthesize` | 2 |
| 5 | quel est le chiffre d'affaires par revendeur ? | `system` → `plan` → `retrieval` → `synthesize` | 5 |
| 6 | fais-moi un graphique de ça | `system` → `rappel` → `plan` → `analysis` → `synthesize` | 5 |
| 7 | prédis la survie d'une passagère… | `system` → `rappel` → `plan` → `inference` → `synthesize` | 3 |
| 8 | reprends le tableau précédent… | `system` → `rappel` → `synthesize` | 6 |

**27 appels LLM pour huit tours.** Le nœud `system` en coûte un en tête de
chaque question, y compris celles qui ne le concernent pas : c'est le prix de ne
pas reconnaître les questions méta par un lexique, et il est assumé
([`docs/surface-conversationnelle.md`](surface-conversationnelle.md)).

---

# Quatre défauts corrigés

Ce document décrivait quatre défauts relevés ici même. Ils sont corrigés, et le
relevé ci-dessus est celui d'APRÈS. Ce qui suit dit ce qui a changé, et
comment chacun se mesure.

**Une prédiction en attente ne confisque plus le fil.** Les nœuds `system` et
`rappel` se retirent quand une prédiction attend des features : c'est
délibéré — un « oui » ou « une femme » doit aller compléter la prédiction, et
non se faire attraper par un autre agent. Ce retrait n'était pas borné, et il
emportait tout : sur un fil qui avait produit un tableau, « reprends le tableau
précédent » ne trouvait plus rien, le planificateur partait en `query` sur un
objet qu'il n'interroge pas, et l'utilisateur lisait « je n'ai pas interrogé la
source ». Le retrait du nœud `rappel` est maintenant borné par
``designation_dun_artefact_passe`` — la construction que ce nœud emploie déjà :
un message qui désigne un artefact déjà produit ne complète pas une prédiction.
Et un tour qui ne s'est pas prononcé sur la prédiction ne l'efface plus
(``Orchestrator._pending_retenu``) : sans cela, borner le retrait n'aurait fait
que déplacer la confiscation — le tableau redevenait atteignable et c'est la
prédiction qui se perdait.

**La prédiction aboutit.** « Sans famille à bord » fixe les deux compteurs
d'accompagnants, et non un seul (tour 7). La clause est en outre retirée d'une
SECONDE lecture quand la première laisse la prédiction incomplète : mesuré, sa
seule présence faisait perdre au planificateur `age`, qu'il extrayait 5 tirages
sur 5 sans elle. Le coût est d'un appel LLM, et seulement sur le chemin qui,
sans lui, ne rendait rien.

**Le rappel d'un tableau ne nie plus ce qu'il sert.** L'outil de rejeu passait
au refus des noms inconnus un nom qu'il venait de décorer — « resultat_1 (ce
n'est pas du code) » — et la phrase rendue niait l'artefact qu'elle énumérait
dans la même haleine. Deux choses ont changé : la contradiction est devenue
impossible quelle que soit la main qui appelle (``refus_dartefact`` lit la
désignation, pas le nom nu), et un artefact qui existe ne se cache plus derrière
un refus — rien n'est exécuté, et son contenu est rendu.

**Comment ces trois-là se mesurent.** Pas ici : ce document relève UN fil de
huit messages, et ces défauts demandent des fils construits pour eux — un
tableau, puis une prédiction incomplète, puis un rappel.
[`scripts/mesure_fils_de_prediction.py`](../scripts/mesure_fils_de_prediction.py)
joue huit fils, dont trois témoins, en reportant d'un tour à l'autre ce que la
route `/chat` reporte.

    DAA_CATALOG_PATH=sources/metier/catalogue.yaml \
      uv run python scripts/mesure_fils_de_prediction.py --tirages 3

Avant : `a` 0/3, `d` 0/3, `f` 0/3, `g` 0/3. Après : **8 fils sur 8, 3 tirages
chacun**.

**Un chiffre dérivé n'a plus besoin qu'on désigne le tableau.** Le tour 8 le
montre avec la désignation — « reprends le tableau précédent » — mais le défaut
était juste à côté : le MÊME message sans elle ne trouvait pas le tableau qui
venait d'être produit. « donne-moi les pourcentages », « et ça fait combien en
pourcentage du total ? », « ajoute une colonne avec la part de chacun » : les
trois partaient au planificateur, qui repartait en `query`, ne rendait aucune
ligne, et l'utilisateur lisait « je n'ai pas interrogé la source ».

Le fil brut disait où, et ce n'était pas le retrait du nœud : l'agent de rappel
ÉTAIT appelé, ses deux outils lui étaient offerts, et il répondait la
sentinelle. Sa démarche ne connaissait qu'une liste de tournures de reprise, et
aucun de ces trois messages n'en porte. Ce qui lui manquait n'était pas un mot
de plus dans la liste — trois fois payé sur ce projet — mais un FAIT : lequel
des artefacts du catalogue vient d'être produit. Il se lit dans le magasin
(`ConversationWorkspace.produits_au_tour_precedent`), en comparant la question
qui a produit chaque artefact à celle que le tour d'avant a retenue, sans rien
demander au vocabulaire du message courant. Le catalogue injecté le marque, et
la démarche de l'agent ouvre une seconde porte, bornée par la SUFFISANCE de la
demande : ce qu'on ne comprend pas sans le tour précédent porte sur ce qui
vient d'être montré ; ce qui se tient debout tout seul reste au planificateur.

Avant : 0/4 des formulations sans désignation atteignaient le rappel. Après :
4/4, rejeu du tableau, pourcentages justes. La borne est mesurée par ses
témoins — « combien de clients au total ? », « quelles sources as-tu ? »,
« fais-moi un histogramme des montants de commande », « quel est le montant
moyen d'une commande ? » restent au planificateur, 4/4, avec un tableau tout
frais au catalogue.

Et le garde-fou n'a pas bougé : « je n'ai pas interrogé la source pour cette
question » reste ce que dit un tour qui n'a rien interrogé. C'est le chemin qui
a changé, pas ce qu'on s'autorise à affirmer.

---

---

# Vérifier ce document

Un document qui nomme des nœuds et des outils devient faux au premier
renommage, sans que rien ne rougisse.
[`tests/unit/docs/test_parcours_de_l_agent.py`](../tests/unit/docs/test_parcours_de_l_agent.py)
l'en empêche : il vérifie que tout nœud cité existe dans le graphe et que tout
nœud du graphe est cité, que tout outil cité existe parmi les outils de l'agent
système et réciproquement, et que tout fichier cité existe. Un renommage fait
échouer ce test.

**Et sa limite était béante.** Ces vérifications-là sont restées vertes pendant
que ce document décrivait, au tour 8, le contraire de ce que le code fait : « ce
n'est pas du code : rien n'a été exécuté », « le tableau, sans les
pourcentages », alors que le calcul s'exécutait et que les pourcentages étaient
justes. Tous les noms cités existaient ; seule la prose était fausse. Un test ne
lit pas une prose, et il serait malhonnête de prétendre le contraire.

Ce qu'on vérifie à la place est plus modeste, et c'est vérifiable :

1. **La prose déclare le relevé dont elle est tirée**, par l'empreinte SHA-256
   ci-dessus. Régénérer `docs/releve-des-parcours.md` sans reprendre ce
   document fait rougir le test. Il ne dit pas que la prose est juste ; il dit
   qu'elle a été relue depuis la dernière mesure, et c'est exactement ce qui
   avait manqué.
2. **Les faits que les deux documents portent tous les deux concordent** : pour
   chacun des huit tours, les nœuds traversés et le nombre d'appels LLM du
   tableau « Le coût, en appels LLM » sont comparés à ceux du relevé. Ce sont
   les seuls énoncés de cette prose qu'une machine sache confronter à une
   mesure — et ils auraient rougi : le document annonçait 7 appels au tour 6 et
   4 au tour 8, là où le relevé en comptait 5 et 6.
