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

The service currently implements GET /issues/{issue_id} using fixed data, with API documentation and automated tests. Trello integration is pending.

GitHub Actions is configured to run linting, formatting checks, type checking, and automated tests on pushes and pull requests.

## Project Structure

- `app/main.py`: FastAPI application and issue retrieval endpoint.
- `app/models.py`: Public response model for an issue.
- `tests/test_get_issue.py`: Tests for successful retrieval and unknown issues.
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

The current implementation uses fixed data and requires no credentials
or environment variables.

To be added with the Trello integration: test-account setup, required
permissions, and instructions for supplying credentials locally.

Do not commit credentials to the repository.

## Running the Service

From the repository root, run:

```bash
uv run --locked uvicorn app.main:app --reload
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

The current implementation returns fixed data for this example ID.
Unknown IDs return `404 Not Found`.

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

The current tests verify successful issue retrieval and a 404 response
for an unknown issue. They require no running server, Trello credentials,
or live network access.

## Trello Verification

To be added upon completion of Level 2: instructions for verifying the operation against a real Trello test account, including the request to run, expected result, required test data, and cleanup steps.

## Contributing

See [AGENTS.md](AGENTS.md) for the contribution and release workflow.