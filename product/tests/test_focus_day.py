"""POST /v1/focus-day end to end, with the in-memory providers.

The fakes are chosen through the same *_PROVIDER variables as the real
providers, and failures are injected per step through each fake's
FailureSchedule.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from calendar_contract import CalendarError
from calendar_contract import ErrorCode as CalendarCode
from cloud_storage_api import (
    PermissionDeniedError,
    ProviderError,
    ProviderUnavailableError,
)
from fastapi.testclient import TestClient
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import MAX_LIST_LIMIT, Issue

from ospsd_team4_product.factories import Providers
from ospsd_team4_product.fakes import (
    InMemoryCalendarService,
    InMemoryDocumentStorage,
    InMemoryIssueProvider,
    InMemoryStateStore,
)
from ospsd_team4_product.main import app
from ospsd_team4_product.store import PlanBlock, StoreError

FAKES = {
    "TRACKER_PROVIDER": "ospsd_team4_product.fakes:InMemoryIssueProvider",
    "CALENDAR_PROVIDER": "ospsd_team4_product.fakes:InMemoryCalendarService",
    "STORAGE_PROVIDER": "ospsd_team4_product.fakes:InMemoryDocumentStorage",
    "DEFAULT_TIMEZONE": "America/New_York",
    "DEFAULT_WORKING_WINDOW": "09:00-17:00",
    "DEFAULT_MINUTES": "90",
    "STATE_DB_URL": "memory://",
}

REQUEST: dict[str, Any] = {
    "assignee": "tyler",
    "day": "2026-11-18",
    "timezone": "America/New_York",
    "working_window": {"start": "09:00", "end": "17:00"},
    "busy": [
        {"start": "2026-11-18T10:00:00-05:00", "end": "2026-11-18T11:00:00-05:00"}
    ],
    "default_minutes": 90,
    "replay": None,
}
DOC_PATH = "/standups/2026-11-18-tyler.md"
SECRET = "token=sk-live-SECRET"


class Service:
    """A running app plus the fake providers it built at startup."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        providers: Providers = client.app.state.providers  # type: ignore[attr-defined]
        assert isinstance(providers.tracker, InMemoryIssueProvider)
        assert isinstance(providers.calendar, InMemoryCalendarService)
        assert isinstance(providers.storage, InMemoryDocumentStorage)
        store = client.app.state.store  # type: ignore[attr-defined]
        assert isinstance(store, InMemoryStateStore)
        self.tracker = providers.tracker
        self.calendar = providers.calendar
        self.storage = providers.storage
        self.store = store

    def post(self, body: dict[str, Any]) -> Any:
        return self.client.post("/v1/focus-day", json=body)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[Service]:
    for name, value in FAKES.items():
        monkeypatch.setenv(name, value)
    with TestClient(app) as client:
        yield Service(client)


def replay_of(body: dict[str, Any]) -> dict[str, Any]:
    """The request a caller sends to retry: the same input plus `replay`."""
    return REQUEST | {"replay": {"plan": body["plan"], "created": body["created"]}}


def statuses(body: dict[str, Any]) -> dict[str, str]:
    return {step["name"]: step["status"] for step in body["steps"]}


# Success


def test_success_books_every_block_and_writes_the_doc(service: Service) -> None:
    response = service.post(REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "steps": [
            {"name": "validate", "status": "ok", "error": None},
            {"name": "check_doc", "status": "ok", "error": None},
            {"name": "list_issues", "status": "ok", "error": None},
            {"name": "plan", "status": "ok", "error": None},
            {"name": "create_events", "status": "ok", "error": None},
            {"name": "write_standup_doc", "status": "ok", "error": None},
            {"name": "save_plan", "status": "ok", "error": None},
        ],
        "plan": {
            "blocks": [
                {
                    "issue_id": "demo-1",
                    "title": "Draft the design doc",
                    "start": "2026-11-18T11:00:00-05:00",
                    "end": "2026-11-18T12:30:00-05:00",
                },
                {
                    "issue_id": "demo-2",
                    "title": "Review the partner integration",
                    "start": "2026-11-18T12:30:00-05:00",
                    "end": "2026-11-18T14:00:00-05:00",
                },
                {
                    "issue_id": "demo-3",
                    "title": "Write the standup summary",
                    "start": "2026-11-18T14:00:00-05:00",
                    "end": "2026-11-18T15:30:00-05:00",
                },
            ],
            "unscheduled": [],
        },
        "created": {
            "demo-1": "fake-event-1",
            "demo-2": "fake-event-2",
            "demo-3": "fake-event-3",
        },
        "doc": {"path": DOC_PATH, "id": "fake-file-1"},
        "issues_truncated": False,
        "failed_step": None,
    }
    assert (
        service.calendar.events["fake-event-1"].title == "Focus: Draft the design doc"
    )
    doc = service.storage.files[DOC_PATH].decode()
    assert doc.startswith("# Standup: tyler, 2026-11-18\n")
    assert "- 11:00-12:30 `demo-1` Draft the design doc" in doc


