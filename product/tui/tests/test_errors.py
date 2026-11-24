"""Every error case of ERRORS.md gets a message with a next step (issue #88).

One case per row of the issue's error table. After each error the person
submits again in the same app, which then succeeds: the app never crashes
and needs no restart.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest
from textual.widgets import Input

from focus_day_tui.app import FocusDayApp
from tests.bodies import INVALID_INPUT, OK, PARTIAL, failed
from tests.conftest import API_URL, FakeApi, screen_of, settle, text

MakeApp = Callable[..., FocusDayApp]


def _raise(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


@dataclass(frozen=True)
class Case:
    answer: httpx.Response | Callable[[httpx.Request], httpx.Response]
    expected: list[str]
    fields: dict[str, str] = field(default_factory=dict)


CASES = {
    "422 invalid_input with details": Case(
        httpx.Response(422, json=INVALID_INPUT),
        [
            "replay.created: names an issue with no block",
            "Fix these and submit again.",
        ],
        {"error-assignee": "must be 1 to 64 of A-Z a-z 0-9 . _ -"},
    ),
    "404 not_found": Case(
        httpx.Response(
            404,
            json=failed("list_issues", "not_found", "The issue list was not found"),
        ),
        [
            "The issue list was not found.",
            "Check that what the issue tracker needs still exists",
        ],
    ),
    "404 unknown path": Case(
        httpx.Response(
            404, json={"error": {"code": "not_found", "message": "Resource not found"}}
        ),
        ["Resource not found.", f"Check the API URL ({API_URL})"],
    ),
    "403 access_denied": Case(
        httpx.Response(
            403,
            json=failed("create_events", "access_denied", "The calendar denied access"),
        ),
        [
            "The calendar denied access.",
            "Ask the account owner to check permissions.",
        ],
    ),
    "503 service_not_configured": Case(
        httpx.Response(
            503,
            json=failed(
                "create_events",
                "service_not_configured",
                "The calendar is not authorized",
            ),
        ),
        [
            "The calendar is not configured.",
            "Ask the operator to re-authorize or configure it.",
        ],
    ),
    "412 precondition_failed": Case(
        httpx.Response(
            412,
            json=failed(
                "write_standup_doc",
                "precondition_failed",
                "The standup doc changed while it was written",
                plan=PARTIAL["plan"],
                created={"6afbc544721ea7f145ad1445": "fake-event-1"},
            ),
        ),
        [
            (
                "Your plan changed since you reviewed it. "
                "Submit again to see the new proposal."
            )
        ],
    ),
    "502 provider_error retryable": Case(
        httpx.Response(502, json=PARTIAL),
        [
            "The calendar is currently unavailable.",
            "Try again in a minute.",
            "Press r to finish booking.",
        ],
    ),
    "502 provider_error retryable, tracker": Case(
        httpx.Response(
            502,
            json=failed(
                "list_issues",
                "provider_error",
                "The issue tracker is currently unavailable",
                retryable=True,
            ),
        ),
        ["The issue tracker is currently unavailable.", "Try again in a minute."],
    ),
    "may_have_applied": Case(
        httpx.Response(
            502,
            json=failed(
                "create_events",
                "provider_error",
                "The calendar may have created this event; check it before retrying",
                may_have_applied=True,
                plan=PARTIAL["plan"],
                created={"6afbc544721ea7f145ad1445": "fake-event-1"},
            ),
        ),
        [
            (
                "This change may already have been applied. "
                "Check your calendar before retrying."
            )
        ],
    ),
    "500 internal_error": Case(
        httpx.Response(
            500,
            json={
                "error": {"code": "internal_error", "message": "Internal server error"}
            },
            headers={"X-Trace-ID": "trace-4f2a"},
        ),
        [
            (
                "The service hit an unexpected error. "
                "Report it with this trace ID: trace-4f2a."
            )
        ],
    ),
    "connection refused": Case(
        _raise(httpx.ConnectError("Connection refused")),
        [f"Cannot reach the service at {API_URL}. Is it running?"],
    ),
    "timeout": Case(
        _raise(httpx.ReadTimeout("timed out")),
        [
            (
                "The service did not answer within 5s. "
                "It may still finish: check before retrying."
            )
        ],
    ),
    "undocumented shape": Case(
        httpx.Response(200, json={"hello": "world"}),
        ["The service returned an unexpected response (HTTP 200)."],
    ),
    "not JSON": Case(
        httpx.Response(502, text="<html>Bad Gateway</html>"),
        ["The service returned an unexpected response (HTTP 502)."],
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
async def test_each_error_shows_what_to_do_next(
    case: Case, make_app: MakeApp, api: FakeApi
) -> None:
    api.responses.append(case.answer)
    api.reply(200, OK)
    app = make_app()
    async with app.run_test(size=(120, 50)) as pilot:
        screen_of(app).query_one("#assignee", Input).value = "tyler"
        await pilot.click("#submit")
        await settle(pilot)
        shown = text(app, "#messages")
        for line in case.expected:
            assert line in shown
        assert "Traceback" not in shown
        for widget_id, message in case.fields.items():
            assert message in text(app, f"#{widget_id}")

        # Fix and submit again, no restart.
        await pilot.click("#submit")
        await settle(pilot)
        assert "Your focus day is booked." in text(app, "#messages")
        for widget_id in case.fields:
            assert text(app, f"#{widget_id}") == ""


async def test_invalid_busy_rows_from_the_api_are_shown_on_their_row(
    make_app: MakeApp, api: FakeApi
) -> None:
    api.reply(422, INVALID_INPUT)
    app = make_app()
    async with app.run_test(size=(120, 50)) as pilot:
        screen = screen_of(app)
        screen.query_one("#assignee", Input).value = "tyler"
        for start, end in (("", ""), ("12:00", "13:00")):
            await pilot.click("#add-busy")
            await pilot.pause()
            row = list(screen.query(".busy-row"))[-1]
            row.query_one(".busy-start", Input).value = start
            row.query_one(".busy-end", Input).value = end
        await pilot.click("#submit")
        await settle(pilot)
        errors = [str(w.render()) for w in screen.query(".busy-row .field-error")]
        # The API's busy[0] is the first filled row, the second on screen.
        assert errors == ["", "start is not before end"]


async def test_an_unreachable_api_at_startup_still_opens_the_app(
    make_app: MakeApp, api: FakeApi
) -> None:
    api.ready = _raise(httpx.ConnectError("Connection refused"))
    app = make_app()
    async with app.run_test(size=(120, 50)) as pilot:
        await settle(pilot)
        assert app.is_running
        assert text(app, "#messages") == (
            f"Cannot reach the service at {API_URL}. Is it running?"
        )


def test_the_service_is_named_when_the_message_does_not_name_it() -> None:
    from focus_day_tui.client import ApiError
    from focus_day_tui.messages import error_lines

    error = ApiError("provider_error", "Upstream timed out", "write_standup_doc", True)
    assert error_lines(error)[0] == (
        "The storage service reported a problem: Upstream timed out."
    )


def test_a_provider_rejection_is_not_blamed_on_the_form() -> None:
    """Found running against the real API: storage `invalid_input` has no details."""
    from focus_day_tui.client import ApiError
    from focus_day_tui.messages import error_lines

    error = ApiError(
        "invalid_input", "Storage rejected the request", "write_standup_doc"
    )
    lines = error_lines(error)
    assert lines[0] == "Storage rejected the request."
    assert "Ask the operator to check its configuration." in lines[1]
    assert "Fix these and submit again." not in lines
