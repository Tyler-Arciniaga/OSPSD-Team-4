"""Deploy one image digest to Render and confirm the service runs it.

Used by the release workflow (.github/workflows/release.yml):

    python -m scripts.release deploy --service-id srv-... --image ghcr.io/o/r --digest sha256:...
    python -m scripts.release check-ready --url https://....onrender.com --commit <40-hex sha>

`deploy` refuses anything but an image pinned by digest. It first points the
service's image setting at that digest, so any later deploy Render starts on
its own runs the same image, then starts a deploy of exactly that digest and
waits until Render reports it live. Render answers 202 with no body while
another deploy is running; the queued deploy is found by its digest once it
starts.

`check-ready` waits for liveness, then for /readyz to be ready and to report
the expected commit.

Reads RENDER_API_KEY and RENDER_OWNER_ID from the environment, never prints
them, and writes step outputs to $GITHUB_OUTPUT when it is set.
"""

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

RENDER_API = "https://api.render.com/v1"

DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
# A lowercase registry path with no tag or digest; a registry host may carry
# a port (localhost:5000/team/app).
IMAGE_PATTERN = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+"
)

# A free deploy can wait behind another deploy, then pull and start the image.
DEPLOY_TIMEOUT_SECONDS = 30 * 60
POLL_SECONDS = 20.0
# A free instance can take about a minute to wake.
READY_TIMEOUT_SECONDS = 5 * 60
READY_POLL_SECONDS = 10.0

FAILED_STATUSES = frozenset(
    {
        "build_failed",
        "update_failed",
        "pre_deploy_failed",
        "canceled",
        "deactivated",
    }
)

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class ReleaseError(Exception):
    """A release step failed. The message is safe to print."""


@dataclass(frozen=True)
class DeployResult:
    deploy_id: str
    started_at: str
    finished_at: str


def image_reference(image: str, digest: str) -> str:
    """Return `<image>@<digest>`, refusing tags, mutable references and bad input."""
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError(
            f"refusing to deploy {digest!r}: only an image digest "
            "(sha256:<64 hex>) names exactly one image"
        )
    if not IMAGE_PATTERN.fullmatch(image):
        raise ReleaseError(
            f"image must be a lowercase repository with no tag or digest, got {image!r}"
        )
    return f"{image}@{digest}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _check(response: httpx.Response, action: str) -> None:
    if response.is_success:
        return
    # Render's error bodies are short messages; they hold no credentials.
    detail = response.text[:300].strip()
    raise ReleaseError(
        f"Render refused to {action}: HTTP {response.status_code} {detail}"
    )


def _deploy_matches(deploy: Mapping[str, Any], digest: str) -> bool:
    image = deploy.get("image")
    if not isinstance(image, Mapping):
        return False
    return image.get("sha") == digest or str(image.get("ref", "")).endswith(
        f"@{digest}"
    )


def _find_started_deploy(
    render: httpx.Client, service_id: str, digest: str, requested_at: datetime
) -> Mapping[str, Any] | None:
    """Return the deploy of `digest` created since the request, if one has started."""
    response = render.get(f"/services/{service_id}/deploys", params={"limit": 20})
    _check(response, "list deploys")
    for item in response.json():
        deploy = item.get("deploy", item) if isinstance(item, Mapping) else None
        if not isinstance(deploy, Mapping) or not _deploy_matches(deploy, digest):
            continue
        created = _parse_time(deploy.get("createdAt"))
        # An older deploy of the same digest (an earlier release or rollback)
        # is not this one. Allow for a little clock difference.
        if created is None or (requested_at - created).total_seconds() <= 60:
            return deploy
    return None


