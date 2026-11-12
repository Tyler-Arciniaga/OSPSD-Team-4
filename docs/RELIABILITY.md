# Reliability objectives (HW2 Level 4)

Status: proposed targets; implementation and local checks are ready for review.
Live Grafana reporting, notification delivery, and resolution still require the
account owner's verification. Student owner: Vanchi; reviewer: not yet assigned.
Account owner: Tyler. These targets are a team operating objective, not an
external SLA. They do not change the shared API.

## What we measure

The representative operation is `get_issue`: the consumer needs to read an
existing issue before deciding what to change. We measure completed requests
at the service's request middleware, using the Level 3 metrics. Both providers
use the same definitions. Cache hits count because the consumer experiences
them as reads; the existing 30-second freshness limit still applies.

| Indicator | Eligible events | Good events | Proposed SLO |
| --- | --- | --- | --- |
| Availability | Completed `get_issue` requests, excluding `invalid_input` and `not_found` | Eligible requests with `outcome="success"` | At least 99% over a rolling 7 days |
| Latency | The same eligible completed reads | Successful reads whose service duration is at most 1 second | At least 95% over a rolling 7 days |

Provider outages, access denials, missing service configuration and internal
errors count against **both** objectives. A fast failure is not a good latency
event. Unknown/malformed IDs and genuinely missing issues are normal caller
outcomes and are excluded; this also means a mistaken `404` cannot be detected
by these SLIs alone. A wrong body with HTTP 200 is another blind spot: release
conformance tests check contents separately. We do not change eligibility to
improve a result after an incident.

