"""POST /issues with the real Linear adapter and simulated responses.

A failed create's logged `write_outcome` is "not_applied" only when Linear
certainly created nothing, and "unknown" when an issue may exist.
"""

import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.providers.common import REQUEST_TIMEOUT_SECONDS
from app.providers.linear_provider import CREATE_ISSUE_MUTATION, LINEAR_API_URL
from tests.expected_errors import (
    ACCESS_DENIED_MESSAGE,
    CREDENTIALS_NOT_CONFIGURED,
    UNEXPECTED_RESPONSE,
    error_body,
)
from tests.linear.fake_linear import (
    API_KEY,
    AUTHENTICATION_BODY,
    ISSUE_ID,
    PROVIDER_TEXT,
    RATE_LIMITED_BODY,
    TEAM_ID,
    TITLE,
    FakeLinear,
    assert_no_provider_details,
    created_body,
    fail_with,
    failure_records,
    graphql_error,
    respond,
)


def test_create_issue_returns_id_and_title(
    client: TestClient, linear: FakeLinear
) -> None:
    response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 201
    assert response.json() == {"id": ISSUE_ID, "title": TITLE}
    assert response.headers["Location"] == f"/issues/{ISSUE_ID}"
    [request] = linear.requests
    assert request.method == "POST"
    assert str(request.url) == LINEAR_API_URL
    assert request.headers["Authorization"] == API_KEY
    assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS
    assert linear.payloads == [
        {
            "query": CREATE_ISSUE_MUTATION,
            "variables": {"input": {"teamId": TEAM_ID, "title": TITLE}},
        }
    ]


def test_created_issue_can_be_read_back(client: TestClient, linear: FakeLinear) -> None:
    created = client.post("/issues", json={"title": TITLE})
    fetched = client.get(created.headers["Location"])

    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert [payload["variables"] for payload in linear.payloads] == [
        {"input": {"teamId": TEAM_ID, "title": TITLE}},
        {"id": ISSUE_ID},
    ]


def test_create_uses_the_optional_workflow_state(
    client: TestClient, linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_id = "5c2d8e4f-1a3b-4c5d-8e9f-0a1b2c3d4e5f"
    monkeypatch.setenv("LINEAR_STATE_ID", state_id)

    client.post("/issues", json={"title": TITLE})

    assert linear.payloads[0]["variables"]["input"] == {
        "teamId": TEAM_ID,
        "title": TITLE,
        "stateId": state_id,
    }


def test_create_sends_the_title_unchanged(
    client: TestClient, linear: FakeLinear
) -> None:
    title = "  padded title  "

    response = client.post("/issues", json={"title": title})

    assert response.json()["title"] == title
    assert linear.payloads[0]["variables"]["input"]["title"] == title


@pytest.mark.parametrize(
    "body", [{"title": ""}, {"title": "   "}, {"title": "x" * 257}, {}]
)
def test_invalid_body_never_reaches_linear(
    client: TestClient, linear: FakeLinear, body: dict[str, str]
) -> None:
    response = client.post("/issues", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert linear.requests == []


@pytest.mark.parametrize(
    ("variable", "error", "cause"),
    [
        ("LINEAR_API_KEY", CREDENTIALS_NOT_CONFIGURED, "missing_credentials"),
        (
            "LINEAR_TEAM_ID",
            error_body(
                "service_not_configured", "Issue tracker target team not configured"
            ),
            "missing_target_team",
        ),
    ],
)
def test_missing_configuration_returns_503_and_sends_nothing(
    client: TestClient,
    linear: FakeLinear,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    variable: str,
    error: dict[str, Any],
    cause: str,
) -> None:
    monkeypatch.delenv(variable)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 503
    assert response.json() == error
    assert linear.requests == []
    [record] = failure_records(caplog)
    assert (record["cause"], record["write_outcome"]) == (cause, "not_applied")
    assert record["provider_involved"] is False


@pytest.mark.parametrize(
    ("status", "body", "expected", "code", "write_outcome"),
    [
        # Rejected before anything was written.
        (401, AUTHENTICATION_BODY, 403, "access_denied", "not_applied"),
        (403, b"private", 403, "access_denied", "not_applied"),
        (403, graphql_error("FORBIDDEN"), 403, "access_denied", "not_applied"),
        (429, RATE_LIMITED_BODY, 502, "provider_error", "not_applied"),
        (429, b"private", 502, "provider_error", "not_applied"),
        (400, RATE_LIMITED_BODY, 502, "provider_error", "not_applied"),
        (200, RATE_LIMITED_BODY, 502, "provider_error", "not_applied"),
        (
            400,
            graphql_error("GRAPHQL_VALIDATION_FAILED"),
            502,
            "provider_error",
            "not_applied",
        ),
        # HTTP 200 carrying a known rejection, e.g. an unknown team ID.
        (200, graphql_error("INPUT_ERROR"), 502, "provider_error", "not_applied"),
        (
            200,
            graphql_error("AUTHENTICATION_ERROR"),
            403,
            "access_denied",
            "not_applied",
        ),
        # Outcome unknown.
        (200, graphql_error("FORBIDDEN"), 502, "provider_error", "unknown"),
        (200, graphql_error("INTERNAL_SERVER_ERROR"), 502, "provider_error", "unknown"),
        (503, b"private", 502, "provider_error", "unknown"),
    ],
)
def test_linear_failures_report_the_write_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    status: int,
    body: Any,
    expected: int,
    code: str,
    write_outcome: str,
) -> None:
    linear.handler = respond(status, body)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == expected
    assert response.json()["error"]["code"] == code
    assert len(linear.requests) == 1
    [record] = failure_records(caplog)
    assert record["write_outcome"] == write_outcome
    assert record["provider_involved"] is True
    assert_no_provider_details(response.text + caplog.text)


def test_rejected_api_key_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "wrong-key")

    response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 403
    assert response.json() == error_body("access_denied", ACCESS_DENIED_MESSAGE)


