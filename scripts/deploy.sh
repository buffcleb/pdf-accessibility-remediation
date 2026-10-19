#!/usr/bin/env bash
# Deploy the service to the Kubernetes cluster (the images scripts/build.sh pushed).
#
#   scripts/deploy.sh init          first time: make deploy/k8s/deploy.env and the two secret files
#   scripts/deploy.sh               deploy or update everything (backs up the database before migrations)
#   scripts/deploy.sh status        what's running
#   scripts/deploy.sh render        write the manifests as they'd be applied to deploy/k8s/.rendered/
#
# What `deploy` does, in order: checks the cluster and its storage; makes the namespace, the registry pull secret
# and the secrets (from deploy/k8s/engine.env and portal.env, split by who reads what); starts Postgres, Redis and SeaweedFS; backs up
# the portal database if it already exists (scripts/backup.sh, into ./backups); runs the portal's migrations; then
# rolls out the AI gateway, engine, portal and web app and checks they answer.
#
# Options:
#   -y, --yes            don't ask before changing the cluster
#   --skip-backup        don't back up the database before migrations (not recommended)
#   -h, --help
#
# Settings: deploy/k8s/deploy.env (see deploy/k8s/deploy.env.example). Image tags come from deploy/k8s/.last-build
# (written by scripts/build.sh) unless deploy.env sets them. The registry pull secret uses GHCR_PULL_TOKEN, else
# `gh auth token` (it needs read:packages).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
K8S="$ROOT/deploy/k8s"
die() { echo "deploy: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

CMD=deploy YES=0 BACKUP=1
while [ $# -gt 0 ]; do
  case "$1" in
    init|deploy|status|render) CMD="$1" ;;
    -y|--yes) YES=1 ;;
    --skip-backup) BACKUP=0 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

rand() { openssl rand -hex "${1:-24}"; }

# A value the two secrets must agree on: the Redis password (REDIS_PASSWORD in engine.env, PORTAL_REDIS_PASSWORD
# in portal.env) and the engine's API key (SERVICE_API_KEY, PORTAL_ENGINE_API_KEY). Made and written to both files
# when neither has it, copied when one has it; two different values are left for you to settle. Run at init and
# before every deploy, so a deployment made before these existed gets them.
shared_key() {
  local engine_key="$1" portal_key="$2" a b v
  a="$(sed -n "s/^$engine_key=//p" "$K8S/engine.env" | head -1)"
  b="$(sed -n "s/^$portal_key=//p" "$K8S/portal.env" | head -1)"
  if [ -n "$a" ] && [ -n "$b" ]; then
    [ "$a" = "$b" ] || die "$engine_key in deploy/k8s/engine.env and $portal_key in portal.env differ: make them the same"
  elif [ -n "$a" ]; then
    echo "$portal_key=$a" >> "$K8S/portal.env"; log "copied $engine_key from engine.env to portal.env as $portal_key"
  elif [ -n "$b" ]; then
    echo "$engine_key=$b" >> "$K8S/engine.env"; log "copied $portal_key from portal.env to engine.env as $engine_key"
  else
    v="$(rand 24)"
    echo "$engine_key=$v" >> "$K8S/engine.env"; echo "$portal_key=$v" >> "$K8S/portal.env"
    log "made $engine_key (engine.env) and $portal_key (portal.env)"
  fi
}
shared_keys() {
  shared_key REDIS_PASSWORD PORTAL_REDIS_PASSWORD
  shared_key SERVICE_API_KEY PORTAL_ENGINE_API_KEY
  # The portal's own database account (#337, deploy/k8s/app-role.sql), made once
  if ! grep -q '^PORTAL_APP_DB_PASSWORD=' "$K8S/portal.env"; then
    echo "PORTAL_APP_DB_PASSWORD=$(rand 24)" >> "$K8S/portal.env"; log "made PORTAL_APP_DB_PASSWORD (portal.env)"
  fi
  # Monitoring's (#397): the Postgres exporter's account (deploy/k8s/monitoring-role.sql), and Grafana's own admin
  # login (nobody signs in with it: super admins come through the portal) and the key it signs its cookies with
  local k
  for k in MONITORING_DB_PASSWORD GRAFANA_ADMIN_PASSWORD GRAFANA_SECRET_KEY; do
    if ! grep -q "^$k=" "$K8S/portal.env"; then
      echo "$k=$(rand 24)" >> "$K8S/portal.env"; log "made $k (portal.env)"
    fi
  done
}

# --- init: settings and secrets, made once ----------------------------------------------------------
# Copies a local env file's KEY=VALUE lines, minus the keys given (they're set below or only make sense locally).
# The passwords the manifests put into URLs (postgresql://user:PASSWORD@..., redis://:PASSWORD@...) go in as they
# are, so one with a character that means something in a URL (@ : / ? # % and the like) would break or alter the
# address. Generated values are hex; a hand-set one is checked here, before anything is applied.
url_safe() {
  local file=$1 key=$2 value
  value="$(sed -n "s/^$key=//p" "$K8S/$file" | head -1 | sed "s/^['\"]//; s/['\"]\$//")"
  case "$value" in
    ''|*[!A-Za-z0-9._~-]*) die "$key in deploy/k8s/$file must be letters, digits, '.', '_', '~' or '-' only (it goes into a URL); make a new one with: openssl rand -hex 24" ;;
  esac
}
url_safe_passwords() {
  url_safe engine.env REDIS_PASSWORD
  url_safe engine.env LITELLM_DB_PASSWORD
  url_safe portal.env PORTAL_REDIS_PASSWORD
  url_safe portal.env PORTAL_DB_PASSWORD
  url_safe portal.env PORTAL_APP_DB_PASSWORD
  url_safe portal.env MONITORING_DB_PASSWORD
}

copy_env() {
  local src="$1"; shift
  [ -f "$src" ] || return 0
  local drop; drop="$(printf '%s|' "$@")"; drop="${drop%|}"
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$src" | grep -vE "^($drop)=" || true
}

