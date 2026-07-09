# Image de la sandbox d'exécution (docs/CADRAGE.md §6).
# Le durcissement (réseau coupé, quotas, cap-drop, rootfs read-only) est appliqué
# par le client au `docker run` — l'image reste un socle scientifique minimal.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Dépendances verrouillées (requirements.txt compilé par uv depuis requirements.in)
COPY requirements.txt /opt/sandbox/requirements.txt
RUN uv pip install --system --no-cache -r /opt/sandbox/requirements.txt

COPY bridge.py /opt/sandbox/bridge.py

# Utilisateur non privilégié (le client force aussi --user au run)
RUN useradd --create-home --uid 1000 sandbox
USER 1000:1000

CMD ["python", "/opt/sandbox/bridge.py"]
