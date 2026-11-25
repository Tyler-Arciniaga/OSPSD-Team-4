"""Turn the Focus day form into a `POST /v1/focus-day` body.

Only checks that need no product knowledge happen here: a required field is
empty, a time is not `HH:MM`, an end is not after its start, a number is not
a whole number. Anything else (assignee format, minute limits, …) is left
to the API's 422.

Blank working window or block minutes are left out of the request, so the
service's configured defaults apply.

Form field keys (used to place messages next to fields): `assignee`, `day`,
`window`, `block_minutes`, `timezone`, `busy[<i>]`.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
ISO_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

HHMM_HINT = "Use HH:MM, for example 09:30"


@dataclass(frozen=True)
class BusyRow:
    start: str
    end: str


@dataclass(frozen=True)
class FormValues:
    assignee: str
    day: str
    window_start: str
    window_end: str
    block_minutes: str
    timezone: str
    busy: tuple[BusyRow, ...] = ()


def parse_clock(text: str) -> time | None:
    match = CLOCK.match(text.strip())
    return time(int(match[1]), int(match[2])) if match else None


def parse_day(text: str) -> date | None:
    text = text.strip()
    if not ISO_DAY.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_zone(text: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(text.strip())
    except ZoneInfoNotFoundError, ValueError:
        return None


def to_iso(day: date, clock: time, zone: ZoneInfo) -> str:
    """`HH:MM` on `day` in `zone`, as RFC 3339 with whole seconds and an offset."""
    return datetime.combine(day, clock, tzinfo=zone).isoformat(timespec="seconds")


def _interval(start: str, end: str) -> tuple[tuple[time, time] | None, str | None]:
    """Both times parsed with start before end, or the problem."""
    if not start.strip() or not end.strip():
        return None, "Enter both start and end"
    begin, finish = parse_clock(start), parse_clock(end)
    if begin is None or finish is None:
        return None, HHMM_HINT
    if finish <= begin:
        return None, "End must be after start"
    return (begin, finish), None


def build_request(values: FormValues) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The request body, or None and a message per form field key."""
    errors: dict[str, str] = {}

    assignee = values.assignee.strip()
    if not assignee:
        errors["assignee"] = "Enter the assignee"

    day = parse_day(values.day)
    if not values.day.strip():
        errors["day"] = "Enter the day"
    elif day is None:
        errors["day"] = "Use YYYY-MM-DD, for example 2026-11-19"

    zone = parse_zone(values.timezone)
    if not values.timezone.strip():
        errors["timezone"] = "Enter the time zone"
    elif zone is None:
        errors["timezone"] = "Unknown time zone; use a name like America/New_York"

    window: tuple[time, time] | None = None
    if values.window_start.strip() or values.window_end.strip():
        window, problem = _interval(values.window_start, values.window_end)
        if problem:
            errors["window"] = problem

    minutes: int | None = None
    if values.block_minutes.strip():
        if values.block_minutes.strip().isdecimal():
            minutes = int(values.block_minutes.strip())
        else:
            errors["block_minutes"] = "Enter a whole number of minutes"

    busy: list[tuple[time, time]] = []
    for index, row in enumerate(values.busy):
        if not row.start.strip() and not row.end.strip():
            continue  # an empty row is ignored, not an error
        interval, problem = _interval(row.start, row.end)
        if problem:
            errors[f"busy[{index}]"] = problem
        elif interval:
            busy.append(interval)

    if errors or day is None or zone is None:
        return None, errors

    body: dict[str, Any] = {
        "assignee": assignee,
        "day": day.isoformat(),
        "timezone": values.timezone.strip(),
        "busy": [
            {"start": to_iso(day, start, zone), "end": to_iso(day, end, zone)}
            for start, end in busy
        ],
    }
    if window:
        body["working_window"] = {
            "start": window[0].strftime("%H:%M"),
            "end": window[1].strftime("%H:%M"),
        }
    if minutes is not None:
        body["default_minutes"] = minutes
    return body, {}


def form_key_for_api_field(field: str, values: FormValues) -> str | None:
    """Where an API `error.details[].field` belongs on the form, if anywhere.

    The request leaves empty busy rows out, so the API's `busy[i]` is the
    i-th filled row.
    """
    if field in ("assignee", "day", "timezone"):
        return field
    if field.startswith("working_window"):
        return "window"
    if field == "default_minutes":
        return "block_minutes"
    match = re.match(r"^busy\[(\d+)\]", field)
    if not match:
        return None
    filled = [
        index
        for index, row in enumerate(values.busy)
        if row.start.strip() or row.end.strip()
    ]
    sent = int(match[1])
    return f"busy[{filled[sent]}]" if sent < len(filled) else None
