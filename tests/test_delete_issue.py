import io
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main, provider
from app.dependencies import get_issue_provider
from app.errors import ErrorCode, ServiceError
from app.interface import IssueProvider
from app.models import Issue, IssueUpdate
from tests.expected_errors import (
    CREDENTIALS_NOT_CONFIGURED,
    ISSUE_HTTP_FAILURES,
    NETWORK_FAILURES,
    NOT_FOUND,
    ErrorJson,
    error_body,
)

client = TestClient(main.app)
KNOWN_ISSUE_ID = "64f1c0a2b3d4e5f607182930"


@pytest.fixture(autouse=True)
def trello_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(provider, "urlopen", forbid_network)


@pytest.mark.parametrize("body", [b"", b"{}"])
def test_delete_sends_one_request_and_returns_no_content(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    requests: list[Request] = []
    trello_response = io.BytesIO(body)

    def delete_card(request: Request, timeout: int) -> io.BytesIO:
        requests.append(request)
        parsed = urlsplit(request.full_url)
        assert request.get_method() == "DELETE"
        assert parsed.scheme == "https"
        assert parsed.netloc == "api.trello.com"
        assert parsed.path == f"/1/cards/{KNOWN_ISSUE_ID}"
        assert parsed.query == ""
        assert request.get_header("Authorization") == (
            'OAuth oauth_consumer_key="test-key", oauth_token="test-token"'
        )
        assert request.data is None
        assert timeout == 10
        return trello_response

    monkeypatch.setattr(provider, "urlopen", delete_card)
    response = client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 204
    assert response.content == b""
    assert len(requests) == 1
    assert trello_response.closed


@pytest.mark.parametrize(
    "issue_id", ["does-not-exist", "abc123", "A" * 24, "a" * 23, "a" * 25]
)
def test_delete_invalid_id_never_contacts_trello(issue_id: str) -> None:
    response = client.delete(f"/issues/{issue_id}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_delete_missing_credentials(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.delenv(variable)
    response = client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 503
    assert response.json() == CREDENTIALS_NOT_CONFIGURED


@pytest.mark.parametrize(("status", "expected", "body"), ISSUE_HTTP_FAILURES)
def test_delete_trello_http_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: int, body: ErrorJson
) -> None:
    trello_body = io.BytesIO(b"private provider details")

    def fail(request: Request, timeout: int) -> None:
        raise HTTPError(request.full_url, status, "private", Message(), trello_body)

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == expected
    assert response.json() == body
    assert trello_body.closed


@pytest.mark.parametrize(("error", "body"), NETWORK_FAILURES)
def test_delete_network_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, error: Exception, body: ErrorJson
) -> None:
    calls: list[Request] = []

    def fail(request: Request, timeout: int) -> None:
        calls.append(request)
        raise error

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 502
    assert response.json() == body
    assert len(calls) == 1


def test_delete_uses_injected_provider_and_cannot_be_retrieved_or_deleted_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issues = {KNOWN_ISSUE_ID: Issue(id=KNOWN_ISSUE_ID, title="Disposable issue")}
    deleted_ids: list[str] = []

    class FakeIssueProvider:
        def get_issue(self, issue_id: str) -> Issue:
            if issue_id not in issues:
                raise ServiceError(
                    ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True
                )
            return issues[issue_id]

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("DELETE must not update an issue")

        def delete_issue(self, issue_id: str) -> None:
            if issue_id not in issues:
                raise ServiceError(
                    ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True
                )
            del issues[issue_id]
            deleted_ids.append(issue_id)

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("DELETE must not create an issue")

    def supply_provider() -> IssueProvider:
        return FakeIssueProvider()

    monkeypatch.delenv("TRELLO_API_KEY")
    monkeypatch.delenv("TRELLO_TOKEN")
    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )

    assert client.get(f"/issues/{KNOWN_ISSUE_ID}").status_code == 200
    deleted = client.delete(f"/issues/{KNOWN_ISSUE_ID}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    for response in (
        client.get(f"/issues/{KNOWN_ISSUE_ID}"),
        client.delete(f"/issues/{KNOWN_ISSUE_ID}"),
    ):
        assert response.status_code == 404
        assert response.json() == NOT_FOUND
    assert deleted_ids == [KNOWN_ISSUE_ID]


def test_delete_invalid_id_does_not_call_injected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedCallProvider:
        def get_issue(self, issue_id: str) -> Issue:
            raise AssertionError("DELETE must not retrieve an issue")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("DELETE must not update an issue")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("Invalid IDs must not reach the provider")

        def create_issue(self, title: str) -> Issue:
            raise AssertionError("DELETE must not create an issue")

    def supply_provider() -> IssueProvider:
        return UnexpectedCallProvider()

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )
    response = client.delete("/issues/does-not-exist")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_delete_lost_response_may_follow_a_successful_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted_ids: list[str] = []

    def delete_then_timeout(request: Request, timeout: int) -> None:
        deleted_ids.append(urlsplit(request.full_url).path.rsplit("/", 1)[1])
        raise TimeoutError("Response lost after deletion")

    monkeypatch.setattr(provider, "urlopen", delete_then_timeout)
    response = client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 502
    assert response.json() == error_body(
        "provider_error", "The issue tracker did not respond in time"
    )
    assert deleted_ids == [KNOWN_ISSUE_ID]


def test_delete_openapi_has_no_success_body() -> None:
    operation = main.app.openapi()["paths"]["/issues/{issue_id}"]["delete"]

    assert "requestBody" not in operation
    assert "content" not in operation["responses"]["204"]
