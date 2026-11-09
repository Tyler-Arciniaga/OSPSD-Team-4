"""scripts/release.py deploy and check-ready, against fake Render and service APIs."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import release
from scripts.release import (
    ReleaseError,
    deploy_image,
    image_reference,
    wait_until_ready,
)

IMAGE = "ghcr.io/tyler-arciniaga/ospsd-team4-issue-service"
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
REFERENCE = f"{IMAGE}@{DIGEST}"
COMMIT = "c" * 40
OLD_COMMIT = "d" * 40
SERVICE_ID = "srv-test123"
OWNER_ID = "tea-test456"
# Not a credential. Tests check it never appears in output.
API_KEY = "fake-render-key-0000"


class FakeClock:
    """Time that moves only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class FakeRender:
    """The parts of Render's API the deploy step uses."""

    def __init__(
        self,
        statuses: list[str],
        *,
        queued_polls: int = 0,
        patch_status: int = 200,
        deploy_status: int = 201,
        configured_image: str | None = None,
    ) -> None:
        self.statuses = statuses
        self.queued_polls = queued_polls
        self.patch_status = patch_status
        self.deploy_status = deploy_status
        self.configured_image = configured_image
        self.requests: list[httpx.Request] = []
        self.deploy_body: dict[str, Any] | None = None
        self.listed: list[dict[str, Any]] = []

    def _deploy(self, status: str) -> dict[str, Any]:
        return {
            "id": "dep-new",
            "status": status,
            "createdAt": _iso(datetime.now(UTC)),
            "image": {"ref": REFERENCE, "sha": DIGEST},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        base = f"/v1/services/{SERVICE_ID}"
        if request.method == "PATCH" and path == base:
            body = json.loads(request.content)
            image = self.configured_image or body["image"]["imagePath"]
            return httpx.Response(
                self.patch_status, json={"id": SERVICE_ID, "imagePath": image}
            )
        if request.method == "POST" and path == f"{base}/deploys":
            self.deploy_body = json.loads(request.content)
            if self.deploy_status == 202:
                return httpx.Response(202)
            if self.deploy_status != 201:
                return httpx.Response(self.deploy_status, json={"message": "bad"})
            return httpx.Response(201, json=self._deploy(self.statuses.pop(0)))
        if request.method == "GET" and path == f"{base}/deploys":
            if self.queued_polls > 0:
                self.queued_polls -= 1
                return httpx.Response(200, json=self.listed)
            started = {"deploy": self._deploy(self.statuses.pop(0)), "cursor": "x"}
            return httpx.Response(200, json=[started, *self.listed])
        if request.method == "GET" and path == f"{base}/deploys/dep-new":
            return httpx.Response(200, json=self._deploy(self.statuses.pop(0)))
        return httpx.Response(
            404, json={"message": f"unexpected {request.method} {path}"}
        )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _client(fake: FakeRender) -> httpx.Client:
    return httpx.Client(
        base_url=release.RENDER_API, transport=httpx.MockTransport(fake.handler)
    )


def _deploy(
    fake: FakeRender, clock: FakeClock, **overrides: Any
) -> release.DeployResult:
    arguments: dict[str, Any] = {
        "service_id": SERVICE_ID,
        "owner_id": OWNER_ID,
        "image": IMAGE,
        "digest": DIGEST,
        "clock": clock,
        "sleep": clock.sleep,
        "log": lambda _: None,
    }
    arguments.update(overrides)
    with _client(fake) as render:
        return deploy_image(render, **arguments)


# --- digest-only rule -------------------------------------------------------


@pytest.mark.parametrize(
    "digest",
    [
        "latest",
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "sha512:" + "a" * 128,
        COMMIT,
        "",
    ],
)
def test_anything_but_a_digest_is_refused(digest: str) -> None:
    with pytest.raises(ReleaseError, match="only an image digest"):
        image_reference(IMAGE, digest)


@pytest.mark.parametrize(
    "image",
    [
        f"{IMAGE}:latest",
        f"{IMAGE}@{DIGEST}",
        "ghcr.io/Tyler-Arciniaga/ospsd-team4-issue-service",
        "ospsd-team4-issue-service",
        "",
    ],
)
def test_image_with_a_tag_digest_or_bad_name_is_refused(image: str) -> None:
    with pytest.raises(ReleaseError, match="lowercase repository"):
        image_reference(image, DIGEST)


def test_registry_with_port_is_accepted() -> None:
    assert image_reference("localhost:5000/team/app", DIGEST) == (
        f"localhost:5000/team/app@{DIGEST}"
    )


def test_refused_digest_sends_nothing_to_render(clock: FakeClock) -> None:
    fake = FakeRender(["live"])

    with pytest.raises(ReleaseError):
        _deploy(fake, clock, digest="latest")

    assert fake.requests == []


# --- deploy -----------------------------------------------------------------


def test_sets_the_service_image_then_deploys_that_digest(clock: FakeClock) -> None:
    fake = FakeRender(["build_in_progress", "update_in_progress", "live"])

    result = _deploy(fake, clock)

    patch, post = fake.requests[0], fake.requests[1]
    assert (patch.method, post.method) == ("PATCH", "POST")
    assert json.loads(patch.content) == {
        "image": {"ownerId": OWNER_ID, "imagePath": REFERENCE}
    }
    assert fake.deploy_body == {"imageUrl": REFERENCE}
    assert result.deploy_id == "dep-new"
    assert result.started_at <= result.finished_at


def test_queued_deploy_is_found_by_digest_once_it_starts(clock: FakeClock) -> None:
    fake = FakeRender(["update_in_progress", "live"], deploy_status=202, queued_polls=2)

    result = _deploy(fake, clock)

    assert result.deploy_id == "dep-new"
    listed = [
        r
        for r in fake.requests
        if r.url.path.endswith("/deploys") and r.method == "GET"
    ]
    assert len(listed) == 3


def test_queued_lookup_ignores_an_older_deploy_of_the_same_digest(
    clock: FakeClock,
) -> None:
    # A rollback redeploys a digest that already has a deploy in the list.
    fake = FakeRender(["live"], deploy_status=202, queued_polls=1)
    fake.listed = [
        {
            "deploy": {
                "id": "dep-old",
                "status": "deactivated",
                "createdAt": _iso(datetime.now(UTC) - timedelta(days=1)),
                "image": {"ref": REFERENCE, "sha": DIGEST},
            }
        }
    ]

    result = _deploy(fake, clock)

    assert result.deploy_id == "dep-new"


def test_queued_lookup_ignores_other_digests(clock: FakeClock) -> None:
    fake = FakeRender([], deploy_status=202, queued_polls=1000)
    fake.listed = [
        {
            "deploy": {
                "id": "dep-other",
                "status": "live",
                "createdAt": _iso(datetime.now(UTC)),
                "image": {"ref": f"{IMAGE}@{OTHER_DIGEST}", "sha": OTHER_DIGEST},
            }
        }
    ]

    with pytest.raises(ReleaseError, match="did not start a deploy"):
        _deploy(fake, clock, timeout_seconds=100, poll_seconds=20)


@pytest.mark.parametrize(
    "failure",
    ["build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"],
)
def test_failed_deploy_is_an_error(clock: FakeClock, failure: str) -> None:
    fake = FakeRender(["update_in_progress", failure])

    with pytest.raises(ReleaseError, match=f"ended as {failure}"):
        _deploy(fake, clock)


def test_deploy_that_never_goes_live_times_out(clock: FakeClock) -> None:
    fake = FakeRender(["update_in_progress"] * 1000)

    with pytest.raises(ReleaseError, match="did not go live in time"):
        _deploy(fake, clock, timeout_seconds=600, poll_seconds=20)

    assert clock.now >= 600
    assert clock.now < 700


def test_render_refusing_the_image_update_stops_before_any_deploy(
    clock: FakeClock,
) -> None:
    # For example, a service still on the Docker runtime (not bootstrapped).
    fake = FakeRender(["live"], patch_status=400)

    with pytest.raises(ReleaseError, match="set the service image: HTTP 400"):
        _deploy(fake, clock)

    assert [r.method for r in fake.requests] == ["PATCH"]


def test_image_setting_that_did_not_take_is_an_error(clock: FakeClock) -> None:
    fake = FakeRender(["live"], configured_image=f"{IMAGE}@{OTHER_DIGEST}")

    with pytest.raises(ReleaseError, match="after the update"):
        _deploy(fake, clock)


def test_render_refusing_the_deploy_is_an_error(clock: FakeClock) -> None:
    fake = FakeRender(["live"], deploy_status=409)

    with pytest.raises(ReleaseError, match="start a deploy: HTTP 409"):
        _deploy(fake, clock)


@pytest.mark.parametrize("service_id", ["", "abc", "evg-123"])
def test_bad_service_id_is_refused(clock: FakeClock, service_id: str) -> None:
    fake = FakeRender(["live"])

    with pytest.raises(ReleaseError, match="Render service ID"):
        _deploy(fake, clock, service_id=service_id)

    assert fake.requests == []


# --- check-ready ------------------------------------------------------------


class FakeService:
    def __init__(
        self, health: list[int], ready: list[tuple[int, dict[str, Any]]]
    ) -> None:
        self.health = health
        self.ready = ready

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            status = self.health.pop(0) if len(self.health) > 1 else self.health[0]
            return httpx.Response(status, json={"status": "ok"})
        status, body = self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]
        return httpx.Response(status, json=body)


