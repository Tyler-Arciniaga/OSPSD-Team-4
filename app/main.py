import re
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from app.dependencies import get_issue_provider
from app.interface import IssueProvider
from app.models import Issue

app = FastAPI(title="Issue Tracking Service")


@app.get("/issues/{issue_id}", response_model=Issue)
def get_issue(
    issue_id: str,
    provider: Annotated[IssueProvider, Depends(get_issue_provider)],
) -> Issue:
    """Retrieve an issue by its full ID using the supplied provider."""
    if not re.fullmatch(r"[0-9a-f]{24}", issue_id):
        raise HTTPException(status_code=404, detail="Issue not found")

    return provider.get_issue(issue_id)
