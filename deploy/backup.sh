#!/usr/bin/env bash
# Sauvegarde : tout ce qui, perdu, ne se reconstruit pas.
#
#     sudo deploy/backup.sh [dossier-de-destination]
#
# Ce qui entre dans l'archive, et pourquoi :
#
#   users.yaml   les comptes et les empreintes de mots de passe. Perdu, plus
#                personne n'entre — il n'y a ni inscription ouverte ni compte
#                par défaut.
#   auth/        les sessions ouvertes et les compteurs d'échecs. Perdu, tout
#                le monde se reconnecte : gênant, pas grave. Emporté quand même,
#                pour qu'une restauration ne déconnecte personne.
#   workspaces/  les conversations et leurs artefacts — tableaux, figures, code.
#                C'est le travail des gens ; c'est ce qui compte.
#   sources/     le catalogue et les fichiers qu'il déclare. Le catalogue est
#                une déclaration locale : il n'est dans aucun dépôt.
#   models/      le registre et les modèles .joblib.
#   daa.env      le fichier d'environnement. Sans lui, l'archive se restaure
#                mais le service ne redémarre pas : il ne saurait ni où est le
#                moteur ni comment ouvrir Postgres.
#
# Ce qui n'y entre PAS : l'image, reconstruite depuis le dépôt ; les bases
# Postgres des sources, qui ont leur propre sauvegarde et ne sont pas à nous.
#
# L'ARCHIVE PORTE DES SECRETS (le mot de passe Postgres, par daa.env) : elle est
# écrite en 0600, et elle doit être conservée comme telle.

set -euo pipefail
umask 077

ENV_FILE="${DAA_ENV_FILE:-/etc/data-analyst-agent/daa.env}"
[[ -r "$ENV_FILE" ]] || { echo "backup: $ENV_FILE illisible (sudo ?)" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a
# Réglage du conteneur, pas du script : il pointerait mktemp sous le dossier
# de données, qui n'existe pas encore sur une machine neuve (cf. daactl).
unset TMPDIR
: "${DAA_DATA_DIR:?DAA_DATA_DIR absent de $ENV_FILE}"

DESTINATION="${1:-/var/backups/data-analyst-agent}"
HORODATAGE="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$DESTINATION/daa-$HORODATAGE.tar.gz"
install -d -m 0700 "$DESTINATION"

CHANTIER="$(mktemp -d)"
trap 'rm -rf "$CHANTIER"' EXIT
RACINE="$CHANTIER/daa-$HORODATAGE"
install -d -m 0700 "$RACINE"

cp -a "$ENV_FILE" "$RACINE/daa.env"
cp -a "$DAA_DATA_DIR" "$RACINE/data"

# Le manifeste sert la RESTAURATION, pas la sauvegarde : c'est lui qu'on relit
# pour savoir ce qu'on devrait retrouver. Un compte de conversations qui ne
# tombe pas juste après restauration est un problème visible en une commande.
{
    echo "horodatage    : $HORODATAGE"
    echo "machine       : $(hostname)"
    echo "dossier source: $DAA_DATA_DIR"
    echo "comptes       : $(grep -c '^\s*-\s*login:' "$RACINE/data/users.yaml" 2>/dev/null || echo 0)"
    echo "conversations : $(find "$RACINE/data/workspaces" -name transcript.json 2>/dev/null | wc -l)"
    echo "artefacts     : $(find "$RACINE/data/workspaces" -type f 2>/dev/null | wc -l)"
} > "$RACINE/MANIFEST"

tar --create --gzip --file "$ARCHIVE" --directory "$CHANTIER" "daa-$HORODATAGE"
chmod 0600 "$ARCHIVE"

echo "archive : $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
sed 's/^/  /' "$RACINE/MANIFEST"
echo
echo "Cohérence : chaque fichier est écrit atomiquement (rename), donc aucun"
echo "fichier n'est à moitié dans l'archive. Pour une photo cohérente à la"
echo "conversation près, arrêter le service avant : daactl stop && daactl backup."
