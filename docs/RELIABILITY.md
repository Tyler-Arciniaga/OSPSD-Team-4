# Reliability (HW2 Level 4)

Status: proposed targets; live verification pending. Student owner: Vanchi.
Grafana account owner and requested reviewer: Tyler.

## Objectives and initial measurement

`get_issue` represents the consumer reading an existing issue before changing
it. Measure completed requests at the service middleware, including cache hits.
Exclude `invalid_input` and `not_found`; include provider outages, access denials,
missing configuration and internal errors in both denominators.

| SLI | Good events / eligible events | Proposed SLO |
| --- | --- | --- |
| Availability | Successful reads / eligible reads | 99% over rolling 7 days |
| Latency | Successful reads taking at most 1 second / eligible reads | 95% over rolling 7 days |

On November 12, 30 deployed reads returned 200 within one second: maximum
199.776 ms, nearest-rank P95 152.197 ms. [Raw timings](evidence/level4-read-baseline.json)
record the interval, release, procedure and successful deletion of the fixture.
Both client-observed ratios were 30/30 (100%). This small warm-path sample,
including cached reads, is not a seven-day or server-side SLI measurement.

An interactive read should usually finish within a second; the baseline leaves
room for variability. These provisional targets permit occasional cold starts
and failures: at 1,000 eligible reads, availability permits 10 failures and
latency permits 50 failed-or-slow reads. Review after seven days of data or a
change in hosting/workload/consumer needs. Team agreement is still required.

Server measurements cannot see DNS/network failures before the middleware,
requests lost in a crash, or client download time. A wrong 200 body or mistaken
404 can escape these SLIs; conformance checks test contents separately. Missing
telemetry and zero traffic prove nothing about health. `increase` handles
counter resets but extrapolates (counts can be fractional) and cannot count
events before the first exported sample. Do not change eligibility after an
incident to improve the result.

## Queries and error budgets

