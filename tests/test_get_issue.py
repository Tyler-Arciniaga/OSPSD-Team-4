import io
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main, provider
from tests.expected_errors import (
    CREDENTIALS_NOT_CONFIGURED,
    ISSUE_HTTP_FAILURES,
    NETWORK_FAILURES,
    NOT_FOUND,
    UNEXPECTED_RESPONSE,
    ErrorJson,
)

client = TestClient(main.app)

KNOWN_ISSUE_ID = "64f1c0a2b3d4e5f607182930"
KNOWN_ISSUE_TITLE = "Fetched Trello issue"


@pytest.fixture(autouse=True)
def trello_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(provider, "urlopen", forbid_network)


def test_get_issue_returns_id_and_title(monkeypatch: pytest.MonkeyPatch):
    def get_card(request: Request, timeout: int) -> io.BytesIO:
        parsed = urlsplit(request.full_url)

        assert parsed.path == f"/1/cards/{KNOWN_ISSUE_ID}"
        assert parse_qs(parsed.query) == {
            "fields": ["id,name"],
        }
        assert request.get_header("Authorization") == (
            'OAuth oauth_consumer_key="test-key", oauth_token="test-token"'
        )
        assert timeout == 10
        return io.BytesIO(
            json.dumps(
                {"id": KNOWN_ISSUE_ID, "name": KNOWN_ISSUE_TITLE, "desc": "private"}
            ).encode()
        )

    monkeypatch.setattr(provider, "urlopen", get_card)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "id": KNOWN_ISSUE_ID,
        "title": KNOWN_ISSUE_TITLE,
    }


def test_get_unknown_issue_returns_404() -> None:
    response = client.get("/issues/does-not-exist")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize(("status", "expected", "body"), ISSUE_HTTP_FAILURES)
def test_trello_http_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: int, body: ErrorJson
):
    def fail(request: Request, timeout: int) -> None:
        raise HTTPError(
            request.full_url, status, "private provider message", Message(), None
        )

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == expected
    assert response.json() == body


@pytest.mark.parametrize(("error", "body"), NETWORK_FAILURES)
def test_trello_network_failure(
    monkeypatch: pytest.MonkeyPatch, error: Exception, body: ErrorJson
):
    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == 502
    assert response.json() == body


@pytest.mark.parametrize(
    "payload", [b"not json", b"{}", b"[]", b'{"id":"wrong","name":"Title"}']
)
def test_invalid_trello_response(monkeypatch: pytest.MonkeyPatch, payload: bytes):
    monkeypatch.setattr(
        provider, "urlopen", lambda request, timeout: io.BytesIO(payload)
    )
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, variable: str):
    monkeypatch.delenv(variable)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == 503
    assert response.json() == CREDENTIALS_NOT_CONFIGURED
