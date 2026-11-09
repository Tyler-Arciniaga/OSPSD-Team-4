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

The release record is a GitHub Deployment per release, in the GitHub
environment of the target (render-production or render-disposable), with the
commit as its ref and the image digest, run link and deploy window in its
payload. Its status is `success` only when the smoke check passed.

    python -m scripts.release record --repo o/r --environment render-production ...
    python -m scripts.release known-good --repo o/r --environment render-production
    python -m scripts.release verify-rollback --repo o/r --environment render-production \
        --commit <sha> --digest sha256:...

`known-good` prints the latest release recorded as `success`: the revision to
return to. `verify-rollback` refuses a commit and digest that were never
released successfully together in that environment.

Reads RENDER_API_KEY, RENDER_OWNER_ID and GITHUB_TOKEN from the environment,
never prints them, and writes step outputs to $GITHUB_OUTPUT when it is set.
"""

import argparse
import json
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
GITHUB_API = "https://api.github.com"
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENVIRONMENTS = ("render-production", "render-disposable")
# The release record is read from the newest deployments of an environment.
DEPLOYMENTS_PAGE = 100

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


@dataclass(frozen=True)
class Release:
    """One recorded release."""

    deployment_id: int
    commit: str
    image: str
    digest: str
    run_url: str
    created_at: str


def _github_payload(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = deployment.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload) if payload else {}
        except ValueError:
            return {}
    return payload if isinstance(payload, Mapping) else {}


def _release_from(deployment: Mapping[str, Any]) -> Release:
    payload = _github_payload(deployment)
    return Release(
        deployment_id=int(deployment["id"]),
        commit=str(deployment.get("sha", "")),
        image=str(payload.get("image", "")),
        digest=str(payload.get("digest", "")),
        run_url=str(payload.get("run_url", "")),
        created_at=str(deployment.get("created_at", "")),
    )


def _check_names(repo: str, environment: str) -> None:
    if not REPO_PATTERN.fullmatch(repo):
        raise ReleaseError(f"repository must be owner/name, got {repo!r}")
    if environment not in ENVIRONMENTS:
        raise ReleaseError(
            f"environment must be one of {', '.join(ENVIRONMENTS)}, got {environment!r}"
        )


def _deployments(
    github: httpx.Client, repo: str, environment: str, commit: str | None = None
) -> list[Mapping[str, Any]]:
    """The environment's newest deployments, newest first."""
    params: dict[str, str | int] = {
        "environment": environment,
        "per_page": DEPLOYMENTS_PAGE,
    }
    if commit:
        params["sha"] = commit
    response = github.get(f"/repos/{repo}/deployments", params=params)
    _check_github(response, "list deployments")
    deployments = response.json()
    return [d for d in deployments if isinstance(d, Mapping)]


def _ever_succeeded(github: httpx.Client, repo: str, deployment_id: int) -> bool:
    response = github.get(
        f"/repos/{repo}/deployments/{deployment_id}/statuses", params={"per_page": 100}
    )
    _check_github(response, "list deployment statuses")
    return any(
        isinstance(status, Mapping) and status.get("state") == "success"
        for status in response.json()
    )


def _check_github(response: httpx.Response, action: str) -> None:
    if response.is_success:
        return
    detail = response.text[:300].strip()
    raise ReleaseError(
        f"GitHub refused to {action}: HTTP {response.status_code} {detail}"
    )


def record_release(
    github: httpx.Client,
    *,
    repo: str,
    environment: str,
    commit: str,
    image: str,
    digest: str,
    succeeded: bool,
    description: str,
    run_url: str,
    service_url: str = "",
    deploy_id: str = "",
    started_at: str = "",
    finished_at: str = "",
) -> int:
    """Create the release's Deployment and its final status; return its ID.

    A release that failed before a digest existed is still recorded, as a
    failure, so the record shows every attempt.
    """
    _check_names(repo, environment)
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ReleaseError(f"commit must be a full 40-character SHA, got {commit!r}")
    if succeeded:
        # Only a revision that can be deployed again counts as known-good.
        image_reference(image, digest)
    elif digest and not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError(f"digest must be sha256:<64 hex>, got {digest!r}")

    response = github.post(
        f"/repos/{repo}/deployments",
        json={
            "ref": commit,
            "environment": environment,
            "description": description[:140],
            # Record what happened; do not merge or wait for other checks.
            "auto_merge": False,
            "required_contexts": [],
            "production_environment": environment == "render-production",
            "transient_environment": environment == "render-disposable",
            "payload": {
                "image": image,
                "digest": digest,
                "run_url": run_url,
                "service_url": service_url,
                "render_deploy_id": deploy_id,
                "deploy_started_at": started_at,
                "deploy_finished_at": finished_at,
            },
        },
    )
    _check_github(response, "create the deployment")
    deployment_id = int(response.json()["id"])

    response = github.post(
        f"/repos/{repo}/deployments/{deployment_id}/statuses",
        json={
            "state": "success" if succeeded else "failure",
            "description": description[:140],
            "log_url": run_url,
            "environment_url": service_url,
            # Keep earlier successes as they are: each stays a revision a
            # rollback may return to.
            "auto_inactive": False,
        },
    )
    _check_github(response, "set the deployment status")
    return deployment_id


