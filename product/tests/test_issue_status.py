"""GET /v1/issue-status, with the in-memory providers.

The store is seeded directly and every test sets "now" through the app's
clock, so nothing depends on the real date.
"""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from calendar_contract import CalendarError
from calendar_contract import ErrorCode as CalendarCode
from fastapi.testclient import TestClient
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import Issue

from ospsd_team4_product.main import app
from ospsd_team4_product.store import CalendarBlock, ProgressEntry, StoreError
from tests.test_focus_day import FAKES, REQUEST, SECRET, Service, replay_of

DAY = date(2026, 11, 20)
EARLIER = date(2026, 11, 18)
BEFORE_BLOCK = datetime(2026, 11, 20, 13, 0, tzinfo=UTC)  # 08:00 in New York
AFTER_BLOCK = datetime(2026, 11, 20, 14, 24, tzinfo=UTC)  # 09:24 in New York
LOGIN = {"issue_id": "card-c", "title": "Login page"}
SETTINGS = {"issue_id": "card-d", "title": "Settings page"}


def block(
    issue_id: str = "card-c", day: date = DAY, start: str = "09:00", end: str = "09:23"
) -> CalendarBlock:
    return CalendarBlock(
        issue_id=issue_id,
        title="Title when it was planned",
        start=f"{day}T{start}:00-05:00",
        end=f"{day}T{end}:00-05:00",
        event_id=f"event-{issue_id}-{day}",
    )


class StatusService(Service):
    def now(self, value: datetime) -> None:
        self.client.app.state.clock = lambda: value  # type: ignore[attr-defined]

    def get(self, **params: str) -> Any:
        return self.client.get("/v1/issue-status", params=params)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[StatusService]:
    for name, value in FAKES.items():
        monkeypatch.setenv(name, value)
    with TestClient(app) as client:
        service = StatusService(client)
        service.now(BEFORE_BLOCK)
        service.tracker.issues = [
            Issue(id="card-e", title="Fix flaky test"),
            Issue(id="card-d", title="Settings page"),
            Issue(id="card-c", title="Login page"),
        ]
        yield service


def seed(service: StatusService) -> None:
    """tyler's plan for DAY, with another issue first, as a real plan has."""
    service.store.save_plan(
        "tyler",
        DAY,
        [block("card-e", start="10:00", end="11:30"), block()],
    )


# Lookup


def test_issue_id_is_answered_from_the_saved_plan(service: StatusService) -> None:
    seed(service)
    service.store.set_remaining("tyler", "card-c", 25)

    response = service.get(issue_id="card-c", day="2026-11-20")

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "issue": LOGIN,
        "owner": "tyler",
        "day": "2026-11-20",
        "scheduled": {
            "start": "2026-11-20T09:00:00-05:00",
            "end": "2026-11-20T09:23:00-05:00",
        },
        "remaining_percent": 25,
        "last_planned_day": "2026-11-20",
        "passed_without_report": False,
    }


@pytest.mark.parametrize("title", ["login", "LOGIN P", "gin pa"])
def test_title_matches_a_case_insensitive_substring(
    service: StatusService, title: str
) -> None:
    seed(service)

    response = service.get(title=title, day="2026-11-20")

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["issue"] == LOGIN
    assert response.json()["owner"] == "tyler"


def test_several_title_matches_are_ambiguous(service: StatusService) -> None:
    seed(service)

    response = service.get(title="page")

    assert response.status_code == 200
    # The tracker lists Settings first, so the order shows the sort.
    assert response.json() == {"status": "ambiguous", "matches": [LOGIN, SETTINGS]}
    assert service.store.failures.calls("find_plans_with_issue") == 0


@pytest.mark.parametrize("params", [{"issue_id": "card-z"}, {"title": "no such"}])
def test_no_match_is_not_found(service: StatusService, params: dict[str, str]) -> None:
    seed(service)

    response = service.get(**params)

    assert response.status_code == 404
    assert response.json() == {
        "status": "failed",
        "failed_step": "find_issue",
        "error": {
            "code": "not_found",
            "message": "No issue matches",
            "details": None,
            "step": "find_issue",
            "retryable": False,
            "may_have_applied": False,
        },
    }


