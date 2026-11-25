# team-4-issue-tracker-trello

Team 4's Trello implementation of the shared issue tracker contract
([`issue-tracker-contract`](../contract/README.md)). Install it in your own
service, point it at your own Trello test list with constructor arguments
or three environment variables, and use it wherever the contract's `IssueProvider` is expected.

The package contains only the provider: no web framework, no telemetry
library, and no `app` module, so it cannot collide with your service's own
`app/` package. It talks to Trello's REST API with the Python standard
library. Its only dependency is the contract, which brings pydantic.

| | |
| --- | --- |
| Distribution | `team-4-issue-tracker-trello` |
| Import name | `team_4_issue_tracker_trello` |
| Current version | 1.1.0, tag `team-4-issue-tracker-trello-v1.1.0` |
| Implements | `issue-tracker-contract` 1.1.0 (installed automatically) |
| Python | 3.11.5 or newer |

## Installing

With pip:

```bash
pip install "team-4-issue-tracker-trello @ git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@team-4-issue-tracker-trello-v1.1.0#subdirectory=team-4-trello-impl"
```

With uv, from your project's directory:

```bash
uv add "git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@team-4-issue-tracker-trello-v1.1.0#subdirectory=team-4-trello-impl"
```

That records the tag in your `pyproject.toml`:

```toml
[tool.uv.sources]
team-4-issue-tracker-trello = { git = "https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git", subdirectory = "team-4-trello-impl", rev = "team-4-issue-tracker-trello-v1.1.0" }
```

Two things to check if `uv add` fails to resolve:

- **`requires-python`.** Your project's `requires-python` must not allow
  versions below 3.11.5. `uv init` writes `>=3.11` on Python 3.11, which uv
  rejects; change it to `>=3.11.5`.
- **How you pin the contract.** If your project also depends on
  `issue-tracker-contract` directly, pin it with `rev =`, not `tag =`:

  ```toml
  issue-tracker-contract = { git = "https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git", subdirectory = "contract", rev = "issue-tracker-contract-v1.1.0" }
  ```

  This package names the contract by its tag in a URL, which does not say
  whether the name is a tag or a branch. uv (checked with 0.12.22) treats
  that as a different URL from a `tag =` source and stops with "conflicting
  URLs for package `issue-tracker-contract`". `rev =` with the same tag name
  or its commit resolves to the same contract. You can also leave the
  contract out of your own dependencies and get it through this package.

## Configuration

The provider needs three values. Pass any of them to the constructor as
keyword arguments; each one you leave out is read from its environment
variable, as in 1.0.0:

```python
TrelloIssueProvider()  # all three from the environment (the default)
TrelloIssueProvider(api_key=key, token=token, list_id=list_id)
TrelloIssueProvider(list_id=list_id)  # key and token from the environment
```

| Argument | Fallback variable | Value |
| --- | --- | --- |
| `api_key` | `TRELLO_API_KEY` | API key of a Trello app on your test account |
| `token` | `TRELLO_TOKEN` | Token that account granted with read and write access |
| `list_id` | `TRELLO_LIST_ID` | Full 24-character ID of the list issues live in |

- `api_key` and `token` name one Trello account and are sent with every
  request. `list_id` is the one list that `list_issues`, `create_issue`,
  `create_tagged_issue`, `find_tagged_issue` and `ping` work in.
  `get_issue`, `update_issue` and `delete_issue` take a card ID and use only
  the credentials, so they work without a list.
- `None` (the default) means "use the environment variable". The variable is
  read each time a call contacts Trello, not at construction.
- An empty string `""` counts as missing and does **not** fall back to the
  environment. It fails like an unset variable.
