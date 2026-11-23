"""The product API over HTTP, and its responses as plain values.

Response shapes are those documented in product/docs/DESIGN.md (4.1) and
product/docs/ERRORS.md. Anything else, a network failure or a timeout is
returned as a `RequestFailure`, never raised, so the UI always has
something to show.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

TRACE_HEADER = "X-Trace-ID"


class _Unexpected(Exception):
    """The body does not have the documented shape."""


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    step: str | None = None
    retryable: bool = False
    may_have_applied: bool = False
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Step:
    name: str
    status: str
    error: ApiError | None = None


@dataclass(frozen=True)
class Block:
    issue_id: str
    title: str
    start: str
    end: str


@dataclass(frozen=True)
class Plan:
    blocks: tuple[Block, ...]
    unscheduled: tuple[str, ...]
    # The plan exactly as received, sent back unchanged in `replay`.
    raw: dict[str, Any] = field(compare=False)


@dataclass(frozen=True)
class FocusDayResult:
    """A documented focus-day body: success (200) or a step failure."""

    http_status: int
    status: str  # ok, partial or failed
    steps: tuple[Step, ...]
    plan: Plan | None
    created: dict[str, str]
    doc_path: str | None
    failed_step: str | None
    error: ApiError | None
    trace_id: str | None = None


FailureKind = Literal["error", "unreachable", "timeout", "unexpected"]


@dataclass(frozen=True)
class RequestFailure:
    """No focus-day body: a plain HW1 error, no answer, or an unknown shape."""

    kind: FailureKind
    http_status: int | None = None
    error: ApiError | None = None
    trace_id: str | None = None


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise _Unexpected
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise _Unexpected
    return value


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Unexpected
    return value


def _list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise _Unexpected
    return value


def parse_error(value: object) -> ApiError:
    data = _dict(value)
    details = data.get("details") or []
    return ApiError(
        code=_str(data.get("code")),
        message=_str(data.get("message")),
        step=None if data.get("step") is None else _str(data["step"]),
        retryable=_bool(data.get("retryable", False)),
        may_have_applied=_bool(data.get("may_have_applied", False)),
        details=tuple(
            (_str(_dict(item).get("field")), _str(item.get("message")))
            for item in _list(details)
        ),
    )


def parse_plan(value: object) -> Plan:
    data = _dict(value)
    blocks = tuple(
        Block(
            issue_id=_str(_dict(item).get("issue_id")),
            title=_str(item.get("title")),
            start=_str(item.get("start")),
            end=_str(item.get("end")),
        )
        for item in _list(data.get("blocks"))
    )
    unscheduled = tuple(
        _str(_dict(item).get("issue_id")) for item in _list(data.get("unscheduled"))
    )
    return Plan(blocks=blocks, unscheduled=unscheduled, raw=data)


def plan_or_none(value: object) -> Plan | None:
    """A plan, or None when `value` is not one (for plans read back from disk)."""
    try:
        return parse_plan(value)
    except _Unexpected:
        return None


def parse_focus_day(
    http_status: int, body: object, trace_id: str | None = None
) -> FocusDayResult | RequestFailure:
    """A focus-day body, a plain HW1 error, or an unexpected response."""
    try:
        data = _dict(body)
        if "steps" not in data:
            if http_status >= 400 and "error" in data:
                return RequestFailure(
                    "error", http_status, parse_error(data["error"]), trace_id
                )
            raise _Unexpected
        status = _str(data.get("status"))
        if (http_status == 200) != (status == "ok") or status not in (
            "ok",
            "partial",
            "failed",
        ):
            raise _Unexpected
        steps = tuple(
            Step(
                name=_str(_dict(item).get("name")),
                status=_str(item.get("status")),
                error=None if item.get("error") is None else parse_error(item["error"]),
            )
            for item in _list(data.get("steps"))
        )
        created = {
            _str(key): _str(value) for key, value in _dict(data.get("created")).items()
        }
        plan = None if data.get("plan") is None else parse_plan(data["plan"])
        doc = data.get("doc")
        error = None if data.get("error") is None else parse_error(data["error"])
        if status == "ok" and doc is None:
            raise _Unexpected
        if status != "ok" and error is None:
            raise _Unexpected
        return FocusDayResult(
            http_status=http_status,
            status=status,
            steps=steps,
            plan=plan,
            created=created,
            doc_path=None if doc is None else _str(_dict(doc).get("path")),
            failed_step=None
            if data.get("failed_step") is None
            else _str(data["failed_step"]),
            error=error,
            trace_id=trace_id,
        )
    except _Unexpected:
        return RequestFailure("unexpected", http_status, trace_id=trace_id)


class ApiClient:
    """One short-lived HTTP client per call; `transport` is injected by tests."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self._transport
        )

    async def _send(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, object, str | None] | RequestFailure:
        try:
            async with self._client() as client:
                response = await client.request(method, path, json=body)
        except httpx.TimeoutException:
            return RequestFailure("timeout")
        except httpx.HTTPError:
            return RequestFailure("unreachable")
        trace_id = response.headers.get(TRACE_HEADER)
        try:
            payload: object = response.json()
        except ValueError:
            return RequestFailure("unexpected", response.status_code, trace_id=trace_id)
        return response.status_code, payload, trace_id

    async def ready(self) -> RequestFailure | None:
        """None when `GET /readyz` answers 200, otherwise why not."""
        sent = await self._send("GET", "/readyz")
        if isinstance(sent, RequestFailure):
            return sent
        status, _, trace_id = sent
        return (
            None
            if status == 200
            else RequestFailure("unexpected", status, None, trace_id)
        )

    async def focus_day(self, body: dict[str, Any]) -> FocusDayResult | RequestFailure:
        sent = await self._send("POST", "/v1/focus-day", body)
        if isinstance(sent, RequestFailure):
            return sent
        return parse_focus_day(*sent)
