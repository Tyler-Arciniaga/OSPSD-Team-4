import io
import json
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main, provider

client = TestClient(main.app)

CREATED_ISSUE_ID = "64f1c0a2b3d4e5f607182930"
CREATED_ISSUE_TITLE = "Created Trello issue"
LIST_ID = "5f1c0a2b3d4e5f6071829304"


@pytest.fixture(autouse=True)
def trello_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(provider, "urlopen", forbid_network)


def test_create_issue_returns_id_and_title(monkeypatch: pytest.MonkeyPatch):
    def create_card(request: Request, timeout: int) -> io.BytesIO:
        parsed = urlsplit(request.full_url)

        assert request.get_method() == "POST"
        assert parsed.path == "/1/cards"
        assert parse_qs(parsed.query) == {
            "idList": [LIST_ID],
            "name": [CREATED_ISSUE_TITLE],
            "key": ["test-key"],
            "token": ["test-token"],
        }
        assert request.get_header("Authorization") is None
        assert timeout == 10
        return io.BytesIO(
            json.dumps(
                {
                    "id": CREATED_ISSUE_ID,
                    "name": CREATED_ISSUE_TITLE,
                    "idList": LIST_ID,
                    "desc": "",
                }
            ).encode()
        )

    monkeypatch.setattr(provider, "urlopen", create_card)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})

    assert response.status_code == 201
    assert response.headers["Location"] == f"/issues/{CREATED_ISSUE_ID}"
    assert response.json() == {
        "id": CREATED_ISSUE_ID,
        "title": CREATED_ISSUE_TITLE,
    }


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_trello_http_errors(monkeypatch: pytest.MonkeyPatch, status: int):
    def fail(request: Request, timeout: int) -> None:
        raise HTTPError(
            request.full_url, status, "private provider message", Message(), None
        )

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 502
    assert response.json() == {"detail": "Trello request failed"}


@pytest.mark.parametrize("error", [URLError("private"), TimeoutError("private")])
def test_trello_network_failure(monkeypatch: pytest.MonkeyPatch, error: Exception):
    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 502
    assert response.json() == {"detail": "Trello request failed"}


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"{}",
        b"[]",
        b'{"id":"wrong","name":"Title"}',
        b'{"id":"64f1c0a2b3d4e5f607182930","name":123}',
    ],
)
def test_invalid_trello_response(monkeypatch: pytest.MonkeyPatch, payload: bytes):
    monkeypatch.setattr(
        provider, "urlopen", lambda request, timeout: io.BytesIO(payload)
    )
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 502
    assert response.json() == {"detail": "Trello request failed"}


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, variable: str):
    monkeypatch.delenv(variable)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 503
    assert response.json() == {"detail": "Trello credentials not configured"}


def test_missing_list_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRELLO_LIST_ID")
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 503
    assert response.json() == {"detail": "Trello list not configured"}