def deploy_image(
    render: httpx.Client,
    *,
    service_id: str,
    owner_id: str,
    image: str,
    digest: str,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
    now: Callable[[], str] = _utc_now,
    timeout_seconds: float = DEPLOY_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    log: Callable[[str], None] = print,
) -> DeployResult:
    """Point the service at `image@digest`, deploy it and wait until it is live."""
    reference = image_reference(image, digest)
    if not service_id.startswith("srv-"):
        raise ReleaseError(
            f"service ID must be a Render service ID (srv-...), got {service_id!r}"
        )
    if not owner_id:
        raise ReleaseError("RENDER_OWNER_ID is not set")
    deadline = clock() + timeout_seconds

    # Render's image deploys do not change the service's settings, so set
    # them first: a deploy Render starts later then runs this digest too.
    response = render.patch(
        f"/services/{service_id}",
        json={"image": {"ownerId": owner_id, "imagePath": reference}},
    )
    _check(response, "set the service image")
    configured = response.json().get("imagePath") if response.content else None
    if configured is not None and configured != reference:
        raise ReleaseError(
            f"the service image is {configured!r} after the update, expected {reference!r}"
        )
    log(f"Service image set to {reference}")

    requested_at = datetime.now(UTC)
    started_at = now()
    response = render.post(
        f"/services/{service_id}/deploys", json={"imageUrl": reference}
    )
    _check(response, "start a deploy")

    deploy: Mapping[str, Any] | None = None
    if response.status_code != 202 and response.content:
        deploy = response.json()
    while deploy is None:
        if clock() >= deadline:
            raise ReleaseError(f"Render did not start a deploy of {reference} in time")
        log("deploy queued behind another deploy; waiting for it to start")
        sleep(poll_seconds)
        deploy = _find_started_deploy(render, service_id, digest, requested_at)

    deploy_id = str(deploy.get("id", ""))
    if not deploy_id:
        raise ReleaseError("Render's deploy response has no deploy ID")
    log(f"Deploy {deploy_id} started for {reference}")

    status = str(deploy.get("status", ""))
    while status != "live":
        if status in FAILED_STATUSES:
            raise ReleaseError(
                f"deploy {deploy_id} ended as {status}; see the Render dashboard logs"
            )
        if clock() >= deadline:
            raise ReleaseError(f"deploy {deploy_id} did not go live in time")
        sleep(poll_seconds)
        response = render.get(f"/services/{service_id}/deploys/{deploy_id}")
        _check(response, "read the deploy")
        status = str(response.json().get("status", ""))
        log(f"{now()} {status}")

    return DeployResult(deploy_id=deploy_id, started_at=started_at, finished_at=now())


def wait_until_ready(
    service: httpx.Client,
    *,
    commit: str,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
    timeout_seconds: float = READY_TIMEOUT_SECONDS,
    poll_seconds: float = READY_POLL_SECONDS,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Wait for /healthz, then for /readyz to be ready and report `commit`.

    Returns the last readiness body. Right after a deploy goes live, a
    request can still reach the previous instance, so a different commit is
    retried until the deadline before it counts as a failure.
    """
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ReleaseError(f"commit must be a full 40-character SHA, got {commit!r}")
    deadline = clock() + timeout_seconds
    last_problem = "no response"
    while True:
        try:
            health = service.get("/healthz")
            if health.status_code == 200:
                ready = service.get("/readyz")
                body = ready.json()
                if not isinstance(body, dict):
                    last_problem = "/readyz did not return an object"
                elif ready.status_code != 200 or body.get("status") != "ready":
                    last_problem = (
                        f"/readyz is {ready.status_code} {body.get('status')!r}"
                    )
                elif body.get("commit") != commit:
                    last_problem = f"/readyz reports commit {body.get('commit')!r}"
                else:
                    log(f"/readyz: {body}")
                    return body
            else:
                last_problem = f"/healthz is {health.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            last_problem = f"{type(exc).__name__} while checking health"
        if clock() >= deadline:
            raise ReleaseError(
                f"the service is not ready with commit {commit}: {last_problem}"
            )
        log(f"waiting: {last_problem}")
        sleep(poll_seconds)


def _write_outputs(values: Mapping[str, str], environ: Mapping[str, str]) -> None:
    path = environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as output:
        output.writelines(f"{name}={value}\n" for name, value in values.items())


def _render_client(api_key: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=RENDER_API,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=30,
        transport=transport,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.release")
    commands = parser.add_subparsers(dest="command", required=True)

    deploy = commands.add_parser("deploy", help="deploy an image digest to Render")
    deploy.add_argument("--service-id", required=True)
    deploy.add_argument("--image", required=True, help="repository, no tag or digest")
    deploy.add_argument("--digest", required=True, help="sha256:<64 hex>")

    ready = commands.add_parser("check-ready", help="confirm the service runs a commit")
    ready.add_argument("--url", required=True)
    ready.add_argument("--commit", required=True)
    ready.add_argument(
        "--timeout",
        type=float,
        default=READY_TIMEOUT_SECONDS,
        help="seconds to wait (default: %(default)s)",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "deploy":
            api_key = environ.get("RENDER_API_KEY", "")
            if not api_key:
                raise ReleaseError("RENDER_API_KEY is not set")
            with _render_client(api_key, transport) as render:
                result = deploy_image(
                    render,
                    service_id=args.service_id,
                    owner_id=environ.get("RENDER_OWNER_ID", ""),
                    image=args.image,
                    digest=args.digest,
                    clock=clock,
                    sleep=sleep,
                )
            print(f"Deploy {result.deploy_id} is live.")
            _write_outputs(
                {
                    "deploy_id": result.deploy_id,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                },
                environ,
            )
        else:
            with httpx.Client(
                base_url=args.url.rstrip("/"), timeout=30, transport=transport
            ) as service:
                wait_until_ready(
                    service,
                    commit=args.commit,
                    clock=clock,
                    sleep=sleep,
                    timeout_seconds=args.timeout,
                    poll_seconds=min(READY_POLL_SECONDS, max(args.timeout / 10, 0.5)),
                )
            print(f"{args.url} is ready and runs {args.commit}.")
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
