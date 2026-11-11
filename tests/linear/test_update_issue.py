"""PATCH /issues/{issue_id} with the real Linear adapter and simulated responses."""

import json
import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.providers.common import REQUEST_TIMEOUT_SECONDS
from app.providers.linear_provider import (
    GET_ISSUE_QUERY,
    LINEAR_API_URL,
    UPDATE_ISSUE_MUTATION,
)
from tests.expected_errors import (
    CREDENTIALS_NOT_CONFIGURED,
    NOT_FOUND,
    UNEXPECTED_RESPONSE,
)
from tests.linear.fake_linear import (
    API_KEY,
    AUTHENTICATION_BODY,
    ISSUE_ID,
    MISSING_ID,
    PROVIDER_TEXT,
    RATE_LIMITED_BODY,
    TITLE,
    FakeLinear,
    Handler,
    assert_no_provider_details,
    fail_with,
    failure_records,
    graphql_error,
    issue_body,
    respond,
)

NEW_TITLE = "Updated issue title"


def is_lookup(request: httpx.Request) -> bool:
    return "query GetIssue" in json.loads(request.content)["query"]


def after_lookup(mutation: Handler) -> Handler:
    """Let the preliminary lookup find the issue, then answer with `mutation`."""

    def handle(request: httpx.Request) -> httpx.Response:
        if is_lookup(request):
            return httpx.Response(200, json=issue_body())
        return mutation(request)

    return handle


def updated_body(issue_id: str = ISSUE_ID, title: str = NEW_TITLE) -> dict[str, Any]:
    return {
        "data": {
            "issueUpdate": {
                "success": True,
                "issue": {"id": issue_id, "title": title},
            }
        }
    }


def test_patch_sends_graphql_mutation_and_returns_updated_issue(
    client: TestClient, linear: FakeLinear
) -> None:
    linear.handler = after_lookup(respond(200, updated_body()))

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 200
    assert response.json() == {"id": ISSUE_ID, "title": NEW_TITLE}
    assert response.headers["ETag"].startswith('"')
    for request in linear.requests:
        assert request.method == "POST"
        assert str(request.url) == LINEAR_API_URL
        assert request.headers["Authorization"] == API_KEY
        assert request.headers["Content-Type"] == "application/json"
        assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS
    # A fresh lookup precedes the mutation.
    assert linear.payloads == [
        {"query": GET_ISSUE_QUERY, "variables": {"id": ISSUE_ID}},
        {
            "query": UPDATE_ISSUE_MUTATION,
            "variables": {"id": ISSUE_ID, "input": {"title": NEW_TITLE}},
        },
    ]


def test_patch_returns_provider_stored_title(
    client: TestClient, linear: FakeLinear
) -> None:
    linear.handler = after_lookup(respond(200, updated_body(title=NEW_TITLE.strip())))

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": f"  {NEW_TITLE}  "})

    assert response.status_code == 200
    assert response.json()["title"] == NEW_TITLE
    assert linear.payloads[1]["variables"]["input"]["title"] == f"  {NEW_TITLE}  "


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": None},
        {"title": 123},
        {"title": "x" * 257},
        {"title": NEW_TITLE, "status": "done"},
    ],
)
def test_patch_invalid_body_never_reaches_linear(
    client: TestClient, linear: FakeLinear, body: dict[str, Any]
) -> None:
    response = client.patch(f"/issues/{ISSUE_ID}", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert linear.requests == []


@pytest.mark.parametrize(
    "issue_id",
    ["does-not-exist", "TYL-5", ISSUE_ID.upper(), ISSUE_ID[:-1]],
)
def test_patch_malformed_id_returns_404_without_contacting_linear(
    client: TestClient, linear: FakeLinear, issue_id: str
) -> None:
    response = client.patch(f"/issues/{issue_id}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert linear.requests == []


def test_patch_missing_api_key_returns_503_without_request(
    client: TestClient, linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY")

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 503
    assert response.json() == CREDENTIALS_NOT_CONFIGURED
    assert linear.requests == []


def test_patch_does_not_need_team_configuration(
    client: TestClient, linear: FakeLinear, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_TEAM_ID")
    linear.handler = after_lookup(respond(200, updated_body()))

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("status", "body", "expected_status", "expected_code", "outcome"),
    [
        (401, AUTHENTICATION_BODY, 403, "access_denied", "not_applied"),
        (429, RATE_LIMITED_BODY, 502, "provider_error", "not_applied"),
        (
            400,
            graphql_error("GRAPHQL_VALIDATION_FAILED"),
            502,
            "provider_error",
            "not_applied",
        ),
        (
            200,
            graphql_error("AUTHENTICATION_ERROR"),
            403,
            "access_denied",
            "not_applied",
        ),
        (
            200,
            graphql_error("INTERNAL_SERVER_ERROR"),
            502,
            "provider_error",
            "unknown",
        ),
        (503, b"private", 502, "provider_error", "unknown"),
    ],
)
def test_patch_provider_failures_report_write_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    status: int,
    body: Any,
    expected_status: int,
    expected_code: str,
    outcome: str,
) -> None:
    linear.handler = after_lookup(respond(status, body))

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code

    [record] = failure_records(caplog)
    assert record["operation"] == "update_issue"
    assert record["provider_involved"] is True
    assert record["write_outcome"] == outcome
    assert_no_provider_details(response.text + caplog.text)


@pytest.mark.parametrize(
    ("exc", "outcome"),
    [
        (httpx.ConnectError(PROVIDER_TEXT), "not_applied"),
        (httpx.ConnectTimeout(PROVIDER_TEXT), "not_applied"),
        (httpx.PoolTimeout(PROVIDER_TEXT), "not_applied"),
        (httpx.ReadTimeout(PROVIDER_TEXT), "unknown"),
        (httpx.WriteTimeout(PROVIDER_TEXT), "unknown"),
        (httpx.ReadError(PROVIDER_TEXT), "unknown"),
    ],
)
def test_patch_network_failure_reports_write_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
    outcome: str,
) -> None:
    linear.handler = after_lookup(fail_with(exc))

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert failure_records(caplog)[0]["write_outcome"] == outcome
    assert_no_provider_details(response.text + caplog.text)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": None},
        {"data": {"issueUpdate": None}},
        {"data": {"issueUpdate": {"success": False, "issue": None}}},
        {"data": {"issueUpdate": {"success": True, "issue": None}}},
        updated_body(issue_id=MISSING_ID),
        updated_body(issue_id="not-a-uuid"),
        {
            "data": {
                "issueUpdate": {
                    "success": True,
                    "issue": {"id": ISSUE_ID, "title": 5},
                }
            }
        },
        b"not json",
    ],
)
def test_patch_unusable_response_returns_502_with_unknown_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    body: Any,
) -> None:
    linear.handler = after_lookup(respond(200, body))

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE

    if body != b"not json":
        assert failure_records(caplog)[0]["write_outcome"] == "unknown"


