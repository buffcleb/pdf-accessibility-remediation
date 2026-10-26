#!/usr/bin/env bash
# Deploy the service to the Kubernetes cluster (the images scripts/build.sh pushed).
#
#   scripts/deploy.sh init          first time: make deploy/k8s/deploy.env and the two secret files
#   scripts/deploy.sh               deploy or update everything (backs up the database before migrations)
#   scripts/deploy.sh status        what's running
#   scripts/deploy.sh render        write the manifests as they'd be applied to deploy/k8s/.rendered/
#   scripts/deploy.sh downgrade REV take the database back to migration REV, with the portal image that's running
#                                   (a rollback: docs/operations/rollback.md); backs up first
#
# What `deploy` does, in order: checks the cluster and its storage; makes the namespace, the registry pull secret
# and the secrets (from deploy/k8s/engine.env and portal.env, split by who reads what); starts Postgres, Redis and SeaweedFS; backs up
# the portal database if it already exists (scripts/backup.sh, into ./backups); runs the portal's migrations; then
# rolls out the AI gateway, engine, portal and web app and checks they answer. A volume whose size setting went up
# (DB_SIZE, FILES_SIZE and the rest) is grown first, once it's shown and you agree; one can't shrink
# (docs/deployment/first-cluster/storage.md#growing-a-volume).
#
# Options:
#   -y, --yes            don't ask before changing the cluster
#   --skip-backup        don't back up the database before migrations (not recommended)
#   --new-gateway-key    give the engine a new AI gateway key and revoke its old one (rotation, or after a leak)
#   -h, --help
#
# Each deploy that finishes adds a line to deploy/k8s/deploy-history: when, the image tags and the database's
# migration, so a rollback knows what ran before.
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

CMD=deploy YES=0 BACKUP=1 NEW_GATEWAY_KEY=0 DOWNGRADE_TO=""
while [ $# -gt 0 ]; do
  case "$1" in
    init|deploy|status|render) CMD="$1" ;;
    downgrade) CMD=downgrade; DOWNGRADE_TO="${2:-}"; [ $# -gt 1 ] && shift
      [[ "$DOWNGRADE_TO" =~ ^[0-9]{4}$ ]] || die "downgrade needs the migration to go back to, e.g. 0043" ;;
    -y|--yes) YES=1 ;;
    --skip-backup) BACKUP=0 ;;
    --new-gateway-key) NEW_GATEWAY_KEY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

rand() { openssl rand -hex "${1:-24}"; }

# A value the two secrets must agree on: the engine's API key (SERVICE_API_KEY in engine.env, PORTAL_ENGINE_API_KEY in
# portal.env). Made and written to both files
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
  # Two Redis, two passwords (#472): the engine's queue (REDIS_PASSWORD) and the portal's rate limits
  # (PORTAL_REDIS_PASSWORD). A deployment from before has the same value in both: the portal's gets a new one
  grep -q '^REDIS_PASSWORD=.' "$K8S/engine.env" || { echo "REDIS_PASSWORD=$(rand 24)" >> "$K8S/engine.env"; log "made REDIS_PASSWORD (engine.env)"; }
  local engine_redis portal_redis
  engine_redis="$(sed -n 's/^REDIS_PASSWORD=//p' "$K8S/engine.env" | head -1)"
  portal_redis="$(sed -n 's/^PORTAL_REDIS_PASSWORD=//p' "$K8S/portal.env" | head -1)"
  if [ -z "$portal_redis" ] || [ "$portal_redis" = "$engine_redis" ]; then
    sed -i.bak '/^PORTAL_REDIS_PASSWORD=/d' "$K8S/portal.env" && rm -f "$K8S/portal.env.bak"
    echo "PORTAL_REDIS_PASSWORD=$(rand 24)" >> "$K8S/portal.env"
    log "made PORTAL_REDIS_PASSWORD (portal.env): the portal's own Redis, apart from the engine's"
  fi
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
# The pods that read uploaded documents (the engine's API, workers and scheduler, the Adobe worker, the music and
# formula readers) on nodes of their own (#471): ENGINE_NODES is a node label, key=value; those pods get it as a node
# selector and tolerate the taint of the same name (key=value:NoSchedule), which keeps everything else off those
# nodes. Empty: they run anywhere, as before.
ENGINE_NODES="${ENGINE_NODES:-}"
export ENGINE_PLACEMENT=""
if [ -n "$ENGINE_NODES" ]; then
  [[ "$ENGINE_NODES" =~ ^([A-Za-z0-9./_-]+)=([A-Za-z0-9._-]+)$ ]] || die "ENGINE_NODES is a node label, key=value (pdfrem/role=engine)"
  ENGINE_PLACEMENT="      nodeSelector: { \"${BASH_REMATCH[1]}\": \"${BASH_REMATCH[2]}\" }
      tolerations: [{ key: \"${BASH_REMATCH[1]}\", operator: Equal, value: \"${BASH_REMATCH[2]}\", effect: NoSchedule }]"
fi
# Outbound traffic from the portal, the AI gateway, the Adobe worker and ClamAV (#453, #455): off (as before), log
# (through the egress proxy, which lets everything through and records each destination) or enforce (only the hosts
# this deployment uses, deploy/k8s/egress_hosts.py, and network policies so they can't go round the proxy). CLUSTER_CIDRS: the pod and
# service ranges, which the proxy is kept out of.
export EGRESS_PROXY="${EGRESS_PROXY:-off}" EGRESS_ALLOW="${EGRESS_ALLOW:-}"
case "$EGRESS_PROXY" in off|log|enforce) ;; *) die "EGRESS_PROXY is off, log or enforce" ;; esac
export EGRESS_PROXY_IMAGE="${EGRESS_PROXY_IMAGE:-ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c}"
CLUSTER_CIDRS="${CLUSTER_CIDRS:-10.42.0.0/16,10.43.0.0/16}"
# As a YAML list for the policies: ["10.42.0.0/16","10.43.0.0/16"]
IFS=, read -r -a cidrs <<< "$CLUSTER_CIDRS"
CLUSTER_CIDRS="[$(printf '"%s",' "${cidrs[@]}")"; CLUSTER_CIDRS="${CLUSTER_CIDRS%,}]"; export CLUSTER_CIDRS
export EGRESS_PROXY_ENV="" EGRESS_PROXY_CONFIG_SHA=none
if [ "$EGRESS_PROXY" != off ]; then
  no_proxy="localhost,127.0.0.1,.svc,.cluster.local,postgres,redis,redis-portal,seaweedfs,engine,litellm,verapdf,prometheus,grafana,web,clamav"
  EGRESS_PROXY_ENV=""
  for v in HTTPS_PROXY HTTP_PROXY https_proxy http_proxy; do
    EGRESS_PROXY_ENV+="            - { name: $v, value: \"http://egress-proxy:3128\" }"$'\n'
  done
  EGRESS_PROXY_ENV+="            - { name: NO_PROXY, value: \"$no_proxy\" }"$'\n'
  EGRESS_PROXY_ENV+="            - { name: no_proxy, value: \"$no_proxy\" }"$'\n'
  # LiteLLM's aiohttp transport doesn't read the proxy settings; its httpx one does
  EGRESS_PROXY_ENV+="            - { name: DISABLE_AIOHTTP_TRANSPORT, value: \"True\" }"
