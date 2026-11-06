"""Conformance: Test POST /issues behaves the same with every provider."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from issue_tracker_contract.models import MAX_TITLE_LENGTH

TITLE = "Conformance create test"


def assert_created(response: httpx.Response, title: str) -> dict[str, Any]:
    """Check a response is one newly created issue with `title`, and return it."""
    assert response.status_code == 201
    body = response.json()
    assert body.keys() == {"id", "title"}
    assert isinstance(body["id"], str) and body["id"]
    assert body["title"] == title
    assert response.headers["Location"] == f"/issues/{body['id']}"
    assert "Idempotency-Replayed" not in response.headers
    return body


def assert_invalid_input(response: httpx.Response) -> list[str]:
    """Check a response is the 422 invalid_input error, and return its fields."""
    assert response.status_code == 422
    body = response.json()
    assert body.keys() == {"error"}
    error = body["error"]
    assert error.keys() == {"code", "message", "details"}
    assert (error["code"], error["message"]) == (
        "invalid_input",
        "Request body is invalid",
    )
    assert error["details"]
    for detail in error["details"]:
        assert detail.keys() == {"field", "message"}
        assert isinstance(detail["field"], str) and detail["field"]
        assert isinstance(detail["message"], str) and detail["message"]
    return [detail["field"] for detail in error["details"]]


def test_create_returns_the_new_issue_and_its_location(
    create_issue: Callable[[str], httpx.Response],
) -> None:
    response = create_issue(TITLE)

    assert_created(response, TITLE)


def test_create_accepts_the_longest_valid_title(
    create_issue: Callable[[str], httpx.Response],
) -> None:
    title = TITLE + "x" * (MAX_TITLE_LENGTH - len(TITLE))

    response = create_issue(title)

    assert_created(response, title)


def test_create_returns_the_title_as_stored(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    # The service sends the title unchanged, but a provider may trim
    # surrounding whitespace when storing it (Linear does, DESIGN.md 3.2).
    # Either way, the response and later reads report the same stored title.
    title = "  Conformance padded title  "

    created = create_issue(title)
    stored = created.json()["title"]
    fetched = service.get(created.headers["Location"])

    assert stored in (title, title.strip())
    body = assert_created(created, stored)
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_each_create_makes_a_new_issue(
    create_issue: Callable[[str], httpx.Response],
) -> None:
    first = create_issue(TITLE)
    second = create_issue(TITLE)

    assert assert_created(first, TITLE)["id"] != assert_created(second, TITLE)["id"]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "   "}, "title"),
        ({"title": "x" * (MAX_TITLE_LENGTH + 1)}, "title"),
        ({"title": 5}, "title"),
        ({"title": None}, "title"),
        ({}, "title"),
        ({"title": TITLE, "status": "done"}, "status"),
    ],
    ids=[
        "empty",
        "whitespace-only",
        "too-long",
        "not-a-string",
        "null",
        "missing",
        "extra-field",
    ],
)
def test_invalid_body_returns_422(
    service: httpx.Client, body: dict[str, Any], field: str
) -> None:
    response = service.post("/issues", json=body)

    assert field in assert_invalid_input(response)
    assert "Location" not in response.headers


def test_body_that_is_not_json_returns_422(service: httpx.Client) -> None:
    response = service.post(
        "/issues",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )

    assert_invalid_input(response)
    assert "Location" not in response.headers
