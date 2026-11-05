# issue-tracker-contract

The shared issue tracker contract: the `Issue`, `IssueCreate` and `IssueUpdate`
models, the `IssueProvider` interface, and the domain error types (`ErrorCode`,
`ServiceError`, the code-to-status mapping, and the JSON error response
models).

The package depends only on the Python standard library and pydantic. It must
not import the service (`app/`), a web framework, or a provider SDK;
`tests/test_import_boundary.py` enforces this.

- `src/issue_tracker_contract/`: the importable package.
- `tests/conformance/`: the shared conformance suite (not yet written).
