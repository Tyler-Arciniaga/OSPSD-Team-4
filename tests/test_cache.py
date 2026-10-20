import pytest
from fastapi.testclient import TestClient

from app import dependencies, main
from app.cache import CachedIssueProvider
from app.errors import ErrorCode, ServiceError
from app.models import Issue, IssueUpdate


class FakeIssueProvider:
    """Count retrieval calls without contacting Trello."""

    def __init__(self) -> None:
        self.get_calls: list[str] = []

    def get_issue(self, issue_id: str) -> Issue:
        self.get_calls.append(issue_id)
        return Issue(id=issue_id, title="Test issue")

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        raise AssertionError("This test should not update issues")

    def delete_issue(self, issue_id: str) -> None:
        raise AssertionError("This test should not delete issues")

    def create_issue(self, title: str) -> Issue:
        raise AssertionError("This test should not create issues")


def test_repeated_read_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # Freeze the cache's clock so this test does not depend on elapsed time.
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    underlying = FakeIssueProvider()
    cached = CachedIssueProvider(underlying)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # First read fetches from the provider; second read should use the cache.
    first = cached.get_issue(issue_id)
    second = cached.get_issue(issue_id)

    expected = Issue(id=issue_id, title="Test issue")
    assert first == expected
    assert second == expected

    # Two reads, but only one call to the underlying provider.
    assert underlying.get_calls == [issue_id]


def test_cached_issue_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr("app.cache.monotonic", lambda: now)

    underlying = FakeIssueProvider()
    cached = CachedIssueProvider(underlying, ttl_seconds=30)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # Fetch at time 100. This entry should expire at time 130.
    cached.get_issue(issue_id)

    # At time 129, the cached entry is still valid.
    now = 129.0
    cached.get_issue(issue_id)
    assert underlying.get_calls == [issue_id]

    # At time 130, it has expired and must be fetched again.
    now = 130.0
    result = cached.get_issue(issue_id)

    assert result == Issue(id=issue_id, title="Test issue")
    assert underlying.get_calls == [issue_id, issue_id]


def test_cache_evicts_least_recently_used_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep time fixed so expiration does not affect this test.
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    underlying = FakeIssueProvider()
    cached = CachedIssueProvider(underlying, max_entries=2)

    issue_a = "a" * 24
    issue_b = "b" * 24
    issue_c = "c" * 24

    cached.get_issue(issue_a)
    cached.get_issue(issue_b)

    # Access A again: B is now the least recently used.
    cached.get_issue(issue_a)

    # Adding C should evict B.
    cached.get_issue(issue_c)
    assert underlying.get_calls == [issue_a, issue_b, issue_c]

    # A should still be cached.
    cached.get_issue(issue_a)
    assert underlying.get_calls == [issue_a, issue_b, issue_c]

    # B was evicted, so retrieving it requires another provider call.
    result = cached.get_issue(issue_b)
    assert result == Issue(id=issue_b, title="Test issue")
    assert underlying.get_calls == [issue_a, issue_b, issue_c, issue_b]


def test_update_invalidates_cached_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    class UpdatingProvider(FakeIssueProvider):
        def __init__(self) -> None:
            super().__init__()
            self.title = "Original title"

        def get_issue(self, issue_id: str) -> Issue:
            self.get_calls.append(issue_id)
            return Issue(id=issue_id, title=self.title)

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            assert changes.title is not None
            self.title = changes.title
            return Issue(id=issue_id, title=self.title)

    underlying = UpdatingProvider()
    cached = CachedIssueProvider(underlying)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # Save the original title in the cache.
    original = cached.get_issue(issue_id)
    assert original.title == "Original title"

    # Update through the cache wrapper.
    updated = cached.update_issue(issue_id, IssueUpdate(title="Updated title"))
    assert updated == Issue(id=issue_id, title="Updated title")

    # The next read must fetch the updated issue from the provider.
    result = cached.get_issue(issue_id)
    assert result == Issue(id=issue_id, title="Updated title")
    assert underlying.get_calls == [issue_id, issue_id]

    # That fresh result should now be cached.
    assert cached.get_issue(issue_id) == result
    assert underlying.get_calls == [issue_id, issue_id]


def test_delete_invalidates_cached_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    class DeletingProvider(FakeIssueProvider):
        def __init__(self) -> None:
            super().__init__()
            self.deleted = False

        def get_issue(self, issue_id: str) -> Issue:
            self.get_calls.append(issue_id)
            if self.deleted:
                raise ServiceError(ErrorCode.NOT_FOUND, "Issue not found")
            return Issue(id=issue_id, title="Test issue")

        def delete_issue(self, issue_id: str) -> None:
            self.deleted = True

    underlying = DeletingProvider()
    cached = CachedIssueProvider(underlying)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # Save the issue in the cache, then delete it through the wrapper.
    cached.get_issue(issue_id)
    cached.delete_issue(issue_id)
    assert underlying.deleted

    # The next read must reach the provider and report the missing issue.
    with pytest.raises(ServiceError) as exc_info:
        cached.get_issue(issue_id)

    assert exc_info.value.code == ErrorCode.NOT_FOUND
    assert underlying.get_calls == [issue_id, issue_id]


