"""Liveness and readiness endpoints, and the cached provider check."""

import shutil
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from app import main
from app.dependencies import get_provider_probe
from app.health import ProviderProbe
from app.providers.trello_provider import TrelloIssueProvider
from tests.conftest import FAKE_STARTUP_CONFIG


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingCheck:
    """A provider check with a fixed answer that counts its calls."""

    def __init__(self, result: bool | Exception) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _use_probe(monkeypatch: pytest.MonkeyPatch, probe: ProviderProbe) -> None:
    supply: Callable[[], ProviderProbe] = lambda: probe
    monkeypatch.setitem(main.app.dependency_overrides, get_provider_probe, supply)


def _assert_no_fake_value(text: str) -> None:
    for value in FAKE_STARTUP_CONFIG.values():
        assert value not in text


def test_healthz_reports_the_process_is_up(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_does_not_contact_the_provider(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    check = CountingCheck(True)
    _use_probe(monkeypatch, ProviderProbe(check))

    client.get("/healthz")

    assert check.calls == 0


@pytest.mark.parametrize(
    ("result", "provider"),
    [
        (True, "reachable"),
        (False, "unreachable"),
        (RuntimeError("provider down"), "unreachable"),
    ],
)
def test_readyz_is_ready_whatever_the_provider_reports(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    result: bool | Exception,
    provider: str,
) -> None:
    _use_probe(monkeypatch, ProviderProbe(CountingCheck(result)))

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"config": "ok", "idempotency_store": "ok"},
        "provider": provider,
    }
    _assert_no_fake_value(response.text)


def test_readyz_uses_the_adapter_check_built_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without overrides, readiness asks the real adapter (here: Trello is down)."""
    monkeypatch.setenv("ISSUE_PROVIDER", "trello")
    monkeypatch.setattr(TrelloIssueProvider, "ping", lambda self: False)

    with TestClient(main.app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["provider"] == "unreachable"


def test_readyz_fails_when_the_key_store_cannot_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = tmp_path / "store"
    directory.mkdir()
    monkeypatch.setenv("IDEMPOTENCY_DB_PATH", str(directory / "keys.sqlite3"))
    _use_probe(monkeypatch, ProviderProbe(CountingCheck(True)))

    with TestClient(main.app) as client:
        # The directory was valid at startup and disappears afterwards.
        shutil.rmtree(directory)
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"config": "ok", "idempotency_store": "failed"},
        "provider": "reachable",
    }
    assert str(directory) not in response.text


def test_provider_is_checked_at_most_once_per_interval() -> None:
    clock = FakeClock()
    check = CountingCheck(True)
    probe = ProviderProbe(check, clock=clock)

    assert probe.status() == "reachable"
    clock.now += 29.9
    check.result = False
    assert probe.status() == "reachable"
    assert check.calls == 1

    clock.now += 0.1
    assert probe.status() == "unreachable"
    assert check.calls == 2


def test_status_is_unknown_while_the_first_check_runs() -> None:
    started, release = Event(), Event()

    def slow_check() -> bool:
        started.set()
        release.wait(timeout=5)
        return True

    probe = ProviderProbe(slow_check)
    first = Thread(target=probe.status)
    first.start()
    assert started.wait(timeout=5)

    # A second caller neither waits nor starts another check.
    assert probe.status() == "unknown"

    release.set()
    first.join(timeout=5)
    assert probe.status() == "reachable"


def test_readyz_ignores_the_key_store_for_linear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Linear never opens the key store, so a broken store does not matter."""
    directory = tmp_path / "store"
    directory.mkdir()
    monkeypatch.setenv("ISSUE_PROVIDER", "linear")
    monkeypatch.setenv("IDEMPOTENCY_DB_PATH", str(directory / "keys.sqlite3"))
    _use_probe(monkeypatch, ProviderProbe(CountingCheck(True)))

    with TestClient(main.app) as client:
        shutil.rmtree(directory)
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"config": "ok", "idempotency_store": "not_used"},
        "provider": "reachable",
    }
