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
from app.telemetry import Telemetry
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
    """Create shared application objects and release them at shutdown."""
    try:
        config = validate_startup_config()
    except ConfigError as exc:
        logger.critical(
            json.dumps({"event": "startup_config_invalid", "message": str(exc)})
        )
        raise

    adapter = _build_adapter(config.provider)

    app.state.issue_provider = CachedIssueProvider(
        adapter,
        ttl_seconds=30,
        max_entries=100,
    )
    app.state.idempotency_store = IdempotencyStore(config.idempotency_db_path)
    app.state.provider_probe = ProviderProbe(adapter.ping)

    try:
        telemetry = Telemetry()
    except Exception:
        adapter.close()
        raise

    # One telemetry setup per application process.
    app.state.telemetry = telemetry
    app.state.tracer = telemetry.tracer

    try:
        yield
    finally:
        try:
            adapter.close()
        finally:
            telemetry.shutdown()

            del app.state.issue_provider
            del app.state.idempotency_store
            del app.state.provider_probe
            del app.state.tracer
            del app.state.telemetry


def get_issue_provider(request: Request) -> IssueProvider:
    """Return the shared provider created during application startup."""
    return request.app.state.issue_provider


def get_idempotency_store(request: Request) -> IdempotencyStore:
    """Return the idempotency key store created during application startup."""
    return request.app.state.idempotency_store


def get_provider_probe(request: Request) -> ProviderProbe:
    """Return the provider reachability check created during startup."""
    return request.app.state.provider_probe
