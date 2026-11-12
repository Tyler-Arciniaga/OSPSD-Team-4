"""LinearIssueProvider.list_issues with simulated GraphQL responses, no network."""

import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.providers.common import REQUEST_TIMEOUT_SECONDS
from app.providers.linear_provider import LINEAR_API_URL, LIST_ISSUES_QUERY
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import Issue
from tests.linear.fake_linear import (
    API_KEY,
    AUTHENTICATION_BODY,
    ISSUE_ID,
    PROVIDER_TEXT,
    RATE_LIMITED_BODY,
    TEAM_ID,
    TITLE,
    FakeLinear,
    Handler,
    assert_no_provider_details,
    fail_with,
    failure_records,
    graphql_error,
    list_body,
    node,
    respond,
    simulated_provider,
)

OTHER_ID = "11111111-2222-4333-8444-555555555555"

REJECTED = "The issue tracker rejected the request"
RATE_LIMITED = "The issue tracker is rate limiting requests"
UNAVAILABLE = "The issue tracker is currently unavailable"
UNEXPECTED = "The issue tracker returned an unexpected response"
ACCESS_DENIED = "The issue tracker denied access to this issue"


def list_error(linear: FakeLinear) -> ServiceError:
    with pytest.raises(ServiceError) as exc_info:
        simulated_provider(linear).list_issues(limit=50)
    return exc_info.value


def test_list_queries_the_target_team(linear: FakeLinear) -> None:
    linear.handler = respond(200, list_body(node()))

    issues = simulated_provider(linear).list_issues(limit=7)

    assert issues == [Issue(id=ISSUE_ID, title=TITLE)]
    [request] = linear.requests
    assert request.method == "POST"
    assert str(request.url) == LINEAR_API_URL
    assert request.headers["Authorization"] == API_KEY
    assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS
    # The limit is sent to Linear, so it never returns more than needed.
    assert linear.payloads == [
        {"query": LIST_ISSUES_QUERY, "variables": {"teamId": TEAM_ID, "first": 7}}
    ]


def test_list_keeps_linear_order_and_drops_issue_details(linear: FakeLinear) -> None:
    newest = node(OTHER_ID, "Newest") | {"description": "Private description"}
    linear.handler = respond(200, list_body(newest, node(ISSUE_ID, "Older")))

    issues = simulated_provider(linear).list_issues(limit=50)

    assert issues == [
        Issue(id=OTHER_ID, title="Newest"),
        Issue(id=ISSUE_ID, title="Older"),
    ]
    assert all(set(issue.model_dump()) == {"id", "title"} for issue in issues)


def test_trashed_issues_are_left_out(linear: FakeLinear) -> None:
    # Archived issues are excluded in the query; a trashed one that still
    # arrives is dropped too.
    linear.handler = respond(
        200,
        list_body(node(OTHER_ID, "Trashed", trashed=True), node(ISSUE_ID, TITLE)),
    )

    assert simulated_provider(linear).list_issues(limit=50) == [
        Issue(id=ISSUE_ID, title=TITLE)
    ]


def test_missing_trashed_flag_counts_as_not_trashed(linear: FakeLinear) -> None:
    linear.handler = respond(200, list_body(node(trashed=None)))

    assert simulated_provider(linear).list_issues(limit=50) == [
        Issue(id=ISSUE_ID, title=TITLE)
    ]


def test_list_never_returns_more_than_the_limit(linear: FakeLinear) -> None:
    linear.handler = respond(200, list_body(node(OTHER_ID), node(ISSUE_ID)))

    assert len(simulated_provider(linear).list_issues(limit=1)) == 1


def test_list_reflects_created_and_deleted_issues(linear: FakeLinear) -> None:
    adapter = simulated_provider(linear)
    first = adapter.create_issue("First")
    second = adapter.create_issue("Second")

    assert adapter.list_issues(limit=50) == [second, first]

    adapter.delete_issue(second.id)

    assert adapter.list_issues(limit=50) == [first]


def test_empty_team_returns_no_issues(linear: FakeLinear) -> None:
    linear.handler = respond(200, list_body())

    assert simulated_provider(linear).list_issues(limit=50) == []


def test_every_list_queries_linear(linear: FakeLinear) -> None:
    linear.handler = respond(200, list_body(node()))
    adapter = simulated_provider(linear)

    adapter.list_issues(limit=50)
    adapter.list_issues(limit=50)

    assert len(linear.requests) == 2