fi
# The ports the portal reaches directly (82-egress-enforce.yaml): its SMTP relay's, and the SIEM's syslog collector's
# with PORTAL_SIEM_MODE=syslog (#456)
portal_setting() { sed -n "s/^$1=//p" "$K8S/portal.env" 2>/dev/null | head -1 | tr -d '"'"'"; }
PORTAL_DIRECT_PORTS="[{ port: $(portal_setting PORTAL_SMTP_PORT | grep -E '^[0-9]+$' || echo 587) }"
if [ "$(portal_setting PORTAL_SIEM_MODE | tr '[:upper:]' '[:lower:]')" = syslog ]; then
  PORTAL_DIRECT_PORTS+=", { port: $(portal_setting PORTAL_SIEM_SYSLOG_PORT | grep -E '^[0-9]+$' || echo 6514) }"
fi
export PORTAL_DIRECT_PORTS="$PORTAL_DIRECT_PORTS]"
# Outbound traffic from everything else (#594): off (as before: only the pods that open documents, and the proxy's
# users with EGRESS_PROXY=enforce, are limited) or on (default-deny-egress, and each workload reaches only what it
# uses: 83-egress-deny.yaml). NODE_CIDRS: the nodes' addresses, where Prometheus reads the kubelets; KUBE_API_CIDRS
# and KUBE_API_PORT: the API servers' own addresses and port (the kubernetes service's endpoints), which policies
# see instead of the service's cluster address.
export EGRESS_DENY="${EGRESS_DENY:-off}"
case "$EGRESS_DENY" in off|on) ;; *) die "EGRESS_DENY is off or on" ;; esac
NODE_CIDRS="${NODE_CIDRS:-}" KUBE_API_CIDRS="${KUBE_API_CIDRS:-${NODE_CIDRS:-}}"
export KUBE_API_PORT="${KUBE_API_PORT:-6443}" NODE_TO="" KUBE_API_TO=""
# As policy destinations: [{ ipBlock: { cidr: "192.0.2.11/32" } }, ...]
ip_blocks() {
  local c out="" list
  IFS=, read -r -a list <<< "$2"
  for c in "${list[@]}"; do
    [[ "$c" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?$ ]] || die "$1 is a comma-separated list of IPv4 addresses or ranges, not \"$c\""
    [[ "$c" == */* ]] || c="$c/32"
    out+="{ ipBlock: { cidr: \"$c\" } }, "
  done
  echo "[${out%, }]"
}
if [ "$EGRESS_DENY" = on ]; then
  [ -n "$NODE_CIDRS" ] || die "EGRESS_DENY=on needs NODE_CIDRS: the nodes' addresses (kubectl get nodes -o wide)"
  [[ "$KUBE_API_PORT" =~ ^[0-9]+$ ]] || die "KUBE_API_PORT is a port number"
  NODE_TO="$(ip_blocks NODE_CIDRS "$NODE_CIDRS")"
  KUBE_API_TO="$(ip_blocks KUBE_API_CIDRS "$KUBE_API_CIDRS")"
fi
# The service the tunnel's public hostname points at (70-cloudflared.yaml)
TUNNEL_ORIGIN=web; [ "$FRONTEND" = worker ] && TUNNEL_ORIGIN=portal-api
export TUNNEL_ORIGIN
# HTTPS at the cluster's own ingress controller (not needed behind the Cloudflare tunnel, which ends TLS itself): the
# Secret with APP_HOST's certificate, made by cert-manager when CERT_ISSUER names a ClusterIssuer (web-tls unless
# INGRESS_TLS_SECRET names it), or put there by you. With TLS on, the controller sends plain HTTP on to HTTPS.
# The ingress controller (#460): Traefik (RKE2's default from v1.36; ingress-nginx is retired upstream) or
# ingress-nginx. Each Ingress gets that controller's own settings; INGRESS_CLASS defaults to its usual class name.
export INGRESS_CONTROLLER="${INGRESS_CONTROLLER:-nginx}"
case "$INGRESS_CONTROLLER" in nginx|traefik) ;; *) die "INGRESS_CONTROLLER must be nginx or traefik" ;; esac
export INGRESS_CLASS="${INGRESS_CLASS:-$INGRESS_CONTROLLER}"
INGRESS_TLS_SECRET="${INGRESS_TLS_SECRET:-}" CERT_ISSUER="${CERT_ISSUER:-}"
[ -n "$CERT_ISSUER" ] && [ -z "$INGRESS_TLS_SECRET" ] && INGRESS_TLS_SECRET=web-tls
export INGRESS_TLS="" INGRESS_ISSUER=""
if [ -n "$INGRESS_TLS_SECRET" ]; then
  INGRESS_TLS="  tls:
    - hosts: [\"$APP_HOST\"]
      secretName: $INGRESS_TLS_SECRET"
fi
[ -n "$CERT_ISSUER" ] && INGRESS_ISSUER="    cert-manager.io/cluster-issuer: \"$CERT_ISSUER\""
# What each controller needs told per Ingress. ingress-nginx: the upload size, no buffering of uploads, and 5 minutes
# for the portal to answer. Traefik doesn't buffer and has no answer timeout by default, and the web pod's own nginx
# limits the upload size; its entry points' timeouts are the cluster's (docs/deployment/first-cluster/middleware.md).
# The API origin's allow-list is a Traefik middleware (62-traefik-api-origin.yaml).
export INGRESS_ANNOTATIONS="" API_ORIGIN_ANNOTATIONS="" API_ORIGIN_SOURCE_LIST=""
if [ "$INGRESS_CONTROLLER" = nginx ]; then
  [ -n "${MAX_UPLOAD_SIZE:-}" ] || die "MAX_UPLOAD_SIZE isn't set in deploy/k8s/deploy.env"
  INGRESS_ANNOTATIONS="    nginx.ingress.kubernetes.io/proxy-body-size: \"$MAX_UPLOAD_SIZE\"
    nginx.ingress.kubernetes.io/proxy-request-buffering: \"off\"
    nginx.ingress.kubernetes.io/proxy-read-timeout: \"300\"
    nginx.ingress.kubernetes.io/proxy-send-timeout: \"300\""
  API_ORIGIN_ANNOTATIONS="$INGRESS_ANNOTATIONS
    nginx.ingress.kubernetes.io/whitelist-source-range: \"$API_ORIGIN_ALLOWED_SOURCES\""
else
  API_ORIGIN_ANNOTATIONS="    traefik.ingress.kubernetes.io/router.middlewares: \"$NAMESPACE-api-origin-sources@kubernetescrd\""
fi
# The same allow-list as a YAML list, for Traefik's middleware
API_ORIGIN_SOURCE_LIST="[$(printf '%s' "$API_ORIGIN_ALLOWED_SOURCES" | tr -d ' ' | sed 's/[^,][^,]*/"&"/g')]"
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
# Malware scanning of uploads (#455, 27-clamav.yaml): off (nothing scanned, nothing extra runs), or clamav: a ClamAV
# pod (about 2 GB of memory) that every upload is streamed to before it's stored; uploads are refused while it's down
export MALWARE_SCAN="${MALWARE_SCAN:-off}"
case "$MALWARE_SCAN" in off|clamav) ;; *) die "MALWARE_SCAN is off or clamav" ;; esac
export CLAMAV_IMAGE="${CLAMAV_IMAGE:-clamav/clamav:1.5.4@sha256:e19130cdebaeacc71fa46eab3ed6f69941f2f22aa5e08d55e9197f8edf43ce5e}"
export CLAMAV_MEMORY_REQUEST="${CLAMAV_MEMORY_REQUEST:-1536Mi}" CLAMAV_MEMORY_LIMIT="${CLAMAV_MEMORY_LIMIT:-2560Mi}"
# clamd's longest stream: the largest upload (MAX_UPLOAD_SIZE, nginx's "100m"; clamd has K and M, no G)
upload_size="$(printf '%s' "${MAX_UPLOAD_SIZE:-100m}" | tr '[:upper:]' '[:lower:]')"
case "$upload_size" in
  *g) CLAMD_MAX_SIZE="$(( ${upload_size%g} * 1024 ))M" ;;
  *m|*k) CLAMD_MAX_SIZE="$(printf '%s' "$upload_size" | tr '[:lower:]' '[:upper:]')" ;;
  *) CLAMD_MAX_SIZE="$upload_size" ;;
