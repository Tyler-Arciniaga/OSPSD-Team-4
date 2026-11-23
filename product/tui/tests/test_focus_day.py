"""The Focus day screen, driven with Pilot against a fake API (issue #88)."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
from textual.pilot import Pilot
from textual.widgets import Input

from focus_day_tui.app import STOPPED_WAITING, WAITING_SUBMIT, FocusDayApp
from tests.bodies import DAY, OK, PARTIAL, REPLAY_OK, ZONE
from tests.conftest import FakeApi, screen_of, settle, text

MakeApp = Callable[..., FocusDayApp]
SIZE = (120, 50)


def field(app: FocusDayApp, widget_id: str) -> Input:
    return screen_of(app).query_one(f"#{widget_id}", Input)


def fill(app: FocusDayApp, **values: str) -> None:
    for name, value in values.items():
        field(app, name.replace("_", "-")).value = value


async def add_busy(pilot: Pilot[None], start: str, end: str) -> None:
    await pilot.click("#add-busy")
    await pilot.pause()
    row = list(screen_of(pilot.app).query(".busy-row"))[-1]  # type: ignore[arg-type]
    row.query_one(".busy-start", Input).value = start
    row.query_one(".busy-end", Input).value = end
    await pilot.pause()


async def submit(pilot: Pilot[None]) -> None:
    await pilot.click("#submit")
    await settle(pilot)


# Inputs


async def test_form_defaults_to_today_and_the_system_zone(make_app: MakeApp) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert field(app, "day").value == DAY
        assert field(app, "timezone").value == ZONE
        assert field(app, "assignee").value == ""


async def test_remembered_fields_come_from_the_last_successful_run(
    make_app: MakeApp, api: FakeApi, state_dir: Path
) -> None:
    api.reply(200, OK)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(
            app,
            assignee="tyler",
            window_start="08:30",
            window_end="16:00",
            block_minutes="45",
            timezone="Europe/Paris",
        )
        await submit(pilot)
    saved = json.loads((state_dir / "preferences.json").read_text())
    assert saved == {
        "assignee": "tyler",
        "window_start": "08:30",
        "window_end": "16:00",
        "block_minutes": "45",
        "timezone": "Europe/Paris",
    }

    reopened = make_app()
    async with reopened.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert field(reopened, "assignee").value == "tyler"
        assert field(reopened, "window-start").value == "08:30"
        assert field(reopened, "window-end").value == "16:00"
        assert field(reopened, "block-minutes").value == "45"
        assert field(reopened, "timezone").value == "Europe/Paris"
        assert field(reopened, "day").value == DAY  # the day is never remembered


async def test_busy_rows_are_sent_as_iso_intervals_on_the_chosen_day(
    make_app: MakeApp, api: FakeApi
) -> None:
    api.reply(200, OK)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler", window_start="09:00", window_end="17:00")
        await add_busy(pilot, "12:00", "13:00")
        await add_busy(pilot, "10:00", "10:15")
        await add_busy(pilot, "15:00", "16:00")
        # Remove the second row.
        removes = list(screen_of(app).query(".remove-busy"))
        await pilot.click(removes[1])
        await pilot.pause()
        await submit(pilot)
    assert api.posts == [
        {
            "assignee": "tyler",
            "day": DAY,
            "timezone": ZONE,
            "busy": [
                {
                    "start": "2026-11-19T12:00:00-05:00",
                    "end": "2026-11-19T13:00:00-05:00",
                },
                {
                    "start": "2026-11-19T15:00:00-05:00",
                    "end": "2026-11-19T16:00:00-05:00",
                },
            ],
            "working_window": {"start": "09:00", "end": "17:00"},
        }
    ]


async def test_blank_window_and_minutes_are_left_to_the_service(
    make_app: MakeApp, api: FakeApi
) -> None:
    api.reply(200, OK)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await submit(pilot)
    assert api.posts == [
        {"assignee": "tyler", "day": DAY, "timezone": ZONE, "busy": []}
    ]


async def test_the_timeline_shows_what_is_entered(make_app: MakeApp) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, window_start="10:00", window_end="12:00")
        await add_busy(pilot, "11:00", "11:30")
        shown = text(app, "#timeline")
    assert "Thu 2026-11-19" in shown
    assert ZONE in shown
    assert "window 10:00–12:00" in shown
    assert "11:00 ▒▒ 11:00–11:30  busy" in shown


# Form-level checks: shown next to the field, nothing sent.

FORM_PROBLEMS = [
    ({"assignee": ""}, "error-assignee", "Enter the assignee"),
    ({"day": ""}, "error-day", "Enter the day"),
    ({"day": "19/11/2026"}, "error-day", "YYYY-MM-DD"),
    ({"day": "2026-02-30"}, "error-day", "YYYY-MM-DD"),
    ({"timezone": ""}, "error-timezone", "Enter the time zone"),
    ({"timezone": "Mars/Base"}, "error-timezone", "Unknown time zone"),
    ({"window_start": "9am", "window_end": "17:00"}, "error-window", "HH:MM"),
    ({"window_start": "17:00", "window_end": "09:00"}, "error-window", "after start"),
    ({"window_start": "09:00"}, "error-window", "both start and end"),
    ({"block_minutes": "ninety"}, "error-block-minutes", "whole number"),
]


async def test_form_problems_are_shown_next_to_the_field_and_nothing_is_sent(
    make_app: MakeApp, api: FakeApi
) -> None:
    for values, error_id, expected in FORM_PROBLEMS:
        app = make_app()
        async with app.run_test(size=SIZE) as pilot:
            fill(app, assignee="tyler")
            fill(app, **values)
            await submit(pilot)
            assert expected in text(app, f"#{error_id}"), values
            assert "Fix these and submit again." in text(app, "#messages")
        assert api.posts == [], values


async def test_busy_row_problems_are_shown_on_the_row(
    make_app: MakeApp, api: FakeApi
) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await add_busy(pilot, "13:00", "12:00")
        await add_busy(pilot, "1pm", "14:00")
        await add_busy(pilot, "", "")  # an empty row is ignored
        await submit(pilot)
        rows = list(screen_of(app).query(".busy-row .field-error"))
        assert "End must be after start" in str(rows[0].render())
        assert "HH:MM" in str(rows[1].render())
        assert str(rows[2].render()) == ""
    assert api.posts == []


# Results


async def test_a_successful_run_shows_steps_timeline_and_doc(
    make_app: MakeApp, api: FakeApi
) -> None:
    api.reply(200, OK)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await add_busy(pilot, "12:00", "13:00")
        await submit(pilot)
        steps = text(app, "#steps")
        shown = text(app, "#timeline")
        assert steps.splitlines() == [
            "✓ Read your issues (3)",
            "✓ Planned your day",
            "✓ Booked calendar blocks",
            "✓ Wrote standup doc",
        ]
        assert "09:00 ██ 09:00–10:30 fix auth bug  booked ✓" in shown
        assert "10:30 ██ 10:30–12:00 update deployment docs  booked ✓" in shown
        assert "12:00 ▒▒ 12:00–13:00  busy" in shown
        assert "13:00 ██ 13:00–14:30 fix flaky idempotency test  booked ✓" in shown
        assert "██ booked  ░░ failed  ·· skipped  ▒▒ busy" in shown
        assert "Your focus day is booked." in text(app, "#messages")
        assert "/standups/2026-11-19/tyler.md" in text(app, "#doc")

        await pilot.press("c")
        assert app.clipboard == "/standups/2026-11-19/tyler.md"


async def test_unscheduled_issues_are_listed_with_the_reason(
    make_app: MakeApp, api: FakeApi
) -> None:
    body = json.loads(json.dumps(OK))
    body["plan"]["unscheduled"] = [{"issue_id": "demo-4", "reason": "no_free_slot"}]
    api.reply(200, body)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await submit(pilot)
        assert text(app, "#unscheduled") == "Not scheduled (no free slot): demo-4"
        assert "✓ Read your issues (4)" in text(app, "#steps")


async def test_while_waiting_a_loading_message_shows_and_esc_stops_waiting(
    make_app: MakeApp, api: FakeApi
) -> None:
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=OK)

    api.responses.append(slow)  # type: ignore[arg-type]
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await pilot.click("#submit")
        await pilot.pause()
        assert screen_of(app).query_one("#loading").display
        assert text(app, "#loading-text") == WAITING_SUBMIT
        assert "Planning and booking your focus blocks…" == WAITING_SUBMIT

        await pilot.press("escape")
        await pilot.pause()
        assert not screen_of(app).query_one("#loading").display
        assert text(app, "#messages") == STOPPED_WAITING
        release.set()


async def test_q_quits_while_waiting(make_app: MakeApp, api: FakeApi) -> None:
    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError

    api.responses.append(never)  # type: ignore[arg-type]
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await pilot.click("#submit")
        await pilot.pause()
        assert screen_of(app).query_one("#loading").display
        await pilot.press("q")
        await pilot.pause()
    assert app.return_code == 0


# Partial failure and Retry


async def test_a_partial_run_shows_what_was_booked_failed_and_skipped(
    make_app: MakeApp, api: FakeApi, state_dir: Path
) -> None:
    api.reply(502, PARTIAL)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await submit(pilot)
        shown = text(app, "#timeline")
        assert "09:00 ██ 09:00–10:30 fix auth bug  booked ✓" in shown
        assert "10:30 ░░ 10:30–12:00 update deployment docs  failed ✗" in shown
        assert "13:00 ·· 13:00–14:30 fix flaky idempotency test  skipped" in shown
        assert text(app, "#steps").splitlines() == [
            "✓ Read your issues (3)",
            "✓ Planned your day",
            "✗ Booked calendar blocks: The calendar is currently unavailable",
            "– Wrote standup doc (skipped)",
        ]
        assert "Press r to finish booking." in text(app, "#messages")
        bindings = {b.binding.action for b in app.active_bindings.values()}
        assert "retry" in bindings

    saved = json.loads((state_dir / "last-focus-day.json").read_text())
    assert saved["plan"] == PARTIAL["plan"]
    assert saved["created"] == PARTIAL["created"]
    assert saved["request"]["assignee"] == "tyler"


async def test_retry_replays_the_saved_run_and_then_deletes_it(
    make_app: MakeApp, api: FakeApi, state_dir: Path
) -> None:
    api.reply(502, PARTIAL)
    api.reply(200, REPLAY_OK)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await submit(pilot)
        await pilot.press("r")
        await settle(pilot)
        assert "Your focus day is booked." in text(app, "#messages")
        shown = text(app, "#timeline")
        assert "10:30 ██ 10:30–12:00 update deployment docs  booked ✓" in shown
        assert "– Read your issues (skipped)" in text(app, "#steps")

        await pilot.press("r")
        await pilot.pause()
        assert text(app, "#messages") == "Nothing to retry."

    first, retry = api.posts
    assert retry == {
        **first,
        "replay": {"plan": PARTIAL["plan"], "created": PARTIAL["created"]},
    }
    assert not (state_dir / "last-focus-day.json").exists()


async def test_retry_works_after_the_app_is_reopened(
    make_app: MakeApp, api: FakeApi, state_dir: Path
) -> None:
    api.reply(502, PARTIAL)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await submit(pilot)
    first = api.posts[0]

    api.reply(200, REPLAY_OK)
    reopened = make_app()
    async with reopened.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert "Press r to finish booking." in text(reopened, "#messages")
        await pilot.press("r")
        await settle(pilot)
        assert "Your focus day is booked." in text(reopened, "#messages")
    assert api.posts[1] == {
        **first,
        "replay": {"plan": PARTIAL["plan"], "created": PARTIAL["created"]},
    }
    assert not (state_dir / "last-focus-day.json").exists()


async def test_with_nothing_saved_retry_is_hidden_and_r_says_so(
    make_app: MakeApp, api: FakeApi
) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        bindings = {b.binding.action for b in app.active_bindings.values()}
        assert "retry" not in bindings
        await pilot.press("r")
        await pilot.pause()
        assert text(app, "#messages") == "Nothing to retry."
    assert api.posts == []


async def test_a_retry_that_fails_again_keeps_the_newer_run(
    make_app: MakeApp, api: FakeApi, state_dir: Path
) -> None:
    again = json.loads(json.dumps(PARTIAL))
    again["created"]["6afbc54ac1bb3fbab0aa7860"] = "fake-event-2"
    api.reply(502, PARTIAL)
    api.reply(502, again)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app, assignee="tyler")
        await submit(pilot)
        await pilot.press("r")
        await settle(pilot)
        assert "Press r to finish booking." in text(app, "#messages")
    saved = json.loads((state_dir / "last-focus-day.json").read_text())
    assert saved["created"] == again["created"]


async def test_esc_leaves_a_field_so_shortcuts_work(make_app: MakeApp) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        field(app, "assignee").focus()
        await pilot.press("r")
        assert field(app, "assignee").value == "r"  # typing goes to the field
        await pilot.press("escape", "r")
        assert text(app, "#messages") == "Nothing to retry."
        await pilot.press("f")
        assert app.focused is field(app, "assignee")
