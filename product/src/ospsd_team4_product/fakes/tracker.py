"""In-memory IssueProvider for tests and the disposable environment.

Select it with `TRACKER_PROVIDER=ospsd_team4_product.fakes:InMemoryIssueProvider`.
Built with no arguments it holds three demo issues, so the disposable
environment has something to plan.
"""

from collections.abc import Iterable
from itertools import count

from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import Issue, IssueUpdate

from ospsd_team4_product.fakes._failures import FailureSchedule

DEMO_ISSUES = (
    Issue(id="demo-1", title="Draft the design doc"),
    Issue(id="demo-2", title="Review the partner integration"),
    Issue(id="demo-3", title="Write the standup summary"),
)


class InMemoryIssueProvider:
    def __init__(
        self,
        issues: Iterable[Issue] = DEMO_ISSUES,
        *,
        always_fail: Exception | None = None,
    ) -> None:
        # Newest first, as list_issues returns them.
        self.issues: list[Issue] = list(issues)
        self.failures = FailureSchedule(always_fail)
        self._ids = count(1)

    def _index(self, issue_id: str) -> int:
        for index, issue in enumerate(self.issues):
            if issue.id == issue_id:
                return index
        raise ServiceError(ErrorCode.NOT_FOUND, "Issue not found")

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        self.failures.check("get_issue")
        return self.issues[self._index(issue_id)]

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        self.failures.check("update_issue")
        index = self._index(issue_id)
        updated = self.issues[index].model_copy(
            update=changes.model_dump(exclude_unset=True)
        )
        self.issues[index] = updated
        return updated

    def delete_issue(self, issue_id: str) -> None:
        self.failures.check("delete_issue")
        del self.issues[self._index(issue_id)]

    def create_issue(self, title: str) -> Issue:
        self.failures.check("create_issue")
        issue = Issue(id=f"fake-issue-{next(self._ids)}", title=title)
        self.issues.insert(0, issue)
        return issue

    def list_issues(self, *, limit: int) -> list[Issue]:
        self.failures.check("list_issues")
        return self.issues[:limit]
