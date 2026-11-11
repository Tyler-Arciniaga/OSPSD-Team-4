"""Linear adapter: implements the shared `IssueProvider` contract over GraphQL.

Linear-specific details (GraphQL documents, response shapes, error codes)
stay in this module. Callers only see `Issue` and `ServiceError`.
"""

import os
import re
from collections.abc import Mapping
from typing import Any

import httpx

from app.providers.common import (
    RATE_LIMITED_MESSAGE,
    REJECTED_MESSAGE,
    REQUEST_TIMEOUT_SECONDS,
    TIMEOUT_MESSAGE,
    UNAVAILABLE_MESSAGE,
    UNEXPECTED_RESPONSE_MESSAGE,
    UNREACHABLE_MESSAGE,
    access_denied,
    credentials_not_configured,
    malformed_issue_id,
    not_found,
    provider_error,
    target_not_configured,
)
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import Issue, IssueUpdate

LINEAR_API_URL = "https://api.linear.app/graphql"

# Linear issue IDs are lowercase UUIDs. Used to reject an ID that cannot
# name an issue before contacting Linear, and to check a returned issue.
# Linear also accepts short identifiers such as "TYL-5"; this service does
# not, so callers always use the `id` the service returned.
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

# The cheapest authenticated query: the API key's own user.
PING_QUERY = """
query Ping {
  viewer {
    id
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

UPDATE_ISSUE_MUTATION = """
mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue {
      id
      title
    }
  }
}
"""

DELETE_ISSUE_MUTATION = """
mutation DeleteIssue($id: String!) {
  issueDelete(id: $id) {
    success
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
        raise credentials_not_configured()
    return api_key


def _target_team_id() -> str:
    """Return the team new issues are created in, or fail if it is missing."""
    team_id = os.environ.get("LINEAR_TEAM_ID")
    if not team_id:
        raise target_not_configured("team")
    return team_id


def _require_issue_id(issue_id: str) -> None:
    """Reject an ID that cannot be a Linear issue ID as a missing issue.

    Runs before the credential check, so no request is attempted.
    """
    if not re.fullmatch(ISSUE_ID_PATTERN, issue_id):
        raise malformed_issue_id()


def _not_available(operation: str) -> ServiceError:
    """Refuse an operation this adapter does not implement yet, before any request."""
    return ServiceError(
        ErrorCode.SERVICE_NOT_CONFIGURED,
        f"{operation} for Linear issue provider is not available yet",
        cause="operation_not_implemented",
    )


def _unexpected_response(cause: str) -> ServiceError:
    return provider_error(UNEXPECTED_RESPONSE_MESSAGE, cause)


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
        return access_denied(cause)
    if status == 429 or RATELIMITED in codes:
        cause = f"provider_status={status}" if status == 429 else _error_cause(codes)
        return provider_error(RATE_LIMITED_MESSAGE, cause)
    if status >= 500:
        return provider_error(UNAVAILABLE_MESSAGE, f"provider_status={status}")
    cause = _error_cause(codes) if codes else f"provider_status={status}"
    return provider_error(REJECTED_MESSAGE, cause)


def _translate_transport_failure(exc: httpx.TransportError) -> ServiceError:
    """Translate a network failure. Only the exception's class name is kept."""
    if isinstance(exc, httpx.TimeoutException):
        return provider_error(TIMEOUT_MESSAGE, "timeout")
    return provider_error(UNREACHABLE_MESSAGE, type(exc).__name__)


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

    def ping(self) -> bool:
        """Ask Linear for the key's user; True only on a clean answer."""
        try:
            status, body = self._send(_credentials(), PING_QUERY, {})
            if status != 200 or _error_codes(body):
                return False
            return isinstance(body["data"]["viewer"]["id"], str)
        except (ServiceError, KeyError, TypeError):
            # Missing configuration or a network failure (ServiceError, from
            # `_credentials` and `_send`) or an unexpected body (KeyError,
            # TypeError) all count as unreachable. Nothing about the failure
            # is kept since it is provider data.
            return False

    def get_issue(self, issue_id: str, *, fresh: bool = False) -> Issue:
        """Retrieve a Linear issue.

        This adapter saves no results, so every call sends a query to Linear
        and `fresh` needs no handling. A trashed (deleted) issue is reported
        as missing, matching Trello.
        """
        _require_issue_id(issue_id)
        status, body = self._send(_credentials(), GET_ISSUE_QUERY, {"id": issue_id})
        codes = _error_codes(body)
        if status != 200 or codes:
            # Linear reports a missing issue as HTTP 200 with INPUT_ERROR.
            # `issue` takes only the ID, so an input error here means no issue.
            if status == 200 and INPUT_ERROR in codes:
                raise not_found(f"graphql_code={INPUT_ERROR}")
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
            raise not_found("issue_trashed")
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
        """Rename a Linear issue using the shared IssueProvider contract."""
        _require_issue_id(issue_id)

        try:
            self.get_issue(issue_id, fresh=True)
        except ServiceError as lookup_error:
            lookup_error.may_have_applied = False
            raise

        # The shared model currently permits only title updates.
        status, body = self._send(
            _credentials(),
            UPDATE_ISSUE_MUTATION,
            {
                "id": issue_id,
                "input": {"title": changes.title},
            },
        )

        codes = _error_codes(body)

        if status != 200 or codes:
            if status == 200 and INPUT_ERROR in codes:
                try:
                    self.get_issue(issue_id, fresh=True)
                except ServiceError as exc:
                    if exc.code == ErrorCode.NOT_FOUND:
                        raise not_found(f"graphql_code={INPUT_ERROR}") from None

                    # Could not confirm whether the issue exists.
                    error = provider_error(REJECTED_MESSAGE, "graphql_code=INPUT_ERROR")
                    error.may_have_applied = True
                    raise error from None

                # Issue exists, but Linear rejected the update.
                error = provider_error(REJECTED_MESSAGE, "graphql_code=INPUT_ERROR")
                error.may_have_applied = True
                raise error

            error = _translate_failure(status, codes)
            error.may_have_applied = status >= 500 or (
                status == 200 and not all(code in REJECTION_CODES for code in codes)
            )
            raise error

        try:
            payload = body["data"]["issueUpdate"]

            if payload["success"] is not True:
                raise ValueError("Update not confirmed")

            issue = _to_issue(payload["issue"])

            if issue.id != issue_id:
                raise ValueError("Unexpected issue ID")

        except (KeyError, TypeError, ValueError):
            error = _unexpected_response("invalid_update_response")
            error.may_have_applied = True
            raise error from None

        return issue

    def delete_issue(self, issue_id: str) -> None:
        """Delete an existing Linear issue."""
        try:
            self.get_issue(issue_id, fresh=True)
        except ServiceError as lookup_error:
            lookup_error.may_have_applied = False
            raise

        status, body = self._send(
            _credentials(),
            DELETE_ISSUE_MUTATION,
            {"id": issue_id},
        )

        codes = _error_codes(body)
        if status != 200 or codes:
            error = _translate_failure(status, codes)

            if 400 <= status < 500:
                error.may_have_applied = False
            elif status >= 500:
                error.may_have_applied = True
            else:
                error.may_have_applied = not all(
                    code in REJECTION_CODES for code in codes
                )

            raise error
        try:
            if body["data"]["issueDelete"]["success"] is not True:
                raise ValueError("Deletion not reported as successful")
        except (KeyError, TypeError, ValueError) as exc:
            raise _unexpected_response(type(exc).__name__) from None

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
