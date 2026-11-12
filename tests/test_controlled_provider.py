"""The controlled implementation follows the contract the conformance suite checks."""

import re

import pytest
from fastapi.testclient import TestClient

from app.interface import ClosableIssueProvider, IdempotentIssueProvider
from app.main import app
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import IssueUpdate
from tests.controlled.provider import (
    WRONG_TITLE_SUFFIX,
    InMemoryIssueProvider,
    dependency_overrides,
)


def assert_not_found(exc_info: pytest.ExceptionInfo[ServiceError]) -> None:
    assert exc_info.value.code is ErrorCode.NOT_FOUND
    assert exc_info.value.message == "Issue not found"


def test_satisfies_the_provider_protocols() -> None:
    provider = InMemoryIssueProvider()

    assert isinstance(provider, IssueProvider)
    assert isinstance(provider, ClosableIssueProvider)
    # Not idempotent: keyed creation is refused, as for any such provider.
    assert not isinstance(provider, IdempotentIssueProvider)
    assert provider.ping() is True


def test_create_then_get_returns_the_same_issue() -> None:
    provider = InMemoryIssueProvider()

    created = provider.create_issue("First")

    assert re.fullmatch(r"[0-9a-f]{24}", created.id)
    assert created.title == "First"
    assert provider.get_issue(created.id) == created
    assert provider.get_issue(created.id, fresh=True) == created


def test_each_create_makes_a_new_id() -> None:
    provider = InMemoryIssueProvider()

    ids = {provider.create_issue("Same title").id for _ in range(50)}

    assert len(ids) == 50


def test_reads_return_the_title_as_stored() -> None:
    provider = InMemoryIssueProvider()

    created = provider.create_issue("  padded  ")

    assert created.title in ("  padded  ", "padded")
    assert provider.get_issue(created.id).title == created.title


@pytest.mark.parametrize(
    "issue_id",
    [
        "does-not-exist",
        "000000000000000000000000",
        "00000000-0000-4000-8000-000000000000",
    ],
)
def test_unknown_id_is_not_found(issue_id: str) -> None:
    provider = InMemoryIssueProvider()

    with pytest.raises(ServiceError) as get_error:
        provider.get_issue(issue_id)
    with pytest.raises(ServiceError) as update_error:
        provider.update_issue(issue_id, IssueUpdate(title="New"))
    with pytest.raises(ServiceError) as delete_error:
        provider.delete_issue(issue_id)

    for exc_info in (get_error, update_error, delete_error):
        assert_not_found(exc_info)


def test_update_renames_the_issue() -> None:
    provider = InMemoryIssueProvider()
    created = provider.create_issue("Old")

    updated = provider.update_issue(created.id, IssueUpdate(title="New"))

    assert updated.id == created.id
    assert updated.title == "New"
    assert provider.get_issue(created.id).title == "New"


def test_deleted_issue_is_not_found() -> None:
    provider = InMemoryIssueProvider()
    created = provider.create_issue("Doomed")

    provider.delete_issue(created.id)

    with pytest.raises(ServiceError) as get_error:
        provider.get_issue(created.id)
    with pytest.raises(ServiceError) as delete_again_error:
        provider.delete_issue(created.id)
    assert_not_found(get_error)
    assert_not_found(delete_again_error)


def test_list_returns_newest_first_up_to_the_limit() -> None:
    provider = InMemoryIssueProvider()
    first = provider.create_issue("First")
    second = provider.create_issue("Second")
    third = provider.create_issue("Third")
    provider.update_issue(first.id, IssueUpdate(title="First renamed"))

    assert provider.list_issues(limit=100) == [
        third,
        second,
        first.model_copy(update={"title": "First renamed"}),
    ]
    assert provider.list_issues(limit=2) == [third, second]


def test_list_leaves_out_deleted_issues() -> None:
    provider = InMemoryIssueProvider()
    kept = provider.create_issue("Kept")
    deleted = provider.create_issue("Deleted")

    provider.delete_issue(deleted.id)

    assert provider.list_issues(limit=100) == [kept]


def test_unknown_fault_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown fault"):
        InMemoryIssueProvider("explode")


def test_wrong_title_fault_alters_created_titles() -> None:
    provider = InMemoryIssueProvider("wrong_title")

    created = provider.create_issue("Requested")

    assert created.title == "Requested" + WRONG_TITLE_SUFFIX


def test_delete_fails_fault_keeps_the_issue() -> None:
    provider = InMemoryIssueProvider("delete_fails")
    created = provider.create_issue("Stays")

    with pytest.raises(ServiceError) as exc_info:
        provider.delete_issue(created.id)

    assert exc_info.value.code is ErrorCode.PROVIDER_ERROR
    assert provider.get_issue(created.id) == created


def test_get_failure_exercise_uses_the_real_error_and_telemetry_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for dependency, override in dependency_overrides("get_fails").items():
        monkeypatch.setitem(app.dependency_overrides, dependency, override)
    with TestClient(app) as client:
        created = client.post("/issues", json={"title": "Alert exercise"})
        location = created.headers["Location"]
        response = client.get(location)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_error"
        assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-ID"])
        # Cleanup remains possible while reads fail.
        assert client.delete(location).status_code == 204


@pytest.fixture
def controlled_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for dependency, override in dependency_overrides().items():
        monkeypatch.setitem(app.dependency_overrides, dependency, override)
    return TestClient(app)


def test_service_lifecycle_through_the_real_app(controlled_client: TestClient) -> None:
    with controlled_client as client:
        created = client.post("/issues", json={"title": "Through the app"})
        location = created.headers["Location"]
        fetched = client.get(location)
        again = client.get(location)
        deleted = client.delete(location)
        after_delete = client.get(location)

    assert created.status_code == 201
    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    etag = fetched.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert again.headers["ETag"] == etag
    assert deleted.status_code == 204
    # The cache must not keep serving a deleted issue.
    assert after_delete.status_code == 404
    assert after_delete.json() == {
        "error": {"code": "not_found", "message": "Issue not found"}
    }
    assert "ETag" not in after_delete.headers


def test_readiness_uses_no_real_provider(controlled_client: TestClient) -> None:
    with controlled_client as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["provider"] == "reachable"
    assert body["checks"]["idempotency_store"] == "not_used"
