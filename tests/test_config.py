"""Startup refuses to run with missing configuration, naming only variables."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dependencies, main
from app.config import (
    PROVIDERS_USING_IDEMPOTENCY_STORE,
    REQUIRED_VARIABLES,
    ConfigError,
    StartupConfig,
    validate_startup_config,
)
from app.interface import IdempotentIssueProvider
from tests.conftest import FAKE_STARTUP_CONFIG

TRELLO_VARIABLES = ["TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"]
LINEAR_VARIABLES = ["LINEAR_API_KEY", "LINEAR_TEAM_ID"]
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _environ(tmp_path: Path, provider: str | None, **overrides: str) -> dict[str, str]:
    environ = {
        **FAKE_STARTUP_CONFIG,
        "IDEMPOTENCY_DB_PATH": str(tmp_path / "idempotency.sqlite3"),
    }
    if provider is not None:
        environ["ISSUE_PROVIDER"] = provider
    environ.update(overrides)
    return environ


def _assert_no_fake_value(text: str) -> None:
    for value in FAKE_STARTUP_CONFIG.values():
        assert value not in text


@pytest.mark.parametrize(
    ("provider", "expected"),
    [(None, "trello"), ("", "trello"), ("trello", "trello"), ("linear", "linear")],
)
def test_valid_configuration_is_accepted(
    tmp_path: Path, provider: str | None, expected: str
) -> None:
    environ = _environ(tmp_path, provider)

    assert validate_startup_config(environ) == StartupConfig(
        provider=expected, idempotency_db_path=environ["IDEMPOTENCY_DB_PATH"]
    )


@pytest.mark.parametrize(
    ("provider", "variable"),
    [("trello", name) for name in TRELLO_VARIABLES]
    + [("linear", name) for name in LINEAR_VARIABLES],
)
@pytest.mark.parametrize("removal", ["unset", "empty"])
def test_each_missing_variable_is_named_without_values(
    tmp_path: Path, provider: str, variable: str, removal: str
) -> None:
    environ = _environ(tmp_path, provider)
    if removal == "unset":
        del environ[variable]
    else:
        environ[variable] = ""

    with pytest.raises(ConfigError) as caught:
        validate_startup_config(environ)

    message = str(caught.value)
    assert variable in message
    _assert_no_fake_value(message)


def test_all_missing_variables_are_named_together(tmp_path: Path) -> None:
    environ = _environ(tmp_path, "trello")
    for name in TRELLO_VARIABLES:
        del environ[name]

    with pytest.raises(ConfigError) as caught:
        validate_startup_config(environ)

    for name in TRELLO_VARIABLES:
        assert name in str(caught.value)


@pytest.mark.parametrize(
    ("provider", "other_provider_variables"),
    [("trello", LINEAR_VARIABLES), ("linear", TRELLO_VARIABLES)],
)
def test_only_the_selected_provider_is_required(
    tmp_path: Path, provider: str, other_provider_variables: list[str]
) -> None:
    environ = _environ(tmp_path, provider)
    for name in other_provider_variables:
        del environ[name]

    assert validate_startup_config(environ).provider == provider


def test_unknown_provider_is_rejected_without_echoing_it(tmp_path: Path) -> None:
    environ = _environ(tmp_path, "secret-looking-value-5d1")

    with pytest.raises(ConfigError, match="ISSUE_PROVIDER") as caught:
        validate_startup_config(environ)

    assert "secret-looking-value-5d1" not in str(caught.value)


def test_missing_store_directory_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "idempotency.sqlite3"
    environ = _environ(tmp_path, "trello", IDEMPOTENCY_DB_PATH=str(path))

    with pytest.raises(ConfigError, match="IDEMPOTENCY_DB_PATH") as caught:
        validate_startup_config(environ)

    assert str(path) not in str(caught.value)


def test_store_directory_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("")
    path = not_a_directory / "idempotency.sqlite3"
    environ = _environ(tmp_path, "trello", IDEMPOTENCY_DB_PATH=str(path))

    with pytest.raises(ConfigError, match="IDEMPOTENCY_DB_PATH"):
        validate_startup_config(environ)


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX permissions and a non-root user",
)
def test_read_only_store_directory_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "read-only"
    directory.mkdir()
    directory.chmod(0o500)
    environ = _environ(
        tmp_path,
        "trello",
        IDEMPOTENCY_DB_PATH=str(directory / "idempotency.sqlite3"),
    )
    try:
        with pytest.raises(ConfigError, match="IDEMPOTENCY_DB_PATH"):
            validate_startup_config(environ)
    finally:
        directory.chmod(0o700)


def test_startup_fails_before_building_an_adapter(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ISSUE_PROVIDER", "trello")
    monkeypatch.delenv("TRELLO_TOKEN")

    with pytest.raises(ConfigError, match="TRELLO_TOKEN"), TestClient(main.app):
        pass

    assert "TRELLO_TOKEN" in caplog.text
    _assert_no_fake_value(caplog.text)
    assert not hasattr(main.app.state, "issue_provider")


def test_server_process_exits_with_a_sanitized_message(tmp_path: Path) -> None:
    """Run the real server command with a variable missing."""
    environ = {
        key: value
        for key, value in os.environ.items()
        if key not in FAKE_STARTUP_CONFIG and key != "ISSUE_PROVIDER"
    }
    environ.update(_environ(tmp_path, "trello"))
    del environ["TRELLO_TOKEN"]

    result = subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
        cwd=REPOSITORY_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "TRELLO_TOKEN" in output
    _assert_no_fake_value(output)


def test_linear_does_not_require_a_store_directory(tmp_path: Path) -> None:
    """Linear never opens the key store, so its directory is not checked."""
    path = tmp_path / "missing" / "idempotency.sqlite3"
    environ = _environ(tmp_path, "linear", IDEMPOTENCY_DB_PATH=str(path))

    assert validate_startup_config(environ).provider == "linear"


@pytest.mark.parametrize("provider", sorted(REQUIRED_VARIABLES))
def test_store_requirement_matches_the_adapter(provider: str) -> None:
    """The providers needing a store directory are those supporting idempotency."""
    adapter = dependencies._build_adapter(provider)
    try:
        assert isinstance(adapter, IdempotentIssueProvider) == (
            provider in PROVIDERS_USING_IDEMPOTENCY_STORE
        )
    finally:
        adapter.close()
