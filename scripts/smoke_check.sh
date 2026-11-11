#!/usr/bin/env bash
# Post-deployment smoke check: does the deployed revision actually work?
#
# 1. Waits for liveness, then requires /readyz to be ready and to report the
#    expected commit (scripts/release.py check-ready).
# 2. Runs the shared conformance suite against the service, which calls the
#    real provider: issues are created, read and deleted through the API.
#    Every title the suite creates starts with "Conformance", so leftovers
#    are easy to recognise in the provider.
#
# It exits non-zero if the service is not ready with that commit or if any
# conformance test fails. An issue the suite could not delete afterwards
# (a CleanupWarning) does not fail the check; it is listed prominently in the
# summary so it can be removed by hand.
#
# No provider credentials are needed here: the deployed service holds them.
#
# Usage: scripts/smoke_check.sh <base-url> <expected-commit>
#   SMOKE_READY_TIMEOUT  seconds to wait for readiness (default 300)
#   GITHUB_STEP_SUMMARY  when set, the results are appended to it
# Needs: uv.

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <base-url> <expected-commit>" >&2
  exit 2
fi
BASE_URL="${1%/}"
COMMIT="$2"

cd "$(git rev-parse --show-toplevel)"

summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    cat >>"$GITHUB_STEP_SUMMARY"
  else
    cat >/dev/null
  fi
}

echo "Checking ${BASE_URL} is ready and runs ${COMMIT}"
if ! uv run --locked python -m scripts.release check-ready \
  --url "$BASE_URL" --commit "$COMMIT" --timeout "${SMOKE_READY_TIMEOUT:-300}"; then
  summary <<EOF
## Smoke check: FAILED

The service at ${BASE_URL} is not ready with commit \`${COMMIT}\`; the conformance suite did not run.
EOF
  exit 1
fi

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

echo "Running the conformance suite against ${BASE_URL}"
set +e
CONFORMANCE_BASE_URL="$BASE_URL" uv run --locked pytest contract/tests/conformance -v -rf 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"
set -e

totals="$(tail -n 1 "$log" | sed -E 's/^=+ //; s/ =+$//')"
failed_tests="$(grep -E '^FAILED ' "$log" | sed -E 's/^FAILED ([^ ]+).*/\1/' | sort -u || true)"
leftovers="$(grep -oE 'could not delete issue [^ ]+ \(HTTP [0-9]+\)' "$log" \
  | sed -E 's/could not delete issue ([^ ]+) \(HTTP ([0-9]+)\)/\1 (HTTP \2)/' | sort -u || true)"

# A suite that skipped tests did not check them.
if [[ "$status" -eq 0 ]] && grep -Eq '[0-9]+ skipped' <<<"$totals"; then
  echo "Conformance tests were skipped; the smoke check must run the full suite." >&2
  status=1
fi

if [[ "$status" -eq 0 ]]; then
  result="passed"
else
  result="FAILED"
fi

{
  echo "## Smoke check: ${result}"
  echo
  echo "Conformance suite against ${BASE_URL} at commit \`${COMMIT}\`: ${totals}."
  if [[ -n "$failed_tests" ]]; then
    echo
    echo "Failing tests:"
    echo
    while read -r test; do echo "- \`${test}\`"; done <<<"$failed_tests"
  fi
  if [[ -n "$leftovers" ]]; then
    echo
    echo "## :warning: Leftover issues: remove by hand"
    echo
    echo "The smoke check could not delete these issues it created (titles start with \"Conformance\"):"
    echo
    while read -r issue; do echo "- \`${issue}\`"; done <<<"$leftovers"
  fi
} | summary

if [[ -n "$leftovers" ]]; then
  echo
  echo "Leftover issues to remove by hand:"
  echo "$leftovers"
fi

exit "$status"
