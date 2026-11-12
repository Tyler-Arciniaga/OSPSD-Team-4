"""TrelloIssueProvider.list_issues with simulated Trello responses, no network."""

import io
import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from app.providers import trello_provider as provider_module
from app.providers.trello_provider import TrelloIssueProvider
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import Issue
from tests.conftest import FAKE_STARTUP_CONFIG

LIST_ID = FAKE_STARTUP_CONFIG["TRELLO_LIST_ID"]
# Card IDs begin with their creation time: OLDEST < MIDDLE < NEWEST.
OLDEST = "5f0000000000000000000001"
MIDDLE = "60aaaaaaaaaaaaaaaaaaaaaa"
NEWEST = "64f1c0a2b3d4e5f607182930"
PROVIDER_TEXT = "private provider message"


@pytest.fixture(autouse=True)
def trello_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not contact Trello")

    monkeypatch.setattr(provider_module, "urlopen", forbid_network)


def card(card_id: str, name: str) -> dict[str, Any]:
    return {"id": card_id, "name": name, "desc": "Private description"}


def answer_with(monkeypatch: pytest.MonkeyPatch, payload: Any) -> list[Request]:
    """Answer every Trello request with `payload` (JSON, or raw if bytes)."""
    requests: list[Request] = []
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def respond(request: Request, timeout: int) -> io.BytesIO:
        requests.append(request)
        assert timeout == 10
        return io.BytesIO(body)

    monkeypatch.setattr(provider_module, "urlopen", respond)
    return requests


def fail_with(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(provider_module, "urlopen", fail)


def http_error(status: int) -> HTTPError:
    return HTTPError(
        "https://api.trello.com/1/lists", status, PROVIDER_TEXT, Message(), None
    )


def test_list_requests_the_open_cards_of_the_target_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = answer_with(monkeypatch, [card(NEWEST, "Only card")])

    issues = TrelloIssueProvider().list_issues(limit=50)

    assert issues == [Issue(id=NEWEST, title="Only card")]
    [request] = requests
    parsed = urlsplit(request.full_url)
    assert request.get_method() == "GET"
    assert parsed.netloc == "api.trello.com"
    assert parsed.path == f"/1/lists/{LIST_ID}/cards"
    # Archived cards are left out by asking only for open ones.
    assert parse_qs(parsed.query) == {"fields": ["id,name"], "filter": ["open"]}
    assert request.get_header("Authorization") == (
        'OAuth oauth_consumer_key="test-key", oauth_token="test-token"'
    )
    # Credentials never appear in the URL.
    assert "test-key" not in request.full_url
    assert "test-token" not in request.full_url


def test_list_returns_newest_first_and_drops_card_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Trello answers in board position order, not creation order.
    answer_with(
        monkeypatch,
        [card(MIDDLE, "Middle"), card(NEWEST, "Newest"), card(OLDEST, "Oldest")],
    )

    issues = TrelloIssueProvider().list_issues(limit=50)

    assert issues == [
        Issue(id=NEWEST, title="Newest"),
        Issue(id=MIDDLE, title="Middle"),
        Issue(id=OLDEST, title="Oldest"),
    ]
    assert all(set(issue.model_dump()) == {"id", "title"} for issue in issues)


@pytest.mark.parametrize(("limit", "expected"), [(1, [NEWEST]), (2, [NEWEST, MIDDLE])])
def test_list_keeps_only_the_newest_up_to_the_limit(
    monkeypatch: pytest.MonkeyPatch, limit: int, expected: list[str]
) -> None:
    answer_with(
        monkeypatch,
        [card(OLDEST, "Oldest"), card(MIDDLE, "Middle"), card(NEWEST, "Newest")],
    )

    issues = TrelloIssueProvider().list_issues(limit=limit)

    assert [issue.id for issue in issues] == expected


def test_empty_list_returns_no_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    answer_with(monkeypatch, [])

    assert TrelloIssueProvider().list_issues(limit=50) == []


def test_every_list_contacts_trello(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = answer_with(monkeypatch, [card(NEWEST, "Card")])
    trello = TrelloIssueProvider()

    trello.list_issues(limit=50)
    trello.list_issues(limit=50)

    assert len(requests) == 2


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        # A missing list is a configuration problem, never a missing issue.
        (404, ErrorCode.PROVIDER_ERROR, "The issue tracker rejected the request"),
        (400, ErrorCode.PROVIDER_ERROR, "The issue tracker rejected the request"),
        (401, ErrorCode.ACCESS_DENIED, "The issue tracker denied access to this issue"),
        (403, ErrorCode.ACCESS_DENIED, "The issue tracker denied access to this issue"),
        (429, ErrorCode.PROVIDER_ERROR, "The issue tracker is rate limiting requests"),
        (500, ErrorCode.PROVIDER_ERROR, "The issue tracker is currently unavailable"),
    ],
)
def test_trello_http_errors_are_translated(
    monkeypatch: pytest.MonkeyPatch, status: int, code: ErrorCode, message: str
) -> None:
    fail_with(monkeypatch, http_error(status))

    with pytest.raises(ServiceError) as exc_info:
        TrelloIssueProvider().list_issues(limit=50)

    error = exc_info.value
    assert error.code is code
    assert error.message == message
    assert error.provider_involved
    assert error.cause == f"provider_status={status}"
    assert PROVIDER_TEXT not in str(error)


