"""Conformance: Test GET /issues/{issue_id} behaves the same with every provider."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

TITLE = "Conformance get test"

NOT_FOUND = {"error": {"code": "not_found", "message": "Issue not found"}}


def assert_fetched(response: httpx.Response, issue: dict[str, Any]) -> str:
    """Check a response is a successful read of `issue`, and return its ETag."""
    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"id", "title"}
    assert body == issue
    etag = response.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"') and len(etag) > 2
    return etag


def test_get_returns_the_created_issue(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201

    response = service.get(f"/issues/{created.json()['id']}")

    assert_fetched(response, created.json())


def test_get_by_location_returns_the_created_issue(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201

    response = service.get(created.headers["Location"])

    assert_fetched(response, created.json())


def test_repeated_get_returns_the_same_issue_and_etag(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201
    path = f"/issues/{created.json()['id']}"

    first = service.get(path)
    second = service.get(path)

    assert assert_fetched(first, created.json()) == assert_fetched(
        second, created.json()
    )


@pytest.mark.parametrize(
    "issue_id",
    [
        "does-not-exist",
        "000000000000000000000000",
        "00000000-0000-4000-8000-000000000000",
    ],
)
def test_get_unknown_issue_returns_404(service: httpx.Client, issue_id: str) -> None:
    response = service.get(f"/issues/{issue_id}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert "ETag" not in response.headers
