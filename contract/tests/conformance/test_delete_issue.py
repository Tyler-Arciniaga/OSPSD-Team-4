"""Conformance: Test DELETE /issues/{issue_id} behaves the same with every provider."""

from collections.abc import Callable

import httpx
import pytest

TITLE = "Conformance delete test"
OTHER_TITLE = "Conformance delete bystander"
UPDATED_TITLE = "Conformance title after delete"

NOT_FOUND = {"error": {"code": "not_found", "message": "Issue not found"}}


def create_test_issue(create_issue: Callable[[str], httpx.Response]) -> str:
    """Create a disposable issue and return its path."""
    response = create_issue(TITLE)
    assert response.status_code == 201
    return response.headers["Location"]


def assert_deleted(response: httpx.Response) -> None:
    """Check a response is a successful, empty deletion."""
    assert response.status_code == 204
    assert response.content == b""
    assert "ETag" not in response.headers


def assert_not_found(response: httpx.Response) -> None:
    """Check a response is the 404 not_found error."""
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_delete_returns_204_with_empty_body(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    path = create_test_issue(create_issue)

    assert_deleted(service.delete(path))


def test_get_after_delete_returns_404(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    created = create_issue(TITLE)
    assert created.status_code == 201
    path = created.headers["Location"]

    # Read first to populate this service process's cache.
    fetched = service.get(path)
    assert fetched.status_code == 200
    assert fetched.json() == created.json()

    assert_deleted(service.delete(path))

    after = service.get(path)
    assert_not_found(after)
    assert "ETag" not in after.headers


def test_repeated_delete_returns_404(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    path = create_test_issue(create_issue)

    assert_deleted(service.delete(path))

    assert_not_found(service.delete(path))


def test_delete_without_prior_read(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    # Nothing is cached for this issue, so deletion goes straight to the provider.
    path = create_test_issue(create_issue)

    assert_deleted(service.delete(path))

    assert_not_found(service.get(path))


def test_delete_only_removes_the_target_issue(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    target_path = create_test_issue(create_issue)
    other = create_issue(OTHER_TITLE)
    assert other.status_code == 201

    assert_deleted(service.delete(target_path))

    fetched = service.get(other.headers["Location"])
    assert fetched.status_code == 200
    assert fetched.json() == other.json()


def test_update_after_delete_returns_404(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    path = create_test_issue(create_issue)
    assert_deleted(service.delete(path))

    updated = service.patch(path, json={"title": UPDATED_TITLE})

    assert_not_found(updated)
    assert "ETag" not in updated.headers
    # The rejected update must not restore the issue.
    assert_not_found(service.get(path))


def test_stale_if_match_after_delete_returns_404(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    path = create_test_issue(create_issue)
    before = service.get(path)
    assert before.status_code == 200
    old_etag = before.headers["ETag"]
    assert_deleted(service.delete(path))

    updated = service.patch(
        path,
        json={"title": UPDATED_TITLE},
        headers={"If-Match": old_etag},
    )

    # A missing issue is reported as missing, not as a version conflict.
    assert_not_found(updated)


@pytest.mark.parametrize(
    "issue_id",
    [
        "does-not-exist",
        "000000000000000000000000",
        "00000000-0000-4000-8000-000000000000",
    ],
)
def test_delete_unknown_issue_returns_404(service: httpx.Client, issue_id: str) -> None:
    assert_not_found(service.delete(f"/issues/{issue_id}"))


def test_delete_response_is_not_compressed(
    service: httpx.Client, create_issue: Callable[[str], httpx.Response]
) -> None:
    path = create_test_issue(create_issue)

    response = service.delete(path, headers={"Accept-Encoding": "gzip"})

    assert_deleted(response)
    assert "Content-Encoding" not in response.headers
