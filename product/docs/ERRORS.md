# Errors

This is the single reference for every error the product service can report:
startup failures, the error bodies of `POST /v1/focus-day` and
`GET /v1/issue-status`, and how each provider's failures map to them. Code
that maps errors links here instead of repeating the tables.

## Codes and HTTP statuses

The service reuses the HW1 error codes from `issue_tracker_contract.errors`
(`ErrorCode`, `STATUS_FOR_CODE`) and adds no new ones, so callers branch on
the same codes as for the HW2 service.

| Code                     | HTTP status |
| ------------------------ | ----------- |
| `invalid_input`          | 422         |
| `not_found`              | 404         |
| `access_denied`          | 403         |
| `service_not_configured` | 503         |
| `precondition_failed`    | 412         |
| `provider_error`         | 502         |
| `internal_error`         | 500         |

`idempotency_key_reused` (422) and `request_in_progress` (409) exist in the
HW1 model but the product does not use them.

## Step errors

Every failure inside an operation is a `StepError`
(`src/ospsd_team4_product/errors.py`). Only the adapters create them from
provider failures, and the operations from a state store failure
(`save_plan`, `read_plan`); nothing else from a provider or the database
reaches an operation.

| Field              | Meaning                                                                    |
| ------------------ | -------------------------------------------------------------------------- |
| `step`             | Focus-day: `validate`, `check_doc`, `list_issues`, `plan`, `create_events`, `write_standup_doc` or `save_plan`. Issue-status: `find_issue` or `read_plan` |
| `code`             | An HW1 code from the table above; it sets the HTTP status                  |
| `message`          | Safe to show callers. Always written by this service, never a provider's   |
| `retryable`        | Repeating the same request later may succeed (a transient outage)          |
| `may_have_applied` | A write was sent but its outcome is unknown, so it may have taken effect  |
| `details`          | Optional per-field problems (`ErrorDetail`), for invalid input             |
| `cause`            | A short internal reason, written to logs only, never returned              |

Provider messages are never passed on because they can contain paths, IDs,
URLs or operator hints. The one exception is the tracker: its `ServiceError`
messages already follow the HW1 rule (written by our tracker package for
callers), so they pass through unchanged.

## Error body of `POST /v1/focus-day`

Source: `src/ospsd_team4_product/models.py`, `orchestrator.py`. The body
extends the HW1 shape: `error.code`, `error.message` and `error.details` keep
their HW1 meaning, and the product adds `error.step`, `error.retryable` and
`error.may_have_applied`. The top level reports how far the operation got:

| Field         | Meaning                                                                 |
| ------------- | ----------------------------------------------------------------------- |
| `status`      | `partial` if `created` is not empty, otherwise `failed`                 |
| `steps`       | All seven steps, each `ok`, `failed` or `skipped`; the failed one has `error` |
| `plan`        | The plan, or `null` if planning did not finish                          |
| `created`     | Issue ID to event ID, for every event known to exist                    |
| `doc`         | Always `null` on failure                                                |
| `issues_truncated` | `true` if `list_issues` returned 100 issues (older ones may be missing), `false` if fewer, `null` if `list_issues` did not run (F6) |
| `failed_step` | The step that failed (same as `error.step`)                             |
| `error`       | The fields above                                                        |

Example (calendar outage on block 2 of 3, HTTP 502):

