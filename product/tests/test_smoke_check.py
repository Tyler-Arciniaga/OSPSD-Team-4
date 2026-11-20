"""The post-deployment smoke check, run against the app in process.

This proves the script's checks, not a deployment: the release workflow runs
it against the deployed service.
"""

from collections.abc import Iterator

import httpx
import pytest
from calendar_contract import CalendarError
from calendar_contract import ErrorCode as CalendarCode
from fastapi.testclient import TestClient
from scripts import smoke_check

from ospsd_team4_product.main import app
from tests.test_focus_day import FAKES, Service

COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[Service]:
    for name, value in FAKES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GIT_SHA", COMMIT)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    with TestClient(app) as client:
        yield Service(client)


def no_wait(_: float) -> None:
    raise AssertionError("must not wait")


@pytest.mark.parametrize("mode", ["fakes", "live"])
def test_passes_against_a_working_service(service: Service, mode: str) -> None:
    lines = smoke_check.run(service.client, commit=COMMIT, mode=mode, sleep=no_wait)

    assert lines[0] == f"ready with commit {COMMIT}"
    assert lines[-1] == "invalid request: 422, rejected in the validate step"


def test_live_mode_creates_no_event_and_reuses_one_doc(service: Service) -> None:
    smoke_check.run(service.client, commit=COMMIT, mode="live", sleep=no_wait)
    smoke_check.run(service.client, commit=COMMIT, mode="live", sleep=no_wait)

    assert service.calendar.events == {}
    assert list(service.storage.files) == ["/standups/2000-01-03-release-smoke.md"]


def test_fakes_mode_checks_the_replay(service: Service) -> None:
    smoke_check.run(service.client, commit=COMMIT, mode="fakes", sleep=no_wait)

    assert service.calendar.failures.calls("create") == 3
    assert service.calendar.failures.calls("get") == 3


def test_a_failing_step_fails_the_check(service: Service) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_UNAVAILABLE, "secret", retryable=True),
    )

    with pytest.raises(smoke_check.SmokeError, match="create_events") as raised:
        smoke_check.run(service.client, commit=COMMIT, mode="fakes", sleep=no_wait)

    assert "secret" not in str(raised.value)


def test_a_different_commit_is_not_ready(service: Service) -> None:
    ticks = iter([0.0, 0.0, 1.0])

    with pytest.raises(smoke_check.SmokeError, match="not ready"):
        smoke_check.wait_until_ready(
            service.client,
            "f" * 40,
            timeout=0.5,
            clock=lambda: next(ticks),
            sleep=lambda _: None,
        )


def test_an_unreachable_service_is_reported() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x")
    ticks = iter([0.0, 1.0])

    with pytest.raises(smoke_check.SmokeError, match="ConnectError"):
        smoke_check.wait_until_ready(
            client, COMMIT, timeout=0.5, clock=lambda: next(ticks), sleep=lambda _: None
        )
