#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh.
#
#   scripts/restore.sh --from backups/backup-20261007-020000 --db --dry-run
#   scripts/restore.sh --from backups/backup-20261007-020000 --db --files
#   scripts/restore.sh --from s3://my-backups/pdf-remediation/backup-20261007-020000 --all
#
# Nothing is restored unless you name it. Checksums are verified first.
#
# Options:
#   --from DIR|URL       a backup folder, or s3://bucket/prefix/backup-... (downloaded into
#                        ./backups/restore-<name>/ first, using the same BACKUP_S3_* settings as backups)
#   --db                 the portal database. Replaces the current one. Before replacing it, the current
#                        database is dumped to ./backups/pre-restore-<time>/, and portal-api and
#                        portal-worker are stopped (and started again afterwards, which applies any newer
#                        migrations)
#   --litellm            the LiteLLM database (stops and restarts litellm)
#   --files              stored files. Files that already exist are kept unless --overwrite-files
#   --files-prefix P     only files under P
#   --overwrite-files
#   --config             unpack the config files into ./backups/restored-config-<time>/ to compare and copy
#                        by hand
#   --config-in-place    put the config files back where they were instead (current ones are first copied
#                        to <file>.pre-restore-<time>)
#   --all                --db --litellm --files --config
#   --sql                instead of restoring --db (and/or --litellm), write each dump as plain SQL into
#                        ./backups/sql-<backup>-<time>/ to read and copy from by hand. Nothing live is touched
#   --into-scratch       instead of replacing the portal database, restore the backup into a separate database,
#                        portal_restore (replaced if it exists), beside the live one, to query and copy from
#   --table NAME         with --sql or --into-scratch: only this table (repeatable)
#   --data-only          with --sql or --into-scratch: the rows only, no schema
#   --no-stop            don't stop and start services around database restores
#   --k8s                restore into the Kubernetes deployment (deploy/k8s/deploy.env's cluster and namespace;
#                        or set K8S_NAMESPACE). Services are scaled to 0 and back instead of stopped
#   --dry-run            show what would happen; change nothing
#   -y, --yes            don't ask for confirmation
#   -h, --help
#
# Encrypted config: age needs BACKUP_AGE_IDENTITY (the key file); openssl needs BACKUP_PASSPHRASE.
# PORTAL_COMPOSE, ENGINE_COMPOSE, PORTAL_DB_EXEC, LITELLM_DB_EXEC and PORTAL_CLI work as in backup.sh.
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

FROM="" DB=0 LITELLM=0 FILES=0 CONFIG=0 IN_PLACE=0 STOP=1 DRY=0 YES=0 OVERWRITE=0 FILES_PREFIX=""
SQL=0 SCRATCH=0 DATA_ONLY=0 TABLES=()
STAMP="$(date +%Y%m%d-%H%M%S)"

usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "restore: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="${2:?--from needs a folder or s3:// URL}"; shift ;;
    --db) DB=1 ;;
    --litellm) LITELLM=1 ;;
    --files) FILES=1 ;;
    --files-prefix) FILES=1; FILES_PREFIX="${2:?--files-prefix needs a prefix}"; shift ;;
    --overwrite-files) OVERWRITE=1 ;;
    --config) CONFIG=1 ;;
    --config-in-place) CONFIG=1; IN_PLACE=1 ;;
    --all) DB=1; LITELLM=1; FILES=1; CONFIG=1 ;;
    --sql) SQL=1 ;;
    --into-scratch) SCRATCH=1 ;;
    --table) TABLES+=("${2:?--table needs a table name}"); shift ;;
    --data-only) DATA_ONLY=1 ;;
    --no-stop) STOP=0 ;;
    --dry-run) DRY=1 ;;
    -y|--yes) YES=1 ;;
    --k8s) ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