def test_a_planned_issue_deleted_from_the_tracker_is_not_found(
    service: StatusService,
) -> None:
    seed(service)
    service.tracker.delete_issue("card-c")

    response = service.get(issue_id="card-c")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "No issue matches"


def test_issue_id_is_matched_whole(service: StatusService) -> None:
    assert service.get(issue_id="card").status_code == 404


@pytest.mark.parametrize(
    ("params", "fields"),
    [
        ({}, ["issue_id", "title"]),
        ({"issue_id": "card-c", "title": "login"}, ["issue_id", "title"]),
        ({"title": ""}, ["title"]),
        ({"title": "  "}, ["title"]),
        ({"issue_id": ""}, ["issue_id"]),
        ({"issue_id": "card-c", "day": "20/11/2026"}, ["day"]),
        ({"issue_id": "card-c", "day": "2026-02-30"}, ["day"]),
        ({"issue_id": "card-c", "day": ""}, ["day"]),
        ({"issue_id": "card-c", "surprise": "1"}, ["surprise"]),
    ],
)
def test_invalid_input_is_rejected_before_any_call(
    service: StatusService, params: dict[str, str], fields: list[str]
) -> None:
    response = service.get(**params)

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "failed"
    assert body["failed_step"] == "find_issue"
    assert body["error"]["code"] == "invalid_input"
    assert body["error"]["step"] == "find_issue"
    assert body["error"]["retryable"] is False
    assert body["error"]["may_have_applied"] is False
    assert [detail["field"] for detail in body["error"]["details"]] == fields
    assert service.tracker.failures.calls("list_issues") == 0
    assert service.tracker.failures.calls("get_issue") == 0


# Plan answers


def test_issue_in_no_saved_plan(service: StatusService) -> None:
    seed(service)

    response = service.get(issue_id="card-d")

    assert response.status_code == 200
    assert response.json() == {"status": "no_approved_plan", "issue": SETTINGS}


def test_issue_planned_only_on_an_earlier_day(service: StatusService) -> None:
    service.store.save_plan("tyler", EARLIER, [block(day=EARLIER)])
    service.store.save_plan("tyler", DAY, [block("card-d")])

    body = service.get(issue_id="card-c", day="2026-11-20").json()

    assert body["status"] == "answered"
    assert body["owner"] == "tyler"
    assert body["day"] == "2026-11-20"
    assert body["scheduled"] is None
    assert body["last_planned_day"] == "2026-11-18"


def test_owner_is_the_assignee_of_the_newest_plan(service: StatusService) -> None:
    # bob's plan is newer by day but saved first; alice sorts before bob.
    service.store.save_plan("bob", DAY, [block()])
    service.store.save_plan("alice", EARLIER, [block(day=EARLIER)])

    body = service.get(issue_id="card-c", day="2026-11-18").json()

    assert body["owner"] == "bob"
    assert body["last_planned_day"] == "2026-11-20"
    # alice's block that day is not the owner's.
    assert body["scheduled"] is None


def test_day_defaults_to_today_in_the_default_timezone(service: StatusService) -> None:
    seed(service)
    service.now(datetime(2026, 11, 21, 3, 0, tzinfo=UTC))  # still the 20th there

    body = service.get(issue_id="card-c").json()

    assert body["day"] == "2026-11-20"
    assert body["scheduled"]["start"] == "2026-11-20T09:00:00-05:00"


def test_drafts_are_never_reported(service: StatusService) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_UNAVAILABLE, "down", retryable=True),
        on_call=2,
    )
    draft = service.post(REQUEST).json()
    assert draft["failed_step"] == "create_events"
    assert list(draft["created"]) == ["card-c"]

    response = service.get(issue_id="card-c", day=REQUEST["day"])

    assert response.status_code == 200
    assert response.json() == {"status": "no_approved_plan", "issue": LOGIN}

    # Once the run is finished, the same query is answered.
    assert service.post(replay_of(draft)).status_code == 200
    body = service.get(issue_id="card-c", day=REQUEST["day"]).json()
    assert body["status"] == "answered"
    assert body["owner"] == "tyler"


