# Installing the Trello package from its tag (November 15, 2026)

Verification that another team can install `team-4-issue-tracker-trello`
1.0.0 from its release tag and use it against Trello, with nothing from this
repository's service. Times are UTC. No credentials appear below or in the
raw output: the live check read them from the gitignored `.env` inside its
own process, and the transcripts were searched for the credential values
before saving.

## Setup

- Tag `team-4-issue-tracker-trello-v1.0.0` (annotated), pointing at merge
  commit `c88ce2fd287f2524ba09ad5d94faf3983fdac08d` (PR #66). The release
  workflow for that commit had passed.
- Each install ran in a new, empty directory outside the repository, with
  Python 3.11.5. Every script ran with `python -I`, so nothing from the
  working directory could be imported.
- pip: a new venv, `pip install --no-cache-dir` of the documented pip line.
- uv 0.12.22: a new `uv init --bare` project with `requires-python =
  ">=3.11.5"`, then `uv add --no-cache` of the documented uv line.
- The scripts that ran are saved next to the transcripts:
  [offline_check.py](trello-package-install/offline_check.py) and
  [live_check.py](trello-package-install/live_check.py).

## Results

| Check | pip | uv |
| --- | --- | --- |
| Installs from the tag | Yes. The tag resolves to `c88ce2f`. | Yes. The tag resolves to `c88ce2f`. |
| Contract installed automatically | `issue-tracker-contract` 1.1.0 from tag `issue-tracker-contract-v1.1.0` | Same, locked at commit `18fb7ae` |
| Packages installed | The two packages and pydantic with its four dependencies | Same 7 packages |
| `import team_4_issue_tracker_trello`, `import issue_tracker_contract` | Work | Work |
| `app`, `fastapi`, `opentelemetry` importable | No, none of them | No, none of them |
| `TrelloIssueProvider()` is an `IssueProvider` | True | True |
| Call with `TRELLO_*` unset | `ServiceError` `service_not_configured`, cause `missing_credentials`, status 503 | Same |

Live check against the team's Trello test list (pip environment), at about
19:05:

| Step | Result |
| --- | --- |
| `ping()` | `True` |
| `create_issue(...)` | Card `6afa02513db5ac2cc4b370bb` created |
| `get_issue(id, fresh=True)` | The same id and title |
| `list_issues(limit=5)` | 3 issues, newest first. The new card is first. |
| `delete_issue(id)` | Done |
| `get_issue(id, fresh=True)` after the delete | `ServiceError` `not_found`, status 404 |

The live check deleted the only card it created.

Raw output:

- [pip install and offline checks](trello-package-install/pip-install.txt)
- [uv install and offline checks](trello-package-install/uv-install.txt)
  (the local interpreter path is replaced with a placeholder)
- [Live check](trello-package-install/live-check.txt)

## Notes for consumers

- uv rejects a project whose `requires-python` allows versions below 3.11.5,
  such as `>=3.11`, the default from `uv init` on Python 3.11.
- If a uv project also pins `issue-tracker-contract` itself, it must use
  `rev =` rather than `tag =` in `[tool.uv.sources]`. With `tag =`, locking
  fails with "conflicting URLs". See the
  [package README](../../team-4-trello-impl/README.md#installing).
