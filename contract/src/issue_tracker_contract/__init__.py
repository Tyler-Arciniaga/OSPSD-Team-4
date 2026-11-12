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
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    MAX_TITLE_LENGTH,
    Issue,
    IssueCreate,
    IssueList,
    IssueTitle,
    IssueUpdate,
)

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "MAX_TITLE_LENGTH",
    "OUTCOME_UNKNOWN_CODES",
    "STATUS_FOR_CODE",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "ErrorResponse",
    "Issue",
    "IssueCreate",
    "IssueList",
    "IssueProvider",
    "IssueTitle",
    "IssueUpdate",
    "ServiceError",
]
