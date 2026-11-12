"""An in-memory IssueProvider that follows the shared contract.

It stands in for a real provider so the conformance suite can run on every
pull request. It must keep satisfying every test in
`contract/tests/conformance/`: when an operation's behavior or the suite
grows (for example rename or delete checks), extend this provider in the same
change.

A fault can be injected by name to show that conformance and smoke checks
catch a broken release, or to exercise the GET availability alert. Faults
exist only for disposable pipeline and observability exercises.
"""

import secrets
from collections.abc import Callable
from threading import Lock
from typing import Any

from app.cache import CachedIssueProvider
from app.dependencies import get_issue_provider, get_provider_probe
from app.health import ProviderProbe
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import Issue, IssueUpdate

# Injectable faults:
#   wrong_title   create stores and returns a different title than requested
#                 (a contract break the conformance suite must catch)
#   delete_fails  delete always fails with provider_error (leaves issues
#                 behind, which the suite reports as CleanupWarning)
#   get_fails     reads fail with provider_error, for the Level 4 alert exercise
FAULTS = frozenset({"wrong_title", "delete_fails", "get_fails"})

WRONG_TITLE_SUFFIX = " (altered)"


def _not_found() -> ServiceError:
    return ServiceError(
        ErrorCode.NOT_FOUND,
        "Issue not found",
        provider_involved=True,
        cause="unknown_issue",
    )


class InMemoryIssueProvider:
    """Issues kept in a dictionary, safe to share between request threads.

    IDs are 24 lowercase hexadecimal characters, like Trello card IDs, and are
    never reused. A deleted or unknown ID is `not_found`.
    """

    def __init__(self, fault: str | None = None) -> None:
        if fault is not None and fault not in FAULTS:
            raise ValueError(
                f"unknown fault {fault!r}; expected one of {sorted(FAULTS)}"
            )
        self._fault = fault
        self._issues: dict[str, str] = {}
        self._lock = Lock()

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        if self._fault == "get_fails":
            raise ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker is currently unavailable",
                provider_involved=True,
                cause="injected_fault",
            )
        # Nothing is cached here, so every read is already fresh.
        with self._lock:
            title = self._issues.get(issue_id)
        if title is None:
            raise _not_found()
        return Issue(id=issue_id, title=title)

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        with self._lock:
            if issue_id not in self._issues:
                raise _not_found()
            if changes.title is not None:
                self._issues[issue_id] = changes.title
            return Issue(id=issue_id, title=self._issues[issue_id])

    def delete_issue(self, issue_id: str) -> None:
        if self._fault == "delete_fails":
            raise ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker is currently unavailable",
                provider_involved=True,
                cause="injected_fault",
            )
        with self._lock:
            if self._issues.pop(issue_id, None) is None:
                raise _not_found()

    def create_issue(self, title: str) -> Issue:
        if self._fault == "wrong_title":
            title += WRONG_TITLE_SUFFIX
        with self._lock:
            issue_id = secrets.token_hex(12)
            while issue_id in self._issues:
                issue_id = secrets.token_hex(12)
            self._issues[issue_id] = title
        return Issue(id=issue_id, title=title)

    def list_issues(self, *, limit: int) -> list[Issue]:
        # Dictionaries keep insertion order (renames do not move an entry),
        # so reversing it gives newest first.
        with self._lock:
            newest_first = list(reversed(self._issues.items()))
        return [Issue(id=issue_id, title=title) for issue_id, title in newest_first][
            :limit
        ]

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        """Nothing to release."""


def dependency_overrides(
    fault: str | None = None,
) -> dict[Callable[..., Any], Callable[..., Any]]:
    """FastAPI overrides that run the real app on one in-memory provider.

    The provider is wrapped in the same cache the service uses in production,
    and readiness reports it reachable without contacting any real provider.
    """
    provider: IssueProvider = CachedIssueProvider(
        InMemoryIssueProvider(fault), ttl_seconds=30, max_entries=100
    )
    probe = ProviderProbe(lambda: True)
    return {
        get_issue_provider: lambda: provider,
        get_provider_probe: lambda: probe,
    }
