"""The product's failure type: an HW1 error code plus the step that failed.

Codes and their HTTP statuses come from the HW1 error model
(`issue_tracker_contract.errors`), so callers branch on the same codes as for
the HW2 service. Adapters raise StepError; nothing else from a provider
crosses into the orchestrator.

`message` is safe to show callers. It is always written by this service,
never copied from a provider, because provider messages can contain paths,
IDs or operator hints. `cause` is a short internal reason for logs only.
"""

from typing import Literal

from issue_tracker_contract.errors import STATUS_FOR_CODE, ErrorCode, ErrorDetail

Step = Literal["validate", "list_issues", "plan", "create_events", "write_standup_doc"]
STEPS: tuple[Step, ...] = (
    "validate",
    "list_issues",
    "plan",
    "create_events",
    "write_standup_doc",
)


class StepError(Exception):
    """A step failed.

    `retryable`: repeating the same request later may succeed (a transient
    outage). `may_have_applied`: a write was sent but its outcome is unknown,
    so it may have taken effect.
    """

    def __init__(
        self,
        step: Step,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        may_have_applied: bool = False,
        details: list[ErrorDetail] | None = None,
        cause: str | None = None,
    ) -> None:
        super().__init__(message)
        self.step = step
        self.code = code
        self.message = message
        self.retryable = retryable
        self.may_have_applied = may_have_applied
        self.details = details
        self.cause = cause

    @property
    def status(self) -> int:
        return STATUS_FOR_CODE[self.code]
