"""Plusieurs utilisateurs DISTINCTS, en même temps, sur la surface HTTP.

Le cloisonnement était éprouvé au repos : Alice puis Bob, chacun son tour (cf.
``test_cloisonnement``). C'est la moitié de la question. L'autre moitié — celle
que le produit vit réellement, et qu'aucun des vingt-huit chantiers n'avait
mesurée — c'est Alice ET Bob en même temps, avec tout ce que ça met en
concurrence : un magasin de sessions unique réécrit à chaque requête, un
compteur de débit unique, une racine de conversations par utilisateur.

Ce que ces tests ne sont pas
---------------------------
Ce n'est pas le banc. Le banc (``scripts/mesure_concurrence.py``) mesure des
latences contre le vrai moteur ; ici on n'attend rien du temps qui passe, on
vérifie des INVARIANTS avec un orchestrateur factice qui dort juste assez pour
que les requêtes se chevauchent vraiment. Un test de concurrence qui ne
chevauche pas ne teste rien.

Chaque utilisateur porte un MARQUEUR unique dans ses questions. Il ne peut se
retrouver chez un autre que par une fuite : c'est ce qui permet d'affirmer
« aucune donnée n'a traversé » au lieu de « les identifiants sont distincts ».
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_analyst_agent.api.app import create_app
from data_analyst_agent.config import Settings
from data_analyst_agent.orchestrator.graph import ChatAnswer
from helpers.auth import client_connecte, creer_compte, reglages_de_test

# Assez d'utilisateurs pour que la concurrence soit réelle, assez peu pour que
# la suite reste rapide : quatre comptes, quatre sessions, quatre racines.
# Chaque compte coûte une empreinte argon2 à pleins paramètres — la connexion
# rehache l'empreinte affaiblie du helper de test (cf. `AccountStore.verify`) —
# et c'est ce qui borne le nombre, pas la concurrence.
UTILISATEURS = [f"u{i}" for i in range(4)]

# Ce qu'un tour factice met à « répondre ». Le but n'est pas de simuler un LLM
# mais de garantir le CHEVAUCHEMENT : sans attente, quatre requêtes lancées
# ensemble peuvent très bien s'exécuter l'une après l'autre et le test ne
# prouverait rien.
DUREE_DUN_TOUR = 0.05


class OrchestrateurLent:
    """Répond en écho, après une pause, et note la racine qu'on lui a passée.

    L'écho est ce qui fait le marqueur : la réponse porte la question, donc le
    marqueur de celui qui l'a posée. Les racines sont relevées pour vérifier que
    chaque requête a bien reçu CELLE de son appelant — le cloisonnement est un
    chemin, et c'est ce chemin-là qu'on regarde.
    """

    def __init__(self) -> None:
        self.racines: list[Path | None] = []
        self.simultanes = 0
        self.simultanes_max = 0
        self._verrou = threading.Lock()

    def ask(
        self,
        question: str,
        source: str | None = None,
        pending=None,
        conversation_id=None,
        workspace_root: Path | None = None,
        source_de_travail=None,
        echange_precedent=None,
    ) -> ChatAnswer:
        with self._verrou:
            self.racines.append(workspace_root)
            self.simultanes += 1
            self.simultanes_max = max(self.simultanes_max, self.simultanes)
        try:
            threading.Event().wait(DUREE_DUN_TOUR)
            return ChatAnswer(answer=f"réponse à : {question}")
        finally:
            with self._verrou:
                self.simultanes -= 1


@pytest.fixture
def settings(tmp_path) -> Settings:
    return reglages_de_test(tmp_path)


@pytest.fixture
def orchestrateur() -> OrchestrateurLent:
    return OrchestrateurLent()


@pytest.fixture
def app(orchestrateur: OrchestrateurLent, settings: Settings):
    return create_app(orchestrator_factory=lambda: orchestrateur, settings=settings)


@pytest.fixture
def clients(app, settings: Settings) -> dict[str, TestClient]:
    """Un client CONNECTÉ par utilisateur — un client partagé n'en ferait qu'un."""
    return {
        login: client_connecte(app, settings, creer_compte(settings, login), login=login)
        for login in UTILISATEURS
    }


def marqueur(login: str) -> str:
    """Le canari d'un utilisateur : littéral, introuvable ailleurs par accident."""
    return f"MARQUEUR-{login}-7f3a"


def _en_meme_temps(taches: list) -> list:
    """Exécute les tâches dans autant de threads, toutes relâchées au même instant.

    La barrière est ce qui fait la simultanéité : sans elle, les threads partent
    à la file et le chevauchement dépend de l'ordonnanceur.
    """
    depart = threading.Barrier(len(taches))

    def lancer(tache):
        depart.wait()
        return tache()

    with ThreadPoolExecutor(max_workers=len(taches)) as pool:
        return [futur.result() for futur in [pool.submit(lancer, t) for t in taches]]


