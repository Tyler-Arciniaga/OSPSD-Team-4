"""Response bodies copied from the product's documentation.

- OK: product/docs/DESIGN.md, 4.1 step 5 (the live run's 200 response).
- PARTIAL and REPLAY_OK: product/docs/evidence/calendar-failure.md, the
  controlled outage on block 2 of 3 and its replay (the ERRORS.md example).
- Error bodies: product/docs/ERRORS.md, with the code, step and flags each
  table row documents.
"""

from typing import Any

DAY = "2026-11-19"
ZONE = "America/New_York"

OK: dict[str, Any] = {
    "status": "ok",
    "steps": [
        {"name": "list_issues", "status": "ok", "error": None},
        {"name": "plan", "status": "ok", "error": None},
        {"name": "create_events", "status": "ok", "error": None},
        {"name": "write_standup_doc", "status": "ok", "error": None},
    ],
    "plan": {
        "blocks": [
            {
                "issue_id": "6afbc544721ea7f145ad1445",
                "title": "fix auth bug",
                "start": "2026-11-19T09:00:00-05:00",
                "end": "2026-11-19T10:30:00-05:00",
            },
            {
                "issue_id": "6afbc54ac1bb3fbab0aa7860",
                "title": "update deployment docs",
                "start": "2026-11-19T10:30:00-05:00",
                "end": "2026-11-19T12:00:00-05:00",
            },
            {
                "issue_id": "6afbc55430fecf9043bc30e4",
                "title": "fix flaky idempotency test",
                "start": "2026-11-19T13:00:00-05:00",
                "end": "2026-11-19T14:30:00-05:00",
            },
        ],
        "unscheduled": [],
    },
    "created": {
        "6afbc544721ea7f145ad1445": "cnrnufjai4cf8av9rip72uk3sk",
        "6afbc54ac1bb3fbab0aa7860": "5b5a1mlvvijdrbu3652cv707q4",
        "6afbc55430fecf9043bc30e4": "jrvuc224n5m4hcejog2uva9egg",
    },
    "doc": {"path": "/standups/2026-11-19/tyler.md", "id": "id:oinkehfsr60AAAAAAAAADA"},
    "failed_step": None,
}

CALENDAR_OUTAGE = {
    "code": "provider_error",
    "message": "The calendar is currently unavailable",
    "details": None,
    "step": "create_events",
    "retryable": True,
    "may_have_applied": False,
}

PARTIAL: dict[str, Any] = {
    "status": "partial",
    "steps": [
        {"name": "list_issues", "status": "ok", "error": None},
        {"name": "plan", "status": "ok", "error": None},
        {"name": "create_events", "status": "failed", "error": CALENDAR_OUTAGE},
        {"name": "write_standup_doc", "status": "skipped", "error": None},
    ],
    "plan": OK["plan"],
    "created": {"6afbc544721ea7f145ad1445": "fake-event-1"},
    "doc": None,
    "failed_step": "create_events",
    "error": CALENDAR_OUTAGE,
}

# As recorded in the evidence: the replay's 200 body (its `plan` was left out
# of the excerpt, which the TUI tolerates).
REPLAY_OK: dict[str, Any] = {
    "status": "ok",
    "steps": [
        {"name": "list_issues", "status": "skipped", "error": None},
        {"name": "plan", "status": "skipped", "error": None},
        {"name": "create_events", "status": "ok", "error": None},
        {"name": "write_standup_doc", "status": "ok", "error": None},
    ],
    "created": {
        "6afbc544721ea7f145ad1445": "fake-event-1",
        "6afbc54ac1bb3fbab0aa7860": "fake-event-2",
        "6afbc55430fecf9043bc30e4": "fake-event-3",
    },
    "doc": {"path": "/standups/2026-11-19/tyler.md", "id": "id:oinkehfsr60AAAAAAAAAFQ"},
    "failed_step": None,
    "error": None,
}


def failed(
    step: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    may_have_applied: bool = False,
    details: list[dict[str, str]] | None = None,
    plan: dict[str, Any] | None = None,
    created: dict[str, str] | None = None,
) -> dict[str, Any]:
    """An ERRORS.md error body for `step` failing with `code`."""
    error = {
        "code": code,
        "message": message,
        "details": details,
        "step": step,
        "retryable": retryable,
        "may_have_applied": may_have_applied,
    }
    order = ["list_issues", "plan", "create_events", "write_standup_doc"]
    index = order.index(step)
    steps = [
        {
            "name": name,
            "status": "ok" if i < index else "failed" if i == index else "skipped",
            "error": error if i == index else None,
        }
        for i, name in enumerate(order)
    ]
    return {
        "status": "partial" if created else "failed",
        "steps": steps,
        "plan": plan,
        "created": created or {},
        "doc": None,
        "failed_step": step,
        "error": error,
    }


INVALID_INPUT = failed(
    "plan",
    "invalid_input",
    "The request is invalid",
    details=[
        {"field": "assignee", "message": "must be 1 to 64 of A-Z a-z 0-9 . _ -"},
        {"field": "busy[0]", "message": "start is not before end"},
        {"field": "replay.created", "message": "names an issue with no block"},
    ],
)
