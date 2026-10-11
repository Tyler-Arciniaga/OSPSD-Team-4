
# Provider-Independent Interface (Level 3)

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

The application follows this structure:

```text
HTTP Request
    |
    v
FastAPI Application (main.py)
    |
    v
IssueProvider Interface (interface.py)
    |
    v
TrelloIssueProvider (provider.py)
    |
    v
Trello REST API
```

The application operates on the `Issue` domain model, while the provider implementation handles Trello-specific details.

## Testing

Provider tests are located in `tests/test_provider.py`.

They verify:

- Compliance with the `IssueProvider` contract.
- Translation of Trello responses into the `Issue` model.
- Handling of missing issues and invalid provider responses.

Existing endpoint tests remain in `tests/test_get_issue.py`.

## Current Scope

The interface contains only one operation because issue retrieval is the only provider behavior required at this stage.

Level 3 does not require runtime provider replacement or dependency injection. Those capabilities will be introduced in Level 4.
