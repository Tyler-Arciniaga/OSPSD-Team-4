from typing import Protocol, runtime_checkable

from app.models import Issue, IssueUpdate


@runtime_checkable
class IssueProvider(Protocol):
    """Operations every issue provider must support.

    Implementations report expected failures only by raising
    `app.errors.ServiceError`; provider SDK exceptions and provider-specific
    messages must not escape. Set `provider_involved=True` only when a request
    to the provider was attempted. Use `not_found` for a missing issue,
    `access_denied` when the provider rejects the service's credentials or
    permissions, `service_not_configured` for missing provider configuration,
    and `provider_error` for any other failed or unusable provider response.

    Issue IDs are opaque to the service and are passed through unchanged.
    A provider that receives an ID it cannot recognise reports it as
    `not_found` without contacting the provider.
    """

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        """Retrieve an issue; fresh=True must bypass any saved result."""
        ...

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        """Apply the supplied changes to an issue and return the updated issue."""
        ...

    def delete_issue(self, issue_id: str) -> None:
        """Delete an existing issue; report a missing issue as an error."""
        ...

    def create_issue(self, title: str) -> Issue:
        """Create an issue with the given title and return it."""
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
