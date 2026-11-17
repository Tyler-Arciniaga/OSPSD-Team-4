"""Post-deployment smoke check for the product service.

Run by the product release workflow (.github/workflows/release-product.yml)
after a deploy. It needs no credentials: the service holds them.

1. Waits for /healthz, then for /readyz to be ready and report the commit.
2. Calls POST /v1/focus-day, depending on the target:

   - `fakes` (the disposable service, which runs the in-memory providers):
     a full run must create one event per block and write the doc; a replay
     of that response must keep every event and skip `list_issues` and
     `plan`.
   - `live` (production, real providers): the day is fully booked, so the
     plan has no blocks and no calendar event is created. The run still
     reads the real tracker and writes the real doc, at the same path every
     time (/standups/2000-01-03/release-smoke.md), so repeated releases
     overwrite one file instead of adding more. The calendar is not
     exercised: the service has no way to remove an event it creates.

   Both targets also send an invalid request, which must fail the `plan`
   step with 422 before any provider is called.

It prints step statuses and counts only, never issue titles or provider
text, and appends a summary to $GITHUB_STEP_SUMMARY when it is set.

Usage: uv run python scripts/smoke_check.py --url <base-url> --commit <sha> --mode fakes|live
Exit status: 0 if every check passed, 1 otherwise.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

READY_TIMEOUT_SECONDS = 300.0
READY_POLL_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 60.0

SMOKE_ASSIGNEE = "release-smoke"
FAKES_REQUEST: dict[str, Any] = {"assignee": SMOKE_ASSIGNEE, "day": "2026-11-18"}
# 2000-01-03 is a Monday; the busy interval covers the whole working window.
LIVE_REQUEST: dict[str, Any] = {
    "assignee": SMOKE_ASSIGNEE,
    "day": "2000-01-03",
    "timezone": "America/New_York",
    "working_window": {"start": "09:00", "end": "17:00"},
    "busy": [
        {"start": "2000-01-03T09:00:00-05:00", "end": "2000-01-03T17:00:00-05:00"}
    ],
}
INVALID_REQUEST: dict[str, Any] = {"assignee": "../smoke", "day": "2026-11-18"}


class Client(Protocol):
    """What the checks need from an HTTP client: an httpx.Client in a release,
    a FastAPI TestClient in tests. Responses need `status_code` and `json()`.
    """

    def get(self, url: str) -> Any: ...
    def post(self, url: str, *, json: Any) -> Any: ...


class SmokeError(Exception):
    """A smoke check failed. The message is safe to print."""


def wait_until_ready(
    client: Client,
    commit: str,
    *,
    timeout: float = READY_TIMEOUT_SECONDS,
    poll: float = READY_POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = clock() + timeout
    problem = "no response"
    while True:
        try:
            if client.get("/healthz").status_code == 200:
                ready = client.get("/readyz")
                body = ready.json()
                if ready.status_code == 200 and body.get("commit") == commit:
                    return
                problem = (
                    f"/readyz is {ready.status_code}, commit {body.get('commit')!r}"
                )
            else:
                problem = "/healthz is not 200"
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            problem = type(exc).__name__
        if clock() >= deadline:
            raise SmokeError(f"not ready with commit {commit}: {problem}")
        sleep(poll)


def _post(client: Client, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = client.post("/v1/focus-day", json=body)
    try:
        payload = response.json()
    except ValueError:
        raise SmokeError(f"HTTP {response.status_code} with a non-JSON body") from None
    if not isinstance(payload, dict):
        raise SmokeError(f"HTTP {response.status_code} with a non-object body")
    return response.status_code, payload


def _steps(body: dict[str, Any]) -> dict[str, str]:
    return {step["name"]: step["status"] for step in body.get("steps", [])}


def _expect_ok(status: int, body: dict[str, Any], what: str) -> None:
    if status != 200 or body.get("status") != "ok":
        error = body.get("error") or {}
        raise SmokeError(
            f"{what}: HTTP {status}, status {body.get('status')!r}, "
            f"failed step {body.get('failed_step')!r}, code {error.get('code')!r}"
        )


def check_fakes(client: Client) -> list[str]:
    status, first = _post(client, FAKES_REQUEST)
    _expect_ok(status, first, "first run")
    blocks = first["plan"]["blocks"]
    if not blocks or len(first["created"]) != len(blocks):
        raise SmokeError(
            f"first run: {len(blocks)} blocks but {len(first['created'])} events"
        )
    if first["doc"]["path"] != f"/standups/2026-11-18/{SMOKE_ASSIGNEE}.md":
        raise SmokeError("first run: the doc was written to an unexpected path")

    replay = FAKES_REQUEST | {
        "replay": {"plan": first["plan"], "created": first["created"]}
    }
    status, second = _post(client, replay)
    _expect_ok(status, second, "replay")
    if second["created"] != first["created"]:
        raise SmokeError("replay: the events changed")
    if _steps(second).get("list_issues") != "skipped":
        raise SmokeError("replay: list_issues was not skipped")
    return [
        f"first run: ok, {len(blocks)} blocks, {len(first['created'])} events, doc written",
        "replay: ok, every event kept, list_issues and plan skipped",
    ]


def check_live(client: Client) -> list[str]:
    status, body = _post(client, LIVE_REQUEST)
    _expect_ok(status, body, "booked day")
    if body["plan"]["blocks"] or body["created"]:
        raise SmokeError("booked day: a block or event was created")
    if body["doc"]["path"] != f"/standups/2000-01-03/{SMOKE_ASSIGNEE}.md":
        raise SmokeError("booked day: the doc was written to an unexpected path")
    return [
        (
            f"booked day: ok, tracker read ({len(body['plan']['unscheduled'])} "
            "issues, none scheduled), no events, doc written"
        ),
    ]


def check_invalid(client: Client) -> str:
    status, body = _post(client, INVALID_REQUEST)
    if status != 422 or body.get("failed_step") != "plan":
        raise SmokeError(
            f"invalid request: HTTP {status}, failed step {body.get('failed_step')!r}"
        )
    return "invalid request: 422, rejected in the plan step"


def run(client: Client, *, commit: str, mode: str, **ready: Any) -> list[str]:
    wait_until_ready(client, commit, **ready)
    lines = [f"ready with commit {commit}"]
    lines += check_fakes(client) if mode == "fakes" else check_live(client)
    lines.append(check_invalid(client))
    return lines


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--mode", required=True, choices=("fakes", "live"))
    args = parser.parse_args(argv)

    with httpx.Client(
        base_url=args.url.rstrip("/"), timeout=REQUEST_TIMEOUT_SECONDS
    ) as client:
        try:
            lines = run(client, commit=args.commit, mode=args.mode)
        except SmokeError as exc:
            print(f"Smoke check FAILED: {exc}")
            _summary(
                f"## Product smoke check: FAILED\n\n{args.url} ({args.mode}): {exc}"
            )
            return 1
    for line in lines:
        print(f"  PASS  {line}")
    _summary(
        f"## Product smoke check: passed\n\n{args.url} ({args.mode}):\n\n"
        + "\n".join(f"- {line}" for line in lines)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