jwt_key() {
  if [ -x .venv/bin/python ] && .venv/bin/python -c 'import cryptography' 2>/dev/null; then
    .venv/bin/python -m portal.cli genkeys | sed -n 's/^PORTAL_JWT_PRIVATE_KEYS=//p'
  else
    printf '"%s"' "$(openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 2>/dev/null \
      | openssl pkcs8 -topk8 -nocrypt | awk 'BEGIN{ORS="\\n"} {print}' | sed 's/\\n$//')"
  fi
}

init() {
  umask 077
  if [ -f "$K8S/deploy.env" ]; then log "deploy/k8s/deploy.env already exists; leaving it"
  else cp "$K8S/deploy.env.example" "$K8S/deploy.env"; log "made deploy/k8s/deploy.env: check it (hostname, storage classes, sizes)"; fi

  if [ -f "$K8S/engine.env" ]; then log "deploy/k8s/engine.env already exists; leaving it"
  else
    local master; master="sk-$(rand 24)"
    {
      echo "# Engine and AI gateway settings for the cluster (a Kubernetes secret). Copied from .env, with new"
      echo "# passwords and keys. REDIS_URL, DATA_DIR and GATEWAY_BASE_URL are set by the manifests."
      copy_env .env REDIS_URL DATA_DIR GATEWAY_BASE_URL OLLAMA_BASE_URL ALLOW_MOCK_PROVIDER LITELLM_MASTER_KEY \
        GATEWAY_API_KEY LITELLM_DB_PASSWORD UI_PASSWORD ADOBE_BASE_URL ADOBE_ENABLED
      echo "LITELLM_MASTER_KEY=$master"
      echo "GATEWAY_API_KEY=$master"
      echo "LITELLM_DB_PASSWORD=$(rand 24)"
      echo "UI_PASSWORD=$(rand 16)"
      echo "# Adobe Autotag: set ADOBE_ENABLED=true, ADOBE_CLIENT_ID and ADOBE_CLIENT_SECRET, and ADOBE_WORKERS in deploy.env"
      echo "ADOBE_ENABLED=false"
    } > "$K8S/engine.env"
    log "made deploy/k8s/engine.env (from .env, with new gateway and database passwords)"
  fi

  if [ -f "$K8S/portal.env" ]; then log "deploy/k8s/portal.env already exists; leaving it"
  else
    {
      echo "# Portal settings for the cluster (a Kubernetes secret). Copied from .env.portal, with new keys and"
      echo "# passwords. Database, Redis, S3, engine and app URLs are set by the manifests."
      copy_env .env.portal PORTAL_JWT_PRIVATE_KEYS PORTAL_SECRET_KEY PORTAL_DB_PASSWORD PORTAL_S3_ACCESS_KEY \
        PORTAL_S3_SECRET_KEY PORTAL_S3_SSE_KEK PORTAL_ENGINE_URL PORTAL_DATABASE_URL PORTAL_REDIS_URL PORTAL_APP_URL \
        PORTAL_CORS_ORIGINS PORTAL_COOKIE_SECURE PORTAL_S3_ENDPOINT_URL PORTAL_GATEWAY_ADMIN_URL POSTGRES_ADMIN_PASSWORD
      echo "PORTAL_JWT_PRIVATE_KEYS=$(jwt_key)"
      echo "PORTAL_SECRET_KEY=$(rand 36)"
      echo "PORTAL_DB_PASSWORD=$(rand 24)"
      echo "POSTGRES_ADMIN_PASSWORD=$(rand 24)"
      echo "PORTAL_S3_ACCESS_KEY=portal-$(rand 8)"
      echo "PORTAL_S3_SECRET_KEY=$(rand 24)"
      echo "PORTAL_S3_SSE_KEK=$(rand 32)"
    } > "$K8S/portal.env"
    log "made deploy/k8s/portal.env (from .env.portal, with new keys and passwords)"
    if grep -qE '^PORTAL_MAIL_BACKEND=smtp' "$K8S/portal.env" && ! grep -q '^PORTAL_SMTP_HOST=' "$K8S/portal.env"; then
      log "note: mail is set to smtp with no PORTAL_SMTP_HOST (locally that was Mailpit). Set the mail settings in portal.env"
    fi
  fi
  shared_keys
  log "next: check deploy/k8s/deploy.env and the two .env files, then scripts/build.sh and scripts/deploy.sh"
}

if [ "$CMD" = init ]; then init; exit 0; fi

# --- settings ----------------------------------------------------------------------------------------
[ -f "$K8S/deploy.env" ] || die "no deploy/k8s/deploy.env yet: run scripts/deploy.sh init"
set -a
# shellcheck disable=SC1091
. "$K8S/deploy.env"
# shellcheck disable=SC1091
[ -f "$K8S/.last-build" ] && . "$K8S/.last-build"
set +a
# deploy.env's own (non-empty) tags win over the last build's
for v in ENGINE_TAG PORTAL_TAG WEB_TAG MUSIC_TAG FORMULA_TAG; do
  own="$(sed -n "s/^$v=//p" "$K8S/deploy.env")"
  [ -n "$own" ] && export "$v=$own"
done
FRONTEND="${FRONTEND:-cluster}"
# Where the ingress controller's traffic to the API origin comes from (81-api-origin-policy.yaml): an ingress
# controller in the nodes' own network, as RKE2's is, is seen by its node address, so name the node network here.
# The default lets nothing in that way.
export API_ORIGIN_SOURCE_CIDR="${API_ORIGIN_SOURCE_CIDR:-127.0.0.1/32}"
# Who may reach the API origin at all (61-api-origin.yaml): the proxies in front of it, such as Cloudflare's
# published ranges (https://www.cloudflare.com/ips/). Anyone else could set the caller's header themselves (#336)
export API_ORIGIN_ALLOWED_SOURCES="${API_ORIGIN_ALLOWED_SOURCES:-}"
# The proxies whose headers name the caller to the portal API: the pods in front of it (cloudflared and the web
# pod; RKE2's pod range by default), and with FRONTEND=worker the ingress controller. Headers from anyone else are
# ignored, so nobody can pick a fresh sign-in rate-limit bucket by setting one (#336)
export TRUSTED_PROXY_CIDRS="${TRUSTED_PROXY_CIDRS:-10.42.0.0/16}"
case "$FRONTEND" in
  cluster) export CLIENT_IP_HEADER=cf-connecting-ip API_TRUSTED_PROXIES="$TRUSTED_PROXY_CIDRS" ;;
  worker) export CLIENT_IP_HEADER=x-client-ip API_TRUSTED_PROXIES="$TRUSTED_PROXY_CIDRS,$API_ORIGIN_SOURCE_CIDR"
          [ -n "${API_ORIGIN_HOST:-}" ] || die "FRONTEND=worker needs API_ORIGIN_HOST in deploy/k8s/deploy.env"
          [ "$API_ORIGIN_SOURCE_CIDR" != 127.0.0.1/32 ] || die "FRONTEND=worker needs API_ORIGIN_SOURCE_CIDR (where the ingress controller's traffic comes from)"
          [ -n "$API_ORIGIN_ALLOWED_SOURCES" ] || die "FRONTEND=worker needs API_ORIGIN_ALLOWED_SOURCES: the proxies allowed to reach the API origin (Cloudflare's ranges); without it anyone could name themselves to the portal" ;;
  *) die "FRONTEND is cluster or worker" ;;
