"""Idempotent issue creation.

A caller that sends `POST /issues` with an `Idempotency-Key` header gets at
most one issue per key, however many times or however concurrently it sends
the request, including across service restarts and after lost provider
responses. docs/api.md#idempotent-creation states the exact guarantee.

How it works:

1. Every key is recorded in a durable SQLite store *before* the provider is
   called. The record holds a hash of the key, a fingerprint of the request
   body, and the attempt's state.
2. The record is claimed with a lease. While an attempt holds the lease,
   overlapping requests with the same key are refused with `409` instead of
   creating a second issue.
3. The provider marks the issue it creates with a creation token derived
   from the key. If an attempt ends without a known outcome (lost response,
   crash, restart), the next attempt waits until the lease has run out, so
   the earlier provider request has finished, then looks the token up
   before creating anything.
4. Once an issue is known, the record stores it, and every later request
   with that key receives the same issue.
"""

import hashlib
import json
import logging
import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.errors import ErrorCode, ServiceError
from app.interface import IdempotentIssueProvider
from app.models import Issue, IssueCreate

logger = logging.getLogger("app.idempotency")

# How long a key is remembered after its first use. A key reused after this
# is treated as a new request.
KEY_RETENTION_SECONDS = 24 * 60 * 60

# How long one attempt may hold a key. It must exceed the longest time a
# provider request can still take effect after the service stops waiting for
# it (the provider timeout is 10 seconds per network operation).
ATTEMPT_LEASE_SECONDS = 60

# Keys are 1-255 visible ASCII characters, so they survive HTTP headers,
# logs and copy-and-paste unchanged. UUIDs are recommended.
IDEMPOTENCY_KEY_PATTERN = r"^[\x21-\x7e]+$"
MAX_IDEMPOTENCY_KEY_LENGTH = 255

# Keys belong to one operation. Every caller shares this namespace, because
# the service has no caller identity, so keys must be unique (such as UUIDs).
_CREATE_SCOPE = "POST /issues"


class ClaimKind(StrEnum):
    NEW = "new"  # First use of the key: create the issue.
    RECOVER = "recover"  # Earlier attempt ended without a known outcome.
    REPLAY = "replay"  # The issue is known: return it.
    IN_PROGRESS = "in_progress"  # Another attempt holds the key.
    REUSED = "reused"  # The key was used with a different request body.


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    attempt: int = 0
    issue: Issue | None = None
    retry_after: int = 0


def key_digest(idempotency_key: str) -> str:
    """Return the hash stored in place of a key and used as the creation token.

    The raw key is never stored or sent to the provider.
    """
    material = f"{_CREATE_SCOPE}\n{idempotency_key}".encode()
    return hashlib.sha256(material).hexdigest()


