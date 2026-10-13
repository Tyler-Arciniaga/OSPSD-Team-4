import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dependencies import get_issue_provider
from app.interface import IssueProvider
from app.main import app
from app.models import Issue
from app.provider import TrelloIssueProvider


def test_endpoint_uses_supplied_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"
    requested_ids: list[str] = []

    class FakeIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            requested_ids.append(issue_id)
            return Issue(id=issue_id, title="Issue from the fake provider")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("GET must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("GET must not create an issue")

    def supply_fake_provider() -> IssueProvider:
        return FakeIssueProvider()

    # The test must work without Trello credentials.
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)

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
        response = client.get(f"/issues/{issue_id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": issue_id,
        "title": "Issue from the fake provider",
    }
    assert requested_ids == [issue_id]


def test_invalid_id_does_not_call_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedCallProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("Invalid IDs must not reach the provider")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("Invalid IDs must not reach the provider")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("Invalid IDs must not reach the provider")

    def supply_provider() -> IssueProvider:
        return UnexpectedCallProvider()

    monkeypatch.setitem(
        app.dependency_overrides,
        get_issue_provider,
        supply_provider,
    )

    with TestClient(app) as client:
        response = client.get("/issues/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Issue not found"}


def test_provider_missing_issue_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise HTTPException(status_code=404, detail="Issue not found")

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
    assert response.json() == {"detail": "Issue not found"}


def test_create_uses_supplied_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"
    requested_titles: list[str] = []

    class FakeIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("POST must not retrieve an issue")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("POST must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            requested_titles.append(title)
            return Issue(id=issue_id, title=title)

    def supply_fake_provider() -> IssueProvider:
        return FakeIssueProvider()

    # The test must work without Trello credentials.
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    monkeypatch.delenv("TRELLO_LIST_ID", raising=False)

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
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("Invalid bodies must not reach the provider")

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


def test_create_accepts_maximum_title_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EchoProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("POST must not retrieve an issue")

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
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("POST must not retrieve an issue")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("POST must not delete an issue")

        def create_issue(self, title: str) -> Issue:
            raise HTTPException(status_code=502, detail="Trello request failed")

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
    assert response.json() == {"detail": "Trello request failed"}