The histogram already has a one-second boundary, so the latency numerator uses
that bucket directly, without estimating a percentile. Its selector accepts
both `le="1"` and the [normalized OpenMetrics value](https://prometheus.io/docs/prometheus/3.14/migration/#le-and-quantile-label-values)
`le="1.0"`. The denominator uses
the corresponding histogram count. Availability uses the request counter.
We aggregate across `release` and process instances: deploying a new commit
must not reset the accounting window. Production is selected by
`job="ospsd-team4-render"`; exercise traffic uses a different job.

Server measurements exclude DNS/TLS/network failures before the request reaches
the middleware, requests lost during a process crash, and client download time.
They cannot prove an idle or unreachable service is healthy. First samples of
a newly created counter series are a baseline, not an observed increase;
Prometheus can miss events before that first export. The seven-day result is
only as complete as the telemetry retained and successfully exported.

## Why these initial targets

On November 12 we measured 30 sequential reads through the deployed service,
one second apart, on release `c813c5577d6ec3ad19b4ed65e659b6f7c8e13656`.
All returned 200 within one second; maximum 199.776 ms, nearest-rank P95
152.197 ms. The [raw timings and procedure](evidence/level4-read-baseline.json)
record the UTC interval and successful deletion of the temporary issue.

This is a small **client-observed warm-path baseline**, including cached reads.
It is not a seven-day measurement or a Grafana SLI report. An interactive read
should usually finish within a second; the baseline leaves substantial room
for normal variability. The provisional 95% latency target allows occasional
slow reads, including cold starts. Availability at 99% allows 10 failures per
1,000 eligible reads while making sustained provider failures actionable.
These are proposed consumer/budget tradeoffs, not statistically established
reliability claims. Review after a full seven-day observation, or earlier if
cold starts, workload, hosting or consumer needs change. Keep the original
incident calculation when changing a future target.

For the recorded 30-read client sample, the availability numerator/denominator
is 30/30 (100%) and the successful-within-one-second count is also 30/30 (100%).
These values describe only the recorded interval, not the server-side SLO.

## Report the SLIs and budgets

For each objective:

```text
SLI = good / eligible
allowed bad events = eligible * (1 - target)
observed bad events = eligible - good
remaining budget = allowed bad events - observed bad events
remaining budget fraction = remaining budget / allowed bad events
```

For 1,000 eligible reads, availability permits 10 failures and latency permits
50 failed-or-slow reads. Fifteen failures spend 15 of the 10 availability
events, leaving -5 (-50% of the budget). Sixty failed-or-slow reads leave -10
(-20%) of the latency budget. Fractional allowances are not rounded up to grant
an extra failure at low volume. Zero eligible requests means **unknown**, not
100%; missing measurements also remain unknown.

Print the exact PromQL, including numerators and denominators:

```bash
uv run --locked python -m scripts.reliability queries
```

Each query uses `sum(increase(...[7d]))`. Increase handles counter resets and
extrapolates at window boundaries, so reported counts can be fractional.
An absent success series becomes zero only if an eligible series exists;
an absent eligible series stays absent. See [Prometheus increase](https://prometheus.io/docs/prometheus/latest/querying/functions/#increase).

In Grafana, open **Connections → Data sources → grafanacloud-redowl1208-prom**
and copy its UID (the name is not necessarily its UID). An account owner can
create a least-privilege service account with access to query this datasource.
Put its token in the shell environment or an ignored local environment file:

```bash
export GRAFANA_URL=https://redowl1208.grafana.net
export GRAFANA_PROMETHEUS_UID=<actual-uid>
read -rs -p 'Grafana query token: ' GRAFANA_TOKEN
export GRAFANA_TOKEN
uv run --locked python -m scripts.reliability report
```

The reporter sends GET requests only, with no retries, through Grafana's
[datasource proxy](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/).
It prints each numerator, denominator, target, current value and remaining
budget, with the job, window and common evaluation time. Backend response
bodies and tokens are never printed. Incomplete/warning responses fail the
report rather than becoming a healthy value.

For reproducible evidence, choose an absolute evaluation time and record the
output, e.g. `report --at 2026-11-12T19:00:00Z --window 30m`. This shorter window
is a recorded test run, not a replacement for the seven-day SLO. Put the
evaluation time, window, release(s), job, counts and values in the submission.
Add the generated good/eligible queries and their ratios to the existing
[team dashboard](https://redowl1208.grafana.net/d/ckp4g9s/ospsd-team-4-level-3-observability).
Display missing/zero-denominator results as No data. Keep the release visible
on the Level 3 panels while the SLO panels aggregate across releases.

## Availability alert

The alert is a Grafana-managed rule on a user-visible symptom: **more than 5%
of eligible GETs fail over five minutes, with at least 20 eligible requests,
for two continuous minutes**. Evaluate every minute. The short alert window
detects sustained trouble; it does not replace either seven-day objective.
At low traffic one failure must not page the team as a 100% outage.

Generate a reviewable API request without contacting Grafana:

```bash
uv run --locked python -m scripts.reliability alert-rule \
  --datasource-uid <actual-prometheus-uid> --folder-uid <team-folder-uid>
```

The generated JSON is the request format for
`POST /api/v1/provisioning/alert-rules`, not a provisioning-file export.
Grafana documents these as [different formats](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/http-api-provisioning/).
`orgID` defaults to 1; confirm the stack's organization before applying.
Creating the rule is a separate operator action. Alternatively, enter query A
from `queries` in the rule editor, use an instant query and a last-value
condition greater than `0.05`, and use the timings above.

Configure all of these in the team stack:

1. Create the rule in a team folder and set its evaluation group to one minute.
2. Create an email or Slack contact point for the **agreed real destination**.
   Leave resolved notifications enabled. Do not store the address/webhook/token
   in Git. Owner confirmation of the destination is still pending.
3. Add a notification policy matching `service=ospsd-team4-render` and
   `operation=get_issue`, routed to that contact point. Suggested grouping:
   service and operation; group wait 30s, group interval 1m, repeat interval 4h.
4. Verify the notification includes the service, condition, dashboard and
   [runbook](RUNBOOK.md). The generated summary/description contain these links.
5. Preview the real datasource query before enabling notifications. A contact
   point's Test button checks delivery only; it does not prove the rule fires.

Below 20 eligible events, the query returns no series. No Data and query errors
use **Keep Last State**: missing telemetry cannot falsely resolve an incident.
A firing alert needs sufficient **healthy** traffic to resolve. If traffic or
telemetry disappears while normal, this rule cannot detect an outage. Check
export freshness/readiness during triage; continuous black-box probing is not
implemented. Query errors retain the previous state and need investigation in
Grafana. These limitations matter on this nearly idle service.

## Error-budget policy

When either seven-day budget is zero or negative, pause feature releases for
the affected service. Ship only reviewed changes needed to restore service,
repair telemetry/security, or reduce the failure. The student owner and one
reviewer inspect the dashboard and traces, identify the cause, and record the
mitigation. A provider failure still spends budget; do not exclude it after
the fact. Resume normal feature releases only after both measured budgets
are positive and a teammate verifies the fix. Unknown/stale data is not
approval to resume; repair measurement first. This is a documented human
release decision; the pipeline does not automatically enforce it.

## Verification and remaining acceptance evidence

Follow the [runbook's disposable alert exercise](RUNBOOK.md#disposable-alert-exercise).
It uses a separate job, the actual GET error/metric path, and a real contact
point. Do not change production's linked Render secrets: the disposable Render
service shares that environment group. Save firing **and resolved** messages,
rule state history, absolute dashboard links, and the SLI report for the run.

| Required evidence | Current state |
| --- | --- |
| Initial read measurement | Recorded in `evidence/level4-read-baseline.json`; 30/30 client-observed reads |
| Seven-day server SLI values and remaining budgets | Reporter implemented; Grafana access required to record actual values |
| Alert installed, real destination configured | JSON generator and setup documented; not yet applied |
| Firing message received | Not yet verified |
| Recovery and resolved message received | Not yet verified |
| Human review of targets, policy and exercise | Pending |

Level 4 is not complete until the live rows above are filled with observed
results. Do not substitute mock tests, the baseline, or rule existence for
notification delivery and recovery. Remove the exercise rule, policy and
temporary contact point after verification; follow the existing November 20
Grafana-stack teardown plan for the remaining resources.

## Local verification

The regular test suite checks accounting, report authentication/response
handling, secret sanitization, alert generation, and the controlled GET fault.
An additional engine check uses Prometheus `promtool` without adding a service
dependency. Download the official Prometheus release (verified here with
3.16.0), then run:

```bash
PROMTOOL=/absolute/path/to/promtool uv run --locked pytest tests/test_reliability.py
```

It evaluates the actual generated PromQL for absent/idle data, zero successes,
caller exclusions, slow successes, fast provider errors, both histogram-label
formats, sparse traffic, release aggregation and counter resets. Without
`PROMTOOL` that one engine check is explicitly skipped. These local checks do
not assert that the team's Grafana rule is installed or notifications arrived.
