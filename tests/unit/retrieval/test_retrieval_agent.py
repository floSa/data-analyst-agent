"""Agent text-to-SQL scripté (FunctionModel + ToolCallPart) sur DuckDB en mémoire.

DuckDB est in-process : ces tests exercent le VRAI chemin tools -> SQL sans
Docker ni réseau, y compris la self-correction après une erreur SQL.
"""

from pathlib import Path

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_analyst_agent.agents.retrieval.agent import run_retrieval
from data_analyst_agent.agents.retrieval.duckdb_excel import DuckDBAdapter
from data_analyst_agent.config import Settings


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def adapter(tmp_path: Path) -> DuckDBAdapter:
    csv = tmp_path / "mini.csv"
    csv.write_text("sexe,survie\nf,1\nf,1\nf,0\nm,0\n", encoding="utf-8")
    return DuckDBAdapter.from_file(csv)


def scripted_model(steps: list[list]) -> FunctionModel:
    """Rejoue une séquence de réponses modèle (parts) dans l'ordre."""
    remaining = [ModelResponse(parts=parts) for parts in steps]

    def responder(messages, info):
        return remaining.pop(0)

    return FunctionModel(responder)


def test_flux_nominal_schema_puis_sql(adapter):
    model = scripted_model(
        [
            [ToolCallPart("get_schema", {})],
            [ToolCallPart("run_sql", {"query": "SELECT count(*) AS n FROM mini WHERE sexe = 'f'"})],
            [TextPart("Il y a 3 femmes dans la table.")],
        ]
    )
    outcome = run_retrieval(
        "Combien de femmes ?", adapter=adapter, model=model, settings=make_settings()
    )
    assert outcome.succeeded
    assert outcome.summary == "Il y a 3 femmes dans la table."
    assert outcome.result.rows == [[3]]
    assert "count(*)" in outcome.sql
    assert [q.ok for q in outcome.executed] == [True]


def test_self_correction_apres_erreur_sql(adapter):
    model = scripted_model(
        [
            [ToolCallPart("run_sql", {"query": "SELECT nexiste_pas FROM mini"})],
            [ToolCallPart("run_sql", {"query": "SELECT count(*) AS n FROM mini"})],
            [TextPart("4 lignes.")],
        ]
    )
    outcome = run_retrieval(
        "Combien de lignes ?", adapter=adapter, model=model, settings=make_settings()
    )
    assert outcome.succeeded
    assert [q.ok for q in outcome.executed] == [False, True]
    assert "nexiste_pas" in outcome.executed[0].error
    assert outcome.result.rows == [[4]]


def test_ecriture_bloquee_par_le_garde_fou(adapter):
    model = scripted_model(
        [
            [ToolCallPart("run_sql", {"query": "DROP VIEW mini"})],
            [TextPart("Je n'ai pas pu modifier la base.")],
        ]
    )
    outcome = run_retrieval(
        "Supprime la table.", adapter=adapter, model=model, settings=make_settings()
    )
    assert not outcome.succeeded
    assert outcome.executed[0].ok is False
    assert "interdit" in outcome.executed[0].error or "SELECT" in outcome.executed[0].error


def test_list_tables_disponible(adapter):
    model = scripted_model(
        [
            [ToolCallPart("list_tables", {})],
            [TextPart("La source contient la table mini.")],
        ]
    )
    outcome = run_retrieval(
        "Quelles tables ?", adapter=adapter, model=model, settings=make_settings()
    )
    assert "mini" in outcome.summary


def test_boucle_infinie_coupee_par_la_limite(adapter):
    def responder_infini(messages, info):
        return ModelResponse(parts=[ToolCallPart("run_sql", {"query": "SELECT oops FROM mini"})])

    model = FunctionModel(responder_infini)
    with pytest.raises(UsageLimitExceeded):
        run_retrieval(
            "Question impossible",
            adapter=adapter,
            model=model,
            settings=make_settings(retrieval_request_limit=4),
        )


# --- le dictionnaire de la source, jusqu'à celui qui écrit le SQL ------------
#
# Le dictionnaire était servi à l'agent SYSTÈME et pas à celui-ci. Mesuré sur le
# catalogue de démonstration, dans une même conversation et à un tour d'écart :
# l'agent cite « seules les sessions au statut `T` ont abouti », puis écrit
# `WHERE statut = 'E'` et rend 1 405 au lieu de 42 281 — sans exception et sans
# log. Ces tests tiennent le chemin par lequel le texte arrive au prompt.

DICO_DE_SOURCE = """\
# Dictionnaire — `mini`

## Les pièges de cette source

`survie` vaut 1 pour un survivant. Les lignes à 0 ne sont PAS des survivants.
"""


def _prompt_systeme_vu(model_calls: list) -> str:
    """Le prompt système effectivement envoyé au modèle, au premier appel."""
    from pydantic_ai.messages import ModelRequest, SystemPromptPart

    for message in model_calls[0]:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    return part.content
    raise AssertionError("aucun prompt système")