def _rafale_de_questions(clients: dict[str, TestClient], tours: int = 1) -> dict[str, list[dict]]:
    """Chaque utilisateur pose ``tours`` questions marquées, tous en même temps."""

    def travail(login: str):
        def poser():
            reponses = []
            for tour in range(tours):
                reponse = clients[login].post(
                    "/chat",
                    json={
                        "message": f"question {tour} de {marqueur(login)}",
                        "conversation_id": f"fil-de-{login}",
                    },
                )
                assert reponse.status_code == 200, reponse.text
                reponses.append(reponse.json())
            return login, reponses

        return poser

    return dict(_en_meme_temps([travail(login) for login in clients]))


# -- la concurrence est-elle réelle ? -----------------------------------------


def test_les_requetes_se_chevauchent_vraiment(
    clients: dict[str, TestClient], orchestrateur: OrchestrateurLent
) -> None:
    """Le témoin de tous les autres tests de ce fichier.

    ``POST /chat`` est déclaré en ``def`` et non en ``async def`` : Starlette le
    sert donc dans son pool de threads, et plusieurs tours se déroulent en même
    temps. Si ce test échouait, tous les suivants passeraient sans rien prouver —
    ils vérifieraient une concurrence qui n'a pas eu lieu.
    """
    _rafale_de_questions(clients)

    assert orchestrateur.simultanes_max > 1, (
        f"aucun chevauchement : {orchestrateur.simultanes_max} tour à la fois"
    )


# -- le cloisonnement sous charge ---------------------------------------------


def test_chacun_recoit_sa_propre_reponse(clients: dict[str, TestClient]) -> None:
    """La réponse d'un utilisateur ne porte jamais le marqueur d'un autre.

    C'est la propriété la plus chère du produit, et elle n'avait été éprouvée
    qu'au repos : quatre réponses rendues ensemble, chacune ne doit parler que
    de celui qui a demandé.
    """
    par_login = _rafale_de_questions(clients, tours=2)

    for login, reponses in par_login.items():
        etrangers = [autre for autre in UTILISATEURS if autre != login]
        for reponse in reponses:
            texte = json.dumps(reponse, ensure_ascii=False)
            assert marqueur(login) in texte
            assert not [autre for autre in etrangers if marqueur(autre) in texte]


def test_chaque_tour_ecrit_sous_la_racine_de_son_appelant(
    clients: dict[str, TestClient], orchestrateur: OrchestrateurLent, settings: Settings
) -> None:
    """Le cloisonnement est un CHEMIN : une racine distincte par compte.

    Si l'orchestrateur recevait deux fois la même racine, deux utilisateurs
    écriraient leurs tableaux intermédiaires au même endroit — et le suivant
    remonterait ceux du précédent.
    """
    _rafale_de_questions(clients)

    racines = [r for r in orchestrateur.racines if r is not None]
    assert len(racines) == len(UTILISATEURS)
    assert len(set(racines)) == len(UTILISATEURS)
    for racine in racines:
        assert settings.workspace_dir in racine.parents


def test_le_meme_identifiant_de_fil_ne_mele_pas_deux_comptes(
    clients: dict[str, TestClient],
) -> None:
    """Quatre utilisateurs, quatre fils, et personne ne voit celui d'un autre.

    Les identifiants sont volontairement DEVINABLES (``fil-de-u0``) : le
    cloisonnement ne doit rien devoir au secret d'un identifiant.
    """
    _rafale_de_questions(clients, tours=2)

    for login, client in clients.items():
        fils = client.get("/conversations").json()
        assert [f["id"] for f in fils] == [f"fil-de-{login}"]
        for autre in UTILISATEURS:
            if autre == login:
                continue
            assert client.get(f"/conversations/fil-de-{autre}").status_code == 404


def test_le_meme_id_de_fil_chez_deux_comptes_donne_deux_fils(
    clients: dict[str, TestClient],
) -> None:
    """Le MÊME identifiant demandé par tous, en même temps : quatre fils, pas un.

    ``POST /chat`` honore l'identifiant du client, mais le résout sous la racine
    de l'appelant : ``commun`` chez u0 et ``commun`` chez u1 sont deux dossiers.
    Ce que la simultanéité éprouve ici, c'est qu'aucun d'eux ne crée le fil
    d'un autre — ni ne l'écrase.
    """

    def travail(login: str):
        return lambda: clients[login].post(
            "/chat", json={"message": marqueur(login), "conversation_id": "commun"}
        )

    reponses = _en_meme_temps([travail(login) for login in clients])
    assert [r.status_code for r in reponses] == [200] * len(clients)
    assert {r.json()["conversation_id"] for r in reponses} == {"commun"}

    for login, client in clients.items():
        fil = client.get("/conversations/commun").json()
        assert fil["owner"] == login
        contenu = json.dumps(fil, ensure_ascii=False)
        assert marqueur(login) in contenu
        for autre in UTILISATEURS:
            if autre != login:
                assert marqueur(autre) not in contenu


