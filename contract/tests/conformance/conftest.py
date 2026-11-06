"""Fixtures for the shared conformance suite.

The suite speaks HTTP to one running service, so it runs unchanged whichever
provider that service is configured for. It calls a
real provider, so it runs only when `CONFORMANCE_BASE_URL` names the service
(for example `http://127.0.0.1:8000`) and is skipped otherwise, as in CI.
"""

import os
import warnings
from collections.abc import Callable, Iterator

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


@pytest.fixture
def create_issue(service: httpx.Client) -> Iterator[CreateIssue]:
    """Create disposable issues, deleting each one after the test, even on failure.

    A delete that fails is reported as a `CleanupWarning` naming the issue,
    rather than failing the test, so the issue can be removed by hand.
    """
    created: list[str] = []

    def create(title: str) -> httpx.Response:
        response = service.post("/issues", json={"title": title})
        if response.status_code == 201:
            created.append(response.json()["id"])
        return response

    yield create

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
