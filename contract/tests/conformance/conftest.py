"""Fixtures for the shared conformance suite.

The suite speaks HTTP to one running service, so it runs unchanged whichever
provider that service is configured for. It calls a
real provider, so it runs only when `CONFORMANCE_BASE_URL` names the service
(for example `http://127.0.0.1:8000`) and is skipped otherwise, as in CI.
"""

import os
import warnings
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import httpx
import pytest

BASE_URL_VARIABLE = "CONFORMANCE_BASE_URL"

# A real provider call can take up to the service's own 10 second budget.
REQUEST_TIMEOUT_SECONDS = 30

# Creates an issue with the given title through the service.
CreateIssue = Callable[[str], httpx.Response]


class CleanupWarning(UserWarning):
    """An issue the conformance suite created could not be deleted and needs removing by hand."""


@pytest.fixture(scope="session")
def service() -> Iterator[httpx.Client]:
    """An HTTP client for the service under test.

    Every test uses the same service process, so its per-process cache is
    shared by a test's requests.
    """
    base_url = os.environ.get(BASE_URL_VARIABLE)
    if not base_url:
        pytest.skip(f"set {BASE_URL_VARIABLE} to run the conformance suite")
    with httpx.Client(base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        yield client


class PostIssue(Protocol):
    """Sends `POST /issues` with any body, valid or not."""

    def __call__(
        self,
        *,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response: ...


@pytest.fixture
def post_issue(service: httpx.Client) -> Iterator[PostIssue]:
    """Send `POST /issues`, deleting any issue it creates after the test, even on failure.

    Tests that expect a rejection use this too, so an issue the service
    wrongly creates is still removed.

    A delete that fails is reported as a `CleanupWarning` naming the issue,
    rather than failing the test, so the issue can be removed by hand.
    """
    created: list[str] = []

    def post(
        *,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        response = service.post("/issues", json=json, content=content, headers=headers)
        if response.status_code == 201:
            created.append(response.json()["id"])
        return response

    yield post

    for issue_id in created:
        response = service.delete(f"/issues/{issue_id}")
        # 404: the test already deleted it.
        if response.status_code not in (204, 404):
            warnings.warn(
                f"could not delete issue {issue_id} "
                f"(HTTP {response.status_code}); remove it by hand",
                CleanupWarning,
                stacklevel=1,
            )


@pytest.fixture
def create_issue(post_issue: PostIssue) -> CreateIssue:
    """Create disposable issues by title; cleanup is as for `post_issue`."""
    return lambda title: post_issue(json={"title": title})