def test_aucune_transcription_ne_porte_le_marqueur_dun_autre(
    clients: dict[str, TestClient], settings: Settings
) -> None:
    """Le disque confirme ce que l'API dit : rien n'a traversé.

    Vérifier par l'API seule laisserait une possibilité ouverte — un fichier
    écrit au mauvais endroit que la route ne montrerait simplement pas. On relit
    donc ce qui est réellement rangé.
    """
    _rafale_de_questions(clients, tours=2)

    transcriptions = list(settings.workspace_dir.rglob("transcript.json"))
    assert len(transcriptions) == len(UTILISATEURS)
    for chemin in transcriptions:
        contenu = chemin.read_text(encoding="utf-8")
        proprietaire = json.loads(contenu)["owner"]
        assert proprietaire in UTILISATEURS
        for autre in UTILISATEURS:
            if autre != proprietaire:
                assert marqueur(autre) not in contenu


def test_les_sessions_survivent_a_la_rafale(clients: dict[str, TestClient]) -> None:
    """Le magasin de sessions est un fichier UNIQUE, relu et réécrit à chaque
    requête authentifiée (``SessionStore.resolve`` met à jour ``last_seen_at``).
    Quatre utilisateurs en même temps, c'est quatre lecture-modification-écriture
    concurrentes sur ce fichier : sans verrou, la dernière écriture effacerait
    les sessions des autres, et le produit déconnecterait des gens au hasard dès
    qu'ils seraient plusieurs.
    """
    _rafale_de_questions(clients, tours=2)

    for login, client in clients.items():
        reponse = client.get("/me")
        assert reponse.status_code == 200, f"{login} a perdu sa session : {reponse.text}"
        assert reponse.json()["login"] == login


def test_des_connexions_simultanees_donnent_autant_de_sessions(app, settings: Settings) -> None:
    """Se connecter en même temps, et non se connecter à la file.

    Même fichier, même lecture-modification-écriture : quatre ouvertures de
    session concurrentes doivent laisser quatre sessions valables, pas une.
    """
    mots_de_passe = {login: creer_compte(settings, login) for login in UTILISATEURS}

    def ouvrir(login: str):
        return lambda: (login, client_connecte(app, settings, mots_de_passe[login], login=login))

    ouverts = dict(_en_meme_temps([ouvrir(login) for login in UTILISATEURS]))

    for login, client in ouverts.items():
        reponse = client.get("/me")
        assert reponse.status_code == 200, f"{login} n'a pas de session : {reponse.text}"
        assert reponse.json()["login"] == login


# -- le quota de débit ---------------------------------------------------------


def test_le_quota_ne_se_contourne_pas_en_tirant_en_meme_temps(
    orchestrateur: OrchestrateurLent, tmp_path
) -> None:
    """Le compteur de débit est un lecture-modification-écriture, comme le reste.

    Sans atomicité, K requêtes simultanées liraient toutes le même compteur et
    passeraient toutes : le quota deviendrait une suggestion, que n'importe quel
    client parallèle franchirait. On tire douze questions ensemble sur un quota
    de quatre — il doit en passer quatre, ni cinq, ni douze.
    """
    quota = 4
    reglages = reglages_de_test(tmp_path, chat_rate_limit_requests=quota)
    app = create_app(orchestrator_factory=lambda: orchestrateur, settings=reglages)
    client = client_connecte(app, reglages, creer_compte(reglages, "u0"), login="u0")

    def tirer(index: int):
        return lambda: client.post("/chat", json={"message": f"question {index}"}).status_code

    statuts = _en_meme_temps([tirer(i) for i in range(12)])

    assert statuts.count(200) == quota
    assert statuts.count(429) == 12 - quota


def test_le_quota_est_par_compte_et_non_global(
    clients: dict[str, TestClient], settings: Settings
) -> None:
    """Quatre utilisateurs en même temps, chacun sous son quota : aucun refus.

    Un compteur global serait indiscernable d'un compteur par compte tant qu'on
    mesure un utilisateur à la fois. C'est à plusieurs qu'il se voit — et un
    quota global ferait payer à chacun les questions des autres.
    """
    par_login = _rafale_de_questions(clients, tours=3)

    assert sum(len(r) for r in par_login.values()) == 3 * len(UTILISATEURS)
