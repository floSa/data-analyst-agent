# Rédiger un dictionnaire de source

Ce guide énonce les règles de rédaction du dictionnaire d'une source, le document qui donne le sens de ses données à l'agent.

| Section | Contenu |
|---|---|
| [Rôle du dictionnaire](#rôle-du-dictionnaire) | Ce qu'il apporte, et à qui |
| [La règle principale](#la-règle-principale) | Prescrire plutôt que décrire |
| [Les sept règles d'écriture](#les-sept-règles-décriture) | Ordre, comptage et somme, sentinelles, tables, format, taille, vocabulaire |
| [Conséquences d'un dictionnaire ambigu](#conséquences-dun-dictionnaire-ambigu) | Les écarts mesurés |
| [Liste de contrôle](#liste-de-contrôle) | À vérifier avant la mise en service |
| [Emplacement dans le code](#emplacement-dans-le-code) | Où le dictionnaire est lu |

## Rôle du dictionnaire

- Un dictionnaire est un fichier Markdown déclaré par une source (`dictionary` dans le catalogue).
- Le schéma donne les types ; le dictionnaire donne le sens : codes, valeurs particulières, unités, filtres selon la question.
- Il est transmis aux agents qui écrivent le SQL et le Python, avant chaque requête.
- Un dictionnaire clair pour un lecteur humain peut conduire l'agent à des chiffres faux. Le texte du dictionnaire décide du résultat davantage que le prompt ([mesure](historique/dictionnaire-redige-pour-un-humain.md)).

## La règle principale

> **Indiquer quel filtre s'applique à quelle question, et pas seulement ce que signifient les codes.**

- Descriptif, à éviter : « `T` signifie terminée ».
- Prescriptif, à suivre : « pour compter les recharges abouties, `WHERE statut = 'T'` ; pour sommer l'énergie, aucun filtre ».

## Les sept règles d'écriture

### 1. La règle par défaut d'abord, l'exception ensuite

L'agent retient ce qui est placé en tête.
Écrire la règle qui s'applique par défaut, puis l'exception, nommée comme telle, avec ce qui la déclenche.

```markdown
**Règle par défaut : AUCUN filtre sur `statut`.** Tout comptage, tout classement
et toute somme portent sur les 48 000 sessions, les trois codes confondus.

**L'unique exception :** la question porte sur l'ABOUTISSEMENT : elle distingue ce qui a réussi de ce qui a été tenté.
On pose alors `WHERE statut = 'T'`, et dans ce cas seulement.
```

### 2. Distinguer ce qui se compte de ce qui se somme

Une règle de comptage ne dit rien d'une somme.
Pour chaque colonne de code, préciser séparément son effet sur un comptage, une somme, une moyenne et un classement.

### 3. Nommer les valeurs sentinelles, et ce qui leur ressemble sans en être

Une valeur comme `-1`, `999` ou `1900-01-01` signale une absence de mesure.
Elle doit être nommée, ainsi que les valeurs proches qui sont de vraies mesures.

```markdown
**`-1` est une valeur sentinelle** : le compteur n'a rien remonté. Elle est à
écarter de toute moyenne. `0` est une vraie mesure : la borne répond et ne
charge personne.
```

### 4. Une table de correspondance ne remplace pas une règle

Face à une table de cas, l'agent cherche la ligne la plus proche de sa question ; une question nouvelle tombe entre deux lignes.
Une table peut compléter une règle écrite en phrases, jamais la remplacer.

### 5. La mise en forme ne hiérarchise pas

Gras, titres et ordre des paragraphes n'établissent aucune priorité pour l'agent.
Seul le contenu des phrases compte.

### 6. Respecter la taille maximale

- Le dictionnaire est transmis en entier jusqu'à `DAA_DICTIONARY_MAX_CHARS`, 8 000 caractères par défaut.
- Au-delà, les sections de fin sont retirées entières, et la coupe est annoncée.
- Les règles importantes se placent donc en tête, ou le plafond se relève en connaissance de cause.

### 7. Un mot déclencheur ne doit pas être le mot ordinaire de la chose

Un même mot ne peut pas désigner à la fois le cas général et l'exception.
Exemple : « recharge » désigne toute session ; il ne peut pas aussi annoncer le filtre `statut = 'T'`.

- Choisir pour l'exception des mots absents du cas par défaut : « abouties », « réussies », « terminées ».
- Énoncer l'exception comme une propriété : « la question porte sur l'aboutissement », plutôt que par une liste de tournures.
- Relire chaque section en cherchant les mots à double sens.

## Conséquences d'un dictionnaire ambigu

Un dictionnaire ambigu ne produit ni erreur ni avertissement. Il produit un chiffre faux et plausible.

| Question | Chiffre rendu | Chiffre juste |
|---|---|---|
| Énergie totale délivrée | 1 730 823,72 kWh | 1 757 519,23 kWh |
| Nombre total de sessions | 42 281 | 48 000 |
| Palmarès des stations | 907 / 818 / 759 | 1 015 / 911 / 872 |
| Puissance moyenne relevée | 66,32 kW | 68,33 kW |

## Liste de contrôle

- [ ] Chaque colonne de code indique quel filtre s'applique à quelle question.
- [ ] La règle par défaut précède l'exception.
- [ ] Comptage, somme, moyenne et classement sont traités séparément.
- [ ] Les valeurs sentinelles sont nommées, avec les valeurs voisines qui n'en sont pas.
- [ ] Les règles sont écrites en phrases, pas seulement en tableau.
- [ ] Aucun mot déclencheur d'exception n'est employé dans le cas par défaut.
- [ ] Le fichier tient sous `DAA_DICTIONARY_MAX_CHARS`.

Vérification : trois ou quatre questions dont la réponse est connue, dont une au moins qui ne doit pas être filtrée.
Le script [`scripts/mesure_dictionnaire_redige_pour_un_humain.py`](../scripts/mesure_dictionnaire_redige_pour_un_humain.py) accepte un dictionnaire de substitution sans modifier le dépôt.

## Emplacement dans le code

| Élément | Emplacement |
|---|---|
| Déclaration | `sources/**/catalogue.yaml`, clé `dictionary` |
| Coupe, budget, en-têtes | [`agents/dictionnaire.py`](../src/data_analyst_agent/agents/dictionnaire.py) |
| Prompt de l'agent SQL | `agents/retrieval/agent.py`, `composer_le_prompt` |
| Prompt de l'agent d'analyse | `agents/analysis/agent.py`, `composer_le_prompt` |
| Plafond | `DAA_DICTIONARY_MAX_CHARS` (8 000) |

Exemples : les dictionnaires de `sources/demonstration/dictionnaires/`. Celui d'`exploitation` illustre la règle et son exception ; celui de `telemetrie`, la valeur sentinelle.
