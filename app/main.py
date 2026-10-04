from fastapi import FastAPI, HTTPException
from app.models import Issue

app = FastAPI(title="Issue Tracking Service")

_FIXED_ISSUES = {
    "64f1c0a2b3d4e5f607182930": Issue(
        id="64f1c0a2b3d4e5f607182930",
        title="Example issue",
    ),
}


@app.get("/issues/{issue_id}", response_model=Issue)
def get_issue(issue_id: str) -> Issue:
    """Fetches one issue using its ID.

    Read-only. Responds 404 if the id is unknown or invalid
    """

    issue = _FIXED_ISSUES.get(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Requested issue not found.")
    return issue
