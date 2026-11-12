"""SLO accounting and authenticated, read-only Grafana reporting."""

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from scripts.reliability import (
    ReliabilityError,
    alert_query,
    alert_rule,
    budget,
    main,
    queries,
    query_value,
    report,
)


def test_budget_reports_exhaustion_without_clamping_it_away() -> None:
    result = budget(985, 1000, 0.99)
    assert result["value"] == 0.985
    assert result["allowed_bad"] == pytest.approx(10)
    assert result["observed_bad"] == 15
    assert result["remaining_bad"] == pytest.approx(-5)
    assert result["remaining_fraction"] == pytest.approx(-0.5)


@pytest.mark.parametrize("good,eligible", [(None, None), (None, 10), (0, 0)])
def test_missing_or_idle_data_is_not_success(
    good: float | None, eligible: float | None
) -> None:
    result = budget(good, eligible, 0.99)
    assert result["value"] is None
    assert result["remaining_bad"] is None


def test_zero_successes_spends_the_budget() -> None:
    result = budget(0, 100, 0.95)
    assert result["value"] == 0
    assert result["observed_bad"] == 100
    assert result["remaining_bad"] == pytest.approx(-95)


def test_budget_is_exhausted_at_the_objective_boundary() -> None:
    result = budget(990, 1000, 0.99)
    assert result["remaining_bad"] == 0
    assert result["remaining_fraction"] == 0


def test_inconsistent_counts_are_refused() -> None:
    with pytest.raises(ReliabilityError, match="inconsistent"):
        budget(101, 100, 0.99)


@pytest.mark.parametrize(
    "job,window",
    [('bad"selector', "7d"), ("service", "0m"), ("service", "7d]"), ("service", "")],
)
def test_query_arguments_cannot_change_the_selector(job: str, window: str) -> None:
    with pytest.raises(ReliabilityError):
        queries(job, window)


def test_report_uses_one_time_and_preserves_all_failure_categories() -> None:
    values = iter((1000, 985, 1000, 940))
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer fake-secret"
        assert request.url.path == "/api/datasources/proxy/uid/prom/api/v1/query"
        assert request.url.params["time"] == "2026-11-12T19:00:00Z"
        expression = request.url.params["query"]
        # A deployment must not reset the accounting window, and provider
        # failures must not disappear from the denominator.
        assert "release=" not in expression
        assert 'outcome!~"invalid_input|not_found"' in expression
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"value": [0, str(next(values))]}],
                },
            },
        )

    with httpx.Client(
        base_url="https://grafana.example",
        headers={"Authorization": "Bearer fake-secret"},
        transport=httpx.MockTransport(respond),
    ) as client:
        result = report(
            client, "prom", "ospsd-team4-render", "7d", "2026-11-12T19:00:00Z"
        )
    assert len(seen) == 4
    assert result["availability"]["value"] == 0.985
    assert result["latency"]["value"] == 0.94
    assert result["latency"]["remaining_bad"] == pytest.approx(-10)


def test_empty_backend_series_is_unknown() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )
    )
    with httpx.Client(
        base_url="https://grafana.example", transport=transport
    ) as client:
        assert query_value(client, "prom", "expression", "0") is None


@pytest.mark.parametrize(
    "status,body",
    [
        (401, {"secret": "DO-NOT-PRINT"}),
        (200, {"status": "error", "error": "DO-NOT-PRINT"}),
        (
            200,
            {
                "status": "success",
                "data": {"resultType": "vector", "result": [{"value": [0, "NaN"]}]},
            },
        ),
        (200, {"status": "success", "warnings": ["DO-NOT-PRINT"], "data": {}}),
        (
            200,
            {
                "status": "success",
                "data": {"resultType": "vector", "result": [{"value": [0, "-1"]}]},
            },
        ),
    ],
)
def test_backend_errors_are_sanitized(status: int, body: dict[str, object]) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json=body))
    with (
        httpx.Client(base_url="https://grafana.example", transport=transport) as client,
        pytest.raises(ReliabilityError) as caught,
    ):
        query_value(client, "prom", "expression", "0")
    assert "DO-NOT-PRINT" not in str(caught.value)


def test_alert_generation_does_not_need_credentials_or_send_notifications(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GRAFANA_TOKEN", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "reliability",
            "alert-rule",
            "--datasource-uid",
            "prom",
            "--folder-uid",
            "team4",
            "--job",
            "ospsd-team4-level4-test",
        ],
    )
    assert main() == 0
    rule = json.loads(capsys.readouterr().out)
    assert rule["labels"]["service"] == "ospsd-team4-level4-test"
    assert rule["folderUID"] == "team4"
    assert "ospsd-team4-render" not in json.dumps(rule)


