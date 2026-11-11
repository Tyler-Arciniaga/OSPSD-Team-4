"""Conformance: Test PATCH /issues/{issue_id} with every provider."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from issue_tracker_contract.models import MAX_TITLE_LENGTH

TITLE = "Conformance update test"
UPDATED_TITLE = "Conformance updated title"

NOT_FOUND = {"error": {"code": "not_found", "message": "Issue not found"}}


def create_test_issue(
    create_issue: Callable[[str], httpx.Response],
) -> dict[str, Any]:
    """Create a disposable issue for an update test."""
    response = create_issue(TITLE)

    assert response.status_code == 201
    issue = response.json()
    assert issue.keys() == {"id", "title"}
    return issue


def assert_updated(
    response: httpx.Response,
    issue_id: str,
    title: str,
) -> str:
    """Check the updated issue and return its ETag."""
    assert response.status_code == 200

    body = response.json()
    assert body.keys() == {"id", "title"}
    assert body == {"id": issue_id, "title": title}

    etag = response.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert len(etag) > 2
    return etag


def test_update_returns_updated_issue(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    issue = create_test_issue(create_issue)

    response = service.patch(
        f"/issues/{issue['id']}",
        json={"title": UPDATED_TITLE},
    )

    assert_updated(response, issue["id"], UPDATED_TITLE)


def test_get_after_update(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    issue = create_test_issue(create_issue)
    path = f"/issues/{issue['id']}"

    updated = service.patch(path, json={"title": UPDATED_TITLE})
    fetched = service.get(path)

    etag = assert_updated(updated, issue["id"], UPDATED_TITLE)

    assert fetched.status_code == 200
    assert fetched.json() == updated.json()
    assert fetched.headers["ETag"] == etag


def test_repeated_update(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    issue = create_test_issue(create_issue)
    path = f"/issues/{issue['id']}"

    first = service.patch(path, json={"title": UPDATED_TITLE})
    second = service.patch(path, json={"title": UPDATED_TITLE})
    fetched = service.get(path)

    first_etag = assert_updated(first, issue["id"], UPDATED_TITLE)
    second_etag = assert_updated(second, issue["id"], UPDATED_TITLE)

    assert first_etag == second_etag
    assert fetched.status_code == 200
    assert fetched.json() == first.json()


def test_update_with_matching_etag(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    issue = create_test_issue(create_issue)
    path = f"/issues/{issue['id']}"

    before = service.get(path)
    assert before.status_code == 200
    old_etag = before.headers["ETag"]

    updated = service.patch(
        path,
        json={"title": UPDATED_TITLE},
        headers={"If-Match": old_etag},
    )

    new_etag = assert_updated(updated, issue["id"], UPDATED_TITLE)
    assert new_etag != old_etag


def test_update_with_stale_etag(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    issue = create_test_issue(create_issue)
    path = f"/issues/{issue['id']}"

    before = service.get(path)
    assert before.status_code == 200
    old_etag = before.headers["ETag"]

    first = service.patch(
        path,
        json={"title": UPDATED_TITLE},
        headers={"If-Match": old_etag},
    )
    assert_updated(first, issue["id"], UPDATED_TITLE)

    stale = service.patch(
        path,
        json={"title": "Should not be applied"},
        headers={"If-Match": old_etag},
    )

    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "precondition_failed"

    fetched = service.get(path)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == UPDATED_TITLE


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "   "}, "title"),
        ({"title": "x" * (MAX_TITLE_LENGTH + 1)}, "title"),
        ({"title": 5}, "title"),
        ({"title": None}, "title"),
        ({}, "title"),
        ({"title": UPDATED_TITLE, "status": "done"}, "status"),
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
def test_invalid_update_body(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
    body: dict[str, Any],
    field: str,
) -> None:
    issue = create_test_issue(create_issue)
    path = f"/issues/{issue['id']}"

    response = service.patch(path, json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_input"
    assert error["message"] == "Request body is invalid"

    allowed_fields = {field}
    if body in ({"title": None}, {}):
        allowed_fields.add("body")

    assert any(detail["field"] in allowed_fields for detail in error["details"])

    # Rejected updates must not modify the issue.
    fetched = service.get(path)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == TITLE


@pytest.mark.parametrize(
    "issue_id",
    [
        "does-not-exist",
        "000000000000000000000000",
        "00000000-0000-4000-8000-000000000000",
    ],
)
def test_unknown_issue(
    service: httpx.Client,
    issue_id: str,
) -> None:
    response = service.patch(
        f"/issues/{issue_id}",
        json={"title": UPDATED_TITLE},
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_invalid_body_precedes_invalid_id(
    service: httpx.Client,
) -> None:
    response = service.patch(
        "/issues/does-not-exist",
        json={"title": ""},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_update_accepts_maximum_title_length(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    issue = create_test_issue(create_issue)
    title = "x" * MAX_TITLE_LENGTH

    updated = service.patch(
        f"/issues/{issue['id']}",
        json={"title": title},
    )

    assert_updated(updated, issue["id"], title)
