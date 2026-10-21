from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_issue_provider
from app.errors import ErrorCode, ServiceError
from app.main import app
from app.models import Issue, IssueUpdate

ISSUE_ID = "64f1c0a2b3d4e5f607182930"


class InMemoryProvider:
    """Stores one issue, so a test can watch a write change it."""

    def __init__(self) -> None:
        self.issue = Issue(id=ISSUE_ID, title="Original")
        self.updates = 0

    def get_issue(self, issue_id: str) -> Issue:
        if issue_id != ISSUE_ID:
            raise ServiceError(ErrorCode.NOT_FOUND, "Issue not found")
        return self.issue

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        self.get_issue(issue_id)
        self.updates += 1
        self.issue = Issue(id=issue_id, title=changes.title or self.issue.title)
        return self.issue

    def delete_issue(self, issue_id: str) -> None:
        raise AssertionError("not used")

    def create_issue(self, title: str) -> Issue:
        raise AssertionError("not used")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryProvider]:
    fake = InMemoryProvider()
    monkeypatch.setitem(app.dependency_overrides, get_issue_provider, lambda: fake)
    yield fake


def test_stale_overwrite_is_rejected(
    store: InMemoryProvider, caplog: pytest.LogCaptureFixture
) -> None:
    client = TestClient(app)
    # Two clients read the same version.
    etag_a = client.get(f"/issues/{ISSUE_ID}").headers["ETag"]
    etag_b = client.get(f"/issues/{ISSUE_ID}").headers["ETag"]
    assert etag_a == etag_b

    first = client.patch(
        f"/issues/{ISSUE_ID}", json={"title": "From A"}, headers={"If-Match": etag_a}
    )
    assert first.status_code == 200
    assert first.headers["ETag"] != etag_a

    second = client.patch(
        f"/issues/{ISSUE_ID}", json={"title": "From B"}, headers={"If-Match": etag_b}
    )
    assert second.status_code == 412
    assert second.json()["error"]["code"] == "precondition_failed"
    assert store.issue.title == "From A"
    assert store.updates == 1
    assert '"provider_involved": true' in caplog.text
    assert '"write_outcome": "not_applied"' in caplog.text

    # Client B re-reads and retries with the current version.
    fresh = client.get(f"/issues/{ISSUE_ID}").headers["ETag"]
    assert fresh == first.headers["ETag"]
    retry = client.patch(
        f"/issues/{ISSUE_ID}", json={"title": "From B"}, headers={"If-Match": fresh}
    )
    assert retry.status_code == 200
    assert store.issue.title == "From B"


@pytest.mark.parametrize("headers", [{}, {"If-Match": "*"}])
def test_patch_without_version_is_unconditional(
    store: InMemoryProvider, headers: dict[str, str]
) -> None:
    response = TestClient(app).patch(
        f"/issues/{ISSUE_ID}", json={"title": "New"}, headers=headers
    )
    assert response.status_code == 200
    assert store.issue.title == "New"


def test_conditional_patch_of_missing_issue_returns_404(
    store: InMemoryProvider,
) -> None:
    response = TestClient(app).patch(
        "/issues/ffffffffffffffffffffffff",
        json={"title": "New"},
        headers={"If-Match": '"0000"'},
    )
    assert response.status_code == 404
    assert store.updates == 0
