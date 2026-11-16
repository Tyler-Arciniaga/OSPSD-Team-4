"""Startup configuration: provider import paths, token file and defaults."""

from datetime import time
from pathlib import Path

import pytest

from ospsd_team4_product.config import (
    FALLBACK_MINUTES,
    FALLBACK_TIMEZONE,
    ConfigError,
    WorkingWindow,
    load_config,
    takes_token_file,
)
from tests import helpers

BASE = {
    "TRACKER_PROVIDER": "tests.helpers:Provider",
    "CALENDAR_PROVIDER": "tests.helpers:make_provider",
    "STORAGE_PROVIDER": "tests.helpers:make_provider",
}


def test_valid_config_resolves_callables_and_defaults() -> None:
    config = load_config(BASE)

    assert config.tracker_factory is helpers.Provider
    assert config.calendar_factory is helpers.make_provider
    assert config.storage_factory is helpers.make_provider
    assert config.calendar_token_file is None
    assert config.default_working_window == WorkingWindow(time(9, 0), time(17, 0))
    assert config.default_minutes == FALLBACK_MINUTES
    assert config.default_timezone == FALLBACK_TIMEZONE


def test_explicit_defaults_are_used() -> None:
    config = load_config(
        BASE
        | {
            "DEFAULT_WORKING_WINDOW": "08:30-12:00",
            "DEFAULT_MINUTES": "45",
            "DEFAULT_TIMEZONE": "Europe/Berlin",
        }
    )

    assert config.default_working_window == WorkingWindow(time(8, 30), time(12, 0))
    assert config.default_minutes == 45
    assert config.default_timezone == "Europe/Berlin"


@pytest.mark.parametrize(
    "variable", ["TRACKER_PROVIDER", "CALENDAR_PROVIDER", "STORAGE_PROVIDER"]
)
@pytest.mark.parametrize("value", [None, ""])
def test_missing_provider_names_the_variable(variable: str, value: str | None) -> None:
    environ = dict(BASE)
    if value is None:
        del environ[variable]
    else:
        environ[variable] = value

    with pytest.raises(
        ConfigError, match=f"Missing required configuration: {variable}"
    ):
        load_config(environ)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("tests.helpers", "must have the form module:attr"),
        ("tests.helpers:", "must have the form module:attr"),
        ("not a path:x", "must have the form module:attr"),
        ("tests.no_such_module:Provider", "cannot be imported"),
        ("tests.helpers:Missing", "does not exist"),
        ("tests.helpers:NOT_CALLABLE", "must name a callable"),
    ],
)
def test_bad_provider_path_names_variable_but_not_value(
    value: str, message: str
) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(BASE | {"STORAGE_PROVIDER": value})

    assert str(error.value).startswith("STORAGE_PROVIDER")
    assert message in str(error.value)
    assert value not in str(error.value)


def test_calendar_factory_with_token_file_requires_it() -> None:
    with pytest.raises(
        ConfigError, match="Missing required configuration: CALENDAR_TOKEN_FILE"
    ):
        load_config(BASE | {"CALENDAR_PROVIDER": "tests.helpers:make_calendar"})


def test_token_file_is_passed_through(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text("{}")

    config = load_config(
        BASE
        | {
            "CALENDAR_PROVIDER": "tests.helpers:make_calendar",
            "CALENDAR_TOKEN_FILE": str(token),
        }
    )

    assert config.calendar_token_file == str(token)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("relative/token.json", "must be an absolute path"),
        ("/no/such/token.json", "readable file"),
    ],
)
def test_unusable_token_file_names_the_variable(value: str, message: str) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(
            BASE
            | {
                "CALENDAR_PROVIDER": "tests.helpers:make_calendar",
                "CALENDAR_TOKEN_FILE": value,
            }
        )

    assert str(error.value).startswith("CALENDAR_TOKEN_FILE")
    assert message in str(error.value)
    assert value not in str(error.value)


def test_token_file_ignored_when_factory_does_not_take_it() -> None:
    config = load_config(BASE | {"CALENDAR_TOKEN_FILE": "/no/such/token.json"})

    assert config.calendar_token_file is None


def test_takes_token_file_detects_keyword() -> None:
    assert takes_token_file(helpers.make_calendar)
    assert not takes_token_file(helpers.make_provider)
    assert not takes_token_file(helpers.Provider)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DEFAULT_WORKING_WINDOW", "9-17"),
        ("DEFAULT_WORKING_WINDOW", "17:00-09:00"),
        ("DEFAULT_WORKING_WINDOW", "09:00-09:00"),
        ("DEFAULT_WORKING_WINDOW", "25:00-26:00"),
        ("DEFAULT_MINUTES", "0"),
        ("DEFAULT_MINUTES", "-5"),
        ("DEFAULT_MINUTES", "ninety"),
        ("DEFAULT_MINUTES", "1441"),
        ("DEFAULT_TIMEZONE", "Mars/Olympus"),
        ("DEFAULT_TIMEZONE", "../etc"),
    ],
)
def test_malformed_default_names_the_variable(variable: str, value: str) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(BASE | {variable: value})

    assert str(error.value).startswith(variable)


def test_real_partner_import_paths_resolve(tmp_path: Path) -> None:
    """The deployed values resolve; Team 5's factory requires the token file.

    Nothing is constructed, so no credentials or network are needed.
    """
    token = tmp_path / "token.json"
    token.write_text("{}")
    real = {
        "TRACKER_PROVIDER": "team_4_issue_tracker_trello:TrelloIssueProvider",
        "CALENDAR_PROVIDER": "team5_calendar:create_calendar_service",
        "STORAGE_PROVIDER": "dropbox_client_impl:DropboxDocumentStorage",
    }

    with pytest.raises(ConfigError, match="CALENDAR_TOKEN_FILE"):
        load_config(real)
    config = load_config(real | {"CALENDAR_TOKEN_FILE": str(token)})

    assert config.calendar_token_file == str(token)
    assert config.tracker_factory.__name__ == "TrelloIssueProvider"
    assert config.storage_factory.__name__ == "DropboxDocumentStorage"