```json
{
  "status": "partial",
  "steps": [
    {"name": "validate", "status": "ok", "error": null},
    {"name": "check_doc", "status": "ok", "error": null},
    {"name": "list_issues", "status": "ok", "error": null},
    {"name": "plan", "status": "ok", "error": null},
    {"name": "create_events", "status": "failed", "error": {"...": "same as error"}},
    {"name": "write_standup_doc", "status": "skipped", "error": null}
  ],
  "plan": {"blocks": ["..."], "unscheduled": []},
  "created": {"demo-1": "fake-event-1"},
  "doc": null,
  "issues_truncated": false,
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

Rules:

- Any failed step returns the HTTP status of its code. A plan that leaves
  some issues unscheduled (`no_free_slot`) is not an error: it is 200 with
  `status: ok`.
- To retry, send the same request with `replay: {"plan": ..., "created": ...}`
  copied from the failure body. On replay `check_doc`, `list_issues` and
  `plan` are `skipped`, existing events are kept, and only missing ones are
  created.
- A run without `replay` whose doc already exists stops at `check_doc` with
  412 `precondition_failed`, before anything is booked
  ([`check_doc`](#check_doc-storage)). Sending the same request twice
  therefore never books a second set of events.
- If `error.may_have_applied` is true for `create_events`, an event may exist
  that is not in `created`. A replay cannot know about it, so check the
  calendar first; replaying anyway can create a duplicate.
- Each failure is logged as one JSON line (`event: focus_day_failed`) with
  the step, code, flags, number of created events and `cause`, never the
  message or any provider text.

## `list_issues` (tracker)

Source: `src/ospsd_team4_product/adapters/tracker.py`.

| Tracker failure                  | Code           | retryable                      | may_have_applied |
| -------------------------------- | -------------- | ------------------------------ | ---------------- |
| `ServiceError` with any code     | the same code  | yes only for `provider_error`  | no               |
| any other exception              | provider_error | no                             | no               |

A failed read changes nothing, so `may_have_applied` is always false. An
unexpected exception is logged with its traceback and reported as "The issue
tracker returned an unusable response".

Not an error: the tracker contract returns at most `MAX_LIST_LIMIT` (100)
issues, newest first. When it returns 100, older issues may be missing from
the plan (they appear in neither `blocks` nor `unscheduled`), and the
response, success or failure, has `issues_truncated: true` (F6). A list of
exactly 100 also sets it, since the contract cannot tell the two apart.

## `validate` (request input)

Source: `src/ospsd_team4_product/models.py`, `orchestrator.py`, `planner.py`,
`doc.py`.

All input is checked in the first step, `validate`, before any provider is
called, so a rejected request has no side effect. Every problem is reported
as the `validate` step failing with `invalid_input` (422), message "The
request is invalid", and one `ErrorDetail` per problem naming the field
(`busy[0].start` style). The other steps are `skipped`. Not retryable,
nothing applied.

Schema errors (the request body itself, `models.py`):

| Field                                        | Problem                                     |
| -------------------------------------------- | ------------------------------------------- |
| `body`                                       | missing or not a JSON object                |
| `assignee`, `day`                            | missing                                     |
| `day`                                        | not `YYYY-MM-DD`, or not a real date        |
| `working_window.start`, `working_window.end` | not `HH:MM` (00:00 to 23:59)                |
| `default_minutes`                            | not a whole number from 1 to 1440           |
| any unknown field                            | extra fields are rejected                   |

Planner and path rules (`PlanInputError(field, message)`):

| Field                                        | Problem                                         |
| -------------------------------------------- | ----------------------------------------------- |
| `assignee`                                   | not 1 to 64 of `a-z 0-9 . _ -` (lowercase only), or `.`/`..` |
| `timezone`                                   | not an IANA time zone name                      |
| `working_window`                             | start is not before end                         |
| `busy[i].start`, `busy[i].end`               | not RFC 3339 with whole seconds and an offset   |
| `busy[i]`                                    | start is not before end                         |
| `replay.plan.blocks[i].start`, `.end`        | not RFC 3339 with whole seconds and an offset   |
| `replay.plan.blocks[i]`                      | start is not before end                         |
| `replay.plan.blocks[i].issue_id`             | the same issue appears in two blocks            |
| `replay.created`                             | names an issue with no block in `replay.plan`   |

## `check_doc` (storage)

Source: `src/ospsd_team4_product/adapters/storage.py` (`check_doc_absent`).
Runs only on a run without `replay`, after `validate` and before any other
provider call (F5). It calls `get_metadata` on the doc path:

| Result of `get_metadata`        | Outcome                                                  |
| ------------------------------- | -------------------------------------------------------- |
| `NotFoundError`                 | No doc: the run continues                                |
| returns file info               | precondition_failed (412), not retryable, nothing applied |
| any other error                 | the storage mapping below, with `may_have_applied` always false (it is a read) |

The 412 message is "A standup doc already exists for this assignee and day;
replay the earlier response instead". To plan that day again from scratch,
remove the earlier events and the doc by hand first.

`get_metadata` cannot tell a missing doc from a missing doc folder, so a
missing `/standups` passes this check and is reported by
`write_standup_doc` (503, below) once the events exist.

What the check does not catch:

- A run that failed before writing its doc (for example the calendar failed
  on block 2 of 3, or the doc folder is missing) leaves no doc, so sending
  it again without `replay` books its events a second time. Replay instead.
- Two runs for the same assignee and day that start at the same time both
  pass the check before either writes the doc.

## `create_events` (calendar)

Source: `src/ospsd_team4_product/adapters/calendar.py`. Mapping from
calendar-contract v0.1.0 `CalendarError`:

| CalendarError                           | Code                   | retryable | may_have_applied            |
| --------------------------------------- | ---------------------- | --------- | --------------------------- |
| `INVALID_INPUT`                         | invalid_input          | no        | no                          |
| `NOT_FOUND`                             | not_found              | no        | no                          |
| `ACCESS_DENIED`                         | access_denied          | no        | no                          |
| `PROVIDER_UNAVAILABLE`, retryable=True  | provider_error         | yes       | no                          |
| `PROVIDER_UNAVAILABLE`, retryable=False | service_not_configured | no        | no                          |
| `PROVIDER_ERROR` on create              | provider_error         | as given  | yes, unless retryable=True  |
| `PROVIDER_ERROR` on get (replay)        | provider_error         | as given  | no                          |
| any other exception on create           | provider_error         | no        | yes                         |
| any other exception on get              | provider_error         | no        | no                          |
| `CreateEvent` fails validation          | internal_error         | no        | no                          |

Notes:

- `PROVIDER_UNAVAILABLE` with retryable=False is how Team 5 reports a missing,
  expired or revoked OAuth token. An operator must re-authorize, so it is a
  configuration problem, not an outage.
- An uncertain create (`PROVIDER_ERROR`, retryable=False, or any unexpected
  exception) is never retried, by the adapter or the orchestrator. Its message
  tells the caller to check the calendar before retrying.
- On replay, `get` returning `NOT_FOUND` is not an error: the event is
  recreated.
- The planner only produces valid times, so a `CreateEvent` validation
  failure is a bug in this service.

## `write_standup_doc` (storage)

Source: `src/ospsd_team4_product/adapters/storage.py`. The shared contract
(cloud-storage-api 0.3.1) has no upsert, so the doc is written as:

1. `replace`. Success: done.
2. `ConflictError` (the file changed while replacing): `replace` once more.
3. `NotFoundError`: `create`.
4. `ConflictError` from that `create` (someone created it in between):
   `replace` once more.
5. `NotFoundError` from that `create`: the doc folder (`/standups`) does not
   exist. The contract does not promise that `create` makes missing parent
   folders, so an operator creates the folder once at setup
   ([DEPLOYMENT.md](DEPLOYMENT.md#3-dropbox-team-3s-storage)), and its
   absence is `service_not_configured` (503), not retryable, nothing applied.
   Once the folder exists, a replay writes the doc.

Anything else fails the step with this mapping. The same plan always gives
the same bytes, so repeating the whole write is safe.

| Storage error                   | Code                | retryable | may_have_applied |
| ------------------------------- | ------------------- | --------- | ---------------- |
| `InvalidRequestError`           | invalid_input       | no        | no               |
| `PermissionDeniedError`         | access_denied       | no        | no               |
| `NotFoundError` from `create`   | service_not_configured | no     | no               |
| `NotFoundError` (otherwise)     | not_found           | no        | no               |
| `ConflictError`                 | precondition_failed | no        | no               |
| `FileTooLargeError`             | invalid_input       | no        | no               |
| `InvalidOperationError`         | invalid_input       | no        | no               |
| `ProviderUnavailableError`      | provider_error      | yes       | yes              |
| `ProviderError`                 | provider_error      | yes       | yes              |
| other `StorageError` / anything | provider_error      | yes       | yes              |

Every provider failure whose outcome is unknown is retryable, even though
it may have applied: rewriting the doc gives the same bytes, so repeating
it is safe. This differs from the calendar, where an uncertain create is not
retryable because repeating it can book a duplicate event.

Unexpected exceptions are logged by class name only.

## `save_plan` (state store)

Source: `src/ospsd_team4_product/orchestrator.py`, `store.py`. The step runs
only after every other step succeeded, and saves the plan with each block's
event ID as the assignee's approved plan for the day. A failed earlier step
skips it, so a `partial` or `failed` run never saves anything.

The save is one database transaction that replaces the day's plan, so a
failure leaves nothing saved and repeating it is safe. Any `StoreError`
(the database is down, unreachable, or rejected the write) fails the step
with:

| Store error  | Code             | HTTP | retryable | may_have_applied |
| ------------ | ---------------- | ---- | --------- | ---------------- |
| `StoreError` | `internal_error` | 500  | yes       | no               |

Message: `Your calendar blocks and standup doc are done, but the plan could
not be saved. Retry with replay to save it.` By then every event exists and
the doc is written, so `status` is `partial` (or `failed` when the plan has
no blocks). A replay with the response's `plan` and `created` confirms the
events with `get`, creates none, rewrites the doc with the same bytes, and
saves the plan. The log line's `cause` is the database error's class name;
the database's own message, which can name hosts or users, is never
logged or returned.

## `GET /v1/issue-status`

Source: `src/ospsd_team4_product/issue_status.py`, `models.py`, `main.py`.
`ambiguous` and `no_approved_plan` are 200 answers, not errors. A failed step
returns the HTTP status of its code and this body; `error` has the same fields
as focus-day's:

```json
{
  "status": "failed",
  "failed_step": "find_issue",
  "error": {
    "code": "not_found",
    "message": "No issue matches",
    "details": null,
    "step": "find_issue",
    "retryable": false,
    "may_have_applied": false
  }
}
```

The endpoint writes nothing, so `may_have_applied` is always false and a
request can always be repeated. Each failure is logged as one JSON line
(`event: issue_status_failed`) with the step, code, HTTP status, flags and
`cause`, never the message, the query or any provider text. As for
`list_issues`, an unexpected tracker exception also logs its traceback.

### `find_issue` (query and tracker)

| Failure                                       | Code            | HTTP            | retryable                     |
| --------------------------------------------- | --------------- | --------------- | ----------------------------- |
| invalid query parameters (below)              | `invalid_input` | 422             | no                            |
| no issue has this `issue_id` or matches `title` | `not_found`   | 404             | no                            |
| tracker `ServiceError` with any code          | the same code   | that code's     | yes only for `provider_error` |
| any other tracker exception                   | `provider_error` | 502            | no                            |

The last two rows are the [`list_issues` table](#list_issues-tracker), since
the step reads the same list. Its message for no match is "No issue matches". When the tracker list was
truncated at 100 issues, it is "No issue matches among the newest 100 issues;
older issues are not searched" (cause `no_match_truncated`).

Query parameters are checked before the tracker is called. The message is
"The request is invalid", with one `ErrorDetail` per problem:

| Field               | Problem                                                                 |
| ------------------- | ----------------------------------------------------------------------- |
| `issue_id`, `title` | neither or both are given (one detail for each), or the one given is blank |
| `day`               | not `YYYY-MM-DD`, or not a real date                                    |
| any unknown parameter | extra parameters are rejected                                         |

### `read_plan` (state store)

| Store error  | Code             | HTTP | retryable | may_have_applied |
| ------------ | ---------------- | ---- | --------- | ---------------- |
| `StoreError` | `internal_error` | 500  | yes       | no               |

Message: `The saved plans could not be read. Try again later.` The log line's
`cause` is the database error's class name, as for `save_plan`.

## Startup errors

Configuration is checked once at startup (`config.py`, `factories.py`). A
problem raises `ConfigError`, logs `startup_config_invalid`, and the process
exits before serving requests. Messages name the variable, never its value.

| Message                                                           | Cause                                                        |
| ----------------------------------------------------------------- | ------------------------------------------------------------ |
| `Missing required configuration: <VAR>`                           | a `*_PROVIDER` variable, or `CALENDAR_TOKEN_FILE` when the calendar factory takes `token_file`, is unset |
| `<VAR> must have the form module:attr`                            | malformed provider path                                      |
| `<VAR> names a module that cannot be imported`                    | the module is not installed or fails to import               |
| `<VAR> names an attribute that does not exist`                    | wrong attribute in the provider path                         |
| `<VAR> must name a callable`                                      | the attribute is not a factory                               |
| `CALENDAR_TOKEN_FILE must be an absolute path`                    | relative token path                                          |
| `CALENDAR_TOKEN_FILE does not name a readable file`               | the secret file is not mounted or not readable               |
| `DEFAULT_WORKING_WINDOW must have the form HH:MM-HH:MM with start before end` | bad default window                               |
| `DEFAULT_MINUTES must be a whole number from 1 to 1440`           | bad default block length                                     |
| `DEFAULT_TIMEZONE must be an IANA time zone name`                 | bad default zone                                             |
| `<VAR> could not be built; check that provider's configuration`   | the factory raised, e.g. Team 3's `DropboxAuthError` when the Dropbox credentials are missing |
| `TRACKER_PROVIDER must build an IssueProvider`                    | the factory returned the wrong type                          |
| `CALENDAR_PROVIDER must build a CalendarService`                  | the factory returned the wrong type                          |
| `STORAGE_PROVIDER must build a DocumentStorage`                   | the factory returned the wrong type                          |
| `Missing required configuration: STATE_DB_URL`                    | `STATE_DB_URL` is unset or empty                             |
| `STATE_DB_URL must be a postgresql:// URL (or memory:// for the in-memory store)` | the value is not a `postgresql://` or `postgres://` URL with a host, nor exactly `memory://` |
| `Cannot connect to state database; check STATE_DB_URL`            | the database could not be reached within 5 seconds, rejected the credentials, or the tables could not be created |

Because providers are built at startup, a missing Dropbox credential stops
the service instead of failing each request with `service_not_configured`.
Likewise the service connects to the state database (and creates any
missing tables) before serving, so a wrong or unreachable `STATE_DB_URL`
stops startup. If the database goes down later, requests fail only at the
`save_plan` and `read_plan` steps, and the pool reconnects once it is back.

## Other HTTP errors

These are outside the operation, so they use the plain HW1 shape
(`{"error": {"code", "message"}}`) with no step:

| Case                         | Status | Code             | Message                 |
| ---------------------------- | ------ | ---------------- | ----------------------- |
| unknown path                 | 404    | `not_found`      | Resource not found      |
| wrong method                 | 405    | `invalid_input`  | Request not supported   |
| a bug (unexpected exception) | 500    | `internal_error` | Internal server error   |

A bug is logged with its traceback (`event: unexpected_error`). Provider
exceptions never reach this handler: the adapters translate all of them.