def test_partial_fit_is_a_success(service: Service) -> None:
    response = service.post(REQUEST | {"busy": [], "default_minutes": 240})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [block["issue_id"] for block in body["plan"]["blocks"]] == [
        "demo-1",
        "demo-2",
    ]
    assert body["plan"]["unscheduled"] == [
        {"issue_id": "demo-3", "reason": "no_free_slot"}
    ]
    assert list(body["created"]) == ["demo-1", "demo-2"]
    assert "- `demo-3`: no free slot" in service.storage.files[DOC_PATH].decode()


def test_defaults_come_from_configuration(service: Service) -> None:
    response = service.post({"assignee": "tyler", "day": "2026-11-18"})

    assert response.status_code == 200
    blocks = response.json()["plan"]["blocks"]
    assert blocks[0]["start"] == "2026-11-18T09:00:00-05:00"
    assert blocks[0]["end"] == "2026-11-18T10:30:00-05:00"


def test_more_than_100_issues_plans_the_newest_100_and_says_so(
    service: Service,
) -> None:
    service.tracker.issues = [
        Issue(id=f"issue-{n:03}", title=f"Issue {n}") for n in range(130)
    ]

    response = service.post(REQUEST | {"busy": [], "default_minutes": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["issues_truncated"] is True
    planned = {block["issue_id"] for block in body["plan"]["blocks"]} | {
        item["issue_id"] for item in body["plan"]["unscheduled"]
    }
    assert planned == {f"issue-{n:03}" for n in range(MAX_LIST_LIMIT)}


def test_truncation_is_reported_on_a_failure_after_list_issues(
    service: Service,
) -> None:
    service.tracker.issues = [
        Issue(id=f"issue-{n:03}", title=f"Issue {n}") for n in range(MAX_LIST_LIMIT)
    ]
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_UNAVAILABLE, "down", retryable=True),
    )

    body = service.post(REQUEST).json()

    assert body["failed_step"] == "create_events"
    assert body["issues_truncated"] is True


# The same request sent again without `replay` (F5)


def test_same_request_again_is_rejected_before_any_event(service: Service) -> None:
    first = service.post(REQUEST).json()
    doc = service.storage.files[DOC_PATH]

    response = service.post(REQUEST)

    assert response.status_code == 412
    body = response.json()
    assert body["status"] == "failed"
    assert body["failed_step"] == "check_doc"
    assert body["plan"] is None
    assert body["created"] == {}
    assert body["issues_truncated"] is None
    assert body["error"] == {
        "code": "precondition_failed",
        "message": (
            "A standup doc already exists for this assignee and day; "
            "replay the earlier response instead"
        ),
        "details": None,
        "step": "check_doc",
        "retryable": False,
        "may_have_applied": False,
    }
    assert statuses(body) == {
        "validate": "ok",
        "check_doc": "failed",
        "list_issues": "skipped",
        "plan": "skipped",
        "create_events": "skipped",
        "write_standup_doc": "skipped",
        "save_plan": "skipped",
    }
    # Only run 1's events exist, and its doc is untouched.
    assert set(service.calendar.events) == set(first["created"].values())
    assert service.storage.files[DOC_PATH] == doc
    assert service.tracker.failures.calls("list_issues") == 1


def test_a_doc_written_by_anyone_stops_the_run(service: Service) -> None:
    service.storage.create(DOC_PATH, b"old")

    response = service.post(REQUEST)

    assert response.status_code == 412
    assert service.calendar.events == {}
    assert service.storage.files[DOC_PATH] == b"old"