def test_input_error_is_a_rejected_request_not_a_missing_issue(
    client: TestClient, linear: FakeLinear
) -> None:
    # On create, INPUT_ERROR means our configuration (e.g. the team) is wrong.
    linear.handler = respond(200, graphql_error("INPUT_ERROR"))

    response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 502
    assert response.json() == error_body(
        "provider_error", "The issue tracker rejected the request"
    )


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        {},
        {"data": {"issueCreate": None}},
        {"data": {"issueCreate": {"success": False, "issue": None}}},
        {"data": {"issueCreate": {"success": True, "issue": None}}},
        created_body(issue_id="short"),
        {"data": {"issueCreate": {"success": True, "issue": {"id": ISSUE_ID}}}},
    ],
)
def test_unusable_success_response_returns_502_with_unknown_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    body: Any,
) -> None:
    linear.handler = respond(200, body)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE
    assert failure_records(caplog)[0]["write_outcome"] == "unknown"


@pytest.mark.parametrize(
    ("exc", "write_outcome"),
    [
        # The connection was never made, so nothing reached Linear.
        (httpx.ConnectError(PROVIDER_TEXT), "not_applied"),
        (httpx.ConnectTimeout(PROVIDER_TEXT), "not_applied"),
        (httpx.PoolTimeout(PROVIDER_TEXT), "not_applied"),
        # The request may have reached Linear before the failure.
        (httpx.ReadTimeout(PROVIDER_TEXT), "unknown"),
        (httpx.WriteTimeout(PROVIDER_TEXT), "unknown"),
        (httpx.ReadError(PROVIDER_TEXT), "unknown"),
        (httpx.RemoteProtocolError(PROVIDER_TEXT), "unknown"),
    ],
)
def test_network_failure_reports_the_write_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
    write_outcome: str,
) -> None:
    linear.handler = fail_with(exc)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.post("/issues", json={"title": TITLE})

    assert response.status_code == 502
    assert failure_records(caplog)[0]["write_outcome"] == write_outcome
    assert_no_provider_details(response.text + caplog.text)


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
