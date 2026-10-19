import io
import json

import pytest

from app import provider as provider_module
from app.errors import ErrorCode, ServiceError
from app.interface import IssueProvider
from app.models import Issue, IssueUpdate
from app.provider import TrelloIssueProvider


def test_trello_implements_issue_provider():
    # Abstraction test
    provider = TrelloIssueProvider()
    assert isinstance(provider, IssueProvider)


def test_trello_translates_card_to_issue(monkeypatch):
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    card = {
        "id": issue_id,
        "name": "Fix login bug",
        "desc": "Private description",
    }

    def fake_urlopen(request, timeout):
        return io.BytesIO(json.dumps(card).encode())

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    result = TrelloIssueProvider().get_issue(issue_id)

    assert isinstance(result, Issue)
    assert result.id == issue_id
    assert result.title == "Fix login bug"
    assert "desc" not in result.model_dump()


from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request


def test_trello_missing_issue_returns_404(monkeypatch):
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def fake_urlopen(request: Request, timeout: int):
        raise HTTPError(
            request.full_url,
            404,
            "Not Found",
            Message(),
            None,
        )

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider().get_issue(issue_id)

    assert exc.value.code == ErrorCode.NOT_FOUND
    assert exc.value.status == 404
    assert exc.value.message == "Issue not found"
    assert exc.value.provider_involved


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",  # Invalid JSON
        b'{"id":"64f1c0a2b3d4e5f607182930"}',  # Missing name
        b'{"id":"wrong","name":"Fix login bug"}',  # Incorrect ID
    ],
)
def test_trello_rejects_malformed_response(monkeypatch, payload):
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def fake_urlopen(request, timeout):
        return io.BytesIO(payload)

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider().get_issue(issue_id)

    assert exc.value.code == ErrorCode.PROVIDER_ERROR
    assert exc.value.status == 502
    assert exc.value.message == "The issue tracker returned an unexpected response"


def test_provider_interchangeability():
    class FakeIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            return Issue(id=issue_id, title="Fake issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            return Issue(id=issue_id, title=changes.title or "Fake issue")

        def delete_issue(self, issue_id: str) -> None:
            pass

        def create_issue(self, title: str) -> Issue:
            return Issue(id="issue-456", title=title)

    fake_provider: IssueProvider = FakeIssueProvider()

    assert isinstance(fake_provider, IssueProvider)

    result = fake_provider.get_issue("issue-123")

    assert result.id == "issue-123"
    assert result.title == "Fake issue"

    updated = fake_provider.update_issue("issue-123", IssueUpdate(title="Renamed"))

    assert updated.id == "issue-123"
    assert updated.title == "Renamed"

    created = fake_provider.create_issue("New fake issue")

    assert created.id == "issue-456"
    assert created.title == "New fake issue"


def test_trello_translates_created_card_to_issue(monkeypatch):
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", "5f1c0a2b3d4e5f6071829304")

    card = {
        "id": issue_id,
        "name": "Fix login bug",
        "idList": "5f1c0a2b3d4e5f6071829304",
        "desc": "",
    }

    def fake_urlopen(request, timeout):
        return io.BytesIO(json.dumps(card).encode())

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    result = TrelloIssueProvider().create_issue("Fix login bug")

    assert isinstance(result, Issue)
    assert result.model_dump() == {"id": issue_id, "title": "Fix login bug"}


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",  # Invalid JSON
        b'{"id":"64f1c0a2b3d4e5f607182930"}',  # Missing name
        b'{"id":"short","name":"Fix login bug"}',  # Malformed ID
    ],
)
def test_trello_rejects_malformed_created_card(monkeypatch, payload):
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", "5f1c0a2b3d4e5f6071829304")

    def fake_urlopen(request, timeout):
        return io.BytesIO(payload)

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider().create_issue("Fix login bug")

    assert exc.value.code == ErrorCode.PROVIDER_ERROR
    assert exc.value.status == 502
    assert exc.value.message == "The issue tracker returned an unexpected response"
