"""Provider capabilities the service uses beyond the shared contract.

The core `IssueProvider` interface lives in the `issue_tracker_contract`
package. Closing an adapter at shutdown and idempotent creation are
service-specific needs, so their protocols are defined here.
"""

from typing import Protocol, runtime_checkable

from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import Issue


@runtime_checkable
class ClosableIssueProvider(IssueProvider, Protocol):
    """An adapter the service can probe while running and release at shutdown.

    Every adapter that startup builds implements this, so readiness and
    shutdown are the same for all of them. `close()` releases whatever the
    adapter holds (for example a long-lived HTTP client); an adapter holding
    nothing does nothing.
    """

    def ping(self) -> bool:
        """Report whether the provider answers a lightweight authenticated read.

        Used only to inform the readiness report. Never raises; any failure,
        including missing configuration, returns False.
        """
        ...

    def close(self) -> None:
        """Release the adapter's resources. Called once, at shutdown."""
        ...


@runtime_checkable
class IdempotentIssueProvider(IssueProvider, Protocol):
    """A provider that can take part in idempotent creation.

    Idempotent creation needs two things the basic interface lacks: a way to
    mark a created issue with an opaque *creation token*, and a way to find
    the issue carrying a token later. The token lets the service recover when
    a creation's outcome is unknown (the response was lost, or the service
    stopped mid-request) without creating the issue a second time.

    The token is a 64-character lowercase hexadecimal string. It is not the
    caller's idempotency key and reveals nothing about it.
    """

    def create_tagged_issue(self, title: str, creation_token: str) -> Issue:
        """Create one issue marked with `creation_token` and return it.

        On failure, raise `ServiceError` with `may_have_applied=False` only
        when the provider certainly did not create the issue.
        """
        ...

    def find_tagged_issue(self, creation_token: str) -> Issue | None:
        """Return the issue marked with `creation_token`, or None if none exists.

        If several issues carry the token, return the oldest. This is a read:
        it never creates or changes an issue.
        """
        ...
