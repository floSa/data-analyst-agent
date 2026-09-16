"""Configuration de l'application (pydantic-settings, préfixe d'environnement DAA_)."""

import logging
import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from dotenv import dotenv_values
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Nom du fichier d'environnement, relu à CHAQUE lecture (jamais figé dans un
# argument par défaut ni dans `model_config`) : c'est le seul point par lequel
# la suite de tests peut se couper du `.env` du poste, cf. tests/conftest.py.
ENV_FILE = ".env"

logger = logging.getLogger("data_analyst_agent.config")

# Ancien nom du champ d'URL, du temps où le moteur s'appelait dans la
# configuration. Conservé en lecture seule : un `.env` en service ne doit pas
# cesser de marcher parce qu'on a renommé une variable.
URL_MOTEUR_DEPRECIEE = "DAA_OLLAMA_BASE_URL"
URL_MOTEUR = "DAA_LLM_BASE_URL"

# Même histoire pour le plafond du dictionnaire : il ne bornait que le prompt de
# l'agent SQL, il borne maintenant celui de tout agent qui lit le dictionnaire
# d'une source. Le nom d'avant survit en lecture seule.
PLAFOND_DICTIONNAIRE_DEPRECIE = "DAA_RETRIEVAL_DICTIONARY_MAX_CHARS"
PLAFOND_DICTIONNAIRE = "DAA_DICTIONARY_MAX_CHARS"


