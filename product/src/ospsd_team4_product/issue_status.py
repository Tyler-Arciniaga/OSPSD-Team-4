"""The issue-status operation: answer one question from approved plans.

Steps, in order:

1. `find_issue`: the issue in the configured tracker collection, by ID or by
   a case-insensitive part of its title.
2. `read_plan`: its owner, block, remaining share and completion log entry
   from the state store (store.py).

It writes nothing, so it has no approval step, and it reports only saved
plans, so a draft is never shown. All input is validated before the tracker
is called.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from issue_tracker_contract.errors import ErrorCode, ErrorDetail

from ospsd_team4_product.adapters.tracker import TrackerAdapter
from ospsd_team4_product.config import ProductConfig
from ospsd_team4_product.errors import StepError
from ospsd_team4_product.models import (
    IssueModel,
    IssueStatusAmbiguous,
    IssueStatusAnswered,
    IssueStatusNoPlan,
    IssueStatusQuery,
    IssueStatusResponse,
    ScheduledModel,
)
from ospsd_team4_product.orchestrator import INVALID_REQUEST
from ospsd_team4_product.planner import PlanIssue
from ospsd_team4_product.store import StateStore, StoreError

NO_MATCH = "No issue matches"
READ_FAILED = "The saved plans could not be read. Try again later."


def invalid_query(details: list[ErrorDetail]) -> StepError:
    """The failure for invalid query parameters (`find_issue` step)."""
    return StepError(
        "find_issue",
        ErrorCode.INVALID_INPUT,
        INVALID_REQUEST,
        details=details,
        cause="invalid_input",
    )


def _issue(issue: PlanIssue) -> IssueModel:
    return IssueModel(issue_id=issue.id, title=issue.title)


class IssueStatus:
    def __init__(
        self, *, tracker: TrackerAdapter, store: StateStore, config: ProductConfig
    ) -> None:
        self._tracker = tracker
        self._store = store
        self._timezone = ZoneInfo(config.default_timezone)

    def run(self, query: IssueStatusQuery, *, now: datetime) -> IssueStatusResponse:
        """Answer the query; raises StepError if a step fails."""
        if (query.issue_id is None) == (query.title is None):
            raise invalid_query(
                [
                    ErrorDetail(
                        field=name,
                        message="exactly one of issue_id and title is required",
                    )
                    for name in ("issue_id", "title")
                ]
            )
        found = self._find_issues(query)
        if len(found) > 1:
            found.sort(key=lambda issue: (issue.title.casefold(), issue.id))
            return IssueStatusAmbiguous(
                status="ambiguous", matches=[_issue(issue) for issue in found]
            )
        day = query.parsed_day() or now.astimezone(self._timezone).date()
        try:
            return self._read_plan(found[0], day, now)
        except StoreError as exc:
            raise StepError(
                "read_plan",
                ErrorCode.INTERNAL_ERROR,
                READ_FAILED,
                retryable=True,
                cause=exc.cause or "store_error",
            ) from None

    def _find_issues(self, query: IssueStatusQuery) -> list[PlanIssue]:
        """The matching issues, at least one.

        An ID is looked up in the list too: the provider's get_issue also
        returns archived issues and those of other lists (another team's).
        """
        issues = self._tracker.list_issues(step="find_issue").issues
        if query.title is not None:
            part = query.title.casefold()
            found = [issue for issue in issues if part in issue.title.casefold()]
        else:
            found = [issue for issue in issues if issue.id == query.issue_id]
        if not found:
            raise StepError(
                "find_issue", ErrorCode.NOT_FOUND, NO_MATCH, cause="no_match"
            )
        return found

    def _read_plan(
        self, issue: PlanIssue, day: date, now: datetime
    ) -> IssueStatusResponse:
        plans = self._store.find_plans_with_issue(issue.id)
        if not plans:
            return IssueStatusNoPlan(status="no_approved_plan", issue=_issue(issue))
        newest = plans[0]
        owner = newest.assignee
        scheduled = next(
            (p.block for p in plans if p.assignee == owner and p.day == day), None
        )
        reported = any(
            entry.issue_id == issue.id
            for entry in self._store.get_log(owner, newest.day)
        )
        return IssueStatusAnswered(
            status="answered",
            issue=_issue(issue),
            owner=owner,
            day=day,
            scheduled=(
                ScheduledModel(start=scheduled.start, end=scheduled.end)
                if scheduled
                else None
            ),
            remaining_percent=self._store.get_remaining(owner).get(issue.id),
            last_planned_day=newest.day,
            passed_without_report=(
                not reported and datetime.fromisoformat(newest.block.end) < now
            ),
        )
