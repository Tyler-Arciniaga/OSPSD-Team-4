"""In-memory providers, selectable through the same variables as the real ones.

    TRACKER_PROVIDER=ospsd_team4_product.fakes:InMemoryIssueProvider
    CALENDAR_PROVIDER=ospsd_team4_product.fakes:InMemoryCalendarService
    STORAGE_PROVIDER=ospsd_team4_product.fakes:InMemoryDocumentStorage

Used by tests and the disposable environment, so neither needs credentials.

`calendar_outage_on_second_create` is a calendar with a controlled failure whose
purpose is to show the documented `create_events` error through a running service.
"""

from ospsd_team4_product.fakes.calendar import (
    InMemoryCalendarService,
    calendar_outage_on_second_create,
)
from ospsd_team4_product.fakes.storage import InMemoryDocumentStorage
from ospsd_team4_product.fakes.tracker import InMemoryIssueProvider

__all__ = [
    "InMemoryCalendarService",
    "InMemoryDocumentStorage",
    "InMemoryIssueProvider",
    "calendar_outage_on_second_create",
]
