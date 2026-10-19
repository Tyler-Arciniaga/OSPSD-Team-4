"""Error responses shared by the endpoint tests.

The exact error contract is pinned literally in `tests/test_errors.py`; these
helpers keep the per-endpoint tests from repeating it.
"""

from urllib.error import URLError

ACCESS_DENIED_MESSAGE = "The issue tracker denied access to this issue"

# The JSON body of an error response.
ErrorJson = dict[str, dict[str, str]]


def error_body(code: str, message: str) -> ErrorJson:
    """Return the JSON body the service sends for an error."""
    return {"error": {"code": code, "message": message}}


NOT_FOUND = error_body("not_found", "Issue not found")
CREDENTIALS_NOT_CONFIGURED = error_body(
    "service_not_configured", "Issue tracker credentials not configured"
)
LIST_NOT_CONFIGURED = error_body(
    "service_not_configured", "Issue tracker target list not configured"
)
UNEXPECTED_RESPONSE = error_body(
    "provider_error", "The issue tracker returned an unexpected response"
)

# Trello HTTP failures for operations on an existing issue (GET, PATCH,
# DELETE), as (Trello status, service status, expected body).
ISSUE_HTTP_FAILURES = [
    (404, 404, NOT_FOUND),
    (401, 403, error_body("access_denied", ACCESS_DENIED_MESSAGE)),
    (403, 403, error_body("access_denied", ACCESS_DENIED_MESSAGE)),
    (
        429,
        502,
        error_body("provider_error", "The issue tracker is rate limiting requests"),
    ),
    (
        500,
        502,
        error_body("provider_error", "The issue tracker is currently unavailable"),
    ),
]

# Network failures, as (raised error, expected body). Every one is a 502.
NETWORK_FAILURES = [
    (
        URLError("private"),
        error_body("provider_error", "The issue tracker could not be reached"),
    ),
    (
        URLError(TimeoutError("private")),
        error_body("provider_error", "The issue tracker did not respond in time"),
    ),
    (
        TimeoutError("private"),
        error_body("provider_error", "The issue tracker did not respond in time"),
    ),
]
