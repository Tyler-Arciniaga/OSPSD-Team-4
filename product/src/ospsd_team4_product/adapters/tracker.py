"""Issue tracker adapter for the `list_issues` step.

The error mapping is documented in product/docs/ERRORS.md ("list_issues
(tracker)").
"""

import logging
from typing import Final

from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import MAX_LIST_LIMIT

from ospsd_team4_product.errors import Step, StepError
from ospsd_team4_product.planner import PlanIssue

logger = logging.getLogger(__name__)

STEP: Final[Step] = "list_issues"


class TrackerAdapter:
    def __init__(self, provider: IssueProvider) -> None:
        self._provider = provider

    def list_issues(self) -> list[PlanIssue]:
        """Every issue in the configured collection, up to the contract's limit.

        The contract has no assignee field or filter, so this is every issue
        in the configured list (product DESIGN.md 4.2).
        """
        try:
            issues = self._provider.list_issues(limit=MAX_LIST_LIMIT)
        except ServiceError as exc:
            # Already in the HW1 model with a caller-safe message. A failed
            # read changes nothing; provider_error is worth retrying, as the
            # HW1 API documents for reads.
            raise StepError(
                STEP,
                exc.code,
                exc.message,
                retryable=exc.code == ErrorCode.PROVIDER_ERROR,
                cause=exc.cause,
            ) from None
        except Exception as exc:  # contract breach: never leak it
            logger.exception("tracker raised an unexpected exception")
            raise StepError(
                STEP,
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker returned an unusable response",
                cause=type(exc).__name__,
            ) from None
        return [PlanIssue(id=issue.id, title=issue.title) for issue in issues]
