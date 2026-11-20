"""Callables that tests name in TRACKER_PROVIDER, CALENDAR_PROVIDER and
STORAGE_PROVIDER. They stand in for real providers in configuration tests."""

from pathlib import Path

from ospsd_team4_product.fakes import InMemoryDocumentStorage


class Provider:
    """A provider built with no arguments."""


def make_provider() -> Provider:
    return Provider()


def make_calendar(*, token_file: str | Path) -> Provider:
    """A calendar factory that, like Team 5's, needs a token file."""
    return Provider()


NOT_CALLABLE = 42


class TokenCalendar:
    """Records the token file it was built with; otherwise does nothing."""

    def __init__(self, token_file: str) -> None:
        self.token_file = token_file

    def create(self) -> None: ...
    def get(self) -> None: ...
    def replace(self) -> None: ...
    def delete(self) -> None: ...


def make_token_calendar(*, token_file: str) -> TokenCalendar:
    return TokenCalendar(token_file)


def failing_factory() -> Provider:
    raise RuntimeError("secret credential missing")


def storage_that_makes_parents() -> InMemoryDocumentStorage:
    """The in-memory storage, making missing parent folders as Dropbox does."""
    return InMemoryDocumentStorage(folders=(), make_parents=True)
