"""The pure focus-block planner."""

from datetime import date, time

import pytest

from ospsd_team4_product.planner import (
    Block,
    BusyInterval,
    Plan,
    PlanInputError,
    PlanIssue,
    Unscheduled,
    plan_day,
)

DAY = date(2026, 11, 18)
NY = "America/New_York"  # UTC-05:00 on DAY


def issues(*ids: str) -> list[PlanIssue]:
    return [PlanIssue(id=issue_id, title=f"Issue {issue_id}") for issue_id in ids]


def at(clock: str) -> str:
    """A New York timestamp on DAY."""
    return f"2026-11-18T{clock}:00-05:00"


def busy(*pairs: tuple[str, str]) -> list[BusyInterval]:
    return [BusyInterval(start=start, end=end) for start, end in pairs]


def plan(
    plan_issues: list[PlanIssue],
    *,
    busy_intervals: list[BusyInterval] | None = None,
    minutes: int = 90,
    window: tuple[time, time] = (time(9), time(17)),
    timezone: str = NY,
    day: date = DAY,
) -> Plan:
    return plan_day(
        plan_issues,
        day=day,
        timezone=timezone,
        window_start=window[0],
        window_end=window[1],
        busy=busy_intervals or [],
        minutes=minutes,
    )


def spans(result: Plan) -> list[tuple[str, str, str]]:
    return [(block.issue_id, block.start, block.end) for block in result.blocks]


def test_no_busy_three_issues_fit_in_sequence() -> None:
    result = plan(issues("a", "b", "c"))

    assert spans(result) == [
        ("a", at("09:00"), at("10:30")),
        ("b", at("10:30"), at("12:00")),
        ("c", at("12:00"), at("13:30")),
    ]
    assert result.unscheduled == ()
    assert result.blocks[0] == Block(
        issue_id="a", title="Issue a", start=at("09:00"), end=at("10:30")
    )


def test_busy_in_the_middle_blocks_go_around_it() -> None:
    result = plan(
        issues("a", "b", "c"),
        busy_intervals=busy((at("10:00"), at("11:00"))),
        minutes=60,
    )

    assert spans(result) == [
        ("a", at("09:00"), at("10:00")),
        ("b", at("11:00"), at("12:00")),
        ("c", at("12:00"), at("13:00")),
    ]


def test_gap_too_short_is_skipped_not_split() -> None:
    result = plan(issues("a"), busy_intervals=busy((at("10:00"), at("11:00"))))

    assert spans(result) == [("a", at("11:00"), at("12:30"))]


def test_touching_busy_intervals_are_merged() -> None:
    # 10:00-10:30 and 10:30-11:00 touch; no zero-length gap appears between.
    result = plan(
        issues("a", "b"),
        busy_intervals=busy((at("10:30"), at("11:00")), (at("10:00"), at("10:30"))),
        minutes=60,
        window=(time(9), time(12)),
    )

    assert spans(result) == [
        ("a", at("09:00"), at("10:00")),
        ("b", at("11:00"), at("12:00")),
    ]


def test_overlapping_busy_intervals_are_merged() -> None:
    result = plan(
        issues("a", "b"),
        busy_intervals=busy((at("10:00"), at("12:00")), (at("11:00"), at("13:00"))),
        minutes=60,
    )

    assert spans(result) == [
        ("a", at("09:00"), at("10:00")),
        ("b", at("13:00"), at("14:00")),
    ]


def test_contained_busy_interval_does_not_shrink_the_merge() -> None:
    result = plan(
        issues("a"),
        busy_intervals=busy((at("09:00"), at("13:00")), (at("10:00"), at("11:00"))),
        minutes=60,
    )

    assert spans(result) == [("a", at("13:00"), at("14:00"))]


def test_busy_partly_outside_the_window_is_clipped() -> None:
    result = plan(
        issues("a", "b"),
        busy_intervals=busy(
            ("2026-11-18T07:00:00-05:00", at("10:00")),
            (at("16:00"), "2026-11-18T20:00:00-05:00"),
            # Entirely outside the window: ignored.
            ("2026-11-17T09:00:00-05:00", "2026-11-17T17:00:00-05:00"),
        ),
        minutes=180,
    )

    assert spans(result) == [
        ("a", at("10:00"), at("13:00")),
        ("b", at("13:00"), at("16:00")),
    ]


def test_busy_in_another_offset_is_compared_by_instant() -> None:
    # 15:00Z is 10:00 in New York.
    result = plan(
        issues("a"),
        busy_intervals=busy(("2026-11-18T14:00:00Z", "2026-11-18T15:00:00Z")),
        minutes=60,
    )

    assert spans(result) == [("a", at("10:00"), at("11:00"))]


def test_fully_booked_day_leaves_everything_unscheduled() -> None:
    result = plan(
        issues("a", "b"),
        busy_intervals=busy((at("08:00"), at("18:00"))),
    )

    assert result.blocks == ()
    assert result.unscheduled == (
        Unscheduled(issue_id="a", reason="no_free_slot"),
        Unscheduled(issue_id="b", reason="no_free_slot"),
    )


def test_issue_longer_than_every_remaining_gap_is_unscheduled() -> None:
    # Gaps: 09:00-10:00 and 11:00-12:30. Two 90-minute blocks need 3 hours.
    result = plan(
        issues("a", "b"),
        busy_intervals=busy((at("10:00"), at("11:00"))),
        window=(time(9), time(12, 30)),
    )

    assert spans(result) == [("a", at("11:00"), at("12:30"))]
    assert result.unscheduled == (Unscheduled(issue_id="b", reason="no_free_slot"),)


