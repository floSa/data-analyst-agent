"""Tests des réglages (pydantic-settings) et de l'isolation d'environnement.

Ce fichier testait ``Settings``, pas la sandbox : il vivait sous
``tests/unit/sandbox/``. Renommé plutôt que déplacé tel quel — les dossiers de
tests n'ont pas de ``__init__.py``, deux ``test_config.py`` de même basename
donneraient un « import file mismatch » à pytest.
"""

import os
import re
from pathlib import Path

import pytest

import conftest
from data_analyst_agent import config
from data_analyst_agent.config import Settings, export_env_file, get_settings


def make_settings(**overrides) -> Settings:
    """Settings isolés du .env et de l'environnement du développeur."""
    return Settings(_env_file=None, **overrides)


def test_valeurs_par_defaut():
    settings = make_settings()
    assert settings.sandbox_docker_cmd == ["docker"]
    assert settings.sandbox_image.startswith("data-analyst-agent-sandbox")
    assert settings.sandbox_exec_timeout > 0


def test_surcharge_par_environnement(monkeypatch):
    monkeypatch.setenv("DAA_SANDBOX_IMAGE", "sandbox-perso:dev")
    monkeypatch.setenv("DAA_SANDBOX_CPUS", "2.5")
    settings = Settings(_env_file=None)
    assert settings.sandbox_image == "sandbox-perso:dev"
    assert settings.sandbox_cpus == 2.5


def test_get_settings_est_un_cache():
    assert get_settings() is get_settings()


def test_export_env_file_publie_les_variables_hors_settings(tmp_path, monkeypatch):
    """Les DAA_PG_* du .env doivent atteindre os.environ : le DSN du catalogue est
    résolu par os.path.expandvars, qui ne lit que l'environnement réel."""
    env = tmp_path / ".env"
    env.write_text("DAA_PG_PORT=5432\nDAA_PG_USER=postgres\n", encoding="utf-8")
    monkeypatch.delenv("DAA_PG_PORT", raising=False)
    monkeypatch.delenv("DAA_PG_USER", raising=False)

    export_env_file(env)

    assert os.environ["DAA_PG_PORT"] == "5432"
    assert os.environ["DAA_PG_USER"] == "postgres"


