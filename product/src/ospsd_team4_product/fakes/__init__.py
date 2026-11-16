"""In-memory providers, selectable through the same variables as the real ones.

    TRACKER_PROVIDER=ospsd_team4_product.fakes:InMemoryIssueProvider
    CALENDAR_PROVIDER=ospsd_team4_product.fakes:InMemoryCalendarService
    STORAGE_PROVIDER=ospsd_team4_product.fakes:InMemoryDocumentStorage

Used by tests and the disposable environment, so neither needs credentials.
"""

from ospsd_team4_product.fakes.calendar import InMemoryCalendarService
from ospsd_team4_product.fakes.storage import InMemoryDocumentStorage
from ospsd_team4_product.fakes.tracker import InMemoryIssueProvider

__all__ = [
    "InMemoryCalendarService",
    "InMemoryDocumentStorage",
    "InMemoryIssueProvider",
]
