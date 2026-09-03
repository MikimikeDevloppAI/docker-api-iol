#!/usr/bin/env bash
# Met à jour l'API IOL sur le VPS Hostinger (stack Docker Compose + Traefik dans /root).
#
# Appelé par GitHub Actions (job "deploy") après les tests, ou à la main :
#   cd /root/iol-api && git pull --ff-only && bash deploy/update-vps.sh
#
# Le code doit déjà être à la version voulue dans APP_DIR (git). Le script :
#   1. reconstruit l'image avec APP_VERSION = SHA court, redémarre le service
#   2. attend que GET /health réponde avec cette version
#   3. en cas d'échec : logs, puis retour à PREVIOUS_SHA (1er argument) si fourni
set -euo pipefail

APP_DIR="${APP_DIR:-/root/iol-api}"
COMPOSE_DIR="${COMPOSE_DIR:-/root}"
SERVICE="${SERVICE:-iol-api}"
PREVIOUS_SHA="${1:-}"

log() { echo "==> $*"; }

current_version() {
  docker exec "$SERVICE" curl -fsS -m 3 http://localhost:5000/health 2>/dev/null \
    | sed -E 's/.*"version": *"([^"]+)".*/\1/' || true
}

build_and_check() {
  local version="$1"
  (cd "$COMPOSE_DIR" && APP_VERSION="$version" docker compose up -d --build "$SERVICE")
  for _ in $(seq 1 45); do
    if [ "$(current_version)" = "$version" ]; then
      return 0
    fi
    sleep 2
  done
  return 1
}

SHA=$(git -C "$APP_DIR" rev-parse --short HEAD)
log "Déploiement de $SHA ($(git -C "$APP_DIR" log -1 --format=%s | cut -c1-70))"

if build_and_check "$SHA"; then
  log "OK : version $SHA en ligne"
  docker image prune -f >/dev/null 2>&1 || true   # couches de build orphelines uniquement
  exit 0
fi

log "ÉCHEC : /health ne renvoie pas la version $SHA (obtenu : '$(current_version)')"
docker logs --tail 40 "$SERVICE" 2>&1 || true

if [ -n "$PREVIOUS_SHA" ] && [ "$PREVIOUS_SHA" != "$(git -C "$APP_DIR" rev-parse HEAD)" ]; then
  log "Rollback vers $PREVIOUS_SHA"
  git -C "$APP_DIR" reset -q --hard "$PREVIOUS_SHA"
  PREV_SHORT=$(git -C "$APP_DIR" rev-parse --short HEAD)
  if build_and_check "$PREV_SHORT"; then
    log "Rollback OK : $PREV_SHORT de nouveau en ligne"
  else
    log "Rollback ÉCHOUÉ : intervention manuelle nécessaire"
  fi
fi
exit 1
