import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_issue_provider
from app.main import app
from app.providers.trello_provider import TrelloIssueProvider
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import Issue, IssueUpdate


def test_endpoint_uses_supplied_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"
    requested_ids: list[str] = []

    class FakeIssueProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            requested_ids.append(issue_id)
            return Issue(id=issue_id, title="Issue from the fake provider")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("GET must not update the issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("GET must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("GET must not create an issue")

    def supply_fake_provider() -> IssueProvider:
        return FakeIssueProvider()

    # Fail if the endpoint accidentally uses the real Trello implementation.
    def forbid_trello(self: TrelloIssueProvider, issue_id: str) -> Issue:
        raise AssertionError("This test must not use Trello")

    monkeypatch.setattr(TrelloIssueProvider, "get_issue", forbid_trello)

    # Tell FastAPI to supply our fake instead of the normal provider.
    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_fake_provider,
    )

    with TestClient(app) as client:
        # The request must work without Trello credentials. They are removed
        # after startup, which requires them.
        monkeypatch.delenv("TRELLO_API_KEY", raising=False)
        monkeypatch.delenv("TRELLO_TOKEN", raising=False)
        response = client.get(f"/issues/{issue_id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": issue_id,
        "title": "Issue from the fake provider",
    }
    assert requested_ids == [issue_id]


@pytest.mark.parametrize(
    "issue_id", ["3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13", "12345", "PROJ-42"]
)
def test_endpoints_pass_any_id_to_supplied_provider(
    monkeypatch: pytest.MonkeyPatch, issue_id: str
) -> None:
    # IDs are opaque to the service: only the provider decides which it accepts.
    received: list[tuple[str, str]] = []

    class AnyIdProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            received.append(("get", issue_id))
            return Issue(id=issue_id, title="Issue from the fake provider")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            received.append(("update", issue_id))
            return Issue(id=issue_id, title=changes.title or "")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            received.append(("delete", issue_id))

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("No issue should be created")

    def supply_provider() -> IssueProvider:
        return AnyIdProvider()

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_provider,
    )

    with TestClient(app) as client:
        got = client.get(f"/issues/{issue_id}")
        updated = client.patch(f"/issues/{issue_id}", json={"title": "Renamed"})
        deleted = client.delete(f"/issues/{issue_id}")

    assert got.status_code == 200
    assert got.json()["id"] == issue_id
    assert updated.status_code == 200
    assert deleted.status_code == 204
    assert received == [("get", issue_id), ("update", issue_id), ("delete", issue_id)]


def test_provider_missing_issue_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingIssueProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise ServiceError(
                ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True
            )

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("GET must not update the issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("GET must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("GET must not create an issue")

    def supply_provider() -> IssueProvider:
        return MissingIssueProvider()

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_provider,
    )

    with TestClient(app) as client:
        response = client.get("/issues/64f1c0a2b3d4e5f607182930")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Issue not found"}
    }


def test_create_uses_supplied_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"
    requested_titles: list[str] = []

    class FakeIssueProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise AssertionError("POST must not retrieve an issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("This test must not update an issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("POST must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            requested_titles.append(title)
            return Issue(id=issue_id, title=title)

    def supply_fake_provider() -> IssueProvider:
        return FakeIssueProvider()

    # Fail if the endpoint accidentally uses the real Trello implementation.
    def forbid_trello(self: TrelloIssueProvider, title: str) -> Issue:
        raise AssertionError("This test must not use Trello")

    monkeypatch.setattr(TrelloIssueProvider, "create_issue", forbid_trello)

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_fake_provider,
    )

    with TestClient(app) as client:
        # The request must work without Trello credentials. They are removed
        # after startup, which requires them.
        monkeypatch.delenv("TRELLO_API_KEY", raising=False)
        monkeypatch.delenv("TRELLO_TOKEN", raising=False)
        monkeypatch.delenv("TRELLO_LIST_ID", raising=False)
        response = client.post("/issues", json={"title": "New issue"})

    assert response.status_code == 201
    assert response.json() == {"id": issue_id, "title": "New issue"}
    assert response.headers["Location"] == f"/issues/{issue_id}"
    assert requested_titles == ["New issue"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": 123},
        {"title": None},
        {"title": "x" * 257},
        {"title": "New issue", "description": "Not part of the contract"},
    ],
)
def test_invalid_create_body_does_not_call_provider(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object]
) -> None:
    class UnexpectedCallProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise AssertionError("Invalid bodies must not reach the provider")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("This test must not update an issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("Invalid bodies must not reach the provider")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("Invalid bodies must not reach the provider")

    def supply_provider() -> IssueProvider:
        return UnexpectedCallProvider()

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_provider,
    )

    with TestClient(app) as client:
        response = client.post("/issues", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_create_accepts_maximum_title_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EchoProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise AssertionError("POST must not retrieve an issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("This test must not update an issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("POST must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            return Issue(id="64f1c0a2b3d4e5f607182930", title=title)

    def supply_provider() -> IssueProvider:
        return EchoProvider()

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_provider,
    )

    with TestClient(app) as client:
        response = client.post("/issues", json={"title": "x" * 256})

    assert response.status_code == 201
    assert response.json()["title"] == "x" * 256


def test_provider_create_failure_is_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise AssertionError("POST must not retrieve an issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("This test must not update an issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("POST must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            raise ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker is currently unavailable",
                provider_involved=True,
            )

    def supply_provider() -> IssueProvider:
        return FailingProvider()

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_provider,
    )

    with TestClient(app) as client:
        response = client.post("/issues", json={"title": "New issue"})

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "provider_error",
            "message": "The issue tracker is currently unavailable",
        }
    }


