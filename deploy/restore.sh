#!/usr/bin/env bash
# Restauration d'une archive produite par deploy/backup.sh.
#
#     sudo deploy/restore.sh /var/backups/data-analyst-agent/daa-….tar.gz
#
# Trois propriétés, tenues dans cet ordre :
#
#   - le service est ARRÊTÉ avant qu'on touche au dossier de données. Restaurer
#     sous un service qui écrit, c'est restaurer un état que personne n'a eu ;
#   - le dossier en place n'est jamais effacé : il est DÉPLACÉ à côté, sous
#     <dossier>.avant-restauration-<horodatage>. Une restauration qui se révèle
#     être la mauvaise archive doit pouvoir se défaire ;
#   - le fichier d'environnement n'est reposé QUE s'il manque. Sur une machine
#     neuve c'est ce qu'on veut ; sur une machine en service, écraser la
#     configuration courante avec celle d'hier serait un effet de bord que
#     personne n'a demandé. Le chemin de celui de l'archive est affiché.
#
# Pour éprouver une archive SANS toucher au service, poser DAA_DATA_CIBLE sur un
# dossier jetable : le service n'est alors ni arrêté ni relancé.

set -euo pipefail
umask 077

ARCHIVE="${1:?usage : restore.sh <archive.tar.gz>}"
[[ -r "$ARCHIVE" ]] || { echo "restore: archive illisible : $ARCHIVE" >&2; exit 1; }

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${DAA_ENV_FILE:-/etc/data-analyst-agent/daa.env}"
[[ -r "$ENV_FILE" ]] || { echo "restore: $ENV_FILE illisible (sudo ?)" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a
# Réglage du conteneur, pas du script : il pointerait mktemp sous le dossier
# de données, qui n'existe pas encore sur une machine neuve (cf. daactl).
unset TMPDIR

# Essai à blanc sur un dossier jetable : ni arrêt ni relance du service.
CIBLE="${DAA_DATA_CIBLE:-${DAA_DATA_DIR:?DAA_DATA_DIR absent de $ENV_FILE}}"
A_BLANC=0
[[ -n "${DAA_DATA_CIBLE:-}" ]] && A_BLANC=1

CHANTIER="$(mktemp -d)"
trap 'rm -rf "$CHANTIER"' EXIT
tar --extract --gzip --file "$ARCHIVE" --directory "$CHANTIER"
RACINE="$(find "$CHANTIER" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -d "$RACINE/data" ]] || { echo "restore: archive invalide (pas de data/)" >&2; exit 1; }

echo "archive  : $ARCHIVE"
sed 's/^/  /' "$RACINE/MANIFEST" 2>/dev/null
echo "cible    : $CIBLE"

if (( ! A_BLANC )); then
    echo "arrêt du service…"
    # daactl délègue à systemctl si l'unité est installée : restaurer sous le
    # dos de systemd lui laisserait un état faux (cf. daactl, `par_systemd`).
    "$DEPLOY_DIR/daactl" stop || true
fi

if [[ -e "$CIBLE" ]]; then
    ECARTE="$CIBLE.avant-restauration-$(date +%Y%m%d-%H%M%S)"
    mv "$CIBLE" "$ECARTE"
    echo "dossier en place écarté (non supprimé) : $ECARTE"
fi

# `cp -a` puis rm, et non un mv depuis /tmp : le chantier peut être sur un autre
# système de fichiers, où mv n'est pas un rename mais une copie sans droits.
cp -a "$RACINE/data" "$CIBLE"
chown -R 1000:1000 "$CIBLE"

if [[ ! -e "$ENV_FILE" ]]; then
    install -D -m 0600 "$RACINE/daa.env" "$ENV_FILE"
    echo "fichier d'environnement reposé : $ENV_FILE"
else
    echo "fichier d'environnement CONSERVÉ : $ENV_FILE"
    echo "  (l'archive en porte un ; pour le comparer : tar -xzOf '$ARCHIVE' --wildcards '*/daa.env')"
fi

echo "conversations restaurées : $(find "$CIBLE/workspaces" -name transcript.json 2>/dev/null | wc -l)"

if (( A_BLANC )); then
    echo "essai à blanc (DAA_DATA_CIBLE posé) — le service n'a pas été touché."
else
    "$DEPLOY_DIR/daactl" start
fi
