"""Startup builds the one adapter named by ISSUE_PROVIDER and closes it at shutdown."""

import pytest
from fastapi.testclient import TestClient

from app import dependencies, main
from app.cache import CachedIssueProvider
from app.interface import ClosableIssueProvider
from app.providers.linear_provider import LinearIssueProvider
from app.providers.trello_provider import TrelloIssueProvider


@pytest.mark.parametrize(
    ("value", "adapter_type"),
    [
        (None, TrelloIssueProvider),
        ("", TrelloIssueProvider),
        ("trello", TrelloIssueProvider),
        ("linear", LinearIssueProvider),
    ],
)
def test_startup_builds_the_configured_adapter_behind_the_cache(
    monkeypatch: pytest.MonkeyPatch, value: str | None, adapter_type: type
) -> None:
    if value is None:
        monkeypatch.delenv("ISSUE_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("ISSUE_PROVIDER", value)

    with TestClient(main.app):
        provider = main.app.state.issue_provider
        assert isinstance(provider, CachedIssueProvider)
        assert isinstance(provider._provider, adapter_type)


def test_unknown_provider_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUE_PROVIDER", "jira")

    with pytest.raises(ValueError, match="ISSUE_PROVIDER"), TestClient(main.app):
        pass


@pytest.mark.parametrize("value", ["trello", "linear"])
def test_shutdown_closes_the_adapter_once(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ISSUE_PROVIDER", value)
    closed: list[object] = []
    build = dependencies._build_adapter

    def build_recording_close() -> ClosableIssueProvider:
        adapter = build()
        close = adapter.close

        def recording_close() -> None:
            closed.append(adapter)
            close()

        monkeypatch.setattr(adapter, "close", recording_close)
        return adapter

    monkeypatch.setattr(dependencies, "_build_adapter", build_recording_close)

    with TestClient(main.app):
        assert closed == []
    assert len(closed) == 1