class ListingProvider:
    """Records each list request's limit and answers with fixed issues."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        self.limits: list[int] = []

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        raise AssertionError("A list must not read single issues")

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        raise AssertionError("A list must not update issues")

    def list_issues(self, *, limit: int) -> list[Issue]:
        self.limits.append(limit)
        return self.issues[:limit]

    def delete_issue(self, issue_id: str) -> None:
        raise AssertionError("A list must not delete issues")

    def create_issue(self, title: str) -> Issue:
        raise AssertionError("A list must not create issues")


def supply(monkeypatch: pytest.MonkeyPatch, provider: IssueProvider) -> None:
    monkeypatch.setitem(app.dependency_overrides, get_issue_provider, lambda: provider)


def test_list_returns_the_providers_issues_in_an_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ListingProvider(
        [Issue(id="newest", title="Newest"), Issue(id="older", title="Older")]
    )
    supply(monkeypatch, provider)

    with TestClient(app) as client:
        response = client.get("/issues")

    assert response.status_code == 200
    assert response.json() == {
        "issues": [
            {"id": "newest", "title": "Newest"},
            {"id": "older", "title": "Older"},
        ]
    }
    # The provider's order is kept, and the default limit is applied.
    assert provider.limits == [50]


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_list_passes_the_limit_to_the_provider(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    provider = ListingProvider([Issue(id=str(n), title=f"Issue {n}") for n in range(3)])
    supply(monkeypatch, provider)

    with TestClient(app) as client:
        response = client.get("/issues", params={"limit": limit})

    assert response.status_code == 200
    assert len(response.json()["issues"]) == min(limit, 3)
    assert provider.limits == [limit]


def test_empty_list_returns_an_empty_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    supply(monkeypatch, ListingProvider([]))

    with TestClient(app) as client:
        response = client.get("/issues")

    assert response.status_code == 200
    assert response.json() == {"issues": []}


@pytest.mark.parametrize("limit", ["0", "101", "-1", "abc", "1.5", ""])
def test_invalid_limit_is_rejected_before_the_provider(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    provider = ListingProvider([])
    supply(monkeypatch, provider)

    with TestClient(app) as client:
        response = client.get(f"/issues?limit={limit}")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_input"
    assert [detail["field"] for detail in body["error"]["details"]] == ["limit"]
    assert provider.limits == []


def test_list_failure_uses_the_error_format(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingListProvider(ListingProvider):
        def list_issues(self, *, limit: int) -> list[Issue]:
            raise ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker rejected the request",
                provider_involved=True,
                cause="provider_status=404",
            )

    supply(monkeypatch, FailingListProvider([]))

    with TestClient(app) as client:
        response = client.get("/issues")

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "provider_error",
            "message": "The issue tracker rejected the request",
        }
    }


def test_limit_below_one_returns_the_documented_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supply(monkeypatch, ListingProvider([]))

    with TestClient(app) as client:
        response = client.get("/issues?limit=0")

    # Pinned because docs/api.md shows this exact response.
    assert response.json() == {
        "error": {
            "code": "invalid_input",
            "message": "Request body is invalid",
            "details": [
                {
                    "field": "limit",
                    "message": "Input should be greater than or equal to 1",
                }
            ],
        }
    }
