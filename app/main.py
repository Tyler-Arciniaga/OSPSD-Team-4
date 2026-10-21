import hashlib
from collections.abc import Mapping
from threading import Lock
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.dependencies import get_issue_provider
from app.errors import (
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ServiceError,
    log_failure,
)
from app.interface import IssueProvider
from app.models import Issue, IssueCreate, IssueUpdate

app = FastAPI(title="Issue Tracking Service")

# Makes each PATCH's version check and write one step, so two callers cannot
# both pass the check. shortcut: one lock per process, serializing every PATCH;
# move to a shared store (e.g. Redis) before running several workers.
_update_lock = Lock()


def _etag(issue: Issue) -> str:
    """Return a version tag that changes whenever the issue's public fields change."""
    return f'"{hashlib.sha256(issue.model_dump_json().encode()).hexdigest()[:16]}"'


def _error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Document the given error statuses with the shared error shape."""
    return {status: {"model": ErrorResponse} for status in statuses}


def _operation(request: Request) -> str:
    """Name the endpoint handling the request, useed for failure logging."""
    endpoint = request.scope.get("endpoint")
    return getattr(endpoint, "__name__", "unknown")


def _is_write(request: Request) -> bool:
    return request.method not in ("GET", "HEAD")


def _error_response(
    status: int,
    code: ErrorCode,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


@app.exception_handler(ServiceError)
def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
    """Return an expected failure in the service's error shape."""
    log_failure(
        _operation(request),
        exc.code,
        exc.status,
        provider_involved=exc.provider_involved,
        write=_is_write(request),
        cause=exc.cause,
    )
    return _error_response(exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Report a rejected request body without FastAPI's internal fields."""
    details = []
    for error in exc.errors():
        # Drop the location prefix ("body"); an empty remainder means the
        # whole body was rejected.
        field = ".".join(str(part) for part in error["loc"][1:]) or "body"
        details.append(ErrorDetail(field=field, message=error["msg"]))
    log_failure(
        _operation(request),
        ErrorCode.INVALID_INPUT,
        422,
        provider_involved=False,
        write=_is_write(request),
    )
    return _error_response(
        422, ErrorCode.INVALID_INPUT, "Request body is invalid", details
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return routing failures (unknown path, wrong method) in the error shape."""
    if exc.status_code == 404:
        code, message = ErrorCode.NOT_FOUND, "Resource not found"
    else:
        code, message = ErrorCode.INVALID_INPUT, "Request not supported"
    log_failure(
        _operation(request),
        code,
        exc.status_code,
        provider_involved=False,
        write=_is_write(request),
    )
    # Keep routing headers such as a 405 response's Allow header.
    return _error_response(exc.status_code, code, message, headers=exc.headers)


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Hide an unexpected failure (i.e. a bug) from the caller but log its trace for debugging."""
    log_failure(
        _operation(request),
        ErrorCode.INTERNAL_ERROR,
        500,
        provider_involved=False,
        write=_is_write(request),
        cause=type(exc).__name__,
        exc_info=True,
    )
    return _error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")


@app.get(
    "/issues/{issue_id}",
    response_model=Issue,
    responses=_error_responses(403, 404, 502, 503),
)
def get_issue(
    issue_id: str,
    response: Response,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Issue:
    """Retrieve an issue by its ID using the supplied provider."""
    issue = provider.get_issue(issue_id)
    response.headers["ETag"] = _etag(issue)
    return issue


@app.patch(
    "/issues/{issue_id}",
    response_model=Issue,
    responses=_error_responses(403, 404, 412, 422, 502, 503),
)
def update_issue(
    issue_id: str,
    changes: IssueUpdate,
    response: Response,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
    if_match: Annotated[str | None, Header()] = None,
) -> Issue:
    """Partially update an issue, rejecting the change if If-Match is stale."""
    with _update_lock:
        if (
            if_match not in (None, "*")
            and _etag(provider.get_issue(issue_id)) != if_match
        ):
            raise ServiceError(
                ErrorCode.PRECONDITION_FAILED,
                "The issue has changed since it was read",
                cause="version_mismatch",
            )
        issue = provider.update_issue(issue_id, changes)
    response.headers["ETag"] = _etag(issue)
    return issue


@app.delete(
    "/issues/{issue_id}",
    status_code=204,
    response_class=Response,
    responses=_error_responses(403, 404, 502, 503),
)
def delete_issue(
    issue_id: str,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Response:
    """Permanently delete an issue by its ID using the supplied provider."""
    provider.delete_issue(issue_id)
    return Response(status_code=204)


@app.post(
    "/issues",
    response_model=Issue,
    status_code=201,
    responses=_error_responses(403, 422, 502, 503),
)
def create_issue(
    body: IssueCreate,
    response: Response,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Issue:
    """Create an issue using the supplied provider."""
    issue = provider.create_issue(body.title)
    response.headers["Location"] = f"/issues/{issue.id}"
    return issue
