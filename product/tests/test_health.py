"""Liveness, readiness and startup configuration checks."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from ospsd_team4_product.config import ConfigError
from ospsd_team4_product.main import app, deployed_commit
from ospsd_team4_product.store import PostgresStateStore, StoreError
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


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "Missing required configuration: STATE_DB_URL"),
        ("sqlite:///state.db", "STATE_DB_URL must be a postgresql:// URL"),
    ],
)
def test_startup_fails_on_bad_state_db_url(
    monkeypatch: pytest.MonkeyPatch, value: str | None, message: str
) -> None:
    if value is None:
        monkeypatch.delenv("STATE_DB_URL")
    else:
        monkeypatch.setenv("STATE_DB_URL", value)

    with pytest.raises(ConfigError, match=message), TestClient(app):
        pass


@pytest.mark.usefixtures("configured")
def test_startup_fails_when_the_state_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(
        "STATE_DB_URL", "postgresql://focus:sk-SECRET-pw@127.0.0.1:1/focus_day"
    )

    with (
        pytest.raises(ConfigError) as raised,
        TestClient(app),
    ):
        pass

    assert str(raised.value) == ("Cannot connect to state database; check STATE_DB_URL")
    assert "SECRET" not in caplog.text
    assert "startup_config_invalid" in caplog.text


@pytest.mark.usefixtures("configured")
def test_store_is_built_at_startup_and_closed_on_shutdown(
    monkeypatch: pytest.MonkeyPatch, postgres_url: str
) -> None:
    monkeypatch.setenv("STATE_DB_URL", postgres_url)

    with TestClient(app) as client:
        store = client.app.state.store  # type: ignore[attr-defined]
        assert isinstance(store, PostgresStateStore)
        assert store.get_plan("tyler", date(2026, 11, 18)) is None

    with pytest.raises(StoreError):
        store.get_plan("tyler", date(2026, 11, 18))


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
