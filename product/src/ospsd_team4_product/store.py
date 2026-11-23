"""The product's state: approved plans, remaining work and the completion log.

The state lives in a database shared by every service instance (product
docs, DESIGN.md "State store"), so any instance can answer from a plan
another instance saved. Each record is keyed by `assignee`:

- Approved plan: one per (assignee, day); saving again replaces it whole.
- Remaining share: the percent of an unfinished issue left, from 1 to 100.
- Completion log entry: one per (assignee, day, issue_id); recording it
  again updates it.

`StateStore` is what the rest of the service uses. `PostgresStateStore` is
the shared implementation; `ospsd_team4_product.fakes.InMemoryStateStore`
keeps everything in one process, for tests and the disposable environment.

Every write is an upsert inside a transaction, and the uniqueness and range
rules are database constraints, so concurrent requests on different
instances converge instead of failing or duplicating rows.

Failures raise StoreError. Its message is safe to show and never includes
the connection URL or a database message.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

import psycopg
from psycopg_pool import ConnectionPool

# How long to wait for a connection, at startup and per operation, before
# failing. Short, so an outage fails the request instead of hanging it.
CONNECT_TIMEOUT_SECONDS = 5
# Connections each instance keeps. Render's free Postgres allows about 100
# in total, so this leaves room for many instances.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5


class StoreError(Exception):
    """The store could not complete an operation; nothing was changed.

    `cause` is a short internal reason for logs only.
    """

    def __init__(self, message: str, *, cause: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class PlanBlock:
    """A booked focus block of an approved plan."""

    issue_id: str
    title: str
    start: str
    end: str
    event_id: str


@dataclass(frozen=True)
class PlanMatch:
    """A block of someone's approved plan, found by its issue."""

    assignee: str
    day: date
    block: PlanBlock


@dataclass(frozen=True)
class ProgressEntry:
    """What someone reported for an issue on a day.

    `remaining_percent` 0 means finished. `applied` records whether the
    reported change has been made in the providers. `recorded_at` must be
    time-zone aware and is converted to UTC.
    """

    assignee: str
    day: date
    issue_id: str
    remaining_percent: int
    recorded_at: datetime
    applied: bool = False

    def __post_init__(self) -> None:
        if not _is_int(self.remaining_percent) or not (
            0 <= self.remaining_percent <= 100
        ):
            raise ValueError("remaining_percent must be a whole number from 0 to 100")
        if self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must include a time zone")
        # Kept in UTC, so every store returns the same value.
        object.__setattr__(self, "recorded_at", self.recorded_at.astimezone(UTC))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_remaining(percent: int) -> None:
    """Raise ValueError unless `percent` is a whole number from 1 to 100."""
    if not _is_int(percent) or not 1 <= percent <= 100:
        raise ValueError("remaining percent must be a whole number from 1 to 100")


def check_blocks(blocks: Sequence[PlanBlock]) -> None:
    """Raise StoreError if an issue appears in more than one block.

    The in-memory store calls this; Postgres enforces the same rule with a
    constraint, so a bad plan fails inside the transaction there.
    """
    issue_ids = [block.issue_id for block in blocks]
    if len(issue_ids) != len(set(issue_ids)):
        raise StoreError(SAVE_FAILED, cause="duplicate_issue_in_plan")


SAVE_FAILED = "The plan could not be saved"
UNAVAILABLE = "The state store is unavailable"


