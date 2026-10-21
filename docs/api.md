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
| `issue_id` | path | string | yes | The issue's `id`, exactly as the service returned it. The service does not define an ID format; the current Trello provider accepts only full card IDs (24 lowercase hexadecimal characters). |

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

The response also carries an `ETag` header naming the issue's current
version. Pass it to [Update an issue](#update-an-issue) as `If-Match` to
avoid overwriting someone else's change.

### Read freshness and caching

A successful GET may return a saved result for up to 30 seconds after its
provider fetch finishes. Repeated reads do not extend that lifetime.
Each application process holds at most 100 entries, evicting the least
recently used when full. Errors are not cached.

Cache hits make no provider request. Changes made directly in Trello,
including deletion or revoked permissions, may not be noticed until the
entry expires. A cached read can also succeed during a provider outage.
Missing configuration and provider errors are checked when a read reaches
the underlying provider; the Trello provider rejects malformed IDs before making a network request.

PATCH and DELETE discard the affected entry in the process handling the
write before contacting the provider, including when the write fails.
POST does not populate the cache. Other processes have independent caches
and may continue serving an older result until it expires. Restarting a
process clears its cache. There is no public cache-bypass parameter.

When checking the result of an uncertain write, account for this freshness
window or verify directly in Trello. See
[Cache behavior](providers.md#cache-behavior) for implementation details.

### Errors

All errors use the shape described in [Error model](#error-model).

| Situation | Status | Code |
| --- | --- | --- |
| No issue exists with the given `issue_id` | `404 Not Found` | `not_found` |
| Identifier the issue tracker cannot recognise (with Trello, anything other than a full card ID, such as a short card link) | `404 Not Found` | `not_found` |
| The issue tracker denies the service access | `403 Forbidden` | `access_denied` |
| Service credentials are missing | `503 Service Unavailable` | `service_not_configured` |
| The issue tracker cannot be reached, times out, is unavailable, or returns an unusable response | `502 Bad Gateway` | `provider_error` |

GET does not change provider data, so a caller may retry after a `502`. A `403` or
`503` requires operator action. A `404` requires a corrected ID.

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

{"error":{"code":"not_found","message":"Issue not found"}}
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

All errors use the shape described in [Error model](#error-model).

| Situation | Status | Code |
| --- | --- | --- |
| Body is missing, not JSON, or `title` is missing, not a string, empty, whitespace-only, or longer than 256 characters | `422 Unprocessable Content` | `invalid_input`, with `details` |
| Body contains a field other than `title` | `422 Unprocessable Content` | `invalid_input`, with `details` |
| The issue tracker denies the service access | `403 Forbidden` | `access_denied` |
| Service credentials are missing | `503 Service Unavailable` | `service_not_configured` |
| The service's target issue list is not configured | `503 Service Unavailable` | `service_not_configured` |
| The issue tracker cannot be reached, times out, is unavailable, rejects the request, or returns an unusable response | `502 Bad Gateway` | `provider_error` |

A `422` requires a corrected request body. A `403` or `503` requires
operator action. A configured target issue list that the issue tracker
rejects is currently reported as `502` `provider_error`; retrying will not
help until the operator corrects it.

Creation is **not** idempotent: after a `502`, especially a timeout, the
issue may already have been created. A caller who retries may create a
duplicate issue and should check for the issue first where that matters.

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

{"error":{"code":"invalid_input","message":"Request body is invalid","details":[{"field":"title","message":"String should have at least 1 character"}]}}
```

The reason phrase after `422` depends on the Python version running the
service; callers should rely on the status code.

## Update an issue

Change one or more fields of an existing issue and return the updated issue.
Fields left out of the request body are not changed.

| Item | Value |
| --- | --- |
| Method | `PATCH` |
| Route | `/issues/{issue_id}` |
| Changes state? | Yes. Updates the issue in the provider. |
| Success status | `200 OK` |

### Parameters

| Name | Location | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `issue_id` | path | string | yes | The issue's `id`, exactly as the service returned it. The service does not define an ID format; the current Trello provider accepts only full card IDs (24 lowercase hexadecimal characters). |
| `If-Match` | header | string | no | An `ETag` from an earlier GET or PATCH. The update is applied only if the issue still has that version. Omitted or `*` means update unconditionally. |

### Request body

A JSON object containing at least one of the fields below.

```json
{
  "title": "Renamed issue"
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | no | The new title. 1 to 256 characters, containing at least one non-whitespace character. Sent to the provider as given, without trimming. |

`title` is currently the only field that can be changed, and it follows the
same rule as in [Create an issue](#create-an-issue). Unknown fields are
rejected rather than ignored, so a typo cannot be silently dropped.

### Successful response

Status `200 OK`, with the updated issue in the same shape as
[Get an issue](#get-an-issue):

```json
{
  "id": "64f1c0a2b3d4e5f607182930",
  "title": "Renamed issue"
}
```

The response carries an `ETag` header with the issue's new version.

### Conditional updates

Two callers who read the same version cannot both overwrite it:

1. Both `GET` the issue and receive the same `ETag`.
2. The first `PATCH` with `If-Match: <etag>` succeeds and the issue gets a new `ETag`.
3. The second `PATCH` with the old `ETag` is rejected with `412` and changes nothing. That caller should `GET` again, review the change, and retry with the new `ETag`.

The version is a hash of the issue's public fields (`id`, `title`). It is
computed by the service, so it works with any provider. An update that sets
a title back to an earlier value restores the earlier version too; this
cannot overwrite a change the caller has not seen, because the public fields
are then identical.

**How the check and the write stay consistent:** the service reads the
current issue, compares versions, and writes, all while holding a lock that
every PATCH takes. Another PATCH through the same service process therefore
cannot run between the check and the write.

**Limits:**

- The lock lives in one process. Running several workers or instances lets
  their checks and writes interleave.
- Edits made directly in Trello between the check and the write (a window of
  one Trello request) are not detected. Trello offers no conditional write
  to close this gap.
- Every PATCH waits for the lock, so updates run one at a time per process.

### Errors

All errors use the shape described in [Error model](#error-model).

| Situation | Status | Code |
| --- | --- | --- |
| No issue exists with the given `issue_id` | `404 Not Found` | `not_found` |
| Identifier the issue tracker cannot recognise (with Trello, anything other than a full card ID, such as a short card link) | `404 Not Found` | `not_found` |
| `If-Match` was sent and the issue's current version differs | `412 Precondition Failed` | `precondition_failed` |
| Body is missing or not JSON, the object is empty, or `title` is `null`, not a string, empty, whitespace-only, or longer than 256 characters | `422 Unprocessable Content` | `invalid_input`, with `details` |
| Body contains a field other than `title` | `422 Unprocessable Content` | `invalid_input`, with `details` |
| The issue tracker denies the service access | `403 Forbidden` | `access_denied` |
| Service credentials are missing | `503 Service Unavailable` | `service_not_configured` |
| The issue tracker cannot be reached, times out, is unavailable, rejects the request, or returns an unusable response | `502 Bad Gateway` | `provider_error` |

The request body is validated before the identifier, so a request with both
an invalid body and a malformed ID returns `422`. Rejected requests never
reach the issue tracker.

Setting the same title again returns `200` and leaves the issue unchanged,
so repeating a successful request is safe. After a `502`, the update may or
may not have been applied; read the issue with `GET` before retrying. A
`403` or `503` requires operator action.

### Example

```bash
curl -i -X PATCH http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930 \
  -H "Content-Type: application/json" \
  -d '{"title": "Renamed issue"}'
```

```json
{"id": "64f1c0a2b3d4e5f607182930", "title": "Renamed issue"}
```

## Delete an issue

Permanently delete an existing issue from the provider. With Trello this
deletes the card, rather than archiving it.

| Item | Value |
| --- | --- |
| Method | `DELETE` |
| Route | `/issues/{issue_id}` |
| Parameters | `issue_id`: the issue's `id`, exactly as the service returned it (with the current Trello provider, a full 24-character lowercase hexadecimal card ID) |
| Request body | None required |
| Changes state? | Yes. Permanently removes the issue. |
| Success status | `204 No Content`, with an empty body |

### Errors and repetition

All errors use the shape described in [Error model](#error-model).

| Situation | Status | Code |
| --- | --- | --- |
| Missing issue, including an issue already deleted | `404 Not Found` | `not_found` |
| Identifier the issue tracker cannot recognise (with Trello, anything other than a full card ID, such as a short card link) | `404 Not Found` | `not_found` |
| The issue tracker denies the service access | `403 Forbidden` | `access_denied` |
| Service credentials are missing | `503 Service Unavailable` | `service_not_configured` |
| The issue tracker cannot be reached, times out, is unavailable, or rejects the request | `502 Bad Gateway` | `provider_error` |

The Trello provider rejects malformed IDs before making a network request.
A `403` or `503` requires operator action.

After a successful deletion, GET requests handled by the same process and
repeated DELETE requests return 404 when the provider confirms the issue
is missing. Another process may still serve its cached copy until expiry.
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

## Error model

Every failed request returns a JSON body of the same shape, whichever
operation failed and whatever caused it:

```json
{
  "error": {
    "code": "not_found",
    "message": "Issue not found"
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `error.code` | string | A stable, machine-readable error code from the table below. Callers should branch on this. |
| `error.message` | string | A short human-readable explanation. Its wording may change; do not parse it. |
| `error.details` | array | Present only for `invalid_input` caused by a rejected request body. Each entry is `{"field": ..., "message": ...}`. |

A `422` response lists every problem with the body:

```json
{
  "error": {
    "code": "invalid_input",
    "message": "Request body is invalid",
    "details": [
      {"field": "title", "message": "String should have at least 1 character"}
    ]
  }
}
```

`field` names the rejected body field.

### Error codes

| Code | Status | When it occurs | Can the caller retry? |
| --- | --- | --- | --- |
| `invalid_input` | `422 Unprocessable Content` | The request body is missing, not JSON, contains an unknown field, or breaks a field rule (such as the title rule). Also used with status `405` for an unsupported method on a known route. | No. Correct the request first. |
| `not_found` | `404 Not Found` | No issue exists with the given ID (including one already deleted), the ID is malformed, or the route does not exist. | No. |
| `access_denied` | `403 Forbidden` | The issue tracker rejected the service's credentials or does not allow it to access the issue. | No. The service operator must fix the credentials or permissions. |
| `service_not_configured` | `503 Service Unavailable` | The service is missing its issue tracker credentials, or (for creation) its target issue list. | No. The service operator must complete the configuration. |
| `precondition_failed` | `412 Precondition Failed` | A PATCH sent `If-Match` with a version that is no longer current. Nothing was changed. | Only after reading the issue again with GET and sending its new `ETag`. |
| `provider_error` | `502 Bad Gateway` | The issue tracker could not be reached, timed out, rate-limited the service, was unavailable, rejected the request for another reason, or returned a response the service could not use. | Reads: yes, after a short wait. Writes: only after checking with GET, because the change may already have been applied. |
| `internal_error` | `500 Internal Server Error` | An unexpected failure inside the service. This indicates a bug. | Possibly, but report it if it repeats. |

`provider_error` uses one code for every issue tracker failure, so callers
need to handle only one case. Its message says what kind of failure it was:

| Situation | Message |
| --- | --- |
| Could not connect (DNS failure, connection refused or reset) | `The issue tracker could not be reached` |
| No response within the 10-second timeout | `The issue tracker did not respond in time` |
| The issue tracker is rate limiting the service | `The issue tracker is rate limiting requests` |
| The issue tracker reported a server error | `The issue tracker is currently unavailable` |
| Any other rejection | `The issue tracker rejected the request` |
| A response missing required data, or with the wrong issue ID | `The issue tracker returned an unexpected response` |

The other messages are fixed: `Issue not found`, `Resource not found`,
`Request not supported`, `The issue tracker denied access to this issue`,
`Issue tracker credentials not configured`,
`Issue tracker target list not configured`, `Request body is invalid`, and
`Internal server error`.

The service never retries automatically. A failed write can still have been
applied if the issue tracker processed it and the response was lost, so the
retry guidance above is conservative for POST, PATCH and DELETE. POST is not
idempotent: retrying it can create a duplicate issue.

### What callers see and what stays internal

Error responses describe the failure in the service's terms. They never
contain provider names, provider error messages, provider status codes,
request URLs, credentials, or stack traces. A caller cannot tell which issue
tracker the service uses from an error.

Expected failures (every code except `internal_error`) are part of this
contract. `internal_error` means the service has a bug; its cause is
recorded only in the service log.

### Failure logging

The service writes one structured JSON line to the `app.errors` logger for
every failed request:

```json
{"level": "WARNING", "operation": "get_issue", "error": "not_found", "status": 404, "provider_involved": true, "cause": "provider_status=404", "write_outcome": null}
```

| Field | Description |
| --- | --- |
| `level` | `WARNING` for 4xx responses, `ERROR` for 5xx responses. |
| `operation` | The endpoint that failed: `get_issue`, `create_issue`, `update_issue`, `delete_issue`, or `unknown` for an unknown route. |
| `error` | The error code returned to the caller. |
| `status` | The HTTP status returned to the caller. |
| `provider_involved` | `true` only when the service attempted a request to the issue tracker. Malformed IDs, invalid bodies and missing configuration are rejected before that, so they log `false`. |
| `write_outcome` | `null` for reads. For POST, PATCH and DELETE: `not_applied` when the service rejected the request before sending it, or the issue tracker refused it (`not_found`, `access_denied`); `unknown` for `provider_error` and `internal_error`, because the request may have been processed even though no usable answer came back. |
| `cause` | A short internal reason, such as `provider_status=429`, `timeout`, `URLError`, `malformed_issue_id` or `missing_credentials`. It is `null` when nothing more specific is known. |

Logs never contain credentials, authorization data, request URLs,
request or response bodies, or exception messages from the issue tracker client.
Expected failures are logged without a stack trace.
Only `internal_error` includes one, because it is needed to find the bug.

The service has no request or correlation identifier.

## What callers can rely on

- A GET or PATCH `200` or POST `201` response always contains `id` and `title` and nothing else.
- Repeating a GET request does not change provider data. Results follow the [read freshness policy](#read-freshness-and-caching).
- Each `201` response means exactly one new issue was created. A `422` or `503` response means none was created.
- A `PATCH` changes only the fields included in the request body.
- A `PATCH` with a stale `If-Match` changes nothing and returns `412`.
- A successful DELETE returns `204` with no body and removes the issue.
- Every error response has the shape in [Error model](#error-model), and its `error.code` is one of the codes listed there. A failed operation is never reported with a success status.
- The routes, methods, and response shapes above will not change without an update to this document.

## Assumptions

These affect observable behavior and are not guaranteed by the assignment spec, so we are stating them here.

- **`id` is an opaque string.** Callers should treat it as an identifier to pass back to the service. They should not parse it or assume a format.
- **`title` is always present.** An issue without a title is not part of this contract.
- **Real data.** The example ID and title illustrate the response shape; they are not seeded resources. Supply a full card ID accessible to the configured Trello account. Card short links are not supported, ensuring the response ID equals the requested ID.
- **Provider translation.** GET and POST translate Trello's `id` into `id` and `name` into `title`. Successful GET results may be cached as described above. POST creates a Trello card. PATCH sends `title` as the card's `name` and returns the card as Trello reports it afterwards. DELETE permanently removes the Trello card and returns no provider data.
- **Created and updated titles.** POST and PATCH share one title rule (`IssueTitle` in `app/models.py`). The title is sent to the provider as given, without trimming. The response returns the title as the provider reports it after creation or update.
- **Title limit.** The 256-character limit is a service rule for a short title, not a provider limit. Any provider must accept titles at least that long.
- **Where new issues go.** New issues are created in a single target location chosen by the service operator (for Trello, the list named by `TRELLO_LIST_ID`). Callers cannot choose it. Support for more than one location would be added as a provider-independent field, not as a provider ID.
- **Only `id` and `title` are returned or accepted.** Other issue fields (status, description, assignee, and so on) are left out on purpose, because they are not needed for the smallest useful operation. We will add fields only when an operation needs them.
- **Domain vocabulary.** This API says "issue" and does not use any provider's terms. Provider details do not appear in responses, including error responses.
