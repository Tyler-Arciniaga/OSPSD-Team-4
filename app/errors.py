"""Failure logging. Deals exclusively with application policy, so it stays outside the contract."""

import json
import logging

from issue_tracker_contract.errors import OUTCOME_UNKNOWN_CODES, ErrorCode

logger = logging.getLogger("app.errors")


def log_failure(
    operation: str,
    code: ErrorCode,
    status: int,
    *,
    provider_involved: bool,
    write: bool,
    cause: str | None = None,
    exc_info: bool = False,
    may_have_applied: bool | None = None,
) -> None:
    """Write one structured JSON line describing a failed operation.

    Expected failures are logged without a stack trace. Pass `exc_info=True`
    only for unexpected exceptions, where the trace helps find the bug.
    For a write, the record says whether the change is known not to have been
    applied, or whether that is unknown (the request may have taken effect).
    Pass a ServiceError's `may_have_applied` so the log matches what the
    service did; otherwise the code decides.
    """
    write_outcome = None
    if write:
        if may_have_applied is None:
            may_have_applied = code in OUTCOME_UNKNOWN_CODES
        write_outcome = "unknown" if may_have_applied else "not_applied"
    record = {
        "level": "ERROR" if status >= 500 else "WARNING",
        "operation": operation,
        "error": code.value,
        "status": status,
        "provider_involved": provider_involved,
        "cause": cause,
        "write_outcome": write_outcome,
    }
    level = logging.ERROR if status >= 500 else logging.WARNING
    logger.log(level, json.dumps(record), exc_info=exc_info)