class StateStore(Protocol):
    def save_plan(
        self, assignee: str, day: date, blocks: Sequence[PlanBlock]
    ) -> None: ...

    def get_plan(self, assignee: str, day: date) -> tuple[PlanBlock, ...] | None:
        """The approved plan's blocks in order, or None if none is saved."""
        ...

    def find_plans_with_issue(self, issue_id: str) -> list[PlanMatch]:
        """Every approved plan's block for this issue, newest day first."""
        ...

    def set_remaining(self, assignee: str, issue_id: str, percent: int) -> None:
        """Record the share left; raises ValueError outside 1 to 100."""
        ...

    def clear_remaining(self, assignee: str, issue_id: str) -> None: ...

    def get_remaining(self, assignee: str) -> dict[str, int]:
        """Issue ID to remaining percent, for this assignee only."""
        ...

    def log_progress(self, entry: ProgressEntry) -> None:
        """Insert the entry, or update it if (assignee, day, issue_id) exists."""
        ...

    def mark_applied(self, assignee: str, day: date, issue_id: str) -> bool:
        """Mark a log entry applied; False if there is no such entry."""
        ...

    def get_log(self, assignee: str, day: date) -> list[ProgressEntry]:
        """The day's entries for this assignee, ordered by issue ID."""
        ...

    def close(self) -> None: ...


# Run in one transaction at startup, holding an advisory lock so instances
# starting together do not race to create the same tables.
SCHEMA = """
SELECT pg_advisory_xact_lock(hashtext('ospsd_team4_product.schema'));

CREATE TABLE IF NOT EXISTS approved_plans (
    assignee text NOT NULL,
    day date NOT NULL,
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (assignee, day)
);

CREATE TABLE IF NOT EXISTS plan_blocks (
    assignee text NOT NULL,
    day date NOT NULL,
    position integer NOT NULL,
    issue_id text NOT NULL,
    title text NOT NULL,
    start_at text NOT NULL,
    end_at text NOT NULL,
    event_id text NOT NULL,
    PRIMARY KEY (assignee, day, position),
    UNIQUE (assignee, day, issue_id),
    FOREIGN KEY (assignee, day) REFERENCES approved_plans ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS plan_blocks_issue_id ON plan_blocks (issue_id);

CREATE TABLE IF NOT EXISTS remaining_work (
    assignee text NOT NULL,
    issue_id text NOT NULL,
    remaining_percent integer NOT NULL
        CHECK (remaining_percent BETWEEN 1 AND 100),
    PRIMARY KEY (assignee, issue_id)
);

CREATE TABLE IF NOT EXISTS progress_log (
    assignee text NOT NULL,
    day date NOT NULL,
    issue_id text NOT NULL,
    remaining_percent integer NOT NULL
        CHECK (remaining_percent BETWEEN 0 AND 100),
    recorded_at timestamptz NOT NULL,
    applied boolean NOT NULL DEFAULT false,
    PRIMARY KEY (assignee, day, issue_id)
);
"""

# Every connection reads and writes timestamps in UTC.
_CONNECTION_OPTIONS = "-c TimeZone=UTC"


