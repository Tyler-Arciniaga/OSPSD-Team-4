"""Calendar adapter for the `create_events` step.

The error mapping is documented in product/docs/ERRORS.md
("create_events (calendar)"). Keep that table in sync with this module.
"""

import logging
from typing import Final

from calendar_contract import CalendarError, CalendarService, CreateEvent
from calendar_contract import ErrorCode as CalendarCode
from issue_tracker_contract.errors import ErrorCode
from pydantic import ValidationError

from ospsd_team4_product.errors import Step, StepError
from ospsd_team4_product.planner import Block

logger = logging.getLogger(__name__)

STEP: Final[Step] = "create_events"

TITLE_PREFIX = "Focus: "
# The contract sets no length limit; this keeps calendar entries readable.
MAX_TITLE_LENGTH = 120
ELLIPSIS = "..."

_MESSAGES = {
    CalendarCode.INVALID_INPUT: "The calendar rejected the event",
    CalendarCode.NOT_FOUND: "The calendar event was not found",
    CalendarCode.ACCESS_DENIED: "The calendar denied access",
    CalendarCode.PROVIDER_ERROR: "The calendar returned an unusable response",
    CalendarCode.PROVIDER_UNAVAILABLE: "The calendar is currently unavailable",
}
_UNCERTAIN_CREATE = (
    "The calendar did not confirm whether the event was created. "
    "Check the calendar before retrying"
)
_NOT_AUTHORIZED = "The calendar's authorization is missing or expired"


def event_title(issue_title: str) -> str:
    """`Focus: <title>` on one line, at most MAX_TITLE_LENGTH characters."""
    title = TITLE_PREFIX + " ".join(issue_title.split())
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - len(ELLIPSIS)].rstrip() + ELLIPSIS
    return title


def _translate_error(exc: CalendarError, *, write: bool) -> StepError:
    if exc.code == CalendarCode.PROVIDER_UNAVAILABLE and not exc.retryable:
        return StepError(
            STEP,
            ErrorCode.SERVICE_NOT_CONFIGURED,
            _NOT_AUTHORIZED,
            cause="calendar_authorization_unavailable",
        )
    if exc.code == CalendarCode.PROVIDER_ERROR and write and not exc.retryable:
        return StepError(
            STEP,
            ErrorCode.PROVIDER_ERROR,
            _UNCERTAIN_CREATE,
            may_have_applied=True,
            cause="calendar_uncertain_write",
        )
    code = {
        CalendarCode.INVALID_INPUT: ErrorCode.INVALID_INPUT,
        CalendarCode.NOT_FOUND: ErrorCode.NOT_FOUND,
        CalendarCode.ACCESS_DENIED: ErrorCode.ACCESS_DENIED,
    }.get(exc.code, ErrorCode.PROVIDER_ERROR)
    return StepError(
        STEP,
        code,
        _MESSAGES.get(exc.code, _MESSAGES[CalendarCode.PROVIDER_ERROR]),
        retryable=exc.retryable,
        cause=f"calendar_{exc.code.value}",
    )


def _unexpected(exc: Exception, *, write: bool) -> StepError:
    logger.exception("calendar raised an unexpected exception")
    return StepError(
        STEP,
        ErrorCode.PROVIDER_ERROR,
        _UNCERTAIN_CREATE if write else _MESSAGES[CalendarCode.PROVIDER_ERROR],
        may_have_applied=write,
        cause=type(exc).__name__,
    )


class CalendarAdapter:
    def __init__(self, service: CalendarService) -> None:
        self._service = service

    def create_block(self, block: Block) -> str:
        """Create one event for `block` and return its ID. Never retried."""
        try:
            request = CreateEvent(
                title=event_title(block.title), start=block.start, end=block.end
            )
        except ValidationError:
            # The planner only produces valid times, so this is a bug here.
            raise StepError(
                STEP,
                ErrorCode.INTERNAL_ERROR,
                "Internal server error",
                cause="invalid_event_request",
            ) from None
        try:
            return self._service.create(request).id
        except CalendarError as exc:
            raise _translate_error(exc, write=True) from None
        except Exception as exc:  # noqa: BLE001  (contract breach: never leak it)
            raise _unexpected(exc, write=True) from None

    def event_exists(self, event_id: str) -> bool:
        """Whether a previously created event still exists (used on replay)."""
        try:
            self._service.get(event_id)
        except CalendarError as exc:
            if exc.code == CalendarCode.NOT_FOUND:
                return False
            raise _translate_error(exc, write=False) from None
        except Exception as exc:  # noqa: BLE001
            raise _unexpected(exc, write=False) from None
        return True