In the [team Grafana dashboard](https://redowl1208.grafana.net/d/ckp4g9s/ospsd-team-4-level-3-observability),
use the `grafanacloud-redowl1208-prom` datasource and instant queries below.
They aggregate across releases so deployments do not reset the window.
Assign references A–D in order. The latency bucket selector accepts both `1`
and [normalized `1.0`](https://prometheus.io/docs/prometheus/3.14/migration/#le-and-quantile-label-values).
Missing good series become zero only when eligible data exists.

```promql
# availability_eligible
sum(increase(issue_requests_total{
  job="ospsd-team4-render", operation="get_issue", outcome!~"invalid_input|not_found"
}[7d]))
```

```promql
# availability_good
sum(increase(issue_requests_total{
  job="ospsd-team4-render", operation="get_issue", outcome="success"
}[7d])) or (0 * sum(increase(issue_requests_total{
  job="ospsd-team4-render", operation="get_issue", outcome!~"invalid_input|not_found"
}[7d])))
```

```promql
# latency_eligible
sum(increase(issue_request_duration_seconds_count{
  job="ospsd-team4-render", operation="get_issue", outcome!~"invalid_input|not_found"
}[7d]))
```

```promql
# latency_good
sum(increase(issue_request_duration_seconds_bucket{
  job="ospsd-team4-render", operation="get_issue", outcome="success", le=~"1|1[.]0+"
}[7d])) or (0 * sum(increase(issue_request_duration_seconds_count{
  job="ospsd-team4-render", operation="get_issue", outcome!~"invalid_input|not_found"
}[7d])))
```

Add Grafana Math expressions: availability `$B/$A`, latency `$D/$C`, remaining
availability budget `$B-0.99*$A`, remaining latency budget `$D-0.95*$C`.
Display ratios as percent (0–1), budgets as event counts, and absent/zero-
denominator results as No data, never 100%. Preserve negative budgets.

Allowed bad events = eligible × (1 − target); observed bad = eligible − good.
For 1,000 reads, 15 failures leave -5 availability events; 60 failed-or-slow
reads leave -10 latency events. Do not round a fractional allowance up.

**Policy:** when either seven-day budget is zero or negative, pause feature
releases. Ship only reviewed recovery, security or measurement repairs. Owner
and reviewer investigate the cause and record the mitigation; provider outages
still spend budget. Resume feature releases after both measured budgets are
positive and a teammate verifies recovery. Unknown/stale data is not approval
to resume. This is a human release decision, not an automatic pipeline gate.

## Configure one alert

In Grafana Alerting, create a Grafana-managed rule using this instant query:

```promql
# alert
(
  (sum(increase(issue_requests_total{
    job="ospsd-team4-render", operation="get_issue", outcome!~"success|invalid_input|not_found"
  }[5m])) or vector(0))
  / sum(increase(issue_requests_total{
    job="ospsd-team4-render", operation="get_issue", outcome!~"invalid_input|not_found"
  }[5m]))
) and (sum(increase(issue_requests_total{
  job="ospsd-team4-render", operation="get_issue", outcome!~"invalid_input|not_found"
}[5m])) >= 20)
```

Condition: last value **greater than 0.05**; evaluate every minute; pending
period **2 minutes**. This alerts on more than 5% failed eligible reads over
five minutes with at least 20 events, rather than one failure on an idle app.
Use Keep Last State for No Data and query errors. Below 20 events the query
returns no series, so stopping traffic cannot falsely resolve an incident.
If normal traffic or telemetry disappears, this rule cannot detect an outage;
check freshness during triage. The five-minute alert does not replace the SLOs.

Set labels `service=ospsd-team4-render`, `operation=get_issue`. Set the summary
to "ospsd-team4-render: more than 5% of eligible GETs failed over five minutes".
Include the [dashboard](https://redowl1208.grafana.net/d/ckp4g9s/ospsd-team-4-level-3-observability)
and [runbook](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/main/docs/RELIABILITY.md#respond-to-the-alert)
links in the description. Route those labels to the team's agreed email or
Slack contact point, leaving resolved notifications enabled. Suggested grouping:
service/operation; group wait 30s, group interval 1m, repeat interval 4h.
Destination and cloud configuration remain pending; a contact-point Test
message alone does not establish that the rule fires and resolves.

## Respond to the alert

1. Open the dashboard at the alert time. Check the job and release, then an
   affected log/trace ID. Distinguish provider, credential/configuration and
   internal failures; check whether telemetry is fresh.
2. Check production `/healthz` and `/readyz`, then read a known existing test
   issue and check its body. Readiness 200 or an unknown-ID 404 is insufficient.
3. If a release caused it, follow the [known-good rollback](DEPLOYMENT.md#roll-back)
   and its live smoke check. Otherwise have Tyler repair the provider/config.
   Rollback does not undo provider data or manually changed secrets.
4. Keep healthy reads flowing until the five-minute error ratio is at most
   5% with 20+ eligible events. Confirm Normal state **and receipt of the
   resolved notification**. Record UTC times, release, cause, mitigation,
   dashboard/trace links and the seven-day budgets; apply the policy above.

## Disposable verification

Use a local controlled service exporting to Grafana with the team's existing
OTLP settings and `OTEL_SERVICE_NAME=ospsd-team4-level4-test`. Obtain ingestion
credentials privately from Tyler. Duplicate the alert for this job by replacing
the job in its query/labels; route to an agreed real destination and tell its
members before starting. Do not edit production's shared Render secrets.

```bash
GIT_SHA="$(git rev-parse HEAD)" uv run --locked uvicorn tests.controlled.server:app \
  --host 127.0.0.1 --port 8001
```

Run this bounded generator in another terminal for each phase:

```bash
uv run --locked python - <<'PY'
import time
from collections import Counter
from datetime import UTC, datetime
import httpx

with httpx.Client(base_url="http://127.0.0.1:8001", timeout=15) as client:
    created = client.post("/issues", json={"title": "Level 4 disposable test"})
    created.raise_for_status()
    location, statuses = created.headers["Location"], Counter()
    print("Start:", datetime.now(UTC).isoformat(), flush=True)
    try:
        for _ in range(360):
            statuses[client.get(location).status_code] += 1
            time.sleep(1)
    finally:
        client.delete(location).raise_for_status()
    print("End:", datetime.now(UTC).isoformat(), dict(statuses))
PY
```

1. Healthy phase: confirm successful GET metrics and Normal rule state.
2. Stop the server; restart with `CONTROLLED_FAULT=get_fails` prefixed to the
   command. Run the generator again: GETs return `502 provider_error`, but
   create/delete still work. Save the rule state history and **received firing
   message**. Allow for two exported samples, ingestion and notification delays.
3. Restart healthy with the fault unset and run again. Each restart needs a
   new issue. Allow old failures to leave the five-minute window; save Normal
   state and the **received resolved message**. Extend healthy traffic if needed.
4. At one recorded absolute UTC end time, run A–D with `[30m]` and the test job
   in Grafana Explore. Save both numerators, denominators, ratios and remaining
   budgets. Record the window, job and release(s). Separately report production's
   seven-day values. Save absolute dashboard links and sanitized notifications.
5. Stop the server and remove the exercise alert/policy and any temporary contact
   point/token. The generator deletes fixtures; stopping the server discards its
   in-memory data. No real provider data is created by this failure exercise.

**Remaining acceptance:** teammate approval, actual server SLI/budget results,
cloud alert installation, and received firing/resolved messages. The recorded
baseline and local tests do not replace these. Level 4 is still incomplete.
Remove remaining Grafana resources under the November 20 teardown plan.

The documented queries have a real Prometheus engine check:
`PROMTOOL=/path/to/promtool uv run --locked pytest tests/test_reliability.py`.
Verified with Prometheus 3.16.0; these checks skip when `PROMTOOL` is unset.
