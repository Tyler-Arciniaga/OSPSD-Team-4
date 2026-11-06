"""Linear adapter: implements the shared `IssueProvider` contract over GraphQL.

Linear-specific details (GraphQL documents, response shapes, error codes)
stay in this module. Callers only see `Issue` and `ServiceError`.
"""

import os
import re
from collections.abc import Mapping
from typing import Any

import httpx

from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import Issue, IssueUpdate

LINEAR_API_URL = "https://api.linear.app/graphql"

# Same per-operation budget as the Trello adapter.
REQUEST_TIMEOUT_SECONDS = 10

# Linear issue IDs are lowercase UUIDs. Used to reject an ID that cannot
# name an issue before contacting Linear, and to check a returned issue.
ISSUE_ID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# Only `id` and `title` are requested, keeping responses small. `trashed`
# lets a deleted (soft-deleted) issue be reported as missing.
GET_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id
    title
    trashed
  }
}
"""

CREATE_ISSUE_MUTATION = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id
      title
    }
  }
}
"""

# Linear `extensions.code` values this adapter recognises. Any other code is
# logged only as "graphql_error", so provider text never reaches a log line.
INPUT_ERROR = "INPUT_ERROR"
AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
RATELIMITED = "RATELIMITED"
KNOWN_ERROR_CODES = frozenset({INPUT_ERROR, AUTHENTICATION_ERROR, RATELIMITED})

# Codes with which Linear rejects a request outright: a create that fails
# with only these codes definitively created nothing.
REJECTION_CODES = KNOWN_ERROR_CODES


def _credentials() -> str:
    """Return the Linear API key, or fail if it is missing."""
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        raise ServiceError(
            ErrorCode.SERVICE_NOT_CONFIGURED,
            "Issue tracker credentials not configured",
            cause="missing_credentials",
        )
    return api_key


def _target_team_id() -> str:
    """Return the team new issues are created in, or fail if it is missing."""
    team_id = os.environ.get("LINEAR_TEAM_ID")
    if not team_id:
        raise ServiceError(
            ErrorCode.SERVICE_NOT_CONFIGURED,
            "Issue tracker target team not configured",
            cause="missing_target_team",
        )
    return team_id


def _require_issue_id(issue_id: str) -> None:
    """Reject an ID that cannot be a Linear issue ID as a missing issue.

    Runs before the credential check, so no request is attempted.
    """
    if not re.fullmatch(ISSUE_ID_PATTERN, issue_id):
        raise ServiceError(
            ErrorCode.NOT_FOUND, "Issue not found", cause="malformed_issue_id"
        )


def _not_found(cause: str) -> ServiceError:
    return ServiceError(
        ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True, cause=cause
    )


def _not_available(operation: str) -> ServiceError:
    """Refuse an operation this adapter does not implement yet, before any request."""
    return ServiceError(
        ErrorCode.SERVICE_NOT_CONFIGURED,
        f"{operation} for Linear issue provider is not available yet",
        cause="operation_not_implemented",
    )


def _provider_error(message: str, cause: str) -> ServiceError:
    """Report a failed or unusable Linear request without Linear details."""
    return ServiceError(
        ErrorCode.PROVIDER_ERROR, message, provider_involved=True, cause=cause
    )


def _unexpected_response(cause: str) -> ServiceError:
    return _provider_error("The issue tracker returned an unexpected response", cause)


def _error_codes(body: Any) -> list[str | None]:
    """Return each GraphQL error's `extensions.code`, or None where absent."""
    if not isinstance(body, dict):
        return []
    errors = body.get("errors")
    if not isinstance(errors, list):
        return []
    codes: list[str | None] = []
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        code = extensions.get("code") if isinstance(extensions, dict) else None
        codes.append(code if isinstance(code, str) else None)
    return codes


def _error_cause(codes: list[str | None]) -> str:
    """A short log label for GraphQL errors: a known code or a generic label."""
    known = next((code for code in codes if code in KNOWN_ERROR_CODES), None)
    return f"graphql_code={known}" if known else "graphql_error"


def _translate_failure(status: int, codes: list[str | None]) -> ServiceError:
    """Translate a Linear failure (HTTP status and GraphQL error codes).

    Only the status and recognised error codes are kept: messages and the
    response body are provider data.
    """
    if status == 401 or AUTHENTICATION_ERROR in codes:
        cause = f"provider_status={status}" if status == 401 else _error_cause(codes)
        return ServiceError(
            ErrorCode.ACCESS_DENIED,
            "The issue tracker denied access to this issue",
            provider_involved=True,
            cause=cause,
        )
    if status == 429 or RATELIMITED in codes:
        cause = f"provider_status={status}" if status == 429 else _error_cause(codes)
        return _provider_error("The issue tracker is rate limiting requests", cause)
    if status >= 500:
        return _provider_error(
            "The issue tracker is currently unavailable", f"provider_status={status}"
        )
    cause = _error_cause(codes) if codes else f"provider_status={status}"
    return _provider_error("The issue tracker rejected the request", cause)


