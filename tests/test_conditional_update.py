from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.cache import CachedIssueProvider
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

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
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


def test_conditional_patch_checks_provider_even_when_cached(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryProvider
) -> None:
    cached = CachedIssueProvider(store)
    monkeypatch.setitem(app.dependency_overrides, get_issue_provider, lambda: cached)
    with TestClient(app) as client:
        old_tag = client.get(f"/issues/{ISSUE_ID}").headers["ETag"]
        # Simulate an edit directly in Trello while the cached result is valid.
        store.issue = Issue(id=ISSUE_ID, title="Changed outside this service")
        assert client.get(f"/issues/{ISSUE_ID}").headers["ETag"] == old_tag
        rejected = client.patch(
            f"/issues/{ISSUE_ID}",
            json={"title": "Overwrite"},
            headers={"If-Match": old_tag},
        )
        assert rejected.status_code == 412
        assert store.updates == 0
        assert store.issue.title == "Changed outside this service"
        # The comparison refreshed the cache, so a caller can read and retry.
        new_tag = client.get(f"/issues/{ISSUE_ID}").headers["ETag"]
        assert new_tag != old_tag
        accepted = client.patch(
            f"/issues/{ISSUE_ID}",
            json={"title": "Reviewed update"},
            headers={"If-Match": new_tag},
        )
        assert accepted.status_code == 200
        assert store.updates == 1


@pytest.mark.parametrize(
    "code,status", [(ErrorCode.NOT_FOUND, 404), (ErrorCode.PROVIDER_ERROR, 502)]
)
def test_failed_fresh_read_prevents_conditional_write(
    monkeypatch: pytest.MonkeyPatch,
    store: InMemoryProvider,
    code: ErrorCode,
    status: int,
) -> None:
    cached = CachedIssueProvider(store)
    monkeypatch.setitem(app.dependency_overrides, get_issue_provider, lambda: cached)
    with TestClient(app) as client:
        tag = client.get(f"/issues/{ISSUE_ID}").headers["ETag"]

        def fail(issue_id: str, *, fresh: bool = False) -> Issue:
            raise ServiceError(code, "Read failed", provider_involved=True)

        monkeypatch.setattr(store, "get_issue", fail)
        response = client.patch(
            f"/issues/{ISSUE_ID}",
            json={"title": "Overwrite"},
            headers={"If-Match": tag},
        )
        assert response.status_code == status
        assert store.updates == 0
        # A failed refresh must not leave the old successful value available.
        assert client.get(f"/issues/{ISSUE_ID}").status_code == status
