"""Constructor configuration added in 1.1.0 (issue #91).

`TrelloIssueProvider(*, api_key=None, token=None, list_id=None)` uses each
given value and falls back to its environment variable when it is omitted.
Trello is simulated by replacing `urlopen`.
"""

import io
import json
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import IssueUpdate
from team_4_issue_tracker_trello import TrelloIssueProvider
from team_4_issue_tracker_trello import provider as provider_module

ISSUE_ID = "64f1c0a2b3d4e5f607182930"
CREATED_ID = "64f1c0a2b3d4e5f607182931"
TOKEN = "a" * 64
ENV_LIST = "5f1c0a2b3d4e5f6071829304"
LIST_A = "aaaaaaaaaaaaaaaaaaaaaaaa"
LIST_B = "bbbbbbbbbbbbbbbbbbbbbbbb"
TRELLO_VARIABLES = ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID")


def _auth(key: str, token: str) -> str:
    return f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'


@pytest.fixture(autouse=True)
def no_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with no Trello environment variables."""
    for name in TRELLO_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[Request]:
    """Record every request and answer it like Trello would."""
    requests: list[Request] = []
    lock = threading.Lock()

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        with lock:
            requests.append(request)
        url = urlparse(request.full_url)
        body: object
        if request.get_method() == "DELETE":
            body = {}
        elif url.path.endswith("/cards") and request.get_method() == "GET":
            body = [{"id": ISSUE_ID, "name": "Listed", "desc": ""}]
        elif url.path.startswith("/1/lists/"):
            body = {"id": url.path.rsplit("/", 1)[-1]}
        elif request.get_method() == "POST":
            body = {"id": CREATED_ID, "name": parse_qs(url.query)["name"][0]}
        else:
            body = {"id": ISSUE_ID, "name": "Fix login bug"}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(provider_module, "urlopen", fake_urlopen)
    return requests


def _list_in(request: Request) -> str:
    """The list a request works in, from its path or its idList parameter."""
    url = urlparse(request.full_url)
    if url.path.startswith("/1/lists/"):
        return url.path.split("/")[3]
    return parse_qs(url.query)["idList"][0]


# Every operation, and whether it works in the provider's list.
OPERATIONS: list[tuple[str, Callable[[TrelloIssueProvider], object], bool]] = [
    ("ping", lambda trello: trello.ping(), True),
    ("list_issues", lambda trello: trello.list_issues(limit=5), True),
    ("create_issue", lambda trello: trello.create_issue("New"), True),
    (
        "create_tagged_issue",
        lambda trello: trello.create_tagged_issue("New", TOKEN),
        True,
    ),
    ("find_tagged_issue", lambda trello: trello.find_tagged_issue(TOKEN), True),
    ("get_issue", lambda trello: trello.get_issue(ISSUE_ID), False),
    (
        "update_issue",
        lambda trello: trello.update_issue(ISSUE_ID, IssueUpdate(title="Renamed")),
        False,
    ),
    ("delete_issue", lambda trello: trello.delete_issue(ISSUE_ID), False),
]


@pytest.mark.parametrize(
    ("name", "call", "uses_list"), OPERATIONS, ids=[op[0] for op in OPERATIONS]
)
def test_arguments_are_used_with_no_environment(
    sent: list[Request],
    name: str,
    call: Callable[[TrelloIssueProvider], object],
    uses_list: bool,
) -> None:
    trello = TrelloIssueProvider(api_key="key-a", token="token-a", list_id=LIST_A)

    call(trello)

    assert len(sent) == 1
    assert sent[0].get_header("Authorization") == _auth("key-a", "token-a")
    if uses_list:
        assert _list_in(sent[0]) == LIST_A
    assert "key-a" not in sent[0].full_url
    assert "token-a" not in sent[0].full_url


def test_no_arguments_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, sent: list[Request]
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "env-key")
    monkeypatch.setenv("TRELLO_TOKEN", "env-token")
    monkeypatch.setenv("TRELLO_LIST_ID", ENV_LIST)

    TrelloIssueProvider().create_issue("New")

    assert sent[0].get_header("Authorization") == _auth("env-key", "env-token")
    assert _list_in(sent[0]) == ENV_LIST


def test_environment_is_read_when_a_call_runs(
    monkeypatch: pytest.MonkeyPatch, sent: list[Request]
) -> None:
    # As in 1.0.0, an omitted argument is not captured at construction.
    trello = TrelloIssueProvider()
    monkeypatch.setenv("TRELLO_API_KEY", "env-key")
    monkeypatch.setenv("TRELLO_TOKEN", "env-token")
    monkeypatch.setenv("TRELLO_LIST_ID", ENV_LIST)

    trello.create_issue("New")

    assert sent[0].get_header("Authorization") == _auth("env-key", "env-token")


@pytest.mark.parametrize(
    ("arguments", "auth", "list_id"),
    [
        ({"api_key": "arg-key"}, _auth("arg-key", "env-token"), ENV_LIST),
        ({"token": "arg-token"}, _auth("env-key", "arg-token"), ENV_LIST),
        ({"list_id": LIST_A}, _auth("env-key", "env-token"), LIST_A),
        (
            {"api_key": "arg-key", "token": "arg-token"},
            _auth("arg-key", "arg-token"),
            ENV_LIST,
        ),
    ],
)
def test_each_argument_overrides_only_its_variable(
    monkeypatch: pytest.MonkeyPatch,
    sent: list[Request],
    arguments: dict[str, str],
    auth: str,
    list_id: str,
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "env-key")
    monkeypatch.setenv("TRELLO_TOKEN", "env-token")
    monkeypatch.setenv("TRELLO_LIST_ID", ENV_LIST)

    TrelloIssueProvider(**arguments).create_issue("New")

    assert sent[0].get_header("Authorization") == auth
    assert _list_in(sent[0]) == list_id


def test_two_providers_interleaved_keep_their_own_account_and_list(
    monkeypatch: pytest.MonkeyPatch, sent: list[Request]
) -> None:
    # Environment values exist too, and must not leak into either provider.
    monkeypatch.setenv("TRELLO_API_KEY", "env-key")
    monkeypatch.setenv("TRELLO_TOKEN", "env-token")
    monkeypatch.setenv("TRELLO_LIST_ID", ENV_LIST)
    a = TrelloIssueProvider(api_key="key-a", token="token-a", list_id=LIST_A)
    b = TrelloIssueProvider(api_key="key-b", token="token-b", list_id=LIST_B)

    a.create_issue("From A")
    b.create_issue("From B")
    a.list_issues(limit=5)
    b.get_issue(ISSUE_ID)
    a.ping()
    b.find_tagged_issue(TOKEN)

    expected = [
        (_auth("key-a", "token-a"), LIST_A),
        (_auth("key-b", "token-b"), LIST_B),
        (_auth("key-a", "token-a"), LIST_A),
        (_auth("key-b", "token-b"), None),
        (_auth("key-a", "token-a"), LIST_A),
        (_auth("key-b", "token-b"), LIST_B),
    ]
    assert len(sent) == len(expected)
    for request, (auth, list_id) in zip(sent, expected, strict=True):
        assert request.get_header("Authorization") == auth
        if list_id is not None:
            assert _list_in(request) == list_id


def test_providers_on_threads_keep_their_own_account_and_list(
    sent: list[Request],
) -> None:
    accounts = {
        LIST_A: ("key-a", "token-a"),
        LIST_B: ("key-b", "token-b"),
    }
    providers = [
        TrelloIssueProvider(api_key=key, token=token, list_id=list_id)
        for list_id, (key, token) in accounts.items()
    ]
    start = threading.Barrier(len(providers))

    def work(trello: TrelloIssueProvider) -> None:
        start.wait()
        for _ in range(50):
            trello.create_issue("Concurrent")
            trello.list_issues(limit=1)

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        # result() re-raises anything a worker raised.
        for future in [pool.submit(work, p) for p in providers]:
            future.result()

    assert len(sent) == 200
    for request in sent:
        key, token = accounts[_list_in(request)]
        assert request.get_header("Authorization") == _auth(key, token)


def test_construction_with_nothing_configured_does_not_raise() -> None:
    TrelloIssueProvider()
    TrelloIssueProvider(api_key=None, token=None, list_id=None)
    TrelloIssueProvider(api_key="", token="", list_id="")


def test_arguments_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        TrelloIssueProvider("key", "token", LIST_A)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("arguments", "call", "cause"),
    [
        ({"token": "t", "list_id": LIST_A}, "create", "missing_credentials"),
        ({"api_key": "k", "list_id": LIST_A}, "create", "missing_credentials"),
        ({"api_key": "k", "token": "t"}, "create", "missing_target_list"),
        ({"token": "t"}, "get", "missing_credentials"),
        ({"api_key": "k"}, "get", "missing_credentials"),
    ],
)
def test_missing_values_raise_service_not_configured(
    sent: list[Request], arguments: dict[str, str], call: str, cause: str
) -> None:
    trello = TrelloIssueProvider(**arguments)

    with pytest.raises(ServiceError) as exc:
        if call == "create":
            trello.create_issue("New")
        else:
            trello.get_issue(ISSUE_ID)

    assert exc.value.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert exc.value.cause == cause
    assert not sent


def test_card_calls_need_no_list(sent: list[Request]) -> None:
    trello = TrelloIssueProvider(api_key="key-a", token="token-a")

    trello.get_issue(ISSUE_ID)
    trello.update_issue(ISSUE_ID, IssueUpdate(title="Renamed"))
    trello.delete_issue(ISSUE_ID)

    assert len(sent) == 3


@pytest.mark.parametrize(
    ("empty", "cause"),
    [
        ("api_key", "missing_credentials"),
        ("token", "missing_credentials"),
        ("list_id", "missing_target_list"),
    ],
)
def test_empty_string_is_missing_and_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, sent: list[Request], empty: str, cause: str
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "env-key")
    monkeypatch.setenv("TRELLO_TOKEN", "env-token")
    monkeypatch.setenv("TRELLO_LIST_ID", ENV_LIST)

    with pytest.raises(ServiceError) as exc:
        TrelloIssueProvider(**{empty: ""}).create_issue("New")

    assert exc.value.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert exc.value.cause == cause
    assert not sent


def test_ping_without_list_is_false_and_sends_nothing(sent: list[Request]) -> None:
    assert TrelloIssueProvider(api_key="key-a", token="token-a").ping() is False
    assert not sent


def test_credentials_stay_out_of_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(request: Request, timeout: int) -> io.BytesIO:
        raise HTTPError(request.full_url, 401, "Unauthorized", Message(), None)

    monkeypatch.setattr(provider_module, "urlopen", reject)
    trello = TrelloIssueProvider(api_key="secret-key", token="secret-token")

    with pytest.raises(ServiceError) as exc:
        trello.get_issue(ISSUE_ID)

    assert exc.value.code == ErrorCode.ACCESS_DENIED
    for text in (str(exc.value), exc.value.message, str(exc.value.cause), repr(trello)):
        assert "secret-key" not in text
        assert "secret-token" not in text
