# GET availability alert

Student owner: Vanchi. Grafana/Render account owner: Tyler. A teammate reviews
the diagnosis and recovery. The [reliability objectives](RELIABILITY.md) define
the eligible reads and budget policy.

Alert: more than 5% of eligible `get_issue` requests fail over five minutes,
at least 20 eligible requests, sustained for two minutes.

## Triage

1. Open the [dashboard](https://redowl1208.grafana.net/d/ckp4g9s/ospsd-team-4-level-3-observability)
   at the notification time. Check the service/job label: production is
   `ospsd-team4-render`; the exercise is `ospsd-team4-level4-test`.
2. Find the error outcome and deployed release. Open an affected trace/log via
   its trace ID. Distinguish `provider_error`, `access_denied`,
   `service_not_configured`, and `internal_error`. Check telemetry freshness:
   Keep Last State can leave an old firing alert when data stops arriving.
3. For production, GET `/healthz` and `/readyz` on
   `https://ospsd-team4-issue-tracker.onrender.com`. Readiness reports the commit
   and provider reachability; a 200 readiness response alone does not establish
   that issue reads work. Try a known existing **test** issue, check its body,
   and record its trace ID. A malformed ID yielding 404 is not a recovery test.
4. If a release caused the failure, use the documented
   [known-good rollback](DEPLOYMENT.md#roll-back). Verify the deployed commit
   and live conformance smoke check. If credentials/configuration or the
   provider caused it, repair that dependency through the account owner.
   A code rollback does not restore manually changed secrets or provider data.
5. Re-run the seven-day SLI report. If either budget is spent, follow the
   feature-release pause in the policy. Preserve the original incident data.

## Recovery

Restore the failing dependency or revision, then verify successful reads of
an existing issue. Check the five-minute error ratio is at most 5% with at
least 20 eligible events; keep healthy traffic flowing until the previous
failures leave the window. Confirm the rule returns to Normal **and the real
destination receives the resolved notification**. Healthy/no-data charts or a
contact-point Test message alone do not prove recovery. Record UTC start/end,
release, cause, actions, dashboard/trace links and notification timestamps.

## Disposable alert exercise

Use a local disposable controlled service that exports to the team's Grafana
stack. The normal image excludes `tests/`, so this injected fault cannot be
enabled in production. Obtain the existing OTLP endpoint/header privately
from Tyler. OTLP ingestion credentials are different from the Grafana query
token. Do not edit the Render environment group shared with production.

1. In a private terminal, export the OTLP settings described by Level 3, with
   `OTEL_ENABLED=true`, protocol `http/protobuf`, and
   `OTEL_SERVICE_NAME=ospsd-team4-level4-test`. Use a separate terminal for
   read-only report credentials. Never commit either set of secrets.
2. Generate a second alert with `--job ospsd-team4-level4-test`. Install it in
   the one-minute evaluation group. Create a matching notification policy for
   that job routed to the agreed real destination, with resolved notifications
   enabled. Announce the bounded test to the destination's members before
   starting it. Do not change the production rule's selectors or threshold.
3. Start the healthy disposable service, bound to loopback:

   ```bash
   GIT_SHA="$(git rev-parse HEAD)" uv run --locked uvicorn tests.controlled.server:app \
     --host 127.0.0.1 --port 8001
   ```

4. Run the bounded read generator below in another terminal. It creates one
   in-memory issue, reads it once a second for six minutes, and deletes it.
   Confirm successful `get_issue` metrics reach Grafana under the test job and
   the rule becomes Normal. Counters need at least two exported samples;
   allow ingestion delay. Keep each phase's output and UTC times.
5. Stop the server with Ctrl-C. Restart with the same command prefixed by
   `CONTROLLED_FAULT=get_fails`. Run the generator again. GET now returns the
   real `502 provider_error` response while create/delete still work. The new
   process starts with new issues and counters, so the generator creates a
   fresh fixture. Keep the phase running long enough for the two-minute
   pending period, evaluation, and notification grouping delay.
6. Confirm the rule fires, then confirm someone receives the actual firing
   message with service, condition, dashboard and runbook. Save sanitized
   evidence. If it does not arrive, inspect rule history, policy match,
   silences and contact-point delivery errors before changing thresholds.
7. Stop the faulted server and restart healthy, with `CONTROLLED_FAULT` unset.
   Run the generator again for six minutes (longer if ingestion is delayed).
   Confirm both Normal state and the received resolved message. Stopping
   traffic is not recovery: No Data deliberately retains the last state.
8. Run `report --job ospsd-team4-level4-test --window 30m --at <UTC-end-time>`.
   Save both numerators, denominators, current values and budgets. Inspect
   the raw dashboard series across the restarts and retain the bounded-run
   window; this is exercise evidence, separate from the production seven-day
   report. Record the code commit and whether all phases exported successfully.
9. Stop the process and remove the exercise alert/policy and any temporary
   contact point. Revoke temporary tokens and unset their environment values.
   The generated issue is deleted after each phase; stopping the server also
   discards the in-memory data. No real provider data is created by this test.

Bounded read generator (run once per phase):

```bash
uv run --locked python - <<'PY'
import time
from collections import Counter
from datetime import UTC, datetime
import httpx

with httpx.Client(base_url="http://127.0.0.1:8001", timeout=15) as client:
    created = client.post("/issues", json={"title": "Level 4 disposable alert test"})
    created.raise_for_status()
    location = created.headers["Location"]
    statuses = Counter()
    print("Started:", datetime.now(UTC).isoformat(), flush=True)
    try:
        for _ in range(360):
            response = client.get(location)
            statuses[response.status_code] += 1
            time.sleep(1)
    finally:
        cleanup = client.delete(location)
        cleanup.raise_for_status()
    print("Finished:", datetime.now(UTC).isoformat(), dict(statuses))
PY
```

If interrupted while the server stays up, the generator's `finally` attempts
cleanup. If the server is stopped first, its in-memory data is already gone.
This exercise establishes alert delivery for the controlled failure path; it
does not establish real-provider fault behavior or a production seven-day SLO.
