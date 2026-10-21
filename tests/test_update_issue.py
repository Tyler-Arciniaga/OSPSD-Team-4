import io
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main, provider
from app.dependencies import get_issue_provider
from app.errors import ErrorCode, ServiceError
from app.interface import IssueProvider
from app.models import MAX_TITLE_LENGTH, Issue, IssueCreate, IssueUpdate
from app.provider import TrelloIssueProvider
from tests.expected_errors import (
    CREDENTIALS_NOT_CONFIGURED,
    ISSUE_HTTP_FAILURES,
    NETWORK_FAILURES,
    NOT_FOUND,
    UNEXPECTED_RESPONSE,
    ErrorJson,
)

KNOWN_ISSUE_ID = "64f1c0a2b3d4e5f607182930"
NEW_TITLE = "Renamed Trello issue"


@pytest.fixture(autouse=True)
def trello_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(provider, "urlopen", forbid_network)


def card_response(card: dict[str, str]) -> io.BytesIO:
    return io.BytesIO(json.dumps(card).encode())


# Endpoint with the real Trello provider and simulated Trello responses.


def test_patch_updates_title(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[Request] = []

    def update_card(request: Request, timeout: int) -> io.BytesIO:
        requests.append(request)
        parsed = urlsplit(request.full_url)

        assert request.get_method() == "PUT"
        assert parsed.path == f"/1/cards/{KNOWN_ISSUE_ID}"
        assert parse_qs(parsed.query) == {
            "name": [NEW_TITLE],
        }
        assert request.get_header("Authorization") == (
            'OAuth oauth_consumer_key="test-key", oauth_token="test-token"'
        )
        assert timeout == 10
        return card_response(
            {"id": KNOWN_ISSUE_ID, "name": NEW_TITLE, "desc": "private"}
        )

    monkeypatch.setattr(provider, "urlopen", update_card)
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 200
    assert response.json() == {"id": KNOWN_ISSUE_ID, "title": NEW_TITLE}
    assert len(requests) == 1


def test_patch_accepts_title_at_length_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    title = "x" * MAX_TITLE_LENGTH

    def update_card(request: Request, timeout: int) -> io.BytesIO:
        query = parse_qs(urlsplit(request.full_url).query)
        assert query["name"] == [title]
        return card_response({"id": KNOWN_ISSUE_ID, "name": title})

    monkeypatch.setattr(provider, "urlopen", update_card)
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": title})

    assert response.status_code == 200
    assert response.json() == {"id": KNOWN_ISSUE_ID, "title": title}


def test_patch_rejects_title_over_length_limit(client: TestClient) -> None:
    # The autouse fixture fails the test if this request reaches Trello.
    response = client.patch(
        f"/issues/{KNOWN_ISSUE_ID}",
        json={"title": "x" * (MAX_TITLE_LENGTH + 1)},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_input"
    assert [detail["field"] for detail in error["details"]] == ["title"]


def test_patch_keeps_surrounding_whitespace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PATCH follows the same title rule as POST /issues, which does not trim.
    title = f"  {NEW_TITLE}  "

    def update_card(request: Request, timeout: int) -> io.BytesIO:
        query = parse_qs(urlsplit(request.full_url).query)
        assert query["name"] == [title]
        return card_response({"id": KNOWN_ISSUE_ID, "name": title})

    monkeypatch.setattr(provider, "urlopen", update_card)
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": title})

    assert response.status_code == 200
    assert response.json() == {"id": KNOWN_ISSUE_ID, "title": title}


@pytest.mark.parametrize(
    ("title", "valid"),
    [
        ("x", True),
        ("x" * MAX_TITLE_LENGTH, True),
        ("x" * (MAX_TITLE_LENGTH + 1), False),
        ("", False),
        ("   ", False),
    ],
)
def test_create_and_update_share_title_rule(title: str, valid: bool) -> None:
    for model in (IssueCreate, IssueUpdate):
        if valid:
            assert model(title=title).title == title
        else:
            with pytest.raises(ValidationError):
                model(title=title)


@pytest.mark.parametrize("issue_id", ["does-not-exist", "abc123", "A" * 24])
def test_patch_malformed_id_returns_404(client: TestClient, issue_id: str) -> None:
    response = client.patch(f"/issues/{issue_id}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": None},
        {"title": 123},
        {"title": NEW_TITLE, "status": "done"},
    ],
)
def test_patch_invalid_body_returns_422(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_patch_missing_body_returns_422(client: TestClient) -> None:
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


@pytest.mark.parametrize(("status", "expected", "body"), ISSUE_HTTP_FAILURES)
def test_patch_trello_http_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: int,
    body: ErrorJson,
) -> None:
    def fail(request: Request, timeout: int) -> None:
        raise HTTPError(request.full_url, status, "error", Message(), None)

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == expected
    assert response.json() == body


@pytest.mark.parametrize(("error", "body"), NETWORK_FAILURES)
def test_patch_trello_network_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    body: ErrorJson,
) -> None:
    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 502
    assert response.json() == body


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        json.dumps({"id": KNOWN_ISSUE_ID}).encode(),
        json.dumps({"id": "ffffffffffffffffffffffff", "name": NEW_TITLE}).encode(),
    ],
)
def test_patch_invalid_trello_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    monkeypatch.setattr(
        provider, "urlopen", lambda request, timeout: io.BytesIO(payload)
    )
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_patch_missing_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.delenv(variable)
    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 503
    assert response.json() == CREDENTIALS_NOT_CONFIGURED


# Endpoint with supplied providers (dependency injection).


def test_patch_uses_supplied_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[tuple[str, IssueUpdate]] = []

    class FakeIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("PATCH must not call get_issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            received.append((issue_id, changes))
            return Issue(id=issue_id, title="Updated by the fake provider")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("PATCH must not create an issue")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("PATCH must not delete the issue")

    def supply_fake_provider() -> IssueProvider:
        return FakeIssueProvider()

    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)

    def forbid_trello(
        self: TrelloIssueProvider, issue_id: str, changes: IssueUpdate
    ) -> Issue:
        raise AssertionError("This test must not use Trello")

    monkeypatch.setattr(TrelloIssueProvider, "update_issue", forbid_trello)
    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_fake_provider
    )

    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 200
    assert response.json() == {
        "id": KNOWN_ISSUE_ID,
        "title": "Updated by the fake provider",
    }
    assert received == [(KNOWN_ISSUE_ID, IssueUpdate(title=NEW_TITLE))]


def test_patch_rejected_input_does_not_call_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedCallProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("Rejected requests must not reach the provider")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("Rejected requests must not reach the provider")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("PATCH must not create an issue")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("Rejected requests must not reach the provider")

    def supply_provider() -> IssueProvider:
        return UnexpectedCallProvider()

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )

    bad_body = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={})

    assert bad_body.status_code == 422


def test_patch_provider_missing_issue_returns_404(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("PATCH must not call get_issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise ServiceError(
                ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True
            )

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("PATCH must not create an issue")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("PATCH must not delete the issue")

    def supply_provider() -> IssueProvider:
        return MissingIssueProvider()

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )

    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": NEW_TITLE})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