def test_export_env_file_ne_recouvre_pas_lenvironnement_reel(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DAA_PG_PORT=5432\n", encoding="utf-8")
    monkeypatch.setenv("DAA_PG_PORT", "6543")  # posé explicitement : doit primer

    export_env_file(env)

    assert os.environ["DAA_PG_PORT"] == "6543"


def test_workspace_dir_par_defaut_sous_le_projet():
    """Pas dans /tmp : purgé périodiquement (10 jours sur la machine de dev) et
    lisible par tout compte local, alors qu'on y écrit les questions des
    utilisateurs et les données qu'ils font remonter.

    Un défaut ne se lit qu'à vide : ni le `.env` du poste ni une `DAA_*` exportée
    dans le shell ne doivent être là. C'est la fixture d'isolation de
    tests/conftest.py qui l'assure, et plus un `delenv` par test.
    """
    defaut = make_settings().workspace_dir

    assert defaut == Path("var/workspaces")
    assert not defaut.is_absolute()  # relatif au projet, comme catalog_path


def test_daa_workspace_dir_prime_sur_le_defaut(monkeypatch, tmp_path):
    """L'instance en service pointe un volume dédié : changer le défaut ne doit
    pas reprendre la main sur la variable d'environnement."""
    monkeypatch.setenv("DAA_WORKSPACE_DIR", str(tmp_path / "persist"))

    assert Settings(_env_file=None).workspace_dir == tmp_path / "persist"


# -- isolation d'environnement (fixture autouse de tests/conftest.py) ----------


def test_get_settings_publie_le_env_du_poste_dans_lenvironnement(tmp_path, monkeypatch):
    """Le comportement à contenir : get_settings() a un effet de bord DURABLE.

    C'est légitime (le DSN du catalogue passe par ``os.path.expandvars``), mais
    ça fait fuir la configuration du poste dans tous les tests suivants.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DAA_WORKSPACE_DIR=/volume/du/poste\n", encoding="utf-8")
    # La fixture d'isolation pointe ENV_FILE sur un fichier inexistant : ce test-ci
    # étudie justement le mécanisme, il repose donc la constante.
    monkeypatch.setattr(config, "ENV_FILE", ".env")
    get_settings.cache_clear()

    get_settings()

    assert os.environ["DAA_WORKSPACE_DIR"] == "/volume/du/poste"
    # et le poison atteint jusqu'aux Settings censés être isolés du .env
    assert Settings(_env_file=None).workspace_dir == Path("/volume/du/poste")


def test_la_fixture_disolation_rend_lenvironnement_au_test_suivant():
    """Sans elle, un test qui lit une valeur par défaut passe seul et échoue dans
    la suite complète, selon l'ordre de collecte de pytest."""
    fixture = conftest.environnement_isole.__wrapped__()
    next(fixture)  # entrée : l'environnement d'origine est mémorisé

    os.environ["DAA_TEMOIN_FUITE"] = "posee-par-le-test"
    os.environ.pop("PATH", None)  # une variable supprimée doit revenir aussi
    with pytest.raises(StopIteration):
        next(fixture)  # sortie de la fixture

    assert "DAA_TEMOIN_FUITE" not in os.environ
    assert "PATH" in os.environ


def test_la_fixture_disolation_retire_le_env_du_poste_a_lentree(monkeypatch):
    """Rendre l'environnement en sortie ne suffit pas : la fuite précède le
    premier test. Importer ``api/app.py`` exécute son ``app = create_app()`` de
    niveau module — donc get_settings() — dès la collecte ; l'état « avant » que
    chaque test mémorise est donc déjà pollué. Il faut le retirer à l'entrée."""
    monkeypatch.setenv("DAA_LLM_BASE_URL", "http://poste:8100/v1")
    fixture = conftest.environnement_isole.__wrapped__()

    next(fixture)  # entrée : la variable du poste doit disparaître
    retiree = "DAA_LLM_BASE_URL" not in os.environ
    with pytest.raises(StopIteration):
        next(fixture)  # sortie : et revenir telle quelle

    assert retiree
    assert os.environ["DAA_LLM_BASE_URL"] == "http://poste:8100/v1"


def test_la_fixture_disolation_coupe_get_settings_du_fichier_du_poste():
    """Retirer les variables ne suffit pas non plus : get_settings() relit le
    `.env` à chaque appel et republierait la configuration du poste au beau
    milieu du test. La fixture lui retire le fichier."""
    assert not Path(conftest.ENV_FILE_NEUTRE).exists()

    assert get_settings().workspace_dir == Path("var/workspaces")
    assert "DAA_WORKSPACE_DIR" not in os.environ


def test_la_fixture_disolation_vide_le_cache_de_get_settings():
    """Un Settings construit sous l'environnement d'un test ne doit pas servir au
    suivant : le cache est vidé à l'entrée comme à la sortie."""
    premier = get_settings()
    fixture = conftest.environnement_isole.__wrapped__()

    next(fixture)

    assert get_settings() is not premier


# -- moteur LLM : le nom du serveur sort de la configuration -------------------


def test_url_du_moteur_par_defaut():
    """SANS `.env`, l'application vise le service en place — pas un autre port.

    C'est le défaut du CODE qui est éprouvé ici, et lui seul : les campagnes
    tournent avec le `.env` recopié et ne prouveraient rien de cette ligne.
    ``_env_file=None`` coupe le fichier ; la fixture d'isolation coupe les
    variables du poste.
    """
    assert Settings(_env_file=None).llm_base_url == "http://localhost:8100/v1"


def test_modele_par_defaut():
    """Même raison, pour le modèle : le repli doit désigner ce qui est servi.

    Un repli qui nomme un modèle absent échoue en ``404 model not found``, et le
    masquage des erreurs n'en laisse qu'un « je n'ai pas réussi à interpréter la
    demande » dans la réponse — un symptôme qui ne dit pas sa cause.
    """
    assert Settings(_env_file=None).llm_model == "google/gemma-4-E4B-it-qat-w4a16-ct"


def test_le_env_prime_sur_le_defaut(monkeypatch):
    """Le défaut est un repli, pas un verrou : l'environnement reste maître."""
    monkeypatch.setenv("DAA_LLM_BASE_URL", "http://autre:8101/v1")
    monkeypatch.setenv("DAA_LLM_MODEL", "un/autre-modele")

    settings = Settings(_env_file=None)

    assert settings.llm_base_url == "http://autre:8101/v1"
    assert settings.llm_model == "un/autre-modele"


# -- plafond du dictionnaire : un réglage qui a changé de nom en changeant de
# -- portée (d'un agent à deux lecteurs) --------------------------------------


def test_plafond_du_dictionnaire_par_defaut():
    """Au-dessus du plus gros dictionnaire de la démonstration : la coupe est un filet."""
    assert make_settings().dictionary_max_chars == 8000


def test_ancien_plafond_du_dictionnaire_toujours_honore(monkeypatch):
    """Un `.env` posé du temps où seul l'agent SQL lisait le dictionnaire.

    Un plafond qui redeviendrait son défaut en silence, c'est un contexte qui
    gonfle sans que personne l'ait décidé — et sur le prompt qui se paie le plus
    de fois par tour.
    """
    monkeypatch.setenv("DAA_RETRIEVAL_DICTIONARY_MAX_CHARS", "2000")

    with pytest.deprecated_call():
        settings = Settings(_env_file=None)

    assert settings.dictionary_max_chars == 2000


def test_ancien_plafond_signale_dans_les_logs(monkeypatch, caplog):
    monkeypatch.setenv("DAA_RETRIEVAL_DICTIONARY_MAX_CHARS", "2000")

    with caplog.at_level("WARNING"), pytest.deprecated_call():
        Settings(_env_file=None)

    assert "DAA_DICTIONARY_MAX_CHARS" in caplog.text


def test_nouveau_plafond_prime_sur_lancien(monkeypatch):
    monkeypatch.setenv("DAA_RETRIEVAL_DICTIONARY_MAX_CHARS", "2000")
    monkeypatch.setenv("DAA_DICTIONARY_MAX_CHARS", "5000")

    with pytest.deprecated_call():
        settings = Settings(_env_file=None)

    assert settings.dictionary_max_chars == 5000


def test_aucun_avertissement_sans_lancien_plafond(recwarn):
    assert Settings(_env_file=None).retrieval_dictionary_max_chars is None
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_defauts_dappel_llm_bornent_lattente():
    """600 s x 3 essais, c'est ~30 min de thread retenu par un appel bloque."""
    settings = make_settings()

    assert 0 < settings.llm_timeout <= 300
    assert settings.llm_max_retries <= 2
    assert settings.llm_api_key == ""  # aucune authentification exigee par defaut


# -- authentification ---------------------------------------------------------


def test_defauts_dauthentification_sont_surs():
    """Les défauts doivent être ceux d'un service exposé, pas ceux d'un poste de dev."""
    settings = make_settings()

    assert settings.session_cookie_secure is True  # cookie de session en HTTPS seulement
    assert settings.session_idle_timeout > 0  # une session inactive finit par se fermer
    assert settings.session_absolute_timeout > settings.session_idle_timeout
    assert settings.login_max_failures >= 3  # la force brute est plafonnée
    assert settings.login_lockout_seconds > 0
    assert settings.auth_accounts_path == Path("var/users.yaml")  # sous var/, non versionné
    assert settings.auth_state_dir == Path("var/auth")


def test_secure_du_cookie_desactivable_pour_le_dev_local(monkeypatch):
    """Le développement local se fait en http : le cookie doit pouvoir suivre,
    par réglage explicite et jamais par défaut."""
    monkeypatch.setenv("DAA_SESSION_COOKIE_SECURE", "false")

    assert Settings(_env_file=None).session_cookie_secure is False


# -- .env.example, la seule doc que l'exploitant a sous la main ----------------


def test_chaque_reglage_est_documente_dans_env_example():
    """Un réglage ajouté au code et nulle part ailleurs n'existe pour personne.

    `.env.example` est le fichier qu'on copie pour configurer une instance : un
    champ de `Settings` absent d'ici est un plafond, un délai ou un interrupteur
    de sécurité que l'exploitant ne saura jamais qu'il pouvait régler. Ce test
    est là parce que l'oubli est arrivé.
    """
    texte = (conftest.RACINE / ".env.example").read_text(encoding="utf-8")
    cites = set(re.findall(r"DAA_[A-Z0-9_]+", texte))

    attendus = {f"DAA_{nom.upper()}" for nom in Settings.model_fields}

    assert not attendus - cites


# -- mandataires de confiance --------------------------------------------------


def test_aucun_mandataire_nest_cru_par_defaut():
    """Le défaut sûr : sans déclaration, `X-Forwarded-For` ne vaut rien.

    L'inverse offrirait à n'importe qui le choix du compteur d'anti-force brute
    sur lequel inscrire ses échecs — donc l'exemption du verrouillage.
    """
    assert make_settings().trusted_proxies == []


def test_les_mandataires_secrivent_separes_par_des_virgules(monkeypatch):
    """Un fichier d'environnement s'écrit à la main : exiger du JSON y ferait échouer
    le DÉMARRAGE sur une valeur parfaitement lisible."""
    monkeypatch.setenv("DAA_TRUSTED_PROXIES", "172.30.0.0/24, 10.1.3.9")

    assert Settings(_env_file=None).trusted_proxies == ["172.30.0.0/24", "10.1.3.9"]


def test_un_seul_mandataire_sans_virgule(monkeypatch):
    monkeypatch.setenv("DAA_TRUSTED_PROXIES", "172.30.0.0/24")

    assert Settings(_env_file=None).trusted_proxies == ["172.30.0.0/24"]


def test_une_valeur_vide_ne_declare_aucun_mandataire(monkeypatch):
    """`DAA_TRUSTED_PROXIES=` laissé vide dans le fichier ne doit pas déclarer `['']`."""
    monkeypatch.setenv("DAA_TRUSTED_PROXIES", "")

    assert Settings(_env_file=None).trusted_proxies == []
