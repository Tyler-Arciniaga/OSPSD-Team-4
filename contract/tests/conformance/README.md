# Conformance suite

The shared HTTP conformance suite. It talks to one running service over
HTTP and makes no assumption about the provider, so it runs unchanged
against a service configured for Trello and for Linear. Passing on
both shows that a consumer sees the same behavior from all providers.

## Coverage

- `test_create_issue.py`: `POST /issues` returns `201`, a `{id, title}` body
  and a matching `Location`; accepts a 256-character title; creates a new issue
  each time; and rejects invalid bodies with `422 invalid_input`.
- `test_get_issue.py`: `GET` of a created issue returns the same body with an
  `ETag`; repeated reads agree; unknown IDs return `404 not_found`.

Rename, delete, and the post-delete `404` checks will be added as those
operations are implemented for both providers.

## Running it

The suite creates real issues, so it runs only when `CONFORMANCE_BASE_URL`
names a running service. Without it (as in the case of CI) every test is skipped.

1. Configure the provider as in the README's
   [Configuration](../../../README.md#configuration), using a dedicated test
   account.
2. Start one service process for that provider. The suite relies on its
   requests sharing one process's cache, so use a single worker:

   ```bash
   ISSUE_PROVIDER=trello uv run --locked uvicorn app.main:app --env-file .env
   ```

3. In another terminal, run the suite against it:

   ```bash
   CONFORMANCE_BASE_URL=http://127.0.0.1:8000 uv run --locked pytest contract/tests/conformance
   ```

4. Stop the server and repeat steps 2 and 3 with `ISSUE_PROVIDER=linear`.

Each issue a test creates is deleted through `DELETE /issues/{id}` after the
test, even when it fails. A delete that does not succeed is reported as a
`CleanupWarning` naming the issue, so remove it by hand. Until Linear's
delete is implemented, every issue created against Linear is reported this
way.
