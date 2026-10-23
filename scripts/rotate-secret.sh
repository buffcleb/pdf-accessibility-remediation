#!/usr/bin/env bash
# Rotate one of the service's secrets (docs/operations/secret-rotation.md): a new value in deploy/k8s/engine.env,
# portal.env or tunnel.env, the database told first where it checks the password itself, then scripts/deploy.sh,
# which updates the Kubernetes secrets and restarts what reads them. No value is ever printed.
#
#   scripts/rotate-secret.sh NAME                       make a new value for NAME, then deploy
#   scripts/rotate-secret.sh NAME --typed               type the new value (a key you've just made at the provider);
#                                                       it isn't shown, and works for any key in the three files
#   scripts/rotate-secret.sh PORTAL_JWT_PRIVATE_KEYS            a new signing key, put first; the old ones still
#                                                               verify, so nobody is signed out
#   scripts/rotate-secret.sh PORTAL_JWT_PRIVATE_KEYS --finish   keep only the newest key (after the session lifetime)
#
# Made here: REDIS_PASSWORD PORTAL_REDIS_PASSWORD SERVICE_API_KEY (and the portal's copy, PORTAL_ENGINE_API_KEY)
#   PORTAL_SECRET_KEY PORTAL_JWT_PRIVATE_KEYS PORTAL_S3_SECRET_KEY (with a new PORTAL_S3_ACCESS_KEY)
#   PORTAL_DB_PASSWORD LITELLM_DB_PASSWORD POSTGRES_ADMIN_PASSWORD (the database role first) PORTAL_APP_DB_PASSWORD
#   MONITORING_DB_PASSWORD LITELLM_MASTER_KEY UI_PASSWORD
# GATEWAY_API_KEY runs scripts/deploy.sh --new-gateway-key. Refused, with the reason: PORTAL_S3_SSE_KEK,
#   GRAFANA_ADMIN_PASSWORD, GRAFANA_SECRET_KEY.
#
# Options:
#   -y, --yes       passed to scripts/deploy.sh (don't ask before changing the cluster)
#   --no-deploy     change the files (and the database role) only; run scripts/deploy.sh yourself, soon
#   -h, --help
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
K8S="${ROTATE_K8S_DIR:-$ROOT/deploy/k8s}"  # another folder only for the tests
die() { echo "rotate-secret: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }
rand() { openssl rand -hex "${1:-24}"; }

NAME="" TYPED=0 FINISH=0 DEPLOY=1 YES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --typed) TYPED=1 ;;
    --finish) FINISH=1 ;;
    --no-deploy) DEPLOY=0 ;;
    -y|--yes) YES=(--yes) ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) [ -z "$NAME" ] || die "one secret at a time"; NAME="$1" ;;
  esac
  shift
