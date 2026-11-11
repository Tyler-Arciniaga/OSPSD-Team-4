#!/usr/bin/env bash
# Build the deployable image once for a commit, push it tagged with the full
# commit SHA, and report its digest.
#
# The digest, not the tag, identifies the artifact from here on: every later
# stage (the disposable-container check, the deploy, the release record and
# any rollback) uses <repo>@<digest>. No `latest` tag is ever pushed.
#
# Usage: scripts/build_image.sh <image-repo> <commit>
#   e.g. scripts/build_image.sh ghcr.io/tyler-arciniaga/ospsd-team4-issue-service "$(git rev-parse HEAD)"
#
# Prints `image=<repo>` and `digest=sha256:...`, and appends them to
# $GITHUB_OUTPUT and a summary to $GITHUB_STEP_SUMMARY when those are set.
# Needs: docker with buildx, already logged in to the registry; jq; a clean
# checkout of <commit>.

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <image-repo> <commit>" >&2
  exit 2
fi
REPO="$1"
COMMIT="$2"

if [[ ! "$COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "commit must be a full, lowercase 40-character SHA, got '${COMMIT}'." >&2
  exit 2
fi
# The repository names no tag or digest of its own: this script adds both.
last_segment="${REPO##*/}"
lowercase="$(tr '[:upper:]' '[:lower:]' <<<"$REPO")"
if [[ "$REPO" == *@* || "$last_segment" == *:* || "$REPO" != "$lowercase" ]]; then
  echo "image repository must be lowercase with no tag or digest, got '${REPO}'." >&2
  exit 2
fi

cd "$(git rev-parse --show-toplevel)"

# The image must be exactly the commit it is labelled with.
head="$(git rev-parse HEAD)"
if [[ "$head" != "$COMMIT" ]]; then
  echo "The checkout is at ${head}, not ${COMMIT}." >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked files have uncommitted changes; refusing to build ${COMMIT}." >&2
  exit 1
fi

TAG="${REPO}:${COMMIT}"

echo "Building and pushing ${TAG}"
# linux/amd64 is what Render runs. No provenance or SBOM attestations, so the
# pushed digest names a plain image manifest rather than an index.
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  --build-arg GIT_SHA="$COMMIT" \
  --tag "$TAG" \
  --push \
  .

# Ask the registry, not the local build, which digest the tag now names.
digest="$(docker buildx imagetools inspect "$TAG" --format '{{json .Manifest}}' | jq -r '.digest')"
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Could not read the pushed digest for ${TAG} (got '${digest}')." >&2
  exit 1
fi

# Drop the local copy, so later checks pull the pushed bytes from the registry.
docker image rm "$TAG" >/dev/null 2>&1 || true

echo "image=${REPO}"
echo "digest=${digest}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "image=${REPO}"
    echo "digest=${digest}"
  } >>"$GITHUB_OUTPUT"
fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## Artifact"
    echo
    echo "| | |"
    echo "| --- | --- |"
    echo "| Commit | \`${COMMIT}\` |"
    echo "| Image | \`${REPO}@${digest}\` |"
    echo "| Tag (for browsing only; never deployed) | \`${TAG}\` |"
  } >>"$GITHUB_STEP_SUMMARY"
fi
