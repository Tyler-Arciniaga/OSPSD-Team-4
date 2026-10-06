## Team 4 - CS 3943 OSPSD

This is Team 4's repository for CS 3943 OSPSD.

We are building a FastAPI backend service for the Issue Tracker vertical, using Trello as our provider.

## Team Members

| Name | NetID |
| ------ | ------- |
| Tyler Arciniaga | ta2585 |
| Turki Almejhed | taa9292 |
| Joseph Jiminian | jj3945 |
| Chan Young Kang | ck1894 |
| Vanchi Nathan | vt2464 |

## Project Status

The service implements GET /issues/{issue_id} against Trello, translating a
card's `id` and `name` into the public `id` and `title` fields. Automated
tests run without credentials or network access.

GitHub Actions is configured to run linting, formatting checks, type checking, and automated tests on pushes and pull requests.

## Project Structure

- `app/main.py`: FastAPI application and issue retrieval endpoint.
- `app/models.py`: Public response model for an issue.
- `tests/test_get_issue.py`: Offline tests for retrieval, translation, and failures.
- `docs/api.md`: API contract, examples, and assumptions.
- `pyproject.toml`: Python requirement, dependencies, and tool configuration.
- `uv.lock`: Locked dependency versions for reproducible installation.
- `.github/workflows/ci.yml`: Automated linting, formatting, type checking, and tests.
- `AGENTS.md`: Contribution and release workflow.

## Installation

The project requires Python 3.11.5 or newer. Local checks were verified
with Python 3.13.5 and uv 0.12.17; CI uses those same versions.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run these commands from the repository root:

```bash
uv sync --locked --python 3.13.5
```

This creates a local `.venv` and installs application and development
dependencies using the versions in `uv.lock`.

Use `uv run` for project commands; manually activating `.venv` is
not necessary.

## Configuration

Use a dedicated Trello test account and a private test board containing a
card your team may change. The account granting the token must be able to
read the board.

1. Sign in to the test account. Create an app in the
   [Trello developer portal](https://trello.com/apps/admin), selecting
   "My app doesn't use Power-up capabilities." Open **Trello Auth** and
   generate an API key.
2. In a browser, replace `YOUR_API_KEY` in this URL with that key and approve
   read-only access (the token expires after one day):
   `https://trello.com/1/authorize?expiration=1day&scope=read&response_type=token&key=YOUR_API_KEY`.
3. Copy the credential template if you do not already have a `.env` file:

   ```bash
   cp -n .env.example .env
   chmod 600 .env
   ```

   Edit `.env` locally and fill in `TRELLO_API_KEY` and `TRELLO_TOKEN`.
   `.env` is ignored by Git. Do not commit credentials.

The startup command below loads `.env` into the service environment.
Existing exported environment variables take precedence over values in
the file. Restart the server after changing `.env`.

See [Trello authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)
for permissions and token revocation.

## Running the Service

From the repository root, run:

```bash
uv run --locked uvicorn app.main:app --reload --env-file .env
```

The service runs at http://127.0.0.1:8000.
Interactive API documentation is available at http://127.0.0.1:8000/docs.

The `--reload` option automatically restarts the server when code changes
during development. Press Ctrl+C in the terminal to stop it.

## API Usage

Retrieve an issue using `GET /issues/{issue_id}`, where `issue_id` is
the required issue identifier.

With the service running:

```bash
curl -i http://127.0.0.1:8000/issues/64f1c0a2b3d4e5f607182930
```

Expected status: `200 OK`

Expected response body:

```json
{
  "id": "64f1c0a2b3d4e5f607182930",
  "title": "Example issue"
}
```

Replace the example ID with the full ID of your test card. The returned
title is that card's current name. Unknown or malformed IDs return
`404 Not Found`; Trello rejection or unavailability produces HTTP 502.

See [the API contract](docs/api.md) for response fields, errors,
and assumptions.

## Code Checks and Tests

Run the following checks from the repository root. GitHub Actions runs
the same checks on pushes and pull requests.

### Linting

We use Ruff's default lint rules as a baseline for common coding mistakes,
including unused imports, undefined names, and import organization.
See [Ruff's default rules](https://docs.astral.sh/ruff/default-rules/)
for the complete list and explanations.

```bash
uv run --locked ruff check .
```

Ruff's version is locked in `uv.lock`. Review rule changes when upgrading,
investigate reported warnings, and review automated fixes before committing.

### Formatting

Check that Python files follow Ruff's formatting style without modifying them:

```bash
uv run --locked ruff format --check .
```

### Type Checking

Check for inconsistencies between type annotations and how values are used:

```bash
uv run --locked mypy app tests
```

### Automated Tests

Run the behavior tests:

```bash
uv run --locked pytest
```

Tests cover retrieval, field translation, authentication, and failures
without a running server, real credentials, or network access.

## Trello Verification

1. On the dedicated test board, create a card named `HW1 Level 2 test`.
   Open the card in a browser and append `.json` to its URL. Find the
   top-level `id` in that response: use this full 24-character ID, rather
   than the short link from the card URL.
2. Configure credentials as above and start the service. In another
   terminal, set `TRELLO_TEST_CARD_ID` to that full ID and run:

   ```bash
   curl -i "http://127.0.0.1:8000/issues/$TRELLO_TEST_CARD_ID"
   ```

   Expect HTTP 200 and exactly `{"id":"<your full card ID>","title":"HW1 Level 2 test"}`.
   Compare both values to the real card.
3. Rename the card to `HW1 Level 2 renamed` in Trello and repeat the request.
   Expect HTTP 200 with the new title and the same ID.
4. Run `curl -i http://127.0.0.1:8000/issues/does-not-exist`.
   Expect HTTP 404 and `{"detail":"Issue not found"}`.
5. Delete the disposable test card and any board created only for this check.
   Revoke the token from the test account's Settings > Applications page
   when finished, stop the server, clear credentials from `.env`, and run
   `unset TRELLO_API_KEY TRELLO_TOKEN` if you also exported them.

Each service request makes one [Get a Card](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-get)
request for `id,name`, with a 10-second socket timeout and no retries or
caching. See [the API contract](docs/api.md) for errors and supported IDs.

## Contributing

See [AGENTS.md](AGENTS.md) for the contribution and release workflow.
