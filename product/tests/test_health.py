"""Liveness, readiness and startup configuration checks."""

import pytest
from fastapi.testclient import TestClient

from ospsd_team4_product.config import ConfigError
from ospsd_team4_product.main import app, deployed_commit
from tests.test_focus_day import FAKES


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FAKES.items():
        monkeypatch.setenv(name, value)


@pytest.mark.usefixtures("configured")
def test_healthz_and_readyz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {"config": "ok"},
        "commit": "0123456789abcdef0123456789abcdef01234567",
    }


@pytest.mark.usefixtures("configured")
def test_startup_fails_on_bad_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CALENDAR_PROVIDER")

    with pytest.raises(ConfigError, match="CALENDAR_PROVIDER"), TestClient(app):
        pass


def test_deployed_commit_rejects_non_commit_values() -> None:
    assert deployed_commit({"GIT_SHA": "not-a-commit; rm -rf"}) == "unknown"
    assert (
        deployed_commit({"RENDER_GIT_COMMIT": "abcdef1", "GIT_SHA": "1234567"})
        == "abcdef1"
    )
    assert deployed_commit({}) == "unknown"


@pytest.mark.usefixtures("configured")
def test_startup_fails_when_a_provider_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Like Dropbox without credentials: the constructor raises.
    monkeypatch.setenv("STORAGE_PROVIDER", "tests.helpers:failing_factory")

    with (
        pytest.raises(ConfigError, match="STORAGE_PROVIDER could not be built"),
        TestClient(app),
    ):
        pass
