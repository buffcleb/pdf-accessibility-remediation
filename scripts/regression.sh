#!/usr/bin/env bash
# Run the regression documents (samples/regression, local and git-ignored) through the code as it stands.
#
#   scripts/regression.sh                the quick set; every fifth run, the full set
#   scripts/regression.sh --quick        the quick set, whatever the count
#   scripts/regression.sh --full         the full set
#   scripts/regression.sh --provider gateway --model open-small-vision    with real AI (the quick set unless --full)
#
# The quick set (samples/regression/quick) has long documents cut down to the pages that caused trouble; the full
# set (samples/regression/full) has each document whole and catches what a cut-down copy can't. Runs are counted
# in samples/regression/.runs, and only runs with the mock AI count towards "every fifth": a run that costs money
# is never made longer than asked.
#
# It runs in the engine's image with this working tree's app/ and harness/ mounted, so it tests your changes
# without rebuilding or touching the running stack.
#
# Options:
#   --quick | --full     choose the set
#   --provider NAME      AI provider (default mock: no AI calls, no cost)
#   --model NAME         model, for providers that take one
#   --every N            how often the full set runs by itself (default 5; 0: never)
#   -h, --help
#
# Settings (environment):
#   REGRESSION_IMAGE     the image to run in (default buffclebpdfremediation-worker, built by docker compose)
#   REGRESSION_ENV_FILE  an env file with AI keys for real providers (default .env, if there is one)
#   REGRESSION_SAMPLES   the folder holding quick/ and full/ (default samples/regression here). The set is
#                        git-ignored, so a worktree has none: point this at the checkout that has it
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
die() { echo "regression: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

SET="" PROVIDER="mock" MODEL="" EVERY=5
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) SET=quick ;;
    --full) SET=full ;;
    --provider) PROVIDER="${2:?--provider needs a name}"; shift ;;
    --model) MODEL="${2:?--model needs a name}"; shift ;;
    --every) EVERY="${2:?--every needs a number}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done
case "$EVERY" in ''|*[!0-9]*) die "--every takes a whole number" ;; esac

DIR="${REGRESSION_SAMPLES:-$ROOT/samples/regression}"
if [ ! -d "$DIR/quick" ] || [ ! -d "$DIR/full" ]; then
  die "no $DIR/quick and $DIR/full (see samples/regression/NOTES.md; from a worktree, set REGRESSION_SAMPLES)"
fi
command -v docker >/dev/null || die "docker isn't installed"
IMAGE="${REGRESSION_IMAGE:-buffclebpdfremediation-worker}"
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "no image $IMAGE: run 'docker compose build worker' (or set REGRESSION_IMAGE)"

COUNT_FILE="$DIR/.runs"
count=0
[ -f "$COUNT_FILE" ] && count="$(tr -dc '0-9' < "$COUNT_FILE")"
count="${count:-0}"
why=""
if [ -z "$SET" ]; then
  SET=quick
  if [ "$PROVIDER" = mock ]; then
    count=$((count + 1))
    echo "$count" > "$COUNT_FILE"
    if [ "$EVERY" -gt 0 ] && [ $((count % EVERY)) -eq 0 ]; then
      SET=full
      why=" (run $count: the full set runs once every $EVERY)"
    else
      why=" (run $count)"
    fi
  fi
fi

args=(run --rm -w /srv -e PYTHONPATH=/srv -v "$ROOT/app:/srv/app:ro" -v "$ROOT/harness:/srv/harness:ro"
      -v "$DIR:/srv/samples/regression:ro")
if [ "$PROVIDER" = mock ]; then
  args+=(-e ALLOW_MOCK_PROVIDER=true)
else
  env_file="${REGRESSION_ENV_FILE:-.env}"
  [ -f "$env_file" ] && args+=(--env-file "$env_file")
fi
args+=(--entrypoint python "$IMAGE" harness/run_folder.py "/srv/samples/regression/$SET" --provider "$PROVIDER")
[ -n "$MODEL" ] && args+=(--model "$MODEL")

echo "regression: the $SET set with $PROVIDER AI$why"
# (two notices PyMuPDF prints on every run are left out; the exit status is the run's)
docker "${args[@]}" 2>&1 | grep -v "fitz. API is deprecated\|pymupdf_layout"
