# Image de l'APPLICATION (API + page de chat). L'image du BAC À SABLE est une
# autre image, décrite par src/data_analyst_agent/sandbox/image/Dockerfile et
# construite par la CI : celle-ci ne la refait pas, elle la lance.
#
# Deux choses vivent donc ici et nulle part ailleurs :
#   - l'environnement Python verrouillé par uv.lock ;
#   - le CLI docker, parce que le client du bac à sable est un `docker run`.
#     Le CLI, pas le démon : le conteneur pilote le démon de l'hôte par sa
#     socket (cf. deploy/compose.yaml, et docs/INSTALLATION.md §« le bac à
#     sable vu du conteneur »).

FROM python:3.12-slim AS base

# Le CLI docker, pris tel quel à l'image officielle : ~50 Mo de binaire statique
# contre ~250 Mo pour `apt install docker.io`, qui embarque en plus un démon
# dont ce conteneur n'a que faire.
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Les dépendances AVANT le code : une retouche de src/ ne réinstalle pas
# pandas et scikit-learn. `--no-dev` écarte pytest, playwright et le reste de
# l'outillage — ils n'ont rien à faire dans une image de service.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/
COPY scripts/ scripts/
RUN uv sync --locked --no-dev

# Compte non privilégié, uid/gid 1000 — celui qui possède le dossier de données
# sur l'hôte. Le groupe docker n'est PAS ajouté ici : son gid dépend de la
# machine, il est passé au lancement (`group_add` du compose). Une image qui
# porterait un gid en dur ne démarrerait que sur la machine où elle a été
# construite.
RUN groupadd --gid 1000 daa && useradd --uid 1000 --gid 1000 --no-create-home daa
USER 1000:1000

EXPOSE 8000

# Une sonde qui n'a pas besoin de curl : /health est la seule route ouverte sans
# session, et c'est exactement ce qu'une sonde doit pouvoir atteindre.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "data_analyst_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
