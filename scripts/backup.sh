#!/usr/bin/env bash
# Back up the PDF remediation service.
#
# Always: the portal database. By default also: the AI gateway's (LiteLLM) database.
# With flags: stored files (PDFs and reports, through the portal's S3 settings) and config files.
#
#   scripts/backup.sh                         portal + LiteLLM databases
#   scripts/backup.sh --all                   databases, files and config
#   scripts/backup.sh --files --config --keep 14 --upload s3://my-backups/pdf-remediation
#
# Options:
#   --out DIR            where backups go (default: $BACKUP_DIR or ./backups). Each run makes
#                        DIR/backup-YYYYmmdd-HHMMSS/ (folder 700, files 600)
#   --no-litellm         skip the LiteLLM database (spend logs, virtual keys)
#   --files              back up stored files (input and output PDFs, reports, tag maps)
#   --files-prefix P     only files under P, e.g. groups/<group id>/
#   --config             back up config: .env, .env.portal and config/ (plaintext by default: they hold
#                        secrets, so protect the backup location)
#   --config-file PATH   add another config file or folder (repeatable; implies --config)
#   --encrypt-config     encrypt the config archive: with age when BACKUP_AGE_RECIPIENT is set
#                        (recommended), otherwise with openssl AES-256 and the passphrase in
#                        BACKUP_PASSPHRASE
#   --all                --files --config (and both databases)
#   --keep N             afterwards, delete all but the newest N backups in --out
#   --k8s                back up the Kubernetes deployment (deploy/k8s/deploy.env's cluster and namespace; or set
#                        K8S_NAMESPACE). Config is then deploy/k8s/deploy.env, engine.env and portal.env
#   --upload URL         copy the finished backup to s3://bucket/prefix/backup-.../ using
#                        BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY, BACKUP_S3_ENDPOINT_URL (non-AWS),
#                        BACKUP_S3_REGION, or the standard AWS variables / machine role
#   -h, --help
#
# Where things run (override for deployments that don't use these Compose stacks):
#   PORTAL_COMPOSE   default: docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal
#   ENGINE_COMPOSE   default: docker compose
#   PORTAL_DB_EXEC   how to run pg_dump in the portal DB (default: $PORTAL_COMPOSE exec -T portal-db)
#   LITELLM_DB_EXEC  same for the LiteLLM DB (default: $ENGINE_COMPOSE exec -T litellm-db)
#   PORTAL_CLI       how to run the portal CLI with its settings
#                    (default: $PORTAL_COMPOSE run --rm --no-deps -T portal-api python -m portal.cli)
#
# Restore with scripts/restore.sh. Exit code is non-zero if any part failed; a failed run leaves no
# backup folder behind (only a .partial one, removed on the next run).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# On the cluster (--k8s, or K8S_NAMESPACE set): the same steps through kubectl, with deploy/k8s/deploy.env's
# kubeconfig and namespace. Anything set explicitly above still wins.
K8S_MODE=0
for a in "$@"; do [ "$a" = --k8s ] && K8S_MODE=1; done
[ -n "${K8S_NAMESPACE:-}" ] && K8S_MODE=1
if [ "$K8S_MODE" = 1 ]; then
  if [ -f deploy/k8s/deploy.env ]; then
    # shellcheck disable=SC1091
    eval "$(set -a; . deploy/k8s/deploy.env; set +a; printf 'K8S_KUBECONFIG=%q\nK8S_NS_FROM_FILE=%q\nK8S_CTX=%q\n' \
      "${KUBECONFIG_PATH:-}" "${NAMESPACE:-}" "${KUBE_CONTEXT:-}")"
    [ -n "$K8S_KUBECONFIG" ] && export KUBECONFIG="${KUBECONFIG:-$K8S_KUBECONFIG}"
  fi
  K8S_NAMESPACE="${K8S_NAMESPACE:-${K8S_NS_FROM_FILE:-pdf-remediation}}"
  KUBECTL="kubectl -n $K8S_NAMESPACE${K8S_CTX:+ --context $K8S_CTX}"
  PORTAL_DB_EXEC="${PORTAL_DB_EXEC:-$KUBECTL exec -i statefulset/postgres --}"
  LITELLM_DB_EXEC="${LITELLM_DB_EXEC:-$KUBECTL exec -i statefulset/postgres --}"
  PORTAL_CLI="${PORTAL_CLI:-$KUBECTL exec -i deployment/portal-api -- python -m portal.cli}"
