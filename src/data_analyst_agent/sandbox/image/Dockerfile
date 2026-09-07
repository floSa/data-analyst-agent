# Image de la sandbox d'exécution (docs/CADRAGE.md §6).
# Le durcissement (réseau coupé, quotas, cap-drop, rootfs read-only) est appliqué
# par le client au `docker run` — l'image reste un socle scientifique minimal.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Dépendances verrouillées (requirements.txt compilé par uv depuis requirements.in)
COPY requirements.txt /opt/sandbox/requirements.txt
RUN uv pip install --system --no-cache -r /opt/sandbox/requirements.txt

COPY bridge.py /opt/sandbox/bridge.py

# Utilisateur non privilégié. C'est ICI, et nulle part ailleurs, que la propriété
# tient : `docker_run_command` ne passe PAS de `--user` — le commentaire d'origine
# l'affirmait, c'était faux. Conséquence à connaître : une image tierce désignée par
# DAA_SANDBOX_IMAGE tournerait en root, le client ne l'en empêcherait pas.
RUN useradd --create-home --uid 1000 sandbox
USER 1000:1000

CMD ["python", "/opt/sandbox/bridge.py"]
