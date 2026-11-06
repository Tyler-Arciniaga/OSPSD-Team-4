"""End-to-end GET and POST /issues with ISSUE_PROVIDER=linear (DESIGN.md 6.5).

Requests run through the real startup (`lifespan`), cache, routes and error
handlers. Only Linear's HTTP transport is simulated.
"""

import json
import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import dependencies, main
from app.cache import CachedIssueProvider
from app.linear_provider import LinearIssueProvider
from app.provider import TrelloIssueProvider
from tests.expected_errors import (
    ACCESS_DENIED_MESSAGE,
    CREDENTIALS_NOT_CONFIGURED,
    NOT_FOUND,
    UNEXPECTED_RESPONSE,
    error_body,
)

API_KEY = "lin_api_secret-test-key"
TEAM_ID = "9cfb482a-81e3-4154-b5b9-2c805e70a02d"
ISSUE_ID = "3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"
MISSING_ID = "00000000-0000-4000-8000-000000000000"
TITLE = "Fix login bug"

NOT_FOUND_BODY = {
    "errors": [
        {
            "message": "Entity not found: Issue",
            "extensions": {"code": "INPUT_ERROR", "type": "invalid input"},
        }
    ],
    "data": None,
}


class FakeLinear:
    """A minimal in-memory Linear GraphQL endpoint for get and create."""

    def __init__(self) -> None:
        self.issues: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []
        self.next_ids = iter([ISSUE_ID, "11111111-2222-4333-8444-555555555555"])

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != API_KEY:
            return httpx.Response(
                401, json={"errors": [{"extensions": {"code": "AUTHENTICATION_ERROR"}}]}
            )
        payload = json.loads(request.content)
        self.requests.append(payload)
        variables = payload["variables"]
        if "issueCreate" in payload["query"]:
            issue_id = next(self.next_ids)
            self.issues[issue_id] = variables["input"]["title"]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {"id": issue_id, "title": self.issues[issue_id]},
                        }
                    }
                },
            )
        issue_id = variables["id"]
        if issue_id not in self.issues:
            # Observed Linear behavior: HTTP 200 with an error in the body.
            return httpx.Response(200, json=NOT_FOUND_BODY)
        return httpx.Response(
            200,
            json={
                "data": {
                    "issue": {
                        "id": issue_id,
                        "title": self.issues[issue_id],
                        "trashed": False,
                    }
                }
            },
        )


@pytest.fixture
def linear() -> FakeLinear:
    return FakeLinear()


@pytest.fixture
def closed_adapters() -> list[LinearIssueProvider]:
    return []


@pytest.fixture(autouse=True)
def linear_service(
    monkeypatch: pytest.MonkeyPatch,
    linear: FakeLinear,
    closed_adapters: list[LinearIssueProvider],
    tmp_path: Any,
) -> None:
    """Configure the service for Linear and route its HTTP client to the fake."""
    monkeypatch.setenv("ISSUE_PROVIDER", "linear")
    monkeypatch.setenv("LINEAR_API_KEY", API_KEY)
    monkeypatch.setenv("LINEAR_TEAM_ID", TEAM_ID)
    monkeypatch.delenv("LINEAR_STATE_ID", raising=False)
    monkeypatch.setenv("IDEMPOTENCY_DB_PATH", str(tmp_path / "keys.sqlite3"))

    class SimulatedLinearIssueProvider(LinearIssueProvider):
        def __init__(self) -> None:
            # Look the handler up per request so a test can replace it.
            super().__init__(
                transport=httpx.MockTransport(lambda request: linear.handle(request))
            )

        def close(self) -> None:
            closed_adapters.append(self)
            super().close()

    monkeypatch.setattr(
        dependencies, "LinearIssueProvider", SimulatedLinearIssueProvider
    )


def test_startup_selects_cached_linear_adapter(client: TestClient) -> None:
    provider = main.app.state.issue_provider
    assert isinstance(provider, CachedIssueProvider)
    assert isinstance(provider._provider, LinearIssueProvider)


