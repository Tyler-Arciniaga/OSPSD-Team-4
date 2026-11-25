"""The state store.

The behavior tests run on both implementations: the Postgres store (needs
TEST_STATE_DB_URL, see conftest.py) and the in-memory fake, so the fake used
by the focus-day tests and the disposable environment follows the same rules.
Restart, concurrency and constraint tests need Postgres only.
"""

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta, timezone

import psycopg
import pytest

from ospsd_team4_product.fakes import InMemoryStateStore
from ospsd_team4_product.store import (
    CalendarBlock,
    PlanBlock,
    PostgresStateStore,
    ProgressEntry,
    StateStore,
    StoreError,
)

DAY = date(2026, 11, 19)
NEXT_DAY = date(2026, 11, 20)
AT = datetime(2026, 11, 19, 15, 30, tzinfo=UTC)


def block(
    issue_id: str, start_hour: int = 9, event: str | None = None
) -> CalendarBlock:
    return CalendarBlock(
        issue_id=issue_id,
        title=f"Work on {issue_id}",
        start=f"2026-11-19T{start_hour:02d}:00:00-05:00",
        end=f"2026-11-19T{start_hour:02d}:45:00-05:00",
        event_id=event or f"event-{issue_id}",
    )


def entry(
    assignee: str = "tyler",
    issue_id: str = "card-1",
    percent: int = 25,
    *,
    day: date = DAY,
    applied: bool = False,
) -> ProgressEntry:
    return ProgressEntry(
        assignee=assignee,
        day=day,
        issue_id=issue_id,
        remaining_percent=percent,
        recorded_at=AT,
        applied=applied,
    )


@pytest.fixture(params=["postgres", "memory"])
def store(request: pytest.FixtureRequest) -> Iterator[StateStore]:
    if request.param == "memory":
        yield InMemoryStateStore()
        return
    url: str = request.getfixturevalue("postgres_url")
    postgres = PostgresStateStore.connect(url)
    yield postgres
    postgres.close()


# Approved plans


def test_saved_plan_reads_back_in_order(store: StateStore) -> None:
    blocks = [block("card-2", 9), block("card-1", 10)]

    store.save_plan("tyler", DAY, blocks)

    assert store.get_plan("tyler", DAY) == tuple(blocks)


def test_missing_plan_is_none_and_an_empty_plan_is_empty(store: StateStore) -> None:
    assert store.get_plan("tyler", DAY) is None

    store.save_plan("tyler", DAY, [])

    assert store.get_plan("tyler", DAY) == ()


def test_saving_again_replaces_the_plan(store: StateStore) -> None:
    store.save_plan("tyler", DAY, [block("card-1"), block("card-2", 10)])
    store.save_plan("tyler", DAY, [block("card-3", 11)])

    assert store.get_plan("tyler", DAY) == (block("card-3", 11),)
    assert store.find_plans_with_issue("card-1") == []


def test_a_failed_save_keeps_the_previous_plan(store: StateStore) -> None:
    store.save_plan("tyler", DAY, [block("card-1")])

    # card-2 twice: rejected partway through (by a constraint in Postgres).
    with pytest.raises(StoreError) as raised:
        store.save_plan(
            "tyler", DAY, [block("card-2"), block("card-3", 10), block("card-2", 11)]
        )

    assert str(raised.value) == "The plan could not be saved"
    assert store.get_plan("tyler", DAY) == (block("card-1"),)


def test_find_plans_with_issue_newest_day_first(store: StateStore) -> None:
    store.save_plan("tyler", DAY, [block("card-1", 9, "e1")])
    store.save_plan("bob", NEXT_DAY, [block("card-2"), block("card-1", 10, "e2")])
    store.save_plan("alice", NEXT_DAY, [block("card-1", 11, "e3")])
    store.save_plan("tyler", NEXT_DAY, [block("card-9")])

    assert store.find_plans_with_issue("card-1") == [
        PlanBlock("alice", NEXT_DAY, block("card-1", 11, "e3")),
        PlanBlock("bob", NEXT_DAY, block("card-1", 10, "e2")),
        PlanBlock("tyler", DAY, block("card-1", 9, "e1")),
    ]
    assert store.find_plans_with_issue("card-404") == []


def test_same_day_owner_is_the_latest_save(store: StateStore) -> None:
    store.save_plan("alice", DAY, [block("card-1", 9, "e1")])
    store.save_plan("bob", DAY, [block("card-1", 10, "e2")])

    owners = [match.assignee for match in store.find_plans_with_issue("card-1")]
    assert owners == ["bob", "alice"]


