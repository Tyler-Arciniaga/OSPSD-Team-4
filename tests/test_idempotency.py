"""Idempotent creation: the promised logical action is never duplicated.

Most tests use a fake provider backed by a shared in-memory "board", so the
number of issues that really exist can be counted after each scenario. The
board outlives provider and store objects, as Trello outlives the service,
which is how restarts are simulated. The last section repeats the key cases
against the real Trello provider with simulated HTTP responses.
"""

import io
import json
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, provider
from app.cache import CachedIssueProvider
from app.dependencies import get_idempotency_store, get_issue_provider
from app.errors import ErrorCode, ServiceError
from app.idempotency import (
    ATTEMPT_LEASE_SECONDS,
    KEY_RETENTION_SECONDS,
    ClaimKind,
    IdempotencyStore,
    create_issue_idempotently,
    key_digest,
    request_fingerprint,
)
from app.interface import IdempotentIssueProvider, IssueProvider
from app.models import Issue, IssueCreate, IssueUpdate

KEY = "3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"
OTHER_KEY = "8a1d6e0c-2b7f-4c3a-9e5d-1f0b2c3d4e5f"
TITLE = "Idempotent issue"
TIMEOUT_MESSAGE = "The issue tracker did not respond in time"


@pytest.fixture
def raise_server_exceptions() -> bool:
    # Keep unexpected errors as 500 responses so their outcome can be checked.
    return False


_client: TestClient | None = None