def test_create_then_get_round_trip(client: TestClient, linear: FakeLinear) -> None:
    created = client.post("/issues", json={"title": TITLE})

    assert created.status_code == 201
    assert created.json() == {"id": ISSUE_ID, "title": TITLE}
    assert created.headers["Location"] == f"/issues/{ISSUE_ID}"

    fetched = client.get(created.headers["Location"])

    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert fetched.headers["ETag"].startswith('"')
    assert linear.requests[0]["variables"] == {
        "input": {"teamId": TEAM_ID, "title": TITLE}
    }
    assert linear.requests[1]["variables"] == {"id": ISSUE_ID}


def test_repeated_get_is_served_from_the_cache(
    client: TestClient, linear: FakeLinear
) -> None:
    linear.issues[ISSUE_ID] = TITLE

    first = client.get(f"/issues/{ISSUE_ID}")
    second = client.get(f"/issues/{ISSUE_ID}")

    assert first.json() == second.json() == {"id": ISSUE_ID, "title": TITLE}
    assert first.headers["ETag"] == second.headers["ETag"]
    assert len(linear.requests) == 1


def test_get_missing_issue_returns_404(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get(f"/issues/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    [record] = [json.loads(r.getMessage()) for r in caplog.records]
    assert record == {
        "level": "WARNING",
        "operation": "get_issue",
        "error": "not_found",
        "status": 404,
        "provider_involved": True,
        "cause": "graphql_code=INPUT_ERROR",
        "write_outcome": None,
    }


def test_get_malformed_id_returns_404_without_contacting_linear(
    client: TestClient, linear: FakeLinear
) -> None:
    response = client.get("/issues/does-not-exist")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert linear.requests == []


def test_rejected_api_key_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "wrong-key")

    for response in (
        client.get(f"/issues/{ISSUE_ID}"),
        client.post("/issues", json={"title": TITLE}),
    ):
        assert response.status_code == 403
        assert response.json() == error_body("access_denied", ACCESS_DENIED_MESSAGE)


def test_missing_configuration_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, linear: FakeLinear
) -> None:
    monkeypatch.delenv("LINEAR_TEAM_ID")
    no_team = client.post("/issues", json={"title": TITLE})
    monkeypatch.delenv("LINEAR_API_KEY")
    no_key = client.get(f"/issues/{ISSUE_ID}")

    assert no_team.status_code == 503
    assert no_team.json() == error_body(
        "service_not_configured", "Issue tracker target team not configured"
    )
    assert no_key.status_code == 503
    assert no_key.json() == CREDENTIALS_NOT_CONFIGURED
    assert linear.requests == []


def test_invalid_create_body_never_reaches_linear(
    client: TestClient, linear: FakeLinear
) -> None:
    for body in ({"title": ""}, {"title": "   "}, {"title": "x" * 257}, {}):
        response = client.post("/issues", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"
    assert linear.requests == []


def test_unusable_create_response_is_502_with_unknown_outcome(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        linear, "handle", lambda request: httpx.Response(200, content=b"not json")
    )

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE
    [record] = [json.loads(r.getMessage()) for r in caplog.records]
    assert record["write_outcome"] == "unknown"


def test_idempotency_key_returns_503_and_creates_nothing(
    client: TestClient, linear: FakeLinear
) -> None:
    response = client.post(
        "/issues",
        json={"title": TITLE},
        headers={"Idempotency-Key": "3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"},
    )

    assert response.status_code == 503
    assert response.json() == error_body(
        "service_not_configured", "Idempotent creation is not available"
    )
    assert linear.requests == []


def test_shutdown_closes_the_linear_client(
    closed_adapters: list[LinearIssueProvider],
) -> None:
    with TestClient(main.app):
        assert closed_adapters == []
    assert len(closed_adapters) == 1


def test_responses_and_logs_never_contain_the_api_key(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        responses = [
            client.get(f"/issues/{MISSING_ID}"),
            client.post("/issues", json={"title": TITLE}),
        ]

    for response in responses:
        assert API_KEY not in response.text
        assert "Entity not found" not in response.text
    assert API_KEY not in caplog.text


# --- provider selection ----------------------------------------------------


@pytest.mark.parametrize("value", ["", "trello"])
def test_trello_is_the_default_provider(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ISSUE_PROVIDER", value)

    with TestClient(main.app):
        provider = main.app.state.issue_provider
        assert isinstance(provider._provider, TrelloIssueProvider)


def test_unknown_provider_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUE_PROVIDER", "jira")

    with pytest.raises(ValueError, match="ISSUE_PROVIDER"), TestClient(main.app):
        pass
