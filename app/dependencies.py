import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.cache import CachedIssueProvider
from app.idempotency import IdempotencyStore
from app.interface import IssueProvider
from app.provider import TrelloIssueProvider

# Where idempotency keys are kept when IDEMPOTENCY_DB_PATH is not set.
DEFAULT_IDEMPOTENCY_DB_PATH = "idempotency.sqlite3"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the shared provider at startup and release it at shutdown."""

    # Create one Trello provider wrapped in a cache.
    # Creating these objects does not send a request to Trello.
    # Each application process gets its own provider and cache.
    app.state.issue_provider = CachedIssueProvider(
        TrelloIssueProvider(),
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
        # This does not delete any cards or other data from Trello.
        del app.state.issue_provider
        del app.state.idempotency_store


def get_issue_provider(request: Request) -> IssueProvider:
    """Return the shared provider created during application startup."""

    # FastAPI supplies the incoming Request object automatically.
    # request.app refers to the application handling that request.
    # Return its saved provider instead of creating a new, empty cache.
    return request.app.state.issue_provider


def get_idempotency_store(request: Request) -> IdempotencyStore:
    """Return the idempotency key store created during application startup."""
    return request.app.state.idempotency_store
