import re

from fastapi import FastAPI, HTTPException

from app.interface import IssueProvider
from app.models import Issue
from app.provider import TrelloIssueProvider

app = FastAPI(title="Issue Tracking Service")


@app.get("/issues/{issue_id}", response_model=Issue)
def get_issue(issue_id: str) -> Issue:
    """Read a Trello card by its full ID, returning only the public issue fields."""
    if not re.fullmatch(r"[0-9a-f]{24}", issue_id):
        raise HTTPException(status_code=404, detail="Issue not found")

    provider: IssueProvider = TrelloIssueProvider()
    return provider.get_issue(issue_id)
