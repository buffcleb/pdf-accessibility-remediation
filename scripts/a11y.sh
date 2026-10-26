#!/usr/bin/env bash
# Accessibility checks of the web app (#369): axe-core in a headless browser over the main pages, as a person, a
# group admin, a service admin and a super admin, in light and dark, against WCAG 2.2 A and AA. The pages show
# invented people and documents (tests/a11y/serve.py) on the portal test suite's database.
#
#   scripts/a11y.sh                    every page
#   A11Y_ONLY=admin scripts/a11y.sh    the pages whose name contains "admin"
#
# Then, with every page checked (no A11Y_ONLY), that a new build loads on the next click (web/a11y/newBuild.mjs, #575).
#
# Needs Python with the engine's and portal's requirements (the .venv), Node with web/'s packages (npm ci), and a
# browser for Playwright (npx --prefix web playwright install chromium; or A11Y_CHANNEL=chrome to use Chrome).
# The database: PORTAL_TEST_DATABASE_URL if set (it's emptied), otherwise a throwaway Postgres container, removed
# afterwards. Exits non-zero when axe finds a problem; web/a11y/report.json has the details. CI runs it only when
# started by hand (the "Accessibility checks" workflow), to save minutes.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
API_PORT="${A11Y_API_PORT:-8092}"
WEB_PORT="${A11Y_WEB_PORT:-4174}"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
WORK="$(mktemp -d)"
PIDS=()
CONTAINER=""

log() { echo "[a11y] $*" >&2; }
cleanup() {
  for p in "${PIDS[@]+"${PIDS[@]}"}"; do kill "$p" 2>/dev/null || true; done
  if [ -n "$CONTAINER" ]; then docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

wait_for() {  # url, what, pid
  for _ in $(seq 1 120); do
    if curl -sf -o /dev/null "$1"; then return 0; fi
    if ! kill -0 "$3" 2>/dev/null; then log "$2 stopped; its output:"; cat "$WORK/$2.log" >&2; exit 1; fi
    sleep 1
  done
  log "$2 didn't start"; cat "$WORK/$2.log" >&2; exit 1
}

if [ -z "${PORTAL_TEST_DATABASE_URL:-}" ]; then
  CONTAINER="pdfrem-a11y-pg-$$"
  log "starting a throwaway Postgres ($CONTAINER)"
  docker run -d --rm --name "$CONTAINER" -e POSTGRES_USER=portal -e POSTGRES_PASSWORD=test -e POSTGRES_DB=portal \
    -p 127.0.0.1::5432 postgres:16-alpine >/dev/null
  port="$(docker port "$CONTAINER" 5432/tcp | head -1 | sed 's/.*://')"
  export PORTAL_TEST_DATABASE_URL="postgresql+psycopg://portal:test@127.0.0.1:$port/portal"
  for _ in $(seq 1 30); do
    if docker exec "$CONTAINER" pg_isready -U portal >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

log "building the web app"
(cd web && npm run build >"$WORK/build.log" 2>&1) || { cat "$WORK/build.log" >&2; exit 1; }

log "starting the portal API with invented data (port $API_PORT)"
A11Y_SEED_OUT="$WORK/seed.json" "$PY" -m tests.a11y.serve "$API_PORT" "http://localhost:$WEB_PORT" >"$WORK/api.log" 2>&1 &
api_pid=$!
PIDS+=("$api_pid")
wait_for "http://127.0.0.1:$API_PORT/api/v1/live" api "$api_pid"

log "serving the built app (port $WEB_PORT)"
(cd web && PORTAL_API="http://127.0.0.1:$API_PORT" exec node node_modules/vite/bin/vite.js preview --port "$WEB_PORT" \
  --strictPort >"$WORK/web.log" 2>&1) &
web_pid=$!
PIDS+=("$web_pid")
wait_for "http://localhost:$WEB_PORT/" web "$web_pid"

log "checking the pages"
(cd web && A11Y_SEED="$WORK/seed.json" A11Y_BASE="http://localhost:$WEB_PORT" node a11y/axe.mjs)

# Not accessibility, but the same app and data: after a deploy, the next click loads the new build (#575)
if [ -z "${A11Y_ONLY:-}" ]; then
  log "checking the new-build reload"
  (cd web && A11Y_SEED="$WORK/seed.json" A11Y_BASE="http://localhost:$WEB_PORT" node a11y/newBuild.mjs)
fi
