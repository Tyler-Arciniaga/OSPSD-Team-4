"""scripts/release.py record, known-good and verify-rollback against a fake GitHub API."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import release
from scripts.release import (
    ReleaseError,
    latest_success,
    record_release,
    verify_rollback,
)

REPO = "Tyler-Arciniaga/OSPSD-Team-4"
PRODUCTION = "render-production"
DISPOSABLE = "render-disposable"
IMAGE = "ghcr.io/tyler-arciniaga/ospsd-team4-issue-service"
RUN = "https://github.com/Tyler-Arciniaga/OSPSD-Team-4/actions/runs/1"
# Not a credential. Tests check it never appears in output.
TOKEN = "fake-github-token-0000"


def commit(n: int) -> str:
    return f"{n:x}" * 40 if n < 16 else f"{n:040x}"


def digest(n: int) -> str:
    return "sha256:" + f"{n:x}" * 64


class FakeGitHub:
    """GitHub's Deployments API: deployments and their statuses, newest first."""

    def __init__(self) -> None:
        self.deployments: list[dict[str, Any]] = []
        self.statuses: dict[int, list[dict[str, Any]]] = {}
        self.requests: list[httpx.Request] = []

    def add(
        self,
        environment: str,
        sha: str,
        payload_digest: str,
        states: list[str],
        *,
        payload_as_string: bool = False,
    ) -> int:
        deployment_id = len(self.deployments) + 1
        payload: Any = {"image": IMAGE, "digest": payload_digest, "run_url": RUN}
        if payload_as_string:
            payload = json.dumps(payload)
        self.deployments.insert(
            0,
            {
                "id": deployment_id,
                "sha": sha,
                "environment": environment,
                "payload": payload,
                "created_at": f"2026-11-{deployment_id:02d}T00:00:00Z",
            },
        )
        self.statuses[deployment_id] = [{"state": s} for s in reversed(states)]
        return deployment_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        base = f"/repos/{REPO}/deployments"
        if request.method == "GET" and path == base:
            env = request.url.params.get("environment")
            sha = request.url.params.get("sha")
            found = [
                d
                for d in self.deployments
                if d["environment"] == env and (sha is None or d["sha"] == sha)
            ]
            return httpx.Response(200, json=found)
        if request.method == "POST" and path == base:
            body = json.loads(request.content)
            deployment_id = len(self.deployments) + 1
            created = {
                "id": deployment_id,
                "sha": body["ref"],
                "environment": body["environment"],
                "payload": body["payload"],
                "created_at": "2026-11-30T00:00:00Z",
                "request": body,
            }
            self.deployments.insert(0, created)
            self.statuses[deployment_id] = []
            return httpx.Response(201, json=created)
        parts = path.removeprefix(base + "/").split("/")
        if len(parts) == 2 and parts[1] == "statuses":
            deployment_id = int(parts[0])
            if request.method == "GET":
                return httpx.Response(200, json=self.statuses[deployment_id])
            body = json.loads(request.content)
            self.statuses[deployment_id].insert(0, body)
            return httpx.Response(201, json=body)
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


def client(fake: FakeGitHub) -> httpx.Client:
    return httpx.Client(
        base_url=release.GITHUB_API, transport=httpx.MockTransport(fake.handler)
    )


# --- record -----------------------------------------------------------------


def _record(fake: FakeGitHub, **overrides: Any) -> int:
    arguments: dict[str, Any] = {
        "repo": REPO,
        "environment": PRODUCTION,
        "commit": commit(1),
        "image": IMAGE,
        "digest": digest(1),
        "succeeded": True,
        "description": "Smoke check passed",
        "run_url": RUN,
        "service_url": "https://svc.onrender.com",
        "deploy_id": "dep-1",
        "started_at": "2026-11-09T20:00:00Z",
        "finished_at": "2026-11-09T20:03:00Z",
    }
    arguments.update(overrides)
    with client(fake) as gh:
        return record_release(gh, **arguments)


