"""Linear adapter tests with simulated GraphQL responses (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.linear_provider import (
    CREATE_ISSUE_MUTATION,
    GET_ISSUE_QUERY,
    LINEAR_API_URL,
    REQUEST_TIMEOUT_SECONDS,
    LinearIssueProvider,
)
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import Issue, IssueUpdate

API_KEY = "lin_api_secret-test-key"
TEAM_ID = "9cfb482a-81e3-4154-b5b9-2c805e70a02d"
ISSUE_ID = "3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"
TITLE = "Fix login bug"

Handler = Callable[[httpx.Request], httpx.Response]

# Observed from Linear for a missing (or malformed) issue ID: HTTP 200.
NOT_FOUND_BODY = {
    "errors": [
        {
            "message": "Entity not found: Issue",
            "path": ["issue"],
            "extensions": {
                "type": "invalid input",
                "code": "INPUT_ERROR",
                "statusCode": 400,
                "userError": True,
                "userPresentableMessage": "Could not find referenced Issue.",
            },
        }
    ],
    "data": None,
}

# Observed from Linear for an invalid API key: a real HTTP 401.
AUTHENTICATION_BODY = {
    "errors": [
        {
            "message": "Authentication required, not authenticated",
            "extensions": {"code": "AUTHENTICATION_ERROR", "type": "authentication"},
        }
    ]
}

# Documented by Linear for an exceeded rate limit: HTTP 429.
RATE_LIMITED_BODY = {
    "errors": [
        {"message": "Rate limit exceeded", "extensions": {"code": "RATELIMITED"}}
    ]
}


def graphql_error(code: str) -> dict[str, Any]:
    return {
        "errors": [
            {"message": "private provider message", "extensions": {"code": code}}
        ],
        "data": None,
    }


def issue_body(
    issue_id: str = ISSUE_ID, title: str = TITLE, trashed: bool | None = None
) -> dict[str, Any]:
    return {"data": {"issue": {"id": issue_id, "title": title, "trashed": trashed}}}


def created_body(issue_id: str = ISSUE_ID, title: str = TITLE) -> dict[str, Any]:
    return {
        "data": {
            "issueCreate": {"success": True, "issue": {"id": issue_id, "title": title}}
        }
    }


def respond(status: int, body: Any) -> Handler:
    return lambda request: httpx.Response(status, json=body)


def forbid_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError("This case must not contact Linear")


def provider(handler: Handler) -> LinearIssueProvider:
    return LinearIssueProvider(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def linear_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", API_KEY)
    monkeypatch.setenv("LINEAR_TEAM_ID", TEAM_ID)
    monkeypatch.delenv("LINEAR_STATE_ID", raising=False)


def raises(call: Callable[[], object]) -> ServiceError:
    with pytest.raises(ServiceError) as exc:
        call()
    return exc.value


def assert_no_provider_details(error: ServiceError) -> None:
    """Neither the caller-facing message nor the logged cause leaks Linear data."""
    for text in (error.message, error.cause or ""):
        assert API_KEY not in text
        assert "linear" not in text.lower()
        assert "private provider message" not in text
        assert "Entity not found" not in text


def test_linear_implements_issue_provider() -> None:
    assert isinstance(LinearIssueProvider(), IssueProvider)


# --- get_issue -------------------------------------------------------------


def test_get_sends_one_authenticated_graphql_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=issue_body(trashed=False))

    issue = provider(handler).get_issue(ISSUE_ID)

    assert issue == Issue(id=ISSUE_ID, title=TITLE)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == LINEAR_API_URL
    # Personal API keys are sent without a "Bearer" prefix.
    assert request.headers["Authorization"] == API_KEY
    assert request.headers["Content-Type"] == "application/json"
    assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS
    assert json.loads(request.content) == {
        "query": GET_ISSUE_QUERY,
        "variables": {"id": ISSUE_ID},
    }


def test_get_returns_only_id_and_title() -> None:
    body = issue_body()
    body["data"]["issue"]["description"] = "Private description"

    issue = provider(respond(200, body)).get_issue(ISSUE_ID)

    assert issue.model_dump() == {"id": ISSUE_ID, "title": TITLE}


def test_get_missing_issue_on_http_200_is_not_found() -> None:
    error = raises(lambda: provider(respond(200, NOT_FOUND_BODY)).get_issue(ISSUE_ID))

    assert error.code == ErrorCode.NOT_FOUND
    assert error.status == 404
    assert error.message == "Issue not found"
    assert error.provider_involved
    assert error.cause == "graphql_code=INPUT_ERROR"
    assert_no_provider_details(error)


def test_get_trashed_issue_is_not_found() -> None:
    error = raises(
        lambda: provider(respond(200, issue_body(trashed=True))).get_issue(ISSUE_ID)
    )

    assert error.code == ErrorCode.NOT_FOUND
    assert error.status == 404
    assert error.cause == "issue_trashed"
    assert error.provider_involved


@pytest.mark.parametrize(
    "issue_id",
    [
        "does-not-exist",
        "TYL-5",
        ISSUE_ID.upper(),
        ISSUE_ID[:-1],
        ISSUE_ID + "0",
        ISSUE_ID.replace("-", ""),
        "64f1c0a2b3d4e5f607182930",  # A Trello card ID
    ],
)
def test_get_malformed_id_is_not_found_without_a_request(
    monkeypatch: pytest.MonkeyPatch, issue_id: str
) -> None:
    # No credentials and no network: the ID check must come first.
    monkeypatch.delenv("LINEAR_API_KEY")

    error = raises(lambda: provider(forbid_network).get_issue(issue_id))

    assert error.code == ErrorCode.NOT_FOUND
    assert error.status == 404
    assert error.cause == "malformed_issue_id"
    assert not error.provider_involved


def test_get_missing_api_key_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY")

    error = raises(lambda: provider(forbid_network).get_issue(ISSUE_ID))

    assert error.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert error.status == 503
    assert error.message == "Issue tracker credentials not configured"
    assert error.cause == "missing_credentials"
    assert not error.provider_involved


def test_get_does_not_need_a_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_TEAM_ID")

    assert provider(respond(200, issue_body())).get_issue(ISSUE_ID).id == ISSUE_ID


@pytest.mark.parametrize(
    ("status", "body", "code", "message", "cause"),
    [
        (
            401,
            AUTHENTICATION_BODY,
            ErrorCode.ACCESS_DENIED,
            "The issue tracker denied access to this issue",
            "provider_status=401",
        ),
        (
            200,
            graphql_error("AUTHENTICATION_ERROR"),
            ErrorCode.ACCESS_DENIED,
            "The issue tracker denied access to this issue",
            "graphql_code=AUTHENTICATION_ERROR",
        ),
        (
            429,
            RATE_LIMITED_BODY,
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker is rate limiting requests",
            "provider_status=429",
        ),
        (
            400,
            graphql_error("RATELIMITED"),
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker is rate limiting requests",
            "graphql_code=RATELIMITED",
        ),
        (
            500,
            None,
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker is currently unavailable",
            "provider_status=500",
        ),
        (
            400,
            graphql_error("GRAPHQL_VALIDATION_FAILED"),
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker rejected the request",
            "graphql_error",
        ),
        # Forbidden-entity responses are not yet observed (DESIGN.md 8.1 #2),
        # so they stay a provider error rather than access denied.
        (
            200,
            graphql_error("FORBIDDEN"),
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker rejected the request",
            "graphql_error",
        ),
        (
            403,
            None,
            ErrorCode.PROVIDER_ERROR,
            "The issue tracker rejected the request",
            "provider_status=403",
        ),
    ],
)
def test_get_translates_linear_failures(
    status: int, body: Any, code: ErrorCode, message: str, cause: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, text="<html>private provider message</html>")
        return httpx.Response(status, json=body)

    error = raises(lambda: provider(handler).get_issue(ISSUE_ID))

    assert (error.code, error.message, error.cause) == (code, message, cause)
    assert error.provider_involved
    assert_no_provider_details(error)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"data": null}',
        b'{"data": {"issue": null}}',
        b'{"data": {"issue": {"id": "3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"}}}',
        json.dumps({"data": {"issue": {"id": ISSUE_ID, "title": 5}}}).encode(),
        # A different issue than the one requested.
        json.dumps(
            issue_body(issue_id="00000000-0000-4000-8000-000000000000")
        ).encode(),
        json.dumps(
            {"data": {"issue": {"id": ISSUE_ID, "title": "T", "trashed": "yes"}}}
        ).encode(),
    ],
)
def test_get_unusable_response_is_provider_error(body: bytes) -> None:
    error = raises(
        lambda: provider(lambda request: httpx.Response(200, content=body)).get_issue(
            ISSUE_ID
        )
    )

    assert error.code == ErrorCode.PROVIDER_ERROR
    assert error.message == "The issue tracker returned an unexpected response"
    assert error.provider_involved


NETWORK_FAILURES = [
    (httpx.ConnectError("private"), "The issue tracker could not be reached"),
    (httpx.ReadError("private"), "The issue tracker could not be reached"),
    (httpx.ConnectTimeout("private"), "The issue tracker did not respond in time"),
    (httpx.ReadTimeout("private"), "The issue tracker did not respond in time"),
]


@pytest.mark.parametrize(("exc", "message"), NETWORK_FAILURES)
def test_get_network_failure_is_provider_error(
    exc: httpx.TransportError, message: str
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exc

    error = raises(lambda: provider(fail).get_issue(ISSUE_ID))

    assert error.code == ErrorCode.PROVIDER_ERROR
    assert error.message == message
    assert error.provider_involved
    assert_no_provider_details(error)


# --- create_issue ----------------------------------------------------------


def test_create_sends_one_mutation_to_the_configured_team() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=created_body())

    issue = provider(handler).create_issue(TITLE)

    assert issue == Issue(id=ISSUE_ID, title=TITLE)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == LINEAR_API_URL
    assert request.headers["Authorization"] == API_KEY
    assert json.loads(request.content) == {
        "query": CREATE_ISSUE_MUTATION,
        "variables": {"input": {"teamId": TEAM_ID, "title": TITLE}},
    }


def test_create_uses_the_optional_workflow_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_id = "5c2d8e4f-1a3b-4c5d-8e9f-0a1b2c3d4e5f"
    monkeypatch.setenv("LINEAR_STATE_ID", state_id)
    sent: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["variables"]["input"])
        return httpx.Response(200, json=created_body())

    provider(handler).create_issue(TITLE)

    assert sent == [{"teamId": TEAM_ID, "title": TITLE, "stateId": state_id}]


def test_create_sends_the_title_unchanged() -> None:
    title = "  padded title  "
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["variables"]["input"]["title"])
        return httpx.Response(200, json=created_body(title=title))

    assert provider(handler).create_issue(title).title == title
    assert sent == [title]


@pytest.mark.parametrize(
    ("variable", "message", "cause"),
    [
        (
            "LINEAR_API_KEY",
            "Issue tracker credentials not configured",
            "missing_credentials",
        ),
        (
            "LINEAR_TEAM_ID",
            "Issue tracker target team not configured",
            "missing_target_team",
        ),
    ],
)
def test_create_missing_configuration_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, variable: str, message: str, cause: str
) -> None:
    monkeypatch.delenv(variable)

    error = raises(lambda: provider(forbid_network).create_issue(TITLE))

    assert error.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert (error.message, error.cause) == (message, cause)
    assert not error.provider_involved
    assert not error.may_have_applied


@pytest.mark.parametrize(
    ("status", "body", "code", "may_have_applied"),
    [
        # Rejected before anything was written.
        (401, AUTHENTICATION_BODY, ErrorCode.ACCESS_DENIED, False),
        (429, RATE_LIMITED_BODY, ErrorCode.PROVIDER_ERROR, False),
        (
            400,
            graphql_error("GRAPHQL_VALIDATION_FAILED"),
            ErrorCode.PROVIDER_ERROR,
            False,
        ),
        # HTTP 200 carrying a known rejection, e.g. an unknown team ID.
        (200, graphql_error("INPUT_ERROR"), ErrorCode.PROVIDER_ERROR, False),
        (200, graphql_error("AUTHENTICATION_ERROR"), ErrorCode.ACCESS_DENIED, False),
        # Outcome unknown.
        (200, graphql_error("INTERNAL_SERVER_ERROR"), ErrorCode.PROVIDER_ERROR, True),
        (503, None, ErrorCode.PROVIDER_ERROR, True),
    ],
)
def test_create_failure_reports_whether_it_may_have_applied(
    status: int, body: Any, code: ErrorCode, may_have_applied: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, text="private provider message")
        return httpx.Response(status, json=body)

    error = raises(lambda: provider(handler).create_issue(TITLE))

    assert error.code == code
    assert error.may_have_applied is may_have_applied
    assert error.provider_involved
    assert_no_provider_details(error)


def test_create_input_error_is_not_reported_as_a_missing_issue() -> None:
    # On create, INPUT_ERROR means our configuration (e.g. the team) is wrong.
    error = raises(
        lambda: provider(respond(200, graphql_error("INPUT_ERROR"))).create_issue(TITLE)
    )

    assert error.code == ErrorCode.PROVIDER_ERROR
    assert error.message == "The issue tracker rejected the request"
    assert error.cause == "graphql_code=INPUT_ERROR"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"data": {"issueCreate": null}}',
        b'{"data": {"issueCreate": {"success": false, "issue": null}}}',
        b'{"data": {"issueCreate": {"success": true, "issue": null}}}',
        json.dumps(created_body(issue_id="short")).encode(),
        json.dumps(
            {"data": {"issueCreate": {"success": True, "issue": {"id": ISSUE_ID}}}}
        ).encode(),
    ],
)
def test_create_unusable_success_response_may_have_applied(body: bytes) -> None:
    error = raises(
        lambda: provider(
            lambda request: httpx.Response(200, content=body)
        ).create_issue(TITLE)
    )

    assert error.code == ErrorCode.PROVIDER_ERROR
    assert error.message == "The issue tracker returned an unexpected response"
    assert error.may_have_applied


@pytest.mark.parametrize(
    ("exc", "may_have_applied"),
    [
        # The connection was never made, so nothing reached Linear.
        (httpx.ConnectError("private"), False),
        (httpx.ConnectTimeout("private"), False),
        (httpx.PoolTimeout("private"), False),
        # The request may have reached Linear before the failure.
        (httpx.ReadTimeout("private"), True),
        (httpx.WriteTimeout("private"), True),
        (httpx.ReadError("private"), True),
        (httpx.RemoteProtocolError("private"), True),
    ],
)
def test_create_network_failure_reports_whether_it_may_have_applied(
    exc: httpx.TransportError, may_have_applied: bool
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exc

    error = raises(lambda: provider(fail).create_issue(TITLE))

    assert error.code == ErrorCode.PROVIDER_ERROR
    assert error.may_have_applied is may_have_applied
    assert error.provider_involved