esac
export CLOUDFLARED_IMAGE="${CLOUDFLARED_IMAGE:-cloudflare/cloudflared:2026.10.0@sha256:9b49eed8f62806d5d45ddf59ecefb5710429598ea6d3fcccd2af938f621b2b07}"
for v in NAMESPACE APP_HOST REGISTRY STORAGE_CLASS SHARED_STORAGE_CLASS; do
  [ -n "${!v:-}" ] || die "$v isn't set in deploy/k8s/deploy.env"
done
export WEB_TAG="${WEB_TAG:-}"
needed=(ENGINE_TAG PORTAL_TAG); [ "$FRONTEND" = cluster ] && needed+=(WEB_TAG)
for v in "${needed[@]}"; do
  [ -n "${!v:-}" ] || die "no $v: run scripts/build.sh first (or set it in deploy.env)"
done
export LITELLM_IMAGE="${LITELLM_IMAGE:-ghcr.io/berriai/litellm:v1.105.0@sha256:60947db020b57e204823d3fbc8bb295c85be890d7c9adcc77fb4f5fc2fe410b2}"
# Proxies in front of the web app whose X-Forwarded-For is believed (an ingress controller's pod range); not
# needed behind Cloudflare, which is read from its own header
export WEB_TRUSTED_PROXIES="${WEB_TRUSTED_PROXIES:-}"
# The link to the AI gateway's admin UI in Administration; empty (how to reach it is shown instead) unless the UI
# has an address of its own
export GATEWAY_ADMIN_URL="${GATEWAY_ADMIN_URL:-}"
# veraPDF kept running (35-verapdf.yaml): heap, pod memory limit and the restart schedule
export VERAPDF_IMAGE="${VERAPDF_IMAGE:-verapdf/rest:v1.30.2@sha256:341359ac6af558f35f03c355a798fec57ba049dd04f0a503f300170e1696573b}" CURL_IMAGE="${CURL_IMAGE:-curlimages/curl:8.22.0@sha256:58adaa4e8dca9c988bae2aba4ab3434a0bb2da16bbe3f92dec39ec7785166777}"
export VERAPDF_HEAP="${VERAPDF_HEAP:-512m}" VERAPDF_MEMORY_LIMIT="${VERAPDF_MEMORY_LIMIT:-1Gi}"
export VERAPDF_RESTART_SCHEDULE="${VERAPDF_RESTART_SCHEDULE:-0 * * * *}"
# Scaling with the work (45-autoscale.yaml); AUTOSCALE=auto is settled once the cluster can be asked
export AUTOSCALE="${AUTOSCALE:-auto}" ENGINE_WORKERS="${ENGINE_WORKERS:-1}" WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-2}"
export ENGINE_WORKERS_MAX="${ENGINE_WORKERS_MAX:-4}" VERAPDF_MAX="${VERAPDF_MAX:-3}" LITELLM_MAX="${LITELLM_MAX:-2}"
export SCALE_DOWN_SECONDS="${SCALE_DOWN_SECONDS:-600}" WORKER_GRACE_SECONDS="${WORKER_GRACE_SECONDS:-3900}"
case "$AUTOSCALE" in auto|on|off) ;; *) die "AUTOSCALE is auto, on or off" ;; esac
# The music reading service (36-music.yaml): off, or on (needs its image: scripts/build.sh --only music)
export MUSIC_READER="${MUSIC_READER:-off}" MUSIC_MAX="${MUSIC_MAX:-2}" MUSIC_PER_POD="${MUSIC_PER_POD:-4}"
export MUSIC_MEMORY_LIMIT="${MUSIC_MEMORY_LIMIT:-2Gi}" MUSIC_TAG="${MUSIC_TAG:-}"
# How much music is read, and how long the engine waits for it (40-engine.yaml, 36-music.yaml)
export MUSIC_MAX_EXAMPLES="${MUSIC_MAX_EXAMPLES:-80}" MUSIC_MAX_SCORE_LINES="${MUSIC_MAX_SCORE_LINES:-600}"
export MUSIC_PER_MEASURE="${MUSIC_PER_MEASURE:-true}" MUSIC_WHOLE_SCORE="${MUSIC_WHOLE_SCORE:-true}"
export MUSIC_WHOLE_SCORE_MAX_MB="${MUSIC_WHOLE_SCORE_MAX_MB:-40}"
export MUSIC_WAIT_SECONDS="${MUSIC_WAIT_SECONDS:-240}" MUSIC_PAGE_SECONDS="${MUSIC_PAGE_SECONDS:-90}"
for v in MUSIC_MAX_EXAMPLES MUSIC_MAX_SCORE_LINES MUSIC_WHOLE_SCORE_MAX_MB MUSIC_WAIT_SECONDS MUSIC_PAGE_SECONDS; do
  case "${!v}" in ''|*[!0-9]*) die "$v is a whole number" ;; esac
done
for v in MUSIC_PER_MEASURE MUSIC_WHOLE_SCORE; do
  case "${!v}" in true|false) ;; *) die "$v is true or false" ;; esac