def test_success_record_holds_commit_digest_run_and_window(github: FakeGitHub) -> None:
    deployment_id = _record(github)

    created = github.deployments[0]
    request = created["request"]
    assert created["id"] == deployment_id
    assert request["ref"] == commit(1)
    assert request["environment"] == PRODUCTION
    assert request["required_contexts"] == []
    assert request["auto_merge"] is False
    assert request["production_environment"] is True
    assert request["transient_environment"] is False
    assert request["payload"] == {
        "image": IMAGE,
        "digest": digest(1),
        "run_url": RUN,
        "service_url": "https://svc.onrender.com",
        "render_deploy_id": "dep-1",
        "deploy_started_at": "2026-11-09T20:00:00Z",
        "deploy_finished_at": "2026-11-09T20:03:00Z",
    }
    status = github.statuses[deployment_id][0]
    assert status["state"] == "success"
    assert status["log_url"] == RUN
    # Earlier successes stay valid rollback targets.
    assert status["auto_inactive"] is False


def test_failure_record_is_marked_failure(github: FakeGitHub) -> None:
    deployment_id = _record(github, succeeded=False, description="Smoke check failed")

    assert github.statuses[deployment_id][0]["state"] == "failure"


def test_failure_before_any_digest_is_still_recorded(github: FakeGitHub) -> None:
    deployment_id = _record(github, succeeded=False, image="", digest="")

    assert github.deployments[0]["payload"]["digest"] == ""
    assert github.statuses[deployment_id][0]["state"] == "failure"


def test_disposable_records_are_transient(github: FakeGitHub) -> None:
    _record(github, environment=DISPOSABLE)

    request = github.deployments[0]["request"]
    assert request["transient_environment"] is True
    assert request["production_environment"] is False


def test_success_without_a_digest_is_refused(github: FakeGitHub) -> None:
    with pytest.raises(ReleaseError, match="only an image digest"):
        _record(github, digest="")

    assert github.requests == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("commit", "abc123", "full 40-character"),
        ("environment", "production", "environment must be"),
        ("repo", "not a repo", "owner/name"),
    ],
)
def test_bad_record_input_is_refused(
    github: FakeGitHub, field: str, value: str, message: str
) -> None:
    with pytest.raises(ReleaseError, match=message):
        _record(github, **{field: value})

    assert github.requests == []


# --- known-good ---------------------------------------------------------------


def _latest(fake: FakeGitHub, environment: str = PRODUCTION) -> release.Release | None:
    with client(fake) as gh:
        return latest_success(gh, repo=REPO, environment=environment)


def test_known_good_is_the_newest_success(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["in_progress", "success"])
    github.add(PRODUCTION, commit(2), digest(2), ["success"])

    found = _latest(github)

    assert found is not None
    assert (found.commit, found.digest, found.image) == (commit(2), digest(2), IMAGE)


def test_known_good_skips_a_failed_newer_release(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])
    github.add(PRODUCTION, commit(2), digest(2), ["failure"])

    found = _latest(github)

    assert found is not None
    assert found.commit == commit(1)


def test_known_good_counts_a_success_later_marked_inactive(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success", "inactive"])

    found = _latest(github)

    assert found is not None
    assert found.commit == commit(1)


def test_known_good_is_per_environment(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])
    github.add(DISPOSABLE, commit(2), digest(2), ["success"])

    production = _latest(github, PRODUCTION)
    disposable = _latest(github, DISPOSABLE)

    assert production is not None and production.commit == commit(1)
    assert disposable is not None and disposable.commit == commit(2)


def test_known_good_reads_a_payload_stored_as_text(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"], payload_as_string=True)

    found = _latest(github)

    assert found is not None
    assert found.digest == digest(1)


def test_no_known_good_when_nothing_succeeded(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["failure"])

    assert _latest(github) is None


# --- verify-rollback ----------------------------------------------------------


def _verify(
    fake: FakeGitHub, sha: str, image_digest: str, environment: str = PRODUCTION
) -> release.Release:
    with client(fake) as gh:
        return verify_rollback(
            gh, repo=REPO, environment=environment, commit=sha, digest=image_digest
        )


def test_rollback_to_a_known_good_release_is_allowed(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])
    github.add(PRODUCTION, commit(2), digest(2), ["failure"])

    found = _verify(github, commit(1), digest(1))

    assert (found.commit, found.image, found.digest) == (commit(1), IMAGE, digest(1))


def test_rollback_to_an_unknown_revision_is_refused(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])

    with pytest.raises(ReleaseError, match="never released"):
        _verify(github, commit(3), digest(3))


