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

## Create an issue

Create one issue with the given title.

| Item | Value |
| --- | --- |
| Method | `POST` |
| Route | `/issues` |
| Changes state? | Yes. Each successful request creates one new issue. |
| Success status | `201 Created` |

### Request body

A JSON object:

```json
{
  "title": "Example issue"
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | yes | A short human-readable title. 1 to 256 characters, containing at least one non-whitespace character. |

`title` is the only accepted field. Requests containing any other field are rejected.

### Successful response

Status `201 Created`, with a `Location` header naming the new issue
(for example, `Location: /issues/64f1c0a2b3d4e5f607182930`) and a JSON body:

```json
{
  "id": "64f1c0a2b3d4e5f607182930",
  "title": "Example issue"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | The new issue's identifier. Pass it to [Get an issue](#get-an-issue) to retrieve the issue. |
| `title` | string | The issue's title as stored by the service. |

As with retrieval, these two fields are the whole public contract.

### Errors

| Situation | Status | Body |
| --- | --- | --- |
| Body is missing, not JSON, or `title` is missing, not a string, empty, whitespace-only, or longer than 256 characters | `422 Unprocessable Content` | FastAPI's validation error format (`{"detail": [...]}`) |
| Body contains a field other than `title` | `422 Unprocessable Content` | FastAPI's validation error format (`{"detail": [...]}`) |
| Service credentials are missing | `503 Service Unavailable` | `{"detail": "Trello credentials not configured"}` |
| The service's target issue list is not configured | `503 Service Unavailable` | `{"detail": "Trello list not configured"}` |
| Trello rejects the request, cannot be reached, times out, or returns an invalid response | `502 Bad Gateway` | `{"detail": "Trello request failed"}` |

These are initial integration errors, not the final Level 5 error model.
A 422 requires a corrected request body. After a 503, the service operator
must configure credentials or the target issue list. Creation is **not**
idempotent: after a 502, especially a timeout, the issue may already have
been created. A caller who retries may create a duplicate issue and should
check for the issue first where that matters. Invalid credentials,
permissions, or a wrong target issue list require operator action.

### Example

```bash
curl -i -X POST http://127.0.0.1:8000/issues \
  -H "Content-Type: application/json" \
  -d '{"title": "Example issue"}'
```

```
HTTP/1.1 201 Created
location: /issues/64f1c0a2b3d4e5f607182930

{"id":"64f1c0a2b3d4e5f607182930","title":"Example issue"}
```

An empty title:

```bash
curl -i -X POST http://127.0.0.1:8000/issues \
  -H "Content-Type: application/json" \
  -d '{"title": ""}'
```

```
HTTP/1.1 422

{"detail":[{"type":"string_too_short","loc":["body","title"],"msg":"String should have at least 1 character","input":"","ctx":{"min_length":1}}]}
```

The reason phrase after `422` depends on the Python version running the
service; callers should rely on the status code.

## Delete an issue

Permanently delete an existing issue from the provider. With Trello this
deletes the card, rather than archiving it.

| Item | Value |
| --- | --- |
| Method | `DELETE` |
| Route | `/issues/{issue_id}` |
| Parameters | `issue_id`: full Trello card ID (24 lowercase hexadecimal characters) |
| Request body | None required |
| Changes state? | Yes. Permanently removes the issue. |
| Success status | `204 No Content`, with an empty body |

### Errors and repetition

| Situation | Status | Body |
| --- | --- | --- |
| Missing issue, including an issue already deleted | `404 Not Found` | `{"detail": "Issue not found"}` |
| Malformed identifier (including a short card link) | `404 Not Found` | `{"detail": "Issue not found"}` |
| Service credentials are missing | `503 Service Unavailable` | `{"detail": "Trello credentials not configured"}` |
| Trello rejects the request, cannot be reached, or times out | `502 Bad Gateway` | `{"detail": "Trello request failed"}` |

Malformed IDs are rejected before any provider operation. DELETE uses the
same initial error model as GET; the final Level 5 error model remains future
work. A 503 requires credential configuration; rejected credentials or
permissions require operator action.

After a successful deletion, GET and repeated DELETE requests return 404.
Repeated deletion does not restore or create an issue, although the status
changes from 204 to 404.

A 502 does not prove that the card still exists: Trello may have deleted it
before the response was lost. Check with GET before deciding whether to
retry. The service sends one DELETE request and never automatically retries
or makes a preliminary GET request.

### Example

Use a disposable test card; deletion is permanent.

```bash
curl -i -X DELETE http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930
```

```http
HTTP/1.1 204 No Content
```

## What callers can rely on

- A GET `200` or POST `201` response always contains `id` and `title` and nothing else.
- Repeating a GET request has no side effects.
- Each `201` response means exactly one new issue was created. A `422` or `503` response means none was created.
- A successful DELETE returns `204` with no body and removes the issue.
- The routes, methods, and response shapes above will not change without an update to this document.

## Assumptions

These affect observable behavior and are not guaranteed by the assignment spec, so we are stating them here.

- **`id` is an opaque string.** Callers should treat it as an identifier to pass back to the service. They should not parse it or assume a format.
- **`title` is always present.** An issue without a title is not part of this contract.
- **Real data.** The example ID and title illustrate the response shape; they are not seeded resources. Supply a full card ID accessible to the configured Trello account. Card short links are not supported, ensuring the response ID equals the requested ID.
- **Provider translation.** GET and POST translate Trello's `id` into `id` and `name` into `title`, without caching. POST creates a Trello card. DELETE permanently removes the Trello card and returns no provider data.
- **Created titles.** The title is sent to the provider as given, without trimming. The response returns the title as the provider reports it after creation.
- **Title limit.** The 256-character limit is a service rule for a short title, not a provider limit. Any provider must accept titles at least that long.
- **Where new issues go.** New issues are created in a single target location chosen by the service operator (for Trello, the list named by `TRELLO_LIST_ID`). Callers cannot choose it. Support for more than one location would be added as a provider-independent field, not as a provider ID.
- **Only `id` and `title` are returned or accepted.** Other issue fields (status, description, assignee, and so on) are left out on purpose, because they are not needed for the smallest useful operation. We will add fields only when an operation needs them.
- **Domain vocabulary.** This API says "issue" and does not use any provider's terms. Provider details do not appear in responses.
