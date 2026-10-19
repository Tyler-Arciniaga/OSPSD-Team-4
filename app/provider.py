import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.errors import ErrorCode, ServiceError
from app.models import ISSUE_ID_PATTERN, Issue, IssueUpdate


def _credentials() -> tuple[str, str]:
    """Return the Trello API key and token, or fail if either is missing."""
    key = os.environ.get("TRELLO_API_KEY")
    token = os.environ.get("TRELLO_TOKEN")
    if not key or not token:
        raise ServiceError(
            ErrorCode.SERVICE_NOT_CONFIGURED,
            "Issue tracker credentials not configured",
            cause="missing_credentials",
        )
    return key, token


def _provider_error(message: str, cause: str) -> ServiceError:
    """Report a failed or unusable Trello request without Trello details."""
    return ServiceError(
        ErrorCode.PROVIDER_ERROR, message, provider_involved=True, cause=cause
    )


def _translate_request_failure(exc: Exception) -> ServiceError:
    """Translate a network failure or unusable Trello response.

    Only the exception's class name is kept: its message may contain the
    request URL, which carries the key and token.
    """
    # A timeout may be raised directly or wrapped in a URLError.
    if isinstance(exc, TimeoutError) or (
        isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
    ):
        return _provider_error("The issue tracker did not respond in time", "timeout")
    if isinstance(exc, OSError):
        return _provider_error(
            "The issue tracker could not be reached", type(exc).__name__
        )
    return _provider_error(
        "The issue tracker returned an unexpected response", type(exc).__name__
    )


def _translate_http_error(
    exc: HTTPError, *, missing_means_not_found: bool
) -> ServiceError:
    """Translate a Trello HTTP rejection into a service error.

    Only the status code is kept: the URL carries the key and token, and the
    response body is provider data.
    """
    exc.close()
    cause = f"provider_status={exc.code}"
    if exc.code == 404 and missing_means_not_found:
        return ServiceError(
            ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True, cause=cause
        )
    if exc.code in (401, 403):
        return ServiceError(
            ErrorCode.ACCESS_DENIED,
            "The issue tracker denied access to this issue",
            provider_involved=True,
            cause=cause,
        )
    if exc.code == 429:
        return _provider_error("The issue tracker is rate limiting requests", cause)
    if exc.code >= 500:
        return _provider_error("The issue tracker is currently unavailable", cause)
    return _provider_error("The issue tracker rejected the request", cause)


class TrelloIssueProvider:
    def get_issue(self, issue_id: str) -> Issue:
        """Retrieve a Trello card and convert it into an Issue."""
        issue = self._request_card("GET", issue_id, {"fields": "id,name"})
        assert issue is not None
        return issue

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        """Update a Trello card and convert the result into an Issue."""
        params: dict[str, str] = {}
        if changes.title is not None:
            params["name"] = changes.title
        issue = self._request_card("PUT", issue_id, params)
        assert issue is not None
        return issue

    def delete_issue(self, issue_id: str) -> None:
        """Permanently delete a Trello card."""
        self._request_card("DELETE", issue_id)

    def create_issue(self, title: str) -> Issue:
        """Create a Trello card in the configured list and convert it into an Issue."""

        key, token = _credentials()
        list_id = os.environ.get("TRELLO_LIST_ID")
        if not list_id:
            raise ServiceError(
                ErrorCode.SERVICE_NOT_CONFIGURED,
                "Issue tracker target list not configured",
                cause="missing_target_list",
            )

        params = urlencode(
            {
                "idList": list_id,
                "name": title,
                "key": key,
                "token": token,
            }
        )

        request = Request(
            f"https://api.trello.com/1/cards?{params}",
            headers={"Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                card = json.load(response)
            # re.fullmatch raises TypeError if the ID is not a string.
            if not re.fullmatch(ISSUE_ID_PATTERN, card["id"]):
                raise ValueError("Unexpected card ID")
            if not isinstance(card["name"], str):
                raise TypeError("Unexpected card name")
            return Issue(id=card["id"], title=card["name"])
        except HTTPError as exc:
            # A Trello 404 here means the configured list is wrong, not a
            # missing issue, so it is reported as a provider failure.
            raise _translate_http_error(exc, missing_means_not_found=False) from None
        except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
            raise _translate_request_failure(exc) from None

    def _request_card(
        self, method: str, issue_id: str, params: dict[str, str] | None = None
    ) -> Issue | None:
        """Send one card request with shared authentication and error mapping.

        Returns the translated card, or None for DELETE (which has no card body).
        """
        key, token = _credentials()

        query = urlencode({**(params or {}), "key": key, "token": token})
        request = Request(
            f"https://api.trello.com/1/cards/{issue_id}?{query}",
            headers={"Accept": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                if method == "DELETE":
                    return None
                card: Any = json.load(response)
            issue = Issue(id=card["id"], title=card["name"])
            if issue.id != issue_id:
                raise ValueError("Unexpected card ID")
            return issue
        except HTTPError as exc:
            raise _translate_http_error(exc, missing_means_not_found=True) from None
        except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
            raise _translate_request_failure(exc) from None
