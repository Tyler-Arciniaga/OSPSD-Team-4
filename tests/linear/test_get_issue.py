"""GET /issues/{issue_id} with the real Linear adapter and simulated responses."""

import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.providers.common import REQUEST_TIMEOUT_SECONDS
from app.providers.linear_provider import GET_ISSUE_QUERY, LINEAR_API_URL
from tests.expected_errors import (
    ACCESS_DENIED_MESSAGE,
    CREDENTIALS_NOT_CONFIGURED,
    NOT_FOUND,
    UNEXPECTED_RESPONSE,
    ErrorJson,
    error_body,
)
from tests.linear.fake_linear import (
    API_KEY,
    AUTHENTICATION_BODY,
    ISSUE_ID,
    MISSING_ID,
    NOT_FOUND_BODY,
    PROVIDER_TEXT,
    RATE_LIMITED_BODY,
    TITLE,
    FakeLinear,
    assert_no_provider_details,
    fail_with,
    failure_records,
    graphql_error,
    issue_body,
    respond,
)

REJECTED = error_body("provider_error", "The issue tracker rejected the request")
RATE_LIMITED = error_body(
    "provider_error", "The issue tracker is rate limiting requests"
)
UNAVAILABLE = error_body("provider_error", "The issue tracker is currently unavailable")
ACCESS_DENIED = error_body("access_denied", ACCESS_DENIED_MESSAGE)


def test_get_issue_returns_id_and_title(client: TestClient, linear: FakeLinear) -> None:
    body = issue_body()
    body["data"]["issue"]["description"] = "Private description"
    linear.handler = respond(200, body)

    response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == 200
    assert response.json() == {"id": ISSUE_ID, "title": TITLE}
    assert response.headers["ETag"].startswith('"')
    [request] = linear.requests
    assert request.method == "POST"
    assert str(request.url) == LINEAR_API_URL
    # Personal API keys are sent without a "Bearer" prefix.
    assert request.headers["Authorization"] == API_KEY
    assert request.headers["Content-Type"] == "application/json"
    assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS
    assert linear.payloads == [
        {"query": GET_ISSUE_QUERY, "variables": {"id": ISSUE_ID}}
    ]


def test_repeated_get_is_served_from_the_cache(
    client: TestClient, linear: FakeLinear
) -> None:
    linear.issues[ISSUE_ID] = TITLE

    first = client.get(f"/issues/{ISSUE_ID}")
    second = client.get(f"/issues/{ISSUE_ID}")

    assert first.json() == second.json() == {"id": ISSUE_ID, "title": TITLE}
    assert first.headers["ETag"] == second.headers["ETag"]
    assert len(linear.requests) == 1


def test_get_missing_issue_on_http_200_returns_404(
    client: TestClient, linear: FakeLinear, caplog: pytest.LogCaptureFixture
) -> None:
    # Linear reports a missing issue with HTTP 200 and an error in the body.
    linear.handler = respond(200, NOT_FOUND_BODY)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get(f"/issues/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert failure_records(caplog) == [
        {
            "level": "WARNING",
            "operation": "get_issue",
            "error": "not_found",
            "status": 404,
            "provider_involved": True,
            "cause": "graphql_code=INPUT_ERROR",
            "write_outcome": None,
        }
    ]


def test_get_trashed_issue_returns_404(
    client: TestClient, linear: FakeLinear, caplog: pytest.LogCaptureFixture
) -> None:
    # Linear keeps a deleted issue in the trash; the service treats it as gone.
    linear.handler = respond(200, issue_body(trashed=True))

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert failure_records(caplog)[0]["cause"] == "issue_trashed"