def test_patch_missing_issue_returns_404_without_mutation(
    client: TestClient, linear: FakeLinear
) -> None:
    linear.handler = respond(200, graphql_error("INPUT_ERROR"))

    response = client.patch(f"/issues/{MISSING_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    [payload] = linear.payloads
    assert payload["query"] == GET_ISSUE_QUERY


def test_patch_trashed_issue_returns_404_without_renaming_it(
    client: TestClient, linear: FakeLinear
) -> None:
    # Linear itself would rename a trashed issue; the lookup prevents it.
    linear.issues[ISSUE_ID] = TITLE
    linear.trashed.add(ISSUE_ID)

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert [payload["query"] for payload in linear.payloads] == [GET_ISSUE_QUERY]
    assert linear.issues[ISSUE_ID] == TITLE


def test_patch_after_delete_returns_404(client: TestClient, linear: FakeLinear) -> None:
    linear.issues[ISSUE_ID] = TITLE
    assert client.delete(f"/issues/{ISSUE_ID}").status_code == 204

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert linear.issues[ISSUE_ID] == TITLE


@pytest.mark.parametrize(
    ("handler", "expected_status"),
    [
        (respond(503, b"private"), 502),
        (respond(401, AUTHENTICATION_BODY), 403),
        (fail_with(httpx.ReadTimeout(PROVIDER_TEXT)), 502),
    ],
    ids=["server-error", "authentication", "read-timeout"],
)
def test_patch_lookup_failure_sends_no_mutation(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
    handler: Handler,
    expected_status: int,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if is_lookup(request):
            return handler(request)
        return httpx.Response(200, json=updated_body())

    linear.handler = handle

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == expected_status
    # A failed lookup sends no mutation, so the write is known not to apply.
    assert len(linear.requests) == 1
    [record] = failure_records(caplog)
    assert record["operation"] == "update_issue"
    assert record["write_outcome"] == "not_applied"
    assert_no_provider_details(response.text + caplog.text)


def test_patch_input_error_for_issue_removed_after_lookup_returns_404(
    client: TestClient, linear: FakeLinear
) -> None:
    lookups = iter([issue_body(), graphql_error("INPUT_ERROR")])

    def handler(request: httpx.Request) -> httpx.Response:
        if is_lookup(request):
            return httpx.Response(200, json=next(lookups))
        return httpx.Response(200, json=graphql_error("INPUT_ERROR"))

    linear.handler = handler

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    # Lookup, mutation, then a second lookup to explain the rejection.
    assert len(linear.requests) == 3


def test_patch_input_error_for_existing_issue_returns_502(
    client: TestClient, linear: FakeLinear
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if is_lookup(request):
            return httpx.Response(200, json=issue_body())
        return httpx.Response(200, json=graphql_error("INPUT_ERROR"))

    linear.handler = handler

    response = client.patch(f"/issues/{ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert len(linear.requests) == 3
