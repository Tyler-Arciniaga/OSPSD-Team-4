# Provider Interface and Dependency Injection

## Purpose

The Issue Tracker Service uses a provider-independent interface to separate application logic from Trello-specific operations.

This allows application code to retrieve issues using the service's domain model without understanding Trello's API or response structure.

## Interface Contract

**File:** `app/interface.py`

**Interface:** `IssueProvider`

### Operation: `get_issue(issue_id: str) -> Issue`

- **Purpose:** Retrieve a single issue by its identifier.
- **Input:** `issue_id` — a string identifying the requested issue.
- **Return value:** An `Issue` object containing `id` and `title`.
- **External state:** Read-only. The operation does not modify external data.
- **Assumptions:** The caller supplies an issue identifier. A successful operation returns an issue using the service's domain model.

## Provider Implementation

**File:** `app/provider.py`

**Implementation:** `TrelloIssueProvider`

The Trello implementation is responsible for:

- Sending requests to the Trello REST API.
- Managing provider-specific authentication.
- Retrieving Trello card data.
- Translating Trello's `id` and `name` fields into the service's `Issue` model.
- Keeping provider-specific request and response structures hidden from application code.

## Provider Boundary

The endpoint in `app/main.py` receives an object satisfying the
`IssueProvider` interface and calls its `get_issue()` method.
It does not import or construct `TrelloIssueProvider`.

The interface describes the required method; it is not an extra
processing step. The supplied provider performs the actual retrieval
and returns the service's `Issue` model.

## Provider Selection

`app/dependencies.py` defines `get_issue_provider()`, which creates
and returns a `TrelloIssueProvider` during normal operation.

The endpoint declares this dependency using
`Annotated[IssueProvider, Depends(get_issue_provider)]`.
FastAPI calls the supplier and passes the resulting object to the
endpoint when handling a request.

The concrete provider choice is kept in `app/dependencies.py`.
Trello authentication, requests, and response translation remain
in `app/provider.py`.

A new provider object is created for each request. It stores no
shared mutable state. The Trello implementation reads credentials
from environment variables when retrieving an issue.

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

### Live Trello verification

Offline tests do not establish that real credentials and Trello
access work. Follow [Trello Verification](../README.md#trello-verification)
to verify the normally configured service against a real test card.

## Current Scope

The interface contains one operation: issue retrieval.
Trello remains the provider during normal operation, while tests
can supply controlled implementations without changing endpoint code.

No additional real provider or runtime provider-selection feature
is implemented.
