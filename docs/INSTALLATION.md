# Installation

Cette procédure installe data-analyst-agent en service sur une machine Linux, jusqu'à une première question posée à une source.

| Section | Contenu |
|---|---|
| [1. Prérequis](#1-prérequis) | Ce que la machine doit fournir |
| [2. Récupérer le dépôt](#2-récupérer-le-dépôt) | Emplacement du code |
| [3. Le fichier d'environnement](#3-le-fichier-denvironnement) | Réglages et secrets |
| [4. Le dossier de données](#4-le-dossier-de-données) | Ce qui persiste |
| [5. Déclarer une première source](#5-déclarer-une-première-source) | Le catalogue |
| [6. Construire les images](#6-construire-les-images) | Application et bac à sable |
| [7. Installer le service](#7-installer-le-service) | Unité systemd |
| [8. Le certificat et l'exposition](#8-le-certificat-et-lexposition) | HTTPS |
| [9. Créer le premier compte](#9-créer-le-premier-compte) | Comptes utilisateurs |
| [10. Vérifier l'installation](#10-vérifier-linstallation) | Deux questions de contrôle |
| [Le bac à sable vu du conteneur](#le-bac-à-sable-vu-du-conteneur) | Chemins et sécurité |

L'exploitation courante (journaux, sauvegarde, restauration, mise à jour) : [EXPLOITATION.md](EXPLOITATION.md).

## 1. Prérequis

| Élément | Rôle | Vérification |
|---|---|---|
| Docker avec le plugin `compose` | Exécute l'application et le bac à sable | `docker compose version` |
| systemd | Relance le service au démarrage | `systemctl --version` |
| `sudo` | Fichier d'environnement et unité sous `/etc` | `sudo -v` |
| Un LLM servi sur site, derrière une API compatible OpenAI avec appel d'outils | Le raisonnement de l'agent | `curl http://localhost:8100/v1/models` |
| Postgres | Seulement pour une source `postgres` | `pg_isready` |

- Le moteur LLM est installé séparément. Réglages requis pour l'appel d'outils : [MOTEUR.md](MOTEUR.md).
- Python, `uv` et les dépendances sont dans l'image : la machine n'a besoin que de Docker.

## 2. Récupérer le dépôt

```bash
sudo git clone <url-du-dépôt> /opt/data-analyst-agent
sudo chown -R "$USER" /opt/data-analyst-agent
cd /opt/data-analyst-agent
```

`/opt/data-analyst-agent` est le chemin écrit dans `deploy/daa.service`. Un autre emplacement impose de corriger ce fichier avant l'étape 7.

## 3. Le fichier d'environnement

Les secrets vivent dans ce fichier, en `0600`, lisible par `root` seul, hors du dépôt.

```bash
sudo install -D -m 0600 deploy/daa.env.example /etc/data-analyst-agent/daa.env
sudo ${EDITOR:-nano} /etc/data-analyst-agent/daa.env
```

Valeurs obligatoires :

| Variable | Valeur | Obtention |
|---|---|---|
| `DAA_DOCKER_GID` | gid du groupe propriétaire de la socket Docker | `stat -c %g /var/run/docker.sock` |
| `DAA_LLM_BASE_URL` | URL du moteur, vue depuis le conteneur | `http://host.docker.internal:8100/v1` (et non `localhost`) |
| `DAA_LLM_MODEL` | nom du modèle servi | `curl http://localhost:8100/v1/models` |

Pour une source Postgres : `DAA_PG_HOST` (`host.docker.internal` si la base est sur la machine), `DAA_PG_PORT`, `DAA_PG_USER`, `DAA_PG_PASSWORD`.

- Les autres variables sont documentées dans [`deploy/daa.env.example`](../deploy/daa.env.example).
- Les chemins applicatifs doivent rester sous `DAA_DATA_DIR` ([Le bac à sable vu du conteneur](#le-bac-à-sable-vu-du-conteneur)).
- Ce fichier est distinct du `.env` de développement : le service ne lit que lui.

## 4. Le dossier de données

```bash
sudo deploy/daactl init
```

Crée `/var/lib/data-analyst-agent`, en `0700`, propriété de `1000:1000` (l'utilisateur du conteneur).
Ce dossier est tout ce qui persiste, et tout ce que la sauvegarde emporte.

| Élément | Contenu |
|---|---|
| `workspaces/` | Conversations, tableaux, figures, code |
| `sources/` | Catalogue et fichiers de données |
| `models/` | Registre et modèles de prédiction |
| `auth/` | Sessions et compteurs d'échecs de connexion |
| `tmp/` | Fichiers temporaires des tables SQL |
| `users.yaml` | Comptes (créé à l'étape 9) |

## 5. Déclarer une première source

Le catalogue vit dans le dossier de données, pas dans l'image.
Le catalogue livré sert de point de départ :

```bash
sudo cp -a sources/. /var/lib/data-analyst-agent/sources/
sudo cp -a models/. /var/lib/data-analyst-agent/models/
sudo chown -R 1000:1000 /var/lib/data-analyst-agent
```

Le fichier à éditer : `/var/lib/data-analyst-agent/sources/catalogue.yaml`.
Déclaration complète d'une source : [AJOUTER-UNE-SOURCE.md](AJOUTER-UNE-SOURCE.md).

## 6. Construire les images

```bash
sudo deploy/daactl build      # l'application
sudo deploy/daactl sandbox    # le bac à sable, s'il est absent
```

| Image | Rôle |
|---|---|
| `data-analyst-agent:0.1` | API, page de chat, orchestrateur |
| `data-analyst-agent-sandbox:0.1` | Exécution du code écrit par l'agent |

`daactl sandbox` ne reconstruit pas une image déjà présente.

## 7. Installer le service

```bash
sudo install -m 0644 deploy/daa.service /etc/systemd/system/daa.service
sudo systemctl daemon-reload
sudo systemctl enable --now daa
```

Vérification :

```bash
systemctl is-enabled daa    # enabled
systemctl is-active  daa    # active
curl http://127.0.0.1:8000/health
```

- `enable` relance le service au démarrage de la machine.
- À partir de là, `systemctl` commande le service ; `daactl start|stop|restart` lui délègue.
- Le port `8000` n'écoute que sur `127.0.0.1`. L'accès distant passe par HTTPS (étape 8).

## 8. Le certificat et l'exposition

Un mandataire `nginx` termine TLS devant l'application. Il ne démarre pas sans certificat.

```bash
sudo deploy/daactl tls
```

Fichiers créés sous `/etc/data-analyst-agent/tls` :

| Fichier | Rôle |
|---|---|
| `ca.crt` | Autorité locale, publique, à installer sur les postes |
| `ca.key` | Clé de l'autorité, `0600`, ne quitte pas la machine |
| `serveur.crt`, `serveur.key` | Certificat du service, valable 825 jours |

- Le certificat porte le nom de la machine et toutes ses adresses IPv4.
- Autres noms : `sudo deploy/daactl tls daa.interne.exemple autre-nom`.
- Choix de l'autorité locale, et alternatives : [EXPLOITATION.md](EXPLOITATION.md#exposer-le-service).

```bash
sudo systemctl restart daa
curl --cacert /etc/data-analyst-agent/tls/ca.crt https://$(hostname -f):8443/health
```

Le service écoute sur `8443` (HTTPS) et `8080` (redirection vers `8443`).

## 9. Créer le premier compte

Aucun compte n'existe par défaut, et l'inscription n'est pas ouverte.

```bash
sudo deploy/daactl users create alice
sudo deploy/daactl users list
```

- Autres commandes : `disable`, `enable`, `reset-password` (`sudo deploy/daactl users --help`).
- Pour un provisionnement scripté, `--stdin` lit le mot de passe depuis un fichier en `0600`, jamais depuis la ligne de commande.

## 10. Vérifier l'installation

Ouvrir `https://<nom-de-la-machine>:8443/`, se connecter, désigner une source, et poser deux questions.

| Question | Résultat attendu | Ce qui est vérifié |
|---|---|---|
| « Combien de passagers ont survécu, et combien au total ? » (source `titanic`) | 342 sur 891, avec un tableau | Accès au moteur, à la source, écriture du SQL |
| « Trace un histogramme de l'âge des passagers. » | Une figure | Lancement du bac à sable et montage des fichiers |

Si la première réussit et pas la seconde, voir [Le bac à sable vu du conteneur](#le-bac-à-sable-vu-du-conteneur).

Vérification en ligne de commande :

```bash
curl --cacert /etc/data-analyst-agent/tls/ca.crt -s https://$(hostname -f):8443/health
curl -s http://127.0.0.1:8000/health
sudo deploy/daactl status
sudo deploy/daactl logs --tail 20
```

Sans `--cacert`, `curl` refuse le certificat : l'autorité est locale, c'est attendu.

## Le bac à sable vu du conteneur

**Fonctionnement.**
Pour une analyse ou un graphique, l'application lance un conteneur frère, le bac à sable, et lui monte en lecture seule les fichiers nécessaires.
Ce lancement passe par la socket Docker de l'hôte : les chemins sont donc lus par l'hôte.

**Règle.**

> Un seul dossier de données, monté au même chemin absolu dans l'application et sur l'hôte. Tout fichier susceptible d'être monté vit dessous.

- `DAA_WORKSPACE_DIR`, `DAA_CATALOG_PATH`, `DAA_MODELS_REGISTRY_PATH` et `TMPDIR` pointent sous `DAA_DATA_DIR`.
- `compose.yaml` monte `${DAA_DATA_DIR}:${DAA_DATA_DIR}`.
- Un chemin placé ailleurs produit un montage vide, sans erreur.

**Sécurité.**
L'accès à la socket Docker équivaut à un accès administrateur à la machine.
Trois mesures encadrent ce risque :

1. **Pas de privilège supplémentaire.** L'application avait déjà ce pouvoir lorsqu'elle tournait hors conteneur.
2. **Le code généré ne s'exécute jamais dans l'application.** Il s'exécute dans le bac à sable : sans socket, sans réseau (`--network=none`), sans capabilities (`--cap-drop=ALL`), racine en lecture seule (`--read-only`), mémoire, CPU, processus et sessions bornés.
3. **Surface réduite.** L'application tourne en `1000:1000`, n'accède à la socket que par `group_add`, ne monte que son dossier de données et la socket, et n'écoute que sur `127.0.0.1`.

Amélioration possible : un démon Docker sans privilèges (*rootless*), ou un intermédiaire qui n'autorise que le lancement du bac à sable.
En attendant, l'accès au conteneur de l'application doit être traité comme un accès à l'hôte.

L'historique de la mise au point de cette procédure : [historique/mise-au-point-de-l-installation.md](historique/mise-au-point-de-l-installation.md).
