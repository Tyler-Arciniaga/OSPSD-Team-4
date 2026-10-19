# Provider Interface and Dependency Injection

## Purpose

The Issue Tracker Service uses a provider-independent interface to separate application logic from Trello-specific operations.

This allows application code to retrieve, create, and delete issues using the service's domain contract without understanding Trello's API or response structure.

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
- **Input:** `issue_id` — a string identifying the issue. The HTTP endpoint validates the full ID before calling the provider.
- **Return value:** `None` on success. The endpoint returns HTTP 204 with no body.
- **External state:** Removes the issue. For Trello this deletes, rather than archives, the card.
- **Errors:** A missing issue raises `not_found` (HTTP 404); missing credentials raise `service_not_configured` (HTTP 503); a provider rejection or network failure raises `access_denied` or `provider_error`, as for the other operations. See [Errors](#errors).
- **Assumptions:** Repeated deletion reports a missing issue. A provider failure can follow a completed deletion if the response is lost; no automatic retry occurs.

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
- `cause`: a short internal reason, such as `provider_status=429` or
  `timeout`. It must never contain credentials, URLs, provider messages or
  response bodies.

| Failure | Code | Status |
| --- | --- | --- |
| The issue does not exist | `not_found` | 404 |
| The provider rejects the service's credentials or permissions | `access_denied` | 403 |
| Provider configuration is missing | `service_not_configured` | 503 |
| Any other failed provider request or unusable provider response | `provider_error` | 502 |

### Trello mapping

`TrelloIssueProvider` translates failures as follows. GET, PATCH and DELETE
share `_request_card()`; `create_issue()` sends its own request. Both use
`_translate_http_error()` for HTTP errors and `_translate_request_failure()`
for everything else.

| Trello failure | Code | Logged `cause` |
| --- | --- | --- |
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

Only the HTTP status or the exception's class name is kept. Exception
messages are discarded, because a Trello request URL carries the API key
and token in its query string.

## Provider Implementation

**File:** `app/provider.py`

**Implementation:** `TrelloIssueProvider`

The Trello implementation is responsible for:

- Sending requests to the Trello REST API.
- Managing provider-specific authentication.
- Retrieving Trello card data.
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
`get_issue()`, `update_issue()`, and `delete_issue()` also share request
construction. All four share error translation, except that a Trello 404
during creation means the configured list is wrong, not that an issue is
missing, so it is reported as `provider_error` (502).

The interface describes the required methods; it is not an extra
processing step. The supplied provider performs the actual operation.

## Provider Selection

`app/dependencies.py` defines `get_issue_provider()`, which creates
and returns a `TrelloIssueProvider` during normal operation.

All four endpoints declare this dependency using
`Annotated[IssueProvider, Depends(get_issue_provider)]`, so
all four use the same supplier and the same override mechanism.
FastAPI calls the supplier and passes the resulting object to the
endpoint when handling a request.

The concrete provider choice is kept in `app/dependencies.py`.
Trello authentication, requests, and response translation remain
in `app/provider.py`.

A new provider object is created for each request. It stores no
shared mutable state. The Trello implementation reads credentials
(and, for creation, the target issue list) from environment variables on
each call.

## Testing

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
- Invalid IDs are rejected without calling the provider's retrieval method.
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
  request's method, path, query parameters, and timeout; titles at and over
  the 256-character limit; titles sent without trimming; malformed IDs;
  invalid bodies (`422`); Trello `404`, `401`/`403`, rate limiting, server
  errors, network failures and timeouts, and invalid responses; and missing
  credentials.
- With supplied providers: the endpoint passes the ID and validated changes
  to `update_issue()` without touching Trello; rejected requests never reach
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
- Malformed IDs (short links, uppercase, and IDs one character shorter or longer than required) return 404 without calling Trello or an injected provider.
- Missing credentials, provider rejection, a missing card, and network failures produce the documented errors without leaking provider details. Error responses are closed.
- A supplied stateful provider supports GET → DELETE → GET and repeated DELETE: the issue is removed once, then both operations report 404. This test runs without credentials.
- A provider that deletes the card then loses the response produces 502 with no retry. This demonstrates the documented uncertain outcome.
- The generated OpenAPI contract has no DELETE request body or 204 response body.

Run these cases with `uv run --locked pytest tests/test_delete_issue.py`.
Existing GET tests protect retrieval behavior after sharing the request code.
Offline tests establish service behavior, not actual Trello permissions or
post-deletion state. Verify those separately using the README procedure.

Defect check: replacing `main.require_valid_issue_id` with a no-op in a
separate Python process made
`test_delete_invalid_id_does_not_call_injected_provider` fail with
`AssertionError: Invalid IDs must not reach the provider`. No source was
changed for that experiment; the test passes with the real validation.

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
