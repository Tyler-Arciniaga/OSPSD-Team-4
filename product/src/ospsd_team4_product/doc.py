"""The standup doc: where it is stored and what it says. Pure, no I/O.

The path is built only from validated input, so it always satisfies the
shared storage path rule (absolute, no empty, `.` or `..` segments) and the
provider never rejects our own input.

The content is built from the plan, not read back from the calendar, and
contains no timestamps of its own: the same plan always gives the same
bytes, so writing it again is safe.
"""

import re
from datetime import date, datetime

from ospsd_team4_product.planner import Plan, PlanInputError

# Letters, digits, ".", "_" and "-". Not "." or "..", which are path segments
# with a special meaning.
_ASSIGNEE = re.compile(r"[A-Za-z0-9._-]{1,64}")

DOC_ROOT = "/standups"


def validate_assignee(assignee: str) -> str:
    if not _ASSIGNEE.fullmatch(assignee) or assignee in {".", ".."}:
        raise PlanInputError(
            "assignee",
            "must be 1 to 64 letters, digits, '.', '_' or '-', and not '.' or '..'",
        )
    return assignee


def doc_path(day: date, assignee: str) -> str:
    """`/standups/<YYYY-MM-DD>/<assignee>.md`."""
    return f"{DOC_ROOT}/{day.isoformat()}/{validate_assignee(assignee)}.md"


def _one_line(text: str) -> str:
    """Keep a provider title on one Markdown line."""
    return " ".join(text.split())


def _clock(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).strftime("%H:%M")


def render_doc(plan: Plan, *, day: date, assignee: str, timezone: str) -> bytes:
    """The standup doc as UTF-8 Markdown."""
    lines = [
        f"# Standup: {assignee}, {day.isoformat()}",
        "",
        f"Times are in {timezone}.",
        "",
        "## Focus blocks",
        "",
    ]
    if plan.blocks:
        lines += [
            f"- {_clock(block.start)}-{_clock(block.end)} "
            f"`{block.issue_id}` {_one_line(block.title)}"
            for block in plan.blocks
        ]
    else:
        lines.append("None scheduled.")
    lines += ["", "## Unscheduled", ""]
    if plan.unscheduled:
        lines += [
            f"- `{item.issue_id}`: {item.reason.replace('_', ' ')}"
            for item in plan.unscheduled
        ]
    else:
        lines.append("None.")
    return ("\n".join(lines) + "\n").encode("utf-8")
