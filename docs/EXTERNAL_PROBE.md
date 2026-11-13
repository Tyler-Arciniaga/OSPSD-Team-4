# External synthetic probe (optional extension)

## Contract and limits

Every six hours (00:07, 06:07, 12:07 and 18:07 UTC), an opted-in GitHub Actions
runner makes one GET to `/issues/{id}` for a dedicated, non-sensitive test issue.
Success means HTTP 200 and a JSON object with exactly the expected ID and title
values. Other fields are permitted. Redirects are not followed. No retries,
writes, provider credentials or new dependencies are used. Socket timeout is
15 seconds; the workflow has a three-minute job limit. A trickling response
can exceed a socket timeout; the job limit is the outer bound. Bodies above
64 KiB are rejected. Request durations are client-observed, not server timings.

External availability SLI = successful recorded attempts / all recorded attempts.
HTTP errors (including 404), wrong bodies, DNS/TLS/network errors and timeouts
are failures. Invalid configuration fails the workflow without claiming a
service measurement. Missing schedules, cancelled jobs and runner/setup errors
are coverage gaps, not successful requests. Empty evidence means No data.
A fixture deletion/rename counts as a failed consumer check: investigate and
report it, do not remove it from the denominator. Change the fixture only between
observation windows. The report rejects mixed targets and deduplicates samples.

This is a separate sampled SLI; it does not replace Level 4's server-side SLI,
SLO or alert. One region, one issue and low-frequency reads do not establish
whole-service reliability. Provider reads may be cached. With six-hour intervals, a free hosting instance may sleep between checks.
A cold start can exceed the 15-second timeout and counts as a failed consumer
check. Four checks per day can miss outages between samples; record this limit.

## Enable after review

1. Copy these additions onto a feature branch in your actual git checkout and
   run the repository's normal checks. Review and merge using AGENTS.md.
2. Create one disposable issue through your normal test workflow, with a stable
   title such as `External probe fixture`. Keep it until the observation ends.
   Do not use a conformance fixture: those are automatically deleted.
3. In GitHub repository Settings > Secrets and variables > Actions > Variables,
   set `PROBE_BASE_URL` to the deployed base URL (HTTPS, no trailing API route),
   `PROBE_ISSUE_ID` to the returned ID, and `PROBE_EXPECTED_TITLE` to its exact title.
   These are non-sensitive fixture values. Set `EXTERNAL_PROBE_ENABLED` to `true`.
4. After the workflow is on the default branch, open Actions > External synthetic
   probe > Run workflow. Inspect its summary and JSON artifact. A failed probe
   deliberately fails the run, but its measurement is still uploaded.
5. Allow scheduled runs to collect evidence. Scheduling is best-effort and can
   be delayed or dropped; report actual timestamps and gaps. Do not describe
   manual test runs as scheduled evidence.

Local check (uses the same environment variables):

```bash
python3 -m scripts.external_probe check
```

The workflow is read-only (`contents: read`), separate from the release workflow,
and does not change the deployment pipeline or app behavior. Artifacts are
retained for 30 days. The per-run summary is just that run's SLI (one attempt),
not the aggregate window. An uploaded artifact is GitHub storage, not a Grafana
metric; this integration adds no Grafana panel or new notification route.

## Aggregate and submit real evidence

Download **every** probe artifact for the chosen window, including failed runs.
Extract each into its own directory under `probe-results/` (otherwise identically
named `sample.json` files overwrite each other). Example layout:
`probe-results/run-1/sample.json`, `probe-results/run-2/sample.json`.

```bash
python3 -m scripts.external_probe report probe-results --event schedule > docs/evidence/external-probe.md
```

Use a fresh directory for each window. Do not cherry-pick successful runs.
The report lists actual sample times, numerator, denominator and ratio. Append:

- workflow/run links and intended observation start/end times;
- scheduled coverage and any gaps or missing artifacts;
- target identity in non-sensitive terms, deployment revision(s), and fixture setup;
- server-side good/eligible counts and ratio from Grafana over the **same absolute
  window**, using the Level 4 queries and production job; link/screenshot evidence;
- an explanation of observed agreement/disagreement, and limitations.

A short pre-submission sample is valid evidence of the mechanism, not a seven-day
SLO measurement. Do not label this extension demonstrated until scheduled results
and the comparison have actually been recorded. The included evidence document
is a pending checklist, not fabricated production evidence.

## Why external and server SLIs can disagree

| Case | External check | Level 4 server SLI |
| --- | --- | --- |
| DNS, TLS or network failure before middleware | Failure | No corresponding request recorded |
| HTTP 200 with wrong ID/title | Failure | May count as success |
| Fixture deleted; GET returns 404 | Failure | `not_found` excluded |
| Slow network/response download | Adds client delay; possibly timeout | Server timing excludes some of this delay |
| Different traffic patterns | One fixture every six hours | All eligible reads, including probe requests |
| Missing scheduled run | Coverage gap, no sample | Other traffic may still be measured |

## Verification and cleanup

Run `python3 -m unittest tests.test_external_probe` for isolated regression checks.
The normal CI pytest discovery also runs these tests. They cover valid reads,
wrong/malformed/oversized bodies, HTTP/transport failures, redirects, invalid
configuration, duplicate samples, zero data and mixed-target rejection.

Set `EXTERNAL_PROBE_ENABLED=false` when the exercise ends, then delete only the
fixture you created. Retain sanitized evidence before artifacts expire. No
production failure injection or provider mutation is required by the probe.

GitHub references:
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/tutorials/store-and-share-data
