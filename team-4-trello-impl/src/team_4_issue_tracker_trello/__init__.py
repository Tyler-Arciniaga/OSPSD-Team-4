"""Team 4's Trello implementation of the shared `IssueProvider` contract."""

from team_4_issue_tracker_trello._tracing import set_span_factory
from team_4_issue_tracker_trello.provider import TrelloIssueProvider

__all__ = ["TrelloIssueProvider", "set_span_factory"]
