"""Offline checks for an installed team-4-issue-tracker-trello (no network to Trello)."""

import importlib.metadata as md
import importlib.util
import os

for name in ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"):
    os.environ.pop(name, None)

import team_4_issue_tracker_trello
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from team_4_issue_tracker_trello import TrelloIssueProvider

print("team-4-issue-tracker-trello", md.version("team-4-issue-tracker-trello"))
print("issue-tracker-contract", md.version("issue-tracker-contract"))
print("import team_4_issue_tracker_trello: ok")
print("import issue_tracker_contract: ok")
for mod in ("app", "fastapi", "opentelemetry"):
    print(f"{mod} importable:", importlib.util.find_spec(mod) is not None)

provider = TrelloIssueProvider()
print("isinstance(provider, IssueProvider):", isinstance(provider, IssueProvider))
try:
    provider.list_issues(limit=1)
except ServiceError as exc:
    assert exc.code == ErrorCode.SERVICE_NOT_CONFIGURED, exc.code
    print(
        f"env unset -> ServiceError code={exc.code} cause={exc.cause} status={exc.status}"
    )
else:
    raise SystemExit("expected service_not_configured")
assert team_4_issue_tracker_trello.__all__ == [
    "TrelloIssueProvider",
    "set_span_factory",
]
print("OFFLINE CHECKS PASSED")