@pytest.fixture
def startup_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the store created at startup at a temporary file."""
    path = tmp_path / "startup-keys.db"
    monkeypatch.setenv("IDEMPOTENCY_DB_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def bind_client(startup_db_path: Path, client: TestClient) -> Iterator[None]:
    """Let the request helpers use the conftest client, which runs startup."""
    global _client
    _client = client
    yield
    _client = None


def http() -> TestClient:
    assert _client is not None
    return _client


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Board:
    """What really exists in the issue tracker."""

    cards: list[tuple[str, str, str | None]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, title: str, token: str | None) -> Issue:
        with self.lock:
            issue_id = f"{len(self.cards) + 1:024x}"
            self.cards.append((issue_id, title, token))
        return Issue(id=issue_id, title=title)

    def ids(self) -> list[str]:
        return [card[0] for card in self.cards]


class TaggingProvider:
    """An idempotency-capable fake whose failures can be scripted."""

    def __init__(self, board: Board) -> None:
        self.board = board
        self.create_calls = 0
        self.lookup_calls = 0
        # Scripted behaviour for the next create (cleared once used).
        self.create_then_fail: BaseException | None = None
        self.fail_before_create: ServiceError | None = None
        self.fail_lookup: ServiceError | None = None
        # Set to hold create calls open, to make requests overlap.
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        raise AssertionError("not used")

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        raise AssertionError("not used")

    def delete_issue(self, issue_id: str) -> None:
        raise AssertionError("not used")

    def create_issue(self, title: str) -> Issue:
        self.create_calls += 1
        return self.board.add(title, None)

    def create_tagged_issue(self, title: str, creation_token: str) -> Issue:
        self.create_calls += 1
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        if self.fail_before_create is not None:
            error, self.fail_before_create = self.fail_before_create, None
            raise error
        issue = self.board.add(title, creation_token)
        if self.create_then_fail is not None:
            error2, self.create_then_fail = self.create_then_fail, None
            raise error2
        return issue

    def find_tagged_issue(self, creation_token: str) -> Issue | None:
        self.lookup_calls += 1
        if self.fail_lookup is not None:
            error, self.fail_lookup = self.fail_lookup, None
            raise error
        matches = [c for c in self.board.cards if c[2] == creation_token]
        if not matches:
            return None
        issue_id, title, _ = min(matches)
        return Issue(id=issue_id, title=title)


def timeout_after_send() -> ServiceError:
    """A lost response: the request may have been applied."""
    return ServiceError(
        ErrorCode.PROVIDER_ERROR,
        TIMEOUT_MESSAGE,
        provider_involved=True,
        cause="timeout",
    )


def rejected() -> ServiceError:
    """A rejection the provider guarantees created nothing."""
    return ServiceError(
        ErrorCode.PROVIDER_ERROR,
        "The issue tracker is rate limiting requests",
        provider_involved=True,
        cause="provider_status=429",
        may_have_applied=False,
    )


@dataclass
class Service:
    board: Board
    provider: TaggingProvider
    clock: FakeClock
    db_path: Path

    def restart(self) -> None:
        """Replace every in-process object; only the files and the board remain."""
        self.provider = TaggingProvider(self.board)


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Service]:
    board = Board()
    svc = Service(board, TaggingProvider(board), FakeClock(), tmp_path / "keys.db")

    def supply_provider() -> IssueProvider:
        return svc.provider

    def supply_store() -> IdempotencyStore:
        # A fresh store object per request, as in production: all state that
        # matters lives in the database file.
        return IdempotencyStore(svc.db_path, clock=svc.clock)

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )
    monkeypatch.setitem(
        main.app.dependency_overrides, get_idempotency_store, supply_store
    )
    yield svc


def post(key: str | None = KEY, title: str = TITLE) -> httpx.Response:
    headers = {} if key is None else {"Idempotency-Key": key}
    return http().post("/issues", json={"title": title}, headers=headers)


# --- Repeated requests ------------------------------------------------------


def test_repeated_request_creates_one_issue_and_replays_it(service: Service) -> None:
    first = post()
    second = post()
    third = post()

    assert first.status_code == second.status_code == third.status_code == 201
    assert first.json() == second.json() == third.json()
    assert first.headers["Location"] == second.headers["Location"]
    assert "Idempotency-Replayed" not in first.headers
    assert second.headers["Idempotency-Replayed"] == "true"
    assert len(service.board.cards) == 1
    assert service.provider.create_calls == 1


def test_distinct_keys_create_distinct_issues(service: Service) -> None:
    assert post(KEY).json() != post(OTHER_KEY).json()
    assert len(service.board.cards) == 2


def test_requests_without_a_key_keep_existing_behaviour(service: Service) -> None:
    post(None)
    post(None)
    # Not idempotent, as before: each request creates an issue, untagged.
    assert [card[2] for card in service.board.cards] == [None, None]


def test_same_key_with_different_body_is_rejected(service: Service) -> None:
    original = post(title="Original")
    conflicting = post(title="Different")

    assert conflicting.status_code == 422
    assert conflicting.json() == {
        "error": {
            "code": "idempotency_key_reused",
            "message": "This idempotency key was already used with a different request",
        }
    }
    # The conflicting request changed nothing, and the key still replays.
    assert len(service.board.cards) == 1
    assert post(title="Original").json() == original.json()


def test_title_comparison_is_exact(service: Service) -> None:
    post(title="Title")
    # Titles are sent untrimmed, so a different spacing is a different request.
    assert post(title="Title ").status_code == 422


# --- Overlapping requests ---------------------------------------------------


def test_overlapping_request_is_refused_while_first_is_in_flight(
    service: Service,
) -> None:
    service.provider.gate = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        in_flight = pool.submit(post)
        assert service.provider.entered.wait(5)

        overlapping = post()
        assert overlapping.status_code == 409
        assert overlapping.json()["error"]["code"] == "request_in_progress"
        assert overlapping.headers["Retry-After"] == str(ATTEMPT_LEASE_SECONDS)

        service.provider.gate.set()
        first = in_flight.result(timeout=5)

    assert first.status_code == 201
    assert post().json() == first.json()
    assert len(service.board.cards) == 1


def test_many_simultaneous_claims_produce_one_issue(service: Service) -> None:
    store = IdempotencyStore(service.db_path, clock=service.clock)
    body = IssueCreate(title=TITLE)
    service.provider.gate = threading.Event()
    start = threading.Barrier(16)

    def attempt() -> str:
        start.wait()
        try:
            result = create_issue_idempotently(service.provider, store, KEY, body)
            return "replayed" if result.replayed else "created"
        except ServiceError as exc:
            return exc.code.value

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(attempt) for _ in range(16)]
        assert service.provider.entered.wait(5)
        # Hold the winner's provider call open until every other request
        # has been answered, so all 16 genuinely overlap.
        deadline = time.monotonic() + 10
        while sum(f.done() for f in futures) < 15:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        service.provider.gate.set()
        outcomes = [f.result(timeout=10) for f in futures]

    assert outcomes.count("created") == 1
    assert outcomes.count("request_in_progress") == 15
    assert service.provider.create_calls == 1
    assert len(service.board.cards) == 1


def test_claim_holds_the_write_lock_while_deciding(tmp_path: Path) -> None:
    # Two claims for one key must never both see "no record yet". That needs
    # the claim's read and its insert in one write-locked transaction, so no
    # other connection can write in between. The clock is read inside that
    # transaction; use it to try a competing write at that moment.
    db_path = tmp_path / "keys.db"
    competing_write_blocked: list[bool] = []

    def clock() -> float:
        other = sqlite3.connect(db_path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            competing_write_blocked.append(False)
        except sqlite3.OperationalError:
            competing_write_blocked.append(True)
        finally:
            other.close()
        return 1_800_000_000.0

    store = IdempotencyStore(db_path, clock=clock)
    store.claim(key_digest(KEY), request_fingerprint(IssueCreate(title=TITLE)))

    assert competing_write_blocked == [True]


# --- Uncertain provider outcomes --------------------------------------------


def test_lost_response_is_recovered_without_creating_again(service: Service) -> None:
    # Trello created the card, but the response never arrived.
    service.provider.create_then_fail = timeout_after_send()
    lost = post()
    assert lost.status_code == 502
    assert lost.json()["error"]["message"] == TIMEOUT_MESSAGE

    # The earlier request may still be taking effect: wait for the lease.
    early = post()
    assert early.status_code == 409
    assert int(early.headers["Retry-After"]) == ATTEMPT_LEASE_SECONDS

    service.clock.advance(ATTEMPT_LEASE_SECONDS)
    retry = post()

    assert retry.status_code == 201
    assert retry.headers["Idempotency-Replayed"] == "true"
    assert retry.json() == {"id": service.board.ids()[0], "title": TITLE}
    assert service.provider.create_calls == 1
    assert len(service.board.cards) == 1
    # Now recorded: later repeats need no lookup.
    lookups = service.provider.lookup_calls
    assert post().json() == retry.json()
    assert service.provider.lookup_calls == lookups


def test_uncertain_failure_that_created_nothing_creates_on_retry(
    service: Service,
) -> None:
    # The request was sent and timed out, but Trello never stored the card.
    service.provider.fail_before_create = timeout_after_send()
    assert post().status_code == 502

    service.clock.advance(ATTEMPT_LEASE_SECONDS)
    retry = post()

    assert retry.status_code == 201
    assert "Idempotency-Replayed" not in retry.headers
    assert service.provider.lookup_calls == 1
    assert len(service.board.cards) == 1


def test_certain_rejection_releases_the_key_immediately(service: Service) -> None:
    service.provider.fail_before_create = rejected()
    assert post().status_code == 502

    # Nothing was created, so no wait and no lookup is needed.
    retry = post()
    assert retry.status_code == 201
    assert service.provider.lookup_calls == 0
    assert len(service.board.cards) == 1


def test_unexpected_exception_after_create_is_treated_as_uncertain(
    service: Service,
) -> None:
    service.provider.create_then_fail = RuntimeError("bug after the write")
    assert post().status_code == 500

    assert post().status_code == 409
    service.clock.advance(ATTEMPT_LEASE_SECONDS)
    assert post().status_code == 201
    assert len(service.board.cards) == 1


def test_failed_lookup_creates_nothing_and_can_be_retried_at_once(
    service: Service,
) -> None:
    service.provider.create_then_fail = timeout_after_send()
    post()
    service.clock.advance(ATTEMPT_LEASE_SECONDS)

    service.provider.fail_lookup = timeout_after_send()
    assert post().status_code == 502
    # The lookup wrote nothing, so the next retry is not made to wait.
    retry = post()

    assert retry.status_code == 201
    assert retry.headers["Idempotency-Replayed"] == "true"
    assert service.provider.create_calls == 1
    assert len(service.board.cards) == 1


def test_repeated_lost_responses_still_yield_one_issue(service: Service) -> None:
    for _ in range(5):
        service.provider.create_then_fail = timeout_after_send()
        post()
        service.clock.advance(ATTEMPT_LEASE_SECONDS)
    # Only the first attempt created; every later one found that card.
    assert post().status_code == 201
    assert len(service.board.cards) == 1


# --- Restarts ---------------------------------------------------------------


def test_completed_key_replays_after_restart(service: Service) -> None:
    created = post()
    service.restart()

    replayed = post()
    assert replayed.json() == created.json()
    assert service.provider.create_calls == 0
    assert service.provider.lookup_calls == 0


def test_crash_after_provider_write_is_recovered_after_restart(
    service: Service,
) -> None:
    # The process claimed the key and Trello created the card, then the
    # process died before recording the result.
    store = IdempotencyStore(service.db_path, clock=service.clock)
    digest = key_digest(KEY)
    claim = store.claim(digest, request_fingerprint(IssueCreate(title=TITLE)))
    assert claim.kind is ClaimKind.NEW
    service.board.add(TITLE, digest)
    service.restart()

    assert post().status_code == 409
    service.clock.advance(ATTEMPT_LEASE_SECONDS)
    recovered = post()

    assert recovered.status_code == 201
    assert recovered.json()["id"] == service.board.ids()[0]
    assert service.provider.create_calls == 0
    assert len(service.board.cards) == 1


def test_crash_before_provider_write_creates_once_after_restart(
    service: Service,
) -> None:
    store = IdempotencyStore(service.db_path, clock=service.clock)
    store.claim(key_digest(KEY), request_fingerprint(IssueCreate(title=TITLE)))
    service.restart()

    service.clock.advance(ATTEMPT_LEASE_SECONDS)
    assert post().status_code == 201
    assert post().status_code == 201
    assert service.provider.create_calls == 1
    assert len(service.board.cards) == 1


def test_failure_to_record_result_does_not_cause_a_duplicate(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_complete(self: IdempotencyStore, *args: object) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(IdempotencyStore, "complete", broken_complete)
        first = post()
    # The issue exists, so the caller is told so.
    assert first.status_code == 201

    service.clock.advance(ATTEMPT_LEASE_SECONDS)
    assert post().json() == first.json()
    assert len(service.board.cards) == 1


# --- Retention --------------------------------------------------------------


def test_key_is_kept_for_the_retention_period(service: Service) -> None:
    created = post()
    service.clock.advance(KEY_RETENTION_SECONDS - 1)
    assert post().json() == created.json()
    assert len(service.board.cards) == 1


def test_key_is_forgotten_after_the_retention_period(service: Service) -> None:
    post()
    service.clock.advance(KEY_RETENTION_SECONDS)
    # Documented boundary: an expired key starts a new logical request.
    assert post(title="Reused much later").status_code == 201
    assert len(service.board.cards) == 2


def test_expired_keys_are_purged(service: Service) -> None:
    post(KEY)
    service.clock.advance(KEY_RETENTION_SECONDS)
    post(OTHER_KEY)
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT count(*) FROM idempotency_keys").fetchone() == (1,)


# --- Key validation and scope -----------------------------------------------


@pytest.mark.parametrize("key", ["", "a" * 256, "has space", "ключ", "tab\tkey"])
def test_invalid_keys_are_rejected_before_any_work(service: Service, key: str) -> None:
    response = http().post(
        "/issues",
        json={"title": TITLE},
        headers={"Idempotency-Key": key.encode("utf-8")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert response.json()["error"]["details"][0]["field"] == "Idempotency-Key"
    assert service.provider.create_calls == 0
    assert not service.db_path.exists()


def test_longest_valid_key_is_accepted(service: Service) -> None:
    assert post("k" * 255).status_code == 201


def test_raw_key_is_never_stored_or_sent_to_the_provider(service: Service) -> None:
    post()
    assert KEY.encode() not in service.db_path.read_bytes()
    assert all(KEY not in (card[2] or "") for card in service.board.cards)


def test_provider_without_idempotency_support_refuses_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BasicProvider:
        created = 0

        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise AssertionError("not used")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("not used")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("not used")

        def create_issue(self, title: str) -> Issue:
            BasicProvider.created += 1
            return Issue(id="1", title=title)

    def supply_provider() -> IssueProvider:
        return BasicProvider()

    def supply_store() -> IdempotencyStore:
        return IdempotencyStore(tmp_path / "keys.db")

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )
    monkeypatch.setitem(
        main.app.dependency_overrides, get_idempotency_store, supply_store
    )

    response = post()
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Idempotent creation is not available"
    assert BasicProvider.created == 0


def test_trello_provider_supports_idempotent_creation() -> None:
    assert isinstance(provider.TrelloIssueProvider(), IdempotentIssueProvider)


# --- Real Trello provider, simulated HTTP -----------------------------------

LIST_ID = "5f1c0a2b3d4e5f6071829304"
CARD_ID = "64f1c0a2b3d4e5f607182930"


class FakeTrello:
    """Simulates Trello's card endpoints, keeping the cards it stores."""

    def __init__(self) -> None:
        self.cards: list[dict[str, str]] = []
        self.posts = 0
        self.lists_queried: list[dict[str, list[str]]] = []
        self.lose_next_post_response = False

    def __call__(self, request: Request, timeout: int) -> io.BytesIO:
        url = urlsplit(request.full_url)
        query = parse_qs(url.query)
        if request.get_method() == "POST" and url.path == "/1/cards":
            self.posts += 1
            card = {
                "id": f"{len(self.cards) + 0x64F1C0A2:08x}" + "0" * 16,
                "name": query["name"][0],
                "desc": query.get("desc", [""])[0],
                "idList": query["idList"][0],
            }
            self.cards.append(card)
            if self.lose_next_post_response:
                self.lose_next_post_response = False
                raise URLError(TimeoutError("timed out"))
            return io.BytesIO(json.dumps(card).encode())
        if request.get_method() == "GET" and url.path == f"/1/lists/{LIST_ID}/cards":
            self.lists_queried.append(query)
            return io.BytesIO(json.dumps(self.cards).encode())
        raise AssertionError(f"Unexpected request {request.get_method()} {url.path}")


