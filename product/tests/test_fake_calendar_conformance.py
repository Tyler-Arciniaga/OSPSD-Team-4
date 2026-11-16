"""The shared calendar conformance suite against our in-memory fake.

The fake claims to be a CalendarService (tests and the disposable
environment select it through CALENDAR_PROVIDER), so it must pass the
contract's own suite.
"""

import pytest
from calendar_contract import CalendarError, CalendarService, ErrorCode
from calendar_contract.conformance import (
    CalendarServiceConformance,
    FailingServiceFactory,
    ProviderFailure,
)

from ospsd_team4_product.fakes import InMemoryCalendarService

FAILURES = {
    ProviderFailure.ACCESS_DENIED: CalendarError(
        ErrorCode.ACCESS_DENIED, "denied", retryable=False
    ),
    ProviderFailure.UNEXPECTED_RESPONSE: CalendarError(
        ErrorCode.PROVIDER_ERROR, "unusable", retryable=False
    ),
    ProviderFailure.UNAVAILABLE: CalendarError(
        ErrorCode.PROVIDER_UNAVAILABLE, "down", retryable=True
    ),
    ProviderFailure.UNCERTAIN_WRITE: CalendarError(
        ErrorCode.PROVIDER_ERROR, "unconfirmed", retryable=False
    ),
}


class TestInMemoryCalendarConformance(CalendarServiceConformance):
    @pytest.fixture
    def service(self) -> CalendarService:
        return InMemoryCalendarService()

    @pytest.fixture
    def failing_service(self) -> FailingServiceFactory:
        return lambda failure: InMemoryCalendarService(always_fail=FAILURES[failure])