[ -n "$FROM" ] || die "--from is required (see --help)"
if [ "$SQL" = 1 ] || [ "$SCRATCH" = 1 ]; then
  [ "$SQL" = 0 ] || [ "$SCRATCH" = 0 ] || die "--sql and --into-scratch are separate: pick one"
  [ $((DB + LITELLM)) -gt 0 ] || DB=1  # the portal database unless told otherwise
  [ "$SCRATCH" = 0 ] || [ "$LITELLM" = 0 ] || die "--into-scratch is for the portal database"
  [ $((FILES + CONFIG)) = 0 ] || die "--sql and --into-scratch are for the databases only"
else
  if [ ${#TABLES[@]} != 0 ] || [ "$DATA_ONLY" != 0 ]; then
    die "--table and --data-only go with --sql or --into-scratch"
  fi
fi
[ $((DB + LITELLM + FILES + CONFIG)) -gt 0 ] || die "name what to restore: --db, --litellm, --files, --config or --all"

sha256_check() { if command -v sha256sum >/dev/null; then sha256sum -c --quiet SHA256SUMS; else shasum -a 256 -c --quiet SHA256SUMS; fi; }
run() { if [ "$DRY" = 1 ]; then echo "  would run: $*" >&2; else "$@"; fi; }

umask 077
mkdir -p "$ROOT/backups"

# --- fetch -------------------------------------------------------------------------------------
if [[ "$FROM" == s3://* ]]; then
  [ "$K8S_MODE" = 0 ] || die "with --k8s, download the backup first (scripts/restore.sh --from s3://... --config without --k8s), then restore from the folder"
  SRC="$ROOT/backups/restore-$(basename "$FROM")"
  log "downloading $FROM to $SRC"
  mkdir -p "$SRC"
  if [ "$PORTAL_CLI" = "$PORTAL_CLI_DEFAULT" ]; then
    $PORTAL_COMPOSE run --rm --no-deps -T -u "$(id -u):$(id -g)" -v "$SRC:/restore" \
      -e BACKUP_S3_ACCESS_KEY -e BACKUP_S3_SECRET_KEY -e BACKUP_S3_ENDPOINT_URL -e BACKUP_S3_REGION \
      -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_DEFAULT_REGION \
      portal-api python -m portal.cli download-backup "$FROM" /restore
  else
    $PORTAL_CLI download-backup "$FROM" "$SRC"
  fi
else
  SRC="$(cd "$FROM" && pwd)" || die "no such folder: $FROM"
fi

[ -f "$SRC/SHA256SUMS" ] || die "$SRC isn't a backup (no SHA256SUMS)"
(cd "$SRC" && sha256_check) || die "checksums don't match: the backup is damaged or was changed"
log "checksums OK"
sed 's/^/  /' "$SRC/MANIFEST.txt" >&2

need() { [ -f "$SRC/$1" ] || die "this backup has no $1 ($2)"; }
[ "$DB" = 0 ] || need portal.dump "portal database"
[ "$LITELLM" = 0 ] || need litellm.dump "LiteLLM database"
[ "$FILES" = 0 ] || need files.tar "stored files"
CONFIG_ARCHIVE=""
if [ "$CONFIG" = 1 ]; then
  for c in config.tar.gz config.tar.gz.age config.tar.gz.enc; do [ -f "$SRC/$c" ] && CONFIG_ARCHIVE="$c"; done
  [ -n "$CONFIG_ARCHIVE" ] || die "this backup has no config"
fi

# --- confirm -----------------------------------------------------------------------------------
plan=()
if [ "$SQL" = 1 ]; then
  if [ $((DB + LITELLM)) = 2 ]; then which="portal and LiteLLM databases"
  elif [ "$DB" = 1 ]; then which="portal database"
  else which="LiteLLM database"; fi
  plan+=("write the $which as SQL${TABLES[*]:+ (${TABLES[*]})} into backups/ (nothing live changes)")
elif [ "$SCRATCH" = 1 ]; then
  plan+=("restore the portal database into the scratch database portal_restore${TABLES[*]:+ (${TABLES[*]})}, replacing it if it exists (the live one is untouched)")
else
  [ "$DB" = 0 ] || plan+=("REPLACE the portal database (current one saved first)")
  [ "$LITELLM" = 0 ] || plan+=("REPLACE the LiteLLM database (current one saved first)")
fi
[ "$FILES" = 0 ] || plan+=("restore stored files${FILES_PREFIX:+ under $FILES_PREFIX} ($([ "$OVERWRITE" = 1 ] && echo "overwriting existing" || echo "keeping existing"))")
[ "$CONFIG" = 0 ] || plan+=("$([ "$IN_PLACE" = 1 ] && echo "put config files back in place (current ones kept as *.pre-restore-$STAMP)" || echo "unpack config into backups/restored-config-$STAMP/")")
echo "Restore from $SRC:" >&2
for p in "${plan[@]}"; do echo "  - $p" >&2; done
if [ "$DRY" = 0 ] && [ "$YES" = 0 ]; then
  read -r -p "Type 'restore' to continue: " answer
  [ "$answer" = "restore" ] || die "canceled"
fi

PRE="$ROOT/backups/pre-restore-$STAMP"
restarted=()
plain=""
on_exit() {
  local s
  [ -z "$plain" ] || rm -f "$plain"
  for s in "${restarted[@]+"${restarted[@]}"}"; do svc start "$s"; done
}
# Stop or start a service: Compose, or scale the deployment on the cluster
svc() {
  local action="$1" s="$2"
  if [ "$K8S_MODE" = 1 ]; then
    $KUBECTL scale "deployment/$s" --replicas="$([ "$action" = start ] && echo 1 || echo 0)" >/dev/null 2>&1 \
      || log "couldn't $action $s"
    [ "$action" = stop ] && { $KUBECTL rollout status "deployment/$s" --timeout=120s >/dev/null 2>&1 || true; }
    return 0
  fi
  case "$s" in
    litellm) $ENGINE_COMPOSE "$action" litellm >/dev/null 2>&1 || log "couldn't $action litellm" ;;
    *) $PORTAL_COMPOSE "$action" "$s" >/dev/null 2>&1 || log "couldn't $action $s" ;;
  esac
}
trap on_exit EXIT

# --- databases ---------------------------------------------------------------------------------
restore_db() {  # exec-command, user, db, dump file, label
  local exec="$1" user="$2" db="$3" dump="$4" label="$5"
  if [ "$DRY" = 1 ]; then
    echo "  would save the current $label to $PRE/ and restore $dump" >&2
    $exec pg_restore --list < "$SRC/$dump" > /dev/null && log "$dump reads back OK"
    return
  fi
  mkdir -p "$PRE"
  log "saving the current $label to $PRE/$dump"
  $exec pg_dump -U "$user" -d "$db" -Fc > "$PRE/$dump"
  log "restoring the $label"
  $exec pg_restore -U "$user" -d "$db" --clean --if-exists --no-owner --single-transaction < "$SRC/$dump"
}

# Only some of a dump: the tables asked for, the rows without the schema (a plain loop: macOS's bash 3.2 has no mapfile)
parts=()
for t in "${TABLES[@]+"${TABLES[@]}"}"; do parts+=(-t "$t"); done
[ "$DATA_ONLY" = 0 ] || parts+=(--data-only)

if [ "$SQL" = 1 ]; then
  dest="$ROOT/backups/sql-$(basename "$SRC")-$STAMP"
  run mkdir -p "$dest"
  for pair in "$DB:portal.dump:$PORTAL_DB_EXEC" "$LITELLM:litellm.dump:$LITELLM_DB_EXEC"; do
    [ "${pair%%:*}" = 1 ] || continue
    rest="${pair#*:}"; dump="${rest%%:*}"; exec="${rest#*:}"
    log "writing $dest/${dump%.dump}.sql${TABLES[*]:+ (${TABLES[*]})}"
    if [ "$DRY" = 0 ]; then
      $exec pg_restore --no-owner "${parts[@]+"${parts[@]}"}" -f - < "$SRC/$dump" > "$dest/${dump%.dump}.sql"
    fi
  done
  [ "$DRY" = 1 ] || log "SQL written to $dest (nothing live was changed)"
  exit 0
fi

if [ "$SCRATCH" = 1 ]; then
  log "restoring into the scratch database portal_restore (the live portal database is untouched)"
  run $PORTAL_DB_EXEC psql -U postgres -q -c "drop database if exists portal_restore" -c "create database portal_restore owner portal"
  if [ "$DRY" = 0 ]; then
    $PORTAL_DB_EXEC pg_restore -U portal -d portal_restore --no-owner "${parts[@]+"${parts[@]}"}" < "$SRC/portal.dump"
    log "done: connect with psql -U portal -d portal_restore (inside the database's container or pod); drop it with"
    log "  psql -U postgres -c 'drop database portal_restore' when finished"
  fi
  exit 0
fi

if [ "$DB" = 1 ]; then
  if [ "$STOP" = 1 ] && [ "$DRY" = 0 ]; then
    log "stopping portal-worker and portal-api"
    svc stop portal-worker; svc stop portal-api
    restarted+=(portal-api portal-worker)
  fi
  restore_db "$PORTAL_DB_EXEC" portal portal portal.dump "portal database"
fi
if [ "$LITELLM" = 1 ]; then
  if [ "$STOP" = 1 ] && [ "$DRY" = 0 ]; then
    log "stopping litellm"
    svc stop litellm
    restarted+=(litellm)
  fi
  restore_db "$LITELLM_DB_EXEC" litellm litellm litellm.dump "LiteLLM database"
fi

# --- files -------------------------------------------------------------------------------------
if [ "$FILES" = 1 ]; then
  log "stored files"
  args=(import-files --prefix "$FILES_PREFIX")
  [ "$OVERWRITE" = 0 ] || args+=(--overwrite)
  [ "$DRY" = 0 ] || args+=(--dry-run)
  $PORTAL_CLI "${args[@]}" < "$SRC/files.tar"
fi

# --- config ------------------------------------------------------------------------------------
if [ "$CONFIG" = 1 ]; then
  plain="$(mktemp "${TMPDIR:-/tmp}/config.XXXXXX")"
  case "$CONFIG_ARCHIVE" in
    *.age)
      [ -n "${BACKUP_AGE_IDENTITY:-}" ] || die "the config is encrypted with age: set BACKUP_AGE_IDENTITY"
      age -d -i "$BACKUP_AGE_IDENTITY" -o "$plain" "$SRC/$CONFIG_ARCHIVE" ;;
    *.enc)
      [ -n "${BACKUP_PASSPHRASE:-}" ] || die "the config is encrypted: set BACKUP_PASSPHRASE"
      openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE \
        -in "$SRC/$CONFIG_ARCHIVE" -out "$plain" || die "couldn't decrypt the config (wrong passphrase?)" ;;
    *) cp "$SRC/$CONFIG_ARCHIVE" "$plain" ;;
  esac
  # Never write outside the target folder
  if tar -tzf "$plain" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then rm -f "$plain"; die "the config archive has unsafe paths"; fi
  if [ "$IN_PLACE" = 1 ]; then
    tar -tzf "$plain" | grep -v '/$' | while IFS= read -r f; do
      if [ -e "$f" ]; then run cp -p "$f" "$f.pre-restore-$STAMP"; fi
    done
    run tar -xzf "$plain" -C "$ROOT"
    [ "$DRY" = 1 ] || log "config restored in place; restart the stacks to use it"
  else
    dest="$ROOT/backups/restored-config-$STAMP"
    run mkdir -p "$dest"
    run tar -xzf "$plain" -C "$dest"
    [ "$DRY" = 1 ] || log "config unpacked into $dest"
  fi
  rm -f "$plain"
fi

if [ "$DRY" = 1 ]; then log "dry run: nothing was changed"; else log "restore finished"; fi
