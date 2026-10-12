import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException

from app.models import Issue

app = FastAPI(title="Issue Tracking Service")


@app.get("/issues/{issue_id}", response_model=Issue)
def get_issue(issue_id: str) -> Issue:
    """Read a Trello card by its full ID, returning only the public issue fields."""
    if not re.fullmatch(r"[0-9a-f]{24}", issue_id):
        raise HTTPException(status_code=404, detail="Issue not found")

    key = os.environ.get("TRELLO_API_KEY")
    token = os.environ.get("TRELLO_TOKEN")
    if not key or not token:
        raise HTTPException(status_code=503, detail="Trello credentials not configured")

    request = Request(
        f"https://api.trello.com/1/cards/{issue_id}?fields=id,name",
        headers={
            "Accept": "application/json",
            "Authorization": f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"',
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            card = json.load(response)
        issue = Issue(id=card["id"], title=card["name"])
        if issue.id != issue_id:
            raise ValueError("Unexpected card ID")
        return issue
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise HTTPException(status_code=404, detail="Issue not found") from None
        raise HTTPException(status_code=502, detail="Trello request failed") from None
    except (URLError, OSError, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=502, detail="Trello request failed") from None
