"""Focus-block planner: a pure function with no I/O.

Given the issues to work on, one day, a time zone, a working window and the
caller's busy intervals, place one block of `minutes` per issue in the
earliest free gap that fits it. Blocks are never split. An issue with no
gap that fits is listed as unscheduled; that is a normal outcome, not an
error.

All arithmetic is on UTC instants, so a day with a daylight-saving change
still gets blocks of the exact length. Output times are RFC 3339 strings in
the requested time zone, with an explicit offset and whole seconds, which is
what the shared calendar contract requires.

Invalid input raises PlanInputError before anything else happens; the
endpoint maps it to `invalid_input`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# RFC 3339 with whole seconds and an explicit offset (Z or +HH:MM).
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})"
)

UnscheduledReason = Literal["no_free_slot"]


class PlanInputError(ValueError):
    """A planner input is invalid. `field` names it in the request's terms."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


@dataclass(frozen=True)
class PlanIssue:
    id: str
    title: str


@dataclass(frozen=True)
class BusyInterval:
    """Caller-supplied busy time, as RFC 3339 strings."""

    start: str
    end: str


@dataclass(frozen=True)
class Block:
    issue_id: str
    title: str
    start: str
    end: str


@dataclass(frozen=True)
class Unscheduled:
    issue_id: str
    reason: UnscheduledReason


@dataclass(frozen=True)
class Plan:
    blocks: tuple[Block, ...]
    unscheduled: tuple[Unscheduled, ...]


# A half-open UTC interval [start, end).
_Interval = tuple[datetime, datetime]


def _parse_timestamp(value: str, field: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise PlanInputError(
            field,
            "must be an RFC 3339 timestamp with whole seconds and an explicit offset",
        )
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        raise PlanInputError(field, "is not a valid date and time") from None


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError, ValueError:
        raise PlanInputError("timezone", "must be an IANA time zone name") from None


def parse_interval(start: str, end: str, field: str) -> tuple[datetime, datetime]:
    """Parse an RFC 3339 interval as UTC instants; `field` names it in errors.

    Also used for the blocks of a replayed plan, which are checked by the
    same rule before any provider call.
    """
    start_at = _parse_timestamp(start, f"{field}.start")
    end_at = _parse_timestamp(end, f"{field}.end")
    if start_at >= end_at:
        raise PlanInputError(field, "start must be before end")
    return start_at, end_at


def _busy_intervals(busy: Sequence[BusyInterval]) -> list[_Interval]:
    return [
        parse_interval(interval.start, interval.end, f"busy[{index}]")
        for index, interval in enumerate(busy)
    ]


def _merge(intervals: list[_Interval], window: _Interval) -> list[_Interval]:
    """Clip to the window, sort, and merge overlapping or touching intervals."""
    window_start, window_end = window
    clipped = sorted(
        (max(start, window_start), min(end, window_end))
        for start, end in intervals
        if start < window_end and end > window_start
    )
    merged: list[_Interval] = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _free_gaps(busy: list[_Interval], window: _Interval) -> list[_Interval]:
    gaps = []
    cursor = window[0]
    for start, end in busy:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < window[1]:
        gaps.append((cursor, window[1]))
    return gaps


def _ordered_and_unique(issues: Sequence[PlanIssue]) -> list[PlanIssue]:
    """Sort by issue ID; keep the first of any repeated ID."""
    seen: set[str] = set()
    ordered = []
    for issue in sorted(issues, key=lambda issue: issue.id):
        if issue.id not in seen:
            seen.add(issue.id)
            ordered.append(issue)
    return ordered


def plan_day(
    issues: Sequence[PlanIssue],
    *,
    day: date,
    timezone: str,
    window_start: time,
    window_end: time,
    busy: Sequence[BusyInterval],
    minutes: int,
) -> Plan:
    """Place one `minutes`-long block per issue in the earliest gap that fits.

    Issues are taken in ascending ID order, so the result does not depend on
    the order the tracker returned them in.
    """
    zone = _zone(timezone)
    if window_start >= window_end:
        raise PlanInputError("working_window", "start must be before end")
    if minutes < 1:
        raise PlanInputError("default_minutes", "must be at least 1")
    busy_intervals = _busy_intervals(busy)

    window = (
        datetime.combine(day, window_start, tzinfo=zone).astimezone(UTC),
        datetime.combine(day, window_end, tzinfo=zone).astimezone(UTC),
    )
    gaps = _free_gaps(_merge(busy_intervals, window), window)
    duration = timedelta(minutes=minutes)

    blocks = []
    unscheduled = []
    for issue in _ordered_and_unique(issues):
        for index, (gap_start, gap_end) in enumerate(gaps):
            if gap_end - gap_start >= duration:
                block_end = gap_start + duration
                blocks.append(
                    Block(
                        issue_id=issue.id,
                        title=issue.title,
                        start=_format(gap_start, zone),
                        end=_format(block_end, zone),
                    )
                )
                gaps[index] = (block_end, gap_end)
                break
        else:
            unscheduled.append(Unscheduled(issue_id=issue.id, reason="no_free_slot"))

    blocks.sort(key=lambda block: datetime.fromisoformat(block.start))
    return Plan(blocks=tuple(blocks), unscheduled=tuple(unscheduled))


def _format(instant: datetime, zone: ZoneInfo) -> str:
    return instant.astimezone(zone).isoformat(timespec="seconds")