def latest_success(
    github: httpx.Client, *, repo: str, environment: str
) -> Release | None:
    """Return the newest release recorded as success in `environment`."""
    _check_names(repo, environment)
    for deployment in _deployments(github, repo, environment):
        release = _release_from(deployment)
        if DIGEST_PATTERN.fullmatch(release.digest) and _ever_succeeded(
            github, repo, release.deployment_id
        ):
            return release
    return None


def verify_rollback(
    github: httpx.Client, *, repo: str, environment: str, commit: str, digest: str
) -> Release:
    """Return the successful release of exactly `commit` and `digest`, or refuse."""
    _check_names(repo, environment)
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ReleaseError(f"commit must be a full 40-character SHA, got {commit!r}")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError(f"digest must be sha256:<64 hex>, got {digest!r}")

    candidates = [
        _release_from(d) for d in _deployments(github, repo, environment, commit)
    ]
    candidates = [r for r in candidates if r.commit == commit and r.digest == digest]
    if not candidates:
        raise ReleaseError(
            f"{commit} with {digest} was never released to {environment}; "
            "roll back only to a recorded release (known-good)"
        )
    for release in candidates:
        if _ever_succeeded(github, repo, release.deployment_id):
            image_reference(release.image, release.digest)
            return release
    raise ReleaseError(
        f"{commit} with {digest} was released to {environment} but never passed "
        "its smoke check; roll back only to a known-good release"
    )


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


def _github_client(
    environ: Mapping[str, str], transport: httpx.BaseTransport | None
) -> httpx.Client:
    token = environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ReleaseError("GITHUB_TOKEN is not set")
    return httpx.Client(
        base_url=GITHUB_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
        transport=transport,
    )


def _print_release(release: Release) -> None:
    print(f"commit={release.commit}")
    print(f"digest={release.digest}")
    print(f"image={release.image}")
    print(f"run={release.run_url}")
    print(f"recorded_at={release.created_at}")


def _parser() -> argparse.ArgumentParser:
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

    record = commands.add_parser(
        "record", help="record a release as a GitHub Deployment"
    )
    record.add_argument("--repo", required=True)
    record.add_argument("--environment", required=True, choices=ENVIRONMENTS)
    record.add_argument("--commit", required=True)
    record.add_argument("--result", required=True, choices=("success", "failure"))
    record.add_argument("--description", required=True)
    record.add_argument("--run-url", required=True)
    record.add_argument("--image", default="")
    record.add_argument("--digest", default="")
    record.add_argument("--service-url", default="")
    record.add_argument("--deploy-id", default="")
    record.add_argument("--started-at", default="")
    record.add_argument("--finished-at", default="")

    for name, help_text in (
        ("known-good", "print the latest successful release"),
        ("verify-rollback", "check a commit and digest were released successfully"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--repo", required=True)
        command.add_argument("--environment", required=True, choices=ENVIRONMENTS)
        if name == "verify-rollback":
            command.add_argument("--commit", required=True)
            command.add_argument("--digest", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    transport: httpx.BaseTransport | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
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
        elif args.command == "check-ready":
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
        elif args.command == "record":
            with _github_client(environ, transport) as github:
                deployment_id = record_release(
                    github,
                    repo=args.repo,
                    environment=args.environment,
                    commit=args.commit,
                    image=args.image,
                    digest=args.digest,
                    succeeded=args.result == "success",
                    description=args.description,
                    run_url=args.run_url,
                    service_url=args.service_url,
                    deploy_id=args.deploy_id,
                    started_at=args.started_at,
                    finished_at=args.finished_at,
                )
            print(
                f"Recorded deployment {deployment_id} for {args.commit} "
                f"in {args.environment}: {args.result}."
            )
        elif args.command == "known-good":
            with _github_client(environ, transport) as github:
                found = latest_success(
                    github, repo=args.repo, environment=args.environment
                )
            if found is None:
                raise ReleaseError(
                    f"no successful release is recorded in {args.environment}"
                )
            _print_release(found)
        else:
            with _github_client(environ, transport) as github:
                found = verify_rollback(
                    github,
                    repo=args.repo,
                    environment=args.environment,
                    commit=args.commit,
                    digest=args.digest,
                )
            print(
                f"{found.commit} with {found.digest} passed its smoke check in {found.run_url}."
            )
            _write_outputs(
                {"commit": found.commit, "image": found.image, "digest": found.digest},
                environ,
            )
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