class Settings(BaseSettings):
    """Réglages globaux, surchargeables par variables d'environnement (``DAA_*``) ou ``.env``."""

    # Le `.env` n'est PAS déclaré ici : `get_settings()` est le seul point du
    # code qui le lit, et il le passe explicitement. Une porte plutôt que deux —
    # sinon `Settings()` lit le fichier du poste dans le dos de qui l'instancie,
    # à commencer par la suite de tests.
    model_config = SettingsConfigDict(env_prefix="DAA_", extra="ignore")

    # --- LLM mutualisé (docs/CADRAGE.md §5) ---
    # Un seul modèle langage pour tout le système.
    #
    # Le MOTEUR n'est plus nommé ici : l'application ne parle que
    # /v1/chat/completions, qu'Ollama comme vLLM servent. Changer de moteur,
    # c'est changer cette URL — et rien d'autre côté code (audit §4.1).
    llm_base_url: str = "http://localhost:11434/v1"
    # Ancien nom, gardé pour ne pas casser les `.env` déjà en service. Renseigné,
    # il alimente `llm_base_url` et se signale comme déprécié ; à retirer une
    # fois les fichiers d'environnement migrés.
    ollama_base_url: str | None = None
    # Clé d'API. Inutile avec Ollama, EXIGÉE par un vLLM lancé avec `--api-key` :
    # il rejette alors toute requête sans en-tête `Authorization`. Vide = pas
    # d'authentification (le SDK OpenAI pose sa clé factice, qu'il exige non
    # nulle même quand le serveur s'en moque).
    llm_api_key: str = ""
    # Le modèle réellement servi par le central. CADRAGE §5 visait
    # qwen3-coder:30b, qui n'a jamais été chargé : ce repli échouait donc en
    # `404 model not found`, et le masquage des erreurs ne laissait qu'un
    # « je n'ai pas réussi à interpréter la demande » dans la réponse. Le repli
    # doit désigner ce qui existe ; le `.env` reste maître.
    llm_model: str = "gemma4:e4b"
    llm_temperature: float = 0.0
    # Délai d'un appel LLM et nombre de réessais. Les défauts du SDK OpenAI
    # (600 s, 2 réessais) n'étaient pas une décision : un appel bloqué retenait
    # un thread du pool jusqu'à ~30 min, trois essais compris. 120 s ramène ce
    # pire cas à 6 min. Le réessai ne concerne que le transitoire (429, 5xx,
    # coupure) : un refus de contexte (400) n'est jamais rejoué.
    llm_timeout: float = 120.0
    llm_max_retries: int = 2

    # --- Agent Récupération (docs/CADRAGE.md §7-①) ---
    catalog_path: Path = Path("sources/catalogue.yaml")
    retrieval_max_rows: int = 200
    # Borne d'allers-retours LLM (tools compris) : coupe les boucles infinies.
    retrieval_request_limit: int = 10
    # --- Dictionnaire de source, injecté dans les prompts -------------------
    # Plafond, EN CARACTÈRES, du dictionnaire d'une source recopié dans le
    # prompt système de l'agent qui va s'en servir (cf. `agents/dictionnaire`).
    #
    # UN SEUL plafond pour DEUX lecteurs — celui qui écrit le SQL et celui qui
    # écrit le Python — et c'est un choix mesuré, pas une économie. Ce nombre ne
    # règle pas un agent : il règle ce qu'une SOURCE est capable de transmettre
    # de son sens. Deux valeurs distinctes diraient qu'un même dictionnaire est
    # amputé dans un prompt et entier dans l'autre — donc que « combien de
    # recharges ? » et « trace-moi les recharges » ne s'appuient pas sur le même
    # texte, alors que la règle de comptage est la même. Les deux prompts pèsent
    # d'ailleurs le même ordre de grandeur — mesuré au `/tokenize` de vLLM, avec
    # le dictionnaire de `telemetrie` : 2 580 tokens pour l'agent SQL, 2 199
    # pour celui d'analyse — et les deux boucles sont bornées
    # (`retrieval_request_limit` à 10, `analysis_max_attempts` à 3).
    #
    # 8 000 est choisi AU-DESSUS du plus gros dictionnaire du catalogue de
    # démonstration (6 797 caractères, `exploitation`) : les cinq passent
    # entiers, et la coupe reste un filet plutôt qu'un régime. Mesuré contre le
    # tokeniseur de vLLM, 8 000 caractères de ce Markdown valent ~2 550 tokens :
    # le plafond borne donc le prompt système de l'agent SQL à ~3 800 tokens et
    # celui de l'agent d'analyse à ~3 400, pour une fenêtre servie de 32 768.
    #
    # En caractères et non en tokens, comme `context_token_budget` compte des
    # caractères : il n'y a pas de tokeniseur côté client, et en embarquer un
    # ferait dépendre ce plafond du modèle servi.
    # 0 = pas de plafond (le dictionnaire passe entier, quelle que soit sa taille).
    dictionary_max_chars: int = 8000
    # Ancien nom, du temps où seul l'agent SQL lisait le dictionnaire. Gardé en
    # lecture pour ne pas casser un `.env` en service ; à retirer une fois les
    # fichiers migrés.
    retrieval_dictionary_max_chars: int | None = None

    # Relevé d'une source — volumétrie et période LUES dedans (cf.
    # `agents/retrieval/faits.py`). Quatre bornes, parce que sans elles un
    # inventaire attend indéfiniment une source muette et sert des chiffres du
    # démarrage jusqu'au redémarrage.
    #
    # DÉLAI maximal par source. Au-delà, la ligne est dégradée en « injoignable »
    # plutôt qu'attendue : le relevé est ce qui s'affiche AVANT la première
    # question, et une source muette bloquait l'inventaire entier sans plafond
    # (mesuré : toujours bloqué au bout de 75 s, le temps du délai TCP du
    # système). 10 s, c'est déjà deux à trois fois un tour de modèle complet.
    # 0 = pas de délai.
    releve_delai: float = 10.0
    # PÉREMPTION d'un relevé réussi. Une volumétrie sert à CHOISIR une source,
    # pas à répondre : elle bouge à l'échelle du chargement nocturne, pas de la
    # minute. Un quart d'heure borne la fraîcheur sans refaire les comptages à
    # chaque inventaire d'une même séance de travail. 0 = aucune mise en cache.
    releve_peremption: float = 900.0
    # REPRISE d'une source injoignable — bien plus courte que la péremption, et
    # c'est tout l'enjeu : une panne ne se mesure pas en quarts d'heure, elle se
    # répare en minutes. Observé le 2026-09-14 : Postgres arrêté au démarrage,
    # relancé quelques minutes plus tard, et la réponse annonçait encore
    # « volumétrie non relevée ». Le coût d'une reprise inutile est une
    # connexion refusée, immédiate. 0 = aucune mise en cache.
    releve_reprise: float = 30.0
    # SEUIL au-delà duquel le nombre de lignes d'une table est ESTIMÉ par le
    # moteur (`reltuples`) plutôt que compté. Mesuré sur 5 M de lignes Postgres :
    # 88 ms de `count(*)` contre 1,5 ms de lecture du catalogue, pour la même
    # valeur. En dessous du seuil, le comptage exact est de toute façon gratuit
    # et reste exact — on ne dégrade pas sans contrepartie. Sans effet sur une
    # base DuckDB, qui répond `count(*)` depuis ses métadonnées : la liste des
    # moteurs concernés, et pourquoi, est dans `faits.ESTIMATIONS`.
    # 0 = jamais d'estimation.
    releve_seuil_approximation: int = 100_000

    # --- Agent Système (questions SUR l'agent : cf. orchestrator/systeme.py) ---
    # Bien plus court que celui de la récupération, et pour une raison : l'agent
    # système n'a pas de boucle de correction à mener. Un appel par outil qu'il
    # décide d'ouvrir, un dernier pour formuler ce qu'ils ont rendu.
    #
    # 4 pendant longtemps, et 4 était serré : une question sur trente-six —
    # « sur quoi je peux travailler ? » — l'épuisait sous Ollama et tombait dans
    # le repli du planificateur (« request_limit of 4 »), alors qu'elle passait
    # sous vLLM. Ce n'était pas une boucle : la question ouvre sur TOUS les
    # sujets, et Ollama y répond en regardant tout — capacités, sources, deux
    # fois le schéma, modèles. Cinq outils, donc six allers-retours. vLLM
    # répond à la même question avec un seul outil.
    #
    # 6 est la plus basse valeur qui rend 36/36 sur les DEUX moteurs (5 échoue
    # encore), et elle est GRATUITE : batterie complète, 83 appels sous Ollama
    # à 4 comme à 6, 78 sous vLLM aux trois valeurs, et pas une question dont
    # le coût bouge. Un plafond n'est pas un budget dépensé — c'est un budget
    # disponible, et seule la question qui en avait besoin le touche. L'atteindre
    # coûtait d'ailleurs plus cher que de réussir : l'échec ajoute le tour du
    # planificateur et celui de la synthèse.
    systeme_request_limit: int = 6

    # --- Agent de rappel (artefacts du fil : cf. orchestrator/rappel.py) ---
    # Un peu plus large que celui de l'agent système, parce que sa boucle est
    # plus longue d'un cran : reconnaître l'artefact, le relire, le rejouer,
    # formuler. Il n'est sollicité que dans une conversation qui a DÉJÀ produit
    # quelque chose — un fil neuf ne le paie pas.
    rappel_request_limit: int = 5

    # --- Agent Analyse (docs/CADRAGE.md §7-②) ---
    analysis_max_attempts: int = 3
    # Nb max de lignes matérialisées par table quand on analyse une source SQL.
    analysis_table_max_rows: int = 10000

    # --- Inférence (docs/CADRAGE.md §7-③) ---
    models_registry_path: Path = Path("models/registry.yaml")

    # --- Mémoire de conversation (objets intermédiaires persistés) ---
    # Chaque conversation persiste ses tableaux intermédiaires (CSV) sous un
    # sous-dossier de ce répertoire ; ils sont réexposés aux tours suivants
    # (sources éphémères, sandbox du code généré).
    # Sous le projet, et non dans /tmp : le contenu de /tmp est purgé (10 jours
    # sur la machine de dev) et lisible par tout compte local, alors qu'on y
    # écrit les questions des utilisateurs et leurs données. Surchargeable par
    # DAA_WORKSPACE_DIR pour pointer un volume dédié en production.
    workspace_dir: Path = Path("var/workspaces")

    # --- Ce qui entre dans le contexte du modèle (docs/AUDIT-2026-09.md §3.4) ---
    # Les tableaux intermédiaires d'une conversation sont réinjectés à chaque
    # tour sur TROIS axes : prompt du planificateur, montages de la sandbox,
    # catalogue effectif. Sans plafond, la liste grandit indéfiniment (mesuré :
    # 100 montages `--volume` et ~13 000 caractères de catalogue à 100 tours).
    # Fenêtre glissante : on réinjecte les N plus récents. 0 = pas de fenêtre.
    # Les objets évincés RESTENT sur le disque : on plafonne ce qu'on injecte,
    # pas ce qu'on conserve.
    context_artifact_window: int = 8
    # Même fenêtre, pour le CODE des analyses et des figures. Séparée, et c'est
    # délibéré : un tableau retenu coûte un montage `--volume`, une source
    # éphémère et la liste de ses colonnes dans le prompt ; un code retenu coûte
    # UNE ligne de catalogue — son contenu n'entre jamais dans le prompt, il
    # s'ouvre à la demande par un outil. Les faire partager la fenêtre des
    # tableaux ferait évincer la figure du tour 1 au bout de quatre tours qui
    # produisent chacun un tableau, c'est-à-dire exactement ce qu'on corrige :
    # « reprends le graphe de tout à l'heure » doit marcher au tour +2, pas
    # seulement au tour +1. 0 = pas de fenêtre.
    context_code_window: int = 8
    # Budget de tokens du prompt du planificateur, décompté AVANT l'appel. Il
    # borne ce que la fenêtre seule ne borne pas : un catalogue déclaré volumineux
    # ou une question très longue. Au dépassement, les objets intermédiaires les
    # plus ANCIENS sont retirés jusqu'à ce que ça tienne — la dégradation est
    # ordonnée, jamais subie. À tenir NETTEMENT sous la fenêtre du serveur
    # (`OLLAMA_CONTEXT_LENGTH`, 32768 sur le service central) : le budget ne
    # couvre que le planificateur, les autres agents ajoutent leurs propres tours.
    # 0 désactive le budget.
    context_token_budget: int = 8000
    # Fenêtre de contexte RÉELLEMENT servie par le serveur (Ollama :
    # `OLLAMA_CONTEXT_LENGTH`, 32768 sur le service central — et non les 131 072
    # que déclare gemma4). Elle ne règle rien côté client : elle sert à
    # CONSTATER un débordement, en confrontant `prompt_eval_count` à ce qu'on a
    # envoyé. 0 = fenêtre inconnue, la détection se rabat sur l'écart grossier.
    context_model_window: int = 32768
    # Filet quand la fenêtre est inconnue ou mal déclarée : on signale si le
    # serveur dit avoir évalué moins de cette fraction de notre estimation.
    # 0,4 est très en dessous de ce que l'imprécision du compteur peut
    # expliquer (mesurée, elle ne descend jamais sous 0,85).
    context_overflow_ratio: float = 0.4

    # --- Authentification (login + mot de passe, session côté serveur) ---
    # Magasin de comptes : logins et empreintes argon2id. NON VERSIONNÉ, écrit
    # en 0600, peuplé uniquement par scripts/manage_users.py — il n'y a ni
    # inscription ouverte ni compte par défaut. Cf. users.example.yaml.
    auth_accounts_path: Path = Path("var/users.yaml")
    # État d'authentification : sessions ouvertes et compteurs d'échecs.
    auth_state_dir: Path = Path("var/auth")
    session_cookie_name: str = "daa_session"
    csrf_cookie_name: str = "daa_csrf"
    # Inactivité : ferme un poste laissé ouvert (1 h). Durée absolue : borne une
    # session qu'un onglet maintiendrait vivante indéfiniment (12 h).
    session_idle_timeout: float = 3600.0
    session_absolute_timeout: float = 43200.0
    # Défaut SÛR : le cookie de session ne part que sur HTTPS. À passer à false
    # UNIQUEMENT pour un développement local en http, jamais en service.
    session_cookie_secure: bool = True
    # Anti-force brute : au-delà de N échecs, verrouillage temporisé, compté par
    # compte ET par adresse.
    login_max_failures: int = 5
    login_lockout_seconds: float = 300.0

    # --- Mandataires de confiance (terminaison TLS devant l'application) ---
    # Les réseaux depuis lesquels `X-Forwarded-For` et `X-Forwarded-Proto` sont
    # crus. VIDE par défaut, et ce défaut est le sûr : ces en-têtes sont posés
    # par un client, donc falsifiables, et les croire sans condition offrirait à
    # un attaquant le choix du compteur d'anti-force brute sur lequel inscrire
    # ses échecs. Cf. `api/forwarded.py`.
    #
    # À renseigner UNIQUEMENT avec le réseau du mandataire — celui du compose,
    # pas « tout le privé ». Liste séparée par des virgules, adresse nue ou CIDR :
    #     DAA_TRUSTED_PROXIES=172.30.0.0/24
    trusted_proxies: Annotated[list[str], NoDecode] = []

    # --- Surface HTTP exposée (docs/AUDIT-2026-09.md §6.3) ---
    # Documentation interactive (/docs, /redoc, /openapi.json). ÉTEINTE par
    # défaut : elle décrit la surface d'attaque à qui atteint le port, et ses
    # trois pages chargent Swagger/ReDoc depuis un CDN — ce qu'un déploiement
    # au réseau coupé ne peut de toute façon pas servir. À rallumer pour
    # développer, jamais en service.
    api_docs_enabled: bool = False
    # Taille maximale du corps d'une requête, tous chemins confondus. Le plus
    # gros corps légitime est un tour de chat ; 64 Kio laissent de la marge.
    api_max_body_bytes: int = 65536
    # Longueur maximale d'une question. `POST /chat` déclenche jusqu'à 11 appels
    # LLM et un conteneur Docker : sans borne, c'est un amplificateur de charge
    # gratuit. 4 000 caractères, c'est ~1 000 tokens — déjà large devant le
    # budget de contexte du planificateur.
    chat_message_max_chars: int = 4000
    # Débit maximal de `POST /chat`, par compte et par fenêtre glissante. Un
    # humain qui travaille pose quelques questions par minute ; 20 laissent
    # passer une rafale sans laisser passer un script.
    chat_rate_limit_requests: int = 20
    chat_rate_limit_window: float = 60.0

    # --- Sandbox d'exécution (docs/CADRAGE.md §6) ---
    # Commande docker ; surchargez p. ex. avec '["wsl", "docker"]' depuis Windows.
    sandbox_docker_cmd: list[str] = ["docker"]
    sandbox_image: str = "data-analyst-agent-sandbox:0.1"
    sandbox_mem_limit: str = "1g"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_start_timeout: float = 60.0
    sandbox_exec_timeout: float = 30.0
    # Marge accordée au conteneur pour interrompre proprement le kernel avant
    # que l'hôte ne le tue (timeout dur = exec_timeout + kill_grace).
    sandbox_kill_grace: float = 10.0
    # Conteneurs sandbox vivants EN MÊME TEMPS, tout le process confondu. Chaque
    # analyse ouvre sa propre session, donc son propre conteneur, qui réserve
    # `sandbox_mem_limit` : dix analyses simultanées, c'était dix conteneurs et
    # dix gigaoctets, sans file d'attente (audit §2.3). Au-delà du plafond, une
    # session attend son tour au lieu de s'ajouter. 4 x 1 Go tient sur une
    # machine de développement ; à régler sur la RAM réellement disponible.
    # 0 = pas de plafond (à ne poser qu'en connaissance de cause).
    sandbox_max_sessions: int = 4
    # Attente maximale d'une place. Passé ce délai, la demande est REFUSÉE plutôt
    # que mise en attente indéfinie : un utilisateur préfère un refus net à un
    # onglet qui tourne. Tenu sous le budget d'une requête HTTP.
    sandbox_queue_timeout: float = 60.0

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _liste_separee_par_virgules(cls, valeur):
        """Accepte `a,b` en plus du JSON, parce qu'un fichier d'environnement s'écrit à la main.

        `NoDecode` désactive le décodage JSON que pydantic-settings applique
        d'office aux champs composés : sans lui, `DAA_TRUSTED_PROXIES=172.30.0.0/24`
        ferait échouer le DÉMARRAGE sur une erreur de parsing, là où la valeur
        est parfaitement lisible. Une liste Python passe telle quelle : les tests
        et le code appelant ne sont pas obligés d'écrire une chaîne.
        """
        if not isinstance(valeur, str):
            return valeur
        return [morceau.strip() for morceau in valeur.split(",") if morceau.strip()]

    @model_validator(mode="after")
    def _reprendre_url_du_moteur_depreciee(self) -> "Settings":
        """Fait vivre l'ancien nom d'URL, en disant qu'il est déprécié.

        Le nouveau nom l'emporte s'il est renseigné explicitement : on ne veut
        pas qu'une variable oubliée dans un `.env` reprenne la main sur un
        réglage posé sciemment.

        Deux canaux, et c'est voulu : ``warnings`` pour le développeur et les
        tests, un log pour l'exploitant — les DeprecationWarning sont muettes
        par défaut, et c'est précisément lui qui doit migrer son fichier.
        """
        if self.ollama_base_url is None:
            return self
        if "llm_base_url" in self.model_fields_set:
            message = (
                f"{URL_MOTEUR_DEPRECIEE} est dépréciée et IGNORÉE ici : "
                f"{URL_MOTEUR} est renseignée et l'emporte. Retirez l'ancienne."
            )
        else:
            self.llm_base_url = self.ollama_base_url
            message = (
                f"{URL_MOTEUR_DEPRECIEE} est dépréciée : renommez-la {URL_MOTEUR}. "
                "Sa valeur est reprise pour cette exécution."
            )
        warnings.warn(message, DeprecationWarning, stacklevel=2)
        logger.warning(message)
        return self

    @model_validator(mode="after")
    def _reprendre_le_plafond_deprecie(self) -> "Settings":
        """Même mécanique pour le plafond du dictionnaire, et pour la même raison.

        Le réglage existait sous un nom d'agent parce qu'un seul agent lisait le
        dictionnaire. Un `.env` posé entre-temps ne doit pas cesser d'agir parce
        que le second lecteur est arrivé — un plafond qui redevient son défaut
        en silence, c'est un contexte qui gonfle sans que personne l'ait décidé.
        """
        if self.retrieval_dictionary_max_chars is None:
            return self
        if "dictionary_max_chars" in self.model_fields_set:
            message = (
                f"{PLAFOND_DICTIONNAIRE_DEPRECIE} est déprécié et IGNORÉ ici : "
                f"{PLAFOND_DICTIONNAIRE} est renseigné et l'emporte. Retirez l'ancien."
            )
        else:
            self.dictionary_max_chars = self.retrieval_dictionary_max_chars
            message = (
                f"{PLAFOND_DICTIONNAIRE_DEPRECIE} est déprécié : renommez-le "
                f"{PLAFOND_DICTIONNAIRE}. Sa valeur est reprise pour cette exécution."
            )
        warnings.warn(message, DeprecationWarning, stacklevel=2)
        logger.warning(message)
        return self


