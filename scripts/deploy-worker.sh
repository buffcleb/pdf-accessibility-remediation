#!/usr/bin/env bash
# Publish the web app as a Cloudflare Worker (optional: the default serves it from the cluster's web pod).
#
#   scripts/deploy-worker.sh             build the web app and deploy the Worker
#   scripts/deploy-worker.sh --dry-run   build and check, without publishing
#
# The Worker serves the app's files from Cloudflare's edge on APP_HOST and forwards /api/ to the portal API at
# API_ORIGIN_HOST on the cluster (scripts/deploy.sh with FRONTEND=worker sets that up). See deploy/k8s/README.md.
#
# Settings: deploy/k8s/deploy.env (APP_HOST, API_ORIGIN_HOST, WORKER_NAME). Cloudflare sign-in: CLOUDFLARE_API_TOKEN
# (and CLOUDFLARE_ACCOUNT_ID), or `npx wrangler login` once. The Access service token the Worker sends to the
# origin: ACCESS_CLIENT_ID and ACCESS_CLIENT_SECRET in deploy/k8s/worker.env (stored as Worker secrets).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
die() { echo "deploy-worker: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

[ -f deploy/k8s/deploy.env ] || die "no deploy/k8s/deploy.env yet: run scripts/deploy.sh init"
set -a
# shellcheck disable=SC1091
. deploy/k8s/deploy.env
set +a
export WORKER_NAME="${WORKER_NAME:-pdf-remediation-web}"
for v in APP_HOST API_ORIGIN_HOST; do [ -n "${!v:-}" ] || die "$v isn't set in deploy/k8s/deploy.env"; done
[ "${FRONTEND:-cluster}" = worker ] || log "note: FRONTEND isn't 'worker' in deploy.env, so the cluster still serves the app itself"
command -v npm >/dev/null || die "npm isn't installed"

WRANGLER=(npx --yes wrangler@4)
CONF=deploy/cloudflare-worker/wrangler.toml

log "building the web app"
(cd web && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null) || die "the web build failed"

perl -pe 's/\$\{([A-Z_]+)\}/exists $ENV{$1} ? $ENV{$1} : die "unset: $1\n"/ge' \
  deploy/cloudflare-worker/wrangler.toml.template > "$CONF"

if [ "$DRY" = 1 ]; then
  "${WRANGLER[@]}" deploy -c "$CONF" --dry-run >/dev/null || die "wrangler's check failed"
  log "checked: would publish $WORKER_NAME on https://$APP_HOST, forwarding /api/ to https://$API_ORIGIN_HOST"
  exit 0
fi

log "publishing $WORKER_NAME on https://$APP_HOST"
"${WRANGLER[@]}" deploy -c "$CONF" || die "publishing failed (sign in: CLOUDFLARE_API_TOKEN, or npx wrangler login)"

if [ -f deploy/k8s/worker.env ]; then
  for key in ACCESS_CLIENT_ID ACCESS_CLIENT_SECRET; do
    value="$(sed -n "s/^$key=//p" deploy/k8s/worker.env | sed -e 's/^"//' -e 's/"$//')"
    [ -n "$value" ] || continue
    printf '%s' "$value" | "${WRANGLER[@]}" secret put "$key" -c "$CONF" >/dev/null || die "couldn't store $key"
    log "stored $key as a Worker secret"
  done
else
  log "no deploy/k8s/worker.env: the Worker calls the origin without an Access service token"
fi
log "published: https://$APP_HOST"