def test_another_assignee_or_day_is_not_affected(service: Service) -> None:
    assert service.post(REQUEST).status_code == 200

    assert service.post(REQUEST | {"assignee": "chan"}).status_code == 200
    assert service.post(REQUEST | {"day": "2026-11-19", "busy": []}).status_code == 200


def test_doc_check_failure_books_nothing(service: Service) -> None:
    service.storage.failures.fail("get_metadata", ProviderUnavailableError(SECRET))

    response = service.post(REQUEST)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "failed"
    assert body["failed_step"] == "check_doc"
    assert body["error"]["code"] == "provider_error"
    assert body["error"]["retryable"] is True
    assert body["error"]["may_have_applied"] is False
    assert service.tracker.failures.calls("list_issues") == 0
    assert service.calendar.failures.calls("create") == 0
    assert SECRET not in response.text


def test_replay_over_an_existing_doc_rewrites_it(service: Service) -> None:
    first = service.post(REQUEST).json()
    service.storage.files[DOC_PATH] = b"edited"

    response = service.post(replay_of(first))

    assert response.status_code == 200
    assert statuses(response.json())["check_doc"] == "skipped"
    assert service.storage.files[DOC_PATH].startswith(b"# Standup")


# Failures, one step at a time


def test_tracker_failure(service: Service) -> None:
    service.tracker.failures.fail(
        "list_issues",
        ServiceError(ErrorCode.PROVIDER_ERROR, "The issue tracker is unavailable"),
    )

    response = service.post(REQUEST)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "failed"
    assert body["failed_step"] == "list_issues"
    assert body["plan"] is None
    assert body["created"] == {}
    assert body["error"] == {
        "code": "provider_error",
        "message": "The issue tracker is unavailable",
        "details": None,
        "step": "list_issues",
        "retryable": True,
        "may_have_applied": False,
    }
    assert statuses(body) == {
        "validate": "ok",
        "check_doc": "ok",
        "list_issues": "failed",
        "plan": "skipped",
        "create_events": "skipped",
        "write_standup_doc": "skipped",
        "save_plan": "skipped",
    }
    assert body["steps"][2]["error"] == body["error"]
    assert body["issues_truncated"] is None
    assert service.calendar.failures.calls("create") == 0


def test_unexpected_tracker_exception_does_not_leak(service: Service) -> None:
    service.tracker.failures.fail("list_issues", RuntimeError(SECRET))

    response = service.post(REQUEST)

    assert response.status_code == 502
    assert SECRET not in response.text
    assert response.json()["error"]["code"] == "provider_error"


def test_calendar_failure_on_block_two_of_three(service: Service) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_UNAVAILABLE, SECRET, retryable=True),
        on_call=2,
    )

    response = service.post(REQUEST)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_step"] == "create_events"
    assert body["created"] == {"demo-1": "fake-event-1"}
    assert len(body["plan"]["blocks"]) == 3
    assert body["error"]["retryable"] is True
    assert body["error"]["may_have_applied"] is False
    assert statuses(body) == {
        "validate": "ok",
        "check_doc": "ok",
        "list_issues": "ok",
        "plan": "ok",
        "create_events": "failed",
        "write_standup_doc": "skipped",
        "save_plan": "skipped",
    }
    assert DOC_PATH not in service.storage.files
    assert SECRET not in response.text


def test_uncertain_create_is_reported_and_not_retried(service: Service) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_ERROR, "timeout", retryable=False),
    )

    response = service.post(REQUEST)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "failed"
    assert body["created"] == {}
    assert body["error"]["may_have_applied"] is True
    assert body["error"]["retryable"] is False
    assert "Check the calendar before retrying" in body["error"]["message"]
    assert service.calendar.failures.calls("create") == 1


def test_expired_calendar_token_is_a_configuration_failure(service: Service) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(
            CalendarCode.PROVIDER_UNAVAILABLE, "re-run authorize", retryable=False
        ),
    )

    response = service.post(REQUEST)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_configured"
    assert response.json()["error"]["step"] == "create_events"


def test_doc_failure_after_events_is_partial(service: Service) -> None:
    service.storage.failures.fail("replace", ProviderUnavailableError(SECRET))

    response = service.post(REQUEST)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_step"] == "write_standup_doc"
    assert len(body["created"]) == 3
    assert body["error"]["retryable"] is True
    assert body["error"]["may_have_applied"] is True
    assert statuses(body)["create_events"] == "ok"
    assert SECRET not in response.text