done
[ -n "$NAME" ] || { usage; exit 2; }
[[ "$NAME" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "$NAME isn't a setting's name"
[ "$FINISH" = 0 ] || [ "$NAME" = PORTAL_JWT_PRIVATE_KEYS ] || die "--finish is for PORTAL_JWT_PRIVATE_KEYS"

# Which file holds a key: the first of the three that has it
file_of() {
  local f; for f in engine.env portal.env tunnel.env; do
    if [ -f "$K8S/$f" ] && grep -q "^$1=" "$K8S/$f"; then echo "$K8S/$f"; return; fi
  done
  return 1
}
# KEY's value in FILE becomes what's on standard input (never a command's argument); added when it isn't there
set_key() {
  python3 -c '
import re, sys
path, key, value = sys.argv[1], sys.argv[2], sys.stdin.read()
lines, done = [], False
for line in open(path, encoding="utf-8").read().splitlines(True):
    if re.match(re.escape(key) + "=", line):
        if not done:
            lines.append(f"{key}={value}\n")
            done = True
        continue
    lines.append(line)
if not done:
    lines.append(f"{key}={value}\n")
open(path, "w", encoding="utf-8").write("".join(lines))
' "$1" "$2"
}
get_key() { sed -n "s/^$2=//p" "$1" | head -1; }

kube() {
  local ctx=()
  set -a
  # shellcheck disable=SC1091
  [ -f "$K8S/deploy.env" ] && . "$K8S/deploy.env"
  set +a
  export KUBECONFIG="${KUBECONFIG_PATH:-${KUBECONFIG:-$HOME/.kube/config}}"
  [ -n "${KUBE_CONTEXT:-}" ] && ctx=(--context "$KUBE_CONTEXT")
  kubectl --request-timeout=30s ${ctx[@]+"${ctx[@]}"} -n "${NAMESPACE:-pdf-remediation}" "$@"
}
# A database role's new password, set as the superuser over the pod's own socket; the SQL goes on standard input
alter_role() {
  printf "ALTER ROLE %s PASSWORD '%s';\n" "$1" "$2" \
    | kube exec -i statefulset/postgres -- psql -q -v ON_ERROR_STOP=1 -U postgres -d portal >/dev/null
}
# How many API tokens work now (empty if the database can't be asked)
active_tokens() {
  echo "select count(*) from api_tokens where revoked_at is null and expires_at > now();" \
    | kube exec -i statefulset/postgres -- psql -qtA -U postgres -d portal 2>/dev/null | tr -d '[:space:]' || true
}
confirm() {
  [ ${#YES[@]} -gt 0 ] && return 0
  read -r -p "$1 [y/N] " answer
  [[ "$answer" =~ ^[Yy] ]] || die "stopped"
}

case "$NAME" in
  PORTAL_S3_SSE_KEK)
    die "PORTAL_S3_SSE_KEK encrypts every stored file and can't simply be changed: a new one makes them unreadable" \
      "(docs/operations/secret-rotation.md, \"The file store's encryption key\")" ;;
  GRAFANA_ADMIN_PASSWORD)
    die "Grafana keeps its admin password in its own database after the first start: change it in Grafana" \
      "(docs/operations/secret-rotation.md, \"Grafana\")" ;;
  GRAFANA_SECRET_KEY)
    die "GRAFANA_SECRET_KEY encrypts what Grafana stores and isn't rotated (docs/operations/secret-rotation.md)" ;;
  GATEWAY_API_KEY)
    [ "$TYPED" = 0 ] || die "the engine's gateway key is made by the gateway: no --typed"
    [ "$DEPLOY" = 1 ] || die "the engine's gateway key is made during a deploy: no --no-deploy"
    exec scripts/deploy.sh --new-gateway-key ${YES[@]+"${YES[@]}"} ;;
esac

if [ "$TYPED" = 1 ]; then
  case "$NAME" in
    PORTAL_DB_PASSWORD|LITELLM_DB_PASSWORD|POSTGRES_ADMIN_PASSWORD|PORTAL_JWT_PRIVATE_KEYS)
      die "$NAME is made here, not typed: scripts/rotate-secret.sh $NAME" ;;
  esac
  file="$(file_of "$NAME")" || die "$NAME isn't in engine.env, portal.env or tunnel.env"
  read -r -s -p "New value for $NAME (not shown): " value; echo >&2
  read -r -s -p "Again: " again; echo >&2
  [ -n "$value" ] || die "empty: nothing changed"
  [ "$value" = "$again" ] || die "the two didn't match: nothing changed"
  printf '"%s"' "$value" | set_key "$file" "$NAME"
  # The engine's API key is the same in both files
  if [ "$NAME" = SERVICE_API_KEY ]; then printf '"%s"' "$value" | set_key "$K8S/portal.env" PORTAL_ENGINE_API_KEY; fi
  if [ "$NAME" = PORTAL_ENGINE_API_KEY ]; then printf '"%s"' "$value" | set_key "$K8S/engine.env" SERVICE_API_KEY; fi
  unset value again
  log "$NAME changed in $(basename "$file")"
