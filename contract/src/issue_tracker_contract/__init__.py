"""Shared issue tracker contract: models, provider interface, and errors.

This package must not import from any service or provider adapter code.
"""

from issue_tracker_contract.errors import (
    OUTCOME_UNKNOWN_CODES,
    STATUS_FOR_CODE,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ServiceError,
)
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import (
    MAX_TITLE_LENGTH,
    Issue,
    IssueCreate,
    IssueTitle,
    IssueUpdate,
)

__all__ = [
    "MAX_TITLE_LENGTH",
    "OUTCOME_UNKNOWN_CODES",
    "STATUS_FOR_CODE",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "ErrorResponse",
    "Issue",
    "IssueCreate",
    "IssueProvider",
    "IssueTitle",
    "IssueUpdate",
    "ServiceError",
]
