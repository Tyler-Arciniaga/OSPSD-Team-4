import json
import logging
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel

logger = logging.getLogger("app.errors")


class ErrorCode(StrEnum):
    """The service's public error codes. Each one is part of the API contract."""

    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    SERVICE_NOT_CONFIGURED = "service_not_configured"
    PRECONDITION_FAILED = "precondition_failed"
    PROVIDER_ERROR = "provider_error"
    INTERNAL_ERROR = "internal_error"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    REQUEST_IN_PROGRESS = "request_in_progress"


# Codes for which a failed write may still have taken effect, unless the
# failure says otherwise (see ServiceError.may_have_applied).
_OUTCOME_UNKNOWN_CODES = frozenset({ErrorCode.PROVIDER_ERROR, ErrorCode.INTERNAL_ERROR})


STATUS_FOR_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.SERVICE_NOT_CONFIGURED: 503,
    ErrorCode.PRECONDITION_FAILED: 412,
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 422,
    ErrorCode.REQUEST_IN_PROGRESS: 409,
}


class ServiceError(Exception):
    """An expected failure, described in the service's terms rather than coupled with
    any specific provider.

    Providers translate their own failures. `message` is shown to the caller;
    `cause` is a short internal reason that is only logged and must never contain credentials,
    URLs, or provider response bodies.

    `may_have_applied` says whether a failed *write* might still have taken
    effect in the provider (for example, the request was sent but the
    response was lost). It drives both the logged `write_outcome` and whether
    idempotent creation can release a key for a fresh attempt. A provider
    sets it to False only when it knows nothing was written. When it is not
    given, `provider_error` and `internal_error` count as possibly applied
    and every other code as not applied.

    `headers` are added to the error response (for example, `Retry-After`).
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        provider_involved: bool = False,
        cause: str | None = None,
        may_have_applied: bool | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider_involved = provider_involved
        self.cause = cause
        if may_have_applied is None:
            may_have_applied = code in _OUTCOME_UNKNOWN_CODES
        self.may_have_applied = may_have_applied
        self.headers = dict(headers) if headers else None

    @property
    def status(self) -> int:
        return STATUS_FOR_CODE[self.code]


class ErrorDetail(BaseModel):
    """A single problem with a rejected request body."""

    field: str
    message: str


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    """The shape of every error response: {"error": {"code", "message"}}."""

    error: ErrorBody


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
            may_have_applied = code in _OUTCOME_UNKNOWN_CODES
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
