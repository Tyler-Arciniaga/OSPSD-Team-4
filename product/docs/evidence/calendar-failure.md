# Controlled calendar failure (real tracker and storage, local)

Status: PASSED on 2026-11-17 (run locally on Tyler's laptop).
The calendar vertical was replaced, by configuration only, with a controlled
implementation that goes down on its second `create`. Through the running
service, `POST /v1/focus-day` returned the error documented in
[ERRORS.md](../ERRORS.md#error-body-of-post-v1focus-day), naming
`create_events` as the failed step. The documented recovery (a replay) then
completed the operation, creating only the missing events.

## Setup

| | |
| --- | --- |
| Product commit | `d18a4150569cfafe7f6d3d78523b607448194d54` plus the uncommitted controlled calendar below, as reported by `/readyz` |
| Tracker | `team_4_issue_tracker_trello:TrelloIssueProvider`, the real `HW3-Product` Trello list (as in [focus-day-live.md](focus-day-live.md)) |
| Calendar | **`ospsd_team4_product.fakes:calendar_outage_on_second_create`**, the controlled implementation |
| Storage | `dropbox_client_impl:DropboxDocumentStorage`, Team 3's package against the real Dropbox test app (the deployed choice) |
| How it ran | `uv run uvicorn ospsd_team4_product.main:app --port 8000` from `product/` under `env -i`, credentials loaded from `~/.config/ospsd-team4/product.env` (outside the repository) |

Compared with the deployed configuration, only `CALENDAR_PROVIDER`
changed, and `CALENDAR_TOKEN_FILE` was dropped: the controlled factory takes
no `token_file`, so the service does not require it
([config.py](../../src/ospsd_team4_product/config.py), `takes_token_file`).

```sh
env -i PATH="$PATH" HOME="$HOME" GIT_SHA="$(git rev-parse HEAD)" \
  TRELLO_API_KEY=... TRELLO_TOKEN=... TRELLO_LIST_ID=... \
  DROPBOX_APP_KEY=... DROPBOX_APP_SECRET=... DROPBOX_REFRESH_TOKEN=... \
  TRACKER_PROVIDER=team_4_issue_tracker_trello:TrelloIssueProvider \
  CALENDAR_PROVIDER=ospsd_team4_product.fakes:calendar_outage_on_second_create \
  STORAGE_PROVIDER=dropbox_client_impl:DropboxDocumentStorage \
  uv run uvicorn ospsd_team4_product.main:app --port 8000
```

```
$ curl -s localhost:8000/readyz
{"status":"ready","checks":{"config":"ok"},"commit":"d18a4150569cfafe7f6d3d78523b607448194d54"}
```

### The controlled implementation

`calendar_outage_on_second_create` (`src/ospsd_team4_product/fakes/calendar.py`)
returns the in-memory calendar fake, which passes the shared
`CalendarServiceConformance` suite, with one injected fault. The **second**
`create` raises the contract's own error,
`CalendarError(PROVIDER_UNAVAILABLE, retryable=True)`: an outage, as a real
calendar package would report it. Every other call succeeds, so the
outage has passed by the time the caller retries. It is the scenario
ERRORS.md uses as its example ("calendar outage on block 2 of 3").

The service does not know that this calendar is controlled. It is built by
the same factory code as Team 5's package and reaches the orchestrator only
through `CalendarAdapter`.
`tests/test_factories.py::test_controlled_calendar_outage_is_selected_by_configuration`
checks that the value resolves and fails only on the second create.

## Request

The live-run request with a different `assignee`, so the doc goes to its
own path, `/standups/2026-11-20/tyler-failure.md`, and leaves the swap run's
doc alone:

```json
{"assignee": "tyler-failure", "day": "2026-11-20", "timezone": "America/New_York",
 "working_window": {"start": "09:00", "end": "17:00"},
 "busy": [{"start": "2026-11-20T12:00:00-05:00", "end": "2026-11-20T13:00:00-05:00"}]}
```

## 1. The failing run

`HTTP/1.1 502 Bad Gateway`, full body:

```json
{
  "status": "partial",
  "steps": [
    {"name": "list_issues", "status": "ok", "error": null},
    {"name": "plan", "status": "ok", "error": null},
    {"name": "create_events", "status": "failed", "error": {
      "code": "provider_error",
      "message": "The calendar is currently unavailable",
      "details": null,
      "step": "create_events",
      "retryable": true,
      "may_have_applied": false
    }},
    {"name": "write_standup_doc", "status": "skipped", "error": null}
  ],
  "plan": {
    "blocks": [
      {"issue_id": "6afbc544721ea7f145ad1445", "title": "fix auth bug",
       "start": "2026-11-20T09:00:00-05:00", "end": "2026-11-20T10:30:00-05:00"},
      {"issue_id": "6afbc54ac1bb3fbab0aa7860", "title": "update deployment docs",
       "start": "2026-11-20T10:30:00-05:00", "end": "2026-11-20T12:00:00-05:00"},
      {"issue_id": "6afbc55430fecf9043bc30e4", "title": "fix flaky idempotency test",
       "start": "2026-11-20T13:00:00-05:00", "end": "2026-11-20T14:30:00-05:00"}
    ],
    "unscheduled": []
  },
  "created": {"6afbc544721ea7f145ad1445": "fake-event-1"},
  "doc": null,
  "failed_step": "create_events",
  "error": {
    "code": "provider_error",
    "message": "The calendar is currently unavailable",
    "details": null,
    "step": "create_events",
    "retryable": true,
    "may_have_applied": false
  }
}
```

The response compared with what ERRORS.md documents:

| Documented | Observed |
| --- | --- |
| A failed step returns its code's HTTP status; `provider_error` is 502 ([Codes and HTTP statuses](../ERRORS.md#codes-and-http-statuses)) | 502 |
| `status` is `partial` if `created` is not empty | `partial`; `created` holds block 1's event |
| `failed_step` and `error.step` name the step that failed | both `create_events`; that step is `failed`, the steps before it `ok`, the one after it `skipped` |
| `PROVIDER_UNAVAILABLE`, retryable=True maps to `provider_error`, retryable yes, may_have_applied no ([create_events (calendar)](../ERRORS.md#create_events-calendar)) | `provider_error`, `retryable: true`, `may_have_applied: false` |
| The message is the service's own, never the provider's | "The calendar is currently unavailable". The injected text, `Controlled outage (injected)`, appears in neither the response nor the log (`grep -c` gives 0 for both) |
| `doc` is always `null` on failure | `null` |
| The plan is returned so the caller can replay | the full plan, the same three blocks as the live run |

The one structured log line for the failure (no message and no provider
text, per ERRORS.md):

```json
{"event": "focus_day_failed", "status": "partial", "http_status": 502, "step": "create_events", "code": "provider_error", "retryable": true, "may_have_applied": false, "created_events": 1, "cause": "calendar_provider_unavailable"}
```

### Checked directly in the providers

- **Dropbox:** `get_metadata("/standups/2026-11-20/tyler-failure.md")`
  raised `NotFoundError`. Because the doc step was skipped, nothing was
  written to real storage.
- **Trello:** read only; nothing to change.
- **Calendar:** the controlled implementation is in-process; its state is
  what the replay below reads.

## 2. Recovery: replay with the failure body

ERRORS.md says to retry by sending the same request with
`replay: {"plan": ..., "created": ...}` copied from the failure body.
Same running process, so the outage has passed:
`jq -s '.[0] + {replay: {plan: .[1].plan, created: .[1].created}}' req.json run1.json`.

`HTTP/1.1 200 OK`:

```json
{
  "status": "ok",
  "steps": [
    {"name": "list_issues", "status": "skipped", "error": null},
    {"name": "plan", "status": "skipped", "error": null},
    {"name": "create_events", "status": "ok", "error": null},
    {"name": "write_standup_doc", "status": "ok", "error": null}
  ],
  "created": {
    "6afbc544721ea7f145ad1445": "fake-event-1",
    "6afbc54ac1bb3fbab0aa7860": "fake-event-2",
    "6afbc55430fecf9043bc30e4": "fake-event-3"
  },
  "doc": {"path": "/standups/2026-11-20/tyler-failure.md", "id": "id:oinkehfsr60AAAAAAAAAFQ"},
  "failed_step": null,
  "error": null
}
```

- The tracker was not read again and the plan was not recomputed.
- Block 1 kept `fake-event-1`: it was found with `get` and not created
  again. Only blocks 2 and 3 were created, so the failure left no
  duplicate.
- The doc now exists in Dropbox, read back with Team 3's package:
  `id:oinkehfsr60AAAAAAAAAFQ`, 296 bytes, listing all three blocks:

```markdown
# Standup: tyler-failure, 2026-11-20

Times are in America/New_York.

## Focus blocks

- 09:00-10:30 `6afbc544721ea7f145ad1445` fix auth bug
- 10:30-12:00 `6afbc54ac1bb3fbab0aa7860` update deployment docs
- 13:00-14:30 `6afbc55430fecf9043bc30e4` fix flaky idempotency test

## Unscheduled

None.
```

## Not covered by this run

- Other calendar failure modes (an expired token, which gives
  `service_not_configured`, and an uncertain create, which gives
  `may_have_applied: true`). These, and the tracker and storage failures,
  are covered with the fakes in `tests/test_focus_day.py`.
- The deployed service on Render, which keeps Team 5's calendar.

## Cleanup

Nothing was booked on the real calendar. The doc at
`/standups/2026-11-20/tyler-failure.md` can be deleted in Dropbox, or left;
a later run for the same day and assignee overwrites it.