@pytest.mark.parametrize(
    ("now", "logged", "expected"),
    [
        (AFTER_BLOCK, False, True),
        (AFTER_BLOCK, True, False),
        (BEFORE_BLOCK, False, False),
        # Exactly at the end, 09:23 in New York: it has not ended before now.
        (datetime(2026, 11, 20, 14, 23, tzinfo=UTC), False, False),
    ],
)
def test_passed_without_report(
    service: StatusService, now: datetime, logged: bool, expected: bool
) -> None:
    seed(service)
    # None of these is an entry for (tyler, DAY, card-c).
    entries = [
        ("tyler", DAY, "card-e"),
        ("tyler", EARLIER, "card-c"),
        ("bob", DAY, "card-c"),
    ]
    if logged:
        entries.append(("tyler", DAY, "card-c"))
    for assignee, day, issue_id in entries:
        service.store.log_progress(
            ProgressEntry(
                assignee=assignee,
                day=day,
                issue_id=issue_id,
                remaining_percent=50,
                recorded_at=BEFORE_BLOCK,
            )
        )
    service.now(now)

    body = service.get(issue_id="card-c", day="2026-11-20").json()

    assert body["passed_without_report"] is expected


def test_remaining_percent_is_the_stored_share(service: StatusService) -> None:
    seed(service)
    # Other shares that must not be reported for (tyler, card-c).
    service.store.set_remaining("bob", "card-c", 80)
    service.store.set_remaining("tyler", "card-e", 60)

    assert service.get(issue_id="card-c").json()["remaining_percent"] is None

    service.store.set_remaining("tyler", "card-c", 25)

    assert service.get(issue_id="card-c").json()["remaining_percent"] == 25


# Read-only

TRACKER_WRITES = ("create_issue", "update_issue", "delete_issue")
CALENDAR_WRITES = ("create", "replace", "delete")
STORAGE_WRITES = ("create", "replace", "move", "delete")
STORE_WRITES = (
    "save_plan",
    "set_remaining",
    "clear_remaining",
    "log_progress",
    "mark_applied",
)


def test_queries_write_nothing(service: StatusService) -> None:
    seed(service)
    service.store.set_remaining("tyler", "card-c", 25)
    service.store.log_progress(
        ProgressEntry(
            assignee="tyler",
            day=DAY,
            issue_id="card-c",
            remaining_percent=25,
            recorded_at=AFTER_BLOCK,
        )
    )

    def snapshot() -> dict[str, Any]:
        store = service.store
        return {
            "tracker": [service.tracker.failures.calls(op) for op in TRACKER_WRITES],
            "calendar": [service.calendar.failures.calls(op) for op in CALENDAR_WRITES],
            "storage": [service.storage.failures.calls(op) for op in STORAGE_WRITES],
            "store": [store.failures.calls(op) for op in STORE_WRITES],
            "plans": len(store._plans),
            "blocks": sum(len(blocks) for blocks in store._plans.values()),
            "remaining": len(store._remaining),
            "log": len(store._log),
        }

    before = snapshot()
    assert before["blocks"] == 2 and before["remaining"] == before["log"] == 1

    queries = [
        ({"issue_id": "card-c"}, 200),
        ({"title": "login", "day": "2026-11-19"}, 200),
        ({"title": "page"}, 200),
        ({"issue_id": "card-d"}, 200),
        ({"issue_id": "card-z"}, 404),
        ({}, 422),
    ]
    assert [service.get(**params).status_code for params, _ in queries] == [
        status for _, status in queries
    ]
    service.store.failures.fail("find_plans_with_issue", StoreError("down"))
    assert service.get(issue_id="card-c").status_code == 500

    assert snapshot() == before


# Failures


