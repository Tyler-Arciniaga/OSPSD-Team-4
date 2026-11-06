"""Simulated Linear GraphQL API and expected values shared by Linear tests."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.providers.linear_provider import LinearIssueProvider

API_KEY = "lin_api_secret-test-key"
TEAM_ID = "9cfb482a-81e3-4154-b5b9-2c805e70a02d"
ISSUE_ID = "3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"
MISSING_ID = "00000000-0000-4000-8000-000000000000"
TITLE = "Fix login bug"
PROVIDER_TEXT = "private provider message"

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
    "errors": [{"message": PROVIDER_TEXT, "extensions": {"code": "RATELIMITED"}}]
}


def graphql_error(code: str) -> dict[str, Any]:
    return {
        "errors": [{"message": PROVIDER_TEXT, "extensions": {"code": code}}],
        "data": None,
    }


def issue_body(
    issue_id: str = ISSUE_ID, title: str = TITLE, trashed: bool | None = False
) -> dict[str, Any]:
    return {"data": {"issue": {"id": issue_id, "title": title, "trashed": trashed}}}


def created_body(issue_id: str = ISSUE_ID, title: str = TITLE) -> dict[str, Any]:
    return {
        "data": {
            "issueCreate": {"success": True, "issue": {"id": issue_id, "title": title}}
        }
    }


def respond(status: int, body: Any) -> Handler:
    """Answer every request with `status` and `body` (JSON, or raw if bytes)."""
    if isinstance(body, bytes):
        return lambda request: httpx.Response(status, content=body)
    return lambda request: httpx.Response(status, json=body)


def fail_with(exc: Exception) -> Handler:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exc

    return fail


def simulated_provider(linear: "FakeLinear") -> LinearIssueProvider:
    """A real Linear adapter whose HTTP client talks to `linear`."""
    # Look the handler up per request so a test can replace it.
    return LinearIssueProvider(
        transport=httpx.MockTransport(lambda request: linear.handle(request))
    )


class FakeLinear:
    """A minimal in-memory Linear GraphQL endpoint for get and create.

    Records every request. Set `handler` to answer differently.
    """

    def __init__(self) -> None:
        self.issues: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.handler: Handler = self._simulate
        self._next_ids = iter([ISSUE_ID, "11111111-2222-4333-8444-555555555555"])

    @property
    def payloads(self) -> list[Any]:
        return [json.loads(request.content) for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def _simulate(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != API_KEY:
            return httpx.Response(401, json=AUTHENTICATION_BODY)
        payload = json.loads(request.content)
        variables = payload["variables"]
        if "issueCreate" in payload["query"]:
            issue_id = next(self._next_ids)
            self.issues[issue_id] = variables["input"]["title"]
            return httpx.Response(
                200, json=created_body(issue_id, self.issues[issue_id])
            )
        issue_id = variables["id"]
        if issue_id not in self.issues:
            return httpx.Response(200, json=NOT_FOUND_BODY)
        return httpx.Response(200, json=issue_body(issue_id, self.issues[issue_id]))


def failure_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The structured failure log lines written by `app.errors`."""
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.errors"
    ]


def assert_no_provider_details(text: str) -> None:
    """Responses and logs never carry the key or Linear's own wording."""
    assert API_KEY not in text
    assert PROVIDER_TEXT not in text
    assert "Entity not found" not in text