def _ready_body(commit: str, status: str = "ready") -> dict[str, Any]:
    return {
        "status": status,
        "checks": {"config": "ok", "idempotency_store": "ok"},
        "provider": "reachable",
        "commit": commit,
    }


def _wait(fake: FakeService, clock: FakeClock, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "commit": COMMIT,
        "clock": clock,
        "sleep": clock.sleep,
        "log": lambda _: None,
    }
    arguments.update(overrides)
    with httpx.Client(
        base_url="https://service.test", transport=httpx.MockTransport(fake.handler)
    ) as service:
        return wait_until_ready(service, **arguments)


def test_ready_after_waking_and_switching_to_the_new_commit(clock: FakeClock) -> None:
    fake = FakeService(
        health=[503, 503, 200],
        ready=[(200, _ready_body(OLD_COMMIT)), (200, _ready_body(COMMIT))],
    )

    body = _wait(fake, clock)

    assert body["commit"] == COMMIT
    assert len(clock.sleeps) == 3


def test_wrong_commit_until_the_deadline_fails(clock: FakeClock) -> None:
    fake = FakeService(health=[200], ready=[(200, _ready_body(OLD_COMMIT))])

    with pytest.raises(ReleaseError, match=f"reports commit '{OLD_COMMIT}'"):
        _wait(fake, clock, timeout_seconds=60, poll_seconds=10)