def request_fingerprint(body: IssueCreate) -> str:
    """Return a hash of everything in the request that affects the result.

    Titles are compared exactly, as they are sent to the provider untrimmed.
    """
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key_digest TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'completed')),
    attempt INTEGER NOT NULL,
    first_used_at REAL NOT NULL,
    lease_until REAL NOT NULL,
    issue_id TEXT,
    issue_title TEXT
)
"""


class IdempotencyStore:
    """Durable record of idempotency keys, shared by every service process.

    Each change runs in its own `BEGIN IMMEDIATE` transaction, so claims are
    atomic across threads and across processes using the same file. The
    database uses `synchronous=FULL`, so a committed claim survives a crash
    or power loss before the provider is called.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        retention_seconds: float = KEY_RETENTION_SECONDS,
        lease_seconds: float = ATTEMPT_LEASE_SECONDS,
    ) -> None:
        self.path = str(path)
        self.clock = clock
        self.retention_seconds = retention_seconds
        self.lease_seconds = lease_seconds

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(_SCHEMA)
        return connection

    def _transaction(
        self, work: Callable[[sqlite3.Connection, float], Claim | bool]
    ) -> Claim | bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = work(connection, self.clock())
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result
        finally:
            connection.close()

    def claim(self, digest: str, fingerprint: str) -> Claim:
        """Decide atomically what a request with this key may do."""

        def work(db: sqlite3.Connection, now: float) -> Claim:
            db.execute(
                "DELETE FROM idempotency_keys WHERE first_used_at <= ?",
                (now - self.retention_seconds,),
            )
            row = db.execute(
                "SELECT fingerprint, state, attempt, lease_until, issue_id,"
                " issue_title FROM idempotency_keys WHERE key_digest = ?",
                (digest,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO idempotency_keys (key_digest, fingerprint, state,"
                    " attempt, first_used_at, lease_until)"
                    " VALUES (?, ?, 'pending', 1, ?, ?)",
                    (digest, fingerprint, now, now + self.lease_seconds),
                )
                return Claim(ClaimKind.NEW, attempt=1)
            stored_fingerprint, state, attempt, lease_until, issue_id, title = row
            if stored_fingerprint != fingerprint:
                return Claim(ClaimKind.REUSED)
            if state == "completed":
                return Claim(ClaimKind.REPLAY, issue=Issue(id=issue_id, title=title))
            if lease_until > now:
                wait = max(1, math.ceil(lease_until - now))
                return Claim(ClaimKind.IN_PROGRESS, retry_after=wait)
            db.execute(
                "UPDATE idempotency_keys SET attempt = ?, lease_until = ?"
                " WHERE key_digest = ?",
                (attempt + 1, now + self.lease_seconds, digest),
            )
            return Claim(ClaimKind.RECOVER, attempt=attempt + 1)

        result = self._transaction(work)
        assert isinstance(result, Claim)
        return result

    def complete(self, digest: str, attempt: int, issue: Issue) -> bool:
        """Record the issue for the key; False if the attempt lost its lease."""

        def work(db: sqlite3.Connection, now: float) -> bool:
            cursor = db.execute(
                "UPDATE idempotency_keys SET state = 'completed', issue_id = ?,"
                " issue_title = ? WHERE key_digest = ? AND attempt = ?"
                " AND state = 'pending'",
                (issue.id, issue.title, digest, attempt),
            )
            return cursor.rowcount == 1

        return bool(self._transaction(work))

    def release(self, digest: str, attempt: int) -> None:
        """Forget the key: this attempt certainly created nothing."""

        def work(db: sqlite3.Connection, now: float) -> bool:
            db.execute(
                "DELETE FROM idempotency_keys WHERE key_digest = ? AND attempt = ?"
                " AND state = 'pending'",
                (digest, attempt),
            )
            return True

        self._transaction(work)

    def unlock(self, digest: str, attempt: int) -> None:
        """End the lease early: this attempt has no provider write in flight.

        The key stays pending, so the next attempt still looks for an issue
        an earlier attempt may have created.
        """

        def work(db: sqlite3.Connection, now: float) -> bool:
            db.execute(
                "UPDATE idempotency_keys SET lease_until = ?"
                " WHERE key_digest = ? AND attempt = ? AND state = 'pending'",
                (now, digest, attempt),
            )
            return True

        self._transaction(work)


@dataclass(frozen=True)
class CreationResult:
    issue: Issue
    # True when the issue was created by an earlier request with the same key.
    replayed: bool


def _log(event: str, **fields: object) -> None:
    logger.warning(json.dumps({"event": event, **fields}))


def create_issue_idempotently(
    provider: IdempotentIssueProvider,
    store: IdempotencyStore,
    idempotency_key: str,
    body: IssueCreate,
) -> CreationResult:
    """Create at most one issue for `idempotency_key`."""
    digest = key_digest(idempotency_key)
    claim = store.claim(digest, request_fingerprint(body))

    if claim.kind is ClaimKind.REUSED:
        raise ServiceError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "This idempotency key was already used with a different request",
            cause="fingerprint_mismatch",
        )
    if claim.kind is ClaimKind.IN_PROGRESS:
        raise ServiceError(
            ErrorCode.REQUEST_IN_PROGRESS,
            "A request with this idempotency key is still being processed",
            cause="key_leased",
            headers={"Retry-After": str(claim.retry_after)},
        )
    if claim.kind is ClaimKind.REPLAY:
        assert claim.issue is not None
        return CreationResult(claim.issue, replayed=True)

    if claim.kind is ClaimKind.RECOVER:
        # An earlier attempt may have created the issue. Its lease has run
        # out, so its provider request has finished: look before creating.
        try:
            found = provider.find_tagged_issue(digest)
        except BaseException:
            # The lookup wrote nothing, so the next attempt may look again
            # straight away.
            store.unlock(digest, claim.attempt)
            raise
        if found is not None:
            _record(store, digest, claim.attempt, found)
            return CreationResult(found, replayed=True)

    try:
        issue = provider.create_tagged_issue(body.title, digest)
    except ServiceError as exc:
        if not exc.may_have_applied:
            store.release(digest, claim.attempt)
        # Otherwise the key stays pending until its lease runs out; the next
        # attempt then looks the issue up before creating anything.
        raise
    # Any other exception leaves the key pending for the same reason.
    _record(store, digest, claim.attempt, issue)
    return CreationResult(issue, replayed=False)


def _record(store: IdempotencyStore, digest: str, attempt: int, issue: Issue) -> None:
    """Store the result. The issue exists either way, so it is still returned.

    If recording fails, the key stays pending and a later attempt finds the
    same issue by its creation token.
    """
    try:
        recorded = store.complete(digest, attempt, issue)
    except sqlite3.Error as exc:
        _log("idempotency_record_failed", cause=type(exc).__name__)
        return
    if not recorded:
        # The lease ran out while this attempt was still working. This breaks
        # the lease assumption in ATTEMPT_LEASE_SECONDS and is worth knowing.
        _log("idempotency_lease_lost", attempt=attempt)
