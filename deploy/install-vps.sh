#!/usr/bin/env bash
# Installation / mise à jour de l'API IOL sur un VPS Ubuntu 24.04 (Docker + Nginx + Let's Encrypt).
# Idempotent : relançable pour mettre à jour.
#
# Usage (en root sur le VPS) :
#   DOMAIN=api-iol.vps.allia-solutions.ch bash install-vps.sh
#
# Variables :
#   DOMAIN         nom DNS servi par Nginx (défaut : api-iol.vps.allia-solutions.ch)
#   CERTBOT_EMAIL  email Let's Encrypt (défaut : vide -> --register-unsafely-without-email)
#   INSTALL_DIR    dossier du clone (défaut : /opt/iol-api)
#   REPO           dépôt git (défaut : https://github.com/MikimikeDevloppAI/docker-api-iol.git)
#   BRANCH         branche/commit à déployer (défaut : master)
set -euo pipefail

DOMAIN="${DOMAIN:-api-iol.vps.allia-solutions.ch}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/iol-api}"
REPO="${REPO:-https://github.com/MikimikeDevloppAI/docker-api-iol.git}"
BRANCH="${BRANCH:-master}"

log() { echo -e "\n\033[1;34m==> $*\033[0m"; }

[ "$(id -u)" -eq 0 ] || { echo "Lancer en root (sudo -i)"; exit 1; }

# ── 1. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  log "Installation de Docker"
  apt-get update -q
  apt-get install -y -q ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
else
  log "Docker déjà présent : $(docker --version)"
fi
docker compose version >/dev/null 2>&1 || { apt-get install -y -q docker-compose-plugin; }

# ── 2. Nginx + certbot ───────────────────────────────────────────────────────
# Le serveur héberge peut-être déjà autre chose : on refuse d'écraser un autre service sur 80/443.
for PORT in 80 443; do
  OWNER=$(ss -tlnpH "sport = :$PORT" 2>/dev/null | grep -oE 'users:\(\("[^"]+"' | head -1 | cut -d'"' -f2 || true)
  if [ -n "$OWNER" ] && [ "$OWNER" != "nginx" ]; then
    echo "Le port $PORT est déjà utilisé par '$OWNER' (pas nginx). Adapter le reverse proxy existant"
    echo "pour router $DOMAIN vers http://127.0.0.1:5000 avec proxy_read_timeout 180s, puis relancer avec SKIP_NGINX=1."
    [ "${SKIP_NGINX:-0}" = "1" ] || exit 1
  fi
done
if [ "${SKIP_NGINX:-0}" = "1" ]; then
  apt-get install -y -q git
else
  apt-get install -y -q nginx certbot python3-certbot-nginx git
  systemctl enable --now nginx
fi

# ── 3. Code ──────────────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  log "Mise à jour du dépôt dans $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch -q origin
  git -C "$INSTALL_DIR" checkout -q "$BRANCH"
  git -C "$INSTALL_DIR" pull -q --ff-only origin "$BRANCH" || true
else
  log "Clone du dépôt dans $INSTALL_DIR"
  git clone -q --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
fi
SHA=$(git -C "$INSTALL_DIR" rev-parse --short HEAD)
log "Version à déployer : $SHA"

# ── 4. Conteneur ─────────────────────────────────────────────────────────────
log "Build + démarrage du conteneur (APP_VERSION=$SHA)"
cd "$INSTALL_DIR"
mkdir -p screenshots
APP_VERSION="$SHA" docker compose up -d --build
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:5000/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:5000/health; echo

# ── 5. Vhost Nginx ───────────────────────────────────────────────────────────
if [ "${SKIP_NGINX:-0}" = "1" ]; then
  log "SKIP_NGINX=1 : vhost et certificat non gérés par ce script. Version $SHA déployée sur 127.0.0.1:5000."
  exit 0
fi
log "Vhost Nginx pour $DOMAIN"
VHOST=/etc/nginx/sites-available/iol-api.conf
if [ ! -f "$VHOST" ]; then
cat > "$VHOST" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    # Corps JSON petits, mais PNG de ~350 Ko en réponse et calcul long (30-80 s)
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
    }

    access_log /var/log/nginx/iol-api.access.log;
    error_log  /var/log/nginx/iol-api.error.log;
}
EOF
fi
ln -sf "$VHOST" /etc/nginx/sites-enabled/iol-api.conf
nginx -t && systemctl reload nginx

# ── 6. HTTPS (seulement si le DNS pointe déjà ici) ───────────────────────────
MY_IP=$(curl -fsS -4 https://api.ipify.org || hostname -I | awk '{print $1}')
DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk '{print $1; exit}' || true)
if [ -n "$DNS_IP" ] && [ "$DNS_IP" = "$MY_IP" ]; then
  if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    log "Certificat Let's Encrypt pour $DOMAIN"
    if [ -n "$CERTBOT_EMAIL" ]; then
      certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect
    else
      certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect
    fi
  else
    log "Certificat déjà présent pour $DOMAIN"
  fi
  log "OK : https://$DOMAIN/health"
  curl -fsS "https://$DOMAIN/health"; echo
else
  log "DNS de $DOMAIN -> '${DNS_IP:-aucun}' (ce serveur : $MY_IP). HTTPS non configuré."
  echo "    Faire pointer $DOMAIN vers $MY_IP puis relancer ce script pour obtenir le certificat."
  echo "    En attendant, test HTTP : curl -H 'Host: $DOMAIN' http://$MY_IP/health"
fi

# ── 7. Pare-feu (ufw) ────────────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  log "ufw actif : ouverture 80/443"
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi

log "Terminé. Version $SHA déployée."
echo "    Logs :        docker logs -f iol-api"
echo "    Mise à jour : DOMAIN=$DOMAIN bash $INSTALL_DIR/deploy/install-vps.sh"
