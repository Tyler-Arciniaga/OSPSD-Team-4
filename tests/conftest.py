from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

# Fake values that let the service pass startup validation. They are not real
# credentials, and tests can search output for them to prove nothing leaks.
FAKE_STARTUP_CONFIG = {
    "TRELLO_API_KEY": "fake-trello-key-3f9a",
    "TRELLO_TOKEN": "fake-trello-token-8c2e",
    "TRELLO_LIST_ID": "5f1c0a2b3d4e5f6071829304",
    "LINEAR_API_KEY": "fake-linear-key-71bd",
    "LINEAR_TEAM_ID": "0b5a3d9e-1c2f-4a6b-8d7e-9f0a1b2c3d4e",
}


@pytest.fixture(autouse=True)
def startup_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give every test a configuration that passes startup validation.

    Tests that check the adapters' per-call configuration checks remove
    variables after the service has started.
    """
    for name, value in FAKE_STARTUP_CONFIG.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("IDEMPOTENCY_DB_PATH", str(tmp_path / "idempotency.sqlite3"))
    # Readiness reports the build's commit; tests start from an unknown one.
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)


@pytest.fixture
def raise_server_exceptions() -> bool:
    """Normally surface unexpected application errors as test failures."""
    return True


@pytest.fixture
def client(raise_server_exceptions: bool) -> Iterator[TestClient]:
    """Run startup and shutdown for each test, giving it a fresh cache."""
    with TestClient(
        main.app, raise_server_exceptions=raise_server_exceptions
    ) as test_client:
        yield test_client
