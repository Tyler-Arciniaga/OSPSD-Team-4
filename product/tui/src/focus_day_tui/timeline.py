"""The day timeline: working window, busy times and the blocks of a run.

Pure: it takes plain values and returns Textual markup, so it is tested
without an app. One row per 30 minutes.

Which block failed is read from the response, not decided here: blocks are
booked in plan order (product/docs/DESIGN.md, replay), so when
`create_events` fails, the first block with no event in `created` is the
one that failed and the blocks after it were skipped.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from textual.markup import escape

from focus_day_tui.client import FocusDayResult

SLOT_MINUTES = 30
# Shown when the form has no working window (the service then uses its own).
DISPLAY_WINDOW = (9 * 60, 17 * 60)

Kind = Literal["booked", "failed", "skipped", "busy"]
STYLE: dict[Kind, tuple[str, str, str]] = {
    # glyph, markup style, status shown after the label
    "booked": ("██", "bold green", "booked ✓"),
    "failed": ("░░", "bold red", "failed ✗"),
    "skipped": ("··", "yellow", "skipped"),
    "busy": ("▒▒", "dim", "busy"),
}
# When several items share a slot, the first kind here is shown.
PRIORITY: tuple[Kind, ...] = ("failed", "booked", "skipped", "busy")


@dataclass(frozen=True)
class Item:
    kind: Kind
    start: int  # minutes since midnight, in the chosen time zone
    end: int
    label: str = ""


def legend() -> str:
    return "  ".join(f"[{STYLE[kind][1]}]{STYLE[kind][0]}[/] {kind}" for kind in STYLE)


def _clock(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _minutes(clock: time) -> int:
    return clock.hour * 60 + clock.minute


def _local_minutes(stamp: str, day: date, zone: ZoneInfo) -> int | None:
    """Minutes since `day` began in `zone`, or None if `stamp` is unusable."""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    local = moment.astimezone(zone)
    return (local.date() - day).days * 24 * 60 + _minutes(local.time())


def block_items(result: FocusDayResult, day: date, zone: ZoneInfo) -> list[Item]:
    """Each planned block as booked, failed or skipped."""
    if result.plan is None:
        return []
    items: list[Item] = []
    failed_seen = False
    for block in result.plan.blocks:
        if block.issue_id in result.created:
            kind: Kind = "booked"
        elif result.failed_step == "create_events" and not failed_seen:
            kind, failed_seen = "failed", True
        else:
            kind = "skipped"
        start = _local_minutes(block.start, day, zone)
        end = _local_minutes(block.end, day, zone)
        if start is None or end is None or end <= start:
            continue
        items.append(Item(kind, start, end, block.title))
    return items


def busy_items(busy: list[tuple[time, time]]) -> list[Item]:
    return [Item("busy", _minutes(start), _minutes(end)) for start, end in busy]


def render(
    day: date | None,
    zone_name: str,
    window: tuple[time, time] | None,
    items: list[Item],
) -> str:
    """The timeline as markup lines: a heading, the slots, the legend."""
    heading = (
        f"[b]{day.strftime('%a %Y-%m-%d')}[/b]  ({escape(zone_name)})"
        if day
        else "[b]Pick a day[/b]"
    )
    window_minutes = (
        (_minutes(window[0]), _minutes(window[1])) if window else DISPLAY_WINDOW
    )
    window_text = (
        f"window {window[0]:%H:%M}–{window[1]:%H:%M}"
        if window
        else "window: service default"
    )
    lines = [heading, f"[dim]{window_text}[/dim]"]

    first = min([window_minutes[0], *(item.start for item in items)])
    last = max([window_minutes[1], *(item.end for item in items)])
    first -= first % SLOT_MINUTES
    labelled: set[Item] = set()
    for slot in range(first, last, SLOT_MINUTES):
        clock = _clock(slot)
        in_window = window_minutes[0] <= slot < window_minutes[1]
        covering = [
            item
            for item in items
            if item.start < slot + SLOT_MINUTES and item.end > slot
        ]
        covering.sort(key=lambda item: (PRIORITY.index(item.kind), item.start))
        if not covering:
            mark = "  " if in_window else "[dim]--[/dim]"
            note = "" if in_window else "[dim]outside window[/dim]"
            lines.append(f"{clock} {mark} {note}".rstrip())
            continue
        item = covering[0]
        glyph, style, status = STYLE[item.kind]
        text = ""
        if item not in labelled:
            labelled.add(item)
            span = f"{_clock(item.start)}–{_clock(item.end)}"
            name = f" {escape(item.label)}" if item.label else ""
            text = f"{span}{name}  [{style}]{status}[/]"
        lines.append(f"{clock} [{style}]{glyph}[/] {text}".rstrip())
    lines.append("")
    lines.append(legend())
    return "\n".join(lines)
