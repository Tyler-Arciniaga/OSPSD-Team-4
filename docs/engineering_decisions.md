
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

`GET /issues/{issue_id}`

**1. Input size and relevant data structures**

The operation retrieves a single issue using its identifier. The endpoint calls `get_issue()` on the provider supplied by `get_issue_provider()`. During normal operation, that is one `CachedIssueProvider` per application process, wrapping a `TrelloIssueProvider`.

The cache stores entries in an `OrderedDict` keyed by issue ID. Each entry holds an expiration time and an `Issue`. The dictionary holds at most 100 entries, ordered from least to most recently used. A fixed list of 64 locks ("stripes") coordinates operations on the same ID.

On a cache miss, the Trello implementation requests only the card's `id` and `name` fields (`fields=id,name`). It parses the JSON response into a Python object and constructs an `Issue` model containing the public `id` and `title` fields.

Sizes considered:

- **k**: the length of the requested ID. For a valid Trello card ID, k is fixed at 24.
- **t**: the length of the issue's title.
- **n**: the size of the JSON response received from Trello. Because only `id` and `name` are requested, n grows with t plus the small constant k.

The number of cached entries does not affect per-request work, because the cache never scans its entries.

**2. Time complexity**

Both paths begin by hashing the ID to select a stripe, which takes O(k) time. Python caches a string's hash, so later dictionary operations on the same ID do not recompute it.

- **Cache hit: O(t).** The dictionary lookup, expiration check, and `move_to_end()` take O(1) on average. Returning a deep copy of the cached `Issue` takes O(t).
- **Cache miss or expired entry: O(n).** An expired entry is removed in O(1). The provider checks the ID format in O(k), which is O(1) for valid IDs. Parsing the response takes O(n). Storing a copy takes O(t). Thus, the miss path is **O(n)**.

These bounds measure local processing only. They exclude network latency, Trello's internal processing, and time spent waiting for a stripe lock held by another operation on an ID that maps to the same stripe.

**3. Space complexity**

- **Per request:** O(n) on a miss, for the parsed response and the copied `Issue`, and O(t) on a hit, for the returned copy.
- **Shared cache:** at most 100 entries per process, so cache storage is O(100 · t_max), where t_max is the longest cached title. The limit counts entries, not bytes.

**4. Provider calls and assumptions**

In every case, the operation returns one record. The number of records fetched from Trello depends on the path:

| Situation | Trello requests |
| --- | --- |
| Cache hit (entry younger than 30 seconds) | 0 |
| Cache miss or expired entry, valid card ID, credentials set | 1 (`GET /1/cards/{id}?fields=id,name`) |
| ID that is not a full Trello card ID | 0 (rejected as `not_found` before any request) |
| Cache miss with credentials missing | 0 (`503 service_not_configured`) |

Failed reads are never cached, so each repeated failing request costs another Trello request.

The implementation uses a 10-second request timeout and does not automatically retry failed requests. A conditional `PATCH` (with `If-Match`) makes one additional fresh read that bypasses the cache. That cost belongs to PATCH, not to GET.

**5. Delegated work and remaining uncertainty**

Trello performs the actual card lookup and returns the requested data. Our service does not control Trello's internal search algorithms, storage structures, or processing complexity.

Therefore, we cannot determine Trello's internal time or space complexity from our implementation. Network latency and provider processing time also affect real-world performance but are separate from our local asymptotic analysis.

We have not measured the cache hit rate. The share of GETs costing zero Trello requests depends on how often callers re-read the same issue within 30 seconds.

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