esac
export CLAMD_MAX_SIZE
# freshclam reads no proxy variables: its config names the egress proxy, when there is one
export FRESHCLAM_PROXY=""
[ "$EGRESS_PROXY" != off ] && FRESHCLAM_PROXY="    HTTPProxyServer egress-proxy
    HTTPProxyPort 3128"
CLAMAV_CONFIG_SHA="$(printf '%s|%s|%s' "$CLAMD_MAX_SIZE" "$FRESHCLAM_PROXY" "$(sed -n '/^data:/,/^---/p' "$K8S/manifests/27-clamav.yaml")" \
  | shasum -a 256 | cut -c1-16)"
export CLAMAV_CONFIG_SHA
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
VOLUME_ALERT_FREE_PERCENT="${VOLUME_ALERT_FREE_PERCENT:-10}"
case "$VOLUME_ALERT_FREE_PERCENT" in
  ''|*[!0-9]*) die "VOLUME_ALERT_FREE_PERCENT is a whole percent, 1 to 90 (10 by default)" ;;
esac
if [ "$VOLUME_ALERT_FREE_PERCENT" -lt 1 ] || [ "$VOLUME_ALERT_FREE_PERCENT" -gt 90 ]; then
  die "VOLUME_ALERT_FREE_PERCENT is a whole percent, 1 to 90 (10 by default)"
