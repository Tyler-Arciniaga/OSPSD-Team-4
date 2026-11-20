"""Standup doc path and content."""

from datetime import date

import pytest

from ospsd_team4_product.doc import doc_path, render_doc
from ospsd_team4_product.fakes.storage import check_path
from ospsd_team4_product.planner import Block, Plan, PlanInputError, Unscheduled

DAY = date(2026, 11, 18)
PLAN = Plan(
    blocks=(
        Block(
            "abc", "Fix login", "2026-11-18T09:00:00-05:00", "2026-11-18T10:30:00-05:00"
        ),
        Block(
            "def",
            "Two\nlines",
            "2026-11-18T10:30:00-05:00",
            "2026-11-18T12:00:00-05:00",
        ),
    ),
    unscheduled=(Unscheduled("xyz", "no_free_slot"),),
)


def test_doc_path() -> None:
    assert doc_path(DAY, "tyler") == "/standups/2026-11-18/tyler.md"
    assert doc_path(DAY, "a.b_c-1") == "/standups/2026-11-18/a.b_c-1.md"


@pytest.mark.parametrize(
    "assignee",
    ["", ".", "..", "a/b", "a b", "../x", "tylér", "x" * 65, "a\n", "Tyler", "TYLER"],
)
def test_bad_assignee_is_rejected(assignee: str) -> None:
    with pytest.raises(PlanInputError) as error:
        doc_path(DAY, assignee)

    assert error.value.field == "assignee"


@pytest.mark.parametrize("assignee", ["tyler", "...", "a.b", "-", "_", "x" * 64])
def test_every_accepted_assignee_gives_a_valid_storage_path(assignee: str) -> None:
    check_path(doc_path(DAY, assignee))


def test_render_doc() -> None:
    content = render_doc(PLAN, day=DAY, assignee="tyler", timezone="America/New_York")

    assert content.decode("utf-8") == (
        "# Standup: tyler, 2026-11-18\n"
        "\n"
        "Times are in America/New_York.\n"
        "\n"
        "## Focus blocks\n"
        "\n"
        "- 09:00-10:30 `abc` Fix login\n"
        "- 10:30-12:00 `def` Two lines\n"
        "\n"
        "## Unscheduled\n"
        "\n"
        "- `xyz`: no free slot\n"
    )


def test_render_empty_plan() -> None:
    content = render_doc(
        Plan(blocks=(), unscheduled=()), day=DAY, assignee="tyler", timezone="UTC"
    ).decode("utf-8")

    assert "None scheduled." in content
    assert "None." in content


def test_same_plan_gives_same_bytes() -> None:
    def render() -> bytes:
        return render_doc(PLAN, day=DAY, assignee="tyler", timezone="UTC")

    assert render() == render()
