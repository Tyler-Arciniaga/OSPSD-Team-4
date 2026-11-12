# Observability

The service uses OpenTelemetry for request metrics, structured outcome logs, and
traces. Grafana Cloud receives these through OTLP over HTTP/protobuf.

[Team dashboard](https://redowl1208.grafana.net/d/ckp4g9s/ospsd-team-4-level-3-observability)
requires access to the team's Grafana stack. The queries below allow the panels
to be reconstructed without a dashboard export or credentials in Git.

## Configuration

| Variable | Meaning / value |
| --- | --- |
| `OTEL_ENABLED` | `true` enables remote OTLP export; otherwise console exporters are used. Instrumentation still runs. |
| `OTEL_SERVICE_NAME` | `ospsd-team4-render` for the deployed service; `ospsd-team4-local` for local runs. Falls back to `RENDER_SERVICE_NAME`, then `ospsd-team4-issue-tracker`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Grafana's base OTLP ingestion endpoint, required for remote export. This is not the dashboard URL. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` (the only supported protocol here, and the default). |
| `OTEL_EXPORTER_OTLP_HEADERS` | Grafana's authentication header setting. Secret: share privately and never commit it. |
| `OTEL_MEMORY_LIMIT_MB` | Positive finite memory allocation, default `512`; converted to bytes using 1024 × 1024. Set it to the actual deployed allocation. |

Copy the endpoint and authentication setting from the Grafana stack's
OpenTelemetry connection details. Preserve the provided header encoding; do not
add a signal-specific `/v1/traces`, `/v1/metrics`, or `/v1/logs` suffix to the
base endpoint. Use only the base settings above unless deliberately configuring
signal-specific SDK overrides.

Locally, supply these through the process environment (or the `.env` mechanism
used by the start command in the README). Merely copying `.env.example` does not
export variables into an already-running shell. On Render, use the linked
protected environment group and redeploy; see
[deployment configuration](DEPLOYMENT.md#observability-configuration).

The release label comes from `deployed_commit()` in `app/health.py`; the custom
telemetry resource carries it as `service.version`. Request metrics and outcome
logs also carry `release`. A local checkout without a build SHA may report
`unknown`. Set `GIT_SHA` to the checked-out commit when recording local evidence.

## Signals and interpretation

`app/tracing.py` names request spans after the endpoint: `get_issue`,
`create_issue`, `update_issue`, or `delete_issue`. Health endpoints use `healthz`
and `readyz`; other requests use `routing`. It adds `X-Trace-ID` to responses
passing through the middleware. See the [API correlation details](api.md#request-correlation-and-outcome-logs)
for the unhandled-error limitation.

`app/telemetry.py` records:

| Signal | Exported Prometheus name / contents |
| --- | --- |
| Request count | `issue_requests_total` |
| Error count | `issue_errors_total`, grouped by the shared error category in `outcome` |
| Latency histogram | `issue_request_duration_seconds_bucket`, `_sum`, and `_count` |
| Memory saturation | `issue_memory_saturation_ratio` |
| Outcome log | JSON: `operation`, `outcome`, `status`, `release`, `duration_ms`, `trace_id`, `span_id` |

Metric dimensions are `operation`, `outcome`, and `release`; the memory gauge
uses `release`. Service/instance resource labels may also appear after ingestion.
Issue IDs, user IDs, trace IDs, and raw URLs are not application metric labels.
Outcome logs use INFO for success, WARN for 4xx, and ERROR for 5xx. Existing
Python failure/idempotency diagnostics are separate from this explicit OTLP
outcome stream.

Outbound work is represented by `trello.request` or `linear.request` child spans.
Cache hits, invalid bodies, and other rejections before a provider call can have
no provider span. The custom request span and outcome log are the authoritative
application result: an HTTP 200 from Linear can contain GraphQL errors, and its
network span can finish before those errors are translated. A success status
also does not by itself prove that the returned issue contents were correct;
compare the body to the expected result, as the conformance suite does.

Memory is **this process's RSS divided by the configured allocation**, not a
measurement of all processes in the container. It is observed periodically on
Linux through `/proc/self/statm`. On unsupported systems, including a local Mac,
it produces no sample rather than a misleading zero. A ratio of `0.143` means
approximately `14.3%` of the configured allocation.

## Dashboard queries

Use `grafanacloud-redowl1208-prom` for the four metric panels and
`grafanacloud-redowl1208-logs` for the log panel. The deployed metric filter is
`job="ospsd-team4-render"`; the log filter is
`service_name="ospsd-team4-render"`. The distinct local name keeps local test
traffic out of these panels. If labels differ in another stack, inspect a raw
series before changing the selectors.

### Traffic: requests per second

```promql
sum by (operation, release) (
  rate(issue_requests_total{job="ospsd-team4-render"}[$__rate_interval])
)
```

### Errors: errors per second by category

```promql
sum by (operation, outcome, release) (
  rate(issue_errors_total{job="ospsd-team4-render"}[$__rate_interval])
)
```

Use an automatic upper axis bound, rather than a fixed 100, so small error rates
remain visible. This query is errors per second, not an error percentage.

### Latency: estimated P95 in milliseconds

```promql
1000 * histogram_quantile(
  0.95,
  sum by (le, operation, outcome, release) (
    rate(issue_request_duration_seconds_bucket{job="ospsd-team4-render"}[$__rate_interval])
  )
)
```

P95 is estimated from histogram buckets over a window. It need not equal the
duration of an individual trace. Sparse traffic can leave a series without
enough samples to calculate a rate or percentile.

### Saturation: memory percentage

```promql
100 * issue_memory_saturation_ratio{job="ospsd-team4-render"}
```

Use the percent (0–100) display unit with this multiplied query. The `_ratio`
suffix is part of the observed exported metric name.

### Request outcomes

```logql
{service_name="ospsd-team4-render"} | json | outcome!=""
```

Show timestamps and log levels; wrap lines as needed. Keep the release visible
in the metric legends and log records. Health probes are included in request
metrics and may dominate traffic. Zero traffic or no samples does not establish
health: also check `/healthz`, `/readyz`, and whether telemetry is arriving.

## Find a request across signals

1. Record the API response's status, timestamp, and `X-Trace-ID`.
2. Set a dashboard time range around that request and identify its operation,
   outcome, and release. Metric points are aggregates, not unique request IDs.
3. In Explore, select `grafanacloud-redowl1208-traces`, choose **Trace ID**, paste
   the ID, and run the query. Inspect the request and provider spans.
4. Use **Logs for this trace**, or run this query in the logs source with the
   real trace ID substituted:

   ```logql
   {service_name="ospsd-team4-render"} |= "<trace-id>"
   ```

5. Confirm the log's operation, outcome, status, release, and trace ID match.
   Save links with absolute time ranges for later review.

The log pane can retain the previous trace's narrow time window when the trace
ID is changed. If no logs appear, set a range containing the request time and
rerun before concluding that export failed. Allow for export/ingestion delay.
For metric discovery, try `issue_requests_total{job="ospsd-team4-render"}` or
`{job="ospsd-team4-render", __name__=~".*memory.*"}`. A single first counter sample
can show a nonzero count without a nonzero `rate()` point.

## Export failure behavior

The code configures background span and log processors with **256 records per
queue**, **64 records per batch**, and a **1-second scheduled export delay**.
The installed SDK's bounded queues discard older records when full. Metrics
are aggregated rather than queued per request, with a **10-second export
interval** and **5-second reader timeout**. HTTP exporters are constructed with
**3-second timeouts**. These are export settings, not a guarantee that complete
application shutdown finishes in three seconds.

The SDK catches exporter exceptions in its worker paths. A backend export
failure does not call an issue operation again; any exporter retry concerns
telemetry only. Request metric/log recording also catches exceptions and emits
a generic diagnostic. Shutdown attempts each provider separately and catches
shutdown exceptions. There is no durable telemetry spool: failed batches and
queue overflow can lose logs or traces. Operation/outcome allowlists and a
fixed release per process limit metric cardinality.

Configuration errors are different from a backend outage: missing OTLP endpoint,
unsupported protocol, or an invalid memory allocation raises during startup.
The service is not configured to fall back silently when telemetry construction
fails. The middleware also does not isolate every possible exception from span
creation or span attribute methods. Do not describe this as protection against
all possible instrumentation failures.

The review of archive commit `c813c5577d6ec3ad19b4ed65e659b6f7c8e13656`
checked the queue and worker behavior against the locked OpenTelemetry SDK.
See the review validation below for the local outage experiment; it is separate
from the deployed Grafana checks.

## Privacy

Custom request/provider spans disable automatic exception recording. The custom
provider spans attach no request headers, URLs, bodies, GraphQL variables, or
provider response text. The explicitly emitted outcome JSON contains only the
fields listed above. Exporter diagnostics are replaced with a generic message
by the configured filter.

The deployed sample inspection below covers the visible attributes and log
record actually reviewed. It is not a blanket guarantee about every future
exception or additional instrumentation. Python server logs can include
unexpected-exception stack traces; review them separately before adding a log
forwarder. Likewise, inspect framework-generated spans if instrumentation or
header capture settings change.

## Recorded deployed observations

These observations were made on **2026-11-12 UTC** against
`https://ospsd-team4-issue-tracker.onrender.com`, using service
`ospsd-team4-render`, release
`2e08faca946cff2ecb711e6617ccdf428008b86a`, and configured memory allocation 512 MiB.
Grafana screenshots used America/New_York time (five hours behind UTC).
They describe that release, not the later archive commit or every deployment.

| Request | UTC time | Result | Trace ID |
| --- | --- | --- | --- |
| POST with empty title | 03:40:43 | `422 invalid_input` | `060845334c9ca297ac26ccc88161eee1` |
| POST with a verification title | 03:44:16 | `201`, issue created | `ca0946ec4a92cdd8740ff28303050372` |
| GET of that issue | 03:53:42 | `200`, expected ID/title returned | `13afec652aaca5933cc62e5eaf209059` |
| DELETE of the test issue | 04:13:24 | `204`, cleanup completed | `a86df6feb3b6258198e37a1878f7b636` |

- **Success correlation:** the GET was visible in traffic/latency panels, a
  success log, and a matching trace. Its trace duration was about 187.5 ms,
  with a `trello.request` child of about 185.36 ms. The log recorded status 200,
  `get_issue`, `success`, the release, and the same trace ID.
- **Invalid-input correlation:** the 422 trace and WARN outcome log matched;
  the trace had no provider call. The raw invalid-input counter was present.
  A nonzero dashboard error-rate point was not established for that first sample.
- **Memory:** the corrected panel showed about 14.0–14.3% after using
  `issue_memory_saturation_ratio`.
- **Sample privacy inspection:** the successful POST outcome log contained
  operation, outcome, status, release, duration, and trace/span IDs. The inspected
  `create_issue`, `trello.request`, and outer POST span attributes showed no
  credentials or issue contents. The outer span showed method, status, route,
  scheme, public server host/port, and service/SDK metadata. The provider span
  had no span attributes. This is sample evidence, not an exhaustive trace audit.
- **Cleanup:** the DELETE response confirms cleanup; no matching DELETE
  trace/log inspection is claimed here.

These observations are evidence for the cases listed, not a declaration that
all Level 3 acceptance checks are complete. A deployed provider-failure example
is not established by these observations. Historic traces/logs may expire under
the stack's retention policy; rerun a case when live evidence is needed.

## Review validation

The review used the uploaded archive at commit
`c813c5577d6ec3ad19b4ed65e659b6f7c8e13656`. Documentation/configuration examples were
updated without changing application code or the dependency lockfile.

- Locked dependency installation succeeded. Ruff lint and format checks passed;
  mypy reported no issues in 62 source files.
- The offline suite passed: **566 passed, 46 skipped**. The skipped tests require
  an explicit conformance service URL. The run emitted a Starlette/httpx
  deprecation warning. The review runner's inherited SOCKS proxy settings were
  removed for the local checks after they caused an environment-only import
  failure; no dependency or application change was needed.
- A local controlled service was configured with remote OTLP export pointed at
  a loopback HTTP receiver that returned `503` for every export. Requests to all
  three signal paths (`/v1/traces`, `/v1/metrics`, `/v1/logs`) were observed.
  Create and invalid-input responses remained `201`/`422`; after failed exports,
  GET, PATCH, DELETE and liveness still returned `200`, `200`, `204` and `200`.
  This exercised backend rejection with an in-memory issue provider, not a
  Grafana outage or a live provider failure. Queue bounds were checked in code;
  no saturation/load-test result is claimed.