def test_alert_is_volume_gated_and_retains_state_without_data() -> None:
    rule = alert_rule("ospsd-team4-render", "prom", "team4")
    assert rule["for"] == "2m"
    assert rule["noDataState"] == rule["execErrState"] == "KeepLast"
    assert " >= 20)" in rule["data"][0]["model"]["expr"]
    assert rule["data"][1]["model"]["conditions"][0]["evaluator"] == {
        "type": "gt",
        "params": [0.05],
    }
    assert "Dashboard:" in rule["annotations"]["description"]
    assert "Runbook:" in rule["annotations"]["description"]


def test_promql_accounting_with_real_prometheus(tmp_path: Path) -> None:
    """Optional engine check: PROMTOOL=/path/to/promtool pytest this file."""
    binary = os.getenv("PROMTOOL")
    if not binary:
        pytest.skip("set PROMTOOL to run the Prometheus engine checks")
    job = "ospsd-team4-render"
    q = queries(job, "7d")

    def series(
        metric: str, outcome: str, values: str, extra: str = ""
    ) -> dict[str, str]:
        labels = f'job="{job}",operation="get_issue",outcome="{outcome}"{extra}'
        return {"series": f"{metric}{{{labels}}}", "values": values}

    cases: list[tuple[list[dict[str, str]], dict[str, float | None], float | None]] = [
        # No data remains unknown, rather than inventing a zero series.
        ([], {"availability_eligible": None, "availability_good": None}, None),
        # Idle data cannot fire an alert or claim a successful request.
        (
            [series("issue_requests_total", "success", "0+0x10")],
            {"availability_eligible": 0, "availability_good": 0},
            None,
        ),
        # First-ever successes are absent; real eligible failures still count.
        (
            [series("issue_requests_total", "provider_error", "0+10x10")],
            {"availability_eligible": 100, "availability_good": 0},
            1,
        ),
        # Caller failures stay excluded, while slow successes spend latency budget.
        (
            [
                series("issue_requests_total", "success", "0+10x10"),
                series("issue_requests_total", "not_found", "0+100x10"),
                series("issue_requests_total", "invalid_input", "0+100x10"),
                series("issue_request_duration_seconds_count", "success", "0+10x10"),
                series(
                    "issue_request_duration_seconds_bucket",
                    "success",
                    "0+9x10",
                    ',le="1"',
                ),
            ],
            {
                "availability_eligible": 100,
                "availability_good": 100,
                "latency_eligible": 100,
                "latency_good": 90,
            },
            0,
        ),
        # A fast error does not enter the successful latency bucket.
        (
            [
                series(
                    "issue_request_duration_seconds_count", "provider_error", "0+10x10"
                ),
                series(
                    "issue_request_duration_seconds_bucket",
                    "provider_error",
                    "0+10x10",
                    ',le="1"',
                ),
            ],
            {"latency_eligible": 100, "latency_good": 0},
            None,
        ),
        # OpenMetrics normalization changes an integer boundary to "1.0".
        (
            [
                series("issue_request_duration_seconds_count", "success", "0+10x10"),
                series(
                    "issue_request_duration_seconds_bucket",
                    "success",
                    "0+9x10",
                    ',le="1.0"',
                ),
            ],
            {"latency_eligible": 100, "latency_good": 90},
            None,
        ),
        # Ten cumulative reads are below the alert's five-minute volume gate.
        (
            [series("issue_requests_total", "provider_error", "0+1x10")],
            {"availability_eligible": 10},
            None,
        ),
        # Release labels must not split or reset the objective.
        (
            [
                series("issue_requests_total", "success", "0+10x10", ',release="a"'),
                series("issue_requests_total", "success", "0+10x10", ',release="b"'),
            ],
            {"availability_eligible": 200, "availability_good": 200},
            0,
        ),
        # A process counter reset is not a negative increment.
        (
            [
                series(
                    "issue_requests_total", "success", "0 10 20 30 40 50 0 10 20 30 40"
                )
            ],
            {"availability_eligible": 90, "availability_good": 90},
            0,
        ),
    ]
    tests = []
    for inputs, expected, alert in cases:
        expressions = {q[name]: value for name, value in expected.items()}
        expressions[alert_query(job)] = alert
        tests.append(
            {
                "interval": "1m",
                "input_series": inputs,
                "promql_expr_test": [
                    {
                        "expr": expression,
                        "eval_time": "10m",
                        "exp_samples": []
                        if value is None
                        else [{"labels": "{}", "value": value}],
                    }
                    for expression, value in expressions.items()
                ],
            }
        )
    specification = tmp_path / "queries.test.json"
    specification.write_text(
        json.dumps({"rule_files": [], "evaluation_interval": "1m", "tests": tests})
    )
    result = subprocess.run(
        [binary, "test", "rules", str(specification)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
