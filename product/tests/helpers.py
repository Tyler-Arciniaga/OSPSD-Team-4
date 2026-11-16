"""Callables that tests name in TRACKER_PROVIDER, CALENDAR_PROVIDER and
STORAGE_PROVIDER. They stand in for real providers in configuration tests."""

from pathlib import Path


class Provider:
    """A provider built with no arguments."""


def make_provider() -> Provider:
    return Provider()


def make_calendar(*, token_file: str | Path) -> Provider:
    """A calendar factory that, like Team 5's, needs a token file."""
    return Provider()


NOT_CALLABLE = 42
