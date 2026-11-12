"""Evaluate the copyable documentation queries with the real Prometheus engine."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[1] / "docs" / "RELIABILITY.md"


def check_queries(
    tmp_path: Path, inputs: list[dict[str, str]], expected: dict[str, float | None]
) -> None:
    binary = os.getenv("PROMTOOL")
    if not binary:
        pytest.skip("set PROMTOOL to evaluate the documentation queries")
    queries = dict(
        re.findall(r"```promql\n# (\w+)\n(.*?)\n```", DOC.read_text(), re.DOTALL)
    )
    tests = [
        {
            "expr": queries[name],
            "eval_time": "10m",
            "exp_samples": [] if value is None else [{"labels": "{}", "value": value}],
        }
        for name, value in expected.items()
    ]
    specification = tmp_path / "queries.json"
    specification.write_text(
        json.dumps(
            {
                "rule_files": [],
                "evaluation_interval": "1m",
                "tests": [
                    {
                        "interval": "1m",
                        "input_series": inputs,
                        "promql_expr_test": tests,
                    }
                ],
            }
        )
    )
    result = subprocess.run(
        [binary, "test", "rules", str(specification)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def series(metric: str, outcome: str, values: str, extra: str = "") -> dict[str, str]:
    labels = (
        f'job="ospsd-team4-render",operation="get_issue",outcome="{outcome}"{extra}'
    )
    return {"series": f"{metric}{{{labels}}}", "values": values}


@pytest.mark.parametrize(
    "outcome,values,eligible,good,alert",
    [
        (None, "", None, None, None),
        ("success", "0+0x10", 0, 0, None),
        ("success", "0+10x10", 100, 100, 0),
        ("provider_error", "0+10x10", 100, 0, 1),
        ("access_denied", "0+10x10", 100, 0, 1),
        ("invalid_input", "0+100x10", None, None, None),
        ("not_found", "0+100x10", None, None, None),
        ("provider_error", "0+1x10", 10, 0, None),
        ("success", "0 10 20 30 40 50 0 10 20 30 40", 90, 90, 0),
    ],
)
def test_availability_and_alert(
    tmp_path: Path,
    outcome: str | None,
    values: str,
    eligible: float | None,
    good: float | None,
    alert: float | None,
) -> None:
    inputs = (
        [] if outcome is None else [series("issue_requests_total", outcome, values)]
    )
    check_queries(
        tmp_path,
        inputs,
        {"availability_eligible": eligible, "availability_good": good, "alert": alert},
    )


@pytest.mark.parametrize("boundary", ["1", "1.0"])
@pytest.mark.parametrize("outcome,good", [("success", 90), ("provider_error", 0)])
def test_slow_successes_and_fast_failures(
    tmp_path: Path, boundary: str, outcome: str, good: float
) -> None:
    inputs = [
        series("issue_request_duration_seconds_count", outcome, "0+10x10"),
        series(
            "issue_request_duration_seconds_bucket",
            outcome,
            "0+9x10",
            f',le="{boundary}"',
        ),
    ]
    check_queries(tmp_path, inputs, {"latency_eligible": 100, "latency_good": good})


def test_deployment_does_not_reset_the_window(tmp_path: Path) -> None:
    inputs = [
        series("issue_requests_total", "success", "0+10x10", f',release="{release}"')
        for release in ("old", "new")
    ]
    check_queries(
        tmp_path,
        inputs,
        {"availability_eligible": 200, "availability_good": 200, "alert": 0},
    )