- Nothing is checked at import or construction, so `TrelloIssueProvider()`
  always succeeds. Missing values surface as an error on the first call that
  needs them (see [Errors](#errors)).

**Several accounts in one process.** Each provider keeps its own arguments,
so a service that acts for several users can create one provider per user,
each with that user's own key, token and list, and use them at the same time,
including from different threads:

```python
alice = TrelloIssueProvider(api_key=a_key, token=a_token, list_id=a_list)
bob = TrelloIssueProvider(api_key=b_key, token=b_token, list_id=b_list)
```

Do not switch users by changing environment variables per request; one
user's call could then pick up another user's credentials.

Use your own dedicated test account and test list. Keep these values out of
your repository, for example in a gitignored `.env` file or your deployment
platform's secrets.

1. Sign in to the test account, create an app in the
   [Trello developer portal](https://trello.com/apps/admin), open its
   **Trello Auth** tab and generate an API key. Skip the OAuth 2.0 page.
2. Replace `YOUR_API_KEY` in this URL with the key, open it in a browser and
   approve read and write access:
   `https://trello.com/1/authorize?expiration=30days&scope=read,write&response_type=token&key=YOUR_API_KEY`.
   `expiration` sets how long the token lasts: `1hour`, `1day`, `30days` or
   `never`. A deployed service needs a token that outlives your demo, so use
   `30days`, or `never` for a test account you will revoke later. Revoke
   tokens from the account's settings under **Applications**.
3. Open any card in the target list, append `.json` to its URL, and copy the
   top-level `idList` value into `TRELLO_LIST_ID`.

See [Trello authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)
for permissions and revocation.

## Usage

```python
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import IssueUpdate
from team_4_issue_tracker_trello import TrelloIssueProvider

issues: IssueProvider = TrelloIssueProvider()

created = issues.create_issue("Write the standup doc")
same = issues.get_issue(created.id, fresh=True)
issues.update_issue(created.id, IssueUpdate(title="Write the standup doc (draft)"))
newest = issues.list_issues(limit=10)
issues.delete_issue(created.id)
```

Every method returns or accepts the contract's models (`Issue`,
`IssueUpdate`). An issue's `id` is the Trello card ID and its `title` the
card name; nothing else from the card is exposed.

| Method | Behavior |
| --- | --- |
| `get_issue(issue_id, *, fresh=False)` | Reads the card. |
| `update_issue(issue_id, changes)` | Renames the card when `changes.title` is set. |
| `delete_issue(issue_id)` | Permanently deletes the card. |
| `create_issue(title)` | Creates a card in the configured list. |
| `list_issues(*, limit)` | Open (not archived) cards in the list, newest first, at most `limit`. |
| `create_tagged_issue(title, creation_token)` | Like `create_issue`, but stores a marker for `creation_token` as the card's description. |
| `find_tagged_issue(creation_token)` | The oldest card in the list, archived or not, carrying that marker, or `None`. |
| `ping()` | `True` only if Trello answers for the configured list. It never raises. |
| `close()` | Does nothing (see below). |

**`fresh=True`.** This provider keeps no cache: every `get_issue` reads from
Trello, so `fresh=True` and `fresh=False` behave the same. If your service
adds a cache in front of it, honoring `fresh` is that cache's job.

**`close()`.** Each request opens and closes its own connection, so there is
nothing to release. It exists so services that close their providers at
shutdown can treat this one like any other.

**Idempotent creation.** `create_tagged_issue` and `find_tagged_issue` let
a caller create an issue at most once across retries. Generate a
`creation_token` (64 lowercase hexadecimal characters, for example
`secrets.token_hex(32)`), store it, and before retrying a creation whose
outcome is unknown call `find_tagged_issue(token)`; create again only if it
returns `None`. A token in any other format raises `ValueError`. The marker
is the card's whole description, so do not edit the description of a tagged
card, or `find_tagged_issue` will no longer find it.

## Errors

Every failure raises the contract's `ServiceError`. Its `code` is one of the
`ErrorCode` values below and `status` is the matching HTTP status. `message`
is safe to show callers; `cause` is a short internal reason for logs. Neither
contains credentials, URLs or Trello response bodies.

| `code` | Status | When |
| --- | --- | --- |
| `service_not_configured` | 503 | The API key or token is missing or empty, from both the argument and `TRELLO_API_KEY`/`TRELLO_TOKEN` (`cause` `missing_credentials`), or the list is missing or empty when creating or listing (`missing_target_list`). No request is sent. |
| `not_found` | 404 | The card does not exist (Trello 404), or the ID cannot be a Trello card ID (`cause` `malformed_issue_id`, checked before anything else, no request sent). |
| `access_denied` | 403 | Trello rejected the credentials or access (401 or 403). |
| `provider_error` | 502 | Trello timed out, could not be reached, rate limited (429), failed (5xx), rejected the request (other 4xx, including a wrong list ID), or returned something unusable. |

For a failed write, `may_have_applied` says whether the change might still
have happened. A failed `create_issue` sets it to `False` only when Trello
certainly stored nothing (a 4xx, a failed DNS lookup or a refused
connection). That is the case where retrying cannot create a duplicate.

## Tracing (optional)

Each Trello request runs inside a span named `trello.request`, which does
nothing by default. To trace requests, pass a function that takes the span
name and returns a context manager:

```python
from opentelemetry import trace
from team_4_issue_tracker_trello import set_span_factory

tracer = trace.get_tracer("my-service")
set_span_factory(
    lambda name: tracer.start_as_current_span(name, kind=trace.SpanKind.CLIENT)
)
```

`set_span_factory(None)` turns tracing off again. The package records
nothing on the span itself.

## Known limitations

- `list_issues` fetches the whole list from Trello and then sorts and cuts it
  (Trello's list endpoint has no limit), so very large lists are slow.
- `update_issue` can only change the title, because that is all the contract
  exposes.

## Versions and changes

Versions follow [semantic versioning](https://semver.org/). Each release is
an annotated git tag `team-4-issue-tracker-trello-vX.Y.Z` that is never
moved. See [CHANGELOG.md](CHANGELOG.md).

## Development

The package lives in [Team 4's repository](https://github.com/Tyler-Arciniaga/OSPSD-Team-4),
whose checks cover it: from the repository root, `uv run --locked pytest`
runs `team-4-trello-impl/tests/` with the rest of the suite, and the ruff and
mypy commands in the root README include it. The tests simulate Trello and
need no network access or credentials. `tests/test_package_boundary.py` fails
if the package imports the service, a web framework or a telemetry library.
