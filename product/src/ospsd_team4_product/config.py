"""Startup configuration.

Each provider is chosen by an import path of the form `module:attr`, naming a
callable that returns the implementation (a class or a factory function).
Resolving the paths happens here, through importlib, so no module in this
package imports a provider package by name.

A failure raises ConfigError naming the variable concerned. Messages never
include a variable's value, so they are safe to print in deployment logs,
following the HW2 service's rule (app/config.py).
"""

import importlib
import inspect
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TRACKER_PROVIDER = "TRACKER_PROVIDER"
CALENDAR_PROVIDER = "CALENDAR_PROVIDER"
STORAGE_PROVIDER = "STORAGE_PROVIDER"
CALENDAR_TOKEN_FILE = "CALENDAR_TOKEN_FILE"
DEFAULT_WORKING_WINDOW = "DEFAULT_WORKING_WINDOW"
DEFAULT_MINUTES = "DEFAULT_MINUTES"
DEFAULT_TIMEZONE = "DEFAULT_TIMEZONE"

# Used when the matching variable is unset or empty.
FALLBACK_WORKING_WINDOW = "09:00-17:00"
FALLBACK_MINUTES = 90
FALLBACK_TIMEZONE = "America/New_York"

# Longest focus block a caller or the configuration may ask for: one day.
MAX_MINUTES = 24 * 60

# A calendar factory that takes this keyword argument is given the value of
# CALENDAR_TOKEN_FILE (Team 5's create_calendar_service does).
TOKEN_FILE_PARAMETER = "token_file"

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_IMPORT_PATH = re.compile(
    rf"{_IDENTIFIER}(\.{_IDENTIFIER})*:{_IDENTIFIER}(\.{_IDENTIFIER})*"
)
_WINDOW = re.compile(r"(?P<start>[0-9]{2}:[0-9]{2})-(?P<end>[0-9]{2}:[0-9]{2})")


class ConfigError(ValueError):
    """Required configuration is missing or unusable.

    The message names variables only, never their values.
    """


@dataclass(frozen=True)
class WorkingWindow:
    """Local wall-clock hours in which focus blocks may be placed."""

    start: time
    end: time


@dataclass(frozen=True)
class ProductConfig:
    tracker_factory: Callable[..., object]
    calendar_factory: Callable[..., object]
    storage_factory: Callable[..., object]

    # Set only when the calendar factory takes `token_file`.
    calendar_token_file: str | None

    default_working_window: WorkingWindow
    default_minutes: int
    default_timezone: str


def resolve_import_path(
    variable: str, environ: Mapping[str, str]
) -> Callable[..., object]:
    """Import the callable named by `variable` as `module:attr`."""
    value = environ.get(variable)
    if not value:
        raise ConfigError(f"Missing required configuration: {variable}")
    if not _IMPORT_PATH.fullmatch(value):
        raise ConfigError(f"{variable} must have the form module:attr")
    module_name, _, attr_path = value.partition(":")
    try:
        target: object = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001  (any import failure is a configuration problem)
        raise ConfigError(
            f"{variable} names a module that cannot be imported"
        ) from None
    for attr in attr_path.split("."):
        try:
            target = getattr(target, attr)
        except AttributeError:
            raise ConfigError(
                f"{variable} names an attribute that does not exist"
            ) from None
    if not callable(target):
        raise ConfigError(f"{variable} must name a callable")
    return target


def takes_token_file(factory: Callable[..., object]) -> bool:
    """Whether the factory accepts a `token_file` keyword argument."""
    try:
        parameters = inspect.signature(factory).parameters
    except TypeError, ValueError:
        return False
    parameter = parameters.get(TOKEN_FILE_PARAMETER)
    return parameter is not None and parameter.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def _token_file(environ: Mapping[str, str]) -> str:
    value = environ.get(CALENDAR_TOKEN_FILE)
    if not value:
        raise ConfigError(f"Missing required configuration: {CALENDAR_TOKEN_FILE}")
    if not os.path.isabs(value):
        raise ConfigError(f"{CALENDAR_TOKEN_FILE} must be an absolute path")
    if not os.path.isfile(value) or not os.access(value, os.R_OK):
        raise ConfigError(f"{CALENDAR_TOKEN_FILE} does not name a readable file")
    return value


def parse_working_window(value: str) -> WorkingWindow:
    """Parse `HH:MM-HH:MM`; raise ValueError if malformed or not increasing."""
    match = _WINDOW.fullmatch(value)
    if match is None:
        raise ValueError("working window must have the form HH:MM-HH:MM")
    start = time.fromisoformat(match["start"])
    end = time.fromisoformat(match["end"])
    if start >= end:
        raise ValueError("working window start must be before its end")
    return WorkingWindow(start=start, end=end)


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return False
    return True


def _working_window(environ: Mapping[str, str]) -> WorkingWindow:
    value = environ.get(DEFAULT_WORKING_WINDOW) or FALLBACK_WORKING_WINDOW
    try:
        return parse_working_window(value)
    except ValueError:
        raise ConfigError(
            f"{DEFAULT_WORKING_WINDOW} must have the form HH:MM-HH:MM with start before end"
        ) from None


def _minutes(environ: Mapping[str, str]) -> int:
    value = environ.get(DEFAULT_MINUTES)
    if not value:
        return FALLBACK_MINUTES
    if not value.isdigit() or not 1 <= int(value) <= MAX_MINUTES:
        raise ConfigError(
            f"{DEFAULT_MINUTES} must be a whole number from 1 to {MAX_MINUTES}"
        )
    return int(value)


def _timezone(environ: Mapping[str, str]) -> str:
    value = environ.get(DEFAULT_TIMEZONE) or FALLBACK_TIMEZONE
    if not is_valid_timezone(value):
        raise ConfigError(f"{DEFAULT_TIMEZONE} must be an IANA time zone name")
    return value


def load_config(environ: Mapping[str, str] = os.environ) -> ProductConfig:
    """Return the validated configuration, or raise ConfigError."""
    tracker_factory = resolve_import_path(TRACKER_PROVIDER, environ)
    calendar_factory = resolve_import_path(CALENDAR_PROVIDER, environ)
    storage_factory = resolve_import_path(STORAGE_PROVIDER, environ)
    token_file = _token_file(environ) if takes_token_file(calendar_factory) else None
    return ProductConfig(
        tracker_factory=tracker_factory,
        calendar_factory=calendar_factory,
        storage_factory=storage_factory,
        calendar_token_file=token_file,
        default_working_window=_working_window(environ),
        default_minutes=_minutes(environ),
        default_timezone=_timezone(environ),
    )
