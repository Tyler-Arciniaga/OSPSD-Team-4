#!/usr/bin/env bash
# Disposable test environment for the product service image: run it with
# fake configuration, check it, then remove everything. Modeled on the HW2
# service's scripts/disposable_env.sh.
#
# By default it builds the image from product/ at the current commit. With
# IMAGE_REF set to a pushed image pinned by digest, it pulls and checks
# exactly that image instead; the product release workflow does this before
# deploying it.
#
# Checks, with no real credentials and no network calls to any provider:
#   - the image records the commit, runs as non-root, and holds no .env,
#     .git, Terraform or credential files;
#   - bad configuration stops startup, naming the variable and printing no
#     configured value: nothing set; the real partner providers without the
#     calendar token file; the real providers with a placeholder token file
#     but no Dropbox credentials (this also proves the tracker, Team 5 and
#     Team 3 packages import and build inside the image);
#   - with the in-memory providers (selected through the same variables),
#     /healthz and /readyz answer, /readyz reports the commit, and
#     POST /v1/focus-day completes all five steps.
#
# Usage: product/scripts/disposable_env.sh       (from a clean checkout)
#        IMAGE_REF=<repo>@sha256:<digest> EXPECTED_COMMIT=<sha> product/scripts/disposable_env.sh
# Needs: docker, curl, git.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [[ -n "${IMAGE_REF:-}" ]]; then
  if [[ ! "$IMAGE_REF" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "IMAGE_REF must be pinned by digest (<repo>@sha256:<64 hex>), got '${IMAGE_REF}'." >&2
    exit 2
  fi
  COMMIT="${EXPECTED_COMMIT:-$(git rev-parse HEAD)}"
  IMAGE="$IMAGE_REF"
else
  COMMIT="$(git rev-parse HEAD)"
  IMAGE="ospsd-team4-product-disposable:${COMMIT:0:12}"
fi
CONTAINER="ospsd-team4-product-disposable-$$"
PORT="${DISPOSABLE_PORT:-18100}"

FAKES=(
  -e TRACKER_PROVIDER=ospsd_team4_product.fakes:InMemoryIssueProvider
  -e CALENDAR_PROVIDER=ospsd_team4_product.fakes:InMemoryCalendarService
  -e STORAGE_PROVIDER=ospsd_team4_product.fakes:InMemoryDocumentStorage
)
REAL=(
  -e TRACKER_PROVIDER=team_4_issue_tracker_trello:TrelloIssueProvider
  -e CALENDAR_PROVIDER=team5_calendar:create_calendar_service
  -e STORAGE_PROVIDER=dropbox_client_impl:DropboxDocumentStorage
)
# Fake values. The checks below prove none of them is ever printed.
FAKE_TRELLO_KEY="fake-disposable-trello-key-0000"
FAKE_DROPBOX_KEY="fake-disposable-dropbox-key-0000"

TOKEN_DIR="$(mktemp -d)"
printf '{"token": "fake-disposable-google-token-0000"}\n' >"${TOKEN_DIR}/token.json"
chmod 755 "$TOKEN_DIR"
chmod 644 "${TOKEN_DIR}/token.json"

failures=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() {
  printf '  FAIL  %s\n' "$1"
  failures=$((failures + 1))
}

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$TOKEN_DIR"
  if [[ "${KEEP_IMAGE:-0}" != "1" ]]; then
    docker rmi -f "$IMAGE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -n "${IMAGE_REF:-}" ]]; then
  echo "Pulling ${IMAGE}"
  docker pull --quiet "$IMAGE" >/dev/null
else
  if [[ -n "$(git status --porcelain --untracked-files=no -- product)" ]]; then
    echo "note: tracked files in product/ have uncommitted changes; the image"
    echo "      includes them, so it is not exactly commit ${COMMIT}."
  fi
  echo "Building ${IMAGE} from ${COMMIT}"
  docker build --quiet --build-arg GIT_SHA="$COMMIT" -t "$IMAGE" product >/dev/null
fi

echo "Checking the image"
label="$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE")"
[[ "$label" == "$COMMIT" ]] && pass "image label records commit" || fail "image label is '${label}'"

uid="$(docker run --rm --entrypoint id "$IMAGE" -u)"
[[ "$uid" != "0" ]] && pass "runs as non-root (uid ${uid})" || fail "runs as root"

leaked="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  'find /app -xdev \( -name ".env" -o -name ".env.*" -o -name ".git" -o -name "*.tfstate*" -o -name "*.tfvars" -o -name "token*.json" -o -name ".dropbox_token*.json" -o -name "client_secret*.json" \) 2>/dev/null; true')"
[[ -z "$leaked" ]] && pass "no .env, .git, Terraform or credential files in /app" || fail "found: ${leaked}"

# Runs the image with the given arguments; it must exit non-zero, name
# `expected` and print none of the fake values.
expect_startup_failure() {
  local description="$1" expected="$2"
  shift 2
  local output status
  output="$(docker run --rm -e PORT=8000 "$@" "$IMAGE" 2>&1)" && status=0 || status=$?
  [[ "$status" -ne 0 ]] && pass "${description}: exits non-zero (${status})" \
    || fail "${description}: started"
  grep -q "$expected" <<<"$output" && pass "${description}: names ${expected}" \
    || fail "${description}: does not name ${expected}"
  grep -qe "$FAKE_TRELLO_KEY" -e "$FAKE_DROPBOX_KEY" -e "fake-disposable-google-token" <<<"$output" \
    && fail "${description}: printed a configured value" \
    || pass "${description}: prints no configured value"
}

echo "Starting with bad configuration"
expect_startup_failure "nothing configured" "TRACKER_PROVIDER" \
  -e TRELLO_API_KEY="$FAKE_TRELLO_KEY"
expect_startup_failure "real providers, no token file" "CALENDAR_TOKEN_FILE" \
  "${REAL[@]}" -e TRELLO_API_KEY="$FAKE_TRELLO_KEY"
expect_startup_failure "real providers, no Dropbox credentials" "STORAGE_PROVIDER" \
  "${REAL[@]}" -e TRELLO_API_KEY="$FAKE_TRELLO_KEY" \
  -e DROPBOX_APP_KEY="$FAKE_DROPBOX_KEY" \
  -v "${TOKEN_DIR}/token.json:/etc/secrets/token.json:ro" \
  -e CALENDAR_TOKEN_FILE=/etc/secrets/token.json

echo "Starting with the in-memory providers on port ${PORT}"
docker run -d --name "$CONTAINER" -p "127.0.0.1:${PORT}:8000" -e PORT=8000 \
  "${FAKES[@]}" -e TRELLO_API_KEY="$FAKE_TRELLO_KEY" "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done

health="$(curl -sS -w ' %{http_code}' "http://127.0.0.1:${PORT}/healthz" || true)"
[[ "$health" == *' 200' ]] && pass "/healthz 200" || fail "/healthz: ${health}"

ready="$(curl -sS -w ' %{http_code}' "http://127.0.0.1:${PORT}/readyz" || true)"
echo "  /readyz -> ${ready}"
[[ "$ready" == *' 200' ]] && pass "/readyz 200" || fail "/readyz not 200"
[[ "$ready" == *"\"commit\":\"${COMMIT}\""* ]] && pass "/readyz reports commit" || fail "/readyz commit mismatch"

result="$(curl -sS -w ' %{http_code}' -X POST "http://127.0.0.1:${PORT}/v1/focus-day" \
  -H 'content-type: application/json' \
  -d '{"assignee": "disposable", "day": "2026-11-18"}' || true)"
[[ "$result" == *' 200' && "$result" == *'"status":"ok"'* ]] \
  && pass "POST /v1/focus-day completes with the in-memory providers" \
  || fail "POST /v1/focus-day: ${result}"
[[ "$result" == *'"failed_step":null'* && "$result" == *'"path":"/standups/2026-11-18-disposable.md"'* ]] \
  && pass "every step ran and the doc was written" || fail "unexpected focus-day body"

logs="$(docker logs "$CONTAINER" 2>&1)"
grep -qe "$FAKE_TRELLO_KEY" <<<"$logs" && fail "logs contain a credential" || pass "logs contain no credential"

echo
if [[ "$failures" -eq 0 ]]; then
  echo "All product disposable-environment checks passed for ${IMAGE}; removing the container and image."
else
  echo "${failures} check(s) failed for ${IMAGE}; removing the container and image."
  exit 1
fi
