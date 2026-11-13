"""Read-only external contract probe and aggregate evidence report (stdlib only)."""

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def check(
    base_url: str, issue_id: str, title: str, timeout: float = 15
) -> dict[str, Any]:
    """One GET, no retries/writes. Failures remain in the denominator."""
    base_url = base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not issue_id
        or not title
        or not 0 < timeout <= 60
    ):
        raise ValueError(
            "Set a valid base URL, nonempty fixture ID/title and timeout (0,60]."
        )
    target = hashlib.sha256(
        json.dumps([base_url, issue_id, title]).encode()
    ).hexdigest()
    result: dict[str, Any] = {
        "schema_version": 1,
        "sample_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "target": target,
        "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "success": False,
        "status": None,
        "reason": "unknown",
    }
    started = time.perf_counter()
    try:
        request = Request(
            f"{base_url}/issues/{quote(issue_id, safe='')}",
            headers={
                "Accept": "application/json",
                "User-Agent": "ospsd-external-probe/1",
            },
        )
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            result["status"] = response.status
            # Bound memory even if an erroneous server sends a huge response.
            raw = response.read(65537)
        if result["status"] != 200:
            result["reason"] = "http_error"
        elif len(raw) > 65536:
            result["reason"] = "oversized_body"
        else:
            body = json.loads(raw)
            result["success"] = (
                isinstance(body, dict)
                and body.get("id") == issue_id
                and body.get("title") == title
            )
            result["reason"] = "ok" if result["success"] else "contract_mismatch"
    except HTTPError as exc:
        result.update(status=exc.code, reason="http_error")
        exc.close()
    except TimeoutError:
        result["reason"] = "timeout"
    except URLError:
        result["reason"] = "network_error"
    except (ValueError, UnicodeError):
        result["reason"] = "invalid_json"
    except (OSError, HTTPException):
        result["reason"] = "network_error"
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def report(samples: list[dict[str, Any]]) -> str:
    # Downloading the same artifact twice must not inflate the denominator.
    unique = {sample["sample_id"]: sample for sample in samples}
    values = sorted(unique.values(), key=lambda sample: sample["timestamp"])
    if len({sample["target"] for sample in values}) > 1:
        raise ValueError(
            "Do not mix different URLs or fixture configurations in one SLI."
        )
    good = sum(sample["success"] is True for sample in values)
    total = len(values)
    ratio = f"{good / total:.2%}" if total else "No data"
    lines = [
        "# External synthetic probe",
        "",
        f"Availability SLI: **{good}/{total} ({ratio})**",
        "",
        "Denominator: recorded GET attempts; success: HTTP 200 with matching ID/title.",
        "Missed schedules and runner/setup failures are not measured requests.",
        "",
    ]
    if values:
        lines += [
            f"Observed window (UTC): {values[0]['timestamp']} to {values[-1]['timestamp']}.",
            "",
            "| UTC timestamp | Event | Status | Result | Duration (ms) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for sample in values:
            lines.append(
                f"| {sample['timestamp']} | {sample['event']} | "
                f"{sample['status']} | {sample['reason']} | {sample['duration_ms']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("check")
    run.add_argument("--output", type=Path, default=Path("probe-results/sample.json"))
    run.add_argument("--timeout", type=float, default=15)
    summary = commands.add_parser("report")
    summary.add_argument("directory", type=Path)
    summary.add_argument("--event", choices=["schedule", "workflow_dispatch", "local"])
    args = parser.parse_args()
    if args.command == "report":
        samples = [
            json.loads(path.read_text()) for path in args.directory.rglob("sample.json")
        ]
        if args.event:
            samples = [sample for sample in samples if sample["event"] == args.event]
        print(report(samples), end="")
        return 0
    try:
        sample = check(
            os.environ.get("PROBE_BASE_URL", ""),
            os.environ.get("PROBE_ISSUE_ID", ""),
            os.environ.get("PROBE_EXPECTED_TITLE", ""),
            args.timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sample, indent=2) + "\n")
    rendered = report([sample])
    print(rendered, end="")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(rendered)
    return 0 if sample["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