@pytest.mark.parametrize(
    ("error", "message", "cause"),
    [
        (URLError("private"), "The issue tracker could not be reached", "URLError"),
        (
            URLError(TimeoutError("private")),
            "The issue tracker did not respond in time",
            "timeout",
        ),
        (
            TimeoutError("private"),
            "The issue tracker did not respond in time",
            "timeout",
        ),
    ],
)
def test_network_failures_are_provider_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, message: str, cause: str
) -> None:
    fail_with(monkeypatch, error)

    with pytest.raises(ServiceError) as exc_info:
        TrelloIssueProvider().list_issues(limit=50)

    assert exc_info.value.code is ErrorCode.PROVIDER_ERROR
    assert exc_info.value.message == message
    assert exc_info.value.cause == cause
    assert exc_info.value.provider_involved


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        {},
        {"cards": []},
        [None],
        ["not a card"],
        [{"name": "No ID"}],
        [{"id": NEWEST}],
        [{"id": "not-a-card-id", "name": "Bad ID"}],
        [{"id": NEWEST, "name": 7}],
        [card(NEWEST, "Good"), {"id": OLDEST}],
    ],
)
def test_unusable_responses_are_provider_errors(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    answer_with(monkeypatch, payload)

    with pytest.raises(ServiceError) as exc_info:
        TrelloIssueProvider().list_issues(limit=50)

    assert exc_info.value.code is ErrorCode.PROVIDER_ERROR
    assert exc_info.value.message == "The issue tracker returned an unexpected response"
    assert exc_info.value.provider_involved


@pytest.mark.parametrize("variable", ["TRELLO_API_KEY", "TRELLO_TOKEN"])
def test_missing_credentials_fail_without_contacting_trello(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.delenv(variable)

    with pytest.raises(ServiceError) as exc_info:
        TrelloIssueProvider().list_issues(limit=50)

    assert exc_info.value.code is ErrorCode.SERVICE_NOT_CONFIGURED
    assert exc_info.value.message == "Issue tracker credentials not configured"
    assert not exc_info.value.provider_involved


def test_missing_list_fails_without_contacting_trello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRELLO_LIST_ID")

    with pytest.raises(ServiceError) as exc_info:
        TrelloIssueProvider().list_issues(limit=50)

    assert exc_info.value.code is ErrorCode.SERVICE_NOT_CONFIGURED
    assert exc_info.value.message == "Issue tracker target list not configured"
    assert not exc_info.value.provider_involved
