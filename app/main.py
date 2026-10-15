import re
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Response

from app.dependencies import get_issue_provider
from app.interface import IssueProvider
from app.models import ISSUE_ID_PATTERN, Issue, IssueCreate, IssueUpdate

app = FastAPI(title="Issue Tracking Service")


def require_valid_issue_id(issue_id: str) -> None:
    """Reject malformed IDs before they reach the provider."""
    if not re.fullmatch(ISSUE_ID_PATTERN, issue_id):
        raise HTTPException(status_code=404, detail="Issue not found")


@app.get("/issues/{issue_id}", response_model=Issue)
def get_issue(
    issue_id: str,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Issue:
    """Retrieve an issue by its full ID using the supplied provider."""
    require_valid_issue_id(issue_id)
    return provider.get_issue(issue_id)


@app.patch("/issues/{issue_id}", response_model=Issue)
def update_issue(
    issue_id: str,
    changes: IssueUpdate,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Issue:
    """Partially update an issue by its full ID using the supplied provider."""
    require_valid_issue_id(issue_id)
    return provider.update_issue(issue_id, changes)


@app.delete("/issues/{issue_id}", status_code=204, response_class=Response)
def delete_issue(
    issue_id: str,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Response:
    """Permanently delete an issue by its full ID using the supplied provider."""
    require_valid_issue_id(issue_id)
    provider.delete_issue(issue_id)
    return Response(status_code=204)


@app.post("/issues", response_model=Issue, status_code=201)
def create_issue(
    body: IssueCreate,
    response: Response,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Issue:
    """Create an issue using the supplied provider."""
    issue = provider.create_issue(body.title)
    response.headers["Location"] = f"/issues/{issue.id}"
    return issue
