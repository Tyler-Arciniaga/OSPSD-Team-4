"""Building providers from configuration at startup."""

from pathlib import Path

import pytest
from calendar_contract import CalendarError, CreateEvent, ErrorCode

from ospsd_team4_product.config import ConfigError, load_config
from ospsd_team4_product.factories import build_providers
from ospsd_team4_product.fakes import (
    InMemoryCalendarService,
    InMemoryDocumentStorage,
    InMemoryIssueProvider,
)

FAKES = {
    "TRACKER_PROVIDER": "ospsd_team4_product.fakes:InMemoryIssueProvider",
    "CALENDAR_PROVIDER": "ospsd_team4_product.fakes:InMemoryCalendarService",
    "STORAGE_PROVIDER": "ospsd_team4_product.fakes:InMemoryDocumentStorage",
    "STATE_DB_URL": "memory://",
}


def test_fakes_are_selected_through_the_same_variables() -> None:
    providers = build_providers(load_config(FAKES))

    assert isinstance(providers.tracker, InMemoryIssueProvider)
    assert isinstance(providers.calendar, InMemoryCalendarService)
    assert isinstance(providers.storage, InMemoryDocumentStorage)


def test_controlled_calendar_outage_is_selected_by_configuration() -> None:
    """The second create fails with a retryable outage; later calls succeed."""
    providers = build_providers(
        load_config(
            FAKES
            | {
                "CALENDAR_PROVIDER": (
                    "ospsd_team4_product.fakes:calendar_outage_on_second_create"
                )
            }
        )
    )
    calendar = providers.calendar
    request = CreateEvent(
        title="Focus: x",
        start="2026-11-20T09:00:00-05:00",
        end="2026-11-20T10:00:00-05:00",
    )

    first = calendar.create(request)
    with pytest.raises(CalendarError) as raised:
        calendar.create(request)
    third = calendar.create(request)

    assert raised.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert raised.value.retryable is True
    assert calendar.get(first.id) == first
    assert calendar.get(third.id) == third


def test_token_file_is_passed_to_the_calendar_factory(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text("{}")

    providers = build_providers(
        load_config(
            FAKES
            | {
                "CALENDAR_PROVIDER": "tests.helpers:make_token_calendar",
                "CALENDAR_TOKEN_FILE": str(token),
            }
        )
    )

    assert providers.calendar.token_file == str(token)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("TRACKER_PROVIDER", "tests.helpers:Provider", "must build an IssueProvider"),
        ("CALENDAR_PROVIDER", "tests.helpers:Provider", "must build a CalendarService"),
        ("STORAGE_PROVIDER", "tests.helpers:Provider", "must build a DocumentStorage"),
        ("STORAGE_PROVIDER", "tests.helpers:failing_factory", "could not be built"),
    ],
)
def test_wrong_or_failing_provider_stops_startup(
    variable: str, value: str, message: str
) -> None:
    with pytest.raises(ConfigError) as error:
        build_providers(load_config(FAKES | {variable: value}))

    assert str(error.value).startswith(variable)
    assert message in str(error.value)
    assert "secret" not in str(error.value)


def test_dropbox_without_credentials_stops_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "DROPBOX_APP_KEY",
        "DROPBOX_APP_SECRET",
        "DROPBOX_REFRESH_TOKEN",
        "DROPBOX_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    # Point the package's optional token file somewhere empty.
    monkeypatch.setenv("DROPBOX_TOKEN_FILE", str(tmp_path / "none.json"))

    with pytest.raises(ConfigError, match="^STORAGE_PROVIDER could not be built"):
        build_providers(
            load_config(
                FAKES
                | {"STORAGE_PROVIDER": "dropbox_client_impl:DropboxDocumentStorage"}
            )
        )
