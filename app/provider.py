import json
import os
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.errors import ErrorCode, ServiceError
from app.models import Issue, IssueUpdate

# The format of a full Trello card ID. Used to reject IDs that cannot name a
# card before contacting Trello, and to check the ID of a created card.
CARD_ID_PATTERN = r"[0-9a-f]{24}"


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


def _require_card_id(issue_id: str) -> None:
    """Reject an ID that cannot be a full Trello card ID as a missing issue.

    Runs before the credential check, so no request is attempted.
    """
    if not re.fullmatch(CARD_ID_PATTERN, issue_id):
        raise ServiceError(
            ErrorCode.NOT_FOUND, "Issue not found", cause="malformed_issue_id"
        )


def _target_list_id() -> str:
    """Return the list new cards are created in, or fail if it is missing."""
    list_id = os.environ.get("TRELLO_LIST_ID")
    if not list_id:
        raise ServiceError(
            ErrorCode.SERVICE_NOT_CONFIGURED,
            "Issue tracker target list not configured",
            cause="missing_target_list",
        )
    return list_id


# Written as a card's description to mark the card a creation produced.
TOKEN_MARKER_PREFIX = "issue-service-creation-token:"
CREATION_TOKEN_PATTERN = r"[0-9a-f]{64}"


def _require_creation_token(creation_token: str) -> None:
    """Reject a token the service could not have produced (a programming error)."""
    if not re.fullmatch(CREATION_TOKEN_PATTERN, creation_token):
        raise ValueError("Creation token must be 64 lowercase hexadecimal characters")


def _token_marker(creation_token: str) -> str:
    return f"{TOKEN_MARKER_PREFIX}{creation_token}"


def _never_sent(exc: Exception) -> bool:
    """Whether a failure happened before the request could reach Trello.

    Only a failed DNS lookup or a refused connection proves this. A timeout,
    reset, or unreadable response may follow a card Trello already created.
    """
    reason = exc.reason if isinstance(exc, URLError) else exc
    return isinstance(reason, (socket.gaierror, ConnectionRefusedError))


def _trello_headers() -> dict[str, str]:
    """Return request headers carrying the Trello key and token.

    Credentials go in the Authorization header rather than the query string,
    so they never appear in request URLs or in HTTPError.url.
    """
    key, token = _credentials()
    return {
        "Accept": "application/json",
        "Authorization": f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"',
    }


def _provider_error(message: str, cause: str) -> ServiceError:
    """Report a failed or unusable Trello request without Trello details."""
    return ServiceError(
        ErrorCode.PROVIDER_ERROR, message, provider_involved=True, cause=cause
    )


