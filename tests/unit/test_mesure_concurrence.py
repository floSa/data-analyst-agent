"""Tests de l'outillage du banc de concurrence (scripts/mesure_concurrence.py).

Le banc rend des tableaux qui deviendront des chiffres dans un rapport. Trois
choses en lui peuvent mentir sans qu'on s'en aperçoive, et ce sont celles-ci
qu'on teste :

- la **nature** d'une erreur : nommer « plafond de sandbox » ce qui est un refus
  de débit ferait accuser le mauvais goulot ;
- les **percentiles** : un p95 calculé de travers sur seize points, personne ne
  le recalcule à la main ;
- le **canari** : si le terrain écrivait le même jeton pour deux utilisateurs, ou
  le publiait dans le catalogue, l'audit de cloisonnement ne prouverait plus
  rien — il rendrait vert quoi qu'il arrive.

L'audit lui-même est testé sur une fuite FABRIQUÉE : un audit qu'on ne voit
jamais échouer n'est pas un audit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mesure_concurrence import (
    Constat,
    Phase,
    Reponse,
    SondeVllm,
    Utilisateur,
    _csv_dun_utilisateur,
    _percentile,
    auditer_le_cloisonnement,
    nature_de_lerreur,
    preparer_le_terrain,
    questions,
    tableau_des_erreurs,
    tableau_des_latences,
    tableau_des_noeuds,
)

# -- les percentiles -----------------------------------------------------------


def test_le_p50_est_une_valeur_observee():
    """Rang le plus proche, sans interpolation : on rend ce qu'on a mesuré."""
    assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0


def test_le_p95_prend_le_haut_de_la_distribution():
    valeurs = [float(v) for v in range(1, 21)]
    assert _percentile(valeurs, 95) == 19.0


def test_un_percentile_sur_une_seule_valeur_la_rend():
    assert _percentile([7.5], 95) == 7.5


def test_un_percentile_sans_valeur_ne_leve_pas():
    """Une phase dont TOUTES les requêtes ont échoué doit rester affichable."""
    assert _percentile([], 50) != _percentile([], 50)  # NaN


# -- la nature des erreurs -----------------------------------------------------


@pytest.mark.parametrize(
    ("statut", "corps", "attendu"),
    [
        (200, {"answer": "voilà"}, "ok"),
        (
            200,
            {
                "error": "l'analyse n'a pas pu être menée (incident ab12cd34)",
                "trace": [
                    {
                        "node": "analysis",
                        "detail": "incident ab12cd34 — échec : SandboxError: toutes les "
                        "sandboxes sont occupées (4 en parallèle) : pas de place libérée en 60 s",
                    }
                ],
            },
            "plafond de sandbox",
        ),
        (
            429,
            {"detail": "trop de questions en peu de temps : réessayez dans un instant"},
            "limite de requêtes",
        ),
        (401, {"detail": "authentification requise"}, "refus d'authentification"),
        (403, {"detail": "jeton anti-CSRF absent ou invalide"}, "refus d'authentification"),
        (500, {"detail": "Internal Server Error"}, "erreur serveur"),
        (0, {"detail": "connexion coupée : URLError: [Errno 104]"}, "connexion coupée"),
        (0, {"detail": "abandon du banc après 600 s : timed out"}, "abandon du banc"),
        (
            200,
            {
                "error": "la source de données n'a pas pu être interrogée (incident 9f)",
                "trace": [{"detail": "incident 9f — échec : ModelAPIError: Connection error."}],
            },
            "connexion au moteur",
        ),
        (413, {"detail": "message trop long : 5000 caractères"}, "HTTP 413"),
    ],
)
def test_chaque_echec_est_nomme(statut: int, corps: dict, attendu: str):
    assert nature_de_lerreur(statut, corps) == attendu


def test_le_plafond_de_sandbox_ne_se_confond_pas_avec_le_debit():
    """Les deux se ressemblent vus de loin — « ça n'a pas pu passer » — et
    n'appellent pas la même correction : l'un veut plus de mémoire, l'autre un
    quota plus large. Les confondre ferait régler le mauvais réglage."""
    sandbox = {
        "error": "l'analyse n'a pas pu être menée",
        "trace": [{"detail": "SandboxError: toutes les sandboxes sont occupées (4 en parallèle)"}],
    }
    debit = {"detail": "trop de questions en peu de temps : réessayez dans un instant"}

    assert nature_de_lerreur(200, sandbox) != nature_de_lerreur(429, debit)