@pytest.mark.parametrize("params", [{"issue_id": "card-c"}, {"title": "login"}])
@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (ErrorCode.PROVIDER_ERROR, 502, True),
        (ErrorCode.ACCESS_DENIED, 403, False),
        (ErrorCode.SERVICE_NOT_CONFIGURED, 503, False),
    ],
)
def test_tracker_failure(
    service: StatusService,
    params: dict[str, str],
    code: ErrorCode,
    status: int,
    retryable: bool,
) -> None:
    seed(service)
    service.tracker.failures.always = ServiceError(code, "The issue tracker failed")

    response = service.get(**params)

    assert response.status_code == status
    assert response.json() == {
        "status": "failed",
        "failed_step": "find_issue",
        "error": {
            "code": code.value,
            "message": "The issue tracker failed",
            "details": None,
            "step": "find_issue",
            "retryable": retryable,
            "may_have_applied": False,
        },
    }


def test_unexpected_tracker_exception_does_not_leak(service: StatusService) -> None:
    service.tracker.failures.always = RuntimeError(SECRET)

    response = service.get(issue_id="card-c")

    assert response.status_code == 502
    assert response.json()["failed_step"] == "find_issue"
    assert response.json()["error"]["code"] == "provider_error"
    assert SECRET not in response.text


@pytest.mark.parametrize(
    "operation", ["find_plans_with_issue", "get_log", "get_remaining"]
)
def test_store_read_failure(service: StatusService, operation: str) -> None:
    seed(service)
    service.store.failures.fail(operation, StoreError(SECRET, cause="PoolTimeout"))

    response = service.get(issue_id="card-c")

    assert response.status_code == 500
    assert response.json() == {
        "status": "failed",
        "failed_step": "read_plan",
        "error": {
            "code": "internal_error",
            "message": "The saved plans could not be read. Try again later.",
            "details": None,
            "step": "read_plan",
            "retryable": True,
            "may_have_applied": False,
        },
    }


@pytest.mark.parametrize(
    ("params", "failing", "expected"),
    [
        (
            {"issue_id": "card-c"},
            "tracker",
            {
                "http_status": 502,
                "step": "find_issue",
                "code": "provider_error",
                "retryable": True,
                "cause": "timeout",
            },
        ),
        (
            {"issue_id": "card-c"},
            "store",
            {
                "http_status": 500,
                "step": "read_plan",
                "code": "internal_error",
                "retryable": True,
                "cause": "PoolTimeout",
            },
        ),
        (
            {"title": SECRET},
            None,
            {
                "http_status": 404,
                "step": "find_issue",
                "code": "not_found",
                "retryable": False,
                "cause": "no_match",
            },
        ),
        (
            {"issue_id": "card-c", "day": SECRET},
            None,
            {
                "http_status": 422,
                "step": "find_issue",
                "code": "invalid_input",
                "retryable": False,
                "cause": "invalid_input",
            },
        ),
        (
            {"issue_id": "card-c", "title": SECRET},
            None,
            {
                "http_status": 422,
                "step": "find_issue",
                "code": "invalid_input",
                "retryable": False,
                "cause": "invalid_input",
            },
        ),
    ],
)
def test_each_failure_is_one_log_line_without_text(
    service: StatusService,
    caplog: pytest.LogCaptureFixture,
    params: dict[str, str],
    failing: str | None,
    expected: dict[str, Any],
) -> None:
    seed(service)
    if failing == "tracker":
        service.tracker.failures.always = ServiceError(
            ErrorCode.PROVIDER_ERROR, SECRET, cause="timeout"
        )
    if failing == "store":
        service.store.failures.fail(
            "find_plans_with_issue", StoreError(SECRET, cause="PoolTimeout")
        )

    service.get(**params)

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("ospsd_team4_product")
    ]
    assert [json.loads(line) for line in lines] == [
        {
            "event": "issue_status_failed",
            "status": "failed",
            "may_have_applied": False,
        }
        | expected
    ]
    assert SECRET not in lines[0]


def test_answers_are_not_logged_as_failures(
    service: StatusService, caplog: pytest.LogCaptureFixture
) -> None:
    seed(service)

    assert service.get(issue_id="card-c").status_code == 200
    assert "issue_status_failed" not in caplog.text
