"""Behavior every provider adapter shares, independent of the provider's API.

The error helpers fix the caller-facing messages, so a consumer sees the same
`ServiceError` for the same outcome whichever provider is configured. How a
provider's own responses map onto these outcomes stays in each adapter.
"""

from issue_tracker_contract.errors import ErrorCode, ServiceError

# Budget for one network operation with any provider.
REQUEST_TIMEOUT_SECONDS = 10

UNEXPECTED_RESPONSE_MESSAGE = "The issue tracker returned an unexpected response"
TIMEOUT_MESSAGE = "The issue tracker did not respond in time"
UNREACHABLE_MESSAGE = "The issue tracker could not be reached"
RATE_LIMITED_MESSAGE = "The issue tracker is rate limiting requests"
UNAVAILABLE_MESSAGE = "The issue tracker is currently unavailable"
REJECTED_MESSAGE = "The issue tracker rejected the request"


def not_found(cause: str, *, provider_involved: bool = True) -> ServiceError:
    return ServiceError(
        ErrorCode.NOT_FOUND,
        "Issue not found",
        provider_involved=provider_involved,
        cause=cause,
    )


def malformed_issue_id() -> ServiceError:
    """An ID this provider cannot recognise, rejected before any request."""
    return not_found("malformed_issue_id", provider_involved=False)


def access_denied(cause: str) -> ServiceError:
    return ServiceError(
        ErrorCode.ACCESS_DENIED,
        "The issue tracker denied access to this issue",
        provider_involved=True,
        cause=cause,
    )


def credentials_not_configured() -> ServiceError:
    return ServiceError(
        ErrorCode.SERVICE_NOT_CONFIGURED,
        "Issue tracker credentials not configured",
        cause="missing_credentials",
    )


def target_not_configured(target: str) -> ServiceError:
    """Missing creation target, named in the provider's terms (list, team)."""
    return ServiceError(
        ErrorCode.SERVICE_NOT_CONFIGURED,
        f"Issue tracker target {target} not configured",
        cause=f"missing_target_{target}",
    )


def provider_error(message: str, cause: str) -> ServiceError:
    """Report a failed or unusable provider request without provider details."""
    return ServiceError(
        ErrorCode.PROVIDER_ERROR, message, provider_involved=True, cause=cause
    )