@pytest.mark.parametrize(
    "issue_id",
    [
        "does-not-exist",
        "TYL-5",  # Linear's short identifier: accepted by Linear, not by us.
        ISSUE_ID.upper(),
        ISSUE_ID[:-1],
        ISSUE_ID + "0",
        ISSUE_ID.replace("-", ""),
        "64f1c0a2b3d4e5f607182930",  # A Trello card ID.
    ],
)
def test_get_malformed_id_returns_404_without_contacting_linear(
    client: TestClient,
    linear: FakeLinear,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    issue_id: str,
) -> None:
    # No credentials either: the ID check must come first.
    monkeypatch.delenv("LINEAR_API_KEY")

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get(f"/issues/{issue_id}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert linear.requests == []
    [record] = failure_records(caplog)
    assert record["cause"] == "malformed_issue_id"
    assert record["provider_involved"] is False


def test_missing_api_key_returns_503(
    client: TestClient, linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY")

    response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == 503
    assert response.json() == CREDENTIALS_NOT_CONFIGURED
    assert linear.requests == []


def test_get_does_not_need_a_team(
    client: TestClient, linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_TEAM_ID")
    linear.issues[ISSUE_ID] = TITLE

    assert client.get(f"/issues/{ISSUE_ID}").status_code == 200


def test_rejected_api_key_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "wrong-key")

    response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == 403
    assert response.json() == ACCESS_DENIED


@pytest.mark.parametrize(
    ("status", "body", "expected", "error", "cause"),
    [
        (401, AUTHENTICATION_BODY, 403, ACCESS_DENIED, "provider_status=401"),
        (
            200,
            graphql_error("AUTHENTICATION_ERROR"),
            403,
            ACCESS_DENIED,
            "graphql_code=AUTHENTICATION_ERROR",
        ),
        (429, RATE_LIMITED_BODY, 502, RATE_LIMITED, "provider_status=429"),
        (
            400,
            graphql_error("RATELIMITED"),
            502,
            RATE_LIMITED,
            "graphql_code=RATELIMITED",
        ),
        (500, b"<html>private</html>", 502, UNAVAILABLE, "provider_status=500"),
        (
            400,
            graphql_error("GRAPHQL_VALIDATION_FAILED"),
            502,
            REJECTED,
            "graphql_error",
        ),
        # Forbidden-entity responses are not yet observed (DESIGN.md 8.1 #2),
        # so they stay a provider error rather than access denied.
        (200, graphql_error("FORBIDDEN"), 502, REJECTED, "graphql_error"),
        (403, b"private", 502, REJECTED, "provider_status=403"),
    ],
)
def test_linear_failures(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    status: int,
    body: Any,
    expected: int,
    error: ErrorJson,
    cause: str,
) -> None:
    linear.handler = respond(status, body)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == expected
    assert response.json() == error
    [record] = failure_records(caplog)
    assert record["cause"] == cause
    assert record["provider_involved"] is True
    assert_no_provider_details(response.text + caplog.text)


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (
            httpx.ConnectError(PROVIDER_TEXT),
            error_body("provider_error", "The issue tracker could not be reached"),
        ),
        (
            httpx.ReadError(PROVIDER_TEXT),
            error_body("provider_error", "The issue tracker could not be reached"),
        ),
        (
            httpx.ConnectTimeout(PROVIDER_TEXT),
            error_body("provider_error", "The issue tracker did not respond in time"),
        ),
        (
            httpx.ReadTimeout(PROVIDER_TEXT),
            error_body("provider_error", "The issue tracker did not respond in time"),
        ),
    ],
)
def test_network_failure_returns_502(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
    error: ErrorJson,
) -> None:
    linear.handler = fail_with(exc)

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == 502
    assert response.json() == error
    assert_no_provider_details(response.text + caplog.text)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        [],
        {},
        {"data": None},
        {"data": {"issue": None}},
        {"data": {"issue": {"id": ISSUE_ID}}},
        {"data": {"issue": {"id": ISSUE_ID, "title": 5}}},
        # A different issue than the one requested.
        issue_body(issue_id=MISSING_ID),
        {"data": {"issue": {"id": ISSUE_ID, "title": TITLE, "trashed": "yes"}}},
    ],
)
def test_unusable_linear_response_returns_502(
    client: TestClient, linear: FakeLinear, body: Any
) -> None:
    linear.handler = respond(200, body)

    response = client.get(f"/issues/{ISSUE_ID}")

    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE
