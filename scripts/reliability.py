"""Report GET SLOs from Grafana, or print a reviewable alert API request.

    uv run --locked python -m scripts.reliability queries
    uv run --locked python -m scripts.reliability report
    uv run --locked python -m scripts.reliability alert-rule --folder-uid ...

Reads GRAFANA_URL, GRAFANA_TOKEN and GRAFANA_PROMETHEUS_UID from the
environment. Never writes to Grafana. See docs/RELIABILITY.md.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

AVAILABILITY_TARGET = 0.99
LATENCY_TARGET = 0.95
DEFAULT_JOB = "ospsd-team4-render"
DASHBOARD = (
    "https://redowl1208.grafana.net/d/ckp4g9s/ospsd-team-4-level-3-observability"
)
RUNBOOK = "https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/main/docs/RUNBOOK.md"


class ReliabilityError(Exception):
    """An operator-facing error that contains no backend payload or secret."""


def queries(job: str, window: str) -> dict[str, str]:
    """Keep reporting and alert eligibility identical; aggregate across releases."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", job):
        raise ReliabilityError("job must contain only letters, digits, _, ., : or -")
    if not re.fullmatch(r"[1-9][0-9]*(s|m|h|d|w)", window):
        raise ReliabilityError("window must be a positive Prometheus duration, e.g. 7d")
    selector = f'job="{job}",operation="get_issue"'
    eligible = selector + ',outcome!~"invalid_input|not_found"'
    success = selector + ',outcome="success"'
    # Prometheus/OpenMetrics can encode the same boundary as 1 or 1.0.
    fast = success + ',le=~"1|1[.]0+"'

    def total(metric: str, labels: str) -> str:
        return f"sum(increase({metric}{{{labels}}}[{window}]))"

    available = total("issue_requests_total", eligible)
    timed = total("issue_request_duration_seconds_count", eligible)
    # Missing success series means zero good events only when eligible data exists.
    # Do not turn a missing backend or an idle service into 100% success.
    return {
        "availability_eligible": available,
        "availability_good": (
            f"({total('issue_requests_total', success)} or (0 * {available}))"
        ),
        "latency_eligible": timed,
        "latency_good": (
            f"({total('issue_request_duration_seconds_bucket', fast)} or (0 * {timed}))"
        ),
    }


def alert_query(job: str) -> str:
    q = queries(job, "5m")
    eligible, good = q["availability_eligible"], q["availability_good"]
    # Filtering (rather than returning zero) preserves No Data below 20 events.
    return f"(({eligible} - {good}) / {eligible}) and ({eligible} >= 20)"


def budget(good: float | None, eligible: float | None, target: float) -> dict[str, Any]:
    """Keep absent/idle data unknown and show overspending as a negative budget."""
    result: dict[str, Any] = {
        "good": good,
        "eligible": eligible,
        "target": target,
        "value": None,
        "allowed_bad": None,
        "observed_bad": None,
        "remaining_bad": None,
        "remaining_fraction": None,
    }
    if good is None or eligible is None or eligible == 0:
        return result
    if not 0 <= good <= eligible:
        raise ReliabilityError("inconsistent good/eligible counts; inspect telemetry")
    allowed = eligible * (1 - target)
    bad = eligible - good
    remaining = good - target * eligible
    result.update(
        value=good / eligible,
        allowed_bad=allowed,
        observed_bad=bad,
        remaining_bad=remaining,
        remaining_fraction=remaining / allowed,
    )
    return result


def query_value(
    client: httpx.Client, uid: str, expression: str, at: str
) -> float | None:
    """Query through Grafana's authenticated datasource proxy, without retries."""
    try:
        response = client.get(
            f"/api/datasources/proxy/uid/{uid}/api/v1/query",
            params={"query": expression, "time": at},
        )
        if not response.is_success:
            raise ReliabilityError(f"Grafana query failed: HTTP {response.status_code}")
        body = response.json()
        if body["status"] != "success" or body.get("warnings"):
            raise ReliabilityError("query failed or returned warnings; inspect Grafana")
        data = body["data"]
        if data["resultType"] != "vector":
            raise ReliabilityError("expected an instant-vector query result")
        rows = data["result"]
        if not rows:
            return None
        if len(rows) != 1:
            raise ReliabilityError("expected one aggregate series")
        value = float(rows[0]["value"][1])
        if not math.isfinite(value) or value < 0:
            raise ReliabilityError("query returned a non-finite or negative count")
        return value
    except httpx.HTTPError:
        raise ReliabilityError("Grafana query could not complete") from None
    except (KeyError, IndexError, TypeError, ValueError):
        raise ReliabilityError("Grafana returned an invalid query result") from None


def report(
    client: httpx.Client, uid: str, job: str, window: str, at: str
) -> dict[str, Any]:
    counts = {
        name: query_value(client, uid, expression, at)
        for name, expression in queries(job, window).items()
    }
    return {
        "job": job,
        "operation": "get_issue",
        "window": window,
        "evaluated_at": at,
        "availability": budget(
            counts["availability_good"],
            counts["availability_eligible"],
            AVAILABILITY_TARGET,
        ),
        "latency": budget(
            counts["latency_good"], counts["latency_eligible"], LATENCY_TARGET
        ),
    }


def alert_rule(job: str, datasource_uid: str, folder_uid: str) -> dict[str, Any]:
    """Grafana-managed alert API format, not provisioning-file export format."""
    return {
        "title": f"{job}: GET availability degraded",
        "ruleGroup": "issue-service-reliability",
        "folderUID": folder_uid,
        "orgID": 1,
        "condition": "B",
        "for": "2m",
        "noDataState": "KeepLast",
        "execErrState": "KeepLast",
        "isPaused": False,
        "labels": {"service": job, "operation": "get_issue", "severity": "warning"},
        "annotations": {
            "summary": f"{job}: more than 5% of eligible GETs failed over 5 minutes",
            "description": (
                "At least 20 eligible GETs; condition persisted for 2 minutes. "
                f"Investigate and restore reads. Dashboard: {DASHBOARD} Runbook: {RUNBOOK}"
            ),
            "dashboard_url": DASHBOARD,
            "runbook_url": RUNBOOK,
        },
        "data": [
            {
                "refId": "A",
                "datasourceUid": datasource_uid,
                "relativeTimeRange": {"from": 300, "to": 0},
                "model": {
                    "refId": "A",
                    "expr": alert_query(job),
                    "instant": True,
                    "range": False,
                    "editorMode": "code",
                    "datasource": {"type": "prometheus", "uid": datasource_uid},
                },
            },
            {
                "refId": "B",
                "datasourceUid": "__expr__",
                "relativeTimeRange": {"from": 0, "to": 0},
                "model": {
                    "refId": "B",
                    "type": "classic_conditions",
                    "datasource": {"type": "__expr__", "uid": "__expr__"},
                    "conditions": [
                        {
                            "type": "query",
                            "query": {"params": ["A"]},
                            "reducer": {"type": "last", "params": []},
                            "evaluator": {"type": "gt", "params": [0.05]},
                            "operator": {"type": "and"},
                        }
                    ],
                },
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("queries", "report", "alert-rule"))
    parser.add_argument("--job", default=DEFAULT_JOB)
    parser.add_argument("--window", default="7d")
    parser.add_argument(
        "--at", help="Prometheus evaluation time (RFC3339 or Unix seconds)"
    )
    parser.add_argument("--folder-uid")
    parser.add_argument("--datasource-uid", default=os.getenv("GRAFANA_PROMETHEUS_UID"))
    args = parser.parse_args()
    try:
        expressions = queries(args.job, args.window)
        if args.command == "queries":
            output = {**expressions, "alert": alert_query(args.job)}
        else:
            uid = args.datasource_uid
            if not uid or not re.fullmatch(r"[A-Za-z0-9_-]+", uid):
                raise ReliabilityError(
                    "set a valid GRAFANA_PROMETHEUS_UID or --datasource-uid"
                )
            if args.command == "alert-rule":
                if not args.folder_uid:
                    raise ReliabilityError("--folder-uid is required")
                output = alert_rule(args.job, uid, args.folder_uid)
            else:
                url = os.getenv("GRAFANA_URL", "")
                token = os.getenv("GRAFANA_TOKEN", "")
                parsed = httpx.URL(url)
                if (
                    parsed.scheme != "https"
                    or not parsed.host
                    or parsed.userinfo
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ReliabilityError(
                        "GRAFANA_URL must be an HTTPS URL without credentials or query"
                    )
                if not token.strip():
                    raise ReliabilityError("GRAFANA_TOKEN is required")
                at = args.at or datetime.now(UTC).isoformat()
                with httpx.Client(
                    base_url=url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                ) as client:
                    output = report(client, uid, args.job, args.window, at)
        print(json.dumps(output, indent=2, allow_nan=False))
        return 0
    except (ReliabilityError, httpx.InvalidURL) as exc:
        message = (
            str(exc) if isinstance(exc, ReliabilityError) else "invalid GRAFANA_URL"
        )
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
