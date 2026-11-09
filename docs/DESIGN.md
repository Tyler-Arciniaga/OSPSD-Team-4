# Issue Tracking Service Contract: Technical Design

#### Team 4, Issue Tracker Vertical

#### Tyler, Chan, Turki, Vanchi, Joseph

#### Reviewers: Issue Tracking Team

#### Status: Approved

#### Last Update Date: 11/08/2026

#### Relevant Links

- [Decision Memo](https://docs.google.com/document/d/1Le2SpjEt_jmymtxRapjZk4H8QeueM_De) (see ”The Interface and Contract Guarantees” for contract revisions)  
- Relevant Implementation Work:  
  - [HW1 Final Release](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/releases/tag/milestone-3)

---

## 1\. Context, Goals, Non-goals

### 1.1 Context

A consumer should be able to create an issue, retrieve it by ID, rename it, and delete it through one consistent API. Our HW1 service supports this workflow through Trello. HW2 extends the service to support Linear while preserving the common request formats and behavior that consumers depend on.

The providers differ in ways that affect how callers interpret results. For example, our live Linear checks showed that a missing issue can produce HTTP 200 with an error in the response body, whereas Trello returns 404\. A caller checking only the provider’s HTTP status could mistake the Linear response for success. Our adapters will translate these differences into the shared contract so consumers can handle the same outcome consistently.

The shared decision memo defines the agreed behavior. This design explains how our service will implement and verify it.

### 1.2 Goals (observable)

- Allow a consumer to complete the create, get, rename, and delete workflow with the same request formats, response structure, and outcome handling against either provider.  
- Select Trello or Linear through startup configuration.  
- Apply consistent request validation and caching behavior across both providers.  
- Reject renames based on an outdated issue version using ETag and If-Match with both providers.  
- Ensure that successfully deleted issues are no longer retrievable through our API.  
- Verify that both providers follow the shared contract using the same test suite, maintained in `contract/tests/conformance/` in this repository.

### 1.3 Non-goals

- Moving or synchronizing existing issues between Trello and Linear.  
- Supporting every provider capability, such as descriptions, assignees, labels, or workflow states, through the common API.  
- Complete support for Idempotency-Key. Trello supports it but Linear will initially answer with `503 service_not_configured`.  
- Switching providers during a running request. Each process uses the provider selected at startup.  
- Providing identical provider internals or performance. The shared contract defines the observable behavior consumers can rely on.

## 2\. Proposed Architecture and Request Flow

Labels: **HW1** means it exists in the HW1 repo today. **Proposal** means it is new in this design. Linear is not yet implemented, so every Linear detail is a proposal or an observed provider behavior from our live calls.

### 2.1 Components and responsibilities

| Component | Responsibility | Status |
| :---- | :---- | :---- |
| Shared contract package | `Issue`, `IssueCreate`, `IssueUpdate`, the `IssueProvider` interface, and the domain error types: `ErrorCode`, `ServiceError`, code-to-status mapping, and JSON error response models. No provider SDK, no web framework, no application logic. | Proposal (extracted from `app/models.py`, `app/interface.py`, `app/errors.py`) |
| Application decisions | `ETag` and `If-Match` checks, the 30 second read cache (`CachedIssueProvider`), `Idempotency-Key` handling (an optional header on create so a retried request makes at most one issue; the application records each key in its store and asks the adapter to find or tag an earlier create, and only adapters that support this accept the header). The ETag and cache logic is written only against `IssueProvider`, so it runs unchanged with either adapter. Idempotency also needs the optional `IdempotentIssueProvider` capability, which Trello has and Linear does not yet have. | HW1 (`app/main.py`, `app/cache.py`, `app/idempotency.py`) |
| HTTP translation | FastAPI routes, request validation, and the error handlers that turn a `ServiceError`, imported from the contract package, into the shared JSON error body. Failure logging (`log_failure`) also stays here because it is application policy. | HW1 (`app/main.py`) |
| Provider adapters | `TrelloIssueProvider` (unchanged). `LinearIssueProvider` turns the four operations into GraphQL calls, reads error bodies on HTTP 200, and maps provider failures to `ServiceError`. | HW1 (Trello), Proposal (Linear) |
| Startup configuration | `lifespan` reads configuration, picks one adapter, builds it, wraps it in the cache, and closes it at shutdown. | HW1 pattern, Proposal (provider choice and `close`) |

**Dependency direction.** Routes depend on the `IssueProvider` interface. Adapters implement it. The contract package depends on nothing in our service. Only startup code names a concrete adapter, so nothing above the adapters can reference Trello or Linear.

### 2.2 Who creates and closes provider clients, and who owns state

- **Creation/Provider Selection (Proposal).** `lifespan` in `app/dependencies.py` is the only place that constructs an adapter. A configuration value (`ISSUE_PROVIDER=trello|linear`) selects it, unset defaults to `trello`. HW1 already builds one provider per process this way, and construction sends no request to the provider.  
- **Closing (Proposal).** The Linear adapter holds one long-lived HTTP client for connection reuse, so it exposes `close()`. Every adapter implements `close()`, and `lifespan` always calls it in its `finally` block, so shutdown looks the same for both adapters. For Linear it closes the client. The Trello adapter keeps its HW1 per-request `urlopen` calls, so it has no client and its `close()` does nothing. `CachedIssueProvider` forwards `close()` to the adapter it wraps, because the cache is what `lifespan` holds. Without that forwarding, Linear's client would never be closed. We do not rewrite Trello's internals for consistency, because that would churn tests that currently mock `urlopen` and gain nothing for the contract.  
- **Contract boundary.** `close()` is not part of the shared `IssueProvider` contract, because consumers never close a provider. The "must have `close()`" rule is a small startup-side protocol defined in the service (not in the contract package) that `lifespan` and the cache wrapper rely on. Whoever creates a resource closes it, and that ownership stays in startup code.  
- **State ownership.** The read cache is owned by the application and lives per process, as in HW1. The idempotency store is owned by the application and is a file path shared by all processes, as in HW1. Neither belongs to an adapter.&nbsp;  
- **No runtime switching.** One process talks to one tracker for its whole life.

### 2.3 Common workflow, guarantees and limits

The workflow is: create an issue from a title, get it by ID, rename it, delete it. A consumer needs these guarantees on every provider:

- The same request shapes and response shape (`{id, title}`).  
- The same seven core error codes with fixed HTTP statuses.  
- `ETag` on reads, and `If-Match` on rename returns `412` when stale.  
- After a delete, the issue is no longer retrievable through this API. Deliberate limits: no status, description or assignee in the core. Details of the contract are in the [shared memo](https://docs.google.com/document/d/1Le2SpjEt_jmymtxRapjZk4H8QeueM_De) and are not repeated here.

### 2.4 Diagram

```
HTTP caller
    |   POST /issues  {"title": "..."}
    v
FastAPI routes  (validation, ETag, error handlers)      <- HW1
    |
    v
CachedIssueProvider  (30 s read cache)                  <- HW1
    |
    v
IssueProvider interface   (via shared contract package)     <- Proposal: extracted
    |                \
    v                 v
TrelloIssueProvider   LinearIssueProvider               <- HW1 / Proposal
    |                 |
    v                 v
Trello REST API       Linear GraphQL API
 
Startup (lifespan): reads config -> builds ONE adapter -> wraps in cache -> closes it at shutdown
```

### 2.5 Request traces

**Trace A: successful `POST /issues` (Linear, no `Idempotency-Key`).**

1. The caller sends `POST /issues` with `{"title": "Fix login bug"}`.  
2. **Validation (HTTP layer).** FastAPI parses the body into `IssueCreate`. A title outside 1 to 256 characters, or only whitespace, is rejected here as `422 invalid_input` before any provider call.  
3. The route calls `provider.create_issue(title)` on the cache wrapper, which passes writes straight through.  
4. **Domain conversion (adapter).** `LinearIssueProvider` sends an `issueCreate` GraphQL mutation using the configured team ID, and the workflow state if the operator set one. It reads the returned issue and converts it to the shared `Issue` with `id` and `title` only.  
5. The route returns `201` with the issue body and a `Location: /issues/{id}` header naming the new issue, exactly as in HW1 (`app/main.py` sets it from the returned `id`). The route builds `Location` from the shared `Issue`, so it is identical for Trello and Linear. Reads of this issue later carry an `ETag` computed by the application.  
6. **Logging.** None from the application. HW1 logs only failures (`log_failure`) and idempotency events, so a successful create writes no application log line. Server-level access logging, if enabled, is outside this design.&nbsp;

**Trace B: expected failure, `GET /issues/{id}` for an issue that does not exist (Linear).**

1. The caller sends `GET /issues/does-not-exist`.  
2. The route calls `provider.get_issue(id)`. The cache has no entry, so it calls the adapter.  
3. The adapter sends the GraphQL query. **Observed in our live calls:** Linear answers HTTP 200 with `data: null` and an `errors` entry with code `INPUT_ERROR` and the message "Entity not found: Issue". A malformed ID gives the same result.  
4. **Error translation (adapter).** The adapter must inspect the response body, not just the status line. It maps this case to `ServiceError(not_found)`. An unrecognized error body maps to `provider_error`.  
5. **Error translation (HTTP layer).** The `ServiceError` handler in `app/main.py` builds the shared body `{"error": {"code": "not_found", "message": ...}}` with status `404`.  
6. **Logging.** The handler calls `log_failure`, which records the operation, code, status and a short cause. It does not record request payloads or credentials.

### 2.6 Layer check

We add no new runtime layer, meaning no new step that a request passes through. A request still goes routes, then cache wrapper, then the `IssueProvider` interface, then one adapter. The one structural change is a new package boundary: the contract types, interface and error types move out of `app/` into their own package, so the Linear adapter and the shared tests can depend on the contract without importing Trello code. That package is imported, not called, so it adds no hop and no behavior. Existing components cover the remaining responsibilities: `app/main.py` already holds HTTP translation and the `ETag` logic, and `lifespan` already holds startup. The only new component is `LinearIssueProvider`, a second adapter at the outer edge of the request, next to the Trello one. It holds only Linear-specific translation (GraphQL calls, error mapping, deleted-issue detection). Shared application behavior (ETag, cache, idempotency, validation) stays outside it.

## 3\. API and Data Model

### 3.1 Contract references

The existing HW1 definitions provide the starting point for the common contract.

- [HTTP API documentation](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/30187ba3c43b7ba9785e60714a55e4968fdb857a/docs/api.md)  
- [Python provider interfaces](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/30187ba3c43b7ba9785e60714a55e4968fdb857a/app/interface.py)  
- [Issue models](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/30187ba3c43b7ba9785e60714a55e4968fdb857a/app/models.py)  
- [Error definitions](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/30187ba3c43b7ba9785e60714a55e4968fdb857a/app/errors.py)

The links above point to the existing HW1 code and API documentation. For HW2, we plan to move the common models, provider interfaces, and error definitions into a separate shared contract package. Once that package is available, we will add links to its Python definitions and HTTP API specification at the exact version our service uses.

### 3.2 Models and identifiers

The public API uses three models.

| Model | Fields and meaning |
| :---- | :---- |
| `Issue` | Contains the issue’s `id` and `title`, both strings. |
| `IssueCreate` | Requires a `title`. Additional fields are rejected. |
| `IssueUpdate` | Currently supports changing only `title`. An empty body, a null title, and additional fields are rejected. |

- Titles must be 1–256 characters and cannot contain only whitespace.  
- The service sends a title to the provider unchanged, including leading and trailing whitespace. A provider may trim that whitespace when it stores the title (Linear does; observed by the conformance suite). Responses and later reads return the title as stored.  
- Issue IDs are opaque strings: callers should pass them back unchanged without interpreting their format.  
- Each ID belongs to the configured provider; IDs are not shared between Trello and Linear.

### 3.3 Operations and update behavior

The proposal retains the existing HTTP routes.

| Operation | Request | Successful response |
| :---- | :---- | :---- |
| Create | `POST /issues` with a title | `201`, an `Issue`, and a `Location` header |
| Retrieve | `GET /issues/{issue_id}` | `200`, an `Issue`, and an `ETag` header |
| Rename | `PATCH /issues/{issue_id}` with a title | `200`, the updated `Issue`, and an `ETag` header |
| Delete | `DELETE /issues/{issue_id}` | `204` with no body |

- Renaming changes only the title.  
- For conditional renames, send the earlier ETag in \`If-Match\`. The service checks a fresh provider read and returns \`412 precondition\_failed\` if the version differs. Omitting \`If-Match\` or sending \`\*\` skips the version check.  
- The update lock prevents PATCH requests in the same process from overlapping. It does not coordinate other processes or changes made directly in the provider.  
- Reads may use cached results for up to 30 seconds. Update and delete attempts clear the affected local entry. Python callers can bypass the cache with \`fresh=True\`; HTTP callers have no cache-bypass option.

### 3.4 Representative request and response

Illustrative request and expected response based on the existing HW1 implementation.

```
POST /issues HTTP/1.1
Content-Type: application/json

{"title":"Fix login bug"}
```

&nbsp;

```
HTTP/1.1 201 Created
Content-Type: application/json
Location: /issues/64f1c0a2b3d4e5f607182930

{"id":"64f1c0a2b3d4e5f607182930","title":"Fix login bug"}
```

This example follows the existing Trello implementation. Our live Linear checks confirmed relevant provider behavior. After integrating the Linear adapter, we will verify the same request through our service and record the actual response. For both providers the create target is configured by the operator via environment variables.

### 3.5 Errors and create retries

All errors use the same JSON structure:

```
{
  "error": {
    "code": "not_found",
    "message": "Issue not found"
  }
}
```

`error.code` identifies the failure, while `error.message` provides a readable explanation. Validation failures return 422 \`invalid\_input\` and include \`error.details\` identifying invalid fields. Section 5.1 defines the error codes, HTTP statuses, and handling of uncertain writes.

Create requests may include an `Idempotency-Key` to prevent duplicates when retrying. The existing implementation remembers keys for 24 hours.

| Situation | Response |
| :---- | :---- |
| Repeat a successful request with the same key and body | Returns the original issue without creating another |
| Reuse the key with a different body | 422 `idempotency_key_reused` |
| An earlier request with the key is still running or unresolved | 409 `request_in_progress`, with `Retry-After` indicating when to retry |

After 24 hours, reusing a key may create a new issue. Our Trello implementation supports this behavior. We plan to eventually implement and verify it for Linear. Until supported (and what is considered in scope for HW2), the Linear configuration will return 503 `service_not_configured` when the header is supplied.

### 3.6 Provider translation and HW1 compatibility

Both adapters return the same `Issue` model containing `id` and `title`. Trello’s card `name` maps to `title`; Linear’s issue title maps directly. Descriptions, assignees, labels, and workflow states are excluded from the common model.

Each adapter converts provider errors into the shared error codes. Linear requires checking the GraphQL response body because an HTTP 200 response can still contain an error.

Our live checks showed that Linear can return a deleted issue and accept a second delete. The Linear adapter must detect deleted issues so that fetching or deleting them again returns 404 through our API, matching Trello.

Both providers will use the existing HW1 routes for creating, retrieving, renaming, and deleting issues. Both will also support caching and conditional updates. The shared contract does not require `Idempotency-Key` support, although we plan to add it for Linear (outside the scope of HW2 however). Providers may offer extra features, but must still support all required behavior.

## <!-- gdoc_comment_begin: c1 -->4<!-- gdoc_comment_end: c1 -->. Alternatives and Tradeoffs

### <!-- gdoc_comment_begin: c2 -->Consistent Deletion vs. Provider-Specific Deletion<!-- gdoc_comment_end: c2 -->

Another decision our team faced was whether to preserve each provider's deletion behavior or enforce consistent deletion across Trello and Linear. Trello supports deleting cards through its REST API, while Linear's `issueDelete` mutation moves issues to trash rather than immediately removing them permanently. One alternative would be to expose each provider's behavior directly, allowing Linear issues to remain retrievable after deletion while Trello issues would no longer be accessible. However, this would mean that the same `DELETE /issues/{id}` operation could produce different results depending on the configured provider. We chose to standardize deletion behavior so that a successful DELETE returns `204` and subsequent attempts to retrieve the issue return `404`, regardless of which provider is being used.

For consumers, this approach provides predictable behavior without requiring them to understand how each provider handles deletion internally. For example, a consumer could delete an issue and expect it to become inaccessible through our API, whether it originated in Trello or Linear. The tradeoff is that consumers would not have access to Linear's recovery functionality through our shared API, even though the issue may still exist in Linear's trash. For the implementation, Trello can continue using its existing deletion operation, while Linear requires additional handling to prevent trashed issues from being returned. One approach would be to include Linear's `trashed` field alongside `id` and `title` in the GraphQL query and return `404` whenever the issue is marked as trashed. This would allow us to preserve the same `IssueProvider` interface without introducing provider-specific behavior into the FastAPI routes.

Based on Linear's documented deletion behavior, deleted issues may remain available through its API while they are in trash. This creates a difference from Trello that our adapter would need to handle. We propose checking Linear's `trashed` field to determine whether an issue should be returned, but we have not yet verified this approach through implementation or testing. We also need to determine how Linear handles repeated deletion requests to ensure that deleting an already-trashed issue returns `404` rather than `204`. Depending on that behavior, the Linear adapter may need an additional lookup before deleting an issue.

We would reconsider this decision if future requirements included restoring deleted issues, permanently erasing data, or exposing provider-specific deletion features. In those cases, consumers might benefit from distinguishing between moving an issue to trash and permanently deleting it, which could justify introducing additional operations or changing the existing API contract. For our current implementation, consistent deletion behavior allows consumers to rely on the same responses from Trello and Linear while keeping their different deletion mechanisms within their respective adapters.

&nbsp;

## 5\. Failure Behavior and Operational Concerns

Labels: **HW1** means it exists in the HW1 repo today. **Proposal** means it is new in this design. **Observed** means we saw it in our own live calls to Linear. **Assumption** means it comes from provider documentation we have not tested.

### 5.1 Error path

Every failure travels the same three steps: the adapter turns a provider outcome into a `ServiceError` (a domain error with an `ErrorCode`), the service's error handler turns that into a public HTTP response, and `log_failure` records it. The shared contract fixes the codes and statuses (see [decision memo](https://docs.google.com/document/d/1Le2SpjEt_jmymtxRapjZk4H8QeueM_De)); this section covers how each adapter reaches them.

| Provider outcome | Domain error | HTTP | Notes |
| :---- | :---- | :---- | :---- |
| Issue does not exist, or ID is malformed | `not_found` | 404 | Trello: rejects a malformed ID without a request. Linear (Observed): HTTP 200 with an `errors` entry, `INPUT_ERROR`, "Entity not found: Issue"; a malformed ID gives the same result. |
| Provider rejects our credentials | `access_denied` | 403 | Linear (Observed): a bad key is a real HTTP 401, `AUTHENTICATION_ERROR`. Thus distinct translation needed. |
| Credentials, target list, or team missing | `service_not_configured` | 503 | Proposal for Linear: raise on missing API key or team ID. No request is sent. |
| Timeout, network failure, unusable response, rate limit | `provider_error` | 502 | A timeout is reported with cause `timeout`. Linear's over-limit and forbidden-entity responses are not yet observed, so they map to `provider_error`. |
| Provider rejects the request itself | `provider_error` | 502 | Covers a rejection that is not an access problem. |
| Invalid request body, title or `Idempotency-Key` format, or an extra field | `invalid_input` | 422 | Raised by the HTTP layer before any provider call, with `details` naming the field (HW1). Identical for both adapters. |
| `Idempotency-Key` sent but the configured provider cannot support it | `service_not_configured` | 503 | For Linear this is the expected answer until an approach is verified (Proposal). |
| `Idempotency-Key` reused with a different body | `idempotency_key_reused` | 422 | Application decision (HW1). Nothing is created. Does not involve the provider. |
| Another request with the same key is in progress, or an earlier one may still be taking effect | `request_in_progress` | 409 | Application decision (HW1), with a `Retry-After` header. Nothing new is created. |
| Stale `If-Match` | `precondition_failed` | 412 | Application decision, identical for both adapters (HW1). |
| Unexpected exception | `internal_error` | 500 | Logged with a stack trace. |

Decision: a repeated delete is 404 not\_found on every provider. Deleting an issue that is already deleted returns 404, as HW1 already documents. We chose this because it matches GET, which also returns 404 for a deleted issue, so a consumer needs one rule: after a successful delete the issue is gone and every later operation on it is not\_found. The alternative (treating a repeated delete as success) would hide a caller's wrong or stale ID and would force Trello to change a behavior it already has. We would revisit this if a consumer needs safe blind retries of deletes.

The consequence falls on the Linear adapter. Linear accepts a second delete and, in our live calls, still returns the deleted issue on a later fetch (Observed). To honor the decision the adapter must detect that an issue is already deleted, either from a field on the issue Linear returns or from a check before deleting.

**Uncertain writes.** A write whose result cannot be confirmed keeps its uncertainty. A timeout, a lost response or an unusable response on a create, rename or delete sets `may_have_applied` on the `ServiceError`, and the public response is `502`, not a success or a clean failure. The log record carries `write_outcome: unknown`. Only a failure that proves nothing was sent, such as a failed DNS lookup, a refused connection or missing credentials, is logged as `not_applied`. The Linear adapter follows the same rule (Proposal).&nbsp;

One Linear-specific consequence (Observed): a GraphQL response can be HTTP 200 and still carry an error, so the adapter must inspect the body before deciding whether a write succeeded.

**Retries.** The service does not retry provider calls automatically, for any operation and either adapter. HW1 behavior is kept and Linear follows it (Proposal). This is safe because a retry of a write that may have applied could create a second issue or repeat a rename, while a missing retry only returns an honest `502` and lets the caller decide. For creates, the caller's safe retry is the optional `Idempotency-Key` header: with Trello the service records the key and finds an earlier create (HW1), and with Linear the header is answered with `503 service_not_configured` until a Linear approach is implemented (out of scope for HW2). For reads, the caller can simply repeat the request. We would revisit this if live Linear use shows that rate-limit failures are frequent enough to justify bounded read retries.

### 5.2 Credentials, data exposure and logging

- **Credential boundary.** Provider credentials and targets are read from environment variables at the point of use, never from a request and never stored in the repository. HW1 uses `TRELLO_API_KEY`, `TRELLO_TOKEN` and `TRELLO_LIST_ID`. Linear needs an API key and a team ID, with an optional workflow state ID (names to be fixed during implementation) (Proposal). The key is sent only in the `Authorization` header to the provider's own host.  
- **Data exposure.** The only user content the service handles is the issue title. It is returned to the caller who sent it and sent to the chosen provider. It is not logged.  
- **Failure context without sensitive payloads.** `log_failure` writes one JSON line with the operation, error code, status, whether the provider was involved, a short cause and the write outcome. By design `cause` is a short label such as `timeout` or the exception class name, never a message, URL, credential or request body. The Linear adapter must keep to this: no GraphQL query text, variables, response body or key in any log line or `ServiceError` message (Proposal).  
- **Idempotency data.** The raw `Idempotency-Key` is stored nowhere. Only a hash is kept (HW1).
- **Startup configuration check (HW2).** Before building an adapter, the service checks that every variable the selected provider needs is set and not empty:
  - Trello: `TRELLO_API_KEY`, `TRELLO_TOKEN` and `TRELLO_LIST_ID`.
  - Linear: `LINEAR_API_KEY` and `LINEAR_TEAM_ID`.
  - For a provider that uses the idempotency store (Trello), also that the `IDEMPOTENCY_DB_PATH` directory is writable.

  If anything is missing, the service exits with one log line naming the variables. The line never includes a value, path or unrecognised `ISSUE_PROVIDER` value (`app/config.py`). The adapters' per-call checks remain as a second line of defense.
- **Secrets in the deployment (HW2).** Provider credentials for the Render deployment live only in a Render environment group created by hand. Terraform links the group by its ID and never reads its values. That resource's state holds two IDs. The service's own variables in Terraform are configuration only (`ISSUE_PROVIDER`, `IDEMPOTENCY_DB_PATH`). The image contains no credentials, `.env` file or Git history (`.dockerignore`). Terraform state lives in HCP Terraform, and state and plan files are never committed or uploaded as CI artifacts. See [docs/DEPLOYMENT.md](DEPLOYMENT.md#configuration-and-secrets) for who can read each value.
- **Health responses (HW2).** `/healthz` and `/readyz` carry statuses, a provider reachability word and the commit ID only: no credentials, configuration values or provider text.

### 5.3 Limits and cleanup

| Limit | Value | Source |
| :---- | :---- | :---- |
| Title length | 1 to 256 characters, not only whitespace | HW1 contract |
| Provider call timeout | 10 seconds per network operation | HW1 for Trello; will use the same for Linear |
| Read cache | 30 seconds, 100 entries, per process | HW1 |
| Idempotency key retention | 24 hours, then purged | HW1 |
| Linear request budget | 2,500 requests and 3,000,000 complexity points per hour | Observed in response headers; the over-limit response is not yet observed |

#### Cleanup obligations

- **Provider clients.** The adapter's `close()` is called by `lifespan` at shutdown, forwarded through the cache wrapper (see section 2.2). For Linear it closes the HTTP client (Proposal).  
- **Idempotency store.** Expired keys are purged (HW1). The SQLite file must be on storage that survives restarts and is shared by every process that serves the API.  
- **Live test data.** Any issue a live check creates must be deleted by that check, even when the check fails. Linear's delete is soft: deleted issues stay recoverable for a period (Assumption: 30 days, from Linear's help pages), so the test team will keep accumulating deleted issues.  
- **Fast tests versus live checks.** HW1's split is kept. Fast, controlled tests run in CI on every push and pull request (lint and the pytest suite) with simulated provider responses and no credentials or network. Live integration checks call the real provider, need real credentials, and are run deliberately and not in CI (as in HW1's live Trello verification).

### 5.4 Cost of the affected operations

HW1's explanation of the cost of `POST /issues` is in \[[Cost of POST /issues](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/blob/main/docs/engineering_decisions.md#62--rubric-122-explain-the-cost-of-one-operation-1-point)\]. It remains accurate for Trello, because the redesign does not change the Trello adapter's work: one request, local parsing of the response, one `Issue` built, with local work proportional to the request body size and the response size.

For Linear the redesign adds the same shape of work (Proposal, to be confirmed by implementation): one GraphQL request per operation, local validation of the title, local parsing of the response, and one `Issue` built. We ask Linear only for the `id` and `title` fields so the response stays small. The cache wrapper and ETag computation are unchanged and run in the application, not the adapter.&nbsp;

<!-- gdoc_comment_begin: c3 -->For `GET /issues/:{issue_id}` and `DELETE /issues`<!-- gdoc_comment_end: c3 -->`/:{issue_id}`, two points could add work: how the Linear adapter detects a deleted issue on `GET` or `DELETE` (it may need an extra read or field), and whether an operation needs an extra lookup beyond the configured team ID. Both are open questions for implementation.

We do not claim anything about Linear's or Trello's internal complexity. Request latency includes the network and the provider's own processing, and our analysis covers local work and the number of provider calls only.

### 5.5 Tools and hosting

Every choice below favors tools that are free, need no payment method, and take little setup.

| Concern | Choice | Why | Owner of the account | Expected cost |
| --- | --- | --- | --- | --- |
| Provisioning | Terraform | Simple, covered in lecture, and keeps the deployment reviewable in the repository. | Tyler | $0 |
| Hosting | Render, free web service, one instance | Free and no payment method needed. A single instance matches our design, because the update lock and read cache live in one process. | Tyler | $0 |
| CI and delivery | GitHub Actions (HW1 already runs lint and tests with a locked `uv` environment). HW2 adds a container build check on every push, a `terraform plan` comment on pull requests that change `infra/`, and a manual, approved apply-and-deploy workflow. | Already in use, so no new account. | N/A | $0 |
| Infrastructure state | HCP Terraform free plan, workspace in Local execution mode | Shared and locked state for the team without storing the Render key in HCP: plans and applies run where the key is. | Tyler | $0 |
| Telemetry | OpenTelemetry SDK, sent to Grafana Cloud's free plan | Free plan with no credit card: 10k metric series, 50 GB each of logs and traces, 14 day retention. | Tyler | $0 |

**Teardown date:** 11/20/2026. All resources are removed on this date (unless a grader requests an extension), with `terraform destroy` and by deleting the Grafana Cloud stack. `terraform destroy` runs through a manual, approved workflow, so any teammate can run it without a Render key. The resources made by hand are removed by the checklist in [docs/DEPLOYMENT.md](DEPLOYMENT.md#tear-down): the environment group, API keys and tokens, the HCP workspace, and the GitHub secrets.

**Deployment flow (HW2).** The artifact is the container image Render builds from the `Dockerfile` at one pinned commit, with dependencies from `uv.lock`.
- `infra/release.tf` pins the commit. A deploy is a pull request that changes it, and the plan is posted on the pull request.
- After merge, a manual workflow with a required approval:
  1. applies exactly the reviewed plan;
  2. deploys that commit through Render's deploy API;
  3. checks that `/readyz` reports it;
  4. plans again and fails unless nothing would change.
- Nothing deploys on push or merge. Render's auto-deploy is off, and the provider's redeploy after a settings change is disabled (`skip_deploy_after_service_update`).
- The pin goes through the deploy API because Render's Terraform provider can only name a branch, not a commit.

**Liveness and readiness (HW2).** Render's health check uses `/healthz`, which only says the process is serving. A brief provider outage therefore never makes Render restart a healthy service in a loop. `/readyz` reports whether the process can serve:
- 503 only if a dependency the configured provider uses is broken. Today that is the idempotency store, for Trello.
- The provider's reachability is reported for information only (`reachable`, `unreachable` or `unknown`). It never changes the status code, and it is checked at most every 30 seconds with the usual 10 second timeout.
- The response also reports the deployed commit.

See [Health checks](api.md#health-checks).

**Hosting consequences we accept**

- **Idempotency store is not durable.** Render free web services cannot attach a persistent disk, so the SQLite idempotency file is lost on every redeploy, restart or spin-down. HW1 promises that keys are remembered for 24 hours. In this deployment that promise only holds until the next restart. We accept this because Linear does not support `Idempotency-Key` anyway and the deployment is for demonstration. We would revisit this if the deployment needs durable keys, and the fix is a paid instance with a disk.
- **Cold starts.** The free service spins down after 15 minutes without traffic and takes about a minute to wake. The first request after idle will be slow, and the read cache starts empty.
- **Single instance.** Render free services support only one instance, which is what our design already provided.
- **Not for production.** Render's documentation says not to use free instances for production, and it may restart a free service at any time.

**Telemetry boundary:** Traces and logs carry the operation, status code, error code and timing. They never carry issue titles, request bodies, credentials or GraphQL query text, matching section 5.2.

## 6\. Migration and Coordination Plan

Labels: **HW1** means it exists in the HW1 repo today. **Proposal** means it is new in this design. Target dates are left as TBD until the team sets checkpoints.

### 6.1 Contract revision we adopt

We adopt the first release of the shared contract, tagged `issue-tracker-contract-v1.0.0`. The contract lives in this repository in a top-level `contract/` directory, outside `app/`. The importable package is `issue_tracker_contract` (distribution name `issue-tracker-contract`) under `contract/src/`, and the conformance suite lives in `contract/tests/conformance/`. Releases use a prefixed tag because this repository also holds service code. The service depends on the package at a pinned revision. The contract details are in the [decision memo](https://docs.google.com/document/d/1Le2SpjEt_jmymtxRapjZk4H8QeueM_De) and are not repeated here.

Assumption: course staff accept the contract living in our repository. If they require a separate shared contract repository, we move `contract/` as it is and update the links (see section 8).

### 6.2 What changes

| Area | Change |
| :---- | :---- |
| Code | Move the `Issue`, `IssueCreate` and `IssueUpdate` models, the `IssueProvider` interface, `ErrorCode`, `ServiceError`, the error response models and the code-to-status mapping from `app/` into `contract/src/issue_tracker_contract/`. `app/` imports from the package. Add `LinearIssueProvider` as a second adapter. Keep `log_failure` and the FastAPI error handlers in `app/`. |
| Startup configuration | `lifespan` reads `ISSUE_PROVIDER` and builds exactly one adapter. Add the Linear settings (API key, team ID, optional workflow state ID). Every adapter implements `close()` and `lifespan` always calls it, forwarded through the cache wrapper. |
| Tests | Existing HW1 tests update their import paths. Add controlled tests for the Linear adapter, tests for provider selection and `close()`, and the conformance suite. |
| Examples and docs | `docs/api.md` (update it to describe the contract rather than Trello), `docs/providers.md`, the README and `.env.example` describe both providers and the new settings. |

##### Proposed layout after the migration

```
.
├── contract/                        (new)
│   ├── src/issue_tracker_contract/  models, interface, errors (moved from app/)
│   └── tests/conformance/           shared conformance suite
├── app/
│   └── providers/
│       ├── common.py                (new) shared timeout and error helpers
│       ├── trello_provider.py       Trello adapter (moved from app/provider.py)
│       └── linear_provider.py       (new) Linear adapter
├── tests/
├── docs/
    └── DESIGN.md
```

### 6.3 Compatibility for existing callers

- **HTTP callers.** The routes, request and response shapes, error codes and headers do not change. With `ISSUE_PROVIDER` unset the service builds the Trello adapter, so an existing deployment behaves as it does in HW1 (Proposal). Choosing Linear needs no further code change once the Linear adapter is merged: the operator sets `ISSUE_PROVIDER=linear` and the Linear credentials and starts a new instance. A caller then uses that instance's base URL and the Linear issue IDs it returns.  
- **Python code that imports from `app/`.** Imports of the moved models, interface and error types break. We update them inside the repository in the same change that moves them, and we do not keep re-export shims in `app/`, since the only callers are our own code and tests (Proposal). We would add shims if an outside Python consumer turned out to depend on those imports.  
- **Provider IDs.** Existing Trello IDs keep working with the Trello deployment. They are not valid with a Linear deployment, and we do not translate them (a stated non-goal).

### 6.4 Reviewable changes

Each change is one pull request. After change is completed a link to the PR will be added to the appropriate column.

| \# | Change | Owner | Depends on | Verification |
| :---- | :---- | :---- | :---- | :---- |
| 1 | Extract `contract/` and re-point `app/` imports. Add a check that fails if the package imports anything from `app/`. | Tyler | none | The full existing HW1 test suite passes with only import paths changed. The import-boundary check passes. |
| 2 | Add `LinearIssueProvider` for get, create, rename and delete. The team splits the work by core operation. | Whole team | 1 | Controlled tests with simulated GraphQL responses, including HTTP 200 responses that carry errors, a malformed ID, and a repeated delete. Live checks against the test team. |
| 3 | Provider selection and startup: `ISSUE_PROVIDER`, Linear settings, `close()` always called and forwarded by the cache. | Vanchi | 1 (needs 2 to build the Linear adapter) | Tests for the default Trello selection, Linear selection, an unknown value rejected at startup, and `close()` reaching the wrapped adapter. |
| 4 | `Idempotency-Key` behavior with Linear: a Linear deployment answers `503 service_not_configured` when the header is sent. | Joseph | 2, 3 | A test that the header with a Linear provider returns `503` and creates nothing. Existing Trello idempotency tests still pass. |
| 5 | Conformance suite in `contract/tests/conformance/`, run against both adapters. | Turki and Chan | 1 (needs 2 to run against Linear) | The suite passes against Trello and Linear (see section 7). |
| 6 | Documentation updates listed in 6.2. | Whole team | 2, 3, 5 | The docs describe what the merged code does. A teammate reads them against the code. |

### 6.5 Early end-to-end integration step

Before the full Linear adapter is written, we run one thin slice through the whole stack: after change 1 merges, build the Linear `create_issue` and `get_issue` first, start the service with `ISSUE_PROVIDER=linear` pointed at the test team, create an issue through `POST /issues`, read it back with `GET /issues/{id}`, and request a missing ID. This exposes the assumptions most likely to be wrong early: that Linear errors arrive as HTTP 200 and must be read from the body, that a missing and a malformed ID look the same, and that the create target is configured correctly. If it breaks an assumption, we change the plan and this document before building rename and delete.

### 6.6 Handling an incompatible contract change

If a change to the contract would break an adapter or the conformance suite after implementation has started:

1. **Propose it in a pull request** against `contract/`, labeled as breaking, with the reason and the evidence.  
2. **Identify the affected consumers.** These are our service and both adapters, the conformance suite, and the outside consumer reviewer. We have no sibling team in this vertical.  
3. **Notify and review.** Write a notice in the pull request, tell the owner of each affected change (section 6.4), and get review from at least one of them before merging, as the decision memo requires.  
4. **Publish a replacement version** under a new tag (for example `issue-tracker-contract-v2.0.0`). The old tag stays immutable.  
5. **Keep the service on the old version** until the adapters and the conformance suite pass against the new one, then move the pin.  
6. **Update the shared agreement.** Revise the decision memo and this document, and record the change in the change log in section 9\.

### 6.7 Where the two adapters differ

| Aspect | Trello adapter | Linear adapter |
| :---- | :---- | :---- |
| Transport | REST over `urlopen` per request (HW1) | GraphQL over one long-lived HTTP client (Proposal) |
| Credentials | `TRELLO_API_KEY`, `TRELLO_TOKEN` (HW1) | API key in the `Authorization` header (Proposal) |
| Create target | A list, `TRELLO_LIST_ID` | A team, with an optional workflow state (Proposal) |
| ID shape | 24 lowercase hex characters | A lowercase UUID. Linear also accepts short identifiers such as `TYL-5`, but our service does not: it returns `404` for them without a request, so callers use the `id` we return. |
| Missing issue signal | HTTP 404 (HW1). A malformed ID is rejected without a request. | HTTP 200 with an `errors` entry, the same for a malformed ID (Observed) |
| Delete | Permanent, and a repeat is not found | Soft delete. A repeat succeeds and a later fetch can still return the issue (Observed), so the adapter must detect deletion to return `404` (see section 8). |
| `Idempotency-Key` | Supported through tagging the card description (HW1) | Not supported in this milestone, answers `503` |
| Client lifecycle | No client, `close()` does nothing | Closes the HTTP client |

**What stays shared.** Everything above the adapter is identical: routes, validation, `ETag` and `If-Match`, the read cache, the error body and status codes, idempotency handling, logging, and the `Location` header. This is why the same application behavior runs with either implementation. Choosing between them is one startup setting, and a running process never switches.

## 7\. Testing

| Guarantee | Check |
| :---- | :---- |
| Both providers implement the common workflow | Run the same HTTP conformance suite against each provider: create, get, rename, delete, then verify GET and repeated DELETE return `404`. Check response bodies, statuses and `Location`. |
| Invalid requests and provider failures produce the documented errors | Use HTTP tests and controlled provider responses; verify invalid input does not reach the provider and logs exclude credentials and payloads. |
| Stale renames are rejected | Read an ETag, rename the issue, then retry with the old ETag. Expect `412` and verify the earlier title remains. |
| Cache and retry behavior match the design | Use controlled tests for cache invalidation, uncertain writes and the existing Trello idempotency extension. Verify Linear returns `503` for a keyed create until it supports the extension. |

**Conformance.** The team will maintain one HTTP suite in the shared contract repository, proposed under `contract/tests/conformance/`. Run it unchanged against a service configured for Trello and then Linear. Fixtures create disposable issues, retain their IDs and clean them up even after failures; report any cleanup failure for follow-up. Tests use one service process for immediate post-delete checks because caches are per process. A GET that returns an issue after deletion exposes a contract mismatch.

**Controlled and live checks.** The existing `uv run --locked pytest` suite runs without credentials or network; extend it with simulated Linear responses and adapter tests. Run the conformance workflow against dedicated real-provider test accounts separately from CI, following the README's Trello verification. Live checks must clean up test data and keep credentials out of the repository. Controlled tests establish application behavior but cannot verify provider assumptions.

**Gaps.** The shared suite, tagged contract integration and Linear adapter are proposed, not yet verified. Turki will check Linear deletion behavior, Vanchi will investigate permission and rate-limit responses, and Chan will check any extra provider lookup (section 8.1). Record the results before the November 11 implementation milestone.

## 8\. Open Questions and Review

### 8.1 Open questions

No open question affects the shared contract. Everything which previously blocked a contract approval has been resolved in 8.2. The questions below are local to our implementation and can be resolved as we build.

| \# | Question | Consequence if unresolved | Owner | Next step |
| :---- | :---- | :---- | :---- | :---- |
| 1 | Does Linear expose a reliable indicator that an issue was deleted, and do deleted issues still appear in lists? | The Linear adapter would not return `404` after a delete or on a later `GET` of the recently deleted issue, so the settled repeated-delete rule would not be honored with Linear. | Turki | Confirm against the live test account and record the observed results. If there is no reliable indicator, track deletions in the adapter and document this rationale. |
| 2 | How does Linear report hitting its rate limit, and how does it report a valid key without permission for an entity? | Mapping permission errors would default to `provider_error` (502), which may hide a case that should be `access_denied` (403). | Vanchi | Observe the responses if they can be provoked safely. Otherwise ship the default mapping (`provider_error 502`) and record decision. |
| 3 | Does detecting a deleted issue on Linear `GET` add a provider call or an extra lookup to the cost of that operation? | The cost analysis in 5.4 may be wrong for Linear. | Chan | Confirm during implementation and update 5.4. |
| 4 | Which cloud provider and container service, and how do credentials reach the running service? | Cannot finish the Milestone 3 infrastructure plan. | Tyler | Decide before Milestone 3 infrastructure work starts.&nbsp;&nbsp; |

&nbsp;

### 8.2 Resolved questions

| Question | Resolution | Where recorded |
| :---- | :---- | :---- |
| Should a repeated delete be `404` or success? | `404 not_found` on every provider, consistent with `GET` and with HW1. | Section 5.1 |
| Do the JSON error response models (`ErrorBody`, `ErrorDetail`, `ErrorResponse`) live in the contract package or the service? | In the contract package, the JSON error shape has one authoritative definition, which intuitively sits next to `ErrorCode` and `ServiceError`. `log_failure` stays in the service. | Sections 2.1 and 3 |
| Does `Idempotency-Key` on create belong in the core contract, and how is it supported on Linear? | It is an extension, not core. Linear does not support it in this milestone and answers `503 service_not_configured` when the header is sent. | Section 5.1 and the decision memo |
| Where does the shared contract live? | In our HW1 repository, in its own top-level directory `contract/` outside `app/`. The importable package is `issue_tracker_contract` (distribution name `issue-tracker-contract`), under `contract/src/`, and releases use a prefixed tag such as `issue-tracker-contract-v1.0.0`. | Section 6 |

### 8.3 Review record

**Team sign-off.** Check a box when that person has reviewed the full design and agrees it can be accepted for implementation.

- [x] Tyler  
- [x] Chan  
- [x] Vanchi  
- [x] Turki  
- [x] Joseph

**Accepted risks and scope of approval.** Record here anything the team knowingly accepted and what the approvals cover.

- Risks accepted:
  - Deleted-issue detection on Linear is unverified. If Linear exposes no reliable deleted indicator, repeated deletes and later reads of deleted issues may need adapter-side tracking (open question 1).
  - There are no automatic provider retries. Callers handle 502 and re-read before retrying a write that may have applied.
  - The service runs as a single instance. The update lock, read cache and idempotency file assume one process, so scaling out is out of scope.
  - Linear’s 30-day soft-delete window is documented but untested, so test deletions may accumulate in the test team.
- Scope of approval: Approval means the team has reviewed this design and agrees the plan in sections 1 to 8 can go ahead as the Milestone 2 implementation plan.

## 9\. <!-- gdoc_comment_begin: c4 -->Next A<!-- gdoc_comment_end: c4 -->ctions

See section 6.4 followed by 8.1 for the team’s next actions.

### Change Log

- HW2 deployment (sections 5.2 and 5.5): startup configuration check, `/healthz` and `/readyz`, container image, Terraform for Render with HCP Terraform state, plan on pull requests, and a manual, approved apply and deploy. Secrets live in a hand-made Render environment group, which keeps their values out of Terraform state. See [docs/DEPLOYMENT.md](DEPLOYMENT.md).
- Every adapter now implements `ping()` (on `ClosableIssueProvider`), used only for the readiness report.

- Titles: a provider may trim a title's leading and trailing whitespace when storing it (section 3.2). The conformance suite found that Linear does, so the contract now promises the title as stored, consistent across create and later reads, rather than preserved whitespace.
- Linear IDs: the service accepts only Linear's full UUID `id`, not short identifiers such as `TYL-5` (section 6.7). A returned issue's `id` therefore always equals the requested ID, as with Trello.
- Adapters moved to `app/providers/` (`trello_provider.py`, `linear_provider.py`), with what they share (timeout, caller-facing errors) in `app/providers/common.py`. `close()` stays outside the contract as section 2.2 decided: the service-side `ClosableIssueProvider` protocol in `app/interface.py` requires it of every adapter.
- Dropped provider-aware cache keys (planned change 4). Each process runs exactly one provider, chosen at startup, and the read cache is in memory and per process, so IDs from two providers can never share a cache and cannot collide.
