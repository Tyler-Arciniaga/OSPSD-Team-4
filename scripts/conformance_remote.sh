#!/usr/bin/env bash
# Run the shared conformance suite against a deployed service.
#
# The suite creates and deletes real issues through the service, so the
# deployment must point at a dedicated test list (Trello) or test team
# (Linear). It runs from this machine; no credentials are needed here,
# because the deployed service holds them.
#
# Usage: scripts/conformance_remote.sh https://<service>.onrender.com
# Needs: curl, uv.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <base-url>   e.g. $0 https://ospsd-team4-issue-tracker.onrender.com" >&2
  exit 2
fi
BASE_URL="${1%/}"

cd "$(git rev-parse --show-toplevel)"

# A free Render instance sleeps after 15 minutes idle and takes about a
# minute to wake. Wait for liveness before testing, so the first test does
# not time out on the wake-up.
echo "Waiting for ${BASE_URL}/healthz (cold starts take about a minute)"
deadline=$((SECONDS + 180))
until curl -fsS --max-time 30 "${BASE_URL}/healthz" >/dev/null 2>&1; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "The service did not answer /healthz within 3 minutes." >&2
    exit 1
  fi
  sleep 5
done

# Record which build and provider state the results belong to.
echo "Readiness: $(curl -sS --max-time 30 "${BASE_URL}/readyz")"
echo

CONFORMANCE_BASE_URL="$BASE_URL" uv run --locked pytest contract/tests/conformance -v
