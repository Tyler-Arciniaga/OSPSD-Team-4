# Conformance suite

The shared HTTP conformance suite talks to one running service over HTTP.
It uses only the public API, so the same tests run against a service
configured for Trello or Linear. Passing against both verifies that the
tested behavior follows the same contract.

## Coverage

- `test_create_issue.py`: `POST /issues` returns `201`, a `{id, title}` body
  and a matching `Location`; accepts a 256-character title; creates a new
  issue each time; and rejects invalid bodies with `422 invalid_input`.
- `test_get_issue.py`: GET of a created issue returns the same body with an
  `ETag`; repeated reads agree; unknown IDs return `404 not_found`.
- `test_delete_issue.py`: creates and fetches an issue to populate the
  cache, verifies DELETE returns `204` with an empty body, then checks
  that immediate GET and repeated DELETE return `404 not_found`.

Rename and conditional-update checks will be added when those operations
are implemented for both providers.

## Running it

The suite creates and deletes real issues, so it runs only when
`CONFORMANCE_BASE_URL` names a running service. Without that variable,
as in the ordinary CI test run, every conformance test is skipped.

1. Configure the provider as described in the main README's
   [Configuration](../../../README.md#configuration), using a dedicated
   test account or team.

2. Start one service process for that provider. The suite relies on its
   requests sharing one process's cache, so use a single worker:

   ```bash
   ISSUE_PROVIDER=trello uv run --locked uvicorn app.main:app --env-file .env
   ```

3. In another terminal, run the suite:

   ```bash
   CONFORMANCE_BASE_URL=http://127.0.0.1:8000 uv run --locked pytest contract/tests/conformance
   ```

4. Stop the server and repeat steps 2 and 3 with `ISSUE_PROVIDER=linear`,
   using valid Linear credentials in `.env`.

To run only the DELETE workflow:

```bash
CONFORMANCE_BASE_URL=http://127.0.0.1:8000 uv run --locked pytest contract/tests/conformance/test_delete_issue.py -q
```

## Cleanup

The `create_issue` fixture tracks each issue it creates and attempts to
delete it after the test, including when the test fails.

Cleanup accepts `204` when it deletes the issue and `404` when the test
already deleted it. Other HTTP statuses produce a `CleanupWarning`
naming the issue so it can be removed by hand.

Linear deletion moves issues to trash; the service treats trashed issues
as missing. Trello deletion permanently removes the card.