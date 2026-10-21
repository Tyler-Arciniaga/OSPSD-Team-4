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

    def get_issue(self, issue_id: str) -> Issue:
        """Retrieve an issue by its ID."""
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
