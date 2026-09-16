#!/usr/bin/env bash
# Le certificat que présente le mandataire, et l'autorité qui le signe.
#
#     sudo deploy/tls-cert.sh [nom-du-serveur…]
#
# Sans argument, le nom est celui de la machine (`hostname -f`), et toutes ses
# adresses IPv4 sont ajoutées en SAN — c'est ce qui permet de joindre le service
# par https://10.1.3.6:8443/ comme par son nom.
#
# CE QUI EST FABRIQUÉ, et pourquoi deux fichiers et non un :
#
#   ca.crt / ca.key   une autorité locale, valable dix ans. Le .crt est PUBLIC :
#                     c'est lui qu'on installe une fois dans le magasin de
#                     confiance des postes, et lui seul.
#   serveur.crt/.key  le certificat réellement présenté, signé par elle, valable
#                     825 jours (le plafond que les navigateurs acceptent encore
#                     pour une autorité privée).
#
# Une autorité plutôt qu'un certificat auto-signé seul, parce que le
# RENOUVELLEMENT est le vrai sujet : avec une autorité, refaire le certificat du
# serveur ne demande de retoucher AUCUN poste. Avec un auto-signé, chaque
# renouvellement redemande à tout le monde d'accepter une nouvelle exception —
# et une population entraînée à cliquer « continuer quand même » est une
# population sur laquelle l'avertissement ne protège plus de rien.
#
# LES CLÉS PRIVÉES NE SONT PAS DANS LE DÉPÔT et n'y entrent jamais : elles
# vivent sous DAA_TLS_DIR (défaut /etc/data-analyst-agent/tls), en 0600 root.

set -euo pipefail
umask 077

TLS_DIR="${DAA_TLS_DIR:-/etc/data-analyst-agent/tls}"
JOURS_AUTORITE="${DAA_TLS_CA_DAYS:-3650}"
JOURS_SERVEUR="${DAA_TLS_DAYS:-825}"

command -v openssl >/dev/null || { echo "tls-cert: openssl absent" >&2; exit 1; }

# Les noms : ceux passés en argument, sinon celui de la machine.
NOMS=("$@")
if [[ ${#NOMS[@]} -eq 0 ]]; then
    NOMS=("$(hostname -f 2>/dev/null || hostname)")
    court="$(hostname -s 2>/dev/null || true)"
    [[ -n "$court" && "$court" != "${NOMS[0]}" ]] && NOMS+=("$court")
fi
PRINCIPAL="${NOMS[0]}"

# Les adresses de la machine, en SAN : sur un parc sans DNS interne, on joint le
# service par son adresse, et un certificat sans SAN d'adresse serait refusé.
mapfile -t ADRESSES < <(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9.]+$' || true)

SAN=""
for nom in "${NOMS[@]}"; do SAN+="DNS:$nom,"; done
for ip in "${ADRESSES[@]}" 127.0.0.1; do SAN+="IP:$ip,"; done
SAN="${SAN%,}"

install -d -m 0700 "$TLS_DIR"

# L'autorité n'est fabriquée qu'une fois. La refaire invaliderait tous les
# postes où elle est déjà installée — c'est exactement ce qu'on veut éviter.
if [[ -f "$TLS_DIR/ca.key" ]]; then
    echo "autorité déjà en place : $TLS_DIR/ca.crt (conservée)"
else
    openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
        -days "$JOURS_AUTORITE" \
        -keyout "$TLS_DIR/ca.key" -out "$TLS_DIR/ca.crt" \
        -subj "/CN=data-analyst-agent — autorité locale/O=$PRINCIPAL" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
    echo "autorité créée : $TLS_DIR/ca.crt (valable $JOURS_AUTORITE jours)"
fi

# Le certificat du serveur, lui, se refait à volonté : c'est l'opération de
# renouvellement, et elle ne touche à aucun poste.
CHANTIER="$(mktemp -d)"
trap 'rm -rf "$CHANTIER"' EXIT

openssl req -new -newkey rsa:2048 -sha256 -nodes \
    -keyout "$TLS_DIR/serveur.key.neuve" -out "$CHANTIER/demande.csr" \
    -subj "/CN=$PRINCIPAL" 2>/dev/null

cat > "$CHANTIER/extensions" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
EOF

openssl x509 -req -in "$CHANTIER/demande.csr" -sha256 \
    -CA "$TLS_DIR/ca.crt" -CAkey "$TLS_DIR/ca.key" -CAcreateserial \
    -days "$JOURS_SERVEUR" -extfile "$CHANTIER/extensions" \
    -out "$TLS_DIR/serveur.crt.neuf" 2>/dev/null

# Bascule à la fin seulement : un renouvellement interrompu au milieu laissait
# sinon une clé neuve avec un certificat d'hier, et le mandataire ne redémarrait
# plus. On ne remplace la paire en service qu'une fois la nouvelle complète.
mv "$TLS_DIR/serveur.key.neuve" "$TLS_DIR/serveur.key"
mv "$TLS_DIR/serveur.crt.neuf" "$TLS_DIR/serveur.crt"
chmod 0600 "$TLS_DIR/ca.key" "$TLS_DIR/serveur.key"
chmod 0644 "$TLS_DIR/ca.crt" "$TLS_DIR/serveur.crt"

echo "certificat  : $TLS_DIR/serveur.crt"
openssl x509 -in "$TLS_DIR/serveur.crt" -noout -subject -dates -ext subjectAltName | sed 's/^/  /'
echo
echo "À installer sur les postes, UNE fois : $TLS_DIR/ca.crt (il est public)."
echo "Puis : sudo deploy/daactl restart"
