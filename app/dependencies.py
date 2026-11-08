import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.cache import CachedIssueProvider
from app.config import ConfigError, validate_startup_config
from app.idempotency import IdempotencyStore
from app.interface import ClosableIssueProvider
from app.providers.linear_provider import LinearIssueProvider
from app.providers.trello_provider import TrelloIssueProvider
from issue_tracker_contract.interface import IssueProvider

logger = logging.getLogger("app.config")


def _build_adapter(provider: str) -> ClosableIssueProvider:
    """Build the adapter for a validated provider name.

    Construction sends no request to the provider.
    """
    if provider == "trello":
        return TrelloIssueProvider()
    if provider == "linear":
        return LinearIssueProvider()
    raise ValueError("ISSUE_PROVIDER must be 'trello' or 'linear'")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the shared provider at startup and release it at shutdown."""

    # Refuse to start with missing configuration. The message names the
    # variables only, so it is safe in deployment logs.
    try:
        config = validate_startup_config()
    except ConfigError as exc:
        logger.critical(
            json.dumps({"event": "startup_config_invalid", "message": str(exc)})
        )
        raise

    # Create one provider adapter wrapped in a cache.
    # Creating these objects does not send a request to the provider.
    # Each application process gets its own provider and cache.
    adapter = _build_adapter(config.provider)
    app.state.issue_provider = CachedIssueProvider(
        adapter,
        ttl_seconds=30,
        max_entries=100,
    )

    # The key store keeps no state in memory, only a file path: every
    # process serving the API must use the same file, on storage that
    # survives restarts. The file is opened only when a request has a key.
    app.state.idempotency_store = IdempotencyStore(config.idempotency_db_path)

    try:
        # Pause this function while the application handles requests.
        # The saved provider remains available and is reused.
        yield
    finally:
        # During shutdown, remove the application's reference to it.
        # This does not delete any issues or other data from the provider.
        del app.state.issue_provider
        del app.state.idempotency_store
        # Whoever creates the adapter closes it. Every adapter implements
        # close(), so shutdown is always called regardless of the provider configured.
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