def _translate_request_failure(exc: Exception) -> ServiceError:
    """Translate a network failure or unusable Trello response.

    Only the exception's class name is kept: its message may contain the
    request URL or other provider details.
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

    Only the status code is kept: the response body is provider data.
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


def _created_card_to_issue(card: Any) -> Issue:
    """Convert the body of a newly created (or looked-up) card into an Issue."""
    if not re.fullmatch(CARD_ID_PATTERN, card["id"]):
        raise ValueError("Unexpected card ID")
    if not isinstance(card["name"], str):
        raise TypeError("Unexpected card name")
    return Issue(id=card["id"], title=card["name"])


def _card_to_issue(card: Any, issue_id: str) -> Issue:
    """Convert a Trello card body into an Issue for the requested ID.

    A body that is not a card, or is a different card, is reported as an
    unusable provider response.
    """
    try:
        issue = Issue(id=card["id"], title=card["name"])
        if issue.id != issue_id:
            raise ValueError("Unexpected card ID")
    except (ValueError, KeyError, TypeError) as exc:
        raise _translate_request_failure(exc) from None
    return issue


class TrelloIssueProvider:
    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        """Retrieve a Trello card; reads are always fresh with this provider."""
        card = self._send_card_request("GET", issue_id, {"fields": "id,name"})
        return _card_to_issue(card, issue_id)

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        """Update a Trello card and convert the result into an Issue."""
        params: dict[str, str] = {}
        if changes.title is not None:
            params["name"] = changes.title
        card = self._send_card_request("PUT", issue_id, params)
        return _card_to_issue(card, issue_id)

    def delete_issue(self, issue_id: str) -> None:
        """Permanently delete a Trello card."""
        self._send_card_request("DELETE", issue_id)

    def create_issue(self, title: str) -> Issue:
        """Create a Trello card in the configured list and convert it into an Issue."""
        return self._create_card(title)

    def create_tagged_issue(self, title: str, creation_token: str) -> Issue:
        """Create a card whose description carries the creation token.

        The marker is the card's whole description, so `find_tagged_issue()`
        can find the card again after a lost response or a restart.
        """
        _require_creation_token(creation_token)
        return self._create_card(title, desc=_token_marker(creation_token))

    def find_tagged_issue(self, creation_token: str) -> Issue | None:
        """Find the oldest card in the target list marked with the token.

        Archived cards are included: an archived card was still created.
        """
        _require_creation_token(creation_token)
        headers = _trello_headers()
        list_id = _target_list_id()
        params = urlencode({"fields": "id,name,desc", "filter": "all"})
        request = Request(
            f"https://api.trello.com/1/lists/{list_id}/cards?{params}",
            headers=headers,
            method="GET",
        )
        marker = _token_marker(creation_token)
        try:
            with urlopen(request, timeout=10) as response:
                cards = json.load(response)
            if not isinstance(cards, list):
                raise TypeError("Unexpected card list")
            matches = [
                _created_card_to_issue(card)
                for card in cards
                if isinstance(card, dict)
                and isinstance(card.get("desc"), str)
                and marker in card["desc"].splitlines()
            ]
        except HTTPError as exc:
            raise _translate_http_error(exc, missing_means_not_found=False) from None
        except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
            raise _translate_request_failure(exc) from None
        if not matches:
            return None
        # Trello card IDs begin with their creation time, so the smallest ID
        # is the oldest card.
        return min(matches, key=lambda issue: issue.id)

    def _create_card(self, title: str, desc: str | None = None) -> Issue:
        """Send one card creation request and report whether a failure is certain."""
        headers = _trello_headers()
        list_id = _target_list_id()

        fields = {"idList": list_id, "name": title}
        if desc is not None:
            fields["desc"] = desc
        params = urlencode(fields)

        request = Request(
            f"https://api.trello.com/1/cards?{params}",
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                card = json.load(response)
            return _created_card_to_issue(card)
        except HTTPError as exc:
            # A Trello 404 here means the configured list is wrong, not a
            # missing issue, so it is reported as a provider failure.
            error = _translate_http_error(exc, missing_means_not_found=False)
            # Trello rejects a 4xx request without creating a card; after a
            # 5xx it is not known whether the card was stored.
            error.may_have_applied = exc.code >= 500
            raise error from None
        except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
            error = _translate_request_failure(exc)
            error.may_have_applied = not _never_sent(exc)
            raise error from None

    def _send_card_request(
        self, method: str, issue_id: str, params: dict[str, str] | None = None
    ) -> Any:
        """Send one card request with shared authentication and error mapping.

        Returns the decoded JSON body, or None for DELETE (which has no card
        body). Send any returned cards to `_card_to_issue()` to turn a card body into an Issue.
        """
        _require_card_id(issue_id)
        headers = _trello_headers()

        url = f"https://api.trello.com/1/cards/{issue_id}"
        if params:
            url += f"?{urlencode(params)}"
        request = Request(url, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                if method == "DELETE":
                    return None
                return json.load(response)
        except HTTPError as exc:
            raise _translate_http_error(exc, missing_means_not_found=True) from None
        except (URLError, OSError, ValueError) as exc:
            raise _translate_request_failure(exc) from None
