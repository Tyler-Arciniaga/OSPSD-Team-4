# API Contract

This document is the contract for the public operations of the Issue Tracker Service.

## Get an issue

Return one issue by its identifier.

| Item | Value |
| --- | --- |
| Method | `GET` |
| Route | `/issues/{issue_id}` |
| Changes state? | No. Read-only operation. |
| Success status | `200 OK` |

### Parameters

| Name | Location | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `issue_id` | path | string | yes | The full Trello card ID of the issue to return (24 lowercase hexadecimal characters). |

### Successful response

Status `200 OK`, with a JSON body:

```json
{
  "id": "64f1c0a2b3d4e5f607182930",
  "title": "Example issue"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | The issue's identifier. The same value that was requested in the path parameter. |
| `title` | string | A short human-readable title for the issue. |

These two fields are the whole public contract. No other fields are promised, and callers must not depend on any others appearing.

### Errors

| Situation | Status | Body |
| --- | --- | --- |
| No issue exists with the given `issue_id` | `404 Not Found` | `{"detail": "Issue not found"}` |
| Malformed identifier (including a short card link) | `404 Not Found` | `{"detail": "Issue not found"}` |
| Service credentials are missing | `503 Service Unavailable` | `{"detail": "Trello credentials not configured"}` |
| Trello rejects the request, cannot be reached, times out, or returns an invalid response | `502 Bad Gateway` | `{"detail": "Trello request failed"}` |

These are initial integration errors, not the final Level 5 error model.
After a 503, the service operator must configure credentials. A caller may
retry a read after a transient 502; invalid credentials or permissions require
operator action. Missing or malformed identifiers require a corrected ID.

### Example

```bash
curl http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930
```

```json
{"id": "64f1c0a2b3d4e5f607182930", "title": "Example issue"}
```

An unknown identifier:

```bash
curl -i http://127.0.0.1:8000/issues/does-not-exist
```

```
HTTP/1.1 404 Not Found

{"detail":"Issue not found"}
```

## What callers can rely on

- A `200` response always contains `id` and `title` and nothing else.
- Repeating the same request has no side effects.
- The route, method, and response shape above will not change without an update to this document.

## Assumptions

These affect observable behavior and are not guaranteed by the assignment spec, so we are stating them here.

- **`id` is an opaque string.** Callers should treat it as an identifier to pass back to the service. They should not parse it or assume a format.
- **`title` is always present.** An issue without a title is not part of this contract.
- **Real data.** The example ID and title illustrate the response shape; they are not seeded resources. Supply a full card ID accessible to the configured Trello account. Card short links are not supported, ensuring the response ID equals the requested ID.
- **Provider translation.** Trello's `id` becomes `id`, and `name` becomes `title`. Each request reads current Trello data; the service does not cache or modify it.
- **Only `id` and `title` are returned.** Other issue fields (status, description, assignee, and so on) are left out on purpose, because they are not needed for the smallest useful operation. We will add fields only when an operation needs them.
- **Domain vocabulary.** This API says "issue" and does not use any provider's terms. Provider details do not appear in responses.