def test_not_ready_fails(clock: FakeClock) -> None:
    fake = FakeService(health=[200], ready=[(503, _ready_body(COMMIT, "not_ready"))])

    with pytest.raises(ReleaseError, match="/readyz is 503 'not_ready'"):
        _wait(fake, clock, timeout_seconds=60, poll_seconds=10)


def test_service_that_never_answers_fails(clock: FakeClock) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with (
        httpx.Client(
            base_url="https://service.test", transport=httpx.MockTransport(refuse)
        ) as service,
        pytest.raises(ReleaseError, match="ConnectError"),
    ):
        wait_until_ready(
            service,
            commit=COMMIT,
            clock=clock,
            sleep=clock.sleep,
            timeout_seconds=60,
            poll_seconds=10,
            log=lambda _: None,
        )


def test_short_commit_is_refused(clock: FakeClock) -> None:
    fake = FakeService(health=[200], ready=[(200, _ready_body(COMMIT))])

    with pytest.raises(ReleaseError, match="full 40-character"):
        _wait(fake, clock, commit=COMMIT[:12])


# --- command line -----------------------------------------------------------


@pytest.fixture
def outputs(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "github_output"
    path.write_text("")
    yield path


def test_cli_deploy_writes_outputs_and_never_prints_the_key(
    clock: FakeClock, outputs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRender(["live"])
    environ = {
        "RENDER_API_KEY": API_KEY,
        "RENDER_OWNER_ID": OWNER_ID,
        "GITHUB_OUTPUT": str(outputs),
    }

    status = release.main(
        ["deploy", "--service-id", SERVICE_ID, "--image", IMAGE, "--digest", DIGEST],
        environ=environ,
        transport=httpx.MockTransport(fake.handler),
        clock=clock,
        sleep=clock.sleep,
    )

    assert status == 0
    written = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert written["deploy_id"] == "dep-new"
    assert written.keys() == {"deploy_id", "started_at", "finished_at"}
    assert fake.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"
    printed = capsys.readouterr()
    assert API_KEY not in printed.out + printed.err


def test_cli_deploy_without_an_api_key_fails(
    clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    status = release.main(
        ["deploy", "--service-id", SERVICE_ID, "--image", IMAGE, "--digest", DIGEST],
        environ={"RENDER_OWNER_ID": OWNER_ID},
        transport=httpx.MockTransport(FakeRender(["live"]).handler),
        clock=clock,
        sleep=clock.sleep,
    )

    assert status == 1
    assert "RENDER_API_KEY is not set" in capsys.readouterr().err


def test_cli_refused_digest_exits_non_zero(
    clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    status = release.main(
        ["deploy", "--service-id", SERVICE_ID, "--image", IMAGE, "--digest", "latest"],
        environ={"RENDER_API_KEY": API_KEY, "RENDER_OWNER_ID": OWNER_ID},
        transport=httpx.MockTransport(FakeRender(["live"]).handler),
        clock=clock,
        sleep=clock.sleep,
    )

    assert status == 1
    assert "only an image digest" in capsys.readouterr().err


def test_cli_check_ready(clock: FakeClock) -> None:
    fake = FakeService(health=[200], ready=[(200, _ready_body(COMMIT))])

    status = release.main(
        ["check-ready", "--url", "https://service.test/", "--commit", COMMIT],
        environ={},
        transport=httpx.MockTransport(fake.handler),
        clock=clock,
        sleep=clock.sleep,
    )

    assert status == 0
