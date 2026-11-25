"""Build the three providers and the state store from configuration, once,
at startup.

This is the only place that calls a provider's constructor, and the only
place provider-specific constructor arguments are supplied (i.e. the calendar's
`token_file`). Swapping an implementation is a configuration change; the
orchestrator never sees which one was chosen.

A provider that cannot be built (for example Dropbox without its
credentials, which raises in its constructor without a network call) stops
startup with a ConfigError naming the variable. The provider's own message
is not repeated, since it may describe credentials. The same holds for a
state database that cannot be reached.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from calendar_contract import CalendarService
from cloud_storage_api import DocumentStorage
from issue_tracker_contract.interface import IssueProvider

from ospsd_team4_product.config import (
    CALENDAR_PROVIDER,
    MEMORY_STORE_URL,
    STATE_DB_URL,
    STORAGE_PROVIDER,
    TRACKER_PROVIDER,
    ConfigError,
    ProductConfig,
)
from ospsd_team4_product.store import PostgresStateStore, StateStore, StoreError

CALENDAR_OPERATIONS = ("create", "get", "replace", "delete")


@dataclass(frozen=True)
class Providers:
    tracker: IssueProvider
    calendar: CalendarService
    storage: DocumentStorage


def _build(variable: str, factory: Callable[[], object]) -> object:
    try:
        return factory()
    except Exception:  # noqa: BLE001  (any failure means unusable configuration)
        raise ConfigError(
            f"{variable} could not be built; check that provider's configuration"
        ) from None


def build_providers(config: ProductConfig) -> Providers:
    tracker = _build(TRACKER_PROVIDER, config.tracker_factory)
    if not isinstance(tracker, IssueProvider):
        raise ConfigError(f"{TRACKER_PROVIDER} must build an IssueProvider")

    token_file = config.calendar_token_file
    calendar = _build(
        CALENDAR_PROVIDER,
        (lambda: config.calendar_factory(token_file=token_file))
        if token_file is not None
        else config.calendar_factory,
    )
    # CalendarService is a plain Protocol, so check its operations directly.
    if not all(callable(getattr(calendar, op, None)) for op in CALENDAR_OPERATIONS):
        raise ConfigError(f"{CALENDAR_PROVIDER} must build a CalendarService")

    storage = _build(STORAGE_PROVIDER, config.storage_factory)
    if not isinstance(storage, DocumentStorage):
        raise ConfigError(f"{STORAGE_PROVIDER} must build a DocumentStorage")

    return Providers(
        tracker=tracker,
        calendar=cast(CalendarService, calendar),  # checked above
        storage=storage,
    )


def build_store(config: ProductConfig) -> StateStore:
    """Connect to the state database (creating its tables), or the in-memory
    store for MEMORY_STORE_URL. Raises ConfigError if it cannot be reached."""
    if config.state_db_url == MEMORY_STORE_URL:
        # Imported by name, like a provider, so only configuration selects a fake.
        fakes = importlib.import_module("ospsd_team4_product.fakes")
        return cast(StateStore, fakes.InMemoryStateStore())
    try:
        return PostgresStateStore.connect(config.state_db_url)
    except StoreError:
        raise ConfigError(
            f"Cannot connect to state database; check {STATE_DB_URL}"
        ) from None
