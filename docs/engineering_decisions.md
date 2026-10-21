
# Engineering Decisions and Tradeoffs

## 6.1 / Rubric 12.1: Document Where We Intentionally Took On Technical Debt (3 Points)

**Decision: Using Provider-Specific Issue IDs Instead of Implementing an Internal ID Mapping System**

**1. Explain the problem, relevant constraints, and a plausible alternative that could address the problem.**

Our Issue Tracker service was designed around a provider-independent `IssueProvider` interface, allowing the application to interact with an issue-tracking provider without depending on its specific implementation.

During our team discussion, we considered implementing an internal ID mapping system for potential cross-provider identity management. However, we decided against it because our current implementation integrates with a single provider, and maintaining a separate mapping system would introduce additional storage, synchronization, and maintenance requirements beyond our current scope.

An alternative would be to generate service-owned issue identifiers and maintain a mapping between those identifiers and the corresponding provider IDs. This could preserve issue identities across provider migrations but would increase implementation complexity.

**2. Give evidence supporting the selected approach and connect it to the implementation. Distinguish observations from assumptions.**

Our `IssueProvider` interface defines `get_issue(issue_id: str) -> Issue` and treats issue IDs as opaque strings. The application passes these identifiers to the provider without enforcing Trello-specific formatting rules.

Within `TrelloIssueProvider`, the `get_issue()` operation validates the supplied identifier against Trello's card ID format, retrieves the corresponding card, and translates Trello's `id` and `name` fields into our public `Issue` model.

This demonstrates that Trello-specific validation remains behind the provider boundary and that our service can retrieve issues without maintaining a separate identifier mapping database.

Our observation is that this approach supports our current Trello integration while preserving the provider-independent interface. Our assumption is that direct provider identifiers will remain sufficient for the current service scope. We have not established that this strategy would preserve issue identities across provider migrations or simultaneous integrations.

**3. Explain the consequences, remaining uncertainty, and a concrete condition that would make the team revisit the decision.**

Our current approach keeps issue retrieval straightforward and avoids introducing an additional storage layer. The tradeoff is that issue identifiers remain associated with the underlying provider. If we later migrate issues to another provider, we cannot guarantee that clients will be able to reference those same issues using their existing identifiers.

We accepted this limitation because cross-provider identity management is not required for our current implementation. The remaining uncertainty is how identifiers would be managed if multiple providers were introduced, particularly when preserving existing issue references.

We would revisit this decision if the service needed to support multiple providers simultaneously, migrate existing issues between providers, or maintain consistent issue references across providers.

## 6.2 / Rubric 12.2: Explain the Cost of One Operation (1 Point)

`GET /issues/{issue_id}`

**1. Input size and relevant data structures**

The operation retrieves a single issue using its identifier. The application passes the identifier to `IssueProvider.get_issue()`, which is implemented by `TrelloIssueProvider`.

The Trello implementation requests the card's `id` and `name` fields, parses the JSON response into a Python object, and constructs an `Issue` model containing the public `id` and `title` fields.

Let **n** represent the size of the JSON response received from Trello.

**2. Time complexity: O(n)**

The provider validates the issue ID against Trello's fixed-length card ID format, which takes O(1) time because the accepted length is fixed.

Constructing the request is also O(1) under this assumption. Parsing the JSON response takes O(n) time, and extracting the required fields requires work bounded by the response size.

Therefore, the overall local processing time is **O(n)**, excluding network latency and Trello's internal processing.

**3. Space complexity: O(n)**

The provider loads the JSON response into memory before constructing the `Issue` model. The parsed response requires O(n) space, and the returned model stores the issue identifier and title.

Therefore, the overall local space complexity is **O(n)**.

**4. Provider calls and assumptions**

A validly formatted issue ID with available credentials results in one GET request to Trello. If the identifier does not match Trello's expected card ID format, the provider rejects it before making a network request, resulting in zero provider calls. Missing credentials can also prevent the request.

The implementation uses a 10-second request timeout and does not automatically retry failed requests.

This analysis assumes a single-card retrieval and measures response-processing work rather than the total duration of the network request.

**5. Delegated work and remaining uncertainty**

Trello performs the actual card lookup and returns the requested data. Our service does not control Trello's internal search algorithms, storage structures, or processing complexity.

Therefore, we cannot determine Trello's internal time or space complexity from our implementation. Network latency and provider processing time also affect real-world performance but are separate from our local asymptotic analysis.

## 6.3. Extend a Teammate's Feature

Our team has refactored several parts of the architecture to improve provider independence, authentication, and error handling. However, we have not identified a necessary extension to an existing teammate-owned feature. We would revisit an extension if we identified a meaningful improvement that preserves existing guarantees.
