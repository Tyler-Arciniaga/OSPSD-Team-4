import io
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.interface import ClosableIssueProvider
from app.providers import trello_provider as provider_module
from app.providers.trello_provider import TrelloIssueProvider
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import Issue, IssueUpdate


def test_trello_implements_issue_provider() -> None:
    # Abstraction test
    provider = TrelloIssueProvider()
    assert isinstance(provider, IssueProvider)


def test_trello_close_needs_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # Trello opens a connection per request, so close() has nothing to release.
    def forbid_network(request: Request, timeout: int) -> io.BytesIO:
        raise AssertionError("close() must not contact Trello")

    monkeypatch.setattr(provider_module, "urlopen", forbid_network)
    trello = TrelloIssueProvider()

    assert isinstance(trello, ClosableIssueProvider)
    trello.close()


def test_trello_translates_card_to_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    card = {
        "id": issue_id,
        "name": "Fix login bug",
        "desc": "Private description",
    }

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        return io.BytesIO(json.dumps(card).encode())

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    result = TrelloIssueProvider().get_issue(issue_id)

    assert isinstance(result, Issue)
    assert result.id == issue_id
    assert result.title == "Fix login bug"
    assert "desc" not in result.model_dump()


def test_trello_missing_issue_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
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
def test_trello_rejects_malformed_response(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    issue_id = "64f1c0a2b3d4e5f607182930"

    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        return io.BytesIO(payload)

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider().get_issue(issue_id)

    assert exc.value.code == ErrorCode.PROVIDER_ERROR
    assert exc.value.status == 502
    assert exc.value.message == "The issue tracker returned an unexpected response"


@pytest.mark.parametrize(
    "issue_id", ["does-not-exist", "abc123", "A" * 24, "a" * 23, "a" * 25]
)
@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_trello_rejects_malformed_id_without_a_request(
    monkeypatch: pytest.MonkeyPatch, issue_id: str, operation: str
) -> None:
    # No credentials and no network: the ID check must come first.
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)

    def forbid_network(request: Request, timeout: int) -> io.BytesIO:
        raise AssertionError("Malformed IDs must not reach Trello")

    monkeypatch.setattr(provider_module, "urlopen", forbid_network)
    trello = TrelloIssueProvider()

    with pytest.raises(ServiceError) as exc:
        if operation == "get":
            trello.get_issue(issue_id)
        elif operation == "update":
            trello.update_issue(issue_id, IssueUpdate(title="Renamed"))
        else:
            trello.delete_issue(issue_id)

    assert exc.value.code == ErrorCode.NOT_FOUND
    assert exc.value.message == "Issue not found"
    assert exc.value.cause == "malformed_issue_id"
    assert not exc.value.provider_involved


def test_provider_interchangeability() -> None:
    class FakeIssueProvider:
        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            return Issue(id=issue_id, title="Fake issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            return Issue(id=issue_id, title=changes.title or "Fake issue")

        def list_issues(self, *, limit: int) -> list[Issue]:
            raise AssertionError("This test must not list issues")

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


def test_trello_translates_created_card_to_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
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
def test_trello_rejects_malformed_created_card(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", "5f1c0a2b3d4e5f6071829304")

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        return io.BytesIO(payload)

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider().create_issue("Fix login bug")

    assert exc.value.code == ErrorCode.PROVIDER_ERROR
    assert exc.value.status == 502
    assert exc.value.message == "The issue tracker returned an unexpected response"


PING_LIST_ID = "5f1c0a2b3d4e5f6071829304"


def _ping_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", PING_LIST_ID)


def test_trello_ping_reads_the_configured_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ping_configuration(monkeypatch)
    sent: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        sent.append(request)
        assert timeout == provider_module.REQUEST_TIMEOUT_SECONDS
        return io.BytesIO(json.dumps({"id": PING_LIST_ID}).encode())

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)

    assert TrelloIssueProvider().ping() is True
    assert len(sent) == 1
    assert sent[0].get_method() == "GET"
    assert sent[0].full_url == (
        f"https://api.trello.com/1/lists/{PING_LIST_ID}?fields=id"
    )
    # Credentials travel in the header, never in the URL.
    assert "test-token" not in sent[0].full_url
    assert "test-token" in str(sent[0].get_header("Authorization"))


@pytest.mark.parametrize(
    "failure",
    [
        HTTPError("https://api.trello.com", 401, "Unauthorized", Message(), None),
        HTTPError("https://api.trello.com", 503, "Unavailable", Message(), None),
        TimeoutError(),
        OSError("network down"),
    ],
)
def test_trello_ping_reports_failures_as_false(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _ping_configuration(monkeypatch)

    def failing_urlopen(request: Request, timeout: int) -> io.BytesIO:
        raise failure

    monkeypatch.setattr(provider_module, "urlopen", failing_urlopen)

    assert TrelloIssueProvider().ping() is False


def test_trello_ping_rejects_an_unexpected_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ping_configuration(monkeypatch)
    monkeypatch.setattr(
        provider_module,
        "urlopen",
        lambda request, timeout: io.BytesIO(b"not json"),
    )

    assert TrelloIssueProvider().ping() is False


def test_trello_ping_without_configuration_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)

    def forbid_network(request: Request, timeout: int) -> io.BytesIO:
        raise AssertionError("ping() must not contact Trello without credentials")

    monkeypatch.setattr(provider_module, "urlopen", forbid_network)

    assert TrelloIssueProvider().ping() is False