class PostgresStateStore:
    """The shared store. Build it with `connect`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def connect(
        cls,
        url: str,
        *,
        min_size: int = POOL_MIN_SIZE,
        max_size: int = POOL_MAX_SIZE,
    ) -> PostgresStateStore:
        """Connect, create any missing tables, and open the pool.

        Raises StoreError if the database cannot be reached.
        """
        connection_kwargs: dict[str, object] = {
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "options": _CONNECTION_OPTIONS,
        }
        try:
            with psycopg.connect(url, **connection_kwargs) as conn:  # type: ignore[arg-type]
                conn.execute(SCHEMA)
        except psycopg.Error as exc:
            raise StoreError(UNAVAILABLE, cause=type(exc).__name__) from None
        pool = ConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs=connection_kwargs,
            timeout=CONNECT_TIMEOUT_SECONDS,
            # A connection the database dropped (for example after a
            # restart) is replaced instead of failing a request.
            check=ConnectionPool.check_connection,
            open=False,
        )
        try:
            pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS)
        except psycopg.Error as exc:
            pool.close()
            raise StoreError(UNAVAILABLE, cause=type(exc).__name__) from None
        return cls(pool)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _transaction(self, message: str = UNAVAILABLE) -> Iterator[psycopg.Connection]:
        """One transaction: committed on success, rolled back on any error."""
        try:
            with self._pool.connection() as conn:
                yield conn
        except psycopg.Error as exc:
            raise StoreError(message, cause=type(exc).__name__) from None

    def save_plan(self, assignee: str, day: date, blocks: Sequence[PlanBlock]) -> None:
        with self._transaction(SAVE_FAILED) as conn:
            # Locks the (assignee, day) row, so concurrent saves of the same
            # plan run one after the other; the last one wins.
            conn.execute(
                "INSERT INTO approved_plans (assignee, day) VALUES (%s, %s)"
                " ON CONFLICT (assignee, day) DO UPDATE SET saved_at = now()",
                (assignee, day),
            )
            conn.execute(
                "DELETE FROM plan_blocks WHERE assignee = %s AND day = %s",
                (assignee, day),
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO plan_blocks (assignee, day, position, issue_id,"
                    " title, start_at, end_at, event_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            assignee,
                            day,
                            position,
                            block.issue_id,
                            block.title,
                            block.start,
                            block.end,
                            block.event_id,
                        )
                        for position, block in enumerate(blocks)
                    ],
                )

    def get_plan(self, assignee: str, day: date) -> tuple[PlanBlock, ...] | None:
        with self._transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM approved_plans WHERE assignee = %s AND day = %s",
                (assignee, day),
            ).fetchone()
            if exists is None:
                return None
            rows = conn.execute(
                "SELECT issue_id, title, start_at, end_at, event_id FROM plan_blocks"
                " WHERE assignee = %s AND day = %s ORDER BY position",
                (assignee, day),
            ).fetchall()
        return tuple(PlanBlock(*row) for row in rows)

    def find_plans_with_issue(self, issue_id: str) -> list[PlanMatch]:
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT assignee, day, issue_id, title, start_at, end_at, event_id"
                " FROM plan_blocks WHERE issue_id = %s ORDER BY day DESC, assignee",
                (issue_id,),
            ).fetchall()
        return [
            PlanMatch(assignee=row[0], day=row[1], block=PlanBlock(*row[2:]))
            for row in rows
        ]

    def set_remaining(self, assignee: str, issue_id: str, percent: int) -> None:
        check_remaining(percent)
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO remaining_work (assignee, issue_id, remaining_percent)"
                " VALUES (%s, %s, %s) ON CONFLICT (assignee, issue_id)"
                " DO UPDATE SET remaining_percent = EXCLUDED.remaining_percent",
                (assignee, issue_id, percent),
            )

    def clear_remaining(self, assignee: str, issue_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM remaining_work WHERE assignee = %s AND issue_id = %s",
                (assignee, issue_id),
            )

    def get_remaining(self, assignee: str) -> dict[str, int]:
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT issue_id, remaining_percent FROM remaining_work"
                " WHERE assignee = %s ORDER BY issue_id",
                (assignee,),
            ).fetchall()
        return {issue_id: percent for issue_id, percent in rows}

    def log_progress(self, entry: ProgressEntry) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO progress_log (assignee, day, issue_id,"
                " remaining_percent, recorded_at, applied)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (assignee, day, issue_id) DO UPDATE SET"
                " remaining_percent = EXCLUDED.remaining_percent,"
                " recorded_at = EXCLUDED.recorded_at,"
                " applied = EXCLUDED.applied",
                (
                    entry.assignee,
                    entry.day,
                    entry.issue_id,
                    entry.remaining_percent,
                    entry.recorded_at,
                    entry.applied,
                ),
            )

    def mark_applied(self, assignee: str, day: date, issue_id: str) -> bool:
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE progress_log SET applied = true"
                " WHERE assignee = %s AND day = %s AND issue_id = %s",
                (assignee, day, issue_id),
            )
            return cursor.rowcount == 1

    def get_log(self, assignee: str, day: date) -> list[ProgressEntry]:
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT assignee, day, issue_id, remaining_percent, recorded_at,"
                " applied FROM progress_log WHERE assignee = %s AND day = %s"
                " ORDER BY issue_id",
                (assignee, day),
            ).fetchall()
        return [ProgressEntry(*row) for row in rows]
