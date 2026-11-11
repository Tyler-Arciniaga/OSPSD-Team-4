import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.linear.fake_linear import (
    ISSUE_ID,
    MISSING_ID,
    TITLE,
    FakeLinear,
    failure_records,
    issue_body,
)


def test_delete_existing_issue_returns_204(
    client: TestClient, linear: FakeLinear
) -> None:
    # Arrange: put an existing issue in the fake Linear API.
    linear.issues[ISSUE_ID] = TITLE

    # Act: call our real DELETE endpoint.
    response = client.delete(f"/issues/{ISSUE_ID}")

    # Assert: check the HTTP response and the fake's state.
    assert response.status_code == 204
    assert response.content == b""
    assert ISSUE_ID in linear.trashed


def test_delete_already_deleted_issue_returns_404(
    client: TestClient, linear: FakeLinear
) -> None:
    linear.issues[ISSUE_ID] = TITLE

    first = client.delete(f"/issues/{ISSUE_ID}")
    second = client.delete(f"/issues/{ISSUE_ID}")

    assert first.status_code == 204
    assert second.status_code == 404
    assert second.json()["error"]["code"] == "not_found"

    # Only the first DELETE should send a deletion mutation.
    mutations = [
        payload for payload in linear.payloads if "issueDelete" in payload["query"]
    ]
    assert len(mutations) == 1


def test_get_after_delete_returns_404(client: TestClient, linear: FakeLinear) -> None:
    linear.issues[ISSUE_ID] = TITLE

    # Read first so the issue enters the service's cache.
    before = client.get(f"/issues/{ISSUE_ID}")
    assert before.status_code == 200

    deleted = client.delete(f"/issues/{ISSUE_ID}")
    assert deleted.status_code == 204

    # The old cached issue must no longer be returned.
    after = client.get(f"/issues/{ISSUE_ID}")
    assert after.status_code == 404
    assert after.json()["error"]["code"] == "not_found"


def test_delete_missing_issue_returns_404(
    client: TestClient, linear: FakeLinear
) -> None:
    response = client.delete(f"/issues/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

    # The lookup should fail before any delete mutation is sent.
    assert len(linear.requests) == 1
    assert "issueDelete" not in linear.payloads[0]["query"]


def test_delete_unsuccessful_response_returns_502(
    client: TestClient, linear: FakeLinear
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)

        if "issueDelete" in payload["query"]:
            return httpx.Response(
                200,
                json={"data": {"issueDelete": {"success": False}}},
            )

        # Let the initial lookup find an existing issue.
        return httpx.Response(
            200,
            json={
                "data": {
                    "issue": {
                        "id": ISSUE_ID,
                        "title": TITLE,
                        "trashed": False,
                    }
                }
            },
        )

    linear.handler = handle

    response = client.delete(f"/issues/{ISSUE_ID}")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"


def test_delete_timeout_has_unknown_outcome(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)

        if "issueDelete" in payload["query"]:
            raise httpx.ReadTimeout("Simulated timeout")

        return httpx.Response(200, json=issue_body())

    linear.handler = handle

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.delete(f"/issues/{ISSUE_ID}")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert failure_records(caplog)[0]["write_outcome"] == "unknown"

    # One lookup and one mutation; no automatic retry.
    assert len(linear.requests) == 2


def test_delete_lookup_timeout_is_not_applied(
    client: TestClient,
    linear: FakeLinear,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Simulated lookup timeout")

    linear.handler = handle

    with caplog.at_level(logging.WARNING, logger="app.errors"):
        response = client.delete(f"/issues/{ISSUE_ID}")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert failure_records(caplog)[0]["write_outcome"] == "not_applied"

    # Only the lookup was attempted.
    assert len(linear.requests) == 1
    assert "issueDelete" not in linear.payloads[0]["query"]