done
case "$MUSIC_READER" in
  on) export OMR_ENABLED=true
      [ -n "$MUSIC_TAG" ] || die "MUSIC_READER=on needs its image: run scripts/build.sh --only music (or set MUSIC_TAG)" ;;
  off) export OMR_ENABLED=false ;;
  *) die "MUSIC_READER is on or off" ;;
esac
# The formula reading service (37-formula.yaml, #403): off, or on (needs its image: scripts/build.sh --only formula).
# It reads each formula the AI read again from its picture, to check the AI's reading against
export FORMULA_READER="${FORMULA_READER:-off}" FORMULA_MAX="${FORMULA_MAX:-2}" FORMULA_PER_POD="${FORMULA_PER_POD:-20}"
export FORMULA_TAG="${FORMULA_TAG:-}"
case "$FORMULA_READER" in
  on) export FORMULA_OCR_ENABLED=true
      [ -n "$FORMULA_TAG" ] || die "FORMULA_READER=on needs its image: run scripts/build.sh --only formula (or set FORMULA_TAG)" ;;
  off) export FORMULA_OCR_ENABLED=false ;;
  *) die "FORMULA_READER is on or off" ;;
esac
# Backups (#333): cronjob runs the nightly backup onto an NFS share (90-backup.yaml) and the portal alerts when one
# hasn't finished for a while; external relies on the hosts' own backups (or none) and alerts nothing
export BACKUPS="${BACKUPS:-external}" BACKUP_NFS_SERVER="${BACKUP_NFS_SERVER:-}" BACKUP_NFS_PATH="${BACKUP_NFS_PATH:-}"
export BACKUP_SCHEDULE="${BACKUP_SCHEDULE:-0 2 * * *}" BACKUP_TIMEZONE="${BACKUP_TIMEZONE:-America/New_York}"
export BACKUP_KEEP_DAILY="${BACKUP_KEEP_DAILY:-14}" BACKUP_KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-8}"
export BACKUP_FILES="${BACKUP_FILES:-on}" BACKUP_SIZE="${BACKUP_SIZE:-20Gi}" BACKUP_NFS_VERSION="${BACKUP_NFS_VERSION:-4.1}"
case "$BACKUPS" in
  cronjob)
    if [ -z "$BACKUP_NFS_SERVER" ] || [ -z "$BACKUP_NFS_PATH" ]; then
      die "BACKUPS=cronjob needs BACKUP_NFS_SERVER and BACKUP_NFS_PATH (the NAS share)"
    fi
    for v in BACKUP_KEEP_DAILY BACKUP_KEEP_WEEKLY; do case "${!v}" in ''|*[!0-9]*) die "$v is a whole number" ;; esac; done
    case "$BACKUP_FILES" in on|off) ;; *) die "BACKUP_FILES is on or off" ;; esac
    export BACKUP_EXPECTED=true ;;
  external) export BACKUP_EXPECTED=false ;;
  *) die "BACKUPS is cronjob or external" ;;
esac
# Monitoring (#397, 95-monitoring.yaml): Prometheus, its alerts to super admins with the portal's own, and Grafana at
# /grafana for super admins (through the web app, so FRONTEND=cluster only); off removes them but keeps their volumes
export MONITORING="${MONITORING:-on}" PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-15d}"
export PROMETHEUS_SIZE="${PROMETHEUS_SIZE:-20Gi}" PROMETHEUS_MEMORY_LIMIT="${PROMETHEUS_MEMORY_LIMIT:-2Gi}"
export GRAFANA_SIZE="${GRAFANA_SIZE:-1Gi}" MONITORING_STORAGE_CLASS="${MONITORING_STORAGE_CLASS:-$STORAGE_CLASS}"
case "$PROMETHEUS_RETENTION" in
  ''|*[!0-9ywdh]*|[!0-9]*) die "PROMETHEUS_RETENTION is a time such as 15d, 8w or 1y" ;;
esac
# History is also cut to fit its volume: 85% of PROMETHEUS_SIZE unless PROMETHEUS_RETENTION_SIZE says otherwise
if [ -z "${PROMETHEUS_RETENTION_SIZE:-}" ]; then
  case "$PROMETHEUS_SIZE" in
    *Gi) n="${PROMETHEUS_SIZE%Gi}"
         case "$n" in ''|*[!0-9]*) die "PROMETHEUS_SIZE is a whole number of Gi, such as 20Gi" ;; esac
         PROMETHEUS_RETENTION_SIZE="$((n * 1024 * 85 / 100))MB" ;;
    *) die "PROMETHEUS_SIZE is in Gi (20Gi); or set PROMETHEUS_RETENTION_SIZE (e.g. 17GB) yourself" ;;
  esac
fi
export PROMETHEUS_RETENTION_SIZE
export PROMETHEUS_IMAGE="${PROMETHEUS_IMAGE:-prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e}"
export GRAFANA_IMAGE="${GRAFANA_IMAGE:-grafana/grafana:13.2.3@sha256:b28bae15e219c998fb0e0424ed724930cc61b1f61fb404d47c862f9a23f9e572}"
export KSM_IMAGE="${KSM_IMAGE:-registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0@sha256:42cfe3723a5f058171c627537fb57a3ea0f26e4380fa18555a95cb1a1b4cfc5b}"
export REDIS_EXPORTER_IMAGE="${REDIS_EXPORTER_IMAGE:-oliver006/redis_exporter:v1.93.0@sha256:6ca518a72f30f7f576ef92963de1b91d3c3f5fd3aeac949f878bfda183587820}"
export POSTGRES_EXPORTER_IMAGE="${POSTGRES_EXPORTER_IMAGE:-prometheuscommunity/postgres-exporter:v0.20.1@sha256:ac5ec343104fae0e2d84a27bb8d69b38430a11910c5382cad85d478d2bab713e}"
case "$MONITORING" in
  on) export PORTAL_PROMETHEUS_URL=http://prometheus:9090
      if [ "$FRONTEND" = cluster ]; then export PORTAL_GRAFANA_URL=/grafana/ GRAFANA_UPSTREAM=http://grafana:3000
      else export PORTAL_GRAFANA_URL="" GRAFANA_UPSTREAM=""; fi ;;
  off) export PORTAL_PROMETHEUS_URL="" PORTAL_GRAFANA_URL="" GRAFANA_UPSTREAM="" ;;
  *) die "MONITORING is on or off" ;;
