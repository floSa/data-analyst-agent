"""Ce qu'un mandataire dit de l'appelant, et ce qu'on en croit.

Deux familles de tests, et il faut les deux : que l'adresse réelle SOIT
retrouvée derrière un mandataire déclaré — sans quoi l'anti-force brute
compterait tout le monde sur une seule adresse — et qu'elle ne le soit JAMAIS
quand on n'a rien déclaré, sans quoi n'importe qui choisirait le compteur sur
lequel inscrire ses échecs.
"""

import pytest
from starlette.requests import Request

from data_analyst_agent.api.forwarded import (
    ADRESSE_INCONNUE,
    adresse_client,
    reseaux_de_confiance,
    schema_client,
)

MANDATAIRE = "172.30.0.2"
RESEAU_DU_COMPOSE = ("172.30.0.0/24",)


def requete(pair: str | None, **entetes: str) -> Request:
    """Une requête ASGI minimale : un pair et des en-têtes, rien d'autre."""
    portee = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/login",
        "raw_path": b"/login",
        "query_string": b"",
        "root_path": "",
        "server": ("app", 8000),
        "client": (pair, 54321) if pair is not None else None,
        "headers": [
            (nom.lower().replace("_", "-").encode(), valeur.encode())
            for nom, valeur in entetes.items()
        ],
    }
    return Request(portee)


# -- réseaux déclarés ---------------------------------------------------------


def test_une_adresse_nue_vaut_son_reseau():
    (reseau,) = reseaux_de_confiance(["10.1.3.6"])
    assert str(reseau) == "10.1.3.6/32"


def test_une_entree_illisible_est_ecartee_sans_tout_faire_echouer():
    """Un réglage mal tapé ne doit rien autoriser — il ne doit pas empêcher de démarrer."""
    reseaux = reseaux_de_confiance(["pas-une-adresse", " 172.30.0.0/24 ", ""])
    assert [str(reseau) for reseau in reseaux] == ["172.30.0.0/24"]


def test_un_cidr_a_bits_dhote_est_accepte():
    """`172.30.0.5/24` désigne le réseau : on ne refuse pas une écriture courante."""
    (reseau,) = reseaux_de_confiance(["172.30.0.5/24"])
    assert str(reseau) == "172.30.0.0/24"


# -- sans mandataire déclaré : on ne croit rien -------------------------------


def test_sans_mandataire_declare_len_tete_est_ignore():
    """Le défaut. Une configuration qu'on n'a pas posée ne peut pas nous affaiblir."""
    demande = requete("203.0.113.7", x_forwarded_for="1.2.3.4")
    assert adresse_client(demande, reseaux_de_confiance([])) == "203.0.113.7"


def test_un_appelant_direct_ne_se_reecrit_pas_une_adresse():
    """Mandataire déclaré, mais l'appelant n'en est pas un : son en-tête ne vaut rien.

    C'est le contournement qu'on achèterait en croyant l'en-tête sans condition :
    une adresse neuve à chaque tentative, donc un compteur neuf, donc aucun
    verrouillage.
    """
    demande = requete("203.0.113.7", x_forwarded_for="10.0.0.1")
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "203.0.113.7"


def test_un_pair_absent_donne_une_adresse_inconnue():
    """Certains transports ASGI ne portent pas de `client` : une clé, pas un None."""
    assert (
        adresse_client(requete(None), reseaux_de_confiance(RESEAU_DU_COMPOSE)) == ADRESSE_INCONNUE
    )


# -- derrière le mandataire : on retrouve l'appelant --------------------------


def test_derriere_le_mandataire_on_retient_ladresse_annoncee():
    demande = requete(MANDATAIRE, x_forwarded_for="203.0.113.7")
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "203.0.113.7"


def test_ce_que_lappelant_sinvente_a_gauche_nest_jamais_retenu():
    """Le mandataire ajoute le pair réel À DROITE ; tout ce qui précède est du récit."""
    demande = requete(MANDATAIRE, x_forwarded_for="9.9.9.9, 203.0.113.7")
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "203.0.113.7"


def test_deux_mandataires_en_cascade_sont_traverses():
    """Un répartiteur devant le nôtre : les deux déclarés, on remonte jusqu'à l'appelant."""
    reseaux = reseaux_de_confiance(["172.30.0.0/24", "10.1.3.9"])
    demande = requete(MANDATAIRE, x_forwarded_for="203.0.113.7, 10.1.3.9")
    assert adresse_client(demande, reseaux) == "203.0.113.7"


def test_une_entree_illisible_de_la_chaine_ne_devient_pas_une_cle():
    """`unknown` est permis par la RFC, et c'est une chaîne que l'appelant choisit.

    La retenir en ferait une clé de compteur d'anti-force brute — partagée par
    tous ceux qui l'enverraient, donc un verrouillage que chacun peut poser sur
    les autres.
    """
    demande = requete(MANDATAIRE, x_forwarded_for="unknown, 203.0.113.7")
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "203.0.113.7"


def test_sans_en_tete_le_mandataire_repond_de_lui_meme():
    """Sa propre sonde de disponibilité, par exemple : le pair est la réponse honnête."""
    demande = requete(MANDATAIRE)
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == MANDATAIRE


def test_une_chaine_entierement_de_confiance_rend_le_pair():
    demande = requete(MANDATAIRE, x_forwarded_for="172.30.0.3")
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == MANDATAIRE


def test_une_chaine_dentrees_toutes_illisibles_rend_le_pair():
    demande = requete(MANDATAIRE, x_forwarded_for="unknown, _cache")
    assert adresse_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == MANDATAIRE


def test_un_reseau_ipv6_ne_capture_pas_une_adresse_ipv4():
    """Comparer deux familles lève `TypeError` dans ipaddress : on ne le laisse pas passer."""
    demande = requete("192.0.2.1", x_forwarded_for="203.0.113.7")
    assert adresse_client(demande, reseaux_de_confiance(["::1/128"])) == "192.0.2.1"


def test_un_mandataire_ipv6_est_traverse_comme_les_autres():
    demande = requete("::1", x_forwarded_for="2001:db8::7")
    assert adresse_client(demande, reseaux_de_confiance(["::1/128"])) == "2001:db8::7"


# -- le protocole -------------------------------------------------------------


@pytest.mark.parametrize("annonce", ["https", "HTTPS", "https, http"])
def test_le_protocole_annonce_par_le_mandataire_est_cru(annonce: str):
    """L'application est en clair derrière lui : son propre schéma ne dit rien."""
    demande = requete(MANDATAIRE, x_forwarded_proto=annonce)
    assert schema_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "https"


def test_le_protocole_annonce_par_un_inconnu_est_ignore():
    demande = requete("203.0.113.7", x_forwarded_proto="https")
    assert schema_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "http"


def test_un_protocole_fantaisiste_retombe_sur_celui_de_la_requete():
    demande = requete(MANDATAIRE, x_forwarded_proto="gopher")
    assert schema_client(demande, reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "http"


def test_sans_en_tete_le_protocole_est_celui_de_la_requete():
    assert schema_client(requete(MANDATAIRE), reseaux_de_confiance(RESEAU_DU_COMPOSE)) == "http"