@pytest.fixture
def trello(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FakeTrello, FakeClock]]:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)
    fake, clock = FakeTrello(), FakeClock()
    monkeypatch.setattr(provider, "urlopen", fake)

    def supply_store() -> IdempotencyStore:
        return IdempotencyStore(tmp_path / "keys.db", clock=clock)

    monkeypatch.setitem(
        main.app.dependency_overrides, get_idempotency_store, supply_store
    )
    yield fake, clock


def test_trello_card_is_tagged_with_the_creation_token(
    trello: tuple[FakeTrello, FakeClock],
) -> None:
    fake, _ = trello
    post()
    assert fake.cards[0]["desc"] == (f"{provider.TOKEN_MARKER_PREFIX}{key_digest(KEY)}")


def test_trello_lost_response_end_to_end_sends_one_create(
    trello: tuple[FakeTrello, FakeClock],
) -> None:
    fake, clock = trello
    fake.lose_next_post_response = True

    assert post().status_code == 502
    assert post().status_code == 409
    clock.advance(ATTEMPT_LEASE_SECONDS)
    retry = post()

    assert retry.status_code == 201
    assert retry.json() == {"id": fake.cards[0]["id"], "title": TITLE}
    assert fake.posts == 1
    assert fake.lists_queried == [{"fields": ["id,name,desc"], "filter": ["all"]}]


