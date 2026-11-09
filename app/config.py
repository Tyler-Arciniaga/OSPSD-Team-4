"""Startup configuration check.

Before the service builds a provider, it checks that every variable the
selected provider needs is set and, for a provider that uses the idempotency
store, that the store's directory is writable. A failure names the variables concerned and never includes a value,
so the message is safe to print in deployment logs.

The adapters still check their own settings on every call, as a second line
of defense.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Where idempotency keys are kept when IDEMPOTENCY_DB_PATH is not set.
DEFAULT_IDEMPOTENCY_DB_PATH = "idempotency.sqlite3"

DEFAULT_PROVIDER = "trello"

# Variables each provider needs to serve every implemented operation. Only the
# selected provider's variables are required.
REQUIRED_VARIABLES: Mapping[str, tuple[str, ...]] = {
    "trello": ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"),
    "linear": ("LINEAR_API_KEY", "LINEAR_TEAM_ID"),
}

# Providers whose adapter supports idempotent creation, and so writes to the
# idempotency key store. Only these need a writable IDEMPOTENCY_DB_PATH
# directory; the others refuse an Idempotency-Key before opening the store.
PROVIDERS_USING_IDEMPOTENCY_STORE = frozenset({"trello"})


class ConfigError(ValueError):
    """Required configuration is missing or unusable.

    The message names variables only, never their values.
    """


@dataclass(frozen=True)
class StartupConfig:
    provider: str
    idempotency_db_path: str


def _directory_is_writable(db_path: str) -> bool:
    directory = Path(db_path).resolve().parent
    return directory.is_dir() and os.access(directory, os.W_OK | os.X_OK)


def validate_startup_config(
    environ: Mapping[str, str] = os.environ,
) -> StartupConfig:
    """Return the validated startup settings, or raise ConfigError.

    An unset or empty ISSUE_PROVIDER selects Trello by default. Empty values count as
    missing.
    """
    provider = environ.get("ISSUE_PROVIDER") or DEFAULT_PROVIDER
    if provider not in REQUIRED_VARIABLES:
        raise ConfigError("ISSUE_PROVIDER must be 'trello' or 'linear'")

    missing = [name for name in REQUIRED_VARIABLES[provider] if not environ.get(name)]
    if missing:
        raise ConfigError(
            f"Missing required configuration for {provider}: {', '.join(missing)}"
        )

    db_path = environ.get("IDEMPOTENCY_DB_PATH") or DEFAULT_IDEMPOTENCY_DB_PATH
    if provider in PROVIDERS_USING_IDEMPOTENCY_STORE and not _directory_is_writable(
        db_path
    ):
        raise ConfigError(
            "IDEMPOTENCY_DB_PATH directory does not exist or is not writable"
        )

    return StartupConfig(provider=provider, idempotency_db_path=db_path)