esac
[ "$ENGINE_WORKERS_MAX" -ge "$ENGINE_WORKERS" ] || die "ENGINE_WORKERS_MAX is less than ENGINE_WORKERS"
# With scaling on, the autoscalers own the number of pods: the manifests leave it out, so a deploy doesn't reset it
set_replicas() {
  if [ "$AUTOSCALE" = on ]; then
    export ENGINE_WORKER_REPLICAS="# replicas: set by the autoscaler" VERAPDF_REPLICAS="# replicas: set by the autoscaler" \
      LITELLM_REPLICAS="# replicas: set by the autoscaler" MUSIC_REPLICAS="# replicas: set by the autoscaler" \
      FORMULA_REPLICAS="# replicas: set by the autoscaler"
  else
    export ENGINE_WORKER_REPLICAS="replicas: $ENGINE_WORKERS" VERAPDF_REPLICAS="replicas: 1" LITELLM_REPLICAS="replicas: 1" \
      MUSIC_REPLICAS="replicas: 1" FORMULA_REPLICAS="replicas: 1"
  fi
}
export LITELLM_CONFIG_SHA RUN_ID ENGINE_ENV_SHA PORTAL_ENV_SHA TUNNEL_ENV_SHA VERAPDF_CONFIG_SHA PROMETHEUS_CONFIG_SHA
export GRAFANA_DASHBOARDS_SHA
# The gateway's shared admin login (UI_USERNAME and UI_PASSWORD in engine.env): on, or off once every admin has an
# account of their own in the gateway's UI
export GATEWAY_SHARED_LOGIN="${GATEWAY_SHARED_LOGIN:-on}"
case "$GATEWAY_SHARED_LOGIN" in on|off) ;; *) die "GATEWAY_SHARED_LOGIN is on or off" ;; esac
# The gateway's config as deployed: config/litellm.yaml, with the shared login turned off when asked
litellm_config() {
  if [ "$GATEWAY_SHARED_LOGIN" = off ]; then
    grep -q '^general_settings:$' config/litellm.yaml || die "config/litellm.yaml has no general_settings section"
    perl -pe 's/^general_settings:\n/general_settings:\n  disable_env_credential_login: true\n/' config/litellm.yaml
  else
    cat config/litellm.yaml
  fi
}
LITELLM_CONFIG_SHA="$(litellm_config | shasum -a 256 | cut -c1-16)"
VERAPDF_CONFIG_SHA="$(shasum -a 256 deploy/verapdf/validator.xml | cut -c1-16)"
MON="$K8S/monitoring"
PROMETHEUS_CONFIG_SHA="$(cat "$MON/prometheus.yml" "$MON/rules.yml" | shasum -a 256 | cut -c1-16)"
GRAFANA_DASHBOARDS_SHA="$(cat "$MON"/dashboards/*.json | shasum -a 256 | cut -c1-16)"
# Pods restart when their settings file changes (the checksum is in their template)
sha_of() { if [ -f "$1" ]; then shasum -a 256 "$1" | cut -c1-16; else echo none; fi; }
ENGINE_ENV_SHA="$(sha_of "$K8S/engine.env")"
PORTAL_ENV_SHA="$(sha_of "$K8S/portal.env")"
TUNNEL_ENV_SHA="$(sha_of "$K8S/tunnel.env")"
RUN_ID="$(date +%Y%m%d%H%M%S)"

export KUBECONFIG="${KUBECONFIG_PATH:-${KUBECONFIG:-$HOME/.kube/config}}"
KC=(kubectl --request-timeout=30s)
[ -n "${KUBE_CONTEXT:-}" ] && KC+=(--context "$KUBE_CONTEXT")
NS=(-n "$NAMESPACE")

# Fills in ${VAR} from the environment; refuses to leave one unset (other $ forms are left alone).
render_file() {
  perl -pe 'BEGIN { $bad = 0 } s/\$\{([A-Z_][A-Z0-9_]*)\}/exists $ENV{$1} ? $ENV{$1} : do { print STDERR "unset: $1\n"; $bad = 1; "" }/ge; END { exit $bad }' "$1"
}

render_all() {
  local out="$1"
  rm -rf "$out" && mkdir -p "$out"
  for f in "$K8S"/manifests/*.yaml "$K8S"/migrate-job.yaml; do
    # The web pod and its Ingress, or the API origin the Cloudflare Worker forwards to
    case "$FRONTEND:$(basename "$f")" in cluster:61-*|cluster:81-*|worker:60-*) continue ;; esac
    [ "$(basename "$f")" = 70-cloudflared.yaml ] && [ ! -f "$K8S/tunnel.env" ] && [ "${RENDER_ALL:-0}" = 0 ] && continue
    [ "$(basename "$f")" = 45-autoscale.yaml ] && [ "$AUTOSCALE" != on ] && continue
    case "$(basename "$f")" in
      36-music.yaml) [ "$MUSIC_READER" = on ] || continue ;;
      46-music-autoscale.yaml) if [ "$MUSIC_READER" != on ] || [ "$AUTOSCALE" != on ]; then continue; fi ;;
      37-formula.yaml) [ "$FORMULA_READER" = on ] || continue ;;
      47-formula-autoscale.yaml) if [ "$FORMULA_READER" != on ] || [ "$AUTOSCALE" != on ]; then continue; fi ;;
      90-backup.yaml) [ "$BACKUPS" = cronjob ] || [ "${RENDER_ALL:-0}" = 1 ] || continue ;;
      95-monitoring.yaml) [ "$MONITORING" = on ] || [ "${RENDER_ALL:-0}" = 1 ] || continue ;;
    esac
    render_file "$f" > "$out/$(basename "$f")" || die "$(basename "$f") uses a setting that isn't set"
  done
  # Prometheus's own config (its rules and Grafana's dashboards go in as they are: they use $ and {{ }} themselves)
  render_file "$MON/prometheus.yml" > "$out/prometheus.yml" \
    || die "monitoring/prometheus.yml uses a setting that isn't set"
}

if [ "$CMD" = render ]; then
  [ "$AUTOSCALE" = auto ] && AUTOSCALE=on  # nothing to ask: show the manifests with scaling
  set_replicas
  RENDER_ALL=1 render_all "$K8S/.rendered"
  log "rendered into deploy/k8s/.rendered/ (engine $ENGINE_TAG, portal $PORTAL_TAG, web $WEB_TAG)"
  exit 0
fi

"${KC[@]}" version >/dev/null 2>&1 || die "can't reach the cluster with $KUBECONFIG"
SERVER="$("${KC[@]}" config view --minify -o jsonpath='{.clusters[0].cluster.server}')"

if [ "$CMD" = status ]; then
  "${KC[@]}" "${NS[@]}" get pods,pvc,ingress -o wide
  exit 0
fi

# Scaling needs KEDA (workers follow the queue) and metrics-server (veraPDF and the gateway follow their CPU)
has_keda=0; "${KC[@]}" get crd scaledobjects.keda.sh >/dev/null 2>&1 && has_keda=1
has_metrics=0; "${KC[@]}" get apiservice v1beta1.metrics.k8s.io >/dev/null 2>&1 && has_metrics=1
if [ "$AUTOSCALE" = auto ]; then
  if [ "$has_keda" = 1 ] && [ "$has_metrics" = 1 ]; then AUTOSCALE=on; else AUTOSCALE=off; fi
elif [ "$AUTOSCALE" = on ]; then
  [ "$has_keda" = 1 ] || die "AUTOSCALE=on needs KEDA on the cluster (https://keda.sh/docs/latest/deploy/)"
  [ "$has_metrics" = 1 ] || die "AUTOSCALE=on needs metrics-server on the cluster"
fi
set_replicas

# --- deploy ------------------------------------------------------------------------------------------
if [ ! -f "$K8S/engine.env" ] || [ ! -f "$K8S/portal.env" ]; then
  die "missing deploy/k8s/engine.env or portal.env: run scripts/deploy.sh init"
fi
shared_keys
url_safe_passwords
classes=("$STORAGE_CLASS" "$SHARED_STORAGE_CLASS"); [ "$MONITORING" = on ] && classes+=("$MONITORING_STORAGE_CLASS")
for sc in "${classes[@]}"; do
  "${KC[@]}" get storageclass "$sc" >/dev/null 2>&1 \
    || die "the cluster has no StorageClass '$sc' (install one, or change it in deploy/k8s/deploy.env)"
done
TUNNEL=0; [ -f "$K8S/tunnel.env" ] && grep -q '^TUNNEL_TOKEN=.' "$K8S/tunnel.env" && TUNNEL=1

echo "Deploying to $SERVER, namespace $NAMESPACE, as https://$APP_HOST (web app: $FRONTEND)"
echo "  engine $ENGINE_TAG, portal $PORTAL_TAG, web $WEB_TAG; storage $STORAGE_CLASS and $SHARED_STORAGE_CLASS;"
echo "  Cloudflare tunnel: $([ "$TUNNEL" = 1 ] && echo "yes (deploy/k8s/tunnel.env)" || echo "no")"
if [ "$AUTOSCALE" = on ]; then
  echo "  scaling with the queue: $ENGINE_WORKERS to $ENGINE_WORKERS_MAX workers ($WORKER_CONCURRENCY documents each), veraPDF up to $VERAPDF_MAX, AI gateway up to $LITELLM_MAX"
else
  echo "  fixed size: $ENGINE_WORKERS worker(s) ($WORKER_CONCURRENCY documents each); scaling is off$([ "$has_keda" = 0 ] && echo " (no KEDA on the cluster)")"
fi
if [ "$MONITORING" = on ]; then
  echo "  monitoring: Prometheus ($PROMETHEUS_RETENTION of history, $PROMETHEUS_SIZE on $MONITORING_STORAGE_CLASS)"
  [ "$FRONTEND" = cluster ] && echo "    and Grafana at https://$APP_HOST/grafana/ for super admins"
else
  echo "  monitoring: off (MONITORING=off)"
fi
if [ "$BACKUPS" = cronjob ]; then
  echo "  nightly backup: $BACKUP_SCHEDULE ($BACKUP_TIMEZONE) onto $BACKUP_NFS_SERVER:$BACKUP_NFS_PATH, keeping $BACKUP_KEEP_DAILY nightly and $BACKUP_KEEP_WEEKLY weekly$([ "$BACKUP_FILES" = on ] && echo ", stored files included")"
else
  echo "  nightly backup: none here (BACKUPS=external: the hosts' own backups); no backup alerts"
fi
if [ "$FORMULA_READER" = on ]; then
  echo "  formula reading service: $FORMULA_TAG, $([ "$AUTOSCALE" = on ] && echo "started when there are formulas to read (up to $FORMULA_MAX)" || echo "one pod, always running")"
fi
if [ "$MUSIC_READER" = on ]; then
  echo "  music reading service: $MUSIC_TAG, $([ "$AUTOSCALE" = on ] && echo "started when there is music to read (up to $MUSIC_MAX)" || echo "one pod, always running")"
fi
if [ "$YES" != 1 ]; then
  read -r -p "Go ahead? [y/N] " answer
  [[ "$answer" =~ ^[Yy] ]] || die "stopped"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
render_all "$WORK"
apply() { "${KC[@]}" apply -f "$WORK/$1" >/dev/null && log "applied $1"; }
wait_rollout() { "${KC[@]}" "${NS[@]}" rollout status "$1" --timeout="${2:-300s}" >/dev/null || die "$1 didn't become ready (scripts/deploy.sh status; kubectl -n $NAMESPACE logs $1)"; log "$1 ready"; }

apply 00-namespace.yaml

log "secrets"
pull_token="${GHCR_PULL_TOKEN:-}"
if [ -z "$pull_token" ] && command -v gh >/dev/null; then pull_token="$(gh auth token 2>/dev/null || true)"; fi
[ -n "$pull_token" ] || die "no token for pulling images: set GHCR_PULL_TOKEN or sign in with gh"
reg_user="${REGISTRY#*/}"; reg_user="${reg_user%%/*}"
"${KC[@]}" "${NS[@]}" create secret docker-registry pdfrem-ghcr --docker-server="${REGISTRY%%/*}" \
  --docker-username="$reg_user" --docker-password="$pull_token" --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null
# Each pod gets a secret with what it reads and no more (README: "Which pod sees which secret"): the engine's
# without the gateway's keys and Adobe's; the gateway's without the engine's; Adobe's two for its worker alone;
# the database passwords for Postgres; the portal's without the admin password
ENGINE_ONLY='^(REDIS_PASSWORD|SERVICE_API_KEY|GATEWAY_API_KEY|LOCALAI_|OLLAMA_|ADOBE_)'
GATEWAY_ONLY='^(LITELLM_|UI_|LOCAL_|OPENROUTER_)'
# Replaced whole, not applied: an apply with stringData adds and updates keys but never removes one that has gone,
# so a key moved out of a secret would stay in it
secret() {
  python3 "$K8S/env2secret.py" "$@" > "$WORK/secret.json"
  if "${KC[@]}" "${NS[@]}" get secret "$1" >/dev/null 2>&1; then "${KC[@]}" replace -f "$WORK/secret.json" >/dev/null
  else "${KC[@]}" create -f "$WORK/secret.json" >/dev/null; fi
  rm -f "$WORK/secret.json"
}
secret pdfrem-engine-env "$NAMESPACE" "$K8S/engine.env" --drop "$GATEWAY_ONLY|^ADOBE_CLIENT_"
secret pdfrem-gateway-env "$NAMESPACE" "$K8S/engine.env" --drop "$ENGINE_ONLY"
secret pdfrem-adobe-env "$NAMESPACE" "$K8S/engine.env" --keys '^ADOBE_CLIENT_'
secret pdfrem-db-env "$NAMESPACE" "$K8S/engine.env" "$K8S/portal.env" \
  --keys '^(POSTGRES_ADMIN_PASSWORD|PORTAL_DB_PASSWORD|LITELLM_DB_PASSWORD)$'
# The portal's pods get their own account's password, not the owner's (#337): that's only for migrations and backups
MONITORING_KEYS='MONITORING_DB_PASSWORD|GRAFANA_ADMIN_PASSWORD|GRAFANA_SECRET_KEY'
secret pdfrem-portal-env "$NAMESPACE" "$K8S/portal.env" \
  --drop "^(POSTGRES_ADMIN_PASSWORD|PORTAL_DB_PASSWORD|$MONITORING_KEYS)\$"
if [ "$MONITORING" = on ]; then
  secret pdfrem-monitoring-env "$NAMESPACE" "$K8S/portal.env" \
    --keys "^($MONITORING_KEYS)\$"
  "${KC[@]}" "${NS[@]}" create configmap prometheus-config --from-file=prometheus.yml="$WORK/prometheus.yml" \
    --from-file=rules.yml="$MON/rules.yml" --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null
  "${KC[@]}" "${NS[@]}" create configmap grafana-dashboards --from-file="$MON/dashboards" \
    --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null
fi
if [ "$TUNNEL" = 1 ]; then
  python3 "$K8S/env2secret.py" pdfrem-tunnel "$NAMESPACE" "$K8S/tunnel.env" | "${KC[@]}" apply -f - >/dev/null
fi
litellm_config > "$WORK/litellm.yaml"
"${KC[@]}" "${NS[@]}" create configmap litellm-config --from-file=litellm.yaml="$WORK/litellm.yaml" \
  --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null
"${KC[@]}" "${NS[@]}" create configmap verapdf-config --from-file=validator.xml=deploy/verapdf/validator.xml \
  --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null

log "storage, database, queues and file store"
apply 10-jobdata.yaml
apply 20-postgres.yaml
apply 21-redis.yaml
apply 22-seaweedfs.yaml
wait_rollout statefulset/postgres
wait_rollout statefulset/redis
wait_rollout statefulset/seaweedfs

if "${KC[@]}" "${NS[@]}" exec statefulset/postgres -- psql -U portal -d portal -tAc \
    "select 1 from information_schema.tables where table_name='alembic_version'" 2>/dev/null | grep -q 1; then
  if [ "$BACKUP" = 1 ]; then
    log "backing up the database before migrations"
    K8S_NAMESPACE="$NAMESPACE" scripts/backup.sh || die "the backup failed, so nothing was migrated"
  else
    log "skipping the backup (--skip-backup)"
  fi
fi

log "migrations"
apply migrate-job.yaml
if ! "${KC[@]}" "${NS[@]}" wait --for=condition=complete "job/portal-migrate-$RUN_ID" --timeout=300s >/dev/null; then
  "${KC[@]}" "${NS[@]}" logs "job/portal-migrate-$RUN_ID" --tail=50 >&2 || true
  die "migrations failed (the database backup is in ./backups)"
fi
log "migrations applied"

# The portal runs as its own account, which can't change tables or triggers (#337): made, and given the rows of
# whatever the migrations made, on every deploy. The password goes in on standard input, not as an argument.
app_pw="$(sed -n 's/^PORTAL_APP_DB_PASSWORD=//p' "$K8S/portal.env" | head -1)"
sed "s/__APP_PASSWORD__/$app_pw/" "$K8S/app-role.sql" \
  | "${KC[@]}" "${NS[@]}" exec -i statefulset/postgres -- psql -q -v ON_ERROR_STOP=1 -U postgres -d portal >/dev/null \
  || die "couldn't set up the portal's database account (deploy/k8s/app-role.sql)"
log "portal database account ready"
if [ "$MONITORING" = on ]; then
  mon_pw="$(sed -n 's/^MONITORING_DB_PASSWORD=//p' "$K8S/portal.env" | head -1)"
  sed "s/__MONITOR_PASSWORD__/$mon_pw/" "$K8S/monitoring-role.sql" \
    | "${KC[@]}" "${NS[@]}" exec -i statefulset/postgres -- psql -q -v ON_ERROR_STOP=1 -U postgres -d portal >/dev/null \
    || die "couldn't set up the monitoring database account (deploy/k8s/monitoring-role.sql)"
  log "monitoring database account ready"
fi

log "AI gateway, veraPDF, engine, portal and web app"
apply 30-litellm.yaml
apply 35-verapdf.yaml
apply 40-engine.yaml
apply 50-portal.yaml
if [ "$AUTOSCALE" = on ]; then
  apply 45-autoscale.yaml
else
  if [ "$has_keda" = 1 ]; then
    "${KC[@]}" "${NS[@]}" delete scaledobject/engine-worker triggerauthentication/portal-db --ignore-not-found >/dev/null
  fi
  "${KC[@]}" "${NS[@]}" delete hpa/verapdf hpa/litellm --ignore-not-found >/dev/null
fi
if [ "$MUSIC_READER" = on ]; then
  apply 36-music.yaml
  if [ "$AUTOSCALE" = on ]; then
    apply 46-music-autoscale.yaml
  elif [ "$has_keda" = 1 ]; then
    "${KC[@]}" "${NS[@]}" delete scaledobject/music-reader --ignore-not-found >/dev/null
  fi
else
  if [ "$has_keda" = 1 ]; then
    "${KC[@]}" "${NS[@]}" delete scaledobject/music-reader --ignore-not-found >/dev/null
  fi
  "${KC[@]}" "${NS[@]}" delete deployment/music-reader --ignore-not-found >/dev/null
fi
if [ "$FORMULA_READER" = on ]; then
  apply 37-formula.yaml
  if [ "$AUTOSCALE" = on ]; then
    apply 47-formula-autoscale.yaml
  elif [ "$has_keda" = 1 ]; then
    "${KC[@]}" "${NS[@]}" delete scaledobject/formula-reader triggerauthentication/formula-queue --ignore-not-found >/dev/null
  fi
else
  if [ "$has_keda" = 1 ]; then
    "${KC[@]}" "${NS[@]}" delete scaledobject/formula-reader triggerauthentication/formula-queue --ignore-not-found >/dev/null
  fi
  "${KC[@]}" "${NS[@]}" delete deployment/formula-reader --ignore-not-found >/dev/null
fi
if [ "$BACKUPS" = cronjob ]; then
  apply 90-backup.yaml
else
  # The share's volume and claim stay (a static NFS volume never deletes what's on the share)
  "${KC[@]}" "${NS[@]}" delete cronjob/backup configmap/backup-scripts --ignore-not-found >/dev/null
fi
# Before the web app: its nginx looks Grafana up when it starts
if [ "$MONITORING" = on ]; then
  apply 95-monitoring.yaml
else
  # Their volumes (prometheus-data, grafana-data) stay, with the history and any dashboards made in Grafana
  "${KC[@]}" "${NS[@]}" delete deployment/prometheus deployment/grafana deployment/kube-state-metrics \
    deployment/redis-exporter deployment/postgres-exporter service/prometheus service/grafana \
    configmap/prometheus-config configmap/grafana-provisioning configmap/grafana-dashboards \
    secret/pdfrem-monitoring-env networkpolicy/prometheus networkpolicy/grafana networkpolicy/exporters \
    serviceaccount/prometheus serviceaccount/kube-state-metrics role/prometheus role/kube-state-metrics \
    rolebinding/prometheus rolebinding/kube-state-metrics --ignore-not-found >/dev/null
  "${KC[@]}" delete clusterrolebinding/"$NAMESPACE"-prometheus clusterrole/"$NAMESPACE"-prometheus \
    --ignore-not-found >/dev/null
fi
if [ "$FRONTEND" = worker ]; then
  apply 61-api-origin.yaml
  "${KC[@]}" "${NS[@]}" delete ingress/web deployment/web service/web --ignore-not-found >/dev/null
else
  apply 60-web.yaml
  "${KC[@]}" "${NS[@]}" delete ingress/api-origin --ignore-not-found >/dev/null
fi
# Last, once every service is up: what may reach what (see the README, "Network policies and the Redis password")
apply 80-network-policies.yaml
if [ "$FRONTEND" = worker ]; then
  apply 81-api-origin-policy.yaml
else
  "${KC[@]}" "${NS[@]}" delete networkpolicy/portal-api-origin --ignore-not-found >/dev/null
fi
wait_rollout deployment/litellm 420s
wait_rollout deployment/verapdf 300s
wait_rollout deployment/engine-api
wait_rollout deployment/engine-worker
wait_rollout deployment/engine-beat
[ "${ADOBE_WORKERS:-0}" -gt 0 ] && wait_rollout deployment/engine-adobe-worker
wait_rollout deployment/portal-api
wait_rollout deployment/portal-worker
if [ "$MONITORING" = on ]; then
  wait_rollout deployment/prometheus
  wait_rollout deployment/grafana
fi
[ "$FRONTEND" = cluster ] && wait_rollout deployment/web
if [ "$TUNNEL" = 1 ]; then
  apply 70-cloudflared.yaml
  wait_rollout deployment/cloudflared
else
  "${KC[@]}" "${NS[@]}" delete deployment/cloudflared secret/pdfrem-tunnel --ignore-not-found >/dev/null
fi

log "checking they answer"
"${KC[@]}" "${NS[@]}" exec deployment/portal-api -- python -c "
import urllib.request as u
checks = [('portal', 'http://localhost:8080/api/v1/health'), ('engine', 'http://engine:8000/api/v1/health')]
if '$FRONTEND' == 'cluster': checks.append(('web', 'http://web:8080/'))
for name, url in checks:
    u.urlopen(url, timeout=10).read(); print('  ok:', name)
" || die "a service didn't answer (scripts/deploy.sh status)"

if [ "$FRONTEND" = worker ]; then
  log "deployed the cluster side; the API origin is https://$API_ORIGIN_HOST (route it through the tunnel and protect"
  log "it with Cloudflare Access), then publish the web app with scripts/deploy-worker.sh"
else
  log "deployed: https://$APP_HOST (once the Cloudflare tunnel routes $APP_HOST to ingress-nginx)"
fi