fi
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
export GRAFANA_DASHBOARDS_SHA REDIS_KEY_SHA PORTAL_REDIS_KEY_SHA S3_KEY_SHA
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
if [ "$EGRESS_PROXY" != off ]; then
  EGRESS_PROXY_CONFIG_SHA="$(python3 deploy/k8s/egress_hosts.py squid config/litellm.yaml "$K8S/engine.env" "$K8S/portal.env" | shasum -a 256 | cut -c1-16)"
fi
MON="$K8S/monitoring"
# The alert rules, with deploy.env's threshold for a volume nearly full (VOLUME_ALERT_FREE_PERCENT)
alert_rules() {
  local frac marker='< 0.1  # VOLUME_ALERT_FREE_PERCENT'
  frac="$(awk -v p="$VOLUME_ALERT_FREE_PERCENT" 'BEGIN { printf "%g", p / 100 }')"
  grep -qF "$marker" "$MON/rules.yml" || die "monitoring/rules.yml has lost its VOLUME_ALERT_FREE_PERCENT line"
  sed "s|$marker|< $frac  # VOLUME_ALERT_FREE_PERCENT|" "$MON/rules.yml"
}
PROMETHEUS_CONFIG_SHA="$(cat "$MON/prometheus.yml" <(alert_rules) | shasum -a 256 | cut -c1-16)"
GRAFANA_DASHBOARDS_SHA="$(cat "$MON"/dashboards/*.json | shasum -a 256 | cut -c1-16)"
# Pods restart when their settings file changes (the checksum is in their template)
sha_of() { if [ -f "$1" ]; then shasum -a 256 "$1" | cut -c1-16; else echo none; fi; }
ENGINE_ENV_SHA="$(sha_of "$K8S/engine.env")"
PORTAL_ENV_SHA="$(sha_of "$K8S/portal.env")"
TUNNEL_ENV_SHA="$(sha_of "$K8S/tunnel.env")"
# Redis, the portal's Redis and SeaweedFS read their password only when they start: restarted when it changes (a
# rotation, docs/operations/secret-rotation.md), and not for other settings
key_sha() { local k; for k in "${@:2}"; do sed -n "s/^$k=//p" "$1" 2>/dev/null | head -1; done | shasum -a 256 | cut -c1-16; }
REDIS_KEY_SHA="$(key_sha "$K8S/engine.env" REDIS_PASSWORD)"
PORTAL_REDIS_KEY_SHA="$(key_sha "$K8S/portal.env" PORTAL_REDIS_PASSWORD)"
S3_KEY_SHA="$(key_sha "$K8S/portal.env" PORTAL_S3_ACCESS_KEY PORTAL_S3_SECRET_KEY)"
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
    case "$FRONTEND:$(basename "$f")" in cluster:61-*|cluster:62-*|cluster:81-*|worker:60-*) continue ;; esac
    case "$INGRESS_CONTROLLER:$(basename "$f")" in nginx:62-*) continue ;; esac  # Traefik's own objects
    [ "$(basename "$f")" = 70-cloudflared.yaml ] && [ ! -f "$K8S/tunnel.env" ] && [ "${RENDER_ALL:-0}" = 0 ] && continue
    [ "$(basename "$f")" = 45-autoscale.yaml ] && [ "$AUTOSCALE" != on ] && continue
    case "$(basename "$f")" in
      36-music.yaml) [ "$MUSIC_READER" = on ] || continue ;;
      27-clamav.yaml) [ "$MALWARE_SCAN" = clamav ] || continue ;;
      46-music-autoscale.yaml) if [ "$MUSIC_READER" != on ] || [ "$AUTOSCALE" != on ]; then continue; fi ;;
      37-formula.yaml) [ "$FORMULA_READER" = on ] || continue ;;
      47-formula-autoscale.yaml) if [ "$FORMULA_READER" != on ] || [ "$AUTOSCALE" != on ]; then continue; fi ;;
      90-backup.yaml) [ "$BACKUPS" = cronjob ] || [ "${RENDER_ALL:-0}" = 1 ] || continue ;;
      95-monitoring.yaml) [ "$MONITORING" = on ] || [ "${RENDER_ALL:-0}" = 1 ] || continue ;;
      26-egress-proxy.yaml) [ "$EGRESS_PROXY" != off ] || [ "${RENDER_ALL:-0}" = 1 ] || continue ;;
      82-egress-enforce.yaml) [ "$EGRESS_PROXY" = enforce ] || [ "${RENDER_ALL:-0}" = 1 ] || continue ;;
      83-egress-deny.yaml|84-egress-direct.yaml) [ "$EGRESS_DENY" = on ] || continue ;;
    esac
    render_file "$f" > "$out/$(basename "$f")" || die "$(basename "$f") uses a setting that isn't set"
  done
  # Prometheus's own config (its rules and Grafana's dashboards go in as they are: they use $ and {{ }} themselves)
  render_file "$MON/prometheus.yml" > "$out/prometheus.yml" \
    || die "monitoring/prometheus.yml uses a setting that isn't set"
  alert_rules > "$out/rules.yml"
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

