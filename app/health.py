"""Liveness and readiness reporting.

Liveness (`/healthz`) only says the process is actively serving HTTP, so that the platform
restarts the service only when it is truly stuck. Readiness (`/readyz`) says
whether this process can serve (its configuration was validated at startup
and its idempotency store opens). The provider's reachability is reported for
information only and never changes the status code, so a brief provider
outage cannot make the platform restart a healthy service in a loop.
"""

import os
import re
import time
from collections.abc import Callable, Mapping
from threading import Lock
from typing import Literal

from pydantic import BaseModel

ProviderStatus = Literal["reachable", "unreachable", "unknown"]
# "not_used": the configured provider never opens this dependency, so it does
# not affect readiness.
CheckResult = Literal["ok", "failed", "not_used"]

# Provider checks start at most this often.
PROVIDER_CHECK_INTERVAL_SECONDS = 30.0


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]
    provider: ProviderStatus
    commit: str


# A Git commit ID, full or abbreviated. Anything else is reported as unknown,
# so a misconfigured variable can never echo arbitrary text.
COMMIT_PATTERN = r"[0-9a-f]{7,40}"


def deployed_commit(environ: Mapping[str, str] = os.environ) -> str:
    """Return the commit this process was built from, or "unknown".

    RENDER_GIT_COMMIT (set by Render to the commit it actually built) takes precedence
    over GIT_SHA (the image's build argument, set for local and CI builds).
    """
    for name in ("RENDER_GIT_COMMIT", "GIT_SHA"):
        value = environ.get(name, "")
        if re.fullmatch(COMMIT_PATTERN, value):
            return value
    return "unknown"


class ProviderProbe:
    """Cached provider reachability.

    `check` is a call that returns whether the provider answered and never
    raises (each adapter's `ping`). A new check starts only when the last
    one started at least `interval_seconds` ago. While a check is running,
    other callers get the previous result, or "unknown" before the first
    check finishes, instead of waiting or starting a second check.
    """

    def __init__(
        self,
        check: Callable[[], bool],
        *,
        interval_seconds: float = PROVIDER_CHECK_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._check = check
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._lock = Lock()
        # initialize to status "unkown" until first check.
        self._result: ProviderStatus = "unknown"
        self._started_at: float | None = None

    def _is_fresh(self) -> bool:
        return (
            self._started_at is not None
            and self._clock() - self._started_at < self._interval_seconds
        )

    def status(self) -> ProviderStatus:
        if self._is_fresh() or not self._lock.acquire(blocking=False):
            return self._result
        try:
            # Another caller may have finished a check while this one waited.
            if self._is_fresh():
                return self._result

            self._started_at = self._clock()
            try:
                reachable = self._check()
            except Exception:  # noqa: BLE001
                # `check` should not raise. If a bug makes it, readiness still
                # answers, reporting the provider as unreachable.
                reachable = False
            self._result = "reachable" if reachable else "unreachable"
            return self._result
        finally:
            self._lock.release()
