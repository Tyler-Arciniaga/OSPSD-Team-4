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
- `test_delete_issue.py`: DELETE returns `204` with an empty, uncompressed
  body and no `ETag`; GET afterwards returns `404 not_found`, whether or not
  the issue was cached first; repeated DELETE, PATCH (including with a
  stale `If-Match`) and DELETE of unknown IDs return `404 not_found`; and
  deleting one issue leaves others readable.
- `test_list_issues.py`: `GET /issues` returns `{"issues": [...]}` with
  only `id` and `title` per issue; a new issue is among the five newest; a
  rename shows the current title; a deleted issue is left out; at most
  `limit` issues are returned; and a `limit` outside 1 to 100 returns
  `422 invalid_input` naming `limit`. The tests check only the issues they
  create, so other issues in the live list or team do not affect them.

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

The `post_issue` fixture sends `POST /issues` with any body and tracks
every issue that returns `201`. It attempts to delete each one after the
test, including when the test fails. `create_issue` builds on it for valid
titles. Tests that expect a create to be rejected also use `post_issue`, so
an issue the service wrongly creates is still removed.

Cleanup accepts `204` when it deletes the issue and `404` when the test
already deleted it. Other HTTP statuses produce a `CleanupWarning`
naming the issue so it can be removed by hand.

Linear deletion moves issues to trash; the service treats trashed issues
as missing. Trello deletion permanently removes the card.