# Provider Interface and Dependency Injection

## Purpose

The Issue Tracker Service uses a provider-independent interface to separate application logic from Trello-specific operations.

This allows application code to retrieve and delete issues using the service's domain contract without understanding Trello's API or response structure.

## Interface Contract

**File:** `app/interface.py`

**Interface:** `IssueProvider`

### Operation: `get_issue(issue_id: str) -> Issue`

- **Purpose:** Retrieve a single issue by its identifier.
- **Input:** `issue_id` — a string identifying the requested issue.
- **Return value:** An `Issue` object containing `id` and `title`.
- **External state:** Read-only. The operation does not modify external data.
- **Assumptions:** The caller supplies an issue identifier. A successful operation returns an issue using the service's domain model.

### Operation: `delete_issue(issue_id: str) -> None`

- **Purpose:** Permanently delete an existing issue.
- **Input:** `issue_id` — a string identifying the issue. The HTTP endpoint validates the full ID before calling the provider.
- **Return value:** `None` on success. The endpoint returns HTTP 204 with no body.
- **External state:** Removes the issue. For Trello this deletes, rather than archives, the card.
- **Errors:** A missing issue is HTTP 404; missing credentials are HTTP 503; provider rejection or network failure is HTTP 502, matching the current GET convention.
- **Assumptions:** Repeated deletion reports a missing issue. A provider failure can follow a completed deletion if the response is lost; no automatic retry occurs.

## Provider Implementation

**File:** `app/provider.py`

**Implementation:** `TrelloIssueProvider`

The Trello implementation is responsible for:

- Sending requests to the Trello REST API.
- Managing provider-specific authentication.
- Retrieving Trello card data.
- Permanently deleting cards using Trello's [Delete a Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-delete) operation. Trello's HTTP 200 success is translated into the service's HTTP 204, without parsing a response body.
- Translating Trello's `id` and `name` fields into the service's `Issue` model.
- Keeping provider-specific request and response structures hidden from application code.

## Provider Boundary

The endpoints in `app/main.py` receive an object satisfying the
`IssueProvider` interface and call `get_issue()` or `delete_issue()`.
They do not import or construct `TrelloIssueProvider`. Both provider methods
share request authentication, timeout, and error mapping in `app/provider.py`.

The interface describes the required methods; it is not an extra
processing step. The supplied provider performs the actual operation.

## Provider Selection

`app/dependencies.py` defines `get_issue_provider()`, which creates
and returns a `TrelloIssueProvider` during normal operation.

Both endpoints declare this dependency using
`Annotated[IssueProvider, Depends(get_issue_provider)]`.
FastAPI calls the supplier and passes the resulting object to the
endpoint when handling a request.

The concrete provider choice is kept in `app/dependencies.py`.
Trello authentication, requests, and response translation remain
in `app/provider.py`.

A new provider object is created for each request. It stores no
shared mutable state. The Trello implementation reads credentials
from environment variables when retrieving or deleting an issue.

## Testing

### Provider tests

`tests/test_provider.py` checks the provider's interface compatibility,
response translation, and handling of missing issues and invalid
responses. Its runtime protocol check confirms the required method
is present; it does not establish full behavioral correctness.

### Endpoint tests with simulated Trello responses

`tests/test_get_issue.py` exercises the endpoint and real Trello
implementation while replacing network calls with controlled
responses. These tests cover request construction, translation,
credentials, and provider failures without contacting Trello.

### Endpoint tests with supplied providers

`tests/test_injected_provider.py` supplies alternative implementations
through `app.dependency_overrides`.

These tests verify that:

- The endpoint uses the supplied provider and returns its result.
- Invalid IDs are rejected without calling the provider's retrieval method.
- A missing-issue error from the provider produces the expected 404 response.

The successful retrieval test removes Trello credentials and fails
if the real Trello retrieval method is called. Overrides and other
temporary changes are restored automatically by pytest's
`monkeypatch` fixture.

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

The interface contains two operations: issue retrieval and permanent deletion.
Trello remains the provider during normal operation, while tests
can supply controlled implementations without changing endpoint code.

No additional real provider or runtime provider-selection feature
is implemented.
