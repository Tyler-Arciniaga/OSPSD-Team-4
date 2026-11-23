"""The product service's HTTP app.

`/healthz` and `/readyz` follow the HW2 service (docs/api.md#health-checks):
liveness checks nothing, readiness reports that startup configuration was
validated and which commit is running.

`POST /v1/focus-day` runs the operation (orchestrator.py). Its error bodies,
including those for a malformed request, are described in
product/docs/ERRORS.md.
"""

import json
import logging
import os
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from issue_tracker_contract.errors import (
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
)
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from ospsd_team4_product.adapters.calendar import CalendarAdapter
from ospsd_team4_product.adapters.storage import StorageAdapter
from ospsd_team4_product.adapters.tracker import TrackerAdapter
from ospsd_team4_product.config import ConfigError, load_config
from ospsd_team4_product.factories import build_providers, build_store
from ospsd_team4_product.models import (
    FocusDayErrorResponse,
    FocusDayRequest,
    FocusDayResponse,
)
from ospsd_team4_product.orchestrator import FocusDay, Outcome, rejected_request

logger = logging.getLogger("ospsd_team4_product")

# A Git commit ID, full or abbreviated. Anything else is reported as unknown,
# so a misconfigured variable can never echo arbitrary text.
COMMIT_PATTERN = r"[0-9a-f]{7,40}"


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    checks: dict[str, Literal["ok"]]
    commit: str


def deployed_commit(environ: Mapping[str, str] = os.environ) -> str:
    """Return the commit this process was built from, or "unknown".

    RENDER_GIT_COMMIT takes precedence over GIT_SHA (the image's build
    argument), as in the HW2 service.
    """
    for name in ("RENDER_GIT_COMMIT", "GIT_SHA"):
        value = environ.get(name, "")
        if re.fullmatch(COMMIT_PATTERN, value):
            return value
    return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration, build the providers and connect to the state
    store before serving. The store is closed on shutdown.

    A failure stops startup, naming the variable concerned.
    """
    try:
        config = load_config()
        providers = build_providers(config)
        store = build_store(config)
    except ConfigError as exc:
        logger.critical(
            json.dumps({"event": "startup_config_invalid", "message": str(exc)})
        )
        raise
    app.state.providers = providers
    app.state.store = store
    app.state.focus_day = FocusDay(
        tracker=TrackerAdapter(providers.tracker),
        calendar=CalendarAdapter(providers.calendar),
        storage=StorageAdapter(providers.storage),
        store=store,
        config=config,
    )
    try:
        yield
    finally:
        store.close()


app = FastAPI(title="OSPSD Team 4 Product Service", lifespan=lifespan)


@app.get("/healthz", response_model=LivenessResponse)
def healthz() -> LivenessResponse:
    """Liveness: the process is up and serving HTTP. Checks nothing else."""
    return LivenessResponse(status="ok")


@app.get("/readyz", response_model=ReadinessResponse)
def readyz() -> ReadinessResponse:
    """Readiness: startup only completes once configuration is validated.

    Providers are built but not contacted, so a partner outage never makes
    the platform restart the service.
    """
    return ReadinessResponse(
        status="ready", checks={"config": "ok"}, commit=deployed_commit()
    )


def _log_failure(outcome: Outcome) -> None:
    """One JSON line per failed request: codes and flags, never messages."""
    body = outcome.body
    if not isinstance(body, FocusDayErrorResponse):
        return
    logger.warning(
        json.dumps(
            {
                "event": "focus_day_failed",
                "status": body.status,
                "http_status": outcome.status_code,
                "step": body.failed_step,
                "code": body.error.code.value,
                "retryable": body.error.retryable,
                "may_have_applied": body.error.may_have_applied,
                "created_events": len(body.created),
                "cause": outcome.cause,
            }
        )
    )


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": FocusDayErrorResponse}
    for status in (403, 404, 412, 422, 500, 502, 503)
}


@app.post("/v1/focus-day", response_model=FocusDayResponse, responses=_ERROR_RESPONSES)
def focus_day(body: FocusDayRequest, request: Request) -> JSONResponse:
    """List issues, plan focus blocks, book them, write the standup doc, and
    save the plan."""
    operation: FocusDay = request.app.state.focus_day
    outcome = operation.run(body)
    _log_failure(outcome)
    return JSONResponse(
        status_code=outcome.status_code, content=outcome.body.model_dump(mode="json")
    )


def _field(location: tuple[int | str, ...]) -> str:
    """("body", "busy", 0, "start") -> "busy[0].start"."""
    name = ""
    for part in location[1:]:
        name += f"[{part}]" if isinstance(part, int) else f".{part}" if name else part
    return name or "body"


@app.exception_handler(RequestValidationError)
def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """A malformed body fails the `validate` step before any provider call."""
    details = [
        ErrorDetail(field=_field(tuple(error["loc"])), message=error["msg"])
        for error in exc.errors()
    ]
    outcome = rejected_request(details)
    _log_failure(outcome)
    return JSONResponse(
        status_code=outcome.status_code, content=outcome.body.model_dump(mode="json")
    )


def _plain_error(status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(
        status_code=status, content=body.model_dump(mode="json", exclude_none=True)
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Unknown paths and wrong methods, in the HW1 error shape."""
    if exc.status_code == 404:
        return _plain_error(404, ErrorCode.NOT_FOUND, "Resource not found")
    return _plain_error(
        exc.status_code, ErrorCode.INVALID_INPUT, "Request not supported"
    )


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug: hide it from the caller, log its trace."""
    logger.error(
        json.dumps({"event": "unexpected_error", "cause": type(exc).__name__}),
        exc_info=exc,
    )
    return _plain_error(500, ErrorCode.INTERNAL_ERROR, "Internal server error")
