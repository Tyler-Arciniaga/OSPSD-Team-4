"""Calendar adapter: event content and error mapping for create_events."""

import pytest
from calendar_contract import CalendarError
from calendar_contract import ErrorCode as CalendarCode
from issue_tracker_contract.errors import ErrorCode

from ospsd_team4_product.adapters.calendar import (
    MAX_TITLE_LENGTH,
    CalendarAdapter,
    event_title,
)
from ospsd_team4_product.errors import StepError
from ospsd_team4_product.fakes import InMemoryCalendarService
from ospsd_team4_product.planner import Block

BLOCK = Block(
    issue_id="abc",
    title="Fix login",
    start="2026-11-18T09:00:00-05:00",
    end="2026-11-18T10:30:00-05:00",
)
SECRET = "token=sk-live-123 /private/path"


def calendar_error(code: CalendarCode, *, retryable: bool) -> CalendarError:
    return CalendarError(code, SECRET, retryable=retryable, details={"x": SECRET})


def test_create_block_writes_focus_event() -> None:
    service = InMemoryCalendarService()

    event_id = CalendarAdapter(service).create_block(BLOCK)

    event = service.events[event_id]
    assert (event.title, event.start, event.end) == (
        "Focus: Fix login",
        BLOCK.start,
        BLOCK.end,
    )


def test_event_title_is_one_line_and_capped() -> None:
    assert event_title("  Fix\nthe   login\t") == "Focus: Fix the login"
    long = event_title("x" * 300)
    assert len(long) == MAX_TITLE_LENGTH
    assert long.startswith("Focus: x") and long.endswith("...")


def test_event_exists() -> None:
    service = InMemoryCalendarService()
    adapter = CalendarAdapter(service)
    event_id = adapter.create_block(BLOCK)

    assert adapter.event_exists(event_id) is True
    assert adapter.event_exists("missing") is False


@pytest.mark.parametrize(
    ("error", "code", "retryable", "may_have_applied"),
    [
        (
            calendar_error(CalendarCode.INVALID_INPUT, retryable=False),
            ErrorCode.INVALID_INPUT,
            False,
            False,
        ),
        (
            calendar_error(CalendarCode.NOT_FOUND, retryable=False),
            ErrorCode.NOT_FOUND,
            False,
            False,
        ),
        (
            calendar_error(CalendarCode.ACCESS_DENIED, retryable=False),
            ErrorCode.ACCESS_DENIED,
            False,
            False,
        ),
        (
            calendar_error(CalendarCode.PROVIDER_UNAVAILABLE, retryable=True),
            ErrorCode.PROVIDER_ERROR,
            True,
            False,
        ),
        (
            calendar_error(CalendarCode.PROVIDER_UNAVAILABLE, retryable=False),
            ErrorCode.SERVICE_NOT_CONFIGURED,
            False,
            False,
        ),
        (
            calendar_error(CalendarCode.PROVIDER_ERROR, retryable=False),
            ErrorCode.PROVIDER_ERROR,
            False,
            True,
        ),
        (
            calendar_error(CalendarCode.PROVIDER_ERROR, retryable=True),
            ErrorCode.PROVIDER_ERROR,
            True,
            False,
        ),
        (RuntimeError(SECRET), ErrorCode.PROVIDER_ERROR, False, True),
    ],
    ids=[
        "invalid-input",
        "not-found",
        "access-denied",
        "unavailable",
        "not-authorized",
        "uncertain-write",
        "retryable-provider-error",
        "unexpected-exception",
    ],
)
def test_create_errors_are_mapped(
    error: Exception, code: ErrorCode, retryable: bool, may_have_applied: bool
) -> None:
    service = InMemoryCalendarService(always_fail=error)

    with pytest.raises(StepError) as raised:
        CalendarAdapter(service).create_block(BLOCK)

    assert raised.value.step == "create_events"
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.may_have_applied is may_have_applied
    assert SECRET not in raised.value.message
    assert raised.value.details is None
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            calendar_error(CalendarCode.PROVIDER_ERROR, retryable=False),
            ErrorCode.PROVIDER_ERROR,
            False,
        ),
        (
            calendar_error(CalendarCode.PROVIDER_UNAVAILABLE, retryable=True),
            ErrorCode.PROVIDER_ERROR,
            True,
        ),
        (
            calendar_error(CalendarCode.PROVIDER_UNAVAILABLE, retryable=False),
            ErrorCode.SERVICE_NOT_CONFIGURED,
            False,
        ),
        (
            calendar_error(CalendarCode.ACCESS_DENIED, retryable=False),
            ErrorCode.ACCESS_DENIED,
            False,
        ),
        (RuntimeError(SECRET), ErrorCode.PROVIDER_ERROR, False),
    ],
)
def test_get_errors_are_mapped_and_never_may_have_applied(
    error: Exception, code: ErrorCode, retryable: bool
) -> None:
    service = InMemoryCalendarService(always_fail=error)

    with pytest.raises(StepError) as raised:
        CalendarAdapter(service).event_exists("abc")

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.may_have_applied is False
    assert SECRET not in raised.value.message


def test_invalid_block_is_an_internal_error_before_any_call() -> None:
    service = InMemoryCalendarService()
    bad = Block(issue_id="abc", title="t", start=BLOCK.end, end=BLOCK.start)

    with pytest.raises(StepError) as raised:
        CalendarAdapter(service).create_block(bad)

    assert raised.value.code == ErrorCode.INTERNAL_ERROR
    assert service.failures.calls("create") == 0
