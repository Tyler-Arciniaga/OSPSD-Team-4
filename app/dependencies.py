import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.cache import CachedIssueProvider
from app.config import ConfigError, validate_startup_config
from app.health import ProviderProbe
from app.idempotency import IdempotencyStore
from app.interface import ClosableIssueProvider
from app.providers.linear_provider import LinearIssueProvider
from app.providers.trello_provider import TrelloIssueProvider
from app.telemetry import create_tracer_provider
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

    # Readiness reports whether the provider answers, checked at most every
    # 30 seconds. The check is sent only when /readyz is requested.
    app.state.provider_probe = ProviderProbe(adapter.ping)

    tracer_provider = create_tracer_provider()
    app.state.tracer = tracer_provider.get_tracer("issue-service")

    try:
        # Pause this function while the application handles requests.
        # The saved provider remains available and is reused.
        yield
    finally:
        # During shutdown, remove the application's reference to it.
        # This does not delete any issues or other data from the provider.
        del app.state.issue_provider
        del app.state.idempotency_store
        del app.state.provider_probe
        # Whoever creates the adapter closes it. Every adapter implements
        # close(), so shutdown is always called regardless of the provider configured.
        adapter.close()
        tracer_provider.shutdown()
        del app.state.tracer


def get_issue_provider(request: Request) -> IssueProvider:
    """Return the shared provider created during application startup."""

    # FastAPI supplies the incoming Request object automatically.
    # request.app refers to the application handling that request.
    # Return its saved provider instead of creating a new, empty cache.
    return request.app.state.issue_provider


def get_idempotency_store(request: Request) -> IdempotencyStore:
    """Return the idempotency key store created during application startup."""
    return request.app.state.idempotency_store


def get_provider_probe(request: Request) -> ProviderProbe:
    """Return the provider reachability check created during startup."""
    return request.app.state.provider_probe