def test_block_longer_than_the_window_leaves_all_unscheduled() -> None:
    result = plan(issues("a"), minutes=9 * 60)

    assert result.blocks == ()
    assert result.unscheduled == (Unscheduled(issue_id="a", reason="no_free_slot"),)


def test_block_exactly_filling_a_gap_fits() -> None:
    result = plan(issues("a"), minutes=8 * 60)

    assert spans(result) == [("a", at("09:00"), at("17:00"))]


def test_zero_issues() -> None:
    assert plan([]) == Plan(blocks=(), unscheduled=())


def test_same_input_twice_gives_identical_output() -> None:
    def run() -> Plan:
        return plan(
            issues("c", "a", "b"),
            busy_intervals=busy((at("10:00"), at("11:00"))),
            minutes=60,
        )

    assert run() == run()


def test_issue_input_order_does_not_matter() -> None:
    forward = plan(issues("a", "b", "c", "d"), minutes=150)
    shuffled = plan(issues("d", "b", "a", "c"), minutes=150)

    assert forward == shuffled
    assert [block.issue_id for block in forward.blocks] == ["a", "b", "c"]
    assert forward.unscheduled == (Unscheduled(issue_id="d", reason="no_free_slot"),)


def test_busy_input_order_does_not_matter() -> None:
    pairs = [(at("10:00"), at("11:00")), (at("13:00"), at("14:00"))]

    assert plan(issues("a", "b"), busy_intervals=busy(*pairs)) == plan(
        issues("a", "b"), busy_intervals=busy(*reversed(pairs))
    )


def test_repeated_issue_id_is_planned_once() -> None:
    result = plan([PlanIssue(id="a", title="First"), PlanIssue(id="a", title="Second")])

    assert [(block.issue_id, block.title) for block in result.blocks] == [
        ("a", "First")
    ]


def test_output_is_whole_seconds_with_offset_in_the_requested_zone() -> None:
    result = plan(issues("a"), timezone="Asia/Kolkata", minutes=45)

    assert spans(result) == [
        ("a", "2026-11-18T09:00:00+05:30", "2026-11-18T09:45:00+05:30")
    ]


def test_utc_zone_uses_explicit_offset() -> None:
    result = plan(issues("a"), timezone="UTC", minutes=30)

    assert spans(result) == [
        ("a", "2026-11-18T09:00:00+00:00", "2026-11-18T09:30:00+00:00")
    ]


def test_daylight_saving_change_keeps_block_length() -> None:
    # On 2026-03-08 New York clocks jump from 02:00 to 03:00.
    result = plan(
        issues("a"),
        day=date(2026, 3, 8),
        window=(time(1), time(5)),
        minutes=120,
    )

    assert spans(result) == [
        ("a", "2026-03-08T01:00:00-05:00", "2026-03-08T04:00:00-04:00")
    ]


@pytest.mark.parametrize(
    ("busy_intervals", "field"),
    [
        (busy((at("11:00"), at("10:00"))), "busy[0]"),
        (busy((at("10:00"), at("10:00"))), "busy[0]"),
        (
            busy((at("09:00"), at("09:30")), ("2026-11-18T10:00:00", at("11:00"))),
            "busy[1].start",
        ),
        (busy((at("10:00"), "2026-11-18T11:00:00.500-05:00")), "busy[0].end"),
        (busy(("2026-11-18 10:00:00-05:00", at("11:00"))), "busy[0].start"),
        (busy(("2026-02-30T10:00:00-05:00", at("11:00"))), "busy[0].start"),
        (busy((at("10:00"), "2026-11-18T11:00:00+25:00")), "busy[0].end"),
        (busy(("tomorrow", at("11:00"))), "busy[0].start"),
    ],
    ids=[
        "inverted",
        "empty",
        "naive",
        "fractional-seconds",
        "space-separator",
        "impossible-date",
        "bad-offset",
        "not-a-timestamp",
    ],
)
def test_invalid_busy_interval_is_rejected(
    busy_intervals: list[BusyInterval], field: str
) -> None:
    with pytest.raises(PlanInputError) as error:
        plan(issues("a"), busy_intervals=busy_intervals)

    assert error.value.field == field


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "", "../etc/passwd"])
def test_bad_timezone_is_rejected(timezone: str) -> None:
    with pytest.raises(PlanInputError) as error:
        plan(issues("a"), timezone=timezone)

    assert error.value.field == "timezone"


@pytest.mark.parametrize(
    "window", [(time(17), time(9)), (time(9), time(9))], ids=["inverted", "empty"]
)
def test_window_start_not_before_end_is_rejected(window: tuple[time, time]) -> None:
    with pytest.raises(PlanInputError) as error:
        plan(issues("a"), window=window)

    assert error.value.field == "working_window"


@pytest.mark.parametrize("minutes", [0, -30])
def test_non_positive_minutes_is_rejected(minutes: int) -> None:
    with pytest.raises(PlanInputError) as error:
        plan(issues("a"), minutes=minutes)

    assert error.value.field == "default_minutes"


def test_invalid_input_is_rejected_even_with_no_issues() -> None:
    with pytest.raises(PlanInputError):
        plan([], busy_intervals=busy((at("11:00"), at("10:00"))))