else
  case "$NAME" in
    REDIS_PASSWORD|PORTAL_REDIS_PASSWORD|PORTAL_APP_DB_PASSWORD|MONITORING_DB_PASSWORD)
      file="$(file_of "$NAME")" || die "$NAME isn't in engine.env or portal.env"
      rand 24 | set_key "$file" "$NAME" ;;
    UI_PASSWORD)
      file="$(file_of "$NAME")" || die "$NAME isn't in engine.env"
      rand 16 | set_key "$file" "$NAME" ;;
    LITELLM_MASTER_KEY)
      file="$(file_of "$NAME")" || die "$NAME isn't in engine.env"
      # A deployment from before #457 gives the engine the master key itself: it gets a key of its own first
      [ "$(get_key "$file" GATEWAY_API_KEY)" != "$(get_key "$file" LITELLM_MASTER_KEY)" ] \
        || die "the engine still uses the master key: run scripts/deploy.sh once (it gives the engine its own)"
      printf 'sk-%s' "$(rand 24)" | set_key "$file" "$NAME" ;;
    SERVICE_API_KEY|PORTAL_ENGINE_API_KEY)
      new="$(rand 24)"
      printf '%s' "$new" | set_key "$K8S/engine.env" SERVICE_API_KEY
      printf '%s' "$new" | set_key "$K8S/portal.env" PORTAL_ENGINE_API_KEY
      unset new ;;
    PORTAL_SECRET_KEY)
      # API tokens are kept as HMACs under this key (#518): every one stops, and nobody is told (#545)
      tokens="$(active_tokens)"
      case "$tokens" in
        0) stops="no API tokens are active" ;;
        1) stops="the 1 active API token stops working" ;;
        ''|*[!0-9]*) stops="every API token stops working (they couldn't be counted)" ;;
        *) stops="all $tokens active API tokens stop working" ;;
      esac
      confirm "A new PORTAL_SECRET_KEY signs everyone out, and voids codes and unsubscribe links sent. Also, $stops: \
scripts using one are refused (401) until its owner makes a new token in Settings, and nobody is emailed. Go on?"
      rand 48 | set_key "$K8S/portal.env" PORTAL_SECRET_KEY ;;
    PORTAL_S3_SECRET_KEY|PORTAL_S3_ACCESS_KEY)
      printf 'portal-%s' "$(rand 8)" | set_key "$K8S/portal.env" PORTAL_S3_ACCESS_KEY
      rand 24 | set_key "$K8S/portal.env" PORTAL_S3_SECRET_KEY ;;
    PORTAL_DB_PASSWORD|LITELLM_DB_PASSWORD|POSTGRES_ADMIN_PASSWORD)
      case "$NAME" in PORTAL_DB_PASSWORD) role=portal ;; LITELLM_DB_PASSWORD) role=litellm ;; *) role=postgres ;; esac
      file="$(file_of "$NAME")" || die "$NAME isn't in engine.env or portal.env"
      old="$(get_key "$file" "$NAME")" new="$(rand 24)"
      printf '%s' "$new" | set_key "$file" "$NAME"
      if ! alter_role "$role" "$new"; then
        printf '%s' "$old" | set_key "$file" "$NAME"
        die "couldn't set the $role role's password in the database: nothing changed"
      fi
      unset old new
      log "the database's $role role has its new password" ;;
    PORTAL_JWT_PRIVATE_KEYS)
      keys="$(mktemp)"
      python3 -c '
import re, subprocess, sys
path, finish = sys.argv[1], sys.argv[2] == "1"
pem = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.+?-----END [A-Z ]*PRIVATE KEY-----", re.S)
line = next((l for l in open(path, encoding="utf-8") if l.startswith("PORTAL_JWT_PRIVATE_KEYS=")), "")
value = line.split("=", 1)[1].strip() if line else ""
if len(value) > 1 and value[0] == value[-1] and value[0] in "\"\x27":
    value = value[1:-1]
keys = pem.findall(value.replace("\\n", "\n"))
if finish:
    if len(keys) < 2:
        sys.exit("only one signing key: nothing to drop")
    keys = keys[:1]
else:
    new = subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"],
                         capture_output=True, text=True, check=True).stdout
    keys = [pem.search(new).group(0)] + keys
sys.stdout.write("\"" + "\\n\\n".join(k.replace("\n", "\\n") for k in keys) + "\\n\"")
' "$K8S/portal.env" "$FINISH" > "$keys" || { rm -f "$keys"; die "the signing keys weren't changed"; }
      set_key "$K8S/portal.env" PORTAL_JWT_PRIVATE_KEYS < "$keys"
      rm -f "$keys" ;;
    *)
      die "$NAME isn't made here: make the new value where it comes from, then use --typed" ;;
  esac
  log "$NAME has a new value"
fi

if [ "$DEPLOY" = 1 ]; then
  log "deploying, which updates the secrets and restarts what reads them"
  scripts/deploy.sh ${YES[@]+"${YES[@]}"}
else
  log "run scripts/deploy.sh soon: the files have the new value, the cluster doesn't yet"
fi
