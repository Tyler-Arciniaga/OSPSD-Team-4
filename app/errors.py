import json
import logging
from enum import StrEnum

from pydantic import BaseModel

logger = logging.getLogger("app.errors")


class ErrorCode(StrEnum):
    """The service's public error codes. Each one is part of the API contract."""

    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    SERVICE_NOT_CONFIGURED = "service_not_configured"
    PROVIDER_ERROR = "provider_error"
    INTERNAL_ERROR = "internal_error"


STATUS_FOR_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.SERVICE_NOT_CONFIGURED: 503,
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ServiceError(Exception):
    """An expected failure, described in the service's terms rather than coupled with
    any specific provider.

    Providers translate their own failures. `message` is shown to the caller;
    `cause` is a short internal reason that is only logged and must never contain credentials,
    URLs, or provider response bodies.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        provider_involved: bool = False,
        cause: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider_involved = provider_involved
        self.cause = cause

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
    cause: str | None = None,
) -> None:
    """Write one structured JSON line describing a failed operation."""
    record = {
        "operation": operation,
        "error": code.value,
        "status": status,
        "provider_involved": provider_involved,
        "cause": cause,
    }
    level = logging.ERROR if status >= 500 else logging.WARNING
    logger.log(level, json.dumps(record))
