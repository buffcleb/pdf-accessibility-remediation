#!/usr/bin/env bash
# Publish a snapshot of this repository to the public repository, without its history.
#
#   scripts/publish.sh                   export main, check it, show what changed, ask, then commit and push
#   scripts/publish.sh --check           export and check only (nothing is committed or pushed)
#   scripts/publish.sh --ref v1.0        publish another branch, tag or commit
#
# The private repository stays the working repository. Each run exports the committed files of a ref (`git archive`:
# no history, nothing untracked or git-ignored), checks them, and copies them over a local clone of the public
# repository as one new commit, authored with your GitHub no-reply address. It refuses to publish if:
#   - gitleaks finds a secret (run in Docker);
#   - a file looks like a real settings or key file (*.env other than *.example, *.pem, *.key, .claude/…);
#   - the text has a private network address, or an email address outside the placeholder domains
#     (the reserved *.test, *.example, *.invalid and *.localhost; example.org, example.com, example.edu; GitHub
#     no-reply); RKE2's default pod range, 10.42.0.0/16, is allowed;
#   - the text matches a line of .publish-deny: your own private words (hostnames, names, …), one
#     case-insensitive extended regular expression per line. That file is git-ignored, so it is never published.
#   - a file repeats a sentence from a document in samples/regression (scripts/sample_text_check.py). The
#     documents are other people's; this runs where they and the .venv are, and says so where they aren't.
# Then it shows the files changed since the last snapshot and asks before committing and pushing.
#
# Options:
#   --ref REF            what to publish (default main; must be committed and pushed to the private repository)
#   --repo OWNER/NAME    the public repository (default buffcleb/pdf-accessibility-remediation, or $PUBLIC_REPO)
#   --dir PATH           its local clone (default: a sibling folder named after the repository; cloned if missing)
#   --message TEXT       the snapshot's commit message (default: "Snapshot YYYY-MM-DD")
#   --check              stop after the checks
#   -h, --help
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
die() { echo "publish: $*" >&2; exit 1; }
log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

REF="main" REPO="${PUBLIC_REPO:-buffcleb/pdf-accessibility-remediation}" DIR="" MESSAGE="" CHECK_ONLY=0
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.24.3"

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift ;;
    --repo) REPO="${2:?--repo needs a value}"; shift ;;
    --dir) DIR="${2:?--dir needs a value}"; shift ;;
    --message) MESSAGE="${2:?--message needs a value}"; shift ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done
[ -n "$DIR" ] || DIR="$(dirname "$ROOT")/${REPO##*/}"
MESSAGE="${MESSAGE:-Snapshot $(date +%Y-%m-%d)}"

for cmd in git docker rsync gh; do command -v "$cmd" >/dev/null || die "$cmd is needed"; done
COMMIT="$(git rev-parse --verify --quiet "$REF^{commit}")" || die "no such ref: $REF"
SHORT="$(git rev-parse --short "$COMMIT")"

# 1. Export the committed files only.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/publish.XXXXXX")"
RESTORE=""   # set to the public clone while it holds an uncommitted snapshot
cleanup() {
  rm -rf "$WORK"
  if [ -n "$RESTORE" ]; then
    git -C "$RESTORE" reset --quiet --hard && git -C "$RESTORE" clean -fdq
    log "not published; $RESTORE is back as it was"
  fi
}
trap cleanup EXIT
EXPORT="$WORK/export"
mkdir "$EXPORT"
git archive --format=tar "$COMMIT" | tar -x -C "$EXPORT"
log "exported $REF ($SHORT): $(find "$EXPORT" -type f | wc -l | tr -d ' ') files"

# 2. Checks.
PROBLEMS=0
problem() { echo "  ✗ $*" >&2; PROBLEMS=$((PROBLEMS + 1)); }

log "checking file names"
while IFS= read -r f; do
  problem "file that looks private: $f"
done < <(cd "$EXPORT" && find . -type f \( \
    \( -name '*.env' -o -name '.env' -o -name '.env.*' \) ! -name '*.example' -o \
    -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name 'id_rsa*' -o -name 'id_ed25519*' -o \
    -path './.claude/*' -o -path './backups/*' -o -path './samples/*' -o -name '.last-build' \
  \) | sed 's|^\./||')

log "checking for private network addresses"
# Each address found on its own. RKE2's and k3s's default pod range, 10.42.0.0/16, is the same on every cluster, so
# it says nothing about a network: the defaults and examples use it
while IFS= read -r hit; do
  problem "private address: $hit"