@pytest.mark.parametrize(
    ("handler", "code", "message", "cause"),
    [
        (
            respond(401, AUTHENTICATION_BODY),
            ErrorCode.ACCESS_DENIED,
            ACCESS_DENIED,
            "provider_status=401",
        ),
        (
            respond(200, graphql_error("AUTHENTICATION_ERROR")),
            ErrorCode.ACCESS_DENIED,
            ACCESS_DENIED,
            "graphql_code=AUTHENTICATION_ERROR",
        ),
        (
            respond(429, RATE_LIMITED_BODY),
            ErrorCode.PROVIDER_ERROR,
            RATE_LIMITED,
            "provider_status=429",
        ),
        (
            respond(200, graphql_error("RATELIMITED")),
            ErrorCode.PROVIDER_ERROR,
            RATE_LIMITED,
            "graphql_code=RATELIMITED",
        ),
        (
            respond(503, b"<html>down</html>"),
            ErrorCode.PROVIDER_ERROR,
            UNAVAILABLE,
            "provider_status=503",
        ),
        # A wrong team is a configuration problem, never a missing issue.
        (
            respond(200, graphql_error("INPUT_ERROR")),
            ErrorCode.PROVIDER_ERROR,
            REJECTED,
            "graphql_code=INPUT_ERROR",
        ),
        (
            respond(200, graphql_error("SOMETHING_NEW")),
            ErrorCode.PROVIDER_ERROR,
            REJECTED,
            "graphql_error",
        ),
        (
            respond(400, None),
            ErrorCode.PROVIDER_ERROR,
            REJECTED,
            "provider_status=400",
        ),
        (
            fail_with(httpx.ReadTimeout("timed out")),
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker did not respond in time",
            "timeout",
        ),
        (
            fail_with(httpx.ConnectError("refused")),
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker could not be reached",
            "ConnectError",
        ),
    ],
)
def test_failures_are_translated(
    linear: FakeLinear, handler: Handler, code: ErrorCode, message: str, cause: str
) -> None:
    linear.handler = handler

    error = list_error(linear)

    assert error.code is code
    assert error.message == message
    assert error.cause == cause
    assert error.provider_involved
    assert_no_provider_details(f"{error} {error.cause}")


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        {},
        {"data": None},
        {"data": {"team": None}},
        {"data": {"team": {"issues": None}}},
        {"data": {"team": {"issues": {"nodes": None}}}},
        list_body(None),
        list_body({"title": TITLE}),
        list_body(node(issue_id="TYL-5")),
        list_body(node(issue_id=ISSUE_ID.upper())),
        list_body({"id": ISSUE_ID, "title": None}),
        list_body(node() | {"trashed": "yes"}),
        list_body(node(OTHER_ID), {"id": ISSUE_ID}),
    ],
)
def test_unusable_responses_are_provider_errors(linear: FakeLinear, body: Any) -> None:
    linear.handler = respond(200, body)

    error = list_error(linear)

    assert error.code is ErrorCode.PROVIDER_ERROR
    assert error.message == UNEXPECTED
    assert error.provider_involved
    assert PROVIDER_TEXT not in error.message


def test_missing_key_fails_without_contacting_linear(
    linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY")

    error = list_error(linear)

    assert error.code is ErrorCode.SERVICE_NOT_CONFIGURED
    assert error.message == "Issue tracker credentials not configured"
    assert not error.provider_involved
    assert linear.requests == []


def test_missing_team_fails_without_contacting_linear(
    linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_TEAM_ID")

    error = list_error(linear)

    assert error.code is ErrorCode.SERVICE_NOT_CONFIGURED
    assert error.message == "Issue tracker target team not configured"
    assert not error.provider_involved
    assert linear.requests == []


def test_get_issues_lists_through_the_service(
    client: TestClient, linear: FakeLinear
) -> None:
    # The real startup (ISSUE_PROVIDER=linear), cache and route.
    first = client.post("/issues", json={"title": "First"}).json()
    second = client.post("/issues", json={"title": "Second"}).json()

    listed = client.get("/issues", params={"limit": 1})
    again = client.get("/issues")

    assert listed.status_code == 200
    assert listed.json() == {"issues": [second]}
    assert again.json() == {"issues": [second, first]}
    assert [payload["variables"]["first"] for payload in linear.payloads[2:]] == [
        1,
        50,
    ]


def test_get_issues_failure_is_logged_as_a_read(
    client: TestClient, linear: FakeLinear, caplog: pytest.LogCaptureFixture
) -> None:
    linear.handler = respond(200, graphql_error("INPUT_ERROR"))

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.get("/issues")

    assert response.status_code == 502
    assert response.json() == {"error": {"code": "provider_error", "message": REJECTED}}
    assert failure_records(caplog) == [
        {
            "level": "ERROR",
            "operation": "list_issues",
            "error": "provider_error",
            "status": 502,
            "provider_involved": True,
            "cause": "graphql_code=INPUT_ERROR",
            "write_outcome": None,
        }
    ]
    assert_no_provider_details(response.text + caplog.text)