# The database back to an earlier migration (docs/operations/rollback.md), before the earlier release is deployed:
# that release's image doesn't know the later migrations, so the image that's running does it. Backed up first.
if [ "$CMD" = downgrade ]; then
  running="$("${KC[@]}" "${NS[@]}" get deployment/portal-api -o jsonpath='{.spec.template.spec.containers[0].image}')" \
    || die "the portal isn't deployed, so there's nothing to take back"
  current="$("${KC[@]}" "${NS[@]}" exec statefulset/postgres -- psql -U portal -d portal -tAc \
    "select version_num from alembic_version")" || die "couldn't read the database's migration"
  log "the database is at migration $current; going back to $DOWNGRADE_TO with $running"
  log "each migration undone drops what it added, and whatever was written there since (docs/operations/rollback.md)"
  if [ "$YES" != 1 ]; then
    read -r -p "Take the database back to $DOWNGRADE_TO? [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || die "stopped"
  fi
  if [ "$BACKUP" = 1 ]; then
    log "backing up the database first"
    K8S_NAMESPACE="$NAMESPACE" scripts/backup.sh || die "the backup failed, so nothing was changed"
  fi
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  export PORTAL_TAG="${running##*:}"
  render_file "$K8S/migrate-job.yaml" | sed -e "s/portal-migrate-$RUN_ID/portal-downgrade-$RUN_ID/" \
    -e "s/\"portal.cli\", \"migrate\"\]/\"portal.cli\", \"downgrade\", \"$DOWNGRADE_TO\"]/" > "$WORK/downgrade.yaml"
  grep -q '"downgrade"' "$WORK/downgrade.yaml" || die "couldn't make the downgrade job from migrate-job.yaml"
  "${KC[@]}" apply -f "$WORK/downgrade.yaml" >/dev/null
  if ! "${KC[@]}" "${NS[@]}" wait --for=condition=complete "job/portal-downgrade-$RUN_ID" --timeout=300s \
      >/dev/null; then
    "${KC[@]}" "${NS[@]}" logs "job/portal-downgrade-$RUN_ID" --tail=50 >&2 || true
    die "the downgrade failed (the backup from just before is in ./backups)"
  fi
  "${KC[@]}" "${NS[@]}" logs "job/portal-downgrade-$RUN_ID" --tail=5 >&2 || true
  log "now deploy the earlier release: its tags in deploy/k8s/deploy.env, then scripts/deploy.sh"
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

# Traffic between the nodes (#452): checked (scripts/check_cluster_encryption.sh, read-only) and shown before
# deploying. require: stop when it isn't encrypted, or can't be told; off: not checked
export NODE_ENCRYPTION="${NODE_ENCRYPTION:-check}"
case "$NODE_ENCRYPTION" in check|require|off) ;; *) die "NODE_ENCRYPTION is check, require or off" ;; esac
encryption="not checked (NODE_ENCRYPTION=off)"
if [ "$NODE_ENCRYPTION" != off ]; then
  ctx=(); [ -n "${KUBE_CONTEXT:-}" ] && ctx=(--context "$KUBE_CONTEXT")
  enc_rc=0; encryption="$("$ROOT/scripts/check_cluster_encryption.sh" ${ctx[@]+"${ctx[@]}"} 2>&1)" || enc_rc=$?
  encryption="$(printf '%s\n' "$encryption" | tail -n 1)"
  if [ "$enc_rc" != 0 ] && [ "$NODE_ENCRYPTION" = require ]; then
    die "traffic between the nodes: $encryption (NODE_ENCRYPTION=require)"
  fi
fi

# --- deploy ------------------------------------------------------------------------------------------
if [ ! -f "$K8S/engine.env" ] || [ ! -f "$K8S/portal.env" ]; then
  die "missing deploy/k8s/engine.env or portal.env: run scripts/deploy.sh init"
fi
shared_keys
url_safe_passwords
classes=("$STORAGE_CLASS" "$SHARED_STORAGE_CLASS"); [ "$MONITORING" = on ] && classes+=("$MONITORING_STORAGE_CLASS")
if [ -n "$ENGINE_NODES" ]; then
  [ -n "$("${KC[@]}" get nodes -l "$ENGINE_NODES" -o name 2>/dev/null)" ] \
    || die "no node has the label $ENGINE_NODES (ENGINE_NODES): label the engine's nodes first, or the engine can't start"
fi
for sc in "${classes[@]}"; do
  "${KC[@]}" get storageclass "$sc" >/dev/null 2>&1 \
    || die "the cluster has no StorageClass '$sc' (install one, or change it in deploy/k8s/deploy.env)"
done
TUNNEL=0; [ -f "$K8S/tunnel.env" ] && grep -q '^TUNNEL_TOKEN=.' "$K8S/tunnel.env" && TUNNEL=1
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# Volumes whose size in deploy.env went up (#592): grown before anything is applied, since an apply can't change a
# StatefulSet's volume size (docs/deployment/first-cluster/storage.md#growing-a-volume). deploy/k8s/volume_sizes.py
# compares each setting with its claim on the cluster; a smaller size, or a class that can't grow one, stops here
for v in DB_SIZE REDIS_SIZE FILES_SIZE JOBDATA_SIZE; do
  [ -n "${!v:-}" ] || die "$v isn't set in deploy/k8s/deploy.env"
done
volumes=("DB_SIZE=$DB_SIZE:statefulset/postgres" "REDIS_SIZE=$REDIS_SIZE:statefulset/redis"
  "FILES_SIZE=$FILES_SIZE:statefulset/seaweedfs" "JOBDATA_SIZE=$JOBDATA_SIZE:pvc/jobdata")
if [ "$MONITORING" = on ]; then
  volumes+=("PROMETHEUS_SIZE=$PROMETHEUS_SIZE:pvc/prometheus-data:deployment/prometheus"
    "GRAFANA_SIZE=$GRAFANA_SIZE:pvc/grafana-data:deployment/grafana")
fi
[ "$BACKUPS" = cronjob ] && volumes+=("BACKUP_SIZE=$BACKUP_SIZE:pvc/backups")
"${KC[@]}" "${NS[@]}" get pvc -o json > "$WORK/pvcs.json" || die "couldn't read the volume claims in $NAMESPACE"
"${KC[@]}" "${NS[@]}" get statefulset -o json > "$WORK/statefulsets.json" || die "couldn't read the StatefulSets in $NAMESPACE"
"${KC[@]}" get storageclass -o json > "$WORK/classes.json" || die "couldn't read the cluster's storage classes"
volume_plan="$(python3 "$K8S/volume_sizes.py" plan "$WORK/pvcs.json" "$WORK/statefulsets.json" "$WORK/classes.json" \
  "${volumes[@]}")" || die "stopped before changing anything: a volume's size in deploy.env can't be applied (above)"

# With EGRESS_DENY=on, Prometheus, kube-state-metrics and the veraPDF restart reach the API only at KUBE_API_CIDRS
# and KUBE_API_PORT: each of the kubernetes service's endpoints must be in them, or those stop working unnoticed
if [ "$EGRESS_DENY" = on ]; then
  api_endpoints="$("${KC[@]}" -n default get endpointslice -l kubernetes.io/service-name=kubernetes \
    -o jsonpath='{range .items[*]}{range .endpoints[*]}{.addresses[0]}{" "}{end}{":"}{.ports[0].port}{"\n"}{end}')" \
    || die "couldn't read the kubernetes service's endpoints (to check KUBE_API_CIDRS)"
  python3 - "$KUBE_API_CIDRS" "$KUBE_API_PORT" "$api_endpoints" <<'PY' || die "EGRESS_DENY=on: set KUBE_API_CIDRS and KUBE_API_PORT in deploy.env to the API servers' addresses and port (above)"
import ipaddress, sys
nets = [ipaddress.ip_network(c if "/" in c else c + "/32", strict=False) for c in sys.argv[1].split(",")]
bad = []
for row in sys.argv[3].splitlines():
    addrs, _, port = row.rpartition(":")
    for a in addrs.split():
        if port != sys.argv[2] or not any(ipaddress.ip_address(a) in n for n in nets):
            bad.append(f"{a}:{port}")
if bad or not sys.argv[3].strip():
    print(f"the Kubernetes API is at {', '.join(bad)}, outside KUBE_API_CIDRS/KUBE_API_PORT" if bad
          else "the kubernetes service has no endpoints", file=sys.stderr)
    sys.exit(1)
PY
fi

echo "Deploying to $SERVER, namespace $NAMESPACE, as https://$APP_HOST (web app: $FRONTEND)"
echo "  engine $ENGINE_TAG, portal $PORTAL_TAG, web $WEB_TAG; storage $STORAGE_CLASS and $SHARED_STORAGE_CLASS;"
echo "  Cloudflare tunnel: $([ "$TUNNEL" = 1 ] && echo "yes (deploy/k8s/tunnel.env)" || echo "no")"
case "$EGRESS_PROXY" in
  off) echo "  outbound: the portal, the AI gateway and the Adobe worker$([ "$MALWARE_SCAN" = clamav ] && echo " and ClamAV") reach the internet directly (EGRESS_PROXY=off)" ;;
  log) echo "  outbound: through the egress proxy, everything let through and recorded (EGRESS_PROXY=log)" ;;
  enforce) echo "  outbound: through the egress proxy, only these hosts (EGRESS_PROXY=enforce):"
    python3 deploy/k8s/egress_hosts.py hosts config/litellm.yaml "$K8S/engine.env" "$K8S/portal.env" | sed 's/^/    /' ;;
esac
if [ "$EGRESS_DENY" = on ]; then
  echo "  outbound from everything else: only what each pod uses (EGRESS_DENY=on; Kubernetes API at $KUBE_API_CIDRS port $KUBE_API_PORT)"
else
  echo "  outbound from everything else: anywhere, apart from the pods that open documents (EGRESS_DENY=off)"
fi
echo "  traffic between nodes: $encryption"
if [ "$AUTOSCALE" = on ]; then
  echo "  scaling with the queue: $ENGINE_WORKERS to $ENGINE_WORKERS_MAX workers ($WORKER_CONCURRENCY documents each), veraPDF up to $VERAPDF_MAX, AI gateway up to $LITELLM_MAX"
else
  echo "  fixed size: $ENGINE_WORKERS worker(s) ($WORKER_CONCURRENCY documents each); scaling is off$([ "$has_keda" = 0 ] && echo " (no KEDA on the cluster)")"
fi
if [ "$MALWARE_SCAN" = clamav ]; then
  echo "  malware scanning: ClamAV, every upload before it's stored; uploads are refused while it's down"
else
  echo "  malware scanning: off (MALWARE_SCAN=off)"
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
if [ -n "$volume_plan" ]; then
  echo "  volumes to grow (docs/deployment/first-cluster/storage.md#growing-a-volume):"
  while IFS=$'\t' read -r step name from to setting _; do
    case "$step" in
      grow) echo "    $name: $from to $to ($setting)" ;;
      recreate) echo "    statefulset/$name made again for $to, its pod and volume left running (delete --cascade=orphan)" ;;
    esac
  done <<< "$volume_plan"
fi
if [ "$YES" != 1 ]; then
  read -r -p "Go ahead? [y/N] " answer
  [[ "$answer" =~ ^[Yy] ]] || die "stopped"
fi