# Remaining share


@pytest.mark.parametrize("percent", [0, 101, -1, True])
def test_set_remaining_rejects_values_outside_1_to_100(
    store: StateStore, percent: int
) -> None:
    with pytest.raises(ValueError, match="1 to 100"):
        store.set_remaining("tyler", "card-1", percent)

    assert store.get_remaining("tyler") == {}


@pytest.mark.parametrize("percent", [1, 100])
def test_set_remaining_accepts_1_and_100(store: StateStore, percent: int) -> None:
    store.set_remaining("tyler", "card-1", percent)

    assert store.get_remaining("tyler") == {"card-1": percent}


def test_remaining_is_updated_and_cleared(store: StateStore) -> None:
    store.set_remaining("tyler", "card-1", 50)
    store.set_remaining("tyler", "card-2", 75)
    store.set_remaining("tyler", "card-1", 25)

    assert store.get_remaining("tyler") == {"card-1": 25, "card-2": 75}

    store.clear_remaining("tyler", "card-1")
    store.clear_remaining("tyler", "card-404")

    assert store.get_remaining("tyler") == {"card-2": 75}


# Completion log


def test_logging_the_same_entry_twice_keeps_one_with_the_second_value(
    store: StateStore,
) -> None:
    store.log_progress(entry(percent=50))
    store.log_progress(entry(percent=0, applied=True))

    assert store.get_log("tyler", DAY) == [entry(percent=0, applied=True)]


def test_log_is_per_day_ordered_by_issue(store: StateStore) -> None:
    store.log_progress(entry(issue_id="card-2"))
    store.log_progress(entry(issue_id="card-1", percent=0))
    store.log_progress(entry(issue_id="card-3", day=NEXT_DAY))

    assert store.get_log("tyler", DAY) == [
        entry(issue_id="card-1", percent=0),
        entry(issue_id="card-2"),
    ]
    assert store.get_log("tyler", date(2026, 1, 1)) == []


def test_mark_applied(store: StateStore) -> None:
    store.log_progress(entry())

    assert store.mark_applied("tyler", DAY, "card-1") is True
    assert store.get_log("tyler", DAY) == [entry(applied=True)]
    assert store.mark_applied("tyler", DAY, "card-404") is False
    assert store.mark_applied("bob", DAY, "card-1") is False


def test_recorded_at_comes_back_as_the_same_instant(store: StateStore) -> None:
    new_york = timezone(timedelta(hours=-5))
    store.log_progress(
        ProgressEntry(
            "tyler", DAY, "card-1", 10, datetime(2026, 11, 19, 10, 30, tzinfo=new_york)
        )
    )

    [logged] = store.get_log("tyler", DAY)

    assert logged.recorded_at == AT
    assert logged.recorded_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"remaining_percent": 101}, "0 to 100"),
        ({"remaining_percent": -1}, "0 to 100"),
        # Naive on purpose: the entry must reject it.
        ({"recorded_at": AT.replace(tzinfo=None)}, "time zone"),
    ],
)
def test_progress_entry_rejects_bad_values(
    change: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "assignee": "tyler",
        "day": DAY,
        "issue_id": "card-1",
        "remaining_percent": 0,
        "recorded_at": AT,
    }
    with pytest.raises(ValueError, match=message):
        ProgressEntry(**(values | change))  # type: ignore[arg-type]


# Assignees never see each other's rows


def test_reads_are_scoped_to_the_assignee(store: StateStore) -> None:
    store.save_plan("alice", DAY, [block("card-a")])
    store.save_plan("bob", DAY, [block("card-b")])
    store.set_remaining("alice", "card-a", 40)
    store.set_remaining("bob", "card-b", 60)
    store.log_progress(entry("alice", "card-a", 40))
    store.log_progress(entry("bob", "card-b", 60))
    store.clear_remaining("alice", "card-b")

    assert store.get_plan("alice", DAY) == (block("card-a"),)
    assert store.get_remaining("alice") == {"card-a": 40}
    assert store.get_log("alice", DAY) == [entry("alice", "card-a", 40)]
    assert store.get_remaining("bob") == {"card-b": 60}
    assert store.get_plan("carol", DAY) is None


# Postgres only


def test_plan_survives_closing_and_reopening(postgres_url: str) -> None:
    first = PostgresStateStore.connect(postgres_url)
    first.save_plan("tyler", DAY, [block("card-1"), block("card-2", 10)])
    first.close()

    second = PostgresStateStore.connect(postgres_url)
    try:
        assert second.get_plan("tyler", DAY) == (block("card-1"), block("card-2", 10))
    finally:
        second.close()