done < <(grep -rnoIE '(^|[^0-9.])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})([^0-9]|$)' \
  "$EXPORT" | sed "s|^$EXPORT/||" | grep -vE ':[0-9]+:[^0-9.]?10\.42\.[0-9]{1,3}\.[0-9]{1,3}[^0-9]?$' || true)

log "checking email addresses"
while IFS= read -r hit; do
  problem "email address outside the placeholder domains: $hit"
done < <(grep -rnoIiE '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}' "$EXPORT" | sed "s|^$EXPORT/||" \
  | grep -viE '@([a-z0-9-]+\.)*(test|example|invalid|localhost)$|@example\.(org|com|edu)$|@users\.noreply\.github\.com$|^LICENSE:|noreply@anthropic\.com$' || true)

if [ -f .publish-deny ]; then
  log "checking .publish-deny"
  while IFS= read -r pattern; do
    case "$pattern" in ''|'#'*) continue ;; esac
    while IFS= read -r hit; do
      problem "matches .publish-deny ($pattern): $hit"
    done < <(grep -rnIiE -- "$pattern" "$EXPORT" | sed "s|^$EXPORT/||" || true)
  done < .publish-deny
else
  log "no .publish-deny file: only the built-in checks run"
fi

# The sample documents are never exported; a sentence copied out of one into a test or a report would be.
if [ -d samples/regression ] && [ -x .venv/bin/python ]; then
  log "checking for text from the sample documents"
  while IFS= read -r hit; do
    problem "text from a sample document: $hit"
  done < <(.venv/bin/python scripts/sample_text_check.py "$EXPORT" samples/regression || true)
else
  log "no samples/regression or no .venv here: text from the sample documents is not checked"
fi

log "running gitleaks"
if ! docker run --rm -v "$EXPORT:/scan:ro" "$GITLEAKS_IMAGE" dir /scan --no-banner --redact >"$WORK/gitleaks.txt" 2>&1; then
  sed 's/^/    /' "$WORK/gitleaks.txt" >&2
  problem "gitleaks found something (above)"
fi

[ "$PROBLEMS" -eq 0 ] || die "$PROBLEMS problem(s): nothing published. Fix them in this repository and merge, then rerun."
log "all checks passed"
[ "$CHECK_ONLY" -eq 0 ] || exit 0

# 3. The public repository's local clone.
if [ ! -d "$DIR/.git" ]; then
  gh repo view "$REPO" >/dev/null 2>&1 || die "$REPO doesn't exist yet. Create it (empty: no README or license) with:
  gh repo create $REPO --public --description \"Turns inaccessible PDFs into tagged, screen-reader-ready documents\"
then rerun."
  log "cloning $REPO into $DIR"
  gh repo clone "$REPO" "$DIR" -- --quiet
fi
cd "$DIR"
[ -z "$(git status --porcelain)" ] || die "$DIR has uncommitted changes (an earlier run stopped part way?). If they're
only a snapshot, clear them with: git -C \"$DIR\" reset --hard && git -C \"$DIR\" clean -fd"
if git rev-parse --verify --quiet HEAD >/dev/null; then
  git pull --quiet --ff-only || die "couldn't update $DIR from $REPO"
else
  git symbolic-ref HEAD refs/heads/main   # a new, empty repository: the first snapshot goes on main
fi
BRANCH="$(git symbolic-ref --short HEAD)"

# 4. Replace its contents with the snapshot and show the change. Until it's committed, any exit puts the clone back.
RESTORE="$DIR"
rsync -a --delete --exclude .git "$EXPORT/" "$DIR/"
git add -A
if git diff --cached --quiet; then
  log "nothing changed since the last snapshot"
  exit 0
fi
echo
echo "Changes since the last snapshot of $REPO:"
git diff --cached --stat | tail -40
echo

LOGIN="$(gh api user --jq .login)" ID="$(gh api user --jq .id)"
AUTHOR="$LOGIN <$ID+$LOGIN@users.noreply.github.com>"
echo "Commit \"$MESSAGE\" (from $SHORT) as $AUTHOR and push to $REPO ($BRANCH)?"
answer=""
{ printf 'Type yes to publish: ' >/dev/tty && read -r answer </dev/tty; } 2>/dev/null \
  || die "couldn't ask for confirmation: run this in a terminal you can type in"
[ "$answer" = "yes" ] || exit 1

git -c user.name="$LOGIN" -c user.email="$ID+$LOGIN@users.noreply.github.com" \
  commit --quiet -m "$MESSAGE" -m "Source: $SHORT"
RESTORE=""
git push --quiet -u origin "$BRANCH" || die "committed in $DIR but the push failed; push it with: git -C \"$DIR\" push -u origin $BRANCH"
log "published: https://github.com/$REPO"
