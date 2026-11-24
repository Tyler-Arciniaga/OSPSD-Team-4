# Focus Day Product Service: Technical Design

#### Team 4

#### Tyler, Chan, Turki, Vanchi, Joseph

#### Reviewers: Team 3 (ashik-ece)

#### Status: Implemented (HW3 Milestone 1); review follow-ups F1 to F6 implemented, F7 planned ([Follow-up work](#follow-up-work-from-design-review))

#### Last Update Date: 11/23/2026

#### Relevant Links

- Errors, in full: [ERRORS.md](ERRORS.md)
- Deployment and the human-only setup: [DEPLOYMENT.md](DEPLOYMENT.md)
- Evidence:
  - [Partner package installs](evidence/partner-installs.md)
  - [Dropbox live round trip](evidence/dropbox-roundtrip.md)
  - [Live focus-day run with all three real providers](evidence/focus-day-live.md)
- Contracts consumed:
  - [`issue-tracker-contract-v1.1.0`](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/tree/issue-tracker-contract-v1.1.0/contract) (ours)
  - [`calendar-contract` `v0.1.0`](https://github.com/Airbus-A330/OSPSD-Calendar/tree/v0.1.0)
  - [`cloud-storage-api` `0.3.1`](https://github.com/Dayana20/ospsd26-cloud-storage/tree/0.3.1/src/cloud_storage_api)

---

## Overview

`ospsd-team4-product` is a separate service, in the same repository as our
HW2 issue service, with two operations. `GET /v1/issue-status` answers a
question about one issue ([Issue status](#issue-status)).
`POST /v1/focus-day`, given an assignee, a day and the times they are busy:

1. checks that no standup doc exists yet for that assignee and day, so the
   same request sent twice does not book the day twice (F5),
2. reads the issues in our tracker (our Trello implementation),
3. plans one focus block per issue in the available free time,
4. books each block in a calendar (i.e. Team 5's Google Calendar implementation),
5. writes a standup doc listing the plan to cloud storage (i.e. Team 3's Dropbox
   implementation),
6. saves the booked plan as the assignee's approved plan for the day, in a
   Postgres state store shared by every service instance
   ([State store](#state-store)),
7. returns the status of every step.

Currently, the steps are fixed and there is no LLM. Each implementation is reached only
through its shared contract package and is chosen by configuration
(`TRACKER_PROVIDER`, `CALENDAR_PROVIDER`, `STORAGE_PROVIDER`, each a
`module:attr` import path). In-memory fakes are selected through the same
variables for tests and the disposable environment. The state database is
chosen by `STATE_DB_URL`.

**The service cannot see the real calendar.** The calendar contract has
create, get, replace and delete, but no way to list or search events. The
service never reads anyone's schedule: it trusts the `busy` intervals the
caller sends, and conflict avoidance is only as good as that list.

## 4.1 Architecture and one request trace

### Components

| Component | Responsibility |
| --- | --- |
| HTTP layer | Startup (lifespan), request schema, `/healthz` and `/readyz`, JSON responses, the HW1-shaped handlers for `404`, `405` and `500`, one log line per failed request |
| Orchestrator | Validates all input first, runs the steps in order, handles replay, builds the response from what happened |
| Planner | Pure function: inputs are issues, day, window, and busy intervals ; outputs are event blocks and unscheduled issues |
| Standup doc | Pure: the doc path from validated input and the doc bytes from the plan |
| Adapters | The only code that knows contract exceptions. Turns each provider failure into a HW1 compatible `StepError` naming its step |
| Configuration | Reads the environment once, resolves the `module:attr` paths, applies any defaults |
| Factories | The only code that calls a provider constructor, and the only place provider-specific arguments are supplied (i.e. Team 5's `token_file`) |
| State store | `store.py`: approved plans, remaining work and the completion log in a Postgres database shared by every instance. The only code that knows the database ([State store](#state-store)) |
| Fakes | In-memory tracker, calendar, storage and state store with failure injection, for tests and the disposable service |

### Diagram

```
Caller
  |  POST /v1/focus-day  {assignee, day, timezone, working_window, busy, default_minutes, replay}
  v
main.py  (schema validation, HW1-shaped errors, failure log line)
  |
  v
orchestrator.FocusDay.run
  |  0. validate everything (planner rules, doc path, replay plan)
  |
  |-- 0b. check_doc ---------> StorageAdapter  --> DocumentStorage.get_metadata  (not on replay; 412 if the doc exists)
  |-- 1. list_issues --------> TrackerAdapter  --> IssueProvider    (issue-tracker-contract v1.1.0)
  |                                                 '-> team_4_issue_tracker_trello  -> Trello REST
  |-- 2. plan ---------------> planner.plan_day    (pure function, no I/O)
  |-- 3. create_events ------> CalendarAdapter --> CalendarService  (calendar-contract v0.1.0)
  |                                                 '-> team5_calendar  -> Google Calendar, primary calendar
  |-- 4. write_standup_doc --> StorageAdapter  --> DocumentStorage  (cloud-storage-api 0.3.1)
  |                                                 '-> dropbox_client_impl  -> Dropbox
  |-- 5. save_plan ----------> StateStore      --> PostgresStateStore -> Postgres (shared by all instances)
  v
6. response: status, steps (7 in total, validate first), plan, created, doc, issues_truncated  (or the error body with failed_step details)

Startup (lifespan): config.py resolves *_PROVIDER paths and checks STATE_DB_URL -> factories.py
builds the three providers once and connects to the state database (creating its tables) -> any
problem exits before serving, naming the variable (ERRORS.md, Startup errors)
```

### Request trace

A real run on 2026-11-16, with the three real implementations (product
commit `ae94c6a`). Full output and screenshots are in
[evidence/focus-day-live.md](evidence/focus-day-live.md). The run predates
the `save_plan` step ([#85](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/issues/85)),
so it shows four steps; a run today ends with a fifth,
`{"name": "save_plan", "status": "ok", "error": null}`, after which
the three blocks and their event IDs are tyler's approved plan for
2026-11-19.

**Request**

```json
{"assignee": "tyler", "day": "2026-11-19", "timezone": "America/New_York",
 "working_window": {"start": "09:00", "end": "17:00"},
 "busy": [{"start": "2026-11-19T12:00:00-05:00", "end": "2026-11-19T13:00:00-05:00"}]}
```

**0. Validate (before any provider call).** The body matches the schema
(`models.FocusDayRequest`, extra fields rejected). `default_minutes` is
absent, so the configured default, 90, applies. The planner checks its
inputs with no issues: `America/New_York` is a valid zone, 09:00 is before
17:00, and the busy interval is RFC 3339 with whole seconds, an offset, and
start before end. The assignee `tyler` is lowercase, as required (F1). The
doc path is built now, `/standups/2026-11-19/tyler.md`, from the validated
`day` and `assignee`. There is no `replay`.

**0b. `check_doc` (since F5, not in this recording).** Because there is no
`replay`, `StorageAdapter.check_doc_absent` calls `get_metadata` on the doc
path. It raises `NotFoundError` (no doc for this assignee and day yet), so
the run continues. Had the doc existed, the run would stop here with 412
`precondition_failed` and book nothing.

**1. `list_issues`.** `TrackerAdapter.list_issues` calls
`IssueProvider.list_issues(limit=MAX_LIST_LIMIT)` on
`TrelloIssueProvider`, which reads the list named by `TRELLO_LIST_ID` (our
`HW3-Product` list). It returns three issues, reduced to `id` and `title`. Three is below the
contract's maximum of 100, so (since F6) `issues_truncated` is `false`:

| id | title |
| --- | --- |
| `6afbc544721ea7f145ad1445` | fix auth bug |
| `6afbc54ac1bb3fbab0aa7860` | update deployment docs |
| `6afbc55430fecf9043bc30e4` | fix flaky idempotency test |

**2. `plan`.** `plan_day` clips the busy interval to the window, which
leaves two free gaps, 09:00-12:00 and 13:00-17:00. Issues are ordered by ID.
Each gets the earliest gap that fits 90 minutes; blocks are never split.

| issue | start | end |
| --- | --- | --- |
| `6afbc544...` | `2026-11-19T09:00:00-05:00` | `2026-11-19T10:30:00-05:00` |
| `6afbc54a...` | `2026-11-19T10:30:00-05:00` | `2026-11-19T12:00:00-05:00` |
| `6afbc554...` | `2026-11-19T13:00:00-05:00` | `2026-11-19T14:30:00-05:00` |

**3. `create_events`.** For each block, in order,
`CalendarAdapter.create_block` sends `CreateEvent(title="Focus: fix auth bug",
start=..., end=...)` to Calendar provider's `create`, which inserts the event into the
test account's primary calendar. The result is the response's
`created` map, from each tracker issue ID (the Trello card) to the calendar
event ID booked for it:

```json
{"6afbc544721ea7f145ad1445": "cnrnufjai4cf8av9rip72uk3sk",
 "6afbc54ac1bb3fbab0aa7860": "5b5a1mlvvijdrbu3652cv707q4",
 "6afbc55430fecf9043bc30e4": "jrvuc224n5m4hcejog2uva9egg"}
```

**4. `write_standup_doc`.** `render_doc` builds the Markdown from the plan
(not from the calendar), and `StorageAdapter.write_doc` upserts it: it
tries `replace` first, and when the file does not exist yet (`NotFoundError`)
it calls `create`. On this run Dropbox also created the missing
`/standups/2026-11-19` folder, but the storage contract does not promise
that, so the design no longer relies on it: since F2 the path is flat
(`/standups/2026-11-19-tyler.md`), the operator creates `/standups` once
during setup, and a missing folder is reported as an error. Result: path
`/standups/2026-11-19/tyler.md`, id `id:oinkehfsr60AAAAAAAAADA`.

```markdown
# Standup: tyler, 2026-11-19

Times are in America/New_York.

## Focus blocks

- 09:00-10:30 `6afbc544721ea7f145ad1445` fix auth bug
- 10:30-12:00 `6afbc54ac1bb3fbab0aa7860` update deployment docs
- 13:00-14:30 `6afbc55430fecf9043bc30e4` fix flaky idempotency test

## Unscheduled

None.
```

**5. Response, 200.**

```json
{
  "status": "ok",
  "steps": [
    {"name": "list_issues", "status": "ok", "error": null},
    {"name": "plan", "status": "ok", "error": null},
    {"name": "create_events", "status": "ok", "error": null},
    {"name": "write_standup_doc", "status": "ok", "error": null}
  ],
  "plan": {
    "blocks": [
      {"issue_id": "6afbc544721ea7f145ad1445", "title": "fix auth bug",
       "start": "2026-11-19T09:00:00-05:00", "end": "2026-11-19T10:30:00-05:00"},
      {"issue_id": "6afbc54ac1bb3fbab0aa7860", "title": "update deployment docs",
       "start": "2026-11-19T10:30:00-05:00", "end": "2026-11-19T12:00:00-05:00"},
      {"issue_id": "6afbc55430fecf9043bc30e4", "title": "fix flaky idempotency test",
       "start": "2026-11-19T13:00:00-05:00", "end": "2026-11-19T14:30:00-05:00"}
    ],
    "unscheduled": []
  },
  "created": {
    "6afbc544721ea7f145ad1445": "cnrnufjai4cf8av9rip72uk3sk",
    "6afbc54ac1bb3fbab0aa7860": "5b5a1mlvvijdrbu3652cv707q4",
    "6afbc55430fecf9043bc30e4": "jrvuc224n5m4hcejog2uva9egg"
  },
  "doc": {"path": "/standups/2026-11-19/tyler.md", "id": "id:oinkehfsr60AAAAAAAAADA"},
  "failed_step": null
}
```

- `status` is `ok` only when every step finished. A failure returns the
  error body instead, with `partial` or `failed` (4.3).
- `steps` always lists every step in order. Each is `ok`, `failed` or
  `skipped`, and `error` is set only on the step that failed. On a replay,
  `check_doc`, `list_issues` and `plan` are `skipped`. This run, recorded
  before F3, has four steps. Since F3 `validate` comes first (`ok` on
  success and on replay, `failed` for invalid input), since F5 `check_doc`
  follows it, and since #85 `save_plan` comes last, for seven steps.
- `issues_truncated` (since F6, not in this recording) is `true` when
  `list_issues` returned the contract maximum of 100, so older issues may
  be missing from the plan; `false` when it returned fewer; `null` when it
  did not run (replay, or a failure before it). It is on the error body
  too.
- `plan.blocks` keeps each issue's title so a replay can recreate an event
  and rewrite the doc without reading the tracker again. `unscheduled` lists
  issues that did not fit, as `{"issue_id", "reason": "no_free_slot"}`.
- `created` is the issue-to-event map from step 3. Together with `plan`, it
  is what the caller sends back as `replay` to retry or resume.
- `doc.id` is the storage provider's own ID for the file (Dropbox's here),
  returned as is.
- `failed_step` is always `null` on success. It is there so success and
  failure bodies have the same top-level fields.

## 4.2 Decisions

Contract quotes are from `calendar-contract` `v0.1.0`
(`calendar_contract/service.py`).

### Caller-held state

- **Constraint:** "creation is not idempotent" and event IDs are
  "provider-assigned"; `CreateEvent` takes no ID or idempotency key. Knowing
  whether a block is booked means remembering the ID `create` returned.
- **Approach:** the service stores nothing about a run in progress. Every
  response carries `plan` and `created`; the caller sends them back as
  `replay` during retries. (The [state store](#state-store) keeps only
  plans whose every step succeeded, for the progress and status scenarios;
  replay never reads it.)
- **Alternative:** store each run server-side under an `Idempotency-Key`, as
  our HW2 service does for `POST /issues`.
- **Another alternative (from Team 3's review):** keep a run record in the
  storage provider we already call, such as
  `/standups/<day>/<assignee>.run.json`, written before the first `create`
  and updated after each one. Still no database, and a caller that lost the
  response could replay from `{day, assignee}` alone. The cost is an extra
  write per event, and a storage outage could then stop booking.
- **Tradeoff:** a caller that loses the response must check the calendar by
  hand. In return: no database, cleanup or key-reuse rules. Sending the same
  request again without `replay` is caught by the doc check (F5), as long
  as the first run got as far as writing its doc.
- **Evidence:** the [live run](evidence/focus-day-live.md) replayed twice
  against Google Calendar: nothing duplicated, and only a deleted event was
  recreated.
- **Revisit when:** callers cannot keep the response, operators need run
  history, or the contract accepts client-chosen event IDs.

### Replay, not recompute

- **Constraint:** the service cannot see the calendar, so it cannot tell its
  own blocks from the caller's meetings. A retry must not move or duplicate
  booked blocks.
- **Approach:** replay skips `list_issues` and `plan` and reuses the
  returned plan. `get` checks each recorded event ("missing events use
  NOT_FOUND"); only missing issue-events are recreated.
- **Alternative:** re-plan, then reconcile with `replace` and `delete`.
- **Tradeoff:** a retry keeps a possibly stale plan (new issues are not
  scheduled, closed ones stay booked), but never moves, deletes, or duplicates an event.
- **Evidence:** a planner experiment on the live run's input. With the three
  booked blocks added to `busy`, re-planning gave the first issue a second
  block at 14:30 and left the other two unscheduled.
- **Revisit when:** retries must pick up tracker changes, or the contract
  can list events.

### Caller-supplied `busy`

- **Constraint:** the contract has only `create`, `get`, `replace` and
  `delete`, and `get` needs a known ID. There is no list or free/busy read.
- **Approach:** the caller sends busy intervals and the service trusts them.
- **Alternative:** call calendar's free/busy API directly in our own adapter.
- **Tradeoff:** an unlisted meeting can be overlapped. In return the service
  stays contract-only, so the calendar can still be swapped by configuration.
- **Evidence:** in the [live run](evidence/focus-day-live.md), the listed
  12:00-13:00 busy hour was left free on the real calendar.
- **Revisit when:** the calendar contract gains a list or free/busy operation.

### State store

- **Constraint:** reporting progress and asking about an issue (the RFD's
  scenarios 2 and 3, [RFD, Proposal, "State"](RFD.md#proposal)) need state
  that outlives a request: each assignee's approved plan, the remaining
  share of unfinished issues, and a completion log. The service must also
  run as N >= 1 instances, so a plan saved through one instance must be
  readable through any other.
- **Approach:** `store.py` keeps that state in one Postgres database that
  every instance reaches through `STATE_DB_URL`; the instances themselves
  stay stateless. It holds three kinds of record, each keyed by `assignee`:
  - **Approved plan:** `assignee`, `day`, and per block `issue_id`, `title`,
    `start`, `end`, `event_id`. One per (assignee, day); saving replaces it.
  - **Remaining share:** `assignee`, `issue_id`, `remaining_percent` (1 to 100).
  - **Completion log entry:** `assignee`, `day`, `issue_id`,
    `remaining_percent` (0 = finished), `recorded_at` (UTC), `applied`. One
    per (assignee, day, issue_id); recording it again updates it.

  Focus-day saves its plan in a fifth step, `save_plan`, only after every
  other step succeeded, so the store never holds a draft ("answer only from
  approved plans"). Until focus-day has its own approval step, a fully
  booked plan counts as approved. Every write is an upsert in one
  transaction, and uniqueness and ranges are database constraints, so
  concurrent requests on different instances converge on one row. Tables
  are created at startup if missing (under an advisory lock, so instances
  starting together do not race). The in-memory `InMemoryStateStore`
  (`STATE_DB_URL=memory://`) follows the same rules for tests and the
  disposable service; `tests/test_store.py` runs the same tests on both.
- **Alternatives:**
  - *SQLite inside the service* (the first plan for #85): each instance
    would have its own file, so a second instance could not see the first
    one's plans, and Render's free filesystem is wiped on every redeploy.
  - *State in the client* (the TUI, #88): one user's client has only that
    user's plans, but scenario 3 asks about anyone's issue; the Level 3
    agent is a second client that cannot see the TUI's files; and the
    service could not enforce "approved plans only" if the client sent the
    plan. It would also put product state in the interface, which #88
    rules out.
  - *Reading the standup doc back:* it is Markdown for people, so parsing it
    is fragile, and it has no event IDs.
- **Tradeoff:** a database to run and secure, and one more dependency
  (`psycopg`). In return any instance can answer, and the state survives
  redeploys. A query costs a few milliseconds, small next to the tracker,
  calendar and storage calls every request already makes.
- **Evidence:** `tests/test_multi_instance.py` starts two service processes
  on one database: plans saved through each are visible to an independent
  store, and ten concurrent runs of the same plan through both leave one
  whole plan. `tests/test_store.py` shows 20 writers on separate
  connections converging and a failed save leaving the previous plan.
  `docker-compose.yml` runs two instances by hand.
- **Revisit when:** the free database's limits matter (see Known
  limitations), or the state grows beyond these three records.

## Issue status

`GET /v1/issue-status` (`issue_status.py`) is the RFD's scenario 3: is this
issue scheduled, how much is left, and did its block pass with no report? The
input is structured; turning a plain-language question into a query is the
Level 3 agent's job.

**Lookup order.** Parameters are validated first, then:

1. `find_issue` reads the tracker list, as `list_issues` does, and keeps the
   issue whose ID is `issue_id`, or every issue whose title contains `title`
   (ignoring case). Nothing: 404. Several: `ambiguous`, sorted by title, and
   the store is not read. An ID is also looked up in the list, not with
   `get_issue`, because Trello's `get_issue` returns archived cards and cards
   of any list the token can read, and the configured list is what defines
   "same team". The cost is the list's limit: only the 100 newest issues can
   be found (F6).
2. `read_plan` reads every saved plan containing the issue, newest day first.
   None: `no_approved_plan`. Otherwise the answer comes from the newest plan's
   owner: their block for `day` (default: today in `DEFAULT_TIMEZONE`), the
   remaining share, and the completion log. The issue's title comes from the
   tracker, so a renamed issue shows its current title.

**Owner.** The assignee of the newest saved plan containing the issue, by plan
day; two plans for the same day give the first assignee alphabetically.
Claims do not exist yet, so this stands in for "who claimed it". Only the
owner's blocks are reported: another assignee's block on `day` gives
`scheduled: null`.

**`passed_without_report`.** True when the issue's block on
`last_planned_day` (the owner's newest planned day) ended before now and the
completion log has no entry for (owner, that day, issue). It does not depend
on the queried `day`. Now comes from `app.state.clock`, set at startup and
read on each request, so tests replace it.

**Why it is read-only.** The answer uses only the tracker list and the state
store, and nothing is recorded about the question, so there is nothing to
approve and any request can be repeated. The store holds only approved plans
([State store](#state-store)), so a draft is never published.
`test_queries_write_nothing` checks that no provider or store write happens.

| Case | HTTP, status | Test |
| --- | --- | --- |
| Invalid query | 422 `invalid_input`, step `find_issue`, tracker not called | `test_invalid_input_is_rejected_before_any_call` |
| No match, including an issue deleted from the tracker | 404 `not_found`, step `find_issue` | `test_no_match_is_not_found`, `test_a_planned_issue_deleted_from_the_tracker_is_not_found` |
| Tracker fails | Tracker's code, step `find_issue` | `test_tracker_failure` |
| State database down | 500 `internal_error`, `retryable: true`, step `read_plan` | `test_store_read_failure` |

Error bodies: [ERRORS.md](ERRORS.md#get-v1issue-status).

## Boundaries, validation, credentials and invariants

**Boundaries.** Three layers, and each one imports only the layer below it:

| Layer | Code | Knows about |
| --- | --- | --- |
| Our API | `main.py`, `models.py`, `orchestrator.py`, `issue_status.py`, `planner.py`, `doc.py` | Our own models, `StepError` and the HW1 error codes (`issue_tracker_contract.errors`); never a provider interface or type |
| Adapters | `adapters/` | The shared contracts (`issue-tracker-contract`, `calendar-contract`, `cloud-storage-api`); they translate contract results and errors into our types |
| Implementations | Trello, Team 5, Team 3 packages | Reached only through the contract interfaces; never imported by name |

The state store sits beside the adapters: the operations use the
`StateStore` protocol and `StoreError`, and only `store.py` imports the
database driver.

`tests/test_import_boundary.py` fails the build if any module imports an
implementation or SDK, if anything other than the adapters, factories and
fakes imports a provider interface, if the planner or doc builder imports
anything but a few pure standard-library modules (and the doc builder, the
planner), or if anything but `store.py` imports a database driver.

**Where input is validated.** Everything is checked before the first provider
call, so a rejected request has no side effect:

1. The request schema (`models.py`): types, `HH:MM` and `YYYY-MM-DD`
   formats, ranges; unknown fields rejected.
2. The orchestrator: the time zone, the window, the busy intervals, the
   assignee (which also builds the doc path) and any replayed plan, using the
   planner's own rules. `validate_assignee` accepts only lowercase, so two
   assignees can never map to one doc on a case-insensitive provider (such as
   Dropbox) and to two on a case-sensitive one (F1).
3. The contracts check again on the way out (`CreateEvent`'s title and time
   rules, the storage path rule). Our input always passes, so a failure there
   is a bug in this service (`internal_error`).

A problem found in stages 1 or 2 is reported as its own step, `validate`,
so the response shows the request never reached the planner (F3).

**How implementations are selected.** `TRACKER_PROVIDER`, `CALENDAR_PROVIDER`
and `STORAGE_PROVIDER` each name a `module:attr`. At startup, `config.py`
imports the module and `factories.py` builds each provider once and checks
that it implements its contract interface. Provider-specific arguments
(`token_file`) are supplied only there. The fakes are selected through the
same variables, so tests and the disposable service exercise the same path.

**How credentials stay out of the repository.** Credentials exist only as
environment variables and one mounted secret file:

- Locally they are exported in the shell or kept in `~/.config/ospsd-team4/`.
  In production they are in the Render environment group
  `ospsd-team4-product-secrets`, entered by hand, with the Google token as
  a secret file mounted at `/etc/secrets/`.
- Terraform links the environment group by ID only, so state holds no
  values. For the same reason the state database is created by hand and
  its URL (which contains its password) is entered in the environment
  group as `STATE_DB_URL`, rather than managed by Terraform.
- `.gitignore` catches stray token and client-secret files. `.dockerignore`
  keeps every `*.json` and `.env` out of the image, and `disposable_env.sh`
  fails the release if a credential file is found in it.
- Startup errors name the variable, never its value.

**Rules that hold even when a dependency fails:**

1. No provider text reaches a caller or a log.
2. Every failure names its step and uses an HW1 code and status.
3. Steps fail fast: nothing runs after the failed step, and the doc is
   written only after every event exists.
4. `created` lists only events known to exist.
5. An uncertain write is never retried automatically, and is reported with
   `may_have_applied: true`.
6. Invalid input never reaches a provider.
7. Bad configuration, a provider that cannot be built, or an unreachable
   state database stops startup. The service never serves requests
   half-configured.
8. A run without `replay` books nothing if the doc for its assignee and day
   exists, or if storage cannot say whether it does (F5).
9. Only a run whose every other step succeeded saves a plan, and a plan is
   saved whole or not at all.

## 4.3 Failure modes

Every failure response has the body described in
[ERRORS.md](ERRORS.md#error-body-of-post-v1focus-day): the failed step, an
HW1 code and status, `retryable`, `may_have_applied`, and the `plan` and
`created` known so far. `status` is `partial` if any event exists, otherwise
`failed`. "Replay" below means sending the same request with
`replay: {plan, created}` copied from the response.

| Case | HTTP, status | What exists afterwards | What the caller should do | Test |
| --- | --- | --- | --- | --- |
| Invalid input (bad zone, window, busy interval, assignee including upper case (F1), or replay plan) | 422 `invalid_input`, `failed`, step `validate` (F3) | Nothing: rejected before any provider call | Fix the field named in `error.details` | `test_invalid_input_is_rejected_before_any_call`, `test_invalid_replay_is_rejected_before_any_call` |
| Same request sent again without `replay` after a run that wrote its doc (for example after a timeout or a lost response) | 412 `precondition_failed`, `failed`, step `check_doc`, not retryable (F5) | Nothing new: no tracker read, no event, the earlier doc untouched | Replay with the earlier response, or remove the earlier events and doc by hand to plan the day again | `test_same_request_again_is_rejected_before_any_event`, `test_a_doc_written_by_anyone_stops_the_run` |
| Same request sent again without `replay` after a run that failed before writing its doc | 200 `ok` (not caught: there is no doc to find) | A second set of events for the blocks the first run booked | Replay instead; if it was sent anyway, remove the duplicate events by hand | Known gap (F5), documented in ERRORS.md |
| Two runs for the same assignee and day at the same time | Both pass `check_doc` | Two sets of events; the doc lists the later run's | Do not run concurrently; remove the duplicates by hand | Known gap (F5) |
| Storage fails during `check_doc` | Storage's code (for example 502 `provider_error`, retryable), `failed`, step `check_doc`, `may_have_applied: false` | Nothing: fails closed, before the tracker or calendar are called | Retry the same request later if `retryable` | `test_doc_check_failure_books_nothing`, `test_check_failures_are_reads_that_applied_nothing` |
| 100 or more issues in the list | 200, `ok`, `issues_truncated: true` (F6) | Only the 100 newest issues are planned (`list_issues(limit=MAX_LIST_LIMIT)`, newest first). Older ones are in neither `blocks` nor `unscheduled`. Exactly 100 also sets the flag: the contract cannot tell it from more | Close or move issues so the list is under 100 | `test_more_than_100_issues_plans_the_newest_100_and_says_so`, `test_a_full_page_is_reported_as_truncated` |
| Unauthenticated caller | Accepted: there is no caller authentication yet (F7) | Anyone who can reach the endpoint can book events on the test calendar and, through `assignee`, overwrite any assignee's doc | Nothing yet; Level 4 adds authentication | Added with F7 |
| Tracker fails | Tracker's code (for example 502 `provider_error`, retryable), `failed`, step `list_issues` | Nothing | Retry the same request later if `retryable` | `test_tracker_failure`, `test_unexpected_tracker_exception_does_not_leak` |
| Partial fit: some issues do not fit | 200, `ok` | All fitting blocks booked, doc written; the rest in `plan.unscheduled` with `no_free_slot` | Nothing; not an error | `test_partial_fit_is_a_success` |
| Calendar fails on the first block | 502 `provider_error` (or another mapped code), `failed`, step `create_events` | No events, no doc | Replay if `retryable` | Same path as the next row with an empty `created`; `status` is `failed` because no event exists |
| Calendar fails on block k of n | 502 `provider_error`, `partial` | Blocks 1 to k-1 booked and in `created`; no doc | Replay: existing events are kept, only the missing ones are created, then the doc is written | `test_calendar_failure_on_block_two_of_three`, `test_replay_after_a_calendar_failure_creates_only_the_missing_events` |
| Uncertain create | 502 `provider_error`, `may_have_applied: true`, not retryable | Events before it in `created`; this one may exist with no known ID | Check the calendar first. Replaying anyway can create a duplicate | `test_uncertain_create_is_reported_and_not_retried` |
| Calendar token missing, expired or revoked | 503 `service_not_configured`, step `create_events` | Nothing new | Operator re-authorizes and replaces the secret file (DEPLOYMENT.md, step 2); then replay | `test_expired_calendar_token_is_a_configuration_failure` |
| Doc write fails after all events exist | Storage's code (for example 502 `provider_error`, `retryable: true` (F4)), `partial`, step `write_standup_doc` | Every event booked and in `created`; no doc, or possibly the doc (`may_have_applied`), which is harmless because rewriting it gives the same bytes | Replay: every event is confirmed with `get`, none recreated, and only the doc is written | `test_doc_failure_after_events_is_partial`, `test_replay_after_a_doc_failure_only_writes_the_doc` |
| Doc folder missing (the operator has not created it) | 503 `service_not_configured`, `partial`, step `write_standup_doc`, not retryable (F2) | Every event booked and in `created`; no doc | Operator creates the folder (DEPLOYMENT.md, step 3); then replay | `test_missing_doc_folder_is_a_configuration_failure` |
| Doc write fails with no events (empty plan) | Storage's code, `failed` | Nothing | Replay or retry | `test_storage_failure_with_no_events_is_failed` |
| Any step before `save_plan` fails | That step's error | No plan saved for the day (an earlier saved plan is kept) | As in that step's row; a successful replay saves the plan | `test_partial_run_saves_nothing`, `test_failed_run_saves_nothing`, `test_successful_replay_saves_the_plan` |
| Saving the plan fails (database down or unreachable) | 500 `internal_error`, `retryable: true`, `may_have_applied: false`, step `save_plan`; `partial` if any event exists | Every event booked and the doc written; no plan saved (the save is one transaction) | Replay: events are confirmed with `get`, none recreated, the doc is rewritten and the plan saved | `test_save_failure_is_a_retryable_internal_error`, `test_replay_after_a_save_failure_saves_without_new_events` |
| Replay, recorded events all exist | 200, `ok` | No new events; doc rewritten with the same bytes | Nothing | `test_replay_skips_events_that_exist`; live in the evidence |
| Replay after an event was deleted (NOT_FOUND) | 200, `ok` | That event recreated with a new ID in `created`; others unchanged | Keep the new response | `test_replay_recreates_an_event_that_was_not_found`; live in the evidence |
| Replay, but `get` itself fails | The calendar's code, step `create_events` | Unchanged; nothing created after the failed check | Replay again later | `test_replay_stops_when_an_event_cannot_be_checked` |
| Bad configuration (missing or malformed `*_PROVIDER`, unreadable `CALENDAR_TOKEN_FILE`, bad default) | Service does not start | No service: logs `startup_config_invalid` naming the variable, exits | Operator fixes the variable; the release's disposable check catches this before deploy | `test_startup_fails_on_bad_config`, `test_config.py`, `disposable_env.sh` |
| A provider cannot be built (for example missing Dropbox credentials) | Service does not start | As above, naming `STORAGE_PROVIDER` | Operator sets the credentials in the Render environment group | `test_startup_fails_when_a_provider_cannot_be_built` |
| `STATE_DB_URL` missing or not a `postgresql://` URL, or its database unreachable | Service does not start | As above, naming `STATE_DB_URL`; the URL is never printed | Operator sets the Internal Database URL in the environment group, or brings the database back (DEPLOYMENT.md, State database) | `test_startup_fails_on_bad_state_db_url`, `test_startup_fails_when_the_state_database_is_unreachable`, `disposable_env.sh` |
| A bug in this service | 500 `internal_error`, plain HW1 shape | Whatever ran before it | Report it; the log has the traceback | Handler in `main.py` |

Each failed request is logged as one JSON line (`event: focus_day_failed`)
with the step, code, HTTP status, flags, the number of created events and a
short internal cause, never the message, issue titles or provider text.

A rollback to an earlier release does not undo calendar events or docs a
faulty release created; they have to be removed by hand
([DEPLOYMENT.md, Roll back](DEPLOYMENT.md#roll-back)).

## Known limitations and future work

- The service cannot see the calendar; overlap avoidance depends on `busy`
  (4.2). Use a provider read if the contract gains one.
- No delete: the service never removes events, so a plan that changes after
  booking needs manual cleanup. A replay keeps the original plan.
- The assignee is a label; a product-side issue-to-assignee mapping is future
  work.
- One fixed block length per request (`default_minutes`); no per-issue
  estimates, since the tracker contract has none.
- Team 5's Google app runs in Testing mode, whose refresh tokens can expire
  after 7 days. The token must be renewed before demos and grading.
- The storage contract has no folder operation and does not promise that
  `create` makes missing parent folders. The doc folder must be created once
  during setup (F2).
- At most 100 issues are read per request, the newest first; when the
  limit is reached the response sets `issues_truncated` (F6).
- The doc check (F5) catches a repeated request only once its doc exists:
  a run that failed before writing the doc, or two runs at the same time,
  can still book a day twice. A run record in storage (Team 3's
  alternative in 4.2) would close both gaps.
- No caller authentication until Level 4 (F7).
- **The free state database is temporary.** Render's free Postgres
  [expires 30 days after creation](https://render.com/docs/free), and is
  deleted after a further 14-day grace period unless it is upgraded to a
  paid plan. A workspace may have only one free database at a time; it has
  1 GB of storage, no backups and no managed connection pooling
  ([Render: Deploy for Free](https://render.com/docs/free), checked
  2026-11-23). Recreating it loses every saved plan, remaining share and
  log entry (DEPLOYMENT.md, State database). Render may change these
  terms, so check that page before relying on them.
- **The deployment runs one instance.** Render's free web plan runs a
  single instance, so N > 1 is shown locally (`docker-compose.yml`,
  `tests/test_multi_instance.py`) rather than on Render.
- The disposable service uses the in-memory state store
  (`STATE_DB_URL=memory://`), since the one free database is production's.
  Its state is lost on restart and is not shared between instances.

## Follow-up work from design review

Changes agreed after Team 3's review of this document. Each becomes a
GitHub issue; the IDs F1 to F7 are used above.

| ID | Change | Comment it answers | Status |
| --- | --- | --- | --- |
| F1 | `validate_assignee` accepts only lowercase, so every storage provider gives the same result | Assignee case on Dropbox | Done |
| F2 | Stop relying on `create` making parent folders: the doc path becomes flat, `/standups/<YYYY-MM-DD>-<assignee>.md`, in one folder created at setup, and a missing folder is reported as `service_not_configured` | Parent folders aren't part of the contract | Done |
| F3 | Report invalid input as a separate first step, `validate`, instead of `plan` | Small clarity point | Done |
| F4 | Storage provider errors in `write_standup_doc` are `retryable: true`, since rewriting the doc is safe | `retryable` seems inconsistent with this row | Done |
| F5 | A run without `replay` checks the doc path first (new step `check_doc`) and returns 412 `precondition_failed` if the doc exists | Cases to add: same request without `replay` | Done |
| F6 | Report when `list_issues` returned the full 100 (`issues_truncated`), so dropped issues are visible | Cases to add: more than 100 issues | Done |
| F7 | Caller authentication (Level 4) | Cases to add: no caller authentication | Planned |
