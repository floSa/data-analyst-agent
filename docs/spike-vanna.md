# Spike Vanna — text-to-SQL par RAG, comparé au socle maison (roadmap §11, étape 9)

Date : 2026-07-09. LLM identique des deux côtés : `qwen3-coder:30b` via Ollama, température 0.
Schéma identique aux tests : Titanic multi-tables (`passengers` + `classes`, clé étrangère).

## Protocole

- Vanna **0.7.9** (dernière version de la lignée RAG classique, MIT) + ChromaDB local
  + connecteur Ollama. La version 2.x publiée après l'archivage du dépôt réoriente le
  produit vers une plateforme d'agents : le connecteur `vanna.chromadb` / `vanna.ollama`
  historique n'y existe plus.
- Entraînement minimal : les 2 DDL + une phrase de documentation (sémantique de
  `level` et `survived`).
- 3 questions, le SQL généré est exécuté sur une base DuckDB de contrôle seedée
  depuis `sources/titanic.csv`.

## Résultats

| Question | SQL | Valeur | Verdict |
|---|---|---|---|
| % de femmes de 1ʳᵉ classe survivantes (golden n°1) | jointure correcte + CASE | 96,8085 (= oracle pandas 96,81) | correct (21 s, chargement modèle compris) |
| Passagers embarqués à Cherbourg | filtre simple | 168 | correct (1 s) |
| Âge moyen des survivants par classe | jointure + GROUP BY | 35,4 / 25,9 / 20,6 | correct (5 s) |

Le **socle maison** (tools `get_schema`/`run_sql` + self-correction) produit des
requêtes de même qualité avec le même LLM (cf. validation live des scénarios
golden) : sur ce périmètre, **aucun écart de qualité SQL**.

## Frictions constatées avec Vanna

- **Upstream archivé** (mars 2026) : 0.7.9 est figée ; adopter = forker et maintenir.
- **Installation** : `chroma-hnswlib` doit compiler du C++ sous Windows (échec sans
  MSVC) ; OK sous Linux/WSL. Premier lancement : ChromaDB télécharge un modèle
  d'embedding ONNX (~80 Mo) — à pré-provisionner pour de l'on-prem réseau coupé.
- Connecteur Ollama : l'hôte doit être passé via `config["ollama_host"]` (la variable
  d'environnement standard est ignorée) ; télémétrie ChromaDB bruyante.
- Pas de garde-fou lecture seule ni de limite d'allers-retours intégrés — tout ce que
  le socle maison fournit déjà (avec introspection automatique du schéma, sans
  `train()` à maintenir).

## Décision

**Le socle maison reste la brique text-to-SQL du produit.** Vanna n'apporte pas de
gain de qualité SQL ici et ajoute une dépendance archivée + des frictions on-prem.

**Idée à retenir de Vanna** : sa vraie valeur est la *mémoire RAG de paires
question→SQL validées* qui améliore les questions récurrentes. Transposable au socle
maison sans dépendance : stocker les paires validées par les utilisateurs et injecter
les k plus proches dans le prompt de l'agent récupération. À considérer en V2.
