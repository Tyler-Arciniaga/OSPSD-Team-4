"""The product service's HTTP app.

`/healthz` and `/readyz` follow the HW2 service (docs/api.md#health-checks):
liveness checks nothing, readiness reports that startup configuration was
validated and which commit is running. The focus-day operation is added in
WP5.
"""

import json
import logging
import os
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from ospsd_team4_product.config import ConfigError, load_config

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
    """Validate configuration before serving; a failure stops startup."""
    try:
        app.state.config = load_config()
    except ConfigError as exc:
        logger.critical(
            json.dumps({"event": "startup_config_invalid", "message": str(exc)})
        )
        raise
    yield


app = FastAPI(title="OSPSD Team 4 Product Service", lifespan=lifespan)


@app.get("/healthz", response_model=LivenessResponse)
def healthz() -> LivenessResponse:
    """Liveness: the process is up and serving HTTP. Checks nothing else."""
    return LivenessResponse(status="ok")


@app.get("/readyz", response_model=ReadinessResponse)
def readyz() -> ReadinessResponse:
    """Readiness: startup only completes once configuration is validated.

    Providers are not contacted, so a partner outage never makes the
    platform restart the service.
    """
    return ReadinessResponse(
        status="ready", checks={"config": "ok"}, commit=deployed_commit()
    )
