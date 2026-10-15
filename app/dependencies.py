from app.interface import IssueProvider
from app.provider import TrelloIssueProvider


def get_issue_provider() -> IssueProvider:
    """Supply the real provider used during normal operation."""
    return TrelloIssueProvider()