fi

PORTAL_COMPOSE="${PORTAL_COMPOSE:-docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal}"
ENGINE_COMPOSE="${ENGINE_COMPOSE:-docker compose}"
PORTAL_DB_EXEC="${PORTAL_DB_EXEC:-$PORTAL_COMPOSE exec -T portal-db}"
LITELLM_DB_EXEC="${LITELLM_DB_EXEC:-$ENGINE_COMPOSE exec -T litellm-db}"
PORTAL_CLI_DEFAULT="$PORTAL_COMPOSE run --rm --no-deps -T portal-api python -m portal.cli"
PORTAL_CLI="${PORTAL_CLI:-$PORTAL_CLI_DEFAULT}"

OUT="${BACKUP_DIR:-$ROOT/backups}"
LITELLM=1 FILES=0 CONFIG=0 ENCRYPT=0 KEEP=0 UPLOAD="" FILES_PREFIX=""
CONFIG_PATHS=(.env .env.portal config)

usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "backup: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a folder}"; shift ;;
    --no-litellm) LITELLM=0 ;;
    --files) FILES=1 ;;
    --files-prefix) FILES=1; FILES_PREFIX="${2:?--files-prefix needs a prefix}"; shift ;;
    --config) CONFIG=1 ;;
    --config-file) CONFIG=1; CONFIG_PATHS+=("${2:?--config-file needs a path}"); shift ;;
    --encrypt-config) ENCRYPT=1 ;;
    --all) FILES=1; CONFIG=1 ;;
    --keep) KEEP="${2:?--keep needs a number}"; shift ;;
    --upload) UPLOAD="${2:?--upload needs s3://bucket/prefix}"; shift ;;
    --k8s) ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

if [ "$K8S_MODE" = 1 ]; then
  [ -z "$UPLOAD" ] || die "--upload isn't available with --k8s yet: back up, then copy the folder off the machine"
  CONFIG_PATHS=(deploy/k8s/deploy.env deploy/k8s/engine.env deploy/k8s/portal.env config "${CONFIG_PATHS[@]:3}")
