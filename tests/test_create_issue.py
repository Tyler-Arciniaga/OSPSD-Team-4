import io
import json
from collections.abc import Iterator
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main, provider
from tests.expected_errors import (
    ACCESS_DENIED_MESSAGE,
    CREDENTIALS_NOT_CONFIGURED,
    LIST_NOT_CONFIGURED,
    NETWORK_FAILURES,
    UNEXPECTED_RESPONSE,
    ErrorJson,
    error_body,
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Run startup and shutdown for each test, giving it a fresh cache.
    with TestClient(main.app) as test_client:
        yield test_client


CREATED_ISSUE_ID = "64f1c0a2b3d4e5f607182930"
CREATED_ISSUE_TITLE = "Created Trello issue"
LIST_ID = "5f1c0a2b3d4e5f6071829304"
REJECTED_MESSAGE = "The issue tracker rejected the request"


@pytest.fixture(autouse=True)
def trello_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(provider, "urlopen", forbid_network)


def test_create_issue_returns_id_and_title(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
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


# A Trello 404 during creation means the configured list is wrong, not a
# missing issue, so it is reported as a rejected provider request.
@pytest.mark.parametrize(
    ("status", "expected", "body"),
    [
        (400, 502, error_body("provider_error", REJECTED_MESSAGE)),
        (401, 403, error_body("access_denied", ACCESS_DENIED_MESSAGE)),
        (403, 403, error_body("access_denied", ACCESS_DENIED_MESSAGE)),
        (404, 502, error_body("provider_error", REJECTED_MESSAGE)),
        (
            429,
            502,
            error_body("provider_error", "The issue tracker is rate limiting requests"),
        ),
        (
            500,
            502,
            error_body("provider_error", "The issue tracker is currently unavailable"),
        ),
    ],
)
def test_trello_http_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: int,
    body: ErrorJson,
):
    def fail(request: Request, timeout: int) -> None:
        raise HTTPError(
            request.full_url, status, "private provider message", Message(), None
        )

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == expected
    assert response.json() == body


@pytest.mark.parametrize(("error", "body"), NETWORK_FAILURES)
def test_trello_network_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    body: ErrorJson,
):
    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(provider, "urlopen", fail)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 502
    assert response.json() == body


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
def test_invalid_trello_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, payload: bytes
):
    monkeypatch.setattr(
        provider, "urlopen", lambda request, timeout: io.BytesIO(payload)
    )
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 502
    assert response.json() == UNEXPECTED_RESPONSE


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_missing_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, variable: str
):
    monkeypatch.delenv(variable)
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 503
    assert response.json() == CREDENTIALS_NOT_CONFIGURED


def test_missing_list_id(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRELLO_LIST_ID")
    response = client.post("/issues", json={"title": CREATED_ISSUE_TITLE})
    assert response.status_code == 503
    assert response.json() == LIST_NOT_CONFIGURED