def test_trello_lookup_returns_oldest_exact_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)
    token = key_digest(KEY)
    marker = f"{provider.TOKEN_MARKER_PREFIX}{token}"
    cards = [
        {"id": "b" * 24, "name": "Newer duplicate", "desc": marker},
        {"id": "c" * 24, "name": "Prefix only", "desc": marker[:-1]},
        {"id": "0" * 24, "name": "Other token", "desc": marker[:-1] + "x"},
        {"id": "a" * 24, "name": "Oldest", "desc": f"edited by a person\n{marker}"},
    ]
    monkeypatch.setattr(
        provider,
        "urlopen",
        lambda request, timeout: io.BytesIO(json.dumps(cards).encode()),
    )
    found = provider.TrelloIssueProvider().find_tagged_issue(token)
    assert found == Issue(id="a" * 24, title="Oldest")


@pytest.mark.parametrize(
    ("failure", "may_have_applied"),
    [
        (HTTPError("u", 400, "m", Message(), None), False),
        (HTTPError("u", 401, "m", Message(), None), False),
        (HTTPError("u", 404, "m", Message(), None), False),
        (HTTPError("u", 429, "m", Message(), None), False),
        (HTTPError("u", 500, "m", Message(), None), True),
        (HTTPError("u", 503, "m", Message(), None), True),
        (URLError(ConnectionRefusedError()), False),
        (ConnectionRefusedError(), False),
        (URLError(socket.gaierror()), False),
        (URLError(TimeoutError()), True),
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (URLError(ConnectionResetError()), True),
    ],
)
def test_trello_create_failures_report_whether_a_card_may_exist(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, may_have_applied: bool
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)

    def fail(request: Request, timeout: int) -> None:
        raise failure

    monkeypatch.setattr(provider, "urlopen", fail)
    with pytest.raises(ServiceError) as raised:
        provider.TrelloIssueProvider().create_tagged_issue(TITLE, key_digest(KEY))
    assert raised.value.may_have_applied is may_have_applied


