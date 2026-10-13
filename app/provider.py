import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.models import Issue


class TrelloIssueProvider:
    def get_issue(self, issue_id: str) -> Issue:
        """Retrieve a Trello card and convert it into an Issue."""
        issue = self._request_card("GET", issue_id)
        assert issue is not None
        return issue

    def delete_issue(self, issue_id: str) -> None:
        """Permanently delete a Trello card."""
        self._request_card("DELETE", issue_id)

    def _request_card(self, method: str, issue_id: str) -> Issue | None:
        """Send one card request with shared authentication and error mapping."""
        key = os.environ.get("TRELLO_API_KEY")
        token = os.environ.get("TRELLO_TOKEN")
        if not key or not token:
            raise HTTPException(
                status_code=503, detail="Trello credentials not configured"
            )

        params = {"key": key, "token": token}
        if method == "GET":
            params["fields"] = "id,name"
        query = urlencode(params)

        request = Request(
            f"https://api.trello.com/1/cards/{issue_id}?{query}",
            headers={"Accept": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                if method == "DELETE":
                    return None
                card = json.load(response)
            issue = Issue(id=card["id"], title=card["name"])
            if issue.id != issue_id:
                raise ValueError("Unexpected card ID")
            return issue
        except HTTPError as exc:
            exc.close()
            if exc.code == 404:
                raise HTTPException(status_code=404, detail="Issue not found") from None
            raise HTTPException(
                status_code=502, detail="Trello request failed"
            ) from None
        except (URLError, OSError, ValueError, KeyError, TypeError):
            raise HTTPException(
                status_code=502, detail="Trello request failed"
            ) from None
