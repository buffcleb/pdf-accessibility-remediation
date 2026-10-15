#!/usr/bin/env bash
# Build the service's three images for the cluster (linux/amd64) and push them to the registry.
#
#   scripts/build.sh                     build and push, tagged with the current commit
#   scripts/build.sh --no-push           build only (loads nothing; checks that everything builds)
#   scripts/build.sh --only web          just one image: engine, portal, web or music (repeatable)
#   scripts/build.sh --tag v1.2          use this tag instead of the commit
#
# Images: $REGISTRY/pdfrem-engine (the remediation engine: API and workers), $REGISTRY/pdfrem-portal (portal API
# and worker) and $REGISTRY/pdfrem-web (the web app). Each gets the tag and :latest. Each image's tag is written
# to deploy/k8s/.last-build (ENGINE_TAG, PORTAL_TAG, WEB_TAG) so scripts/deploy.sh deploys exactly what was built.
# A fourth, $REGISTRY/pdfrem-music (the music reading service, docker/audiveris), is built when deploy.env has
# MUSIC_READER=on, or when asked for with --only music (MUSIC_TAG).
#
# Options:
#   --tag TAG            image tag (default: the short commit, with -dirty if there are uncommitted changes)
#   --only NAME          engine | portal | web | music (repeatable; default the first three, and music when
#                        MUSIC_READER=on)
#   --no-push            build without pushing
#   --platform P         default linux/amd64 (the cluster's nodes)
#   --allow-dirty        build even with uncommitted changes (the tag says -dirty)
#   -h, --help
#
# Settings (environment, or deploy/k8s/deploy.env):
#   REGISTRY             where to push, e.g. ghcr.io/your-account (required)
#   GHCR_TOKEN           token with write:packages; default: `gh auth token` (run
#                        `gh auth refresh -s write:packages,read:packages` once if the push is refused)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
die() { echo "build: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

# shellcheck disable=SC1091
[ -f deploy/k8s/deploy.env ] && set -a && . deploy/k8s/deploy.env && set +a
REGISTRY="${REGISTRY:-}"
TAG="" PUSH=1 PLATFORM="linux/amd64" DIRTY_OK=0 ONLY=()

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?--tag needs a value}"; shift ;;
    --only) ONLY+=("${2:?--only needs engine, portal, web or music}"); shift ;;
    --no-push) PUSH=0 ;;
    --platform) PLATFORM="${2:?--platform needs a value}"; shift ;;
    --allow-dirty) DIRTY_OK=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done
if [ ${#ONLY[@]} -eq 0 ]; then
  ONLY=(engine portal web)
  [ "${MUSIC_READER:-off}" = on ] && ONLY+=(music)
fi
[ -n "$REGISTRY" ] || die "set REGISTRY (e.g. ghcr.io/your-account) in deploy/k8s/deploy.env or the environment"
for n in "${ONLY[@]}"; do case "$n" in engine|portal|web|music) ;; *) die "--only takes engine, portal, web or music" ;; esac; done

command -v docker >/dev/null || die "docker isn't installed"
docker buildx version >/dev/null 2>&1 || die "docker buildx isn't available"

DIRTY=""
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  [ "$DIRTY_OK" = 1 ] || die "there are uncommitted changes; commit them, or use --allow-dirty (tagged -dirty)"
  DIRTY="-dirty"
fi
TAG="${TAG:-$(git rev-parse --short=10 HEAD)$DIRTY}"

if [ "$PUSH" = 1 ]; then
  host="${REGISTRY%%/*}"
  user="${REGISTRY#*/}"; user="${user%%/*}"
  token="${GHCR_TOKEN:-}"
  if [ -z "$token" ] && command -v gh >/dev/null; then token="$(gh auth token 2>/dev/null || true)"; fi
  [ -n "$token" ] || die "no registry token: set GHCR_TOKEN or sign in with gh"
  log "signing in to $host as $user"
  echo "$token" | docker login "$host" -u "$user" --password-stdin >/dev/null \
    || die "sign-in to $host failed"
fi

dockerfile() {
  case "$1" in
    engine) echo Dockerfile ;; portal) echo Dockerfile.portal ;; web) echo Dockerfile.web ;;
    music) echo docker/audiveris/Dockerfile ;;
  esac
}

for n in "${ONLY[@]}"; do
  image="$REGISTRY/pdfrem-$n"
  log "building $image:$TAG ($PLATFORM)"
  args=(buildx build --platform "$PLATFORM" -f "$(dockerfile "$n")" -t "$image:$TAG" -t "$image:latest"
        --label "org.opencontainers.image.revision=$(git rev-parse HEAD)"
        --label "org.opencontainers.image.source=https://github.com/buffcleb/pdf-accessibility-remediation")
  if [ "$PUSH" = 1 ]; then args+=(--push); else args+=(--output type=cacheonly); fi
  if ! docker "${args[@]}" .; then
    [ "$PUSH" = 1 ] && log "if the push was refused: gh auth refresh -s write:packages,read:packages"
    die "building $n failed"
  fi
done

if [ "$PUSH" = 1 ]; then
  touch deploy/k8s/.last-build
  for n in "${ONLY[@]}"; do
    key="$(echo "$n" | tr '[:lower:]' '[:upper:]')_TAG"
    grep -v "^$key=" deploy/k8s/.last-build > deploy/k8s/.last-build.new || true
    echo "$key=$TAG" >> deploy/k8s/.last-build.new
    mv deploy/k8s/.last-build.new deploy/k8s/.last-build
  done
  log "pushed ${ONLY[*]} as $TAG; deploy with: scripts/deploy.sh"
else
  log "built ${ONLY[*]} as $TAG (not pushed)"
fi