def export_env_file(env_file: str | Path | None = None) -> None:
    """Publie les variables du ``.env`` dans l'environnement du process.

    pydantic-settings lit le ``.env`` dans l'objet ``Settings``, mais **ne
    l'exporte pas**. Or toutes les variables du ``.env`` ne sont pas des champs
    de ``Settings`` : celles du DSN du catalogue (``DAA_PG_*``) sont résolues par
    ``os.path.expandvars``, qui ne lit que ``os.environ``. Sans cette passerelle,
    les renseigner dans le ``.env`` — ce que documente ``.env.example`` — reste
    sans effet et la source postgres échoue sur un ``${DAA_PG_PORT}`` littéral.

    L'environnement réel prime : on ne réécrit jamais une variable déjà posée.

    ``env_file`` vaut ``ENV_FILE`` à défaut — résolu à l'appel, pas à la
    définition, pour que le fichier reste substituable (tests).
    """
    for cle, valeur in dotenv_values(ENV_FILE if env_file is None else env_file).items():
        if valeur is not None:
            os.environ.setdefault(cle, valeur)


@lru_cache
def get_settings() -> Settings:
    """Instance partagée des réglages (cache process-wide).

    Seul point du code qui lit le ``.env`` : d'abord vers ``os.environ`` (les
    ``DAA_PG_*`` du DSN, que `Settings` ne porte pas), puis vers les champs.
    """
    export_env_file()
    return Settings(_env_file=ENV_FILE)
