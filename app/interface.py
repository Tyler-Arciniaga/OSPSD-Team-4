from typing import Protocol, runtime_checkable

from app.models import Issue


@runtime_checkable
class IssueProvider(Protocol):
    def get_issue(self, issue_id: str) -> Issue:
        """Retrieve an issue by its ID."""
        ...

    def delete_issue(self, issue_id: str) -> None:
        """Delete an existing issue; report a missing issue as an error."""
        ...