render_all "$WORK"
apply() { "${KC[@]}" apply -f "$WORK/$1" >/dev/null && log "applied $1"; }
# The engine's own key for the AI gateway (#457): limited to the models in config/litellm.yaml, with no right to
# manage the gateway, instead of the master key. Made by the gateway from inside its pod (deploy/k8s/gateway_key.py:
# the master key never leaves it), kept in engine.env as GATEWAY_API_KEY, and its models kept in step with the config
# on each deploy. A new one (the first time, when the gateway no longer knows the old one, or --new-gateway-key)
# restarts the engine's pods, and the old one is revoked once they use it. GATEWAY_ENGINE_BUDGET (USD per 30 days)
# caps what the key may spend, on top of the gateway's overall budget.
gateway_key() {
  "${KC[@]}" "${NS[@]}" exec -i deploy/litellm -c litellm -- python3 -c "$(cat "$K8S/gateway_key.py")" "$1"
}
engine_gateway_key() {
  local master key models limits new
  master="$(sed -n 's/^LITELLM_MASTER_KEY=//p' "$K8S/engine.env" | head -1)"
  key="$(sed -n 's/^GATEWAY_API_KEY=//p' "$K8S/engine.env" | head -1)"
  models="$(python3 -c 'import json, re, sys; print(json.dumps(re.findall(r"^\s*- model_name:\s*(\S+)", open(sys.argv[1]).read(), re.M)))' config/litellm.yaml)"
  limits="\"models\": $models$([ -n "${GATEWAY_ENGINE_BUDGET:-}" ] && echo ", \"max_budget\": $GATEWAY_ENGINE_BUDGET")"
  if [ -n "$key" ] && [ "$key" != "$master" ] && [ "$NEW_GATEWAY_KEY" = 0 ] \
      && printf '{"key": "%s"}' "$key" | gateway_key check; then
    printf '{"key": "%s", %s}' "$key" "$limits" | gateway_key update || die "the AI gateway didn't update the engine's key"
    log "engine's gateway key: limited to $(echo "$models" | tr -d '[]"' | tr ',' ' ')"
    return
  fi
  new="$(printf '{%s}' "$limits" | gateway_key generate)" || die "the AI gateway didn't make a key for the engine"
  [ -n "$new" ] || die "the AI gateway made an empty key for the engine"
  if grep -q '^GATEWAY_API_KEY=' "$K8S/engine.env"; then
    sed -i.bak "s|^GATEWAY_API_KEY=.*|GATEWAY_API_KEY=$new|" "$K8S/engine.env" && rm -f "$K8S/engine.env.bak"
  else
    echo "GATEWAY_API_KEY=$new" >> "$K8S/engine.env"
  fi
  secret pdfrem-engine-env "$NAMESPACE" "$K8S/engine.env" --drop "$GATEWAY_ONLY|^ADOBE_CLIENT_"
  local d; for d in engine-api engine-worker engine-beat engine-adobe-worker; do
    "${KC[@]}" "${NS[@]}" rollout restart "deployment/$d" >/dev/null 2>&1 || true
  done
  wait_rollout deployment/engine-api
  wait_rollout deployment/engine-worker
  if [ -n "$key" ] && [ "$key" != "$master" ]; then
    printf '{"key": "%s"}' "$key" | gateway_key delete >/dev/null || log "the old engine key couldn't be revoked: revoke it in the gateway's UI"
  fi
  log "the engine has its own gateway key now (limited to its models), in deploy/k8s/engine.env"
}
# The volumes in the plan grown (#592): each claim's request patched (the storage driver grows the disk, then the node
# its filesystem), each StatefulSet whose volume size changed deleted without its pods and claims (--cascade=orphan)
# for the apply below to make again, then a wait for each claim's new size. A filesystem that only grows when its pod
# starts again (FileSystemResizePending) is said, here and at the end
volume_notes=()
grow_volumes() {
  [ -n "$volume_plan" ] || return 0
  local step name from to setting workload grown=() entry st i restart
  while IFS=$'\t' read -r step name from to setting workload; do
    [ "$step" = grow ] || continue
    "${KC[@]}" "${NS[@]}" patch pvc "$name" --type=merge \
      -p "{\"spec\":{\"resources\":{\"requests\":{\"storage\":\"$to\"}}}}" >/dev/null \
      || die "couldn't grow the volume $name to $to ($setting)"
    log "volume $name: asked for $to (was $from)"
    grown+=("$name $to ${workload:--}")
  done <<< "$volume_plan"
  while IFS=$'\t' read -r step name from to setting _; do
    [ "$step" = recreate ] || continue
    "${KC[@]}" "${NS[@]}" delete statefulset "$name" --cascade=orphan >/dev/null \
      || die "couldn't remove statefulset/$name to make it again with its new volume size"
    log "statefulset/$name removed, its pod and volume left running: made again for $to below"
  done <<< "$volume_plan"
  for entry in ${grown[@]+"${grown[@]}"}; do
    read -r name to workload <<< "$entry"
    st=resizing
    for ((i = 0; i < 24; i++)); do
      if "${KC[@]}" "${NS[@]}" get pvc "$name" -o json > "$WORK/pvc.json"; then
        st="$(python3 "$K8S/volume_sizes.py" state "$WORK/pvc.json" "$to")"
      fi
      [ "$st" = grown ] && break
      sleep 5
    done
    restart="kubectl -n $NAMESPACE rollout restart $workload"
    [ "$workload" = - ] && restart="restart the pods that use it"
    case "$st" in
      grown) log "volume $name is $to, its filesystem too: nothing to restart" ;;
      pending) volume_notes+=("volume $name: the disk is $to but its filesystem grows only when its pod starts again (FileSystemResizePending): $restart") ;;
      *) volume_notes+=("volume $name: the storage hadn't finished growing it to $to after 2 minutes: watch kubectl -n $NAMESPACE get pvc $name; if it then says FileSystemResizePending: $restart") ;;
    esac
  done
  for entry in ${volume_notes[@]+"${volume_notes[@]}"}; do log "$entry"; done
}
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
GATEWAY_ONLY='^(LITELLM_|UI_|LOCAL_|OPENROUTER_|AWS_)'
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
    --from-file=rules.yml="$WORK/rules.yml" --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null
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
# First, before any workload starts: what may reach what (see the README, "Network policies and the Redis password"),
# so on a fresh install no pod runs a moment without them (#453)
apply 80-network-policies.yaml
# Default-deny egress (#594). The proxy's users keep their way out (84-egress-direct.yaml) until enforce's policies are
# in place, so they're never cut off while the mode changes; it's applied first, before the deny
if [ "$EGRESS_DENY" = on ]; then
  if [ "$EGRESS_PROXY" != enforce ] || ! "${KC[@]}" "${NS[@]}" get networkpolicy/outbound-through-proxy >/dev/null 2>&1; then
    apply 84-egress-direct.yaml
  fi
  apply 83-egress-deny.yaml
