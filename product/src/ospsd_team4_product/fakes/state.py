"""In-memory StateStore for tests and the disposable environment.

Select it with `STATE_DB_URL=memory://`. State lives only in this process,
so it is lost on restart and NOT shared between instances: never use it
where more than one instance runs. It follows the same rules as
PostgresStateStore (tests/test_store.py runs the same tests on both).
"""

import threading
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from ospsd_team4_product.fakes._failures import FailureSchedule
from ospsd_team4_product.store import (
    CalendarBlock,
    PlanBlock,
    ProgressEntry,
    check_blocks,
    check_remaining,
)


class InMemoryStateStore:
    def __init__(self, *, always_fail: Exception | None = None) -> None:
        self.failures = FailureSchedule(always_fail)
        self._lock = threading.Lock()
        self._plans: dict[tuple[str, date], tuple[CalendarBlock, ...]] = {}
        self._remaining: dict[tuple[str, str], int] = {}
        self._log: dict[tuple[str, date, str], ProgressEntry] = {}

    def save_plan(
        self, assignee: str, day: date, blocks: Sequence[CalendarBlock]
    ) -> None:
        self.failures.check("save_plan")
        check_blocks(blocks)
        with self._lock:
            self._plans[(assignee, day)] = tuple(blocks)

    def get_plan(self, assignee: str, day: date) -> tuple[CalendarBlock, ...] | None:
        self.failures.check("get_plan")
        with self._lock:
            return self._plans.get((assignee, day))

    def find_plans_with_issue(self, issue_id: str) -> list[PlanBlock]:
        self.failures.check("find_plans_with_issue")
        with self._lock:
            matches = [
                PlanBlock(assignee=assignee, day=day, block=block)
                for (assignee, day), blocks in self._plans.items()
                for block in blocks
                if block.issue_id == issue_id
            ]
        matches.sort(key=lambda match: match.assignee)
        matches.sort(key=lambda match: match.day, reverse=True)
        return matches

    def set_remaining(self, assignee: str, issue_id: str, percent: int) -> None:
        self.failures.check("set_remaining")
        check_remaining(percent)
        with self._lock:
            self._remaining[(assignee, issue_id)] = percent

    def clear_remaining(self, assignee: str, issue_id: str) -> None:
        self.failures.check("clear_remaining")
        with self._lock:
            self._remaining.pop((assignee, issue_id), None)

    def get_remaining(self, assignee: str) -> dict[str, int]:
        self.failures.check("get_remaining")
        with self._lock:
            return {
                issue_id: percent
                for (owner, issue_id), percent in sorted(self._remaining.items())
                if owner == assignee
            }

    def log_progress(self, entry: ProgressEntry) -> None:
        self.failures.check("log_progress")
        with self._lock:
            self._log[(entry.assignee, entry.day, entry.issue_id)] = entry

    def mark_applied(self, assignee: str, day: date, issue_id: str) -> bool:
        self.failures.check("mark_applied")
        key = (assignee, day, issue_id)
        with self._lock:
            entry = self._log.get(key)
            if entry is None:
                return False
            self._log[key] = replace(entry, applied=True)
            return True

    def get_log(self, assignee: str, day: date) -> list[ProgressEntry]:
        self.failures.check("get_log")
        with self._lock:
            return [
                entry
                for (owner, entry_day, _), entry in sorted(
                    self._log.items(), key=lambda item: item[0][2]
                )
                if owner == assignee and entry_day == day
            ]

    def close(self) -> None:
        """Nothing to release."""
