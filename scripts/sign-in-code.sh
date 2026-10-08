#!/usr/bin/env bash
# Issue a sign-in code for someone and print it, for when email isn't arriving.
#
#   scripts/sign-in-code.sh person@example.edu --k8s --reason "mail outage"
#   scripts/sign-in-code.sh person@example.edu              (the local Compose stack)
#
# It makes a new code the same way "Email me a code" does (the address must be allowed to sign in and the account
# not disabled), but prints it instead of emailing it. The code works once, for the usual minutes, and replaces any
# code sent before. The portal stores only a hash, so an emailed code can't be read back; this issues a fresh one.
# Each use is recorded in the audit log (login.code_issued, with who ran it and why), never the code itself.
# Pass the code on privately, ideally by phone or in person.
#
# Options:
#   --k8s            the Kubernetes deployment (deploy/k8s/deploy.env's cluster and namespace; or K8S_NAMESPACE)
#   --reason TEXT    why (recorded in the audit log)
#   -h, --help
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
die() { echo "sign-in-code: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -Eeuo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

EMAIL="" REASON="" K8S=0
while [ $# -gt 0 ]; do
  case "$1" in
    --k8s) K8S=1 ;;
    --reason) REASON="${2:?--reason needs text}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) [ -z "$EMAIL" ] || die "one address at a time"; EMAIL="$1" ;;
  esac
  shift
done
[ -n "$EMAIL" ] || die "give an email address (see --help)"
[ -n "${K8S_NAMESPACE:-}" ] && K8S=1
OPERATOR="$(id -un)@$(hostname -s)"

if [ "$K8S" = 1 ]; then
  if [ -f deploy/k8s/deploy.env ]; then
    # shellcheck disable=SC1091
    eval "$(set -a; . deploy/k8s/deploy.env; set +a; printf 'KCFG=%q\nNS=%q\nCTX=%q\n' \
      "${KUBECONFIG_PATH:-}" "${NAMESPACE:-}" "${KUBE_CONTEXT:-}")"
    [ -n "$KCFG" ] && export KUBECONFIG="${KUBECONFIG:-$KCFG}"
  fi
  NS="${K8S_NAMESPACE:-${NS:-pdf-remediation}}"
  args=(kubectl -n "$NS")
  [ -n "${CTX:-}" ] && args+=(--context "$CTX")
  args+=(exec deployment/portal-api --)
else
  args=(docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal exec -T portal-api)
fi
"${args[@]}" python -m portal.cli sign-in-code "$EMAIL" --operator "$OPERATOR" --reason "$REASON"
