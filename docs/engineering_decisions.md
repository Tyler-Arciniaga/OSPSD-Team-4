
# Engineering Decisions and Tradeoffs

## 6.1 / Rubric 12.1: Document Where We Intentionally Took On Technical Debt (3 Points)

**Decision: Using Provider-Specific Issue IDs Instead of Implementing an Internal ID Mapping System**

**1. Explain the problem, relevant constraints, and a plausible alternative that could address the problem.**

Our Issue Tracker service was designed around a provider-independent `IssueProvider` interface, allowing the application to interact with an issue-tracking provider without depending on its specific implementation.

During our team discussion, we considered implementing an internal ID mapping system for potential cross-provider identity management. However, we decided against it because our current implementation integrates with a single provider, and maintaining a separate mapping system would introduce additional storage, synchronization, and maintenance requirements beyond our current scope.

An alternative would be to generate service-owned issue identifiers and maintain a mapping between those identifiers and the corresponding provider IDs. This could preserve issue identities across provider migrations but would increase implementation complexity.

**2. Give evidence supporting the selected approach and connect it to the implementation. Distinguish observations from assumptions.**

Our `IssueProvider` interface (`app/interface.py`) defines `get_issue(issue_id: str, *, fresh: bool = False) -> Issue`, `update_issue()`, and `delete_issue()` with an `issue_id: str`, and treats issue IDs as opaque strings. The endpoints pass these identifiers to the provider unchanged, without enforcing Trello-specific formatting rules.

Within `TrelloIssueProvider`, `_require_card_id()` validates the supplied identifier against Trello's card ID format before any request, and `_card_to_issue()` translates Trello's `id` and `name` fields into our public `Issue` model.

The read cache (`CachedIssueProvider` in `app/cache.py`) also keys its entries by this same provider ID. Because the ID is stable and unique within one Trello account, the cache needs no translation step and no second index.

This demonstrates that Trello-specific validation remains behind the provider boundary and that our service can retrieve, update, delete, and cache issues without maintaining a separate identifier mapping database.

Our observation is that this approach supports our current Trello integration while preserving the provider-independent interface: `tests/test_injected_provider.py` passes a UUID, a number, and `PROJ-42` through the endpoints to a supplied provider unchanged. Our assumption is that direct provider identifiers will remain sufficient for the current service scope. We have not established that this strategy would preserve issue identities across provider migrations or simultaneous integrations.

**3. Explain the consequences, remaining uncertainty, and a concrete condition that would make the team revisit the decision.**

Our current approach keeps issue retrieval straightforward and avoids introducing an additional storage layer. The tradeoff is that issue identifiers remain associated with the underlying provider. If we later migrate issues to another provider, we cannot guarantee that clients will be able to reference those same issues using their existing identifiers. Cache keys would change along with the IDs, although this costs little because the cache is in memory and cleared on restart.

We accepted this limitation because cross-provider identity management is not required for our current implementation. The remaining uncertainty is how identifiers would be managed if multiple providers were introduced, particularly when preserving existing issue references. With several providers behind one cache, two providers could also return the same ID string for different issues, so cache keys would need to include the provider.

We would revisit this decision if the service needed to support multiple providers simultaneously, migrate existing issues between providers, or maintain consistent issue references across providers.

## 6.2 / Rubric 12.2: Explain the Cost of One Operation (1 Point)

`POST /issues`

**1. Input size and relevant data structures**

The operation creates one issue from a JSON body containing a `title`. FastAPI parses the body, and the `IssueCreate` model validates the title (1 to 256 characters, at least one non-whitespace character, no other fields). The endpoint then calls `create_issue(title)` on the supplied provider.

During normal operation the provider is a `CachedIssueProvider`, which forwards creation directly to `TrelloIssueProvider` without reading or changing the cache. The cache therefore plays no part in this cost.

`TrelloIssueProvider.create_issue()` URL-encodes the configured list ID and the title into the query string, sends one request, parses the JSON response, checks the returned card's `id` and `name`, and constructs an `Issue`. The endpoint returns that `Issue` with a `Location` header.

Sizes considered:

- **b**: the size of the request body sent by the caller.
- **t**: the length of the title. Validation caps a successful request at t ≤ 256.
- **n**: the size of the JSON response from Trello. The request does not limit the returned fields, so Trello returns its full representation of the new card, not just `id` and `name`.

**2. Time complexity: O(b + n)**

- **Request parsing and validation: O(b).** The whole body is read and parsed as JSON before any field rule runs. The length and non-whitespace checks then take O(t). The 256-character limit bounds t for accepted requests, but it does not bound b: an oversized title or body is fully read and parsed before being rejected with `422`. The service sets no request-body size limit of its own.
- **Building the request: O(t).** URL-encoding the title and the fixed-length list ID is linear in the title length.
- **Handling the response: O(n).** Parsing Trello's JSON is linear in the response size. Checking the card ID against the 24-character pattern is O(1), and building the `Issue` and the `Location` header is O(t).

For an accepted request, t ≤ 256, so local work is dominated by reading the request body and parsing Trello's response: **O(b + n)**. These bounds exclude network latency and Trello's internal processing.

**3. Space complexity: O(b + n)**