def test_uncertain_doc_write_is_retryable(service: Service) -> None:
    """Rewriting the doc gives the same bytes, so an uncertain write is safe to repeat."""
    service.storage.failures.fail("replace", ProviderError(SECRET))

    response = service.post(REQUEST)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_step"] == "write_standup_doc"
    assert body["error"]["code"] == "provider_error"
    assert body["error"]["retryable"] is True
    assert body["error"]["may_have_applied"] is True
    assert SECRET not in response.text


def test_missing_doc_folder_is_a_configuration_failure(service: Service) -> None:
    service.storage.folders.clear()

    response = service.post(REQUEST)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_step"] == "write_standup_doc"
    assert body["error"]["code"] == "service_not_configured"
    assert body["error"]["retryable"] is False
    assert body["error"]["may_have_applied"] is False
    assert len(body["created"]) == 3
    assert service.storage.files == {}

    # The operator creates the folder; a replay then only writes the doc.
    service.storage.folders.add("/standups")
    replayed = service.post(replay_of(body)).json()

    assert replayed["status"] == "ok"
    assert replayed["created"] == body["created"]
    assert replayed["doc"]["path"] == DOC_PATH
    assert service.calendar.failures.calls("create") == 3


def test_storage_failure_with_no_events_is_failed(service: Service) -> None:
    booked = {"start": "2026-11-18T09:00:00-05:00", "end": "2026-11-18T17:00:00-05:00"}
    service.storage.failures.fail("replace", PermissionDeniedError("denied"))

    response = service.post(REQUEST | {"busy": [booked]})

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "access_denied"
    assert body["plan"]["blocks"] == []
    assert len(body["plan"]["unscheduled"]) == 3


# Replay


def test_replay_skips_events_that_exist(service: Service) -> None:
    first = service.post(REQUEST).json()

    response = service.post(replay_of(first))

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == first["created"]
    assert body["plan"] == first["plan"]
    assert statuses(body) == {
        "validate": "ok",
        "check_doc": "skipped",
        "list_issues": "skipped",
        "plan": "skipped",
        "create_events": "ok",
        "write_standup_doc": "ok",
        "save_plan": "ok",
    }
    assert body["issues_truncated"] is None
    assert service.storage.failures.calls("get_metadata") == 1
    assert service.tracker.failures.calls("list_issues") == 1
    assert service.calendar.failures.calls("create") == 3
    assert service.calendar.failures.calls("get") == 3


def test_replay_recreates_an_event_that_was_not_found(service: Service) -> None:
    first = service.post(REQUEST).json()
    service.calendar.delete(first["created"]["demo-2"])

    body = service.post(replay_of(first)).json()

    assert body["status"] == "ok"
    assert body["created"] == {
        "demo-1": "fake-event-1",
        "demo-2": "fake-event-4",
        "demo-3": "fake-event-3",
    }
    assert set(service.calendar.events) == {
        "fake-event-1",
        "fake-event-3",
        "fake-event-4",
    }


def test_replay_after_a_calendar_failure_creates_only_the_missing_events(
    service: Service,
) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_UNAVAILABLE, "down", retryable=True),
        on_call=2,
    )
    first = service.post(REQUEST).json()

    body = service.post(replay_of(first)).json()

    assert body["status"] == "ok"
    assert list(body["created"]) == ["demo-1", "demo-2", "demo-3"]
    assert body["created"]["demo-1"] == "fake-event-1"
    assert len(service.calendar.events) == 3


def test_replay_after_a_doc_failure_only_writes_the_doc(service: Service) -> None:
    service.storage.failures.fail("replace", ProviderUnavailableError("down"))
    first = service.post(REQUEST).json()

    body = service.post(replay_of(first)).json()

    assert body["status"] == "ok"
    assert body["created"] == first["created"]
    assert service.calendar.failures.calls("create") == 3
    assert DOC_PATH in service.storage.files


def test_replay_stops_when_an_event_cannot_be_checked(service: Service) -> None:
    first = service.post(REQUEST).json()
    service.calendar.failures.fail(
        "get", CalendarError(CalendarCode.ACCESS_DENIED, "no", retryable=False)
    )

    response = service.post(replay_of(first))

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "partial"
    assert body["created"] == first["created"]
    assert body["error"]["may_have_applied"] is False


# Invalid input: rejected in the `validate` step before any provider call


