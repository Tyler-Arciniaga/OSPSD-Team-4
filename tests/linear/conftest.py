"""Linear tests run the service with ISSUE_PROVIDER=linear.

Requests go through the real startup (`lifespan`), cache, routes and error
handlers. Only Linear's HTTP transport is replaced, by `FakeLinear`.
"""

from pathlib import Path

import pytest

from app import dependencies
from tests.linear.fake_linear import API_KEY, TEAM_ID, FakeLinear, simulated_provider


@pytest.fixture
def linear() -> FakeLinear:
    return FakeLinear()


@pytest.fixture(autouse=True)
def linear_service(
    monkeypatch: pytest.MonkeyPatch, linear: FakeLinear, tmp_path: Path
) -> None:
    """Configure the service for Linear and route its HTTP client to the fake."""
    monkeypatch.setenv("ISSUE_PROVIDER", "linear")
    monkeypatch.setenv("LINEAR_API_KEY", API_KEY)
    monkeypatch.setenv("LINEAR_TEAM_ID", TEAM_ID)
    monkeypatch.delenv("LINEAR_STATE_ID", raising=False)
    monkeypatch.setenv("IDEMPOTENCY_DB_PATH", str(tmp_path / "keys.sqlite3"))
    monkeypatch.setattr(
        dependencies, "LinearIssueProvider", lambda: simulated_provider(linear)
    )
