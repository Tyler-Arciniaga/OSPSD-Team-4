## Team 4 - CS 3943 OSPSD

This is Team 4's repository for CS 3943 OSPSD.

We are building a FastAPI backend service for the Issue Tracker vertical, using Trello as our provider.

## Team Members

| Name | NetID |
| ------ | ------- |
| Tyler Arciniaga | ta2585 |
| Turki Almejhed | taa9292 |
| Joseph Jiminian | jj3945 |
| Chan Young Kang | ck1894 |
| Vanchi Nathan | vt2464 |

## Project Status

The service implements `GET /issues/{issue_id}`, `POST /issues`,
`PATCH /issues/{issue_id}`, and `DELETE /issues/{issue_id}` against Trello.
GET, POST, and PATCH translate a card's `id` and `name` into the public `id`
and `title` fields; PATCH updates an issue's `title`; DELETE permanently
removes the card. GET and PATCH return an `ETag`; PATCH with `If-Match`
rejects stale overwrites with `412` (see
[Conditional updates](docs/api.md#conditional-updates)). `POST /issues`
accepts an optional `Idempotency-Key` header, so retries and overlapping
requests create at most one issue (see
[Idempotent creation](docs/api.md#idempotent-creation)). Every failure returns one documented, provider-independent
error format and writes one sanitized log line. Automated tests run without
credentials or network access.

The service checks its required configuration at startup and refuses to
start, naming the missing variable without printing any value, if
something is missing. `GET /healthz` (liveness) and `GET /readyz`
(readiness) report its health (see [Health checks](docs/api.md#health-checks)).
It is packaged as a container image and deployed to Render with Terraform;
see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

GitHub Actions is configured to run linting, formatting checks, type checking, and automated tests on pushes and pull requests. It also builds the container image and checks it in a disposable container. Pull requests that change `infra/` get a `terraform plan` comment, and deploys run only by manual dispatch.

## Project Structure

- `app/main.py`: FastAPI endpoints, which validate input and call the supplied provider, and the handlers that turn failures into error responses and log lines.
- `contract/`: The shared contract package, `issue_tracker_contract`. It must not import anything from `app/`, a web framework, or a provider SDK.
  - `contract/src/issue_tracker_contract/models.py`: Public `Issue` response model, `IssueCreate` and `IssueUpdate` request models, and the shared `IssueTitle` rule.
  - `contract/src/issue_tracker_contract/interface.py`: Provider-independent `IssueProvider` interface.
  - `contract/src/issue_tracker_contract/errors.py`: Error codes, the `ServiceError` raised by providers and endpoints, the code-to-status mapping, and the error response models.
  - `contract/tests/test_import_boundary.py`: Fails if the contract package imports service, framework, or provider code.
  - `contract/tests/conformance/`: Shared HTTP conformance suite for get and create, run unchanged against a live service configured for each provider. Skipped unless `CONFORMANCE_BASE_URL` is set.
- `app/config.py`: Startup configuration check: the variables each provider requires and the idempotency store directory.
- `app/health.py`: Liveness and readiness responses, the cached provider reachability check, and the deployed commit.
- `app/errors.py`: Failure logging.
- `app/interface.py`: Service-side provider protocols: `ClosableIssueProvider` (every adapter startup builds can be probed with `ping()` and closed) and the optional `IdempotentIssueProvider` capability used for idempotent creation.
- `app/providers/`: Provider adapters, one module per provider.
  - `app/providers/common.py`: What every adapter shares: the request timeout and the caller-facing error messages.
  - `app/providers/trello_provider.py`: Trello authentication, requests, and translation of Trello responses and failures.
  - `app/providers/linear_provider.py`: Linear GraphQL requests and translation of Linear responses and failures (get and create; rename and delete not yet implemented).
- `app/cache.py`: Caches successful reads and invalidates saved issues before updates or deletions.
- `app/dependencies.py`: Creates one cached provider (Trello, or Linear with `ISSUE_PROVIDER=linear`) and the idempotency key store at application startup and supplies them to the endpoints.
- `app/idempotency.py`: Durable idempotency key store and the logic that creates each keyed issue at most once.
- `tests/conftest.py`: Shared per-test client setup and application startup/shutdown.
- `tests/test_cache.py`: Cache behavior, concurrency, invalidation, and application lifecycle tests.
- `tests/trello/`: Tests of the real Trello adapter with simulated Trello responses.
  - `tests/trello/test_get_issue.py`, `test_create_issue.py`: Retrieval and creation endpoint tests.
  - `tests/trello/test_update_issue.py`: `PATCH` endpoint tests with simulated Trello responses and supplied providers.
  - `tests/trello/test_delete_issue.py`: DELETE request, failure, and injected-provider workflow tests.
  - `tests/trello/test_provider.py`: Provider compatibility, translation, and error tests.
- `tests/linear/`: Tests of the real Linear adapter with simulated GraphQL responses, through the real startup with `ISSUE_PROVIDER=linear`.
  - `tests/linear/test_get_issue.py`, `test_create_issue.py`: Retrieval and creation endpoint tests.
  - `tests/linear/test_provider.py`: Protocol compatibility, uncached reads, unimplemented operations, and `close()`.
  - `tests/linear/fake_linear.py`: The simulated Linear API and expected values shared by these tests.
- `tests/test_provider_selection.py`: Startup builds the adapter named by `ISSUE_PROVIDER` and closes it at shutdown.
- `tests/test_config.py`: Startup configuration checks, including that a server started with a variable missing exits without printing values.
- `tests/test_health.py`: `/healthz`, `/readyz`, the provider check's caching, and the reported commit.
- `tests/test_injected_provider.py`: Endpoint tests using supplied fake providers.
- `tests/test_idempotency.py`: Repeated, overlapping, conflicting, expired and restarted keyed creations, unknown provider outcomes, and cache and logging integration.
- `tests/test_errors.py`: Error response and failure-logging tests using a controlled provider.
- `tests/expected_errors.py`: Expected error bodies shared by the endpoint tests.
- `docs/api.md`: API contract, error model, examples, and assumptions.
- `docs/providers.md`: Provider interface, error translation, dependency setup, and testing approach.
- `docs/DEPLOYMENT.md`: Deploying, verifying, rolling back, and tearing down the Render deployment, plus the teammate walkthrough.
- `Dockerfile`, `.dockerignore`: The deployable image: locked runtime dependencies, non-root user, no credentials.
- `infra/`: Terraform for the Render deployment, with state in HCP Terraform. `release.tf` pins the deployed commit.
- `scripts/disposable_env.sh`: Builds the image and checks it in a throwaway container with fake configuration.
- `scripts/conformance_remote.sh`: Runs the conformance suite against a deployed service.
- `pyproject.toml`: Python requirement, dependencies, and tool configuration.
- `uv.lock`: Locked dependency versions for reproducible installation.
- `.github/workflows/ci.yml`: Automated linting, formatting, type checking, tests, and the container image check.
- `.github/workflows/terraform-plan.yml`: Posts `terraform plan` on pull requests that change `infra/`.
- `.github/workflows/terraform-apply.yml`: Manual, approved apply and deploy of the pinned commit, followed by a plan that must show no changes.
- `.github/workflows/terraform-destroy.yml`: Manual, approved teardown of everything Terraform created.
- `AGENTS.md`: Contribution and release workflow.

## Installation

The project requires Python 3.11.5 or newer. Local checks were verified
with Python 3.13.5 and uv 0.12.17; CI uses those same versions.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run these commands from the repository root:

```bash
uv sync --locked --python 3.13.5
```

This creates a local `.venv` and installs application and development
dependencies using the versions in `uv.lock`.

Use `uv run` for project commands; manually activating `.venv` is
not necessary.

## Configuration

Copy the credential template if you do not already have a `.env` file:

```bash
cp -n .env.example .env
chmod 600 .env
```

`.env` is ignored by Git. Do not commit credentials. Set `ISSUE_PROVIDER`
in `.env` to the provider this process uses: `trello` (the default when
unset) or `linear`. Any other value stops startup. Configure that provider
below. Startup also stops, naming the missing variable, if any variable that
provider requires is empty. The other provider's variables are never needed.

### Trello Configuration

Use a dedicated Trello test account and a private test board containing a
card your team may change and a list your team may add cards to. The
account granting the token must be able to read the board and create, edit,
and delete its test cards.

1. Sign in to the test account. Create an app in the
   [Trello developer portal](https://trello.com/apps/admin), selecting
   "My app doesn't use Power-up capabilities." The app opens on the
   **OAuth 2.0** page, which asks for a callback URL; this service does not
   use OAuth 2.0, so skip that page. Open the **Trello Auth** tab instead and
   generate an API key.
2. In a browser, replace `YOUR_API_KEY` in this URL with that key and approve
   read and write access (the token expires after one day). Write access is
   required for POST, PATCH, and DELETE:
   `https://trello.com/1/authorize?expiration=1day&scope=read,write&response_type=token&key=YOUR_API_KEY`.
3. Edit `.env` locally and fill in `TRELLO_API_KEY` and `TRELLO_TOKEN`.
4. Set `TRELLO_LIST_ID` in `.env` to the full ID of the list where new
   issues should be created. To find it, open any card in that list,
   append `.json` to its URL, and copy the top-level `idList` value
   (24 hexadecimal characters). The service does not start without it.

See [Trello authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)
for permissions and token revocation.

### Linear Configuration

Use a dedicated Linear test workspace, or a test team in one, whose issues
your team may create and delete.

1. Sign in to the test workspace. Open **Settings > Personal > Security &
   access**, create a personal API key with full access and access to your specific team. Then copy
   it.
2. Edit `.env` locally, set `ISSUE_PROVIDER=linear`, and fill in
   `LINEAR_API_KEY`.
3. Set `LINEAR_TEAM_ID` in `.env` to the ID of the team new issues should be
   created in. To find it, export the key in a terminal and list the teams.
   Personal API keys are sent without a `Bearer` prefix:

   ```bash
   export LINEAR_API_KEY=<your key>
   curl -s https://api.linear.app/graphql \
     -H "Content-Type: application/json" -H "Authorization: $LINEAR_API_KEY" \
     -d '{"query": "{ teams { nodes { id key name } } }"}'
   ```

   Copy the test team's `id` (a UUID). The service does not start without it.
4. Optionally set `LINEAR_STATE_ID` to the workflow state new issues start
   in. When it is empty, Linear uses the team's default state.

See [Linear API authentication](https://linear.app/developers/graphql#authentication)
for API keys and revocation.

### Shared Configuration Settings

Optionally set `IDEMPOTENCY_DB_PATH` to the file that records idempotency
keys (default `idempotency.sqlite3` in the working directory, ignored by
Git). It must survive restarts, and every worker must use the same file.
Deleting it forgets all keys. With Trello, startup checks that its directory
exists and is writable. Linear does not support idempotent creation and never
opens it.

The startup command below loads `.env` into the service environment.
Existing exported environment variables take precedence over values in
the file. Restart the server after changing `.env`.

## Running the Service

From the repository root, run:

```bash
uv run --locked uvicorn app.main:app --reload --env-file .env
```

The service runs at <http://127.0.0.1:8000>.
Interactive API documentation is available at <http://127.0.0.1:8000/docs>.

The `--reload` option automatically restarts the server when code changes
during development. Press Ctrl+C in the terminal to stop it.

### Running in a Container

The deployable image runs the same way anywhere. Build it from the
repository root and pass configuration at run time; the image contains no
credentials:

```bash
docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t ospsd-team4 .
docker run --rm -p 8000:8000 --env-file .env \
  -e IDEMPOTENCY_DB_PATH=/app/data/idempotency.sqlite3 ospsd-team4
```

The `-e` setting overrides the relative `IDEMPOTENCY_DB_PATH` in `.env`,
because inside the image only `/app/data` is writable by the service's
non-root user.

`scripts/disposable_env.sh` builds the image and checks it in a
throwaway container with fake configuration. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the Render deployment.

## API Usage

Retrieve an issue using `GET /issues/{issue_id}`, where `issue_id` is
the required issue identifier. Create an issue using `POST /issues`
with a JSON body containing a `title`. Permanently delete an issue using
`DELETE /issues/{issue_id}`. Check the service with `GET /healthz`
(liveness) and `GET /readyz` (readiness; see
[Health checks](docs/api.md#health-checks)).

With the service running:

```bash
curl -i http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930
```

Expected status: `200 OK`

Expected response body:

```json
{
  "id": "64f1c0a2b3d4e5f607182930",
  "title": "Example issue"
}
```

Replace the example ID with the full ID of your test card. The returned
title is the card's name from the latest fetch, which may be cached for
30 seconds. Malformed IDs return `404 Not Found`; missing cards are detected
when a request reaches Trello. See [Caching](#caching).

If `TRELLO_API_KEY` or `TRELLO_TOKEN` is not set, requests that need the
Trello provider return `503 Service Unavailable` with code
`service_not_configured`, unless the ID is rejected first. Cache hits do
not recheck credentials. Complete [Configuration](#configuration) and
restart the server. Errors have the form
`{"error": {"code": "...", "message": "..."}}`; see
[the error model](docs/api.md#error-model) for every code, its status, and
whether to retry.

To create an issue:

```bash
curl -i -X POST http://127.0.0.1:8000/issues \
  -H "Content-Type: application/json" \
  -d '{"title": "Example issue"}'
```

Expected status: `201 Created`, with a `Location: /issues/<new ID>` header
and a body of the same shape as above containing the new card's ID and
title. A missing, empty, whitespace-only, or over-256-character title, or any
field other than `title`, returns `422`.

Update an issue's title using `PATCH /issues/{issue_id}`:

```bash
curl -i -X PATCH http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930 \
  -H "Content-Type: application/json" \
  -d '{"title": "Renamed issue"}'
```

Expected status: `200 OK`, with the updated issue in the same shape as
above. An empty body, or a title that breaks the same rules as POST
(missing, empty, whitespace-only, or over 256 characters), or any field
other than `title`, returns `422`.

Permanently delete a disposable test issue:

```bash
curl -i -X DELETE http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930
```

Expected status: `204 No Content`, with an empty body. Subsequent GET
requests handled by the same process and repeated DELETE requests return
`404` when Trello confirms the card is missing. Other processes may still
have a cached copy until it expires. This deletes the Trello card,
rather than archiving it. After a `502`, deletion may already have happened;
check with GET before retrying. The service does not retry requests.

See [the API contract](docs/api.md) for response fields, errors,
and assumptions.

## Caching

Successful GET results are cached in memory for 30 seconds after retrieval.
Reading a cached issue does not extend that lifetime. Each application
process keeps up to 100 issues, evicting the least recently used entry
when the cache is full. Errors are never cached.

PATCH and DELETE remove the affected cached entry before contacting Trello,
even if the operation later fails. The next GET fetches the issue again.
POST forwards creation to Trello without filling the cache.

Changes made directly in Trello, including deletion or permission changes,
may not be noticed until the cached entry expires. Separate server workers
have independent caches; an update in one worker does not clear another
worker's cache. Restarting the server clears its cache. Restart after
changing credentials or configuration, because cache hits do not recheck
Trello access.

See [Cache behavior](docs/providers.md#cache-behavior) for implementation
and concurrency details.

## Code Checks and Tests

Run the following checks from the repository root. GitHub Actions runs
the same checks on pushes and pull requests.

### Linting

We use Ruff's default lint rules as a baseline for common coding mistakes,
including unused imports, undefined names, and import organization.
See [Ruff's default rules](https://docs.astral.sh/ruff/default-rules/)
for the complete list and explanations.

```bash
uv run --locked ruff check .
```

Ruff's version is locked in `uv.lock`. Review rule changes when upgrading,
investigate reported warnings, and review automated fixes before committing.

### Formatting

Check that Python files follow Ruff's formatting style without modifying them:

```bash
uv run --locked ruff format --check .
```

### Type Checking

Check for inconsistencies between type annotations and how values are used:

```bash
uv run --locked mypy app tests contract/src contract/tests
```

### Automated Tests

Run all automated tests:

```bash
uv run --locked pytest
```

Tests cover all four endpoints, request body validation (including the
shared title rule for POST and PATCH), Trello request construction and
response translation, missing credentials or target issue list, and provider
failures. They also verify that the endpoints can use a supplied fake
provider, malformed Trello IDs cause no network request, invalid bodies are
rejected before calling provider methods, and deleted issues cannot be read
or deleted again. Error tests check the full error
response and log line for each class of failure, and that neither responses
nor logs contain credentials or provider details.

Cache tests cover repeated reads, expiration, least-recently-used eviction,
returned-copy isolation, invalidation before successful or failed writes,
uncached failures, and reuse and reset across application lifespans.

Linear tests in `tests/linear/` run retrieval and creation through the real
startup with `ISSUE_PROVIDER=linear` and simulated GraphQL responses,
including Linear's HTTP 200 responses that carry errors, trashed issues,
and malformed or short IDs.

The tests use simulated responses or fake providers and require no
running server, real credentials, or network access. Live Trello
verification is performed separately as described below.

### Conformance Suite

The shared conformance suite in `contract/tests/conformance/` checks that
a consumer sees the same behavior from every provider. It sends real HTTP
requests to a running service and creates real issues, so it is skipped
(such as during CI) unless `CONFORMANCE_BASE_URL` is set. Start the service
for one provider, then run:

```bash
CONFORMANCE_BASE_URL=http://127.0.0.1:8000 uv run --locked pytest contract/tests/conformance
```

Repeat for the other provider. See
[the suite's README](contract/tests/conformance/README.md) for the steps
and cleanup of created issues. To run it against the deployed service, use
`scripts/conformance_remote.sh <url>` (see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#verify)).

See [Provider Interface and Dependency Injection](docs/providers.md)
for the provider setup and the responsibilities of each test file.

## Trello Verification

Use the documented single-process startup command for this workflow so
GET, PATCH, and DELETE share the same cache.

1. On the dedicated test board, create a card named `HW1 Level 2 test`.
   Open the card in a browser and append `.json` to its URL. Find the
   top-level `id` in that response: use this full 24-character ID, rather
   than the short link from the card URL.
2. Configure credentials as above and start the service. In another
   terminal, set `TRELLO_TEST_CARD_ID` to that full ID and run:

   ```bash
   curl -i "http://127.0.0.1:8000/issues/$TRELLO_TEST_CARD_ID"
   ```

   Expect HTTP 200 and exactly `{"id":"<your full card ID>","title":"HW1 Level 2 test"}`.
   Compare both values to the real card.
3. Rename the card to `HW1 Level 2 renamed` in Trello. An immediate GET
   may still return the cached title. Wait at least 30 seconds after the
   rename, then repeat the request. Expect HTTP 200 with the new title and
   the same ID. Restarting the server also clears the cache.
4. Run `curl -i http://127.0.0.1:8000/issues/does-not-exist`.
   Expect HTTP 404 and
   `{"error":{"code":"not_found","message":"Issue not found"}}`.
5. With `TRELLO_LIST_ID` set to the test card's `idList`, create an issue:

   ```bash
   curl -i -X POST http://127.0.0.1:8000/issues \
     -H "Content-Type: application/json" \
     -d '{"title": "HW1 POST test"}'
   ```

   Expect HTTP 201, a `Location: /issues/<new ID>` header, and
   `{"id":"<new ID>","title":"HW1 POST test"}`. Confirm a card with that
   name appears in the configured list in Trello, then run
   `curl -i "http://127.0.0.1:8000/issues/<new ID>"` and expect HTTP 200
   with the same body.
6. Repeat the request with `-d '{"title": ""}'`. Expect HTTP 422 and
   confirm no new card appears in Trello.
7. Rename the step 2 test card through the service:

   ```bash
   curl -i -X PATCH "http://127.0.0.1:8000/issues/$TRELLO_TEST_CARD_ID" \
     -H "Content-Type: application/json" \
     -d '{"title": "HW1 PATCH test"}'
   ```

   Expect HTTP 200 with the same ID and the new title. Confirm in Trello
   that the card's name changed, then repeat the step 2 `GET` and expect
   the new title.

   Check that a stale overwrite is rejected. Copy the `ETag` header from
   the step 2 `GET` (repeat it now), then send two updates with it:

   ```bash
   curl -i -X PATCH "http://127.0.0.1:8000/issues/$TRELLO_TEST_CARD_ID" \
     -H "Content-Type: application/json" -H 'If-Match: "<etag>"' \
     -d '{"title": "HW1 client A"}'
   curl -i -X PATCH "http://127.0.0.1:8000/issues/$TRELLO_TEST_CARD_ID" \
     -H "Content-Type: application/json" -H 'If-Match: "<etag>"' \
     -d '{"title": "HW1 client B"}'
   ```

   Expect HTTP 200 for the first and HTTP 412 with
   `{"error":{"code":"precondition_failed","message":"The issue has changed since it was read"}}`
   for the second. Confirm in Trello that the card is named `HW1 client A`.
8. Delete the disposable test card through the service:

   ```bash
   curl -i -X DELETE "http://127.0.0.1:8000/issues/$TRELLO_TEST_CARD_ID"
   ```

   Expect HTTP 204 with an empty body. Confirm the card is gone in Trello.
   Repeat GET and DELETE for the same ID; both should return HTTP 404 and
   `{"error":{"code":"not_found","message":"Issue not found"}}`.
9. Check that a real provider failure is translated. Stop the server and
   restart it with an invalid token, keeping the key from `.env`:

   ```bash
   TRELLO_TOKEN=invalid-token uv run --locked uvicorn app.main:app --env-file .env
   ```

   Request the issue created in step 5, which still exists:
   `curl -i "http://127.0.0.1:8000/issues/<new ID>"`. Expect HTTP 403 and
   `{"error":{"code":"access_denied","message":"The issue tracker denied access to this issue"}}`.
   The server terminal should show one log line like
   `{"level": "WARNING", "operation": "get_issue", "error": "access_denied", "status": 403, "provider_involved": true, "cause": "provider_status=401", "write_outcome": null}`,
   with no key, token, URL, or stack trace.
10. Delete any remaining disposable cards (including the card created in
   step 5) and any board created only for this check.
   Revoke the token from the test account's Settings > Applications page
   when finished, stop the server, clear credentials from `.env`, and run
   `unset TRELLO_API_KEY TRELLO_TOKEN TRELLO_LIST_ID` if you also exported them.

A GET cache hit makes no provider request. A cache miss makes one [Get a Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-get)
request for `id,name`. Each POST makes one
[Create a new Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-post)
request with `idList` and `name`. Each PATCH makes one
[Update a Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-put)
request (a conditional PATCH adds one Get a Card request first). Each DELETE makes one
[Delete a Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-delete)
request and ignores the successful response body. All four use a 10-second
socket timeout with no automatic retries. Invalid requests or missing
configuration can fail before any provider request. See [the API contract](docs/api.md)
for errors and supported IDs.

## Linear Verification

The Linear adapter currently implements GET and POST; add steps here as
PATCH and DELETE are implemented. Use the documented single-process startup
command for this workflow so all requests share the same cache.

1. In the test team on the Linear platform, create an issue named `HW1 Linear GET test`. Linear
   shows its short identifier (for example `TYL-5`); however the service accepts
   only the full ID. With `LINEAR_API_KEY` exported as in
   [Linear Configuration](#linear-configuration), look it up, replacing `short-id` with your
   identifier:

   ```bash
   curl -s https://api.linear.app/graphql \
     -H "Content-Type: application/json" -H "Authorization: $LINEAR_API_KEY" \
     -d '{"query": "{ issue(id: \"short-id\") { id title } }"}'
   ```

   Use the returned `id` (a UUID), rather than the short identifier.
2. Configure credentials as above and start the service. In another
   terminal, set `LINEAR_TEST_ISSUE_ID` to that full ID and run:

   ```bash
   curl -i "http://127.0.0.1:8000/issues/$LINEAR_TEST_ISSUE_ID"
   ```

   Expect HTTP 200 and exactly `{"id":"<your full issue ID>","title":"HW1 Linear GET test"}`.
3. TODO (once updateIssue operation is implemented)
   Rename the issue to `HW1 Linear renamed` in Linear. An immediate GET
   may still return the cached title. Wait at least 30 seconds after the
   rename, then repeat the request. Expect HTTP 200 with the new title and
   the same ID. Restarting the server also clears the cache.
4. Run `curl -i http://127.0.0.1:8000/issues/does-not-exist`, then repeat
   it with your issue's short identifier in place of `does-not-exist`.
   Expect HTTP 404 and
   `{"error":{"code":"not_found","message":"Issue not found"}}` for both.
   The log lines should show `"cause": "malformed_issue_id"` and
   `"provider_involved": false`: neither request reaches Linear.
5. Create an issue:

   ```bash
   curl -i -X POST http://127.0.0.1:8000/issues \
     -H "Content-Type: application/json" \
     -d '{"title": "HW1 Linear POST test"}'
   ```

   Expect HTTP 201, a `Location: /issues/<new ID>` header, and
   `{"id":"<new ID>","title":"HW1 Linear POST test"}`. Confirm an issue with
   that title appears in the configured team in Linear, then run
   `curl -i "http://127.0.0.1:8000/issues/<new ID>"` and expect HTTP 200
   with the same body.
6. Repeat the request with `-d '{"title": ""}'`. Expect HTTP 422 and
   confirm no new issue appears in Linear.
7. TODO (once deleteIssue operation is implemented for Linear):
   Delete the issue created in step 6 in Linear, which moves it to the
   trash. Wait at least 30 seconds after the step 6 GET, then repeat that
   GET. Expect HTTP 404 and
   `{"error":{"code":"not_found","message":"Issue not found"}}`, with
   `"cause": "issue_trashed"` in the log line.
8. Rename the step 2 test issue through the service:

   ```bash
   curl -i -X PATCH "http://127.0.0.1:8000/issues/$LINEAR_TEST_ISSUE_ID" \
     -H "Content-Type: application/json" \
     -d '{"title": "HW1 Linear PATCH test"}'
   ```

   Expect HTTP 503 and
   `{"error":{"code":"service_not_configured","message":"Renaming issues for Linear issue provider is not available yet"}}`.
   Confirm in Linear that the title did not change. Repeat with
   `curl -i -X DELETE "http://127.0.0.1:8000/issues/$LINEAR_TEST_ISSUE_ID"`
   and expect HTTP 503 with the message
   `Deleting issues for Linear issue provider is not available yet`.
   Confirm the issue still exists in Linear.
9. Repeat the step 6 request with
    `-H "Idempotency-Key: 3f2b1c9e-8d4a-4f6b-9c1e-2a7d5e8f0b13"`. Expect
    HTTP 503 and
    `{"error":{"code":"service_not_configured","message":"Idempotent creation is not available"}}`,
    and confirm no new issue appears in Linear.
10. Check that a real provider failure is translated. Stop the server and
    restart it with an invalid key, keeping the rest of `.env`:

    ```bash
    LINEAR_API_KEY=invalid-key uv run --locked uvicorn app.main:app --env-file .env
    ```

    Request the step 2 issue, which still exists:
    `curl -i "http://127.0.0.1:8000/issues/$LINEAR_TEST_ISSUE_ID"`. Expect
    HTTP 403 and
    `{"error":{"code":"access_denied","message":"The issue tracker denied access to this issue"}}`.
    The server terminal should show one log line like
    `{"level": "WARNING", "operation": "get_issue", "error": "access_denied", "status": 403, "provider_involved": true, "cause": "provider_status=401", "write_outcome": null}`,
    with no key, URL, or stack trace.
11. Delete any remaining test issues (including the step 2 issue) and any
    team created only for this check. Revoke the API key from
    **Settings > Account > Security & access** when finished, stop the
    server, clear the Linear values from `.env` (or set
    `ISSUE_PROVIDER=trello`), and run
    `unset LINEAR_API_KEY LINEAR_TEST_ISSUE_ID` if you also exported them.

## Deployment

The service runs on Render, described by Terraform in `infra/`. Pull requests
show the plan, and deploys are manual and approved. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for prerequisites, deploy, verify,
rollback, teardown, and the teammate walkthrough.

## Contributing

See [AGENTS.md](AGENTS.md) for the contribution and release workflow.