def test_trello_unreadable_success_body_may_have_created_a_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)
    monkeypatch.setattr(provider, "urlopen", lambda r, timeout: io.BytesIO(b"{}"))
    with pytest.raises(ServiceError) as raised:
        provider.TrelloIssueProvider().create_tagged_issue(TITLE, key_digest(KEY))
    assert raised.value.may_have_applied is True


def test_missing_configuration_creates_nothing(
    trello: tuple[FakeTrello, FakeClock], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _ = trello
    monkeypatch.delenv("TRELLO_LIST_ID")
    assert post().status_code == 503
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)
    # Released, not left pending: the retry creates at once.
    assert post().status_code == 201
    assert fake.posts == 1


# --- Integration with the cache, startup wiring, and failure logging --------


def test_startup_wires_the_store_and_the_cache_passes_tagged_calls_through(
    monkeypatch: pytest.MonkeyPatch, startup_db_path: Path
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")
    monkeypatch.setenv("TRELLO_LIST_ID", LIST_ID)
    fake = FakeTrello()
    monkeypatch.setattr(provider, "urlopen", fake)

    # No overrides: the client's startup supplied the cached Trello provider
    # and a store using IDEMPOTENCY_DB_PATH.
    assert isinstance(main.app.state.issue_provider, CachedIssueProvider)
    first = post()
    again = post()

    assert first.status_code == again.status_code == 201
    assert again.headers["Idempotency-Replayed"] == "true"
    assert fake.posts == 1
    assert startup_db_path.exists()


def test_cache_over_a_basic_provider_refuses_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BasicProvider:
        created = 0

        def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
            raise AssertionError("not used")

        def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
            raise AssertionError("not used")

        def delete_issue(self, issue_id: str) -> None:
            raise AssertionError("not used")

        def create_issue(self, title: str) -> Issue:
            BasicProvider.created += 1
            return Issue(id="1", title=title)

    cached = CachedIssueProvider(BasicProvider())

    def supply_provider() -> IssueProvider:
        return cached

    def supply_store() -> IdempotencyStore:
        return IdempotencyStore(tmp_path / "keys.db")

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )
    monkeypatch.setitem(
        main.app.dependency_overrides, get_idempotency_store, supply_store
    )

    assert post().status_code == 503
    assert BasicProvider.created == 0
    # Without a key the same provider still creates normally.
    assert post(None).status_code == 201


