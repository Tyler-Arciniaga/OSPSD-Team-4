"""Conformance: Test GET /issues behaves the same with every provider.

The live list or team may already hold other issues, so these tests check
only the issues they create and never the exact contents of the list.
"""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

TITLE = "Conformance list test"

# Newest first means a just-created issue is among the first few results.
# A small window, rather than the very first position, tolerates issues
# created within the same second, which a provider may order either way.
RECENT_WINDOW = 5


def listed_issues(response: httpx.Response) -> list[dict[str, Any]]:
    """Check a response is a successful list, and return its issues."""
    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"issues"}
    issues = body["issues"]
    assert isinstance(issues, list)
    for issue in issues:
        assert issue.keys() == {"id", "title"}
        assert isinstance(issue["id"], str) and isinstance(issue["title"], str)
    return issues


def test_list_includes_a_new_issue_among_the_newest(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201

    response = service.get("/issues", params={"limit": RECENT_WINDOW})

    assert created.json() in listed_issues(response)


def test_list_reflects_the_current_title(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201
    issue_id = created.json()["id"]
    renamed = service.patch(f"/issues/{issue_id}", json={"title": f"{TITLE} renamed"})
    assert renamed.status_code == 200

    response = service.get("/issues", params={"limit": RECENT_WINDOW})

    assert renamed.json() in listed_issues(response)
    assert created.json() not in listed_issues(response)


def test_list_leaves_out_a_deleted_issue(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201
    issue_id = created.json()["id"]
    assert service.delete(f"/issues/{issue_id}").status_code == 204

    response = service.get("/issues", params={"limit": 100})

    assert issue_id not in {issue["id"] for issue in listed_issues(response)}


@pytest.mark.parametrize("limit", [1, 2])
def test_list_returns_at_most_the_limit(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response], limit: int
) -> None:
    for _ in range(limit + 1):
        assert create_issue(TITLE).status_code == 201

    response = service.get("/issues", params={"limit": limit})

    assert len(listed_issues(response)) == limit


def test_list_without_a_limit_succeeds(service: httpx.Client) -> None:
    issues = listed_issues(service.get("/issues"))

    # The default limit is 50.
    assert len(issues) <= 50


@pytest.mark.parametrize("limit", ["0", "101", "-1", "abc"])
def test_invalid_limit_returns_422(service: httpx.Client, limit: str) -> None:
    response = service.get(f"/issues?limit={limit}")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_input"
    assert [detail["field"] for detail in body["error"]["details"]] == ["limit"]
