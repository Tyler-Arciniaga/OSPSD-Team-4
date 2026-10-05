import io
import json
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)

KNOWN_ISSUE_ID = "64f1c0a2b3d4e5f607182930"
KNOWN_ISSUE_TITLE = "Fetched Trello issue"


@pytest.fixture(autouse=True)
def trello_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(main, "urlopen", forbid_network)


def test_get_issue_returns_id_and_title(monkeypatch: pytest.MonkeyPatch):
    def get_card(request: Request, timeout: int) -> io.BytesIO:
        assert request.full_url == (
            f"https://api.trello.com/1/cards/{KNOWN_ISSUE_ID}?fields=id,name"
        )
        assert request.get_header("Authorization") == (
            'OAuth oauth_consumer_key="test-key", oauth_token="test-token"'
        )
        assert timeout == 10
        return io.BytesIO(
            json.dumps(
                {"id": KNOWN_ISSUE_ID, "name": KNOWN_ISSUE_TITLE, "desc": "private"}
            ).encode()
        )

    monkeypatch.setattr(main, "urlopen", get_card)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "id": KNOWN_ISSUE_ID,
        "title": KNOWN_ISSUE_TITLE,
    }


def test_get_unknown_issue_returns_404():
    response = client.get("/issues/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Issue not found"}


@pytest.mark.parametrize(
    "status, expected", [(404, 404), (401, 502), (403, 502), (429, 502), (500, 502)]
)
def test_trello_http_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: int
):
    def fail(request: Request, timeout: int) -> None:
        raise HTTPError(
            request.full_url, status, "private provider message", Message(), None
        )

    monkeypatch.setattr(main, "urlopen", fail)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == expected
    assert response.json() == {
        "detail": "Issue not found" if expected == 404 else "Trello request failed"
    }


@pytest.mark.parametrize("error", [URLError("private"), TimeoutError("private")])
def test_trello_network_failure(monkeypatch: pytest.MonkeyPatch, error: Exception):
    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(main, "urlopen", fail)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == 502
    assert response.json() == {"detail": "Trello request failed"}


@pytest.mark.parametrize(
    "payload", [b"not json", b"{}", b"[]", b'{"id":"wrong","name":"Title"}']
)
def test_invalid_trello_response(monkeypatch: pytest.MonkeyPatch, payload: bytes):
    monkeypatch.setattr(main, "urlopen", lambda request, timeout: io.BytesIO(payload))
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == 502
    assert response.json() == {"detail": "Trello request failed"}


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, variable: str):
    monkeypatch.delenv(variable)
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")
    assert response.status_code == 503
    assert response.json() == {"detail": "Trello credentials not configured"}