def test_failed_read_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    class RecoveringProvider(FakeIssueProvider):
        def get_issue(self, issue_id: str) -> Issue:
            self.get_calls.append(issue_id)

            # Fail the first call, then succeed.
            if len(self.get_calls) == 1:
                raise ServiceError(
                    ErrorCode.PROVIDER_ERROR,
                    "The issue tracker is currently unavailable",
                )

            return Issue(id=issue_id, title="Recovered issue")

    underlying = RecoveringProvider()
    cached = CachedIssueProvider(underlying)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # The first request reports the provider's error.
    with pytest.raises(ServiceError) as exc_info:
        cached.get_issue(issue_id)
    assert exc_info.value.code == ErrorCode.PROVIDER_ERROR

    # A later request tries the provider again and succeeds.
    result = cached.get_issue(issue_id)
    assert result == Issue(id=issue_id, title="Recovered issue")
    assert underlying.get_calls == [issue_id, issue_id]

    # The successful result is now cached.
    assert cached.get_issue(issue_id) == result
    assert underlying.get_calls == [issue_id, issue_id]


def test_returned_issues_cannot_change_cached_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    underlying = FakeIssueProvider()
    cached = CachedIssueProvider(underlying)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # Changing the first result must not change the copy saved in the cache.
    first = cached.get_issue(issue_id)
    first.title = "Changed by caller"

    second = cached.get_issue(issue_id)
    assert second.title == "Test issue"

    # Changing a cache-hit result must not change the saved value either.
    second.title = "Changed again"

    third = cached.get_issue(issue_id)
    assert third.title == "Test issue"

    # These results came from one provider call.
    assert underlying.get_calls == [issue_id]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_failed_write_invalidates_cached_issue(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    class UncertainWriteProvider(FakeIssueProvider):
        def __init__(self) -> None:
            super().__init__()
            self.title = "Original title"
            self.deleted = False

        def get_issue(self, issue_id: str) -> Issue:
            self.get_calls.append(issue_id)
            if self.deleted:
                raise ServiceError(ErrorCode.NOT_FOUND, "Issue not found")
            return Issue(id=issue_id, title=self.title)

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            assert changes.title is not None
            self.title = changes.title

            # Simulate a successful change whose response was lost.
            raise ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker did not respond in time",
            )

        def delete_issue(self, issue_id: str) -> None:
            self.deleted = True

            # The deletion happened, but the caller received an error.
            raise ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker did not respond in time",
            )

    underlying = UncertainWriteProvider()
    cached = CachedIssueProvider(underlying)
    issue_id = "64f1c0a2b3d4e5f607182930"

    # Cache the original issue before attempting the write.
    assert cached.get_issue(issue_id).title == "Original title"

    with pytest.raises(ServiceError) as exc_info:
        if operation == "update":
            cached.update_issue(issue_id, IssueUpdate(title="Updated title"))
        else:
            cached.delete_issue(issue_id)

    assert exc_info.value.code == ErrorCode.PROVIDER_ERROR

    # Despite the write error, the old cached result must be gone.
    if operation == "update":
        result = cached.get_issue(issue_id)
        assert result == Issue(id=issue_id, title="Updated title")
    else:
        with pytest.raises(ServiceError) as missing:
            cached.get_issue(issue_id)
        assert missing.value.code == ErrorCode.NOT_FOUND

    assert underlying.get_calls == [issue_id, issue_id]


def test_application_shares_cache_and_resets_it_on_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.cache.monotonic", lambda: 100.0)

    underlying = FakeIssueProvider()

    # Replace only the underlying provider constructor.
    # Keep the actual lifespan, cache wrapper, and dependency supplier.
    monkeypatch.setattr(
        dependencies,
        "TrelloIssueProvider",
        lambda: underlying,
    )

    issue_id = "64f1c0a2b3d4e5f607182930"
    expected = {"id": issue_id, "title": "Test issue"}

    # Start the application and send two separate HTTP requests.
    with TestClient(main.app) as client:
        first = client.get(f"/issues/{issue_id}")
        second = client.get(f"/issues/{issue_id}")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == expected
        assert second.json() == expected
        assert underlying.get_calls == [issue_id]

    # Startup runs again, creating a fresh cache.
    with TestClient(main.app) as client:
        response = client.get(f"/issues/{issue_id}")

        assert response.status_code == 200
        assert response.json() == expected
        assert underlying.get_calls == [issue_id, issue_id]
