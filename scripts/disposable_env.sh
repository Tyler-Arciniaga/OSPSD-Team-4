#!/usr/bin/env bash
# Disposable test environment: run the deployable image with fake
# configuration, check it, then remove everything.
#
# By default it builds the image from the current commit. With IMAGE_REF set
# to a pushed image pinned by digest, it pulls and checks exactly that image
# instead; the release workflow does this before deploying it.
#
# This shows that the artifact runs anywhere Docker does and does not depend
# on this machine, without touching the real deployment or a real provider
# account. The fake credentials are rejected by the provider, which is
# expected: readiness must stay 200 while the provider is unreachable.
#
# Usage: scripts/disposable_env.sh        (from a clean checkout)
#        IMAGE_REF=<repo>@sha256:<digest> EXPECTED_COMMIT=<sha> scripts/disposable_env.sh
#        (EXPECTED_COMMIT defaults to HEAD: the commit the image must record)
# Needs: docker, curl, git.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [[ -n "${IMAGE_REF:-}" ]]; then
  # A tag can point at different images over time; only a digest names
  # exactly one.
  if [[ ! "$IMAGE_REF" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "IMAGE_REF must be pinned by digest (<repo>@sha256:<64 hex>), got '${IMAGE_REF}'." >&2
    exit 2
  fi
  COMMIT="${EXPECTED_COMMIT:-$(git rev-parse HEAD)}"
  IMAGE="$IMAGE_REF"
else
  COMMIT="$(git rev-parse HEAD)"
  IMAGE="ospsd-team4-disposable:${COMMIT:0:12}"
fi
CONTAINER="ospsd-team4-disposable-$$"
PORT="${DISPOSABLE_PORT:-18000}"

# Fake values. The checks below prove none of them is ever printed.
FAKE_KEY="fake-disposable-key-0000"
FAKE_TOKEN="fake-disposable-token-0000"
FAKE_LIST="5f1c0a2b3d4e5f6071829304"

failures=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() {
  printf '  FAIL  %s\n' "$1"
  failures=$((failures + 1))
}

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "${CONTAINER}-missing" >/dev/null 2>&1 || true
  if [[ "${KEEP_IMAGE:-0}" != "1" ]]; then
    docker rmi -f "$IMAGE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -n "${IMAGE_REF:-}" ]]; then
  echo "Pulling ${IMAGE}"
  docker pull --quiet "$IMAGE" >/dev/null
else
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "note: tracked files have uncommitted changes; the image includes them,"
    echo "      so it is not exactly commit ${COMMIT}."
  fi

  echo "Building ${IMAGE} from ${COMMIT}"
  docker build --quiet --build-arg GIT_SHA="$COMMIT" -t "$IMAGE" . >/dev/null
fi

echo "Checking the image"
label="$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE")"
[[ "$label" == "$COMMIT" ]] && pass "image label records commit" || fail "image label is '${label}'"

uid="$(docker run --rm --entrypoint id "$IMAGE" -u)"
[[ "$uid" != "0" ]] && pass "runs as non-root (uid ${uid})" || fail "runs as root"

# Nothing secret or environment-specific was copied into the image. (find
# meets unreadable system directories as the non-root user; that is fine.)
leaked="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  'find / -xdev \( -name ".env" -o -name ".env.*" -o -name ".git" -o -name "*.tfstate*" -o -name "*.tfvars" \) 2>/dev/null; true')"
[[ -z "$leaked" ]] && pass "no .env, .git or Terraform files in the image" || fail "found: ${leaked}"

echo "Starting with a required variable missing"
missing_output="$(docker run --name "${CONTAINER}-missing" \
  -e ISSUE_PROVIDER=trello -e TRELLO_API_KEY="$FAKE_KEY" -e TRELLO_LIST_ID="$FAKE_LIST" \
  "$IMAGE" 2>&1)" && missing_status=0 || missing_status=$?
[[ "$missing_status" -ne 0 ]] && pass "exits non-zero (${missing_status})" || fail "started without TRELLO_TOKEN"
grep -q "TRELLO_TOKEN" <<<"$missing_output" && pass "names TRELLO_TOKEN" || fail "does not name TRELLO_TOKEN"
grep -qe "$FAKE_KEY" -e "$FAKE_LIST" <<<"$missing_output" && fail "printed a configured value" || pass "prints no configured value"

echo "Starting with complete fake configuration on port ${PORT}"
docker run -d --name "$CONTAINER" -p "127.0.0.1:${PORT}:8000" \
  -e ISSUE_PROVIDER=trello -e TRELLO_API_KEY="$FAKE_KEY" \
  -e TRELLO_TOKEN="$FAKE_TOKEN" -e TRELLO_LIST_ID="$FAKE_LIST" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done

health="$(curl -sS -w ' %{http_code}' "http://127.0.0.1:${PORT}/healthz" || true)"
[[ "$health" == *' 200' ]] && pass "/healthz 200" || fail "/healthz: ${health}"

ready="$(curl -sS -w ' %{http_code}' "http://127.0.0.1:${PORT}/readyz" || true)"
echo "  /readyz -> ${ready}"
[[ "$ready" == *' 200' ]] && pass "/readyz 200 with the provider unreachable" || fail "/readyz not 200"
[[ "$ready" == *"\"commit\":\"${COMMIT}\""* ]] && pass "/readyz reports commit" || fail "/readyz commit mismatch"
[[ "$ready" == *'"idempotency_store":"ok"'* ]] && pass "key store opens as the non-root user" || fail "key store not ok"

logs="$(docker logs "$CONTAINER" 2>&1)"
grep -qe "$FAKE_KEY" -e "$FAKE_TOKEN" <<<"$logs" && fail "logs contain a credential" || pass "logs contain no credential"

echo
if [[ "$failures" -eq 0 ]]; then
  echo "All disposable-environment checks passed for ${IMAGE}; removing the container and image."
else
  echo "${failures} check(s) failed for ${IMAGE}; removing the container and image."
  exit 1
fi
