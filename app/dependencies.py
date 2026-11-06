import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.cache import CachedIssueProvider
from app.idempotency import IdempotencyStore
from app.linear_provider import LinearIssueProvider
from app.provider import TrelloIssueProvider
from issue_tracker_contract.interface import IssueProvider

# Where idempotency keys are kept when IDEMPOTENCY_DB_PATH is not set.
DEFAULT_IDEMPOTENCY_DB_PATH = "idempotency.sqlite3"


def _build_adapter() -> TrelloIssueProvider | LinearIssueProvider:
    """Build the one adapter named by ISSUE_PROVIDER (default: trello).

    Construction sends no request to the provider.
    """
    choice = os.environ.get("ISSUE_PROVIDER") or "trello"
    if choice == "trello":
        return TrelloIssueProvider()
    if choice == "linear":
        return LinearIssueProvider()
    raise ValueError("ISSUE_PROVIDER must be 'trello' or 'linear'")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the shared provider at startup and release it at shutdown."""

    # Create one provider adapter wrapped in a cache.
    # Creating these objects does not send a request to the provider.
    # Each application process gets its own provider and cache.
    adapter = _build_adapter()
    app.state.issue_provider = CachedIssueProvider(
        adapter,
        ttl_seconds=30,
        max_entries=100,
    )

    # The key store keeps no state in memory, only a file path: every
    # process serving the API must use the same file, on storage that
    # survives restarts. The file is opened only when a request has a key.
    app.state.idempotency_store = IdempotencyStore(
        os.environ.get("IDEMPOTENCY_DB_PATH") or DEFAULT_IDEMPOTENCY_DB_PATH
    )

    try:
        # Pause this function while the application handles requests.
        # The saved provider remains available and is reused.
        yield
    finally:
        # During shutdown, remove the application's reference to it.
        # This does not delete any issues or other data from the provider.
        del app.state.issue_provider
        del app.state.idempotency_store
        # Linear holds an HTTP client; Trello opens a connection per request.
        if isinstance(adapter, LinearIssueProvider):
            adapter.close()


def get_issue_provider(request: Request) -> IssueProvider:
    """Return the shared provider created during application startup."""

    # FastAPI supplies the incoming Request object automatically.
    # request.app refers to the application handling that request.
    # Return its saved provider instead of creating a new, empty cache.
    return request.app.state.issue_provider


def get_idempotency_store(request: Request) -> IdempotencyStore:
    """Return the idempotency key store created during application startup."""
    return request.app.state.idempotency_store
