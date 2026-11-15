"""Behavior that consumers of the package rely on.

Covers `fresh=True` reads, missing configuration, listing, tagged creation and
the tracing hook. Trello is simulated by replacing `urlopen`.
"""

import importlib
import io
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from urllib.request import Request

import pytest

import team_4_issue_tracker_trello
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import IssueUpdate
from team_4_issue_tracker_trello import TrelloIssueProvider, set_span_factory
from team_4_issue_tracker_trello import provider as provider_module

ISSUE_ID = "64f1c0a2b3d4e5f607182930"
LIST_ID = "5f1c0a2b3d4e5f6071829304"
TOKEN = "a" * 64
TRELLO_VARIABLES = ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID")


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with no Trello configuration and no tracing hook."""
    for name in TRELLO_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield
    set_span_factory(None)


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)


def _respond_with(
    monkeypatch: pytest.MonkeyPatch, body: object, sent: list[Request]
) -> None:
    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        sent.append(request)
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)


def test_fresh_get_always_reads_from_trello(monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider keeps no cache, so every read, fresh or not, is a request.
    _configure(monkeypatch)
    sent: list[Request] = []
    _respond_with(monkeypatch, {"id": ISSUE_ID, "name": "Fix login bug"}, sent)
    trello = TrelloIssueProvider()

    trello.get_issue(ISSUE_ID)
    trello.get_issue(ISSUE_ID, fresh=True)
    result = trello.get_issue(ISSUE_ID, fresh=True)

    assert result.title == "Fix login bug"
    assert len(sent) == 3
    assert all(request.get_method() == "GET" for request in sent)


def test_import_and_construction_need_no_configuration() -> None:
    importlib.reload(team_4_issue_tracker_trello)
    TrelloIssueProvider()


@pytest.mark.parametrize(
    ("missing", "cause"),
    [
        ("TRELLO_API_KEY", "missing_credentials"),
        ("TRELLO_TOKEN", "missing_credentials"),
        ("TRELLO_LIST_ID", "missing_target_list"),
    ],
)
def test_missing_configuration_is_service_not_configured(
    monkeypatch: pytest.MonkeyPatch, missing: str, cause: str
) -> None:
    _configure(monkeypatch)
    monkeypatch.delenv(missing)

    def forbid_network(request: Request, timeout: int) -> io.BytesIO:
        raise AssertionError("Unconfigured calls must not reach Trello")

    monkeypatch.setattr(provider_module, "urlopen", forbid_network)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider().create_issue("Fix login bug")

    assert exc.value.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert exc.value.cause == cause


@pytest.mark.parametrize(
    "call",
    [
        lambda trello: trello.get_issue(ISSUE_ID, fresh=True),
        lambda trello: trello.update_issue(ISSUE_ID, IssueUpdate(title="Renamed")),
        lambda trello: trello.delete_issue(ISSUE_ID),
        lambda trello: trello.list_issues(limit=5),
    ],
)
def test_every_operation_reports_missing_credentials(
    call: Callable[[TrelloIssueProvider], object],
) -> None:
    with pytest.raises(ServiceError) as exc:
        call(TrelloIssueProvider())

    assert exc.value.code == ErrorCode.SERVICE_NOT_CONFIGURED


def test_list_issues_returns_newest_first_up_to_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    sent: list[Request] = []
    cards = [
        {"id": "64f1c0a2b3d4e5f607182931", "name": "Second"},
        {"id": "64f1c0a2b3d4e5f607182930", "name": "First"},
        {"id": "64f1c0a2b3d4e5f607182932", "name": "Third"},
    ]
    _respond_with(monkeypatch, cards, sent)

    issues = TrelloIssueProvider().list_issues(limit=2)

    assert [issue.title for issue in issues] == ["Third", "Second"]
    assert f"/lists/{LIST_ID}/cards" in sent[0].full_url


def test_tagged_creation_can_be_found_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    marker = f"{provider_module.TOKEN_MARKER_PREFIX}{TOKEN}"
    sent: list[Request] = []
    cards = [
        {"id": "64f1c0a2b3d4e5f607182931", "name": "Retry copy", "desc": marker},
        {"id": ISSUE_ID, "name": "Original", "desc": marker},
        {"id": "64f1c0a2b3d4e5f607182932", "name": "Unrelated", "desc": ""},
    ]
    _respond_with(monkeypatch, cards, sent)

    found = TrelloIssueProvider().find_tagged_issue(TOKEN)

    assert found is not None
    assert found.id == ISSUE_ID


def test_span_factory_wraps_each_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    spans: list[str] = []

    @contextmanager
    def record_span(name: str) -> Iterator[str]:
        spans.append(name)
        yield name

    set_span_factory(record_span)
    _respond_with(monkeypatch, {"id": ISSUE_ID, "name": "Fix login bug"}, [])

    TrelloIssueProvider().get_issue(ISSUE_ID)

    assert spans == ["trello.request"]


def test_close_is_a_no_op() -> None:
    TrelloIssueProvider().close()
