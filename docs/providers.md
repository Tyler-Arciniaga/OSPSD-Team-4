# Provider Interface and Dependency Injection

## Purpose

The Issue Tracker Service uses a provider-independent interface to separate application logic from Trello-specific operations.

This allows application code to retrieve, create, update, and delete issues using the service's domain contract without understanding Trello's API or response structure.

## Interface Contract

**File:** `app/interface.py`

**Interface:** `IssueProvider`

### Operation: `get_issue(issue_id: str) -> Issue`

- **Purpose:** Retrieve a single issue by its identifier.
- **Input:** `issue_id` — a string identifying the requested issue.
- **Return value:** An `Issue` object containing `id` and `title`.
- **External state:** Read-only. The operation does not modify external data.
- **Assumptions:** The caller supplies an issue identifier. A successful operation returns an issue using the service's domain model.

### Operation: `create_issue(title: str) -> Issue`

- **Purpose:** Create a single issue with the given title.
- **Input:** `title` — the new issue's title, already validated by the endpoint (1 to 256 characters, not whitespace-only).
- **Return value:** An `Issue` object containing the new issue's `id` and its `title` as stored by the provider.
- **External state:** Writes. Each successful call creates exactly one issue. The operation is not idempotent.
- **Assumptions:** The provider decides where the issue is created from its own configuration; the caller does not supply a provider-specific location.

### Operation: `update_issue(issue_id: str, changes: IssueUpdate) -> Issue`

- **Purpose:** Apply a partial update to a single issue.
- **Input:** `issue_id` — a string identifying the issue; `changes` — an `IssueUpdate` containing only the fields to change (currently `title`). `IssueUpdate` is validated before the provider is called, so it always contains at least one field, and `title` follows the same rule as `create_issue()` (1 to 256 characters, not whitespace-only).
- **Return value:** The updated `Issue`, containing `id` and `title`.
- **External state:** Modifies the issue in the provider. Fields not present in `changes` are left as they are.
- **Assumptions:** A successful operation returns the issue as the provider reports it after the update, not a copy built from the request.

### Operation: `delete_issue(issue_id: str) -> None`