def test_labandon_du_banc_ne_passe_pas_pour_une_panne_du_produit():
    """Le banc fixe un délai ; le franchir est SA décision, pas une panne du
    service. Les confondre ferait porter au produit une erreur qu'on a créée."""
    abandon = {"detail": "abandon du banc après 600 s : timed out"}
    coupure = {"detail": "connexion coupée : ConnectionResetError"}

    assert nature_de_lerreur(0, abandon) != nature_de_lerreur(0, coupure)


def test_une_erreur_metier_sans_marqueur_connu_reste_nommee():
    corps = {"error": "la source de données n'a pas pu être interrogée (incident 1234abcd)"}
    assert nature_de_lerreur(200, corps) == "erreur métier"


# -- le terrain et le canari ---------------------------------------------------


def test_chaque_ligne_du_csv_porte_le_jeton():
    """Le canari doit être partout dans le fichier : une seule ligne marquée, et
    une requête qui n'en lirait que d'autres ne le ferait jamais apparaître."""
    csv = _csv_dun_utilisateur("JETON-u0-deadbeef", lignes=10)

    lignes = csv.strip().splitlines()
    assert lignes[0] == "jeton,categorie,mesure"
    assert len(lignes) == 11
    assert all(ligne.startswith("JETON-u0-deadbeef,") for ligne in lignes[1:])


def test_deux_utilisateurs_ont_des_jetons_et_des_sources_distincts(tmp_path: Path):
    utilisateurs = preparer_le_terrain(tmp_path, 4)

    assert len({u.jeton for u in utilisateurs}) == 4
    assert len({u.source for u in utilisateurs}) == 4
    assert len({u.login for u in utilisateurs}) == 4
    assert len({u.mot_de_passe for u in utilisateurs}) == 4


def test_le_catalogue_ne_publie_jamais_un_jeton(tmp_path: Path):
    """Le défaut qui viderait l'épreuve de son sens.

    Un jeton dans une description de source entrerait dans le prompt du
    planificateur : il pourrait alors apparaître dans n'importe quelle réponse
    sans que le moindre fichier ait été lu, et « aucune fuite » ne voudrait plus
    rien dire.
    """
    utilisateurs = preparer_le_terrain(tmp_path, 3)

    catalogue = (tmp_path / "sources" / "catalogue.yaml").read_text(encoding="utf-8")
    for utilisateur in utilisateurs:
        assert utilisateur.source in catalogue
        assert utilisateur.jeton not in catalogue


def test_les_comptes_sont_ecrits_dans_un_magasin_jetable(tmp_path: Path):
    """Jamais celui du service : c'est ce qui fait de la suppression un rmtree."""
    utilisateurs = preparer_le_terrain(tmp_path, 2)

    comptes = (tmp_path / "users.yaml").read_text(encoding="utf-8")
    for utilisateur in utilisateurs:
        assert utilisateur.login in comptes
        # Le mot de passe n'est jamais écrit — seulement son empreinte argon2id.
        assert utilisateur.mot_de_passe not in comptes


def test_chaque_phase_ouvre_un_fil_neuf():
    """Un fil réutilisé de palier en palier grandirait avec la campagne, et les
    derniers paliers seraient plus lents pour deux raisons mêlées."""
    utilisateur = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0")

    fils = [utilisateur.nouveau_fil() for _ in range(3)]

    assert len(set(fils)) == 3
    assert utilisateur.fils == fils


def test_les_questions_visent_la_source_de_leur_auteur():
    utilisateur = Utilisateur(2, "banc_u2", "x", "JETON-u2-bb", "mesures_u2")

    for capacite in ("query", "analyze"):
        for question in questions(utilisateur, capacite, 2):
            assert "mesures_u2" in question


def test_la_prediction_ne_depend_daucune_source():
    """Le témoin du banc : le seul chemin qui ne passe ni par une base ni par le
    bac à sable, donc celui qui isole le moteur."""
    utilisateur = Utilisateur(0, "banc_u0", "x", "JETON-u0-cc", "mesures_u0")

    for question in questions(utilisateur, "predict", 2):
        assert "mesures_u0" not in question


# -- l'audit de cloisonnement --------------------------------------------------


class ClientFactice:
    """Un client qui rend ce qu'on lui dit, pour éprouver l'audit sans serveur."""

    def __init__(self, fils: list[str], croisements: dict[str, int] | None = None) -> None:
        self.fils = fils
        self.croisements = croisements or {}

    def get(self, chemin: str):
        if chemin == "/conversations":
            return 200, [{"id": fil} for fil in self.fils]
        return self.croisements.get(chemin, 404), {}