def _translate_transport_failure(exc: httpx.TransportError) -> ServiceError:
    """Translate a network failure. Only the exception's class name is kept."""
    if isinstance(exc, httpx.TimeoutException):
        return _provider_error("The issue tracker did not respond in time", "timeout")
    return _provider_error("The issue tracker could not be reached", type(exc).__name__)


def _never_sent(exc: httpx.TransportError) -> bool:
    """Whether a network failure happened before the request reached Linear.

    A failed connection (including DNS failure, refusal and connect timeout)
    or waiting for a pooled connection proves nothing was sent. A read or
    write timeout or a dropped connection may follow an issue Linear created.
    """
    return isinstance(
        exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
    )


def _to_issue(node: Any) -> Issue:
    """Convert a Linear issue node into an Issue, checking its shape."""
    if not isinstance(node, dict):
        raise TypeError("Unexpected issue")
    issue_id, title = node.get("id"), node.get("title")
    if not isinstance(issue_id, str) or not re.fullmatch(ISSUE_ID_PATTERN, issue_id):
        raise ValueError("Unexpected issue ID")
    if not isinstance(title, str):
        raise TypeError("Unexpected issue title")
    return Issue(id=issue_id, title=title)


class LinearIssueProvider:
    """`IssueProvider` backed by Linear's GraphQL API.

    Holds one long-lived HTTP client for connection reuse; whoever creates the
    provider must call `close()` at shutdown. Credentials and the target team
    are read from the environment on each call, never stored.
    `transport` lets tests supply simulated responses.
    """

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS, transport=transport
        )

    def close(self) -> None:
        """Close the HTTP client. Later calls fail as an unreachable provider."""
        self._client.close()

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        """Retrieve a Linear issue; reads are always fresh with this provider.

        A trashed (deleted) issue is reported as missing, matching Trello.
        """
        _require_issue_id(issue_id)
        status, body = self._send(_credentials(), GET_ISSUE_QUERY, {"id": issue_id})
        codes = _error_codes(body)
        if status == 200 and codes:
            if INPUT_ERROR in codes:
                raise _not_found(f"graphql_code={INPUT_ERROR}")
            raise _translate_failure(status, codes)
        if status != 200:
            raise _translate_failure(status, codes)
        try:
            node = body["data"]["issue"]
            issue = _to_issue(node)
            if issue.id != issue_id:
                raise ValueError("Unexpected issue ID")
            trashed = node.get("trashed")
            if trashed not in (None, True, False):
                raise TypeError("Unexpected trashed flag")
        except (KeyError, TypeError, ValueError) as exc:
            raise _unexpected_response(type(exc).__name__) from None
        if trashed:
            raise _not_found("issue_trashed")
        return issue

    def create_issue(self, title: str) -> Issue:
        """Create a Linear issue in the configured team (and optional state)."""
        api_key = _credentials()
        issue_input = {"teamId": _target_team_id(), "title": title}
        state_id = os.environ.get("LINEAR_STATE_ID")
        if state_id:
            issue_input["stateId"] = state_id

        status, body = self._send(
            api_key, CREATE_ISSUE_MUTATION, {"input": issue_input}
        )
        codes = _error_codes(body)
        if codes or status != 200:
            error = _translate_failure(status, codes)
            if 400 <= status < 500:
                # Linear rejected the request before creating anything.
                error.may_have_applied = False
            elif status >= 500:
                error.may_have_applied = True
            else:
                # HTTP 200 with errors: certain only if every error is a
                # rejection Linear raises before writing.
                error.may_have_applied = not all(
                    code in REJECTION_CODES for code in codes
                )
            raise error
        try:
            payload = body["data"]["issueCreate"]
            if payload["success"] is not True:
                raise ValueError("Creation not reported as successful")
            issue = _to_issue(payload["issue"])
        except (KeyError, TypeError, ValueError) as exc:
            # A success status with an unusable body: the issue may exist.
            error = _unexpected_response(type(exc).__name__)
            error.may_have_applied = True
            raise error from None
        return issue

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        """Rename is not implemented for Linear yet (DESIGN.md 6.4, change 2)."""
        raise _not_available("Renaming issues")

    def delete_issue(self, issue_id: str) -> None:
        """Delete is not implemented for Linear yet (DESIGN.md 6.4, change 2)."""
        raise _not_available("Deleting issues")

    def _send(
        self, api_key: str, query: str, variables: Mapping[str, Any]
    ) -> tuple[int, Any]:
        """Send one GraphQL request and return its status and decoded body.

        A body that is not JSON is returned as None for a failure status (the
        status alone decides the error) and reported as an unexpected
        response, which may follow an applied write, for a success status.
        Network failures become ServiceError, with `may_have_applied` False
        only when nothing was sent.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": api_key,
        }
        try:
            response = self._client.post(
                LINEAR_API_URL,
                json={"query": query, "variables": dict(variables)},
                headers=headers,
            )
        except httpx.TransportError as exc:
            error = _translate_transport_failure(exc)
            error.may_have_applied = not _never_sent(exc)
            raise error from None
        try:
            body = response.json()
        except ValueError:
            if response.is_success:
                raise _unexpected_response("JSONDecodeError") from None
            body = None
        return response.status_code, body