def test_rollback_with_a_mismatched_digest_is_refused(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])
    github.add(PRODUCTION, commit(2), digest(2), ["success"])

    with pytest.raises(ReleaseError, match="never released"):
        _verify(github, commit(1), digest(2))


def test_rollback_to_a_release_that_never_passed_is_refused(github: FakeGitHub) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["in_progress", "failure"])

    with pytest.raises(ReleaseError, match="never passed its smoke check"):
        _verify(github, commit(1), digest(1))


def test_rollback_needs_success_in_the_same_environment(github: FakeGitHub) -> None:
    github.add(DISPOSABLE, commit(1), digest(1), ["success"])

    with pytest.raises(ReleaseError, match="never released to render-production"):
        _verify(github, commit(1), digest(1), PRODUCTION)


@pytest.mark.parametrize(
    ("sha", "image_digest", "message"),
    [
        (commit(1)[:12], digest(1), "full 40-character"),
        (commit(1), "latest", "sha256"),
    ],
)
def test_rollback_input_is_validated_before_any_request(
    github: FakeGitHub, sha: str, image_digest: str, message: str
) -> None:
    with pytest.raises(ReleaseError, match=message):
        _verify(github, sha, image_digest)

    assert github.requests == []


# --- command line -----------------------------------------------------------


def _main(fake: FakeGitHub, argv: list[str], environ: dict[str, str]) -> int:
    return release.main(
        argv, environ=environ, transport=httpx.MockTransport(fake.handler)
    )


def test_cli_verify_rollback_writes_the_image_to_deploy(
    github: FakeGitHub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])
    outputs = tmp_path / "out"
    outputs.write_text("")

    status = _main(
        github,
        [
            "verify-rollback",
            "--repo",
            REPO,
            "--environment",
            PRODUCTION,
            "--commit",
            commit(1),
            "--digest",
            digest(1),
        ],
        {"GITHUB_TOKEN": TOKEN, "GITHUB_OUTPUT": str(outputs)},
    )

    assert status == 0
    written = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert written == {"commit": commit(1), "image": IMAGE, "digest": digest(1)}
    assert github.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err


def test_cli_refused_rollback_exits_non_zero(
    github: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        github,
        [
            "verify-rollback",
            "--repo",
            REPO,
            "--environment",
            PRODUCTION,
            "--commit",
            commit(9),
            "--digest",
            digest(9),
        ],
        {"GITHUB_TOKEN": TOKEN},
    )

    assert status == 1
    assert "never released" in capsys.readouterr().err


def test_cli_known_good_prints_the_revision(
    github: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    github.add(PRODUCTION, commit(1), digest(1), ["success"])

    status = _main(
        github,
        ["known-good", "--repo", REPO, "--environment", PRODUCTION],
        {"GITHUB_TOKEN": TOKEN},
    )

    assert status == 0
    printed = capsys.readouterr().out
    assert f"commit={commit(1)}" in printed
    assert f"digest={digest(1)}" in printed
    assert f"run={RUN}" in printed


def test_cli_known_good_with_none_recorded_fails(
    github: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        github,
        ["known-good", "--repo", REPO, "--environment", PRODUCTION],
        {"GITHUB_TOKEN": TOKEN},
    )

    assert status == 1
    assert "no successful release" in capsys.readouterr().err


def test_cli_record_without_a_token_fails(
    github: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        github,
        [
            "record",
            "--repo",
            REPO,
            "--environment",
            PRODUCTION,
            "--commit",
            commit(1),
            "--result",
            "failure",
            "--description",
            "x",
            "--run-url",
            RUN,
        ],
        {},
    )

    assert status == 1
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err
    assert github.requests == []


def test_cli_record_success(github: FakeGitHub) -> None:
    status = _main(
        github,
        [
            "record",
            "--repo",
            REPO,
            "--environment",
            PRODUCTION,
            "--commit",
            commit(1),
            "--result",
            "success",
            "--description",
            "Smoke check passed",
            "--run-url",
            RUN,
            "--image",
            IMAGE,
            "--digest",
            digest(1),
        ],
        {"GITHUB_TOKEN": TOKEN},
    )

    assert status == 0
    assert github.statuses[1][0]["state"] == "success"