def assert_rejected(service: Service, body: dict[str, Any], field: str) -> None:
    response = service.post(body)

    assert response.status_code == 422
    result = response.json()
    assert result["status"] == "failed"
    assert result["failed_step"] == "validate"
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["step"] == "validate"
    assert [detail["field"] for detail in result["error"]["details"]] == [field]
    assert statuses(result) == {
        "validate": "failed",
        "check_doc": "skipped",
        "list_issues": "skipped",
        "plan": "skipped",
        "create_events": "skipped",
        "write_standup_doc": "skipped",
        "save_plan": "skipped",
    }
    assert service.tracker.failures.calls("list_issues") == 0
    assert service.storage.failures.calls("get_metadata") == 0
    assert service.calendar.failures.calls("create") == 0
    assert service.storage.files == {}
    assert result["issues_truncated"] is None


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"assignee": "../etc"}, "assignee"),
        ({"assignee": ".."}, "assignee"),
        ({"assignee": "Tyler"}, "assignee"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"working_window": {"start": "17:00", "end": "09:00"}}, "working_window"),
        (
            {"busy": [{"start": "2026-11-18T10:00:00", "end": "2026-11-18T11:00:00Z"}]},
            "busy[0].start",
        ),
        (
            {
                "busy": [
                    {
                        "start": "2026-11-18T11:00:00-05:00",
                        "end": "2026-11-18T10:00:00-05:00",
                    }
                ]
            },
            "busy[0]",
        ),
        # Schema errors use the same field names.
        ({"default_minutes": 0}, "default_minutes"),
        ({"day": "2026-02-30"}, "day"),
        ({"day": "18/11/2026"}, "day"),
        ({"working_window": {"start": "9:00", "end": "17:00"}}, "working_window.start"),
        ({"busy": [{"start": "x"}]}, "busy[0].end"),
        ({"surprise": True}, "surprise"),
    ],
)
def test_invalid_input_is_rejected_before_any_call(
    service: Service, change: dict[str, Any], field: str
) -> None:
    assert_rejected(service, REQUEST | change, field)


def test_missing_body_is_rejected(service: Service) -> None:
    response = service.client.post("/v1/focus-day")

    assert response.status_code == 422
    assert response.json()["failed_step"] == "validate"


def _replay_body(blocks: list[dict[str, str]], created: dict[str, str]) -> Any:
    return REQUEST | {
        "replay": {"plan": {"blocks": blocks, "unscheduled": []}, "created": created}
    }


BLOCK = {
    "issue_id": "demo-1",
    "title": "Draft",
    "start": "2026-11-18T09:00:00-05:00",
    "end": "2026-11-18T10:30:00-05:00",
}


@pytest.mark.parametrize(
    ("blocks", "created", "field"),
    [
        ([BLOCK | {"end": "2026-11-18T08:00:00-05:00"}], {}, "replay.plan.blocks[0]"),
        ([BLOCK | {"start": "tomorrow"}], {}, "replay.plan.blocks[0].start"),
        ([BLOCK, BLOCK], {}, "replay.plan.blocks[1].issue_id"),
        ([BLOCK], {"demo-9": "fake-event-1"}, "replay.created"),
    ],
)
def test_invalid_replay_is_rejected_before_any_call(
    service: Service,
    blocks: list[dict[str, str]],
    created: dict[str, str],
    field: str,
) -> None:
    assert_rejected(service, _replay_body(blocks, created), field)
    assert service.calendar.failures.calls("get") == 0


# Routing


def test_unknown_route_uses_the_hw1_error_shape(service: Service) -> None:
    response = service.client.get("/v1/nothing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Resource not found"}
    }


def test_wrong_method_uses_the_hw1_error_shape(service: Service) -> None:
    response = service.client.get("/v1/focus-day")

    assert response.status_code == 405
    assert response.json() == {
        "error": {"code": "invalid_input", "message": "Request not supported"}
    }


# The approved plan (save_plan step)

DAY = date(2026, 11, 18)


def saved_blocks(body: dict[str, Any]) -> tuple[PlanBlock, ...]:
    """The blocks a 200 response should have saved, with their event IDs."""
    return tuple(
        PlanBlock(
            issue_id=block["issue_id"],
            title=block["title"],
            start=block["start"],
            end=block["end"],
            event_id=body["created"][block["issue_id"]],
        )
        for block in body["plan"]["blocks"]
    )