def _terrain_avec_transcriptions(tmp_path: Path, contenus: dict[str, dict]) -> Path:
    racine = tmp_path / "workspaces"
    for login, transcript in contenus.items():
        dossier = racine / login / transcript["id"]
        dossier.mkdir(parents=True)
        (dossier / "transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


def _phase(reponses: list[Reponse]) -> Phase:
    return Phase(n=2, capacite="query", duree=10.0, reponses=reponses)


def _reponse(login: str, texte: str) -> Reponse:
    return Reponse(login, "query", 0.0, 1.0, 200, {"answer": texte})


def test_laudit_est_vert_quand_chacun_ne_voit_que_le_sien(tmp_path: Path):
    alice = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0", ["fil-a"])
    bob = Utilisateur(1, "banc_u1", "x", "JETON-u1-bb", "mesures_u1", ["fil-b"])
    phases = [
        _phase(
            [
                _reponse("banc_u0", "le jeton est JETON-u0-aa"),
                _reponse("banc_u1", "le jeton est JETON-u1-bb"),
            ]
        )
    ]
    clients = {"banc_u0": ClientFactice(["fil-a"]), "banc_u1": ClientFactice(["fil-b"])}
    terrain = _terrain_avec_transcriptions(
        tmp_path,
        {
            "banc_u0": {"id": "fil-a", "owner": "banc_u0", "texte": "JETON-u0-aa"},
            "banc_u1": {"id": "fil-b", "owner": "banc_u1", "texte": "JETON-u1-bb"},
        },
    )

    constats = auditer_le_cloisonnement(clients, [alice, bob], phases, terrain)

    assert all(c.tenu for c in constats), [c for c in constats if not c.tenu]


def test_laudit_voit_une_fuite_dans_une_reponse(tmp_path: Path):
    """La fuite FABRIQUÉE : sans ce test, l'audit pourrait être vert par
    construction et personne ne le saurait."""
    alice = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0", ["fil-a"])
    bob = Utilisateur(1, "banc_u1", "x", "JETON-u1-bb", "mesures_u1", ["fil-b"])
    phases = [
        _phase(
            [
                _reponse("banc_u0", "le jeton est JETON-u0-aa et aussi JETON-u1-bb"),
                _reponse("banc_u1", "le jeton est JETON-u1-bb"),
            ]
        )
    ]
    clients = {"banc_u0": ClientFactice(["fil-a"]), "banc_u1": ClientFactice(["fil-b"])}
    terrain = _terrain_avec_transcriptions(
        tmp_path,
        {
            "banc_u0": {"id": "fil-a", "owner": "banc_u0", "texte": "JETON-u0-aa"},
            "banc_u1": {"id": "fil-b", "owner": "banc_u1", "texte": "JETON-u1-bb"},
        },
    )

    constats = auditer_le_cloisonnement(clients, [alice, bob], phases, terrain)

    rompus = [c.intitule for c in constats if not c.tenu]
    assert rompus == ["aucune réponse ne porte le jeton d'un autre utilisateur"]


def test_laudit_voit_un_fil_etranger_dans_une_liste(tmp_path: Path):
    alice = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0", ["fil-a"])
    bob = Utilisateur(1, "banc_u1", "x", "JETON-u1-bb", "mesures_u1", ["fil-b"])
    phases = [_phase([_reponse("banc_u0", "JETON-u0-aa"), _reponse("banc_u1", "JETON-u1-bb")])]
    clients = {
        "banc_u0": ClientFactice(["fil-a", "fil-b"]),  # voit le fil de bob
        "banc_u1": ClientFactice(["fil-b"]),
    }
    terrain = _terrain_avec_transcriptions(
        tmp_path,
        {
            "banc_u0": {"id": "fil-a", "owner": "banc_u0", "texte": "JETON-u0-aa"},
            "banc_u1": {"id": "fil-b", "owner": "banc_u1", "texte": "JETON-u1-bb"},
        },
    )

    constats = auditer_le_cloisonnement(clients, [alice, bob], phases, terrain)

    assert [c.intitule for c in constats if not c.tenu] == ["chacun ne liste que ses propres fils"]


def test_laudit_refuse_un_403_la_ou_il_attend_un_404(tmp_path: Path):
    """403 dirait « ce fil existe, mais pas pour vous » : un oracle d'existence."""
    alice = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0", ["fil-a"])
    bob = Utilisateur(1, "banc_u1", "x", "JETON-u1-bb", "mesures_u1", ["fil-b"])
    phases = [_phase([_reponse("banc_u0", "JETON-u0-aa"), _reponse("banc_u1", "JETON-u1-bb")])]
    clients = {
        "banc_u0": ClientFactice(["fil-a"], {"/conversations/fil-b": 403}),
        "banc_u1": ClientFactice(["fil-b"]),
    }
    terrain = _terrain_avec_transcriptions(
        tmp_path,
        {
            "banc_u0": {"id": "fil-a", "owner": "banc_u0", "texte": "JETON-u0-aa"},
            "banc_u1": {"id": "fil-b", "owner": "banc_u1", "texte": "JETON-u1-bb"},
        },
    )

    constats = auditer_le_cloisonnement(clients, [alice, bob], phases, terrain)

    assert [c.intitule for c in constats if not c.tenu] == [
        "le fil d'un autre répond 404 (jamais 403, jamais 200)"
    ]


def test_laudit_refuse_un_canari_muet(tmp_path: Path):
    """Si personne n'a jamais vu son propre jeton, « aucune fuite » ne prouve rien."""
    alice = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0", ["fil-a"])
    bob = Utilisateur(1, "banc_u1", "x", "JETON-u1-bb", "mesures_u1", ["fil-b"])
    phases = [_phase([_reponse("banc_u0", "je n'ai pas pu répondre"), _reponse("banc_u1", "idem")])]
    clients = {"banc_u0": ClientFactice(["fil-a"]), "banc_u1": ClientFactice(["fil-b"])}
    terrain = _terrain_avec_transcriptions(
        tmp_path,
        {
            "banc_u0": {"id": "fil-a", "owner": "banc_u0", "texte": ""},
            "banc_u1": {"id": "fil-b", "owner": "banc_u1", "texte": ""},
        },
    )

    constats = auditer_le_cloisonnement(clients, [alice, bob], phases, terrain)

    assert [c.intitule for c in constats if not c.tenu] == [
        "le canari est actif : chacun a bien reçu SON propre jeton"
    ]


def test_laudit_voit_une_transcription_rangee_chez_le_mauvais_proprietaire(tmp_path: Path):
    """La fuite que l'API ne montrerait pas : un fichier au mauvais endroit."""
    alice = Utilisateur(0, "banc_u0", "x", "JETON-u0-aa", "mesures_u0", ["fil-a"])
    bob = Utilisateur(1, "banc_u1", "x", "JETON-u1-bb", "mesures_u1", ["fil-b"])
    phases = [_phase([_reponse("banc_u0", "JETON-u0-aa"), _reponse("banc_u1", "JETON-u1-bb")])]
    clients = {"banc_u0": ClientFactice(["fil-a"]), "banc_u1": ClientFactice(["fil-b"])}
    terrain = _terrain_avec_transcriptions(
        tmp_path,
        {
            "banc_u0": {"id": "fil-a", "owner": "banc_u0", "texte": "JETON-u1-bb"},
            "banc_u1": {"id": "fil-b", "owner": "banc_u1", "texte": "JETON-u1-bb"},
        },
    )

    constats = auditer_le_cloisonnement(clients, [alice, bob], phases, terrain)

    assert [c.intitule for c in constats if not c.tenu] == [
        "sur le disque, aucune transcription ne porte le jeton d'un autre"
    ]


# -- la sonde du moteur --------------------------------------------------------


def test_la_sonde_lit_les_deux_compteurs_du_moteur():
    """Les deux chiffres qui désignent le goulot : ce qui vole, ce qui attend."""
    metriques = (
        "# HELP vllm:num_requests_running Number of requests in model execution batches.\n"
        'vllm:num_requests_running{engine="0",model_name="gemma"} 6.0\n'
        'vllm:num_requests_waiting{engine="0",model_name="gemma"} 2.0\n'
        'vllm:gpu_cache_usage_perc{engine="0",model_name="gemma"} 0.12\n'
    )

    valeurs = dict(SondeVllm.MOTIF.findall(metriques))

    assert valeurs["num_requests_running"] == "6.0"
    assert valeurs["num_requests_waiting"] == "2.0"


def test_la_sonde_ne_confond_pas_une_autre_metrique():
    """`num_requests_waiting_by_reason` porte le même préfixe et n'est pas la
    même chose : la sommer au compteur global gonflerait la file."""
    metriques = (
        'vllm:num_requests_running{engine="0"} 3.0\n'
        'vllm:num_requests_waiting{engine="0"} 1.0\n'
        'vllm:num_requests_waiting_by_reason{engine="0",reason="capacity"} 1.0\n'
    )

    valeurs = SondeVllm.MOTIF.findall(metriques)

    assert valeurs == [("num_requests_running", "3.0"), ("num_requests_waiting", "1.0")]


# -- les tableaux du rapport ---------------------------------------------------


def test_le_tableau_des_latences_compte_les_requetes_abouties():
    """Le débit se calcule sur ce qui a ABOUTI : compter les refus comme du
    débit ferait grimper la courbe au moment exact où le service lâche."""
    phase = Phase(
        n=2,
        capacite="analyze",
        duree=20.0,
        reponses=[
            Reponse("banc_u0", "analyze", 0.0, 10.0, 200, {}),
            Reponse("banc_u1", "analyze", 0.0, 60.0, 200, {}, "plafond de sandbox"),
        ],
    )

    tableau = tableau_des_latences([phase])

    assert "| analyze | 2 | 2 | 1 |" in tableau
    assert phase.debit == pytest.approx(3.0)  # 1 aboutie en 20 s = 3 par minute


def test_le_tableau_des_erreurs_nomme_chaque_nature():
    phase = Phase(
        n=8,
        capacite="analyze",
        duree=30.0,
        reponses=[
            Reponse("banc_u0", "analyze", 0.0, 60.0, 200, {}, "plafond de sandbox"),
            Reponse("banc_u1", "analyze", 0.0, 0.1, 429, {}, "limite de requêtes"),
        ],
    )

    tableau = tableau_des_erreurs([phase])

    assert "plafond de sandbox" in tableau
    assert "limite de requêtes" in tableau


def test_le_tableau_des_erreurs_le_dit_quand_il_ny_en_a_pas():
    phase = Phase(2, "query", 5.0, [Reponse("banc_u0", "query", 0.0, 2.0, 200, {})])

    assert tableau_des_erreurs([phase]) == "Aucune erreur, à aucun palier."


def test_un_constat_porte_son_intitule_et_son_verdict():
    constat = Constat("le canari est actif", True, "2 utilisateurs")

    assert (constat.intitule, constat.tenu, constat.detail) == (
        "le canari est actif",
        True,
        "2 utilisateurs",
    )


# -- où le temps passe (la preuve du goulot) -----------------------------------


def _reponse_tracee(login: str, trace: list[dict], latence: float = 10.0) -> Reponse:
    return Reponse(login, "analyze", 0.0, latence, 200, {"trace": trace})


def test_les_noeuds_sont_medianes_sur_les_reponses_abouties():
    phase = Phase(
        n=2,
        capacite="analyze",
        duree=30.0,
        reponses=[
            _reponse_tracee("u0", [{"node": "plan", "duration_ms": 1000}]),
            _reponse_tracee("u1", [{"node": "plan", "duration_ms": 3000}]),
            # celle-ci a échoué : sa trace ne doit pas entrer dans la médiane
            Reponse("u2", "analyze", 0.0, 60.0, 200, {"trace": []}, "plafond de sandbox"),
        ],
    )

    assert phase.noeuds == {"plan": 1.0}


def test_un_noeud_traverse_deux_fois_est_somme():
    """Une boucle de correction repasse par `analysis` : ce qui compte est ce que
    ce nœud a coûté AU TOUR, pas la durée d'un de ses passages."""
    phase = Phase(
        n=1,
        capacite="analyze",
        duree=20.0,
        reponses=[
            _reponse_tracee(
                "u0",
                [
                    {"node": "analysis", "duration_ms": 4000},
                    {"node": "analysis", "duration_ms": 6000},
                    {"node": "synthesize", "duration_ms": 500},
                ],
            )
        ],
    )

    assert phase.noeuds == {"analysis": 10.0, "synthesize": 0.5}


def test_le_tableau_des_noeuds_montre_la_croissance_dun_seul():
    """La forme que prend la preuve : un nœud qui grandit avec N, les autres non."""
    phases = [
        Phase(
            1,
            "analyze",
            10.0,
            [
                _reponse_tracee(
                    "u0",
                    [
                        {"node": "analysis", "duration_ms": 5000},
                        {"node": "plan", "duration_ms": 900},
                    ],
                )
            ],
        ),
        Phase(
            8,
            "analyze",
            80.0,
            [
                _reponse_tracee(
                    "u0",
                    [
                        {"node": "analysis", "duration_ms": 25000},
                        {"node": "plan", "duration_ms": 1100},
                    ],
                )
            ],
        ),
    ]

    tableau = tableau_des_noeuds(phases)

    assert "| analyze | 1 | 10.0 | 5.0 | 0.9 |" in tableau
    assert "| analyze | 8 | 10.0 | 25.0 | 1.1 |" in tableau


def test_le_tableau_des_noeuds_le_dit_quand_il_ny_a_pas_de_trace():
    phase = Phase(1, "query", 5.0, [Reponse("u0", "query", 0.0, 2.0, 200, {})])

    assert tableau_des_noeuds([phase]) == "Aucune trace exploitable."