The parsed request body takes O(b) space and the parsed Trello response takes O(n). The returned `Issue` and the encoded request URL take O(t). Nothing is retained after the request finishes: POST does not populate the cache.

**4. Provider calls and assumptions**

Each request creates at most one issue and returns one record. The number of Trello requests does not depend on the title length or body size:

| Situation | Trello requests |
| --- | --- |
| Valid body, credentials and `TRELLO_LIST_ID` set | 1 (`POST /1/cards` with `idList` and `name`) |
| Invalid body (`422 invalid_input`) | 0 (rejected before the provider is called) |
| Credentials or `TRELLO_LIST_ID` missing (`503 service_not_configured`) | 0 |

The implementation uses a 10-second request timeout and never retries. Creation is not idempotent: after a `502`, especially a timeout, the card may already exist, so a caller who retries can create a duplicate.

**5. Delegated work and remaining uncertainty**

Trello stores the card, assigns its ID, and places it in the configured list. We cannot determine Trello's internal time or space complexity from our implementation, and network latency is separate from this local analysis.

We have not measured n. We assume a newly created card's response is small and roughly constant apart from the title, because the card has no description, labels, or other content yet. We have not verified how large Trello's create response is, or whether its size changes with board configuration.

## 6.3. Extend a Teammate's Feature

**Extension: Chan added read caching across all four issue operations ([#23](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/pull/23)).**

GET and POST were originally implemented by Tyler (#3, #17), PATCH by Joseph (#13), and DELETE by Vanchi (#15). Chan, who led none of these operations, extended all four through a pull request, reviewed by Tyler.

**Room for improvement and expected behavior**

Before the extension, every GET sent a new request to Trello, even when the same issue had just been read. This added latency and used up Trello's rate limit. Before editing, Chan identified the behavior each operation had to keep:

- **GET** returns the issue's `id` and `title`, with the same errors as before.
- **PATCH and DELETE** must never be followed by a read that returns pre-write data from the same process, even when the write fails with an uncertain outcome.
- **POST** creates exactly one issue per successful request.

**The boundary that contains the change**

The cache belongs between the endpoints and the provider. `CachedIssueProvider` (`app/cache.py`) implements the same `IssueProvider` interface and wraps the real provider:

| Operation | Cache behavior |
| --- | --- |
| GET | Serve a valid cached copy, or fetch and cache a successful result for 30 seconds. |
| PATCH | Remove the entry before forwarding the update, even if the update fails. |
| DELETE | Remove the entry before forwarding the deletion, even if it fails. |
| POST | Forward creation without reading or changing the cache. |

As a result, the endpoint handlers and `TrelloIssueProvider` did not change. The endpoints still receive an `IssueProvider`, and the Trello implementation still knows nothing about caching.

**Which modules changed and why the change spread**

- **`app/cache.py` (new):** the wrapper, its expiration, least-recently-used eviction, and locking.
- **`app/dependencies.py`:** `get_issue_provider()` previously created a new `TrelloIssueProvider` for every request. A cache created there would have been empty on every request. A new `lifespan()` function creates one cached provider at startup and stores it in `app.state`, and `get_issue_provider()` returns that shared instance. This gave the provider an explicit owner and lifetime, which it previously lacked.
- **`app/main.py`:** two lines, registering `lifespan` with the application.
- **Endpoint test files and the new `tests/conftest.py`:** several tests used a module-level `TestClient`, which does not run startup, so the shared provider did not exist. A shared `client` fixture now runs startup and shutdown for each test.
- **`docs/api.md`, `docs/providers.md`, and `README.md`:** documentation of the intentional contract change described below.

**Preserved guarantees and the intentional contract change**

Response shapes, status codes, and error codes are unchanged. Writes invalidate before contacting Trello. The existing endpoint tests that use simulated Trello responses now run through the wrapper, and they still pass.

The intentional contract change is read freshness. A GET may now return a result up to 30 seconds old, and changes made directly in Trello or through another server process are not seen until the entry expires. This is documented in [Read freshness and caching](api.md#read-freshness-and-caching).

**Tests that demonstrate the result**

`tests/test_cache.py` uses fake providers and a controlled clock, without network access or waiting for real expiration:

- `test_repeated_read_uses_cache` and `test_cached_issue_expires` check hits and expiration.
- `test_cache_evicts_least_recently_used_issue` checks the 100-entry bound.
- `test_update_invalidates_cached_issue` and `test_delete_invalidates_cached_issue` check write invalidation.
- `test_failed_write_invalidates_cached_issue` checks invalidation when a write fails with an uncertain outcome.
- `test_failed_read_is_not_cached` and `test_returned_issues_cannot_change_cached_value` check that errors are not cached and that callers cannot alter cached data.
- `test_application_shares_cache_and_resets_it_on_restart` checks the startup-owned lifetime.
- `test_slow_fetch_does_not_block_another_stripe` and `test_same_issue_operations_wait_for_fetch` check concurrency.

POST forwarding is covered by the existing creation tests running through the wrapper; there is no dedicated cache test for POST.

**Review and evaluation of our design**

Tyler's review pointed out that the first version held one global lock during Trello requests, so a slow miss for one issue blocked cache hits for every other issue. Chan replaced it with 64 striped locks and a separate short lock for the dictionary.