def test_success_saves_the_plan_with_event_ids(service: Service) -> None:
    body = service.post(REQUEST).json()

    plan = service.store.get_plan("tyler", DAY)

    assert plan == saved_blocks(body)
    assert [block.event_id for block in plan] == [
        "fake-event-1",
        "fake-event-2",
        "fake-event-3",
    ]


def test_unscheduled_issues_are_not_saved_as_blocks(service: Service) -> None:
    body = service.post(REQUEST | {"busy": [], "default_minutes": 240}).json()

    plan = service.store.get_plan("tyler", DAY)

    assert plan is not None
    assert [block.issue_id for block in plan] == ["demo-1", "demo-2"]
    assert plan == saved_blocks(body)


def test_a_later_save_replaces_the_saved_plan(service: Service) -> None:
    # A second run for the same day must be a replay (check_doc, F5), so a
    # replay with a shorter plan stands in for any later save.
    first = service.post(REQUEST).json()
    kept = first["plan"]["blocks"][:1]
    created = {kept[0]["issue_id"]: first["created"][kept[0]["issue_id"]]}
    replay = REQUEST | {
        "replay": {"plan": {"blocks": kept, "unscheduled": []}, "created": created}
    }

    body = service.post(replay).json()

    assert body["status"] == "ok"
    assert service.store.get_plan("tyler", DAY) == saved_blocks(body)
    assert len(saved_blocks(body)) == 1


def test_partial_run_saves_nothing(service: Service) -> None:
    service.calendar.failures.fail(
        "create",
        CalendarError(CalendarCode.PROVIDER_UNAVAILABLE, "down", retryable=True),
        on_call=2,
    )

    body = service.post(REQUEST).json()

    assert body["status"] == "partial"
    assert statuses(body)["save_plan"] == "skipped"
    assert service.store.get_plan("tyler", DAY) is None
    assert service.store.failures.calls("save_plan") == 0


def test_failed_run_saves_nothing(service: Service) -> None:
    service.tracker.failures.fail(
        "list_issues",
        ServiceError(ErrorCode.PROVIDER_ERROR, "The issue tracker is unavailable"),
    )

    assert service.post(REQUEST).json()["status"] == "failed"
    assert service.store.get_plan("tyler", DAY) is None


def test_successful_replay_saves_the_plan(service: Service) -> None:
    service.storage.failures.fail("replace", ProviderUnavailableError("down"))
    first = service.post(REQUEST).json()
    assert service.store.get_plan("tyler", DAY) is None

    body = service.post(replay_of(first)).json()

    assert body["status"] == "ok"
    assert service.store.get_plan("tyler", DAY) == saved_blocks(body)


def test_save_failure_is_a_retryable_internal_error(service: Service) -> None:
    service.store.failures.fail(
        "save_plan", StoreError("The state store is unavailable", cause=SECRET)
    )

    response = service.post(REQUEST)

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_step"] == "save_plan"
    assert len(body["created"]) == 3
    assert body["error"] == {
        "code": "internal_error",
        "message": (
            "Your calendar blocks and standup doc are done, but the plan could"
            " not be saved. Retry with replay to save it."
        ),
        "details": None,
        "step": "save_plan",
        "retryable": True,
        "may_have_applied": False,
    }
    assert body["steps"] == [
        {"name": "validate", "status": "ok", "error": None},
        {"name": "check_doc", "status": "ok", "error": None},
        {"name": "list_issues", "status": "ok", "error": None},
        {"name": "plan", "status": "ok", "error": None},
        {"name": "create_events", "status": "ok", "error": None},
        {"name": "write_standup_doc", "status": "ok", "error": None},
        {"name": "save_plan", "status": "failed", "error": body["error"]},
    ]
    assert DOC_PATH in service.storage.files
    assert SECRET not in response.text
    assert service.store.get_plan("tyler", DAY) is None


def test_replay_after_a_save_failure_saves_without_new_events(
    service: Service,
) -> None:
    service.store.failures.fail("save_plan", StoreError("down"))
    first = service.post(REQUEST).json()

    body = service.post(replay_of(first)).json()

    assert body["status"] == "ok"
    assert body["created"] == first["created"]
    assert service.calendar.failures.calls("create") == 3
    assert service.store.get_plan("tyler", DAY) == saved_blocks(body)
