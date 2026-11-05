"""Provider capabilities the service uses beyond the shared contract.

The core `IssueProvider` interface lives in the `issue_tracker_contract`
package. Idempotent creation is an optional, service-specific extension, so
its capability is defined here.
"""

from typing import Protocol, runtime_checkable

from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import Issue


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