def failure_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.errors"
    ]


@pytest.mark.parametrize(
    ("failure", "write_outcome", "retry_status"),
    [
        # Certain: logged not_applied, and the key is released at once.
        (HTTPError("u", 429, "m", Message(), None), "not_applied", 201),
        # Unknown: logged unknown, and the key stays held for recovery.
        (URLError(TimeoutError("timed out")), "unknown", 409),
    ],
)
def test_logged_write_outcome_matches_what_happens_to_the_key(
    trello: tuple[FakeTrello, FakeClock],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    write_outcome: str,
    retry_status: int,
) -> None:

    def fail(request: Request, timeout: int) -> None:
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(provider, "urlopen", fail)
        assert post().status_code == 502

    [record] = failure_records(caplog)
    assert record["operation"] == "create_issue"
    assert record["write_outcome"] == write_outcome
    assert post().status_code == retry_status


def test_refused_overlap_and_reused_key_log_not_applied(
    service: Service, caplog: pytest.LogCaptureFixture
) -> None:
    post(title="Original")
    post(title="Different")
    service.provider.create_then_fail = timeout_after_send()
    post(OTHER_KEY)
    caplog.clear()
    post(OTHER_KEY)

    [record] = failure_records(caplog)
    assert record["error"] == "request_in_progress"
    assert record["write_outcome"] == "not_applied"