fi
case "$KEEP" in ''|*[!0-9]*) die "--keep needs a whole number" ;; esac
[ -z "$UPLOAD" ] || [[ "$UPLOAD" == s3://* ]] || die "--upload needs s3://bucket/prefix"
if [ "$ENCRYPT" = 1 ]; then
  [ "$CONFIG" = 1 ] || die "--encrypt-config needs --config"
  if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
    command -v age >/dev/null || die "BACKUP_AGE_RECIPIENT is set but age isn't installed"
  else
    [ -n "${BACKUP_PASSPHRASE:-}" ] || die "--encrypt-config needs BACKUP_AGE_RECIPIENT or BACKUP_PASSPHRASE"
    command -v openssl >/dev/null || die "openssl isn't installed"
  fi
fi

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# Runs the portal CLI with a host folder available inside it at the same name (for uploads).
portal_cli_with_dir() {
  local dir="$1"; shift
  if [ "$PORTAL_CLI" = "$PORTAL_CLI_DEFAULT" ]; then
    $PORTAL_COMPOSE run --rm --no-deps -T -u "$(id -u):$(id -g)" -v "$dir:/backup/$(basename "$dir"):ro" \
      -e BACKUP_S3_ACCESS_KEY -e BACKUP_S3_SECRET_KEY -e BACKUP_S3_ENDPOINT_URL -e BACKUP_S3_REGION \
      -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_DEFAULT_REGION \
      portal-api python -m portal.cli "$1" "/backup/$(basename "$dir")" "${@:2}"
  else
    $PORTAL_CLI "$1" "$dir" "${@:2}"
  fi
}

umask 077
mkdir -p "$OUT"
LOCK="$OUT/.backup.lock"
mkdir "$LOCK" 2>/dev/null || die "another backup is running (remove $LOCK if not)"
NAME="backup-$(date +%Y%m%d-%H%M%S)"
WORK="$OUT/$NAME.partial"
cleanup() { rm -rf "$LOCK"; [ -d "$WORK" ] && rm -rf "$WORK" && log "incomplete backup removed"; return 0; }
trap cleanup EXIT
find "$OUT" -maxdepth 1 -name 'backup-*.partial' -type d -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$WORK"
chmod 700 "$WORK"
PARTS=()

# --- databases -----------------------------------------------------------------------------------
log "portal database"
$PORTAL_DB_EXEC pg_dump -U portal -d portal -Fc > "$WORK/portal.dump"
[ -s "$WORK/portal.dump" ] || die "the portal database dump is empty"
REVISION="$($PORTAL_DB_EXEC psql -U portal -d portal -tAc 'select version_num from alembic_version' 2>/dev/null || echo unknown)"
PARTS+=("portal database (migration $REVISION)")

if [ "$LITELLM" = 1 ]; then
  log "LiteLLM database"
  $LITELLM_DB_EXEC pg_dump -U litellm -d litellm -Fc > "$WORK/litellm.dump"
  [ -s "$WORK/litellm.dump" ] || die "the LiteLLM database dump is empty"
  PARTS+=("LiteLLM database")
fi

# --- files ---------------------------------------------------------------------------------------
if [ "$FILES" = 1 ]; then
  log "stored files${FILES_PREFIX:+ under $FILES_PREFIX}"
  $PORTAL_CLI export-files --prefix "$FILES_PREFIX" > "$WORK/files.tar"
  tar -tf "$WORK/files.tar" > /dev/null || die "the file archive can't be read back"
  PARTS+=("stored files${FILES_PREFIX:+ under $FILES_PREFIX} ($(tar -tf "$WORK/files.tar" | grep -vc '/$' || true) files)")
fi

# --- config --------------------------------------------------------------------------------------
if [ "$CONFIG" = 1 ]; then
  present=()
  for p in "${CONFIG_PATHS[@]}"; do
    if [ -e "$p" ]; then present+=("$p"); else log "  (no $p, skipped)"; fi
  done
  [ ${#present[@]} -gt 0 ] || die "none of the config files exist"
  log "config: ${present[*]}"
  tar -czf "$WORK/config.tar.gz" "${present[@]}"
  if [ "$ENCRYPT" = 1 ]; then
    if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
      age -r "$BACKUP_AGE_RECIPIENT" -o "$WORK/config.tar.gz.age" "$WORK/config.tar.gz"
      PARTS+=("config, encrypted with age: ${present[*]}")
    else
      openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE \
        -in "$WORK/config.tar.gz" -out "$WORK/config.tar.gz.enc"
      PARTS+=("config, encrypted with openssl: ${present[*]}")
    fi
    rm -f "$WORK/config.tar.gz"
  else
    PARTS+=("config (plaintext, contains secrets): ${present[*]}")
  fi
fi

# --- manifest ------------------------------------------------------------------------------------
{
  echo "backup:    $NAME"
  echo "created:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host:      $(hostname)"
  echo "code:      $(git rev-parse --short HEAD 2>/dev/null || echo unknown) ($(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo ?))"
  echo "contents:"
  for p in "${PARTS[@]}"; do echo "  - $p"; done
} > "$WORK/MANIFEST.txt"
(cd "$WORK" && sha256 -- * > "$OUT/.SHA256SUMS.tmp" && mv "$OUT/.SHA256SUMS.tmp" SHA256SUMS)
chmod 600 "$WORK"/*

mv "$WORK" "$OUT/$NAME"
DONE="$OUT/$NAME"
log "done: $DONE ($(du -sh "$DONE" | cut -f1))"

# --- off-machine copy ----------------------------------------------------------------------------
if [ -n "$UPLOAD" ]; then
  log "uploading to $UPLOAD/$NAME/"
  portal_cli_with_dir "$DONE" upload-backup "$UPLOAD"
fi

# --- retention -----------------------------------------------------------------------------------
if [ "$KEEP" -gt 0 ]; then
  old="$(find "$OUT" -maxdepth 1 -type d -name 'backup-*' ! -name '*.partial' | sort -r | tail -n +"$((KEEP + 1))")"
  if [ -n "$old" ]; then
    echo "$old" | while IFS= read -r d; do log "removing old backup $(basename "$d")"; rm -rf "$d"; done
  fi
fi
echo "$DONE"
