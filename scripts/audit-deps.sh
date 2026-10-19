#!/usr/bin/env bash
# Known vulnerabilities in the service's dependencies: the Python packages the three requirements files resolve to
# (pip-audit) and the web app's npm packages (npm audit, high and above). Run by CI when a dependency changes and
# every week (.github/workflows/audit.yml, #362); runs locally the same way, given pip-audit and npm.
#
#   scripts/audit-deps.sh            both
#   scripts/audit-deps.sh python     or just one: python | npm
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
what="${1:-all}"
case "$what" in all|python|npm) ;; *) echo "audit-deps: python, npm or nothing (both)" >&2; exit 2 ;; esac

if [ "$what" != npm ]; then
  # One advisory is left out on purpose: PYSEC-2026-3412 (CVE-2026-49452) is CSS injection through presentational
  # attributes of untrusted HTML, and the only HTML WeasyPrint sees here is the report the engine builds itself from
  # escaped text (app/reports/pdf.py); no fix version exists. Remove the exception when one does.
  pip-audit -r requirements.txt -r portal/requirements.txt -r requirements-dev.txt --progress-spinner off \
    --ignore-vuln PYSEC-2026-3412 --ignore-vuln CVE-2026-49452
fi
if [ "$what" != python ]; then
  (cd web && npm audit --audit-level=high)
fi
