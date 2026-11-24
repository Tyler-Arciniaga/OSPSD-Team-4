# OSPSD Team 4 Product Service

`ospsd-team4-product` (import package `ospsd_team4_product`) is our HW3
product service. Its one operation, `POST /v1/focus-day`, reads issues from
our tracker, plans focus blocks around the caller's busy times, books them in
a calendar and writes a standup doc to cloud storage. The tracker is our
Trello package, the calendar is Team 5's Google Calendar package, and the
storage is Team 3's Dropbox package, each chosen by configuration.

| Document | What it covers |
| --- | --- |
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture, a real request trace, decisions, failure modes |
| [docs/ERRORS.md](docs/ERRORS.md) | Every error the service can return, and every startup error |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | The release pipeline, Terraform and the one-time human setup |
| [docs/evidence/](docs/evidence/) | Partner installs, the Dropbox round trip, a live focus-day run, a storage swap to Team 1's package, a controlled calendar failure, the TUI walkthrough |
| [tui/README.md](tui/README.md) | `focus-day`, the terminal interface: getting started, screens, key bindings |

This service is separate from the HW2 issue service at the repository root:
it has its own dependencies, image and deployment. Everything below runs from
`product/`.

## Setup

Requires [uv](https://docs.astral.sh/uv/) 0.12.17 or newer (CI uses
0.12.22). Python 3.14 is required by Team 5's package; uv downloads it if it
is not installed.

```sh
cd product
uv sync --locked
```

Every contract and implementation is installed from an immutable git tag
(`pyproject.toml`, `[tool.uv.sources]`); `uv.lock` records the exact commits.

## Configuration

