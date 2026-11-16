"""Live check against the Trello test list. Usage: python -I live_check.py <path-to-.env>

Loads the TRELLO_* variables into this process only and never prints them.
Creates one card, reads it fresh, lists, deletes it, and confirms it is gone.
"""

import os
import sys
import uuid

WANTED = {"TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"}
with open(sys.argv[1], encoding="utf-8") as env_file:
    for line in env_file:
        key, sep, value = line.strip().partition("=")
        if sep and key in WANTED:
            os.environ[key] = value.strip().strip('"').strip("'")
assert WANTED <= os.environ.keys(), "missing Trello variables"

from issue_tracker_contract.errors import ErrorCode, ServiceError
from team_4_issue_tracker_trello import TrelloIssueProvider

provider = TrelloIssueProvider()
title = f"team-4 package verification {uuid.uuid4().hex[:8]}"
print("ping():", provider.ping())

created = provider.create_issue(title)
print(f"create_issue -> id={created.id} title={created.title!r}")
try:
    fetched = provider.get_issue(created.id, fresh=True)
    assert fetched == created, fetched
    print(f"get_issue(fresh=True) -> id={fetched.id} title={fetched.title!r}")

    listed = provider.list_issues(limit=5)
    print(
        f"list_issues(limit=5) -> {len(listed)} issues, newest first;"
        f" created card first: {listed[0].id == created.id}"
    )
    assert listed[0].id == created.id
finally:
    provider.delete_issue(created.id)
    print(f"delete_issue({created.id}) -> done")

try:
    provider.get_issue(created.id, fresh=True)
except ServiceError as exc:
    assert exc.code == ErrorCode.NOT_FOUND, exc.code
    print(f"get_issue after delete -> ServiceError code={exc.code} status={exc.status}")
else:
    raise SystemExit("card still exists after delete")
print("LIVE CHECKS PASSED")
