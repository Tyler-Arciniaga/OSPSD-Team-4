"""Conformance: DELETE behaves the same with every provider."""

from collections.abc import Callable

import httpx


def test_delete_makes_issue_unavailable(
    service: httpx.Client,
    create_issue: Callable[[str], httpx.Response],
) -> None:
    created = create_issue("Conformance delete test")
    assert created.status_code == 201
    path = created.headers["Location"]

    # Read first to populate this service process's cache.
    fetched = service.get(path)
    assert fetched.status_code == 200
    assert fetched.json() == created.json()

    deleted = service.delete(path)
    assert deleted.status_code == 204
    assert deleted.content == b""

    expected_error = {
        "error": {
            "code": "not_found",
            "message": "Issue not found",
        }
    }

    after = service.get(path)
    assert after.status_code == 404
    assert after.json() == expected_error

    repeated = service.delete(path)
    assert repeated.status_code == 404
    assert repeated.json() == expected_error