All configuration comes from environment variables, read once at startup. A
missing or malformed value stops the service before it serves requests, with
a log line naming the variable, never its value (see
[ERRORS.md, Startup errors](docs/ERRORS.md#startup-errors)).

| Variable | Required | Meaning |
| --- | --- | --- |
| `TRACKER_PROVIDER` | yes | `module:attr` of a callable returning an `IssueProvider` |
| `CALENDAR_PROVIDER` | yes | `module:attr` of a callable returning a `CalendarService` |
| `STORAGE_PROVIDER` | yes | `module:attr` of a callable returning a `DocumentStorage` |
| `CALENDAR_TOKEN_FILE` | if the calendar factory takes `token_file` (Team 5's does) | Absolute path to the Google OAuth token file |
| `STATE_DB_URL` | yes | The state database every instance shares: a `postgresql://user:password@host:port/database` URL, or `memory://` for the in-memory store (one process only, for tests and the disposable service). Holds a password: treat it as a secret ([DESIGN.md, State store](docs/DESIGN.md#state-store)) |
| `DEFAULT_WORKING_WINDOW` | no, default `09:00-17:00` | Used when a request has no `working_window` |
| `DEFAULT_MINUTES` | no, default `90` | Block length when a request has no `default_minutes` (1 to 1440) |
| `DEFAULT_TIMEZONE` | no, default `America/New_York` | Used when a request has no `timezone` |
| `GIT_SHA` | no | The commit `/readyz` reports (the image sets it) |

Provider values:

| | Real implementation | In-memory fake (tests, disposable service) |
| --- | --- | --- |
| Tracker | `team_4_issue_tracker_trello:TrelloIssueProvider` | `ospsd_team4_product.fakes:InMemoryIssueProvider` |
| Calendar | `team5_calendar:create_calendar_service` | `ospsd_team4_product.fakes:InMemoryCalendarService` |
| Storage | `dropbox_client_impl:DropboxDocumentStorage` | `ospsd_team4_product.fakes:InMemoryDocumentStorage` |

Team 1's Dropbox package is also installed and can replace Team 3's by
configuration alone: `STORAGE_PROVIDER=team1_dropbox:Team1DropboxStorage.from_env`,
with the same three Dropbox variables
([evidence/storage-swap.md](docs/evidence/storage-swap.md)).

`CALENDAR_PROVIDER=ospsd_team4_product.fakes:calendar_outage_on_second_create`
is a controlled calendar whose second `create` reports an outage, for
showing the documented `create_events` error through a running service
([evidence/calendar-failure.md](docs/evidence/calendar-failure.md)).

The real implementations read their own credentials from the environment:
`TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_LIST_ID` (the list the service
plans from), and `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`,
`DROPBOX_REFRESH_TOKEN`. Keep them, the Google token file and the
production `STATE_DB_URL` outside the repository. How to obtain each one is in
[DEPLOYMENT.md, One-time setup](docs/DEPLOYMENT.md#one-time-setup-human-only).

Team 3's package loads a `.env` file from the repository root on import,
which is the HW2 service's. Export every variable above so nothing is picked
up from it by accident.

## Running locally

With the fakes and the in-memory state store, which need no credentials or
database:

```sh
export TRACKER_PROVIDER=ospsd_team4_product.fakes:InMemoryIssueProvider
export CALENDAR_PROVIDER=ospsd_team4_product.fakes:InMemoryCalendarService
export STORAGE_PROVIDER=ospsd_team4_product.fakes:InMemoryDocumentStorage
export STATE_DB_URL=memory://
uv run uvicorn ospsd_team4_product.main:app --port 8000
```

To keep plans in Postgres instead, start the local database from
`docker-compose.yml` and point at it:

```sh
docker compose up -d db
export STATE_DB_URL=postgresql://focus_day:focus_day@localhost:55432/focus_day
```

Then, from another shell:

```sh
curl -s localhost:8000/readyz
curl -s -X POST localhost:8000/v1/focus-day -H 'content-type: application/json' \
  -d '{"assignee": "tyler", "day": "2026-11-19",
       "busy": [{"start": "2026-11-19T12:00:00-05:00", "end": "2026-11-19T13:00:00-05:00"}]}'
```

The response lists seven steps (`validate`, `check_doc`, `list_issues`,
`plan`, `create_events`, `write_standup_doc`, `save_plan`), and the doc is written to
`/standups/2026-11-19-tyler.md`. The assignee must be lowercase (`a-z`,
`0-9`, `.`, `_`, `-`); anything else is rejected with 422 on the `validate`
step. The in-memory storage starts with the `/standups` folder; with real
storage, an operator creates it once
([DEPLOYMENT.md, step 3](docs/DEPLOYMENT.md#3-dropbox-team-3s-storage)).

To retry or resume, send the same request with
`"replay": {"plan": ..., "created": ...}` copied from the response
([DESIGN.md, 4.2](docs/DESIGN.md#replay-not-recompute)). Sending the same
request again without `replay` once its doc exists returns 412
`precondition_failed` on `check_doc` and books nothing.

### Several instances

`docker-compose.yml` runs two service instances (ports 8001 and 8002, with
the fakes) sharing one Postgres database, which shows the service running as
N = 2. Its header has the commands to post a focus day to each instance and
see both plans in the one database:

```sh
docker compose up --build -d
docker compose down -v   # when done
```

### Real providers

Or use the terminal interface, `focus-day`, which fills in the request
from a form, shows the result on a day timeline and retries with `replay`
for you: see [tui/README.md](tui/README.md).

With the real providers, set the three real `*_PROVIDER` values,
`CALENDAR_TOKEN_FILE`, `STATE_DB_URL` and the six credentials instead. This books real
events on the calendar account the token belongs to; the run recorded in
[evidence/focus-day-live.md](docs/evidence/focus-day-live.md) shows the
exact commands.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /healthz` | Liveness: the process is serving |
| `GET /readyz` | Readiness: configuration loaded; reports the deployed commit |
| `POST /v1/focus-day` | The operation; request, response and errors in [DESIGN.md](docs/DESIGN.md) and [ERRORS.md](docs/ERRORS.md) |

## Checks

The same checks run in CI (`.github/workflows/ci-product.yml`):

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run pytest
```

The tests use the fakes and need no credentials or network. They include
the planner, every adapter error mapping, every focus-day failure and
replay path (including `save_plan`), the startup configuration errors, the
import boundary (`tests/test_import_boundary.py`) and the post-deploy smoke
check against the app in process.

The state store and multi-instance tests (`tests/test_store.py`,
`tests/test_multi_instance.py`, and a few in `tests/test_health.py`) also
need a Postgres they can wipe. Without `TEST_STATE_DB_URL` they are
skipped; CI always runs them. To run them locally, use a separate database
on the compose server:

```sh
docker compose up -d db
docker compose exec db createdb -U focus_day focus_day_test   # once
TEST_STATE_DB_URL=postgresql://focus_day:focus_day@localhost:55432/focus_day_test uv run pytest
```

Other checks:

| Check | Command | Needs |
| --- | --- | --- |
| Image and startup checks, as in a release | `product/scripts/disposable_env.sh` (from the repository root) | Docker |
| Terraform | `cd infra && terraform init -backend=false && terraform validate && terraform test` | Terraform 1.17.1 |
| Dropbox live round trip | `uv run python scripts/dropbox_roundtrip.py` | Dropbox test credentials ([evidence](docs/evidence/dropbox-roundtrip.md)) |