def run_concurrently(count: int, work: Callable[[int], None]) -> list[BaseException]:
    """Run `work(i)` for i in range(count) in threads started together."""
    barrier = threading.Barrier(count)
    errors: list[BaseException] = []

    def target(index: int) -> None:
        try:
            barrier.wait()
            work(index)
        except BaseException as exc:  # noqa: BLE001  (reported by the test)
            errors.append(exc)

    threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_writers_on_separate_connections_converge(
    postgres_url: str,
) -> None:
    """20 stores (standing in for 20 instances) write the same keys at once."""
    stores = [
        PostgresStateStore.connect(postgres_url, min_size=1, max_size=1)
        for _ in range(20)
    ]
    try:

        def work(index: int) -> None:
            store = stores[index]
            store.log_progress(entry(percent=index))
            store.set_remaining("tyler", "card-1", index + 1)
            store.save_plan(
                "tyler", DAY, [block(f"card-{index}-a"), block(f"card-{index}-b", 10)]
            )

        assert run_concurrently(20, work) == []

        reader = stores[0]
        [logged] = reader.get_log("tyler", DAY)
        assert 0 <= logged.remaining_percent < 20
        assert list(reader.get_remaining("tyler")) == ["card-1"]
        # One whole plan from one writer, never a mix of two.
        plan = reader.get_plan("tyler", DAY)
        assert plan is not None
        assert len(plan) == 2
        writer = plan[0].issue_id.removesuffix("-a")
        assert plan == (block(f"{writer}-a"), block(f"{writer}-b", 10))
    finally:
        for store in stores:
            store.close()


def test_instances_starting_together_create_the_tables_once(
    empty_postgres_url: str,
) -> None:
    opened: list[PostgresStateStore] = []

    def work(_: int) -> None:
        opened.append(
            PostgresStateStore.connect(empty_postgres_url, min_size=1, max_size=1)
        )

    try:
        assert run_concurrently(5, work) == []
        opened[0].save_plan("tyler", DAY, [block("card-1")])
        assert opened[4].get_plan("tyler", DAY) == (block("card-1"),)
    finally:
        for store in opened:
            store.close()


LOG_COLUMNS = "progress_log (assignee, day, issue_id, remaining_percent, recorded_at)"
REMAINING_ZERO = "INSERT INTO remaining_work VALUES ('tyler', 'card-1', 0)"
LOG_101 = (
    f"INSERT INTO {LOG_COLUMNS} VALUES ('tyler', '2026-11-19', 'card-1', 101, now())"
)
LOG_TWICE = (
    f"INSERT INTO {LOG_COLUMNS} VALUES ('tyler', '2026-11-19', 'card-1', 5, now()),"
    " ('tyler', '2026-11-19', 'card-1', 6, now())"
)
ORPHAN_BLOCK = (
    "INSERT INTO plan_blocks VALUES"
    " ('tyler', '2026-11-19', 0, 'card-1', 't', 's', 'e', 'ev')"
)


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        (REMAINING_ZERO, psycopg.errors.CheckViolation),
        (LOG_101, psycopg.errors.CheckViolation),
        (LOG_TWICE, psycopg.errors.UniqueViolation),
        (ORPHAN_BLOCK, psycopg.errors.ForeignKeyViolation),
    ],
)
def test_rules_are_database_constraints(
    postgres_url: str, statement: str, error: type[Exception]
) -> None:
    """The rules hold for any writer, not only through this module."""
    with psycopg.connect(postgres_url) as conn, pytest.raises(error):
        conn.execute(statement.encode())


def test_operations_fail_with_store_error_once_unavailable(
    postgres_url: str,
) -> None:
    store = PostgresStateStore.connect(postgres_url)
    store.close()

    with pytest.raises(StoreError, match="unavailable"):
        store.get_plan("tyler", DAY)
    with pytest.raises(StoreError, match="could not be saved"):
        store.save_plan("tyler", DAY, [block("card-1")])


def test_unreachable_database_raises_without_the_url() -> None:
    url = "postgresql://focus:sk-SECRET-pw@127.0.0.1:1/focus_day"

    with pytest.raises(StoreError) as raised:
        PostgresStateStore.connect(url)

    assert str(raised.value) == "The state store is unavailable"
    assert "SECRET" not in repr(raised.value)
    assert raised.value.__cause__ is None