else
  "${KC[@]}" "${NS[@]}" delete networkpolicy -l pdfrem/egress-deny --ignore-not-found >/dev/null
fi
if [ "$FRONTEND" = worker ]; then
  apply 81-api-origin-policy.yaml
else
  "${KC[@]}" "${NS[@]}" delete networkpolicy/portal-api-origin --ignore-not-found >/dev/null
fi
grow_volumes
apply 10-jobdata.yaml
apply 20-postgres.yaml
apply 21-redis.yaml
apply 22-seaweedfs.yaml
apply 23-redis-portal.yaml
wait_rollout statefulset/postgres
wait_rollout statefulset/redis
wait_rollout deployment/redis-portal
wait_rollout statefulset/seaweedfs
if [ "$EGRESS_PROXY" != off ]; then
  python3 deploy/k8s/egress_hosts.py squid config/litellm.yaml "$K8S/engine.env" "$K8S/portal.env" > "$WORK/squid.conf"
  "${KC[@]}" "${NS[@]}" create configmap egress-proxy-config --from-file=squid.conf="$WORK/squid.conf" \
    --dry-run=client -o yaml | "${KC[@]}" apply -f - >/dev/null
  apply 26-egress-proxy.yaml
  wait_rollout deployment/egress-proxy
fi
# The malware scanner (#455), before the portal's new pods start scanning with it; its signatures' volume is kept
# when it's turned off
if [ "$MALWARE_SCAN" = clamav ]; then
  apply 27-clamav.yaml
  wait_rollout deployment/clamav 420s
else
  "${KC[@]}" "${NS[@]}" delete deployment/clamav service/clamav configmap/clamav-config networkpolicy/clamav \
    --ignore-not-found >/dev/null
fi
# Once the proxy is up: the portal, the gateway and the Adobe worker reach the internet only through it
if [ "$EGRESS_PROXY" = enforce ]; then
  apply 82-egress-enforce.yaml
  "${KC[@]}" "${NS[@]}" delete networkpolicy/direct-egress --ignore-not-found >/dev/null
else
  "${KC[@]}" "${NS[@]}" delete networkpolicy/outbound-through-proxy networkpolicy/portal-outbound-through-proxy \
    --ignore-not-found >/dev/null
fi

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
  [ "$INGRESS_CONTROLLER" = traefik ] && apply 62-traefik-api-origin.yaml
  apply 61-api-origin.yaml
  "${KC[@]}" "${NS[@]}" delete ingress/web deployment/web service/web --ignore-not-found >/dev/null
else
  apply 60-web.yaml
  "${KC[@]}" "${NS[@]}" delete ingress/api-origin --ignore-not-found >/dev/null
fi
wait_rollout deployment/litellm 420s
engine_gateway_key
wait_rollout deployment/verapdf 300s
wait_rollout deployment/engine-api
wait_rollout deployment/engine-worker
wait_rollout deployment/engine-beat
[ "${ADOBE_WORKERS:-0}" -gt 0 ] && wait_rollout deployment/engine-adobe-worker
wait_rollout deployment/portal-api
wait_rollout deployment/portal-worker
# The proxy turned off: removed once the pods that used it have rolled without it
if [ "$EGRESS_PROXY" = off ]; then
  "${KC[@]}" "${NS[@]}" delete deployment/egress-proxy service/egress-proxy configmap/egress-proxy-config \
    networkpolicy/egress-proxy --ignore-not-found >/dev/null
fi
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

# What ran, for a rollback (docs/operations/rollback.md)
migration="$("${KC[@]}" "${NS[@]}" exec statefulset/postgres -- psql -U portal -d portal -tAc \
  "select version_num from alembic_version" 2>/dev/null || echo unknown)"
ran="$(date '+%Y-%m-%d %H:%M:%S %z')"
for v in ENGINE_TAG PORTAL_TAG WEB_TAG MUSIC_TAG FORMULA_TAG; do if [ -n "${!v}" ]; then ran+=" $v=${!v}"; fi; done
echo "$ran migration=$migration" >> "$K8S/deploy-history"

for entry in ${volume_notes[@]+"${volume_notes[@]}"}; do log "$entry"; done
if [ "$FRONTEND" = worker ]; then
  log "deployed the cluster side; the API origin is https://$API_ORIGIN_HOST (route it through the tunnel and protect"
  log "it with Cloudflare Access), then publish the web app with scripts/deploy-worker.sh"
else
  log "deployed: https://$APP_HOST (once the Cloudflare tunnel routes $APP_HOST to ingress-nginx)"
fi
