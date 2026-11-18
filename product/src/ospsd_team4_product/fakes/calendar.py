"""In-memory CalendarService for tests and the disposable environment.

Select it with `CALENDAR_PROVIDER=ospsd_team4_product.fakes:InMemoryCalendarService`.
It passes the shared `CalendarServiceConformance` suite
(tests/test_fake_calendar_conformance.py). Events live only in this process.
"""

from itertools import count

from calendar_contract import (
    CalendarError,
    CreateEvent,
    ErrorCode,
    Event,
    ReplaceEvent,
)

from ospsd_team4_product.fakes._failures import FailureSchedule


class InMemoryCalendarService:
    def __init__(self, *, always_fail: Exception | None = None) -> None:
        self.events: dict[str, Event] = {}
        self.failures = FailureSchedule(always_fail)
        self._ids = count(1)

    def create(self, request: CreateEvent) -> Event:
        self.failures.check("create")
        event = Event(
            id=f"fake-event-{next(self._ids)}",
            title=request.title,
            start=request.start,
            end=request.end,
        )
        self.events[event.id] = event
        return event

    def get(self, event_id: str) -> Event:
        self.failures.check("get")
        return self._existing(event_id)

    def replace(self, event_id: str, request: ReplaceEvent) -> Event:
        self.failures.check("replace")
        self._existing(event_id)
        event = Event(
            id=event_id, title=request.title, start=request.start, end=request.end
        )
        self.events[event_id] = event
        return event

    def delete(self, event_id: str) -> None:
        self.failures.check("delete")
        self._existing(event_id)
        del self.events[event_id]

    def _existing(self, event_id: str) -> Event:
        try:
            return self.events[event_id]
        except KeyError:
            raise CalendarError(
                ErrorCode.NOT_FOUND, "Event not found", retryable=False
            ) from None


def calendar_outage_on_second_create() -> InMemoryCalendarService:
    """A calendar that goes down on its second `create`, then recovers.

    Select it with
    `CALENDAR_PROVIDER=ospsd_team4_product.fakes:calendar_outage_on_second_create`
    to show a controlled `create_events` failure through the running service
    (docs/evidence/calendar-failure.md): the first block's event is created,
    the second raises a retryable PROVIDER_UNAVAILABLE, and every later call
    succeeds, so a replay against the same process completes.
    """
    service = InMemoryCalendarService()
    service.failures.fail(
        "create",
        CalendarError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Controlled outage (injected)",
            retryable=True,
        ),
        on_call=2,
    )
    return service