def _mouchard(reponses: list[list]):
    """Un modèle scripté qui garde les messages qu'on lui a envoyés."""
    vus: list = []
    restantes = [ModelResponse(parts=parts) for parts in reponses]

    def responder(messages, info):
        vus.append(list(messages))
        return restantes.pop(0)

    return FunctionModel(responder), vus


def test_le_dictionnaire_arrive_dans_le_prompt_de_l_agent_sql(adapter):
    model, vus = _mouchard([[TextPart("ok")]])
    run_retrieval(
        "Combien de survivants ?",
        adapter=adapter,
        model=model,
        settings=make_settings(),
        dictionary=DICO_DE_SOURCE,
    )
    prompt = _prompt_systeme_vu(vus)
    assert "Les lignes à 0 ne sont PAS des survivants" in prompt
    # et la consigne qui en fait une règle, pas une note de bas de page
    assert "il fait autorité" in prompt


def test_sans_dictionnaire_le_prompt_est_celui_d_avant(adapter):
    """Une source qui n'en déclare pas ne paie rien — `titanic` et `iris` en sont."""
    from data_analyst_agent import prompts

    model, vus = _mouchard([[TextPart("ok")]])
    run_retrieval("Combien ?", adapter=adapter, model=model, settings=make_settings())
    attendu = prompts.render(prompts.RETRIEVAL, dialect=adapter.dialect)
    assert _prompt_systeme_vu(vus) == attendu


def test_un_dictionnaire_trop_long_est_coupe_et_l_amputation_remonte(adapter):
    """L'utilisateur doit l'apprendre de l'application, pas de la qualité des réponses."""
    model, vus = _mouchard([[TextPart("ok")]])
    outcome = run_retrieval(
        "Combien ?",
        adapter=adapter,
        model=model,
        settings=make_settings(dictionary_max_chars=60),
        dictionary=DICO_DE_SOURCE,
    )
    assert outcome.dictionary_notice
    assert "Les pièges de cette source" in outcome.dictionary_notice
    assert "ATTENTION" in _prompt_systeme_vu(vus)


def test_un_dictionnaire_entier_ne_signale_rien(adapter):
    outcome = run_retrieval(
        "Combien ?",
        adapter=adapter,
        model=scripted_model([[TextPart("ok")]]),
        settings=make_settings(),
        dictionary=DICO_DE_SOURCE,
    )
    assert outcome.dictionary_notice == ""


# --- get_schema accepte un argument de table plutôt que de le refuser --------
#
# L'outil n'en prenait aucun. Sous vLLM, le modèle lui passait
# `{"table_name": ...}`, la validation refusait, il réessayait, et le budget de
# reprise valant 1 le tour mourait sur « la source de données n'a pas pu être
# interrogée » — 9 fois sur 9, sur trois sources. Jamais sous Ollama : le même
# modèle, deux gabarits d'appel d'outil.


def test_get_schema_sans_argument_rend_le_schema_complet(adapter):
    outcome = run_retrieval(
        "Décris la source.",
        adapter=adapter,
        model=scripted_model(
            [[ToolCallPart("get_schema", {})], [TextPart("La table mini a deux colonnes.")]]
        ),
        settings=make_settings(),
    )
    assert outcome.tools_used == ["get_schema"]


def test_get_schema_accepte_un_nom_de_table_au_lieu_de_refuser(adapter):
    """Le tour ABOUTIT là où il mourait : plus de validation à échouer."""
    outcome = run_retrieval(
        "Combien de lignes en tout ?",
        adapter=adapter,
        model=scripted_model(
            [
                [ToolCallPart("get_schema", {"table_name": "mini"})],
                [ToolCallPart("run_sql", {"query": "SELECT count(*) AS n FROM mini"})],
                [TextPart("4 lignes.")],
            ]
        ),
        settings=make_settings(),
    )
    assert outcome.succeeded
    assert outcome.result.rows == [[4]]


def test_une_table_inconnue_rend_le_schema_complet_et_le_dit(adapter):
    """Le modèle qui invente un nom a besoin de voir les vrais, pas d'un refus.

    Même choix que ``run_sql``, qui rend son erreur SQL en texte au lieu de
    lever : une boucle de correction se nourrit de ce qu'on lui rend.
    """
    from data_analyst_agent.agents.retrieval.agent import _schema_lisible

    rendu = _schema_lisible(adapter.schema(), "passengers")
    assert "inconnue" in rendu
    assert "mini" in rendu  # les tables qui existent, et le schéma complet
    assert "sexe" in rendu


def test_un_nom_de_table_decore_est_reconnu(adapter):
    """Le modèle écrit parfois `"mini"` ou `mini` — la décoration n'est pas un nom."""
    from data_analyst_agent.agents.retrieval.agent import _schema_lisible

    for ecriture in ("mini", " MINI ", '"mini"', "`mini`"):
        rendu = _schema_lisible(adapter.schema(), ecriture)
        assert "inconnue" not in rendu
        assert "sexe" in rendu
