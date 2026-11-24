"""Pure pieces: form conversion, state files, time zone, timeline."""

import json
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

from focus_day_tui import timeline
from focus_day_tui.client import FocusDayResult, parse_focus_day
from focus_day_tui.form import BusyRow, FormValues, build_request, to_iso
from focus_day_tui.state import LastRun, StateStore, default_state_dir
from focus_day_tui.zones import system_timezone
from tests.bodies import OK, PARTIAL


def values(**overrides: object) -> FormValues:
    base: dict[str, object] = {
        "assignee": "tyler",
        "day": "2026-03-08",
        "window_start": "",
        "window_end": "",
        "block_minutes": "",
        "timezone": "America/New_York",
        "busy": (),
    }
    base.update(overrides)
    return FormValues(**base)  # type: ignore[arg-type]


def test_busy_times_use_the_offset_of_that_day() -> None:
    """2026-03-08 is the US daylight saving switch: 01:00 is -05:00, 03:00 -04:00."""
    body, errors = build_request(
        values(busy=(BusyRow("01:00", "01:30"), BusyRow("03:00", "03:30")))
    )
    assert errors == {}
    assert body is not None
    assert body["busy"] == [
        {"start": "2026-03-08T01:00:00-05:00", "end": "2026-03-08T01:30:00-05:00"},
        {"start": "2026-03-08T03:00:00-04:00", "end": "2026-03-08T03:30:00-04:00"},
    ]


def test_iso_times_have_whole_seconds_and_an_offset() -> None:
    stamp = to_iso(date(2026, 11, 19), time(9, 5), ZoneInfo("Asia/Kolkata"))
    assert stamp == "2026-11-19T09:05:00+05:30"


def test_minutes_are_sent_as_a_number_and_the_range_is_left_to_the_api() -> None:
    body, errors = build_request(values(block_minutes="5000"))
    assert errors == {}
    assert body is not None and body["default_minutes"] == 5000


def test_state_dir_comes_from_the_environment(tmp_path: Path) -> None:
    assert default_state_dir({"FOCUS_DAY_STATE_DIR": str(tmp_path)}) == tmp_path
    assert default_state_dir({}) == Path.home() / ".focus-day"


def test_unreadable_state_files_are_treated_as_empty(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    (tmp_path / "preferences.json").write_text("{not json")
    (tmp_path / "last-focus-day.json").write_text(json.dumps({"plan": []}))
    assert store.load_preferences().assignee == ""
    assert store.load_last_run() is None


def test_the_last_run_round_trips(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "new")
    run = LastRun({"assignee": "tyler"}, PARTIAL["plan"], PARTIAL["created"])
    store.save_last_run(run)
    assert store.load_last_run() == run
    store.clear_last_run()
    store.clear_last_run()  # deleting twice is fine
    assert store.load_last_run() is None


def test_system_timezone(tmp_path: Path) -> None:
    link = tmp_path / "localtime"
    link.symlink_to("/var/db/timezone/zoneinfo/Europe/Paris")
    missing = tmp_path / "none"
    assert system_timezone({"TZ": ":Asia/Tokyo"}, link, missing) == "Asia/Tokyo"
    assert system_timezone({}, link, missing) == "Europe/Paris"
    assert system_timezone({}, missing, missing) == "UTC"


def test_a_failure_at_the_doc_step_shows_every_block_booked() -> None:
    body = json.loads(json.dumps(PARTIAL))
    body["created"] = OK["created"]
    body["failed_step"] = "write_standup_doc"
    result = parse_focus_day(502, body)
    assert isinstance(result, FocusDayResult)
    items = timeline.block_items(
        result, date(2026, 11, 19), ZoneInfo("America/New_York")
    )
    assert [item.kind for item in items] == ["booked", "booked", "booked"]


def test_titles_cannot_inject_markup() -> None:
    item = timeline.Item("booked", 9 * 60, 10 * 60, "[red]x[/red]")
    rendered = timeline.render(date(2026, 11, 19), "UTC", None, [item])
    assert "\\[red]x" in rendered
