# Conformance and consumer workflow against both providers (November 13, 2026)

Run by Tyler, with Claude Code running the commands. Commit
`0768f0076ec10b5910268e5627786df369aadfd8` (the tip of `main`, unmodified). Times are UTC.

## Setup

- One local service process (`uv run --locked uvicorn app.main:app --env-file .env`
  on port 8010) with `GIT_SHA` set to the commit. `/readyz` reported `ready`,
  `provider: reachable` and that commit for both providers.
- Trello: the team's dedicated test list. Linear: the team's test team.
- The conformance suite (`contract/tests/conformance/`) and the
  [Provider Verification](../../README.md#provider-verification) workflow ran
  unchanged. Only `ISSUE_PROVIDER` changed between runs.
- pytest ran with `-W error::Warning`, so a `CleanupWarning` (an issue the
  suite could not delete) would have failed the run. None occurred.

## Results

| Check | Trello | Linear |
| --- | --- | --- |
| Conformance suite, all five operations | **56 passed**, 0 failed, 0 skipped (started 20:44:14) | **56 passed**, 0 failed, 0 skipped (started 20:45:03) |
| Consumer workflow (README) | Completed with the expected output | Completed with the expected output, identical to Trello's |
| Invalid credential | `403 access_denied`, log `cause: provider_status=401` | `403 access_denied`, log `cause: provider_status=401` |

Every issue the checks created was deleted by the checks themselves.

Raw output:

- [Trello conformance](provider-conformance/trello-conformance.txt),
  [Linear conformance](provider-conformance/linear-conformance.txt)
- [Trello workflow](provider-conformance/trello-workflow.txt),
  [Linear workflow](provider-conformance/linear-workflow.txt)
  (`diff` of the two files is empty)

For both providers, the invalid-credential request and its log line were:

```text
{"error":{"code":"access_denied","message":"The issue tracker denied access to this issue"}} 403
{"level": "WARNING", "operation": "get_issue", "error": "access_denied", "status": 403, "provider_involved": true, "cause": "provider_status=401", "write_outcome": null}
```

None of the captured output or server logs contained the API key, token or
Linear key; they were checked for the literal values before being added to the repository.