- **Purpose:** Permanently delete an existing issue.
- **Input:** `issue_id` — a string identifying the issue, passed through unchanged by the endpoint.
- **Return value:** `None` on success. The endpoint returns HTTP 204 with no body.
- **External state:** Removes the issue. For Trello this deletes, rather than archives, the card.
- **Errors:** A missing issue raises `not_found` (HTTP 404); missing credentials raise `service_not_configured` (HTTP 503); a provider rejection or network failure raises `access_denied` or `provider_error`, as for the other operations. See [Errors](#errors).
- **Assumptions:** Repeated deletion reports a missing issue. A provider failure can follow a completed deletion if the response is lost; no automatic retry occurs.

### Optional extension: `IdempotentIssueProvider`

Idempotent creation ([API contract](api.md#idempotent-creation)) needs two
more operations, declared in `app/interface.py` as `IdempotentIssueProvider`.
A provider that lacks them still serves every request without an
`Idempotency-Key`; a request with a key is refused with `503` rather than
silently treated as non-idempotent.

- **`create_tagged_issue(title, creation_token) -> Issue`:** like
  `create_issue()`, but marks the issue with an opaque 64-character
  hexadecimal token. On failure it must set `ServiceError.may_have_applied`
  to `False` only when the issue certainly was not created.
- **`find_tagged_issue(creation_token) -> Issue | None`:** a read that
  returns the oldest issue carrying the token, or `None`.

The token is a SHA-256 hash of the key and its scope. Providers never see
the caller's key.

The idempotency logic lives in `app/idempotency.py`, not in the provider:
`IdempotencyStore` keeps keys durably in SQLite, and
`create_issue_idempotently()` decides between creating, replaying,
refusing an overlap and recovering an unknown outcome.

## Errors

**File:** `app/errors.py`

Providers report every expected failure by raising `ServiceError`, with an
`ErrorCode` and a caller-facing message written in the service's terms.
Provider exceptions, provider error messages and provider response bodies
must not leave the provider. The endpoints in `app/main.py` turn a
`ServiceError` into the response described in
[the API error model](api.md#error-model) and log it.

A `ServiceError` also carries two values that are only logged:

- `provider_involved`: set to `True` only when a request to the provider was
  attempted. Configuration checks that fail before any request leave it
  `False`.
- `may_have_applied`: whether a failed write might still have taken effect.
  A provider sets it to `False` only when it knows nothing was written.
  When it is not set, `provider_error` and `internal_error` count as `True`
  and every other code as `False`. It decides the logged `write_outcome`
  and, for creation with an idempotency key, whether the key is released at
  once (`False`) or held so the next attempt looks the issue up first
  (`True`).
- `cause`: a short internal reason, such as `provider_status=429` or
  `timeout`. It must never contain credentials, URLs, provider messages or
  response bodies.

| Failure | Code | Status |
| --- | --- | --- |
| The issue does not exist, or the ID is not one this provider can recognise | `not_found` | 404 |
| The provider rejects the service's credentials or permissions | `access_denied` | 403 |
| Provider configuration is missing | `service_not_configured` | 503 |
| Any other failed provider request or unusable provider response | `provider_error` | 502 |

### Trello mapping

`TrelloIssueProvider` translates failures as follows. GET, PATCH and DELETE
share `_send_card_request()`, and GET and PATCH convert the returned card
with `_card_to_issue()`; `create_issue()` sends its own request. Both use
`_translate_http_error()` for HTTP errors and `_translate_request_failure()`
for everything else.

| Trello failure | Code | Logged `cause` |
| --- | --- | --- |
| ID for GET, PATCH or DELETE is not a full card ID (24 lowercase hexadecimal characters); checked before credentials, without a request | `not_found` | `malformed_issue_id` |
| `TRELLO_API_KEY` or `TRELLO_TOKEN` missing | `service_not_configured` | `missing_credentials` |
| `TRELLO_LIST_ID` missing (creation only) | `service_not_configured` | `missing_target_list` |
| HTTP 404 for GET, PATCH or DELETE | `not_found` | `provider_status=404` |
| HTTP 404 for creation (the configured list is wrong, not a missing issue) | `provider_error` | `provider_status=404` |
| HTTP 401 or 403 | `access_denied` | `provider_status=<status>` |
| HTTP 429 | `provider_error` | `provider_status=429` |
| HTTP 5xx | `provider_error` | `provider_status=<status>` |
| Any other HTTP error | `provider_error` | `provider_status=<status>` |
| Timeout, raised directly or wrapped in `URLError` | `provider_error` | `timeout` |
| Other connection failure (`URLError`, `OSError`) | `provider_error` | exception class name |
| Invalid JSON, missing `id` or `name`, or an unexpected card ID | `provider_error` | exception class name |

For creation, `create_issue()` and `create_tagged_issue()` share
`_create_card()` and also set `may_have_applied`:

| Trello creation failure | `may_have_applied` |
| --- | --- |
| HTTP 4xx (including 401, 403, 404 and 429): Trello rejected the request | `False` |
| HTTP 5xx | `True` |
| DNS failure or refused connection: the request never reached Trello | `False` |
| Timeout, reset or other connection failure | `True` |
| Success status with an unreadable body: the card exists | `True` |
| Missing credentials or target list: no request sent | `False` |

`create_tagged_issue()` writes `issue-service-creation-token:<token>` as the
card's description. `find_tagged_issue()` reads
`GET /1/lists/{TRELLO_LIST_ID}/cards?fields=id,name,desc&filter=all`, so
archived cards are found too, and returns the matching card with the
smallest ID. Trello IDs begin with their creation time, so that is the
oldest. A line in the description must equal the marker exactly. The
lookup's failures are translated like creation's.

Only the HTTP status or the exception's class name is kept. Exception
messages and response bodies are discarded, because they may contain
request URLs or other provider details.

## Provider Implementation

**File:** `app/provider.py`

**Implementation:** `TrelloIssueProvider`

The Trello implementation is responsible for:

- Sending requests to the Trello REST API.
- Managing provider-specific authentication.
- Retrieving Trello card data.
- Rejecting IDs that cannot be a full Trello card ID as `not_found` before any request. Issue IDs are opaque to the endpoints, so this format rule belongs to the provider; another provider can accept UUIDs or numeric IDs without changes to the API.
- Creating Trello cards in the list named by the `TRELLO_LIST_ID` environment variable.
- Updating Trello cards (`title` is sent as the card's `name` with Trello's Update a Card `PUT` request).
- Permanently deleting cards using Trello's [Delete a Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-delete) operation. Trello's HTTP 200 success is translated into the service's HTTP 204, without parsing a response body.
- Translating Trello's `id` and `name` fields into the service's `Issue` model.
- Keeping provider-specific request and response structures hidden from application code.

## Provider Boundary

The endpoints in `app/main.py` receive an object satisfying the
`IssueProvider` interface. `GET /issues/{issue_id}` calls `get_issue()`,
`POST /issues` calls `create_issue()`, `PATCH /issues/{issue_id}` calls
`update_issue()`, and `DELETE /issues/{issue_id}` calls `delete_issue()`.
They do not import or construct `TrelloIssueProvider`.
All four provider methods share the credential check in `app/provider.py`.
The key and token are sent in an `Authorization` header
(`OAuth oauth_consumer_key="...", oauth_token="..."`, as described in
[Trello's authorization guide](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)),
never in the query string, so they do not appear in request URLs.
`get_issue()`, `update_issue()`, and `delete_issue()` also share request
construction. All four share error translation, except that a Trello 404
during creation means the configured list is wrong, not that an issue is
missing, so it is reported as `provider_error` (502).

The interface describes the required methods; it is not an extra
processing step. The supplied provider performs the actual operation.

## Provider Selection

`app/dependencies.py` defines the application's `lifespan()` function.
`app/main.py` connects it with `FastAPI(lifespan=lifespan, ...)`.
At startup it creates a `TrelloIssueProvider`, wraps it in a
`CachedIssueProvider`, and saves the wrapper in `app.state.issue_provider`.
Shutdown removes that reference; the next startup creates an empty cache.

All four endpoints declare
`Annotated[IssueProvider, Depends(get_issue_provider)]`.
For each request, FastAPI supplies a `Request` to `get_issue_provider()`,
which returns `request.app.state.issue_provider`. Requests handled by the
same application process therefore share one wrapper and its cache.
Tests can still override this dependency to supply another implementation.

The concrete provider choice and cache settings stay in
`app/dependencies.py`. Trello authentication, requests, and response
translation remain in `app/provider.py`. The Trello implementation reads
credentials and, for creation, the target list from the environment when
called. A cache hit does not call Trello or recheck credentials. Restart
the application after configuration changes to discard previously cached data.

Startup also saves an `IdempotencyStore` in `app.state.idempotency_store`,
using the file named by `IDEMPOTENCY_DB_PATH` (default
`idempotency.sqlite3`). `POST /issues` receives it through
`get_idempotency_store()`. Unlike the cache, the store keeps nothing in
memory: every process using the same file shares the same keys, and a
restart forgets none of them.

## Cache Behavior

**File:** `app/cache.py`  
**Implementation:** `CachedIssueProvider`

The wrapper supports the same four methods as `IssueProvider`, so endpoints
need no cache-specific code. Normal operation uses a 30-second lifetime
and a limit of 100 entries, configured at startup.

| Operation | Cache behavior |
| --- | --- |
| GET hit | Return a separate copy of the saved issue and mark it most recently used. |
| GET miss or expired entry | Call the underlying provider and cache a copy only if retrieval succeeds. |
| PATCH | Remove the issue's entry before forwarding the update; do not cache the update response. |
| DELETE | Remove the issue's entry before forwarding deletion. |
| POST | Forward creation without reading or changing the cache. |
| POST with `Idempotency-Key` | Forward `create_tagged_issue()` and `find_tagged_issue()` uncached, so recovery always sees the provider's current cards. If the wrapped provider lacks them, raise `service_not_configured` before any request. |

Entries are keyed by issue ID in an `OrderedDict`. Each contains an
expiration time and an `Issue`. Expiration uses `monotonic()` and starts
when retrieval finishes. Hits do not extend it. Each GET checks only its
requested entry and removes it if expired; there is no full-cache scan or
background cleanup task. Other expired entries remain until accessed or evicted.
When capacity is exceeded, the least recently used entry is removed.
The limit counts entries, not bytes.

Copies on storage and on cache hits prevent callers from modifying the
saved object. Exceptions propagate normally and are never cached.
Invalidation happens before writes because Trello might apply a change
even if its response fails to reach the service.

A fixed pool of 64 striped locks coordinates GET, PATCH, and DELETE.
`hash(issue_id) % 64` selects the stripe, which stays locked for the whole
operation, including the provider call. Operations on the same ID take turns;
different IDs can proceed concurrently unless they share a stripe.
A separate short lock protects every access to the shared `OrderedDict`
and its LRU order. It is never held during provider calls. Locks are always
acquired in stripe-then-dictionary order to avoid deadlocks.
POST acquires neither lock because it does not change the cache.

The cache is in memory and local to one application process. Workers do
not share entries or invalidations. Direct Trello changes and permission
revocations can remain unnoticed while an entry is valid. A cache hit can
succeed even while Trello is unavailable; after expiration the service
must fetch again and may return a provider error. See
[Read freshness and caching](api.md#read-freshness-and-caching).

## Testing

The shared client fixture in `tests/conftest.py` uses `with TestClient(...)` so application startup
and shutdown run for each test and cached data does not leak between tests.
Error tests override the `raise_server_exceptions` fixture to inspect 500
responses; other tests keep unexpected exceptions visible.
Tests that override `get_issue_provider()` still bypass the normal wrapper.

### Idempotency tests

`tests/test_idempotency.py` shows that a key never yields a second issue. Its
fake provider records cards on a shared in-memory board, so each test counts
the issues that really exist. The board and the SQLite file outlive the
provider and store objects, which is how a restart is simulated. It covers:

- Repeats (replay with `Idempotency-Replayed`), distinct keys, requests
  without a key, and a reused key with a different body (`422`, nothing
  created, original still replayed).
- Overlap: a second request while the first is in the provider gets `409`
  with `Retry-After`; 16 simultaneous attempts produce one provider call.
  A claim is shown to hold SQLite's write lock while it decides, so two
  claims can never both see an unused key.
- Unknown outcomes: a lost response, a timeout that created nothing, an
  unexpected exception, a failed lookup, and five lost responses in a row
  each end with one issue. Certain rejections release the key immediately.
- Restarts: replay after restart without contacting the provider; recovery
  after a crash before and after the provider write; a failed result write.
- Retention at 24 hours, purge of expired keys, header validation, and that
  the raw key is stored nowhere.
- With the real Trello provider and simulated HTTP: the card description
  marker, a lost response end to end with exactly one Trello `POST`, lookup
  matching, and the `may_have_applied` mapping above.
- Integration: with no overrides, startup supplies the cached Trello
  provider and a store at `IDEMPOTENCY_DB_PATH`, and the cache passes tagged
  calls through; a cache over a provider without the extension refuses keys;
  and the logged `write_outcome` matches whether the key was released.

Removing any one safeguard (the lookup, the lease, the certainty check, the
body fingerprint, the 5xx classification, or the locking transaction) makes
at least one of these tests fail.

### Cache tests

`tests/test_cache.py` uses fake providers and a controlled clock to check
cache hits, expiration without extending the lifetime on a hit,
least-recently-used eviction, and isolation from caller mutations.
It also checks invalidation for updates and deletions, including failed
writes with uncertain outcomes, and that failed reads are not cached.
An HTTP-level test verifies that requests reuse the startup-created cache
and a new application lifespan starts empty. These tests require neither
Trello access nor waits for TTL expiration. Threaded tests use events to
verify that slow fetches do not block hits or misses on other stripes,
concurrent reads share a fetch, and updates/deletions wait for an in-flight
read before invalidating its result. Timeouts bound test waits; no sleeps
are used to arrange thread ordering.

### Provider tests

`tests/test_provider.py` checks the provider's interface compatibility,
response translation for retrieval and creation, and handling of
missing issues and invalid responses. Its runtime protocol check confirms the required method
is present; it does not establish full behavioral correctness.

### Endpoint tests with simulated Trello responses

`tests/test_get_issue.py` exercises the endpoint and real Trello
implementation while replacing network calls with controlled
responses. These tests cover request construction, translation,
credentials, and provider failures without contacting Trello.

`tests/test_create_issue.py` does the same for `POST /issues`,
including the request sent to Trello, the `201` response and
`Location` header, a missing target issue list, and provider failures.

### Endpoint tests with supplied providers

`tests/test_injected_provider.py` supplies alternative implementations
through `app.dependency_overrides`.

These tests verify that:

- The endpoints use the supplied provider and return its result.
- GET, PATCH and DELETE pass any ID, such as a UUID or a number, to the
  supplied provider unchanged: the endpoints define no ID format.
- Invalid creation bodies are rejected with 422 without calling the
  provider's creation method.
- A provider's `not_found` error produces the expected 404 response.
- A provider's creation failure is returned in the service's error shape.

Every fake provider in these tests implements all four interface methods.
Methods a test should not use raise `AssertionError`.

The successful retrieval and creation tests remove Trello credentials
and fail if the corresponding real Trello method is called. Overrides and other
temporary changes are restored automatically by pytest's
`monkeypatch` fixture.

### Update endpoint tests

`tests/test_update_issue.py` covers `PATCH /issues/{issue_id}` in both styles:

- With the real Trello implementation and simulated responses: the `PUT`
  request's method, path, query parameters, `Authorization` header, and timeout; titles at and over
  the 256-character limit; titles sent without trimming; malformed IDs;
  invalid bodies (`422`); Trello `404`, `401`/`403`, rate limiting, server
  errors, network failures and timeouts, and invalid responses; and missing
  credentials.
- With supplied providers: the endpoint passes the ID and validated changes
  to `update_issue()` without touching Trello; rejected bodies never reach
  the provider; and a provider's `not_found` error becomes a `404`.
- `test_create_and_update_share_title_rule` checks that `IssueCreate` and
  `IssueUpdate` accept and reject the same titles.

### Error model tests

`tests/test_errors.py` checks the error contract itself, mostly through a
controlled provider whose operations return or raise a chosen outcome:

- One successful request, one invalid request (`422` with `details`), one
  missing issue (`404`), and one provider failure (`502`), each comparing
  the full response body.
- Access denial (`403`), an unexpected exception (`500` with no internal
  details in the body), and an unknown route (`404`).
- Each failure writes exactly one log line, and the test compares the whole
  record: level, operation, error code, status, `provider_involved` and
  `cause`. Only the unexpected exception includes a stack trace.
- Real Trello failures whose URLs and exception messages contain the key and
  token produce responses and logs containing neither, nor the word
  "Trello".
- Missing configuration and malformed IDs are logged with
  `provider_involved` set to `false`.

Expected error bodies that several endpoint test files share are defined
once in `tests/expected_errors.py`. `tests/test_errors.py` writes its
expected bodies out in full, so the exact contract is checked independently
of those helpers.

### DELETE testing strategy

`tests/test_delete_issue.py` checks the following public guarantees:

- Success returns HTTP 204 with no body and sends exactly one authenticated Trello DELETE, with no preliminary GET. Empty and JSON provider success bodies are both accepted, and the connection is closed.
- Malformed IDs (short links, uppercase, and IDs one character shorter or longer than required) return 404 without calling Trello.
- Missing credentials, provider rejection, a missing card, and network failures produce the documented errors without leaking provider details. Error responses are closed.
- A supplied stateful provider supports GET → DELETE → GET and repeated DELETE: the issue is removed once, then both operations report 404. This test runs without credentials.
- A provider that deletes the card then loses the response produces 502 with no retry. This demonstrates the documented uncertain outcome.
- The generated OpenAPI contract has no DELETE request body or 204 response body.

Run these cases with `uv run --locked pytest tests/test_delete_issue.py`.
Existing GET tests protect retrieval behavior after sharing the request code.
Offline tests establish service behavior, not actual Trello permissions or
post-deletion state. Verify those separately using the README procedure.

Defect check: replacing `provider._require_card_id` with a no-op in a
separate Python process made 25 tests fail, including
`test_delete_invalid_id_never_contacts_trello` and
`test_trello_rejects_malformed_id_without_a_request`. No source was changed
for that experiment; the tests pass with the real check.

### Live Trello verification

Offline tests do not establish that real credentials and Trello
access work. Follow [Trello Verification](../README.md#trello-verification)
to verify the normally configured service against a real test card.

## Current Scope

The interface contains four operations: issue retrieval, issue creation,
partial issue update (only `title` can currently be updated), and permanent
deletion.
Trello remains the provider during normal operation, while tests
can supply controlled implementations without changing endpoint code.

No additional real provider or runtime provider-selection feature
is implemented.


### Fresh reads for conditional updates

`IssueProvider.get_issue(issue_id, fresh=True)` requires retrieval from the
underlying provider instead of a saved result. The default remains `False`,
so ordinary GET requests continue to use the cache. Trello reads are always
fresh; in-memory test providers return their current state.

Before comparing an `If-Match` version, PATCH requests a fresh read. The cache
removes the old entry, forwards the fresh-read requirement, and caches the
successful response. A failed fresh read leaves the old entry invalidated.
This detects changes already present in Trello when the comparison read runs.
It does not make the separate provider read and write atomic: external changes
between them and writes from other service processes remain a limitation.
