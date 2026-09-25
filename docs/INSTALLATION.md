# Installer le service

Cette procédure s'adresse à quelqu'un **qui n'a jamais vu ce dépôt**. Elle part
d'une machine Linux nue et s'arrête à une question posée à une vraie source,
avec sa réponse. Elle a été suivie telle quelle, de bout en bout, depuis un
dossier vide ; ce qui y manquait a été corrigé et est consigné au §10.

Pour l'exploitation courante — journaux, sauvegarde, restauration, migration —
voir **[EXPLOITATION.md](EXPLOITATION.md)**.

- [1. Prérequis](#1-prérequis)
- [2. Récupérer le dépôt](#2-récupérer-le-dépôt)
- [3. Le fichier d'environnement](#3-le-fichier-denvironnement)
- [4. Le dossier de données](#4-le-dossier-de-données)
- [5. Déclarer une première source](#5-déclarer-une-première-source)
- [6. Construire les deux images](#6-construire-les-deux-images)
- [7. Installer le service](#7-installer-le-service)
- [7 bis. Le certificat, et l'exposition](#7-bis-le-certificat-et-lexposition)
- [8. Créer le premier compte](#8-créer-le-premier-compte)
- [9. Vérifier que tout marche](#9-vérifier-que-tout-marche)
- [10. Ce qui manquait à cette procédure](#10-ce-qui-manquait-à-cette-procédure)
- [Le bac à sable vu du conteneur](#le-bac-à-sable-vu-du-conteneur)

## 1. Prérequis

| Il faut | Pourquoi | Vérifier |
|---|---|---|
| **Docker** avec le plugin `compose` | l'application est un conteneur, et elle en lance un autre pour exécuter le code d'analyse | `docker compose version` |
| **systemd** | c'est lui qui relance l'application au démarrage de la machine | `systemctl --version` |
| **`sudo`** | le fichier d'environnement est en 0600 root, l'unité s'installe sous `/etc` | `sudo -v` |
| **un serveur LLM à endpoint OpenAI-compatible**, joignable depuis la machine | l'application n'embarque aucun modèle ; elle parle `/v1/chat/completions` | `curl http://localhost:8100/v1/models` |
| **Postgres**, si le catalogue déclare une source `postgres` | facultatif : une installation sur fichiers CSV/Excel n'en a pas besoin | `pg_isready` |

Le moteur **n'est pas installé par cette procédure** : c'est un service partagé,
monté à part (dépôt `llm-service`), que plusieurs applications joignent. En
service ici : vLLM sur le port 8100, servant
`google/gemma-4-E4B-it-qat-w4a16-ct`. Le banc d'essai et les options qui font
marcher le *tool calling* sont dans [MOTEUR.md](MOTEUR.md).

Ce qui n'est **pas** un prérequis : Python, `uv`, les dépendances. Elles vivent
dans l'image. La machine n'a besoin que de Docker.

## 2. Récupérer le dépôt

```bash
sudo git clone <url-du-dépôt> /opt/data-analyst-agent
sudo chown -R "$USER" /opt/data-analyst-agent
cd /opt/data-analyst-agent
```

`/opt/data-analyst-agent` n'est pas obligatoire, mais c'est le chemin écrit dans
`deploy/daa.service` : ailleurs, corriger les quatre lignes de ce fichier avant
l'étape 7.

## 3. Le fichier d'environnement

**C'est la seule étape où l'on écrit des secrets, et ils n'entrent jamais dans
le dépôt.** Le fichier vit sous `/etc`, en `0600`, lisible du seul `root`.

```bash
sudo install -D -m 0600 deploy/daa.env.example /etc/data-analyst-agent/daa.env
sudo ${EDITOR:-nano} /etc/data-analyst-agent/daa.env
```

Trois valeurs sont **obligatoires** ; sans elles le service refuse de démarrer
ou démarre inutile :

| Variable | Ce qu'on y met | Comment la trouver |
|---|---|---|
| `DAA_DOCKER_GID` | le gid du groupe propriétaire de la socket Docker | `stat -c %g /var/run/docker.sock` |
| `DAA_LLM_BASE_URL` | l'URL du moteur, **vue du conteneur** | `http://host.docker.internal:8100/v1` — pas `localhost`, qui désignerait le conteneur lui-même |
| `DAA_LLM_MODEL` | le modèle réellement servi | `curl http://localhost:8100/v1/models` |

Et, si le catalogue déclare une source Postgres : `DAA_PG_HOST`
(`host.docker.internal` si la base est sur la machine), `DAA_PG_PORT`,
`DAA_PG_USER`, `DAA_PG_PASSWORD`.

Le reste du fichier est documenté ligne à ligne dans
[`deploy/daa.env.example`](../deploy/daa.env.example). Les chemins applicatifs y
sont déjà posés et **doivent tous rester sous `DAA_DATA_DIR`** : la raison est au
[§ Le bac à sable vu du conteneur](#le-bac-à-sable-vu-du-conteneur).

> Ce fichier n'a rien à voir avec le `.env` du dossier de travail, qui sert au
> développement local. Le service ne lit que celui-ci.

## 4. Le dossier de données

```bash
sudo deploy/daactl init
```

Crée `/var/lib/data-analyst-agent` et ses cinq sous-dossiers, en `0700` et
appartenant à `1000:1000` — l'utilisateur du conteneur. C'est **tout ce qui
survit** à une reconstruction de l'image, et tout ce que la sauvegarde emporte :

| | |
|---|---|
| `workspaces/` | les conversations, leurs tableaux, leurs figures, leur code |
| `sources/` | le catalogue et les fichiers qu'il déclare |
| `models/` | le registre et les modèles `.joblib` |
| `auth/` | les sessions ouvertes et les compteurs d'échecs de connexion |
| `tmp/` | les CSV temporaires des tables SQL matérialisées |
| `users.yaml` | les comptes (créé à l'étape 8) |

## 5. Déclarer une première source

Le catalogue est une **déclaration locale** : il n'est ni dans l'image, ni
imposé par le dépôt. Le plus court chemin est de partir de celui qui est livré —
il déclare `titanic` (Postgres, multi-tables) et `iris` (CSV) :

```bash
sudo cp -a sources/. /var/lib/data-analyst-agent/sources/
sudo cp -a models/. /var/lib/data-analyst-agent/models/
sudo chown -R 1000:1000 /var/lib/data-analyst-agent
```

Pour déclarer **votre** source, éditer
`/var/lib/data-analyst-agent/sources/catalogue.yaml`. Une source de fichier tient
en quatre lignes, et le fichier doit être déposé à côté du catalogue :

```yaml
sources:
  - type: file
    name: ventes
    description: >-
      Ventes mensuelles par magasin — date, magasin, référence, quantité,
      chiffre d'affaires.
    path: ventes.csv        # relatif au catalogue, donc sous sources/
```

`description` n'est pas décoratif : c'est sur lui que le planificateur choisit la
source quand l'utilisateur ne la nomme pas.

**Le reste est dans son guide**, et n'est pas repris ici : les trois types avec
un exemple complet chacun, les quatre blocs facultatifs (`dictionary`,
`features`, `date_reference`, `filtre_des_sommes`), la règle du nom de colonne
partagé sans laquelle un croisement ne trouve pas sa clé, ce qu'il faut
redémarrer, et les questions qui vérifient —
**[AJOUTER-UNE-SOURCE.md](AJOUTER-UNE-SOURCE.md)**.

## 6. Construire les deux images

```bash
sudo deploy/daactl build      # l'application
sudo deploy/daactl sandbox    # le bac à sable, SI il n'est pas déjà là
```

Il y a **deux** images, et elles n'ont pas le même métier :

- `data-analyst-agent:0.1` — l'application. API, page de chat, orchestrateur ;
- `data-analyst-agent-sandbox:0.1` — le bac à sable, où s'exécute le code que le
  modèle écrit. C'est une image déjà décrite par le dépôt
  (`src/data_analyst_agent/sandbox/image/`) et construite par la CI. `daactl
  sandbox` ne la refait que si elle manque sur la machine ; si elle est là, il
  se contente de le dire.

Comptez quelques minutes la première fois. `daactl start` construit aussi ce qui
manque, mais le faire ici sépare une erreur de build d'une erreur de démarrage.

## 7. Installer le service

```bash
sudo install -m 0644 deploy/daa.service /etc/systemd/system/daa.service
sudo systemctl daemon-reload
sudo systemctl enable --now daa
```

`enable` est ce qui fait revenir l'application **après un redémarrage de la
machine** ; `--now` la démarre sans attendre le prochain. Vérifier :

```bash
systemctl is-enabled daa    # enabled
systemctl is-active  daa    # active
curl http://127.0.0.1:8000/health
```

À partir d'ici, **`systemctl` commande**, et `daactl start|stop|restart` lui
passe la main de lui-même (§10, manque n° 3).

Le port `8000` n'est ouvert que sur `127.0.0.1`, et **il le reste** : ce qui est
joignable depuis une autre machine, c'est la terminaison TLS de l'étape
suivante. Ce port-ci ne sert plus qu'à la sonde et au diagnostic depuis la
machine elle-même.

## 7 bis. Le certificat, et l'exposition

Le `compose` porte **deux** conteneurs : l'application, en clair sur
`127.0.0.1:8000`, et un mandataire `nginx` qui termine TLS et la relaie. Le
mandataire ne démarre pas sans son certificat — et `daactl start` refuse avant
lui, plutôt que de laisser lire l'erreur dans les journaux de nginx.

```bash
sudo deploy/daactl tls          # autorité locale + certificat de cette machine
```

La commande fabrique, sous `/etc/data-analyst-agent/tls` (jamais dans le dépôt,
clés en `0600 root`) :

| Fichier | Ce que c'est |
|---|---|
| `ca.crt` | l'**autorité locale**. Public. C'est lui, et lui seul, qu'on installe une fois sur les postes |
| `ca.key` | sa clé privée. Ne sort jamais de la machine |
| `serveur.crt` / `serveur.key` | le certificat réellement présenté, signé par l'autorité, valable 825 jours |

Le nom porté par le certificat est celui de la machine, et **toutes ses adresses
IPv4 y sont ajoutées** : sur un parc sans DNS interne, on joint le service par
son adresse, et un certificat sans SAN d'adresse serait refusé avant même
l'avertissement d'autorité inconnue. Pour d'autres noms :
`sudo deploy/daactl tls daa.interne.exemple autre-nom`.

**Pourquoi une autorité locale, et pas Let's Encrypt ni un auto-signé nu** — le
raisonnement complet est dans
[EXPLOITATION.md § Exposer le service](EXPLOITATION.md#exposer-le-service),
avec ce qu'il faut faire à la place si l'organisation a déjà une autorité
interne ou un nom public.

Puis démarrer, et vérifier que la chaîne entière répond :

```bash
sudo systemctl restart daa
curl --cacert /etc/data-analyst-agent/tls/ca.crt https://$(hostname -f):8443/health
```

Le service écoute alors sur **8443** (HTTPS) et **8080** (clair, qui ne fait que
rediriger vers 8443). Ces ports, le nom présenté, ce qu'on ouvre au pare-feu et
ce qu'on n'ouvre pas : [EXPLOITATION.md](EXPLOITATION.md#exposer-le-service).

## 8. Créer le premier compte

Il n'y a **ni inscription ouverte ni compte par défaut** — un compte livré avec
le code finit en production avec son mot de passe d'usine. Le premier compte se
crée à la main, et le mot de passe se tape au terminal :

```bash
sudo deploy/daactl users create alice
```

Puis, pour vérifier : `sudo deploy/daactl users list`. Les autres verbes
(`disable`, `enable`, `reset-password`) sont décrits par
`sudo deploy/daactl users --help`.

Pour un provisionnement scripté, `--stdin` lit le mot de passe sur l'entrée
standard — depuis un fichier en 0600, jamais depuis la ligne de commande, qui
est lisible par tout compte local (`ps`) et reste dans l'historique du shell.

## 9. Vérifier que tout marche

Ouvrir **https://\<le-nom-de-la-machine\>:8443/**, se connecter, choisir la
source `titanic` (ou la vôtre), et poser **deux** questions. Deux, et pas une : elles n'éprouvent
pas la même moitié du système.

**La première — la source répond.**

> Combien de passagers ont survécu, et combien au total ?

Attendu : « Le nombre total de passagers est de 891, et 342 d'entre eux ont
survécu », et un tableau sous la réponse. Ce qu'elle prouve : le conteneur joint
le moteur LLM, joint Postgres, et sait écrire du SQL dessus.

**La seconde — le bac à sable tourne.**

> Trace un histogramme de l'âge des passagers.

Attendu : une figure. Ce qu'elle prouve, et c'est le point délicat de toute
l'installation : l'application a lancé **un second conteneur** depuis le sien, y
a monté des fichiers par des chemins de l'hôte, et le code généré les a lus.
Si la première question marche et pas la seconde, le défaut est là — voir
ci-dessous.

En ligne de commande, la même vérification sans navigateur :

```bash
# la chaîne entière, telle qu'un poste la voit
curl --cacert /etc/data-analyst-agent/tls/ca.crt \
     -s https://$(hostname -f):8443/health     # {"status":"ok","version":"0.1.0"}
# l'application seule, sans traverser TLS : pour départager les deux quand ça coince
curl -s http://127.0.0.1:8000/health
sudo deploy/daactl status                     # les DEUX conteneurs, et « healthy »
sudo deploy/daactl logs --tail 20
```

Un certificat refusé par `curl` sans `--cacert` est **le comportement attendu** :
l'autorité est locale, et rien ne la connaît tant qu'on ne l'a pas installée.

## 10. Ce qui manquait à cette procédure

Elle a été suivie pour de vrai, depuis un dossier vide, sur une machine où rien
n'existait. Trois choses ont manqué, toutes corrigées ; elles sont consignées
parce qu'elles disent où le terrain est glissant.

**1. `TMPDIR` fuyait du fichier d'environnement vers l'hôte.** Le fichier pose
`TMPDIR` sous le dossier de données — les CSV temporaires des tables SQL doivent
être montables dans le bac à sable, donc exister sur l'hôte. Mais `daactl`
*source* ce fichier, et le CLI `docker` qu'il appelle en héritait : le tout
premier `build`, avant que l'arborescence n'existe, échouait sur
`invalid output path: stat /var/lib/data-analyst-agent/tmp: no such file or
directory`. Les trois scripts oublient maintenant `TMPDIR` après lecture ; le
conteneur, lui, le reçoit par `env_file`.

**2. Un catalogue absent ne se voyait pas.** Le dossier de données neuf n'en a
pas ; l'application démarrait, la page s'ouvrait, et l'absence de toute source ne
se lisait que dans les journaux. `daactl start` le dit maintenant en clair et
renvoie à l'étape 5. C'est aussi pourquoi l'étape 4 (`daactl init`) existe : sans
elle, la seule façon de créer l'arborescence était de démarrer le service, donc
de le démarrer une première fois sans source.

**3. `daactl start` court-circuitait systemd.** L'unité activée mais jamais
démarrée *par* systemd, `daactl start` lançait bien l'application — et
`systemctl stop daa` ne l'arrêtait pas : systemd tenait l'unité pour inactive,
donc n'exécutait aucun `ExecStop`. Le service tournait, systemd le disait éteint.
Un exploitant qui coupe avant une sauvegarde aurait sauvegardé un service en
marche sans le savoir. `daactl start|stop|restart` délègue désormais à
`systemctl` dès que l'unité est installée.

Ce qui n'a **pas** manqué, et méritait de l'être : le conteneur a joint le moteur
et Postgres du premier coup (`host.docker.internal`), et le bac à sable a produit
sa figure au premier essai.

## Le bac à sable vu du conteneur

C'est le seul endroit de cette installation qui demande de comprendre quelque
chose plutôt que de recopier une commande.

**Le mécanisme.** L'application ne calcule pas chez elle. Quand une question
demande une statistique ou une figure, elle lance un **conteneur frère** — le bac
à sable — et lui monte en lecture seule les fichiers dont le code aura besoin :
le CSV de la source, les tableaux des tours précédents, les tables SQL
matérialisées. Ce `docker run` part de l'application, mais il est exécuté par le
démon **de l'hôte**, à travers `/var/run/docker.sock`.

**La conséquence.** Les chemins que l'application donne au démon sont lus par
l'hôte, pas par elle. Un fichier qu'elle voit en `/data/x.csv` dans son propre
conteneur, mais qui n'existe pas à ce chemin sur l'hôte, donne un montage vide
— sans erreur. D'où la règle qui tient toute la configuration :

> **Un seul dossier de données, monté au même chemin absolu des deux côtés, et
> tout ce qui peut finir monté vit dessous.**

C'est pourquoi `DAA_WORKSPACE_DIR`, `DAA_CATALOG_PATH`,
`DAA_MODELS_REGISTRY_PATH` et `TMPDIR` pointent tous sous `DAA_DATA_DIR`, et
pourquoi `compose.yaml` monte `${DAA_DATA_DIR}:${DAA_DATA_DIR}` et non
`${DAA_DATA_DIR}:/data`. Déplacer l'un d'eux ailleurs casse le bac à sable, et le
casse silencieusement.

**Et la sécurité ?** Il faut le dire net : **donner la socket Docker à un
conteneur, c'est lui donner la machine.** Qui peut parler à ce démon peut lancer
un conteneur privilégié montant `/`, donc devenir root sur l'hôte. Aucune option
de `compose.yaml` n'y change quoi que ce soit.

Ce qui rend la chose tenable ici tient en trois points :

1. **Le privilège n'augmente pas.** L'application avait déjà exactement ce
   pouvoir quand elle tournait à la main sur la machine, lancée par un compte du
   groupe `docker`. La mettre en conteneur ne le lui donne pas : elle le
   conserve. Le point de comparaison n'est pas « un conteneur ordinaire », c'est
   « ce que faisait l'application hier ».
2. **Ce n'est pas là que tourne le code non fiable.** Le code écrit par le modèle
   ne s'exécute **jamais** dans le conteneur de l'application. Il s'exécute dans
   le bac à sable, qui n'a ni la socket, ni le réseau (`--network=none`), ni de
   capabilities (`--cap-drop=ALL`), ni de racine inscriptible (`--read-only`), et
   dont la mémoire, le CPU, les PIDs et le nombre de sessions simultanées sont
   bornés. La frontière de confiance est là, et elle n'a pas bougé.
3. **La surface est réduite à ce qui sert.** Le conteneur tourne en `1000:1000`,
   pas en root ; il n'obtient l'accès à la socket que par `group_add` ; il ne
   monte **aucun** chemin de l'hôte hormis son dossier de données et cette
   socket ; et son port n'écoute que sur `127.0.0.1`.

Ce qui resterait à faire, et qui n'est pas fait : un démon Docker *rootless*, ou
un service tiers qui n'accepterait que le `docker run` du bac à sable, feraient
tomber le point 1. Tant que ce n'est pas en place, **l'accès à l'hôte et l'accès
au conteneur de l'application se valent** — et le compte qui peut atteindre l'un
doit être traité comme s'il avait l'autre.
